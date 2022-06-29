import datetime
import json
import os
import pickle
import shutil
import uuid
from time import sleep

from django.db import models

from constants.forest_constants import ForestTaskStatus, ForestTree
from database.common_models import TimestampedModel
from database.user_models import Participant
from libs.utils.date_utils import datetime_to_list


class ForestParam(TimestampedModel):
    """ Model for tracking params used in Forest analyses. There is one object for all trees.

    When adding support for a new tree, make sure to add a migration to populate existing
    ForestMetadata objects with the default metadata for the new tree. This way, all existing
    ForestTasks are still associated to the same ForestMetadata object and we don't have to give a
    warning to users that the metadata have changed. """
    # Note: making a BooleanField null=True unique=True allows us to ensure only one object can have
    # default=True at any time (null is considered unique). This means this field should be consumed
    # as True or falsy (null is false), as the value should never be actually set to `False`.
    # (Warning: the above property depends on the database backend.)
    default = models.BooleanField(unique=True, null=True, blank=True)
    notes = models.TextField(blank=True)
    name = models.TextField(blank=True)
    
    jasmine_json_string = models.TextField()
    willow_json_string = models.TextField()
    sycamore_json_string = models.TextField()
    
    def params_for_tree(self, tree_name):
        if tree_name not in ForestTree.values():
            raise KeyError(f"Invalid tree \"{tree_name}\". Must be one of {ForestTree.values()}.")
        json_string_field_name = f"{tree_name}_json_string"
        return json.loads(getattr(self, json_string_field_name))


