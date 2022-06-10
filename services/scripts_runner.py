import importlib
from datetime import timedelta
from modulefinder import Module
from typing import Callable

from django.utils import timezone

from constants.celery_constants import SCRIPTS_QUEUE
from libs.celery_control import safe_apply_async, scripts_celery_app
from libs.sentry import make_error_sentry, SentryTypes


class ImportRepeater():
    @classmethod
    def ensure_run(cls, module: Module):
        if hasattr(cls, module.__name__):
            # module was run once
            importlib.reload(module)
        else:
            # this is first run, which means module was just imported (run)
            setattr(cls, module.__name__, True)
        print(f"Ran script '{module.__name__}'")


def queue_script(a_celery_task: Callable, expiry: str):
    """ forces enqueueing with an expiry. """
    if expiry not in ("daily", "hourly"):
        raise ValueError("Expiry must one of daily or hourly.")
    
    if expiry == "daily":
        expiry = (timezone.now() + timedelta(hours=24)).replace(second=0, microsecond=0)
    if expiry == "hourly":
        expiry = (timezone.now() + timedelta(hours=1)).replace(second=0, microsecond=0)
    
    print(f"Queueing script '{a_celery_task.__name__}', expires at {expiry}")
    safe_apply_async(
        a_celery_task,
        max_retries=0,
        expires=expiry,
        task_track_started=True,
        task_publish_retry=False,
        retry=False
    )


## ios undecryptable files fix

def create_task_ios_no_decryption_key_task():
    with make_error_sentry(sentry_type=SentryTypes.data_processing):
        print("Queueing ios bad decryption keys script.")
        queue_script(celery_process_ios_no_decryption_key, "hourly")


@scripts_celery_app.task(queue=SCRIPTS_QUEUE)
def celery_process_ios_no_decryption_key():
    with make_error_sentry(sentry_type=SentryTypes.data_processing):
        print("running script process_ios_no_decryption_key")
        from scripts import process_ios_no_decryption_key
        ImportRepeater.ensure_run(process_ios_no_decryption_key)


## Log uploads

def create_task_upload_logs():
    with make_error_sentry(sentry_type=SentryTypes.data_processing):
        queue_script(celery_upload_logs, "daily")


# run via celery as long as tasks exist
@scripts_celery_app.task(queue=SCRIPTS_QUEUE)
def celery_upload_logs():
    with make_error_sentry(sentry_type=SentryTypes.data_processing):
        print("running script upload_logs.")
        from scripts import upload_logs
        ImportRepeater.ensure_run(upload_logs)


# purge old data

def create_task_purge_invalid_time_data():
    with make_error_sentry(sentry_type=SentryTypes.data_processing):
        queue_script(celery_purge_invalid_time_data, "daily")


# run via celery as long as tasks exist
@scripts_celery_app.task(queue=SCRIPTS_QUEUE)
def celery_purge_invalid_time_data():
    with make_error_sentry(sentry_type=SentryTypes.data_processing):
        print("running script upload_logs.")
        from scripts import purge_1970_chunks
        ImportRepeater.ensure_run(purge_1970_chunks)
