from collections import defaultdict
from pprint import pprint

from dateutil.tz import gettz

from database.data_access_models import ChunkRegistry
from database.tableau_api_models import SummaryStatisticDaily
from database.user_models import Participant
from libs.utils.date_utils import get_timezone_shortcode


def calculate_data_quantity_stats(participant: Participant):
    """ Update the SummaryStatisticDaily  stats for a participant, using ChunkRegistry data """
    daily_data_quantities = defaultdict(lambda: defaultdict(int))
    days = set()
    tz_longname = participant.study.timezone_name
    study_timezone = gettz(tz_longname)
    query = ChunkRegistry.objects.filter(participant=participant) \
        .values_list('time_bin', 'data_type', 'file_size').iterator()
    
    # Construct a dict formatted like this: dict[date][data_type] = total_bytes
    for time_bin, data_type, file_size in query:
        # if data_type not in ALL_DATA_STREAMS:  # source is chunked data, not needed
        #     raise Exception(f"unknown data type: {data_type}")
        day = time_bin.astimezone(study_timezone).date()
        days.add(day)
        daily_data_quantities[day][data_type] += file_size or 0
    
    print(f"updating {len(days)} daily summaries.")
    # print(f"updating {len(days)} daily summaries: {', '.join(day.isoformat() for day in sorted(days))}")
    
    # For each date, create a dict for SummaryStatisticDaily update_or_create asd pass it through
    for day, day_data in daily_data_quantities.items():
        data_quantity = {
            "participant": participant,
            "date": day,
            "defaults": {
                "timezone": get_timezone_shortcode(day, tz_longname)
            },
        }
        for data_type, total_bytes in day_data.items():
            data_quantity["defaults"][f"beiwe_{data_type}_bytes"] = total_bytes
        
        # if something fails we need the data_quantity dict contents displayed in a log
        try:
            SummaryStatisticDaily.objects.update_or_create(**data_quantity)
        except Exception:
            pprint(data_quantity)
            raise


for participant in Participant.objects.all():
    print(participant.patient_id, "...", end=" ")
    calculate_data_quantity_stats(participant)