class ForestTask(TimestampedModel):
    participant = models.ForeignKey(
        'Participant', on_delete=models.PROTECT, db_index=True
    )
    # the external id is used for endpoints that refer to forest trackers to avoid exposing the
    # primary keys of the model. it is intentionally not the primary key
    external_id = models.UUIDField(default=uuid.uuid4, editable=False)
    
    forest_param = models.ForeignKey(ForestParam, on_delete=models.PROTECT)
    params_dict_cache = models.TextField(blank=True)  # Cache of the params used
    
    forest_tree = models.TextField(choices=ForestTree.choices())
    data_date_start = models.DateField()  # inclusive
    data_date_end = models.DateField()  # inclusive
    
    total_file_size = models.BigIntegerField(blank=True, null=True)  # input file size sum for accounting
    process_start_time = models.DateTimeField(null=True, blank=True)
    process_download_end_time = models.DateTimeField(null=True, blank=True)
    process_end_time = models.DateTimeField(null=True, blank=True)
    
    # Whether or not there was any data output by Forest (None indicates unknown)
    forest_output_exists = models.BooleanField(null=True, blank=True)
    
    status = models.TextField(choices=ForestTaskStatus.choices())
    stacktrace = models.TextField(null=True, blank=True, default=None)  # for logs
    forest_version = models.CharField(blank=True, max_length=10)
    
    all_bv_set_s3_key = models.TextField(blank=True)
    all_memory_dict_s3_key = models.TextField(blank=True)
    
    # non-fields
    _tmp_parent_folder_exists = False
    
    def clean_up_files(self):
        """ Delete temporary input and output files from this Forest run. """
        for i in range(10):
            try:
                shutil.rmtree(self.data_base_path)
            except FileNotFoundError:
                pass
            # file system can be slightly slow, we need to sleep. (this code never executes on frontend)
            sleep(0.5)
            if not os.path.exists(self.data_base_path):
                return
        raise Exception(
            f"Could not delete folder {self.data_base_path} for participant {self.external_id}, tried {i} times."
        )
    
    def params_dict(self, task=False):
        """ Return a dict of params to pass into the Forest function. The task flag is used to indicate whether this
            is being called for use in the serializer or for use in a task (in which case we can call additional 
            functions as needed).
         """
        other_params = {
            "output_folder": self.data_output_path,
            "study_folder": self.data_input_path,
        }
        if self.forest_tree not in [ForestTree.sycamore]:
            # sycamore expects "time_end" and "time_start" as strings, while other trees expect them as lists
            # Need to add a day since this model tracks time end inclusively, but Forest expects
            # it exclusively
            time_end = datetime_to_list(self.data_date_end + datetime.timedelta(days=1))
            time_start = datetime_to_list(self.data_date_start)
            other_params.update({
                "time_end": time_end,
                "time_start": time_start,
            })
        else:
            # sycamore expects string in format "YYYY-MM-DD"
            string_format = "%Y-%m-%d"
            time_end = (self.data_date_end + datetime.timedelta(days=1)).strftime(string_format)
            time_start = self.data_date_start.strftime(string_format)
            other_params.update({
                "end_date": time_end,
                "start_date": time_start,
            })
        if self.forest_tree == ForestTree.jasmine:
            other_params["all_BV_set"] = self.get_all_bv_set_dict()
            other_params["all_memory_dict"] = self.get_all_memory_dict_dict()
        if self.forest_tree == ForestTree.sycamore and task:
            other_params['config_path'] = self.study_config_path
            other_params['interventions_filepath'] = self.interventions_filepath
        return {**self.forest_param.params_for_tree(self.forest_tree), **other_params}
    
    def get_all_bv_set_dict(self):
        """ Return the unpickled all_bv_set dict. """
        # FIXME: rename
        if not self.all_bv_set_s3_key:
            return None  # Forest expects None if it doesn't exist
        from libs.s3 import s3_retrieve
        bytes = s3_retrieve(self.all_bv_set_s3_key, self.participant.study.object_id, raw_path=True)
        return pickle.loads(bytes)
    
    def get_all_memory_dict_dict(self):
        """ Return the unpickled all_memory_dict dict. """
        # FIXME: rename
        if not self.all_memory_dict_s3_key:
            return None  # Forest expects None if it doesn't exist
        from libs.s3 import s3_retrieve
        bytes = s3_retrieve(
            self.all_memory_dict_s3_key,
            self.participant.study.object_id,
            raw_path=True,
        )
        return pickle.loads(bytes)
    
    def get_slug(self):
        """ Return a human-readable identifier. """
        parts = [
            "data",
            self.participant.patient_id,
            self.forest_tree,
            str(self.data_date_start),
            str(self.data_date_end),
        ]
        return "_".join(parts)
    
    def save_all_bv_set_bytes(self, all_bv_set_bytes):
        from libs.s3 import s3_upload
        s3_upload(
            self.generate_all_bv_set_s3_key(),
            all_bv_set_bytes,
            self.participant,
            raw_path=True,
        )
        self.all_bv_set_s3_key = self.generate_all_bv_set_s3_key()
        self.save(update_fields=["all_bv_set_s3_key"])
    
    def save_all_memory_dict_bytes(self, all_memory_dict_bytes):
        from libs.s3 import s3_upload
        s3_upload(
            self.generate_all_memory_dict_s3_key(),
            all_memory_dict_bytes,
            self.participant,
            raw_path=True,
        )
        self.all_memory_dict_s3_key = self.generate_all_memory_dict_s3_key()
        self.save(update_fields=["all_memory_dict_s3_key"])
    
    ## File paths
    @property
    def data_base_path(self):
        """ Return the path to the base data folder, creating it if it doesn't already exist. """
        # on first access of the base path check for the presence of the /tmp/forest
        if not self._tmp_parent_folder_exists and not os.path.exists("/tmp/forest/"):
            try:
                os.mkdir("/tmp/forest/")
            except FileExistsError:
                pass  # it just needs to exist
            self._tmp_parent_folder_exists = True
        return os.path.join("/tmp/forest/", str(self.external_id), self.forest_tree)

    @property
    def interventions_filepath(self):
        """ Generates a study interventions file for the participant's survey and returns the path to it """
        from database.study_models import Study
        study = Study.objects.get(id=self.participant.study_id)
        from libs.intervention_export import intervention_survey_data
        data = intervention_survey_data(study)
        filename = study.name.replace(' ', '_') + "_interventions.json"
        with open(os.path.join(self.data_base_path, filename), "w") as f:
            f.write(json.dumps(data))
        return os.path.join(self.data_base_path, filename)
    
    @property
    def study_config_path(self):
        """ Generates a study config file for the participant's survey and returns the path to it """
        from database.study_models import Study
        from libs.copy_study import format_study
        study = Study.objects.get(pk=self.participant.study_id)
        filename = study.name.replace(' ', '_') + "_surveys_and_settings.json"
        with open(os.path.join(self.data_base_path, filename), "w") as f:
            f.write(format_study(study))
        return os.path.join(self.data_base_path, filename)

    @property
    def data_input_path(self):
        """ Return the path to the input data folder, creating it if it doesn't already exist. """
        return os.path.join(self.data_base_path, "data")
    
    @property
    def data_output_path(self):
        """ Return the path to the output data folder, creating it if it doesn't already exist. """
        return os.path.join(self.data_base_path, "output")
    
    @property
    def forest_results_path(self):
        """ Return the path to the file that contains the output of Forest. """
        return os.path.join(self.data_output_path, f"{self.participant.patient_id}.csv")
    
    @property
    def s3_base_folder(self):
        return os.path.join(self.participant.study.object_id, "forest")
    
    @property
    def all_bv_set_path(self):
        # FIXME: this is part of Willow maybe? rename.
        return os.path.join(self.data_output_path, "all_BV_set.pkl")
    
    @property
    def all_memory_dict_path(self):
        # FIXME: this is part of Jasmine  maybe? rename.
        return os.path.join(self.data_output_path, "all_memory_dict.pkl")
    
    def generate_all_bv_set_s3_key(self):
        """ Generate the S3 key that all_bv_set_s3_key should live in (whereas the direct
        all_bv_set_s3_key field on the instance is where it currently lives, regardless of how
        generation changes). """
        return os.path.join(self.s3_base_folder, 'all_bv_set.pkl')
    
    def generate_all_memory_dict_s3_key(self):
        """ Generate the S3 key that all_memory_dict_s3_key should live in (whereas the direct
        all_memory_dict_s3_key field on the instance is where it currently lives, regardless of how
        generation changes). """
        return os.path.join(self.s3_base_folder, 'all_memory_dict.pkl')


