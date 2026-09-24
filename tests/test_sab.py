"""The queue and history shapes are what Sonarr acts on; a wrong
status string here blocklists a release that succeeded."""

import unittest

from ytdlparr import jobs, sab
from ytdlparr.jobs import Job


def job(state, **overrides):
    fields = {"id": "SABnzbd_nzo_abc", "name": "Show - S01E01", "category": "tv-example",
              "spec": {}, "state": state}

    return Job(**{**fields, **overrides})


class QueueTest(unittest.TestCase):
    def test_progress_is_derived_from_bytes(self):
        slot = sab.queue_slot(
            job(jobs.DOWNLOADING, bytes_done=750 << 20, bytes_total=1500 << 20,
                eta=300),
            0,
        )

        self.assertEqual(slot["percentage"], "50")
        self.assertEqual(slot["mb"], "1500.00")
        self.assertEqual(slot["mbleft"], "750.00")
        self.assertEqual(slot["timeleft"], "0:05:00")
        self.assertEqual(slot["status"], "Downloading")

    def test_waiting_for_the_move_window_reads_as_queued(self):
        slot = sab.queue_slot(job(jobs.WAITING_TO_MOVE), 0)

        self.assertEqual(slot["status"], "Queued")

    def test_unknown_total_does_not_divide_by_zero(self):
        self.assertEqual(sab.queue_slot(job(jobs.DOWNLOADING), 0)["percentage"], "0")

    def test_idle_queue(self):
        self.assertEqual(sab.queue([])["queue"]["status"], "Idle")


class HistoryTest(unittest.TestCase):
    def test_completed_carries_the_storage_path(self):
        slot = sab.history_slot(
            job(jobs.COMPLETED, storage="/downloads/complete/tv/Show - S01E01")
        )

        self.assertEqual(slot["status"], "Completed")
        self.assertEqual(slot["storage"], "/downloads/complete/tv/Show - S01E01")
        self.assertEqual(slot["fail_message"], "")

    def test_failed_carries_the_reason(self):
        slot = sab.history_slot(job(jobs.FAILED, fail_message="geo-blocked"))

        self.assertEqual(slot["status"], "Failed")
        self.assertEqual(slot["fail_message"], "geo-blocked")


class PriorityTest(unittest.TestCase):
    def test_maps_sab_numbers_including_sonarrs_default(self):
        self.assertEqual(sab.priority_from_request("-100"), "normal")
        self.assertEqual(sab.priority_from_request("2"), "force")
        self.assertEqual(sab.priority_from_request("-1"), "low")


class SizeTest(unittest.TestCase):
    def test_human_sizes(self):
        self.assertEqual(sab.human_size(512), "512 B")
        self.assertEqual(sab.human_size(1536), "1.5 KB")
        self.assertEqual(sab.human_size(1500 << 20), "1.5 GB")
