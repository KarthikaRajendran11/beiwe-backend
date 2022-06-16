import csv
import json
import os
import traceback
from datetime import date, datetime, timedelta
from multiprocessing.pool import ThreadPool
from typing import Dict, Tuple

import pytz
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from pkg_resources import get_distribution

from constants.celery_constants import FOREST_QUEUE
from constants.data_access_api_constants import CHUNK_FIELDS
from constants.forest_constants import (FOREST_ERROR_LOCATION_KEY, ForestTaskStatus, ForestTree,
    NO_DATA_ERROR, TREE_COLUMN_NAMES_TO_SUMMARY_STATISTICS, YEAR_MONTH_DAY)
from database.data_access_models import ChunkRegistry
from database.tableau_api_models import ForestTask, SummaryStatisticDaily
from database.user_models import Participant
from libs.celery_control import forest_celery_app, safe_apply_async
from libs.s3 import s3_retrieve
from libs.sentry import make_error_sentry, SentryTypes
from libs.streaming_zip import determine_file_name

from forest.jasmine.traj2stats import gps_stats_main
from forest.willow.log_stats import log_stats_main


MIN_TIME = datetime.time(0, 0, 0, 0)
MAX_TIME = datetime.time(23, 59, 59, 999999)


class NoSentryException(Exception): pass
class BadForestField(Exception): pass


DEBUG_CELERY_FOREST = False


def log(*args, **kwargs):
    if DEBUG_CELERY_FOREST:
        print("celery_forest debug: ", end="")
        print(*args, **kwargs)


# don't stick in constants, we want forest imports limited to forest related files, forest is not
# installed on frontend servers, forest constants must remain freely importable
TREE_TO_FOREST_FUNCTION = {
    ForestTree.jasmine: gps_stats_main,
    ForestTree.willow: log_stats_main,
}


def create_forest_celery_tasks():
    """ Basic entrypoint, does what it says """
    pending_tasks = ForestTask.objects.filter(status=ForestTaskStatus.queued)
    with make_error_sentry(sentry_type=SentryTypes.data_processing):
        for task in pending_tasks:
            # always print
            print(
                f"Queueing up celery task for {task.participant} on tree {task.forest_tree} "
                f"from {task.data_date_start} to {task.data_date_end}"
            )
            enqueue_forest_task(args=[task.id])


# run via celery as long as tasks exist
@forest_celery_app.task(queue=FOREST_QUEUE)
def celery_run_forest(forest_task_id):
    with transaction.atomic():
        task: ForestTask = ForestTask.objects.filter(id=forest_task_id).first()
        
        participant: Participant = task.participant
        forest_tree = task.forest_tree
        
        # Check if there already is a running task for this participant and tree, handling
        # concurrency and requeuing of the ask if necessary
        tasks = ForestTask.objects.select_for_update() \
                .filter(participant=participant, forest_tree=forest_tree)
        
        # FIXME: This is obviously unsafe in the more general case, but interleaved or duplicated tasks
        # in the context of generating daily data will be fine because they just rerun the same
        # idempotent analysis operations.
        # Handle overlap case (paranoid)
        if tasks.filter(status=ForestTaskStatus.running).exists():
            enqueue_forest_task(args=[task.id])
            return
        
        # Get the chronologically earliest task that's queued
        task: ForestTask = tasks.filter(status=ForestTaskStatus.queued) \
                .order_by("-data_date_start").first()
        
        if task is None:
            return
        
        # Set metadata on the task
        task.status = ForestTaskStatus.running
        task.forest_version = get_distribution("forest").version
        task.process_start_time = timezone.now()
        task.save(update_fields=["status", "forest_version", "process_start_time"])
    
    try:
        # ChunkRegistry time_bin hourly chunks are in UTC, and only have hourly datapoints for all
        # automated data, but manually entered data is more specific with minutes, seconds, etc.  We
        # want our query for source data to use the study's timezone such that starts of days align
        # to local midnight and end-of-day to 11:59.59pm. Weird fractional timezones will be
        # noninclusive of their first hour of data between midnight and midnight + 1 hour, except
        # for manually entered data streams which will instead align to the calendar date in the
        # study timezone. Any such "missing" fraction of an hour is instead included at the end of the
        # previous day.
        starttime_midnight = datetime.combine(task.data_date_start, MIN_TIME, participant.study.timezone)
        endtime_11_59pm = datetime.combine(task.data_date_end, MAX_TIME, participant.study.timezone)
        log("starttime_midnight: ", starttime_midnight.isoformat())
        log("endtime_11_59pm: ", endtime_11_59pm.isoformat())
        
        chunks = ChunkRegistry.objects.filter(
            participant=participant, time_bin__gte=starttime_midnight, time_bin__lte=endtime_11_59pm
        )
        file_size = chunks.aggregate(Sum('file_size')).get('file_size__sum')
        if file_size is None:
            raise NoSentryException(NO_DATA_ERROR)
        
        task.total_file_size = file_size
        task.save(update_fields=["total_file_size"])
        
        # Download data
        # FIXME: download only files appropriate to the forest task to be run
        create_local_data_files(task, chunks)
        task.process_download_end_time = timezone.now()
        task.save(update_fields=["process_download_end_time"])
        log("task.process_download_end_time:", task.process_download_end_time.isoformat())
        
        # Run Forest
        params_dict = task.params_dict()
        log("params_dict:", params_dict)
        task.params_dict_cache = json.dumps(params_dict, cls=DjangoJSONEncoder)
        task.save(update_fields=["params_dict_cache"])
        
        log("running:", task.forest_tree)
        TREE_TO_FOREST_FUNCTION[task.forest_tree](**params_dict)
        
        # Save data
        task.forest_output_exists = construct_summary_statistics(task)
        task.save(update_fields=["forest_output_exists"])
        save_cached_files(task)
    
    except Exception as e:
        task.status = ForestTaskStatus.error
        task.stacktrace = traceback.format_exc()
        tags = {k: str(v) for k, v in task.as_dict().items()}
        tags[FOREST_ERROR_LOCATION_KEY] = "forest task general error"
        if not isinstance(e, NoSentryException):
            with make_error_sentry(SentryTypes.data_processing, tags=tags):
                raise
    
    else:
        task.status = ForestTaskStatus.success
    
    finally:
        # This is entirely boilerplate for reporting cleanup operations cleanly to both sentry and
        # forest task infrastructure.
        # log("deleting files 1")
        try:
            task.clean_up_files()
        except Exception:
            if task.stacktrace is None:
                task.stacktrace = traceback.format_exc()
            else:
                task.stacktrace = task.stacktrace + "\n\n" + traceback.format_exc()
            # add all possible tags...
            tags = {k: str(v) for k, v in task.as_dict().items()}
            tags[FOREST_ERROR_LOCATION_KEY] = "forest task cleanup error"
            with make_error_sentry(SentryTypes.data_processing, tags=tags):
                raise
    
    log("task.status:", task.status)
    if task.stacktrace:
        log("stacktrace:", task.stacktrace)
    
    task.save(update_fields=["status", "stacktrace"])
    
    log("deleting files 2")
    task.clean_up_files()  # if this fails you probably have server oversubscription issues.
    task.process_end_time = timezone.now()
    task.save(update_fields=["process_end_time"])


