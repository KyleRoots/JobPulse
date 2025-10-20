"""
Timezone Utility Functions
Handles conversion between UTC and Eastern Time (EDT/EST)
"""
from datetime import datetime
import pytz


# Define timezone objects
UTC = pytz.UTC
EASTERN = pytz.timezone('America/New_York')


def utc_to_eastern(utc_dt):
    """
    Convert a UTC datetime to Eastern Time (EDT/EST)
    
    Args:
        utc_dt: datetime object (can be naive or aware)
        
    Returns:
        datetime: Eastern Time datetime object (aware)
    """
    if utc_dt is None:
        return None
    
    # If naive datetime, assume it's UTC
    if utc_dt.tzinfo is None:
        utc_dt = UTC.localize(utc_dt)
    
    # Convert to Eastern Time
    eastern_dt = utc_dt.astimezone(EASTERN)
    return eastern_dt


def format_eastern_time(utc_dt, format_string='%b %d, %Y at %I:%M %p %Z'):
    """
    Convert UTC datetime to Eastern Time and format as string
    
    Args:
        utc_dt: datetime object (can be naive or aware)
        format_string: strftime format string (default shows: Oct 19, 2025 at 09:44 PM EDT)
        
    Returns:
        str: Formatted Eastern Time string
    """
    if utc_dt is None:
        return "N/A"
    
    eastern_dt = utc_to_eastern(utc_dt)
    return eastern_dt.strftime(format_string)


def get_current_eastern_time():
    """
    Get current time in Eastern Time zone
    
    Returns:
        datetime: Current Eastern Time (aware)
    """
    return datetime.now(EASTERN)


def get_timezone_abbreviation(dt=None):
    """
    Get the current timezone abbreviation (EDT or EST)
    
    Args:
        dt: Optional datetime to check (defaults to now)
        
    Returns:
        str: 'EDT' or 'EST' depending on daylight saving time
    """
    if dt is None:
        dt = get_current_eastern_time()
    else:
        dt = utc_to_eastern(dt)
    
    return dt.strftime('%Z')


# Template filter functions for Jinja2
def jinja_eastern_time(utc_dt, format_string='%b %d, %Y at %I:%M %p %Z'):
    """
    Jinja2 template filter for converting UTC to Eastern Time
    
    Usage in templates:
        {{ timestamp | eastern_time }}
        {{ timestamp | eastern_time('%Y-%m-%d %H:%M %Z') }}
    """
    return format_eastern_time(utc_dt, format_string)


def jinja_eastern_short(utc_dt):
    """
    Jinja2 filter for short Eastern Time format (Oct 19, 2025 9:44 PM EDT)
    
    Usage in templates:
        {{ timestamp | eastern_short }}
    """
    return format_eastern_time(utc_dt, '%b %d, %Y %I:%M %p %Z')


def jinja_eastern_datetime(utc_dt):
    """
    Jinja2 filter for datetime Eastern Time format (2025-10-19 09:44 PM EDT)
    
    Usage in templates:
        {{ timestamp | eastern_datetime }}
    """
    return format_eastern_time(utc_dt, '%Y-%m-%d %I:%M %p %Z')
