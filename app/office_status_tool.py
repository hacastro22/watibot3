"""
Customer Service Office Status Tool

This tool checks whether customer service offices are currently open or closed
by querying the Asterisk timegroups database and evaluating automation eligibility.

Used by the assistant before making any booking to determine:
- If offices are closed → Assistant proceeds with automated booking
- If offices are open → Check automation windows to decide booking method
"""

import os
import logging
import mysql.connector
import time
from datetime import datetime
from pytz import timezone
import holidays
import re
from typing import Dict, List, Tuple, Optional, Callable, Any
from . import config

logger = logging.getLogger(__name__)

# Retry configuration for office database (limited retries - not critical)
OFFICE_MAX_RETRIES = 3
OFFICE_RETRY_DELAY_SECONDS = 2

# El Salvador timezone
EL_SALVADOR_TZ = timezone('America/El_Salvador')

# El Salvador holidays
EL_SALVADOR_HOLIDAYS = holidays.country_holidays('SV')

def _parse_time_segment(time_segment: str, current_time: datetime) -> bool:
    """
    Parse and match time segment in HH:MM-HH:MM format.
    Handles spanning midnight ranges (e.g., 23:00-02:00).
    
    Args:
        time_segment: Time range string (e.g., "08:00-17:00" or "23:00-02:00")
        current_time: Current datetime object
        
    Returns:
        bool: True if current time matches the segment
    """
    if not time_segment or time_segment == '*':
        return True
    
    try:
        if '-' not in time_segment:
            logger.warning(f"Invalid time segment format: {time_segment}")
            return False
            
        start_str, end_str = time_segment.split('-', 1)
        
        # Parse times
        start_hour, start_min = map(int, start_str.split(':'))
        end_hour, end_min = map(int, end_str.split(':'))
        
        current_hour = current_time.hour
        current_min = current_time.minute
        current_total_min = current_hour * 60 + current_min
        start_total_min = start_hour * 60 + start_min
        end_total_min = end_hour * 60 + end_min
        
        # Handle spanning midnight (e.g., 23:00-02:00)
        if start_total_min > end_total_min:
            # Time spans midnight: current time >= start OR current time <= end
            return current_total_min >= start_total_min or current_total_min <= end_total_min
        else:
            # Normal range: start <= current <= end
            return start_total_min <= current_total_min <= end_total_min
            
    except (ValueError, IndexError) as e:
        logger.error(f"Error parsing time segment '{time_segment}': {e}")
        return False

def _parse_day_of_week_segment(day_segment: str, current_time: datetime) -> bool:
    """
    Parse and match day of week segment.
    Handles ranges like "mon-fri" or "thu-sun" including weekend spanning.
    
    Args:
        day_segment: Day range string (e.g., "mon-fri", "thu-sun", "sat")
        current_time: Current datetime object
        
    Returns:
        bool: True if current day matches the segment
    """
    if not day_segment or day_segment == '*':
        return True
    
    day_names = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
    current_day = current_time.weekday()  # 0=Monday, 6=Sunday
    
    day_segment = day_segment.lower().strip()
    
    try:
        if '-' in day_segment:
            start_day_str, end_day_str = day_segment.split('-', 1)
            start_day_idx = day_names.index(start_day_str.strip())
            end_day_idx = day_names.index(end_day_str.strip())
            
            # Handle spanning ranges (e.g., fri-mon = fri,sat,sun,mon)
            if start_day_idx > end_day_idx:
                # Spans weekend: current >= start OR current <= end
                return current_day >= start_day_idx or current_day <= end_day_idx
            else:
                # Normal range: start <= current <= end
                return start_day_idx <= current_day <= end_day_idx
        else:
            # Single day
            target_day_idx = day_names.index(day_segment)
            return current_day == target_day_idx
            
    except (ValueError, IndexError) as e:
        logger.error(f"Error parsing day segment '{day_segment}': {e}")
        return False

def _parse_day_of_month_segment(date_segment: str, current_time: datetime) -> bool:
    """
    Parse and match day of month segment.
    Handles ranges like "1-5" or "28-3" (spanning month boundary).
    
    Args:
        date_segment: Date range string (e.g., "1-5", "28-3", "15")
        current_time: Current datetime object
        
    Returns:
        bool: True if current day of month matches the segment
    """
    if not date_segment or date_segment == '*':
        return True
    
    current_day = current_time.day
    
    try:
        if '-' in date_segment:
            start_str, end_str = date_segment.split('-', 1)
            start_day = int(start_str.strip())
            end_day = int(end_str.strip())
            
            # Validate day ranges
            if not (1 <= start_day <= 31) or not (1 <= end_day <= 31):
                logger.warning(f"Invalid day range: {date_segment}")
                return False
            
            # Handle spanning month boundary (e.g., 28-3)
            if start_day > end_day:
                # Spans month boundary: current >= start OR current <= end
                return current_day >= start_day or current_day <= end_day
            else:
                # Normal range: start <= current <= end
                return start_day <= current_day <= end_day
        else:
            # Single day
            target_day = int(date_segment.strip())
            if not (1 <= target_day <= 31):
                logger.warning(f"Invalid day: {date_segment}")
                return False
            return current_day == target_day
            
    except (ValueError, IndexError) as e:
        logger.error(f"Error parsing date segment '{date_segment}': {e}")
        return False

