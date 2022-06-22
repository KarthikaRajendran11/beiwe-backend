from datetime import date, datetime, timedelta
from typing import List, Union

from django.utils.timezone import make_aware


def daterange(
    start: datetime, stop: datetime, step: timedelta = timedelta(days=1), inclusive: bool = False
):
    # source: https://stackoverflow.com/a/1060376/1940450
    if step.days > 0:
        while start < stop:
            yield start
            start = start + step
            # not +=! don't modify object passed in if it's mutable
            # since this function is not restricted to
            # only types from datetime module
    elif step.days < 0:
        while start > stop:
            yield start
            start = start + step
    if inclusive and start == stop:
        yield start


def datetime_to_list(datetime_obj: Union[date, datetime]) -> List[int]:
    """ Takes in a date or datetime, returns a list of datetime components. """
    datetime_component_list = [datetime_obj.year, datetime_obj.month, datetime_obj.day]
    if isinstance(datetime_obj, datetime):
        datetime_component_list.extend([
            datetime_obj.hour,
            datetime_obj.minute,
            datetime_obj.second,
            datetime_obj.microsecond,
        ])
    else:
        datetime_component_list.extend([0, 0, 0, 0])
    return datetime_component_list


def date_to_start_of_day(a_date: date, timezone_long_name: str):
    """ Given a date and a timezone, returns a timezone'd datetime for the start of that day. """
    if not type(a_date) is date:
        raise TypeError("date_start_of_day requires dates, datetimes must be handled manually")
    make_aware(datetime.combine(a_date, datetime.min.time()), timezone_long_name)


def date_to_end_of_day(a_date: date, timezone_long_name: str):
    """ Given a date and a timezone, returns a timezone'd datetime for the end of that day. """
    if not type(a_date) is date:
        raise TypeError("date_end_of_day requires dates, datetimes must be handled manually")
    make_aware(datetime.combine(a_date, datetime.max.time()), timezone_long_name)


def get_timezone_shortcode(a_date: date, timezone_long_name: str) -> str:
    """ Create datetime of the END OF THE DAY of the target date and get the timezone abbreviation.
    These shortnames provide information about daylight savings, if any, in that timezone, which is
    desireable for researchers.  We always want the end of the day because then the timezone will be
    set to the timezone that _humans were paying attention to_ for that day.
    
    WARNING: modifying this code is extremely error prone, this method is stable and correct. If you
    use pytz tools resulting time will probably be wrong, specifically 4 minutes off for eastern
    time. This the string returned will be the correct abbreviation in the local time, e.g. daylight
    savings will correctly be EDT or EST for eastern time. """
    if not type(a_date) is date:
        raise TypeError("get_timezone_shortcode requires dates, datetimes must be handled manually")
    return make_aware(datetime.combine(a_date, datetime.max.time()), timezone_long_name).tzname()
