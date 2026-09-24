"""Windows decide when the noisy step runs; off-by-one at the edges
means a 23:00 cutoff that writes to the library at 23:00."""

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from ytdlparr.windows import is_open

ZONE = "Europe/Berlin"
WEEKDAY_EVENINGS = [
    {"days": ["mon", "tue", "wed", "thu", "fri"], "from": "09:00", "to": "23:00"},
    {"days": ["sat", "sun"], "from": "10:00", "to": "23:00"},
]


def local(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(ZONE))


class WindowTest(unittest.TestCase):
    def test_inside_a_weekday_window(self):
        # 2026-09-23 is a Wednesday
        self.assertTrue(is_open(WEEKDAY_EVENINGS, ZONE, local(2026, 9, 23, 12)))

    def test_start_is_inclusive_and_end_is_exclusive(self):
        self.assertTrue(is_open(WEEKDAY_EVENINGS, ZONE, local(2026, 9, 23, 9, 0)))
        self.assertFalse(is_open(WEEKDAY_EVENINGS, ZONE, local(2026, 9, 23, 23, 0)))
        self.assertTrue(is_open(WEEKDAY_EVENINGS, ZONE, local(2026, 9, 23, 22, 59)))

    def test_weekend_uses_its_own_window(self):
        # 2026-09-26 is a Saturday
        self.assertFalse(is_open(WEEKDAY_EVENINGS, ZONE, local(2026, 9, 26, 9, 30)))
        self.assertTrue(is_open(WEEKDAY_EVENINGS, ZONE, local(2026, 9, 26, 10, 0)))

    def test_the_night_is_closed(self):
        self.assertFalse(is_open(WEEKDAY_EVENINGS, ZONE, local(2026, 9, 23, 3)))

    def test_twenty_four_hundred_means_the_whole_day(self):
        always = [{"days": ["wed"], "from": "00:00", "to": "24:00"}]

        self.assertTrue(is_open(always, ZONE, local(2026, 9, 23, 23, 59)))

    def test_no_windows_means_always_open(self):
        self.assertTrue(is_open([], ZONE, local(2026, 9, 23, 3)))

    def test_evaluates_in_the_configured_timezone(self):
        # 22:30 UTC on a Wednesday is 00:30 Thursday in Berlin - closed.
        utc = datetime(2026, 9, 23, 22, 30, tzinfo=ZoneInfo("UTC"))

        self.assertFalse(is_open(WEEKDAY_EVENINGS, ZONE, utc))