def _parse_month_segment(month_segment: str, current_time: datetime) -> bool:
    """
    Parse and match month segment.
    Handles ranges like "jan-mar" or "nov-feb" (spanning year boundary).
    
    Args:
        month_segment: Month range string (e.g., "jan-mar", "nov-feb", "dec")
        current_time: Current datetime object
        
    Returns:
        bool: True if current month matches the segment
    """
    if not month_segment or month_segment == '*':
        return True
    
    month_names = ['jan', 'feb', 'mar', 'apr', 'may', 'jun',
                   'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
    current_month = current_time.month - 1  # 0-based for list indexing
    
    month_segment = month_segment.lower().strip()
    
    try:
        if '-' in month_segment:
            start_month_str, end_month_str = month_segment.split('-', 1)
            start_month_idx = month_names.index(start_month_str.strip())
            end_month_idx = month_names.index(end_month_str.strip())
            
            # Handle spanning year boundary (e.g., nov-feb)
            if start_month_idx > end_month_idx:
                # Spans year boundary: current >= start OR current <= end
                return current_month >= start_month_idx or current_month <= end_month_idx
            else:
                # Normal range: start <= current <= end
                return start_month_idx <= current_month <= end_month_idx
        else:
            # Single month
            target_month_idx = month_names.index(month_segment)
            return current_month == target_month_idx
            
    except (ValueError, IndexError) as e:
        logger.error(f"Error parsing month segment '{month_segment}': {e}")
        return False

def _matches_closure_rule(rule: str, current_time: datetime) -> Tuple[bool, str]:
    """
    Check if current time matches a closure rule in Asterisk 11 format.
    
    Args:
        rule: Pipe-delimited rule string (TIME|DAY|DATE|MONTH)
        current_time: Current datetime object
        
    Returns:
        Tuple[bool, str]: (matches, description)
    """
    if not rule or not rule.strip():
        return False, "Empty rule"
    
    # Split by pipe and pad with wildcards if needed
    segments = rule.split('|')
    while len(segments) < 4:
        segments.append('*')
    
    time_seg, day_seg, date_seg, month_seg = segments[:4]
    
    # All segments must match for rule to trigger
    time_matches = _parse_time_segment(time_seg.strip(), current_time)
    day_matches = _parse_day_of_week_segment(day_seg.strip(), current_time)
    date_matches = _parse_day_of_month_segment(date_seg.strip(), current_time)
    month_matches = _parse_month_segment(month_seg.strip(), current_time)
    
    if time_matches and day_matches and date_matches and month_matches:
        description = f"Closure rule: {time_seg}|{day_seg}|{date_seg}|{month_seg}"
        return True, description
    
    return False, f"Rule did not match: {rule}"

def _is_in_automation_window(current_time: datetime) -> Tuple[bool, str]:
    """
    Check if current time is within allowed automation windows.
    
    Args:
        current_time: Current datetime in El Salvador timezone
        
    Returns:
        Tuple[bool, str]: (can_automate, reason)
    """
    # Check if it's a holiday
    current_date = current_time.date()
    if current_date in EL_SALVADOR_HOLIDAYS:
        holiday_name = EL_SALVADOR_HOLIDAYS.get(current_date, "Holiday")
        return True, f"El Salvador holiday ({holiday_name}) - automation allowed all day"
    
    # Check day of week and time
    weekday = current_time.weekday()  # 0 = Monday, 6 = Sunday
    hour = current_time.hour
    minute = current_time.minute
    
    if weekday == 6:  # Sunday
        return True, "Sunday - automation allowed all day"
    elif weekday == 5:  # Saturday
        # Human required 9:10 AM - 1:00 PM, automation allowed rest of day
        if hour >= 13:  # 1:00 PM onwards
            return True, "Saturday automation hours: after 1:00 PM"
        elif hour < 9 or (hour == 9 and minute < 10):  # Before 9:10 AM
            return True, "Saturday automation hours: before 9:10 AM"
        else:
            return False, "Saturday business hours: 9:10 AM - 1:00 PM requires human agent"
    else:  # Monday-Friday
        # Human required 8:10 AM - 5:00 PM, automation allowed rest of day
        if hour >= 17:  # 5:00 PM onwards
            return True, "Weekday automation hours: after 5:00 PM"
        elif hour < 8 or (hour == 8 and minute < 10):  # Before 8:10 AM
            return True, "Weekday automation hours: before 8:10 AM"
        else:
            return False, "Weekday business hours: 8:10 AM - 5:00 PM requires human agent"

def _get_office_database_connection() -> mysql.connector.MySQLConnection:
    """
    Create database connection for office status queries with limited retries.
    Loads credentials directly from .env file for reliability.
    
    Returns:
        MySQL connection object
        
    Raises:
        mysql.connector.Error: If connection fails after OFFICE_MAX_RETRIES attempts
    """
    from dotenv import load_dotenv
    from pathlib import Path
    import os
    
    env_path = Path(__file__).parent.parent / '.env'
    load_dotenv(dotenv_path=env_path)
    
    last_error = None
    
    for attempt in range(1, OFFICE_MAX_RETRIES + 1):
        try:
            connection = mysql.connector.connect(
                host=os.getenv('OFFICE_DB_HOST', '200.31.162.218'),
                port=int(os.getenv('OFFICE_DB_PORT', '3306')),
                database=os.getenv('OFFICE_DB_NAME', 'asterisk'),
                user=os.getenv('OFFICE_DB_USER', ''),
                password=os.getenv('OFFICE_DB_PASSWORD', ''),
                charset='utf8',
                autocommit=True,
                connection_timeout=60
            )
            if attempt > 1:
                logger.info(f"[OFFICE_DB] Successfully connected on attempt #{attempt}")
            return connection
        except mysql.connector.Error as e:
            last_error = e
            logger.warning(f"[OFFICE_DB] Connection attempt #{attempt}/{OFFICE_MAX_RETRIES} failed: {e}")
            if attempt < OFFICE_MAX_RETRIES:
                time.sleep(OFFICE_RETRY_DELAY_SECONDS)
    
    logger.error(f"[OFFICE_DB] All {OFFICE_MAX_RETRIES} connection attempts failed. Giving up.")
    raise last_error

def check_office_status() -> Dict[str, any]:
    """
    Check customer service office status and automation eligibility.
    Uses limited retry (3 attempts) for database operations.
    If database check fails, defaults to allowing automation so bookings can proceed.
    
    This is the main tool function called by the assistant before making bookings.
    
    Returns:
        Dict containing:
        - office_status: "open" | "closed"
        - reason: Human-readable explanation
        - can_automate: bool
        - automation_reason: Human-readable automation explanation
    """
    # Get current time in El Salvador timezone
    current_time = datetime.now(EL_SALVADOR_TZ)
    logger.info(f"[OFFICE_STATUS] Checking office status at {current_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    
    # Query database for closure rules with limited retry
    closure_rules = []
    try:
        connection = _get_office_database_connection()
        try:
            cursor = connection.cursor()
            query = "SELECT time FROM timegroups_details WHERE timegroupid = 3"
            cursor.execute(query)
            closure_rules = cursor.fetchall()
            logger.info(f"[OFFICE_STATUS] Retrieved {len(closure_rules)} closure rules from database")
        finally:
            if connection and connection.is_connected():
                try:
                    cursor.close()
                except:
                    pass
                connection.close()
    except Exception as e:
        # Database failed after 3 retries - default to allowing automation
        logger.error(f"[OFFICE_STATUS] Database check failed after {OFFICE_MAX_RETRIES} retries: {e}")
        logger.info("[OFFICE_STATUS] Defaulting to can_automate=True so bookings can proceed")
        return {
            "office_status": "unknown",
            "reason": f"Database unavailable after {OFFICE_MAX_RETRIES} retries - defaulting to automation",
            "can_automate": True,
            "automation_reason": "Database check failed - allowing automation so bookings can proceed"
        }
    
    # Check if any closure rule matches current time
    office_closed = False
    closure_reason = "Open - no matching closure rules in database"
    
    for rule_tuple in closure_rules:
        rule = rule_tuple[0] if rule_tuple and rule_tuple[0] else ""
        matches, description = _matches_closure_rule(rule, current_time)
        
        if matches:
            office_closed = True
            closure_reason = f"Closed per database rule: {description}"
            logger.info(f"[OFFICE_STATUS] Office closed - matched rule: {rule}")
            break
    
    # Determine office status
    office_status = "closed" if office_closed else "open"
    
    # Determine automation eligibility
    if office_closed:
        can_automate = True
        automation_reason = "Office closed per database - assistant handles all bookings during closures"
    else:
        can_automate, automation_reason = _is_in_automation_window(current_time)
    
    result = {
        "office_status": office_status,
        "reason": closure_reason,
        "can_automate": can_automate,
        "automation_reason": automation_reason
    }
    
    logger.info(f"[OFFICE_STATUS] Result: {result}")
    return result
