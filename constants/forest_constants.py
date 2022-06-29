class ForestTree:
    """ Todo: Once we upgrade to Django 3, use TextChoices """
    jasmine = "jasmine"
    willow = "willow"
    sycamore = "sycamore"
    
    @classmethod
    def choices(cls):
        return [(choice, choice.title()) for choice in cls.values()]
    
    @classmethod
    def values(cls):
        return [cls.jasmine, cls.willow, cls.sycamore]


class ForestTaskStatus:
    queued = 'queued'
    running = 'running'
    success = 'success'
    error = 'error'
    cancelled = 'cancelled'
    
    @classmethod
    def choices(cls):
        return [(choice, choice.title()) for choice in cls.values()]
    
    @classmethod
    def values(cls):
        return [cls.queued, cls.running, cls.success, cls.error, cls.cancelled]


YEAR_MONTH_DAY = ('year', 'month', 'day')


# the following dictionary is a mapping of output CSV fields from various Forest Trees to their
# summary statistic names.  Note that this data structure is imported and used in tableau constants.

TREE_COLUMN_NAMES_TO_SUMMARY_STATISTICS = {
    # Jasmine, GPS
    "diameter": "jasmine_distance_diameter",
    "max_dist_home": "jasmine_distance_from_home",
    "dist_traveled": "jasmine_distance_traveled",
    "av_flight_length": "jasmine_flight_distance_average",
    "sd_flight_length": "jasmine_flight_distance_stddev",
    "av_flight_duration": "jasmine_flight_duration_average",
    "sd_flight_duration": "jasmine_flight_duration_stddev",
    "missing_time": "jasmine_gps_data_missing_duration",
    "home_time": "jasmine_home_duration",
    "radius": "jasmine_gyration_radius",
    "num_sig_places": "jasmine_significant_location_count",
    "entropy": "jasmine_significant_location_entropy",
    "total_pause_time": "jasmine_pause_time",
    "obs_duration": "jasmine_obs_duration",
    "obs_day": "jasmine_obs_day",
    "obs_night": "jasmine_obs_night",
    "total_flight_time": "jasmine_total_flight_time",
    "av_pause_duration": "jasmine_av_pause_duration",
    "sd_pause_duration": "jasmine_sd_pause_duration",

    # Willow, Texts
    "num_r": "willow_incoming_text_count",
    "num_r_tel": "willow_incoming_text_degree",
    "total_char_r": "willow_incoming_text_length",
    "num_s": "willow_outgoing_text_count",
    "num_s_tel": "willow_outgoing_text_degree",
    "total_char_s": "willow_outgoing_text_length",
    "text_reciprocity_incoming": "willow_incoming_text_reciprocity",
    "text_reciprocity_outgoing": "willow_outgoing_text_reciprocity",
    "num_mms_s": "willow_outgoing_MMS_count",
    "num_mms_r": "willow_incoming_MMS_count",

    # willow, calls
    "num_in_call": "willow_incoming_call_count",
    "num_in_caller": "willow_incoming_call_degree",
    "total_mins_in_call": "willow_incoming_call_duration",
    "num_out_call": "willow_outgoing_call_count",
    "num_out_caller": "willow_outgoing_call_degree",
    "total_mins_out_call": "willow_outgoing_call_duration",
    "num_mis_call": "willow_missed_call_count",
    "num_mis_caller": "willow_missed_callers",

    # sycamore, survey frequency
    "num_surveys": "sycamore_total_surveys",
    "num_complete_surveys": "sycamore_total_completed_surveys",
    "num_opened_surveys": "sycamore_total_opened_surveys",
    "avg_time_to_submit": "sycamore_average_time_to_submit",
    "avg_time_to_open": "sycamore_average_time_to_open",
    "avg_duration": "sycamore_average_duration",

}


NO_DATA_ERROR = 'No chunked data found for participant for the dates specified.'
FOREST_ERROR_LOCATION_KEY = "forest_error_location"