class SummaryStatisticDaily(TimestampedModel):
    participant = models.ForeignKey(Participant, on_delete=models.CASCADE)
    date = models.DateField(db_index=True)
    
    # Beiwe data quantities
    beiwe_accelerometer_bytes = models.IntegerField(null=True, blank=True)
    beiwe_ambient_audio_bytes = models.IntegerField(null=True, blank=True)
    beiwe_app_log_bytes = models.IntegerField(null=True, blank=True)
    beiwe_bluetooth_bytes = models.IntegerField(null=True, blank=True)
    beiwe_calls_bytes = models.IntegerField(null=True, blank=True)
    beiwe_devicemotion_bytes = models.IntegerField(null=True, blank=True)
    beiwe_gps_bytes = models.IntegerField(null=True, blank=True)
    beiwe_gyro_bytes = models.IntegerField(null=True, blank=True)
    beiwe_identifiers_bytes = models.IntegerField(null=True, blank=True)
    beiwe_image_survey_bytes = models.IntegerField(null=True, blank=True)
    beiwe_ios_log_bytes = models.IntegerField(null=True, blank=True)
    beiwe_magnetometer_bytes = models.IntegerField(null=True, blank=True)
    beiwe_power_state_bytes = models.IntegerField(null=True, blank=True)
    beiwe_proximity_bytes = models.IntegerField(null=True, blank=True)
    beiwe_reachability_bytes = models.IntegerField(null=True, blank=True)
    beiwe_survey_answers_bytes = models.IntegerField(null=True, blank=True)
    beiwe_survey_timings_bytes = models.IntegerField(null=True, blank=True)
    beiwe_texts_bytes = models.IntegerField(null=True, blank=True)
    beiwe_audio_recordings_bytes = models.IntegerField(null=True, blank=True)
    beiwe_wifi_bytes = models.IntegerField(null=True, blank=True)
    
    # GPS
    jasmine_distance_diameter = models.FloatField(null=True, blank=True)
    jasmine_distance_from_home = models.FloatField(null=True, blank=True)
    jasmine_distance_traveled = models.FloatField(null=True, blank=True)
    jasmine_flight_distance_average = models.FloatField(null=True, blank=True)
    jasmine_flight_distance_stddev = models.FloatField(null=True, blank=True)
    jasmine_flight_duration_average = models.FloatField(null=True, blank=True)
    jasmine_flight_duration_stddev = models.FloatField(null=True, blank=True)
    jasmine_gps_data_missing_duration = models.IntegerField(null=True, blank=True)
    jasmine_home_duration = models.FloatField(null=True, blank=True)
    jasmine_gyration_radius = models.FloatField(null=True, blank=True)
    jasmine_significant_location_count = models.IntegerField(null=True, blank=True)
    jasmine_significant_location_entropy = models.FloatField(null=True, blank=True)
    jasmine_pause_time = models.TextField(null=True, blank=True)
    jasmine_obs_duration = models.FloatField(null=True, blank=True)
    jasmine_obs_day = models.FloatField(null=True, blank=True)
    jasmine_obs_night = models.FloatField(null=True, blank=True)
    jasmine_total_flight_time = models.FloatField(null=True, blank=True)
    jasmine_av_pause_duration = models.FloatField(null=True, blank=True)
    jasmine_sd_pause_duration = models.FloatField(null=True, blank=True)
    
    # Willow, Texts
    willow_incoming_text_count = models.IntegerField(null=True, blank=True)
    willow_incoming_text_degree = models.IntegerField(null=True, blank=True)
    willow_incoming_text_length = models.IntegerField(null=True, blank=True)
    willow_outgoing_text_count = models.IntegerField(null=True, blank=True)
    willow_outgoing_text_degree = models.IntegerField(null=True, blank=True)
    willow_outgoing_text_length = models.IntegerField(null=True, blank=True)
    willow_incoming_text_reciprocity = models.IntegerField(null=True, blank=True)
    willow_outgoing_text_reciprocity = models.IntegerField(null=True, blank=True)
    willow_outgoing_MMS_count = models.IntegerField(null=True, blank=True)
    willow_incoming_MMS_count = models.IntegerField(null=True, blank=True)
    
    # Willow, Calls
    willow_incoming_call_count = models.IntegerField(null=True, blank=True)
    willow_incoming_call_degree = models.IntegerField(null=True, blank=True)
    willow_incoming_call_duration = models.FloatField(null=True, blank=True)
    willow_outgoing_call_count = models.IntegerField(null=True, blank=True)
    willow_outgoing_call_degree = models.IntegerField(null=True, blank=True)
    willow_outgoing_call_duration = models.FloatField(null=True, blank=True)
    willow_missed_call_count = models.IntegerField(null=True, blank=True)
    willow_missed_callers = models.IntegerField(null=True, blank=True)
    
    # Sycamore, Survey Frequency
    sycamore_total_surveys = models.IntegerField(null=True, blank=True)
    sycamore_total_completed_surveys = models.IntegerField(null=True, blank=True)
    sycamore_total_opened_surveys = models.IntegerField(null=True, blank=True)
    sycamore_average_time_to_submit = models.FloatField(null=True, blank=True)
    sycamore_average_time_to_open = models.FloatField(null=True, blank=True)
    sycamore_average_duration = models.FloatField(null=True, blank=True)

    jasmine_task = models.ForeignKey(ForestTask, blank=True, null=True, on_delete=models.PROTECT, related_name="jasmine_summary_statistics")
    willow_task = models.ForeignKey(ForestTask, blank=True, null=True, on_delete=models.PROTECT, related_name="willow_summary_statistics")
    sycamore_task = models.ForeignKey(ForestTask, blank=True, null=True, on_delete=models.PROTECT, related_name="sycamore_summary_statistics")
    
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['date', 'participant'], name="unique_summary_statistic")
        ]
