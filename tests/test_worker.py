import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from tests.support import Harness
from ytdlparr import jobs

NIGHT_ONLY = {"schedule": {
    "timezone": "Europe/Berlin",
    "download": [{"days": ["mon","tue","wed","thu","fri","sat","sun"], "from": "00:00", "to": "24:00"}],
    "move": [{"days": ["mon","tue","wed","thu","fri","sat","sun"], "from": "01:00", "to": "05:00"}],
}}


class GateTest(unittest.TestCase):
    def setUp(self):
        self.harness = Harness(**NIGHT_ONLY)

    def tearDown(self):
        self.harness.close()

    def job(self, priority="0"):
        return self.harness.store.get(self.harness.addfile(priority=priority)["nzo_ids"][0])

    def test_move_waits_for_its_window(self):
        job = self.job()
        self.harness.store.update(job.id, state=jobs.WAITING_TO_MOVE)
        noon = datetime(2026, 9, 23, 12, tzinfo=ZoneInfo("Europe/Berlin"))

        with patch("ytdlparr.worker.is_open", side_effect=lambda w, tz: False):
            self.assertIsNone(self.harness.worker.next_move())

        self.assertEqual(job.state, jobs.WAITING_TO_MOVE)

    def test_force_bypasses_the_window_and_the_pause(self):
        forced = self.job(priority="2")
        self.harness.store.update(forced.id, state=jobs.WAITING_TO_MOVE)
        self.harness.worker.pause("test")

        with patch("ytdlparr.worker.is_open", return_value=False):
            self.assertEqual(self.harness.worker.next_move().id, forced.id)
            self.harness.store.update(forced.id, state=jobs.QUEUED)
            self.assertEqual(self.harness.worker.next_download().id, forced.id)

    def test_pause_holds_normal_downloads(self):
        self.job()
        self.harness.worker.pause("test")

        self.assertIsNone(self.harness.worker.next_download())

        self.harness.worker.resume()
        self.assertIsNotNone(self.harness.worker.next_download())

    def test_postprocessing_holds_new_downloads_when_configured(self):
        busy = self.job()
        self.harness.store.update(busy.id, state=jobs.POSTPROCESSING)
        self.job()

        self.assertIsNone(self.harness.worker.next_download())

    def test_retry_backoff_is_honoured(self):
        job = self.job()
        self.harness.store.update(job.id, added=job.added + 3600)

        self.assertIsNone(self.harness.worker.next_download())

    def test_recover_requeues_what_was_mid_flight(self):
        a = self.job()
        b = self.job()
        self.harness.store.update(a.id, state=jobs.DOWNLOADING)
        self.harness.store.update(b.id, state=jobs.MOVING)

        self.harness.worker.recover()

        self.assertEqual(a.state, jobs.QUEUED)
        self.assertEqual(b.state, jobs.WAITING_TO_MOVE)


class GuardTest(unittest.TestCase):
    """A failing gate must not end a worker thread."""

    def setUp(self):
        self.harness = Harness(schedule={"timezone": "Not/AZone", "download": [
            {"days": ["mon"], "from": "00:00", "to": "24:00"}], "move": []})

    def tearDown(self):
        self.harness.close()

    def test_bad_timezone_is_logged_not_fatal(self):
        self.harness.addfile()

        with self.assertLogs("ytdlparr.worker", level="ERROR") as logged:
            ran = self.harness.worker.guarded(self.harness.worker.download_tick)

        self.assertFalse(ran)
        self.assertIn("poll failed", logged.output[0])

    def test_a_clean_tick_reports_whether_it_ran_a_job(self):
        self.assertFalse(self.harness.worker.guarded(self.harness.worker.move_tick))