def construct_summary_statistics(task: ForestTask):
    """ Construct summary statistics from forest output, returning whether or not any
        SummaryStatisticDaily has potentially been created or updated. """
    
    if not os.path.exists(task.forest_results_path):
        log("path does not exist:", task.forest_results_path)
        return False
    
    if task.forest_tree == ForestTree.jasmine:
        task_attribute = "jasmine_task"
    elif task.forest_tree == ForestTree.willow:
        task_attribute = "willow_task"
    else:
        raise Exception(f"Unknown Forest Tree: {task.forest_tree}")
    log("tree:", task_attribute)
    
    with open(task.forest_results_path, "r") as f:
        reader = csv.DictReader(f)
        has_data = False
        log("opened file...")
        
        for line in reader:
            has_data = True
            summary_date = date(
                int(float(line['year'])),
                int(float(line['month'])),
                int(float(line['day'])),
            )
            # if timestamp is outside of desired range, skip.
            if not (task.data_date_start < summary_date < task.data_date_end):
                continue
            
            updates = {task_attribute: task}
            for column_name, value in line.items():
                if column_name in TREE_COLUMN_NAMES_TO_SUMMARY_STATISTICS:
                    # look up column translation, coerce empty strings to Nones
                    summary_stat_field = TREE_COLUMN_NAMES_TO_SUMMARY_STATISTICS[column_name]
                    # force Nones on no data fields, not empty strings (db table issue)
                    updates[summary_stat_field] = value if value != '' else None
                elif column_name in YEAR_MONTH_DAY:
                    continue
                else:
                    raise BadForestField(column_name)
            
            data = {
                "date": summary_date,
                "defaults": updates,
                "participant": task.participant,
            }
            log("creating SummaryStatisticDaily:", data)
            SummaryStatisticDaily.objects.update_or_create(**data)
    
    return has_data


def create_local_data_files(task, chunks):
    # FIXME: download only files appropriate to the forest task to be run.
    # downloading data is highly threadable and can be the majority of the run time. 4 works for
    # most files, a very high small file count can make use of 10+ before we are cpu limited.
    with ThreadPool(4) as pool:
        for _ in pool.imap_unordered(
            func=batch_create_file,
            iterable=[(task, chunk) for chunk in chunks.values("study__object_id", *CHUNK_FIELDS)],
        ):
            pass


def batch_create_file(singular_task_chunk: Tuple[ForestTask, Dict]):
    """ Wrapper for basic file download operations so that it can be run in a ThreadPool. """
    # weird unpack of variables, do s3_retrieve.
    task, chunk = singular_task_chunk
    contents = s3_retrieve(chunk["chunk_path"], chunk["study__object_id"], raw_path=True)
    # file ops
    file_name = os.path.join(task.data_input_path, determine_file_name(chunk))
    os.makedirs(os.path.dirname(file_name), exist_ok=True)
    with open(file_name, "xb") as f:
        f.write(contents)


def enqueue_forest_task(**kwargs):
    updated_kwargs = {
        "expires": (datetime.utcnow() + timedelta(minutes=5)).replace(second=30, microsecond=0, tzinfo=pytz.utc),
        "max_retries": 0,
        "retry": False,
        "task_publish_retry": False,
        "task_track_started": True,
        **kwargs,
    }
    safe_apply_async(celery_run_forest, **updated_kwargs)


def save_cached_files(task: ForestTask):
    """ Find output files from forest tasks and consume them. """
    # Fixme: we need to standardize this, and rename these functions
    if os.path.exists(task.all_bv_set_path):
        with open(task.all_bv_set_path, "rb") as f:
            task.save_all_bv_set_bytes(f.read())
    
    if os.path.exists(task.all_memory_dict_path):
        with open(task.all_memory_dict_path, "rb") as f:
            task.save_all_memory_dict_bytes(f.read())
