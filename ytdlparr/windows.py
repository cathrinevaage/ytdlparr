"""Schedule windows. A job step outside its window waits, and Sonarr
sees it queued - the same thing it sees during a SAB unpack."""

from datetime import datetime
from zoneinfo import ZoneInfo

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def minutes(clock):
    """"09:30" -> 570. "24:00" is allowed as an end-of-day bound."""
    hours, mins = clock.split(":")

    return int(hours) * 60 + int(mins)


def covers(window, moment):
    """Whether one window contains a local moment."""
    if DAYS[moment.weekday()] not in window.get("days", DAYS):
        return False

    now = moment.hour * 60 + moment.minute

    return minutes(window["from"]) <= now < minutes(window["to"])


def is_open(windows, timezone, now=None):
    """Whether any window is open. No windows at all means always."""
    if not windows:
        return True

    moment = (now or datetime.now(ZoneInfo(timezone))).astimezone(
        ZoneInfo(timezone)
    )

    return any(covers(window, moment) for window in windows)
