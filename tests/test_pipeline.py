import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support import FakeFetch, Harness
from ytdlparr import jobs


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.harness = Harness()

    def tearDown(self):
        self.harness.close()

    def queued_job(self, **kwargs):
        job_id = self.harness.addfile(**kwargs)["nzo_ids"][0]
        return self.harness.store.update(job_id, state=jobs.DOWNLOADING)

    def test_download_then_move_lands_in_the_category_folder(self):
        job = self.queued_job()

        with patch("ytdlparr.fetcher.run", FakeFetch()):
            self.harness.pipeline.download(job)

        self.assertEqual(job.state, jobs.WAITING_TO_MOVE)
        self.assertEqual(job.bytes_done, 100)

        self.harness.pipeline.move(job)

        expected = Path(self.harness.config["paths"]["complete"]) / "tv" / job.name
        self.assertEqual(job.state, jobs.COMPLETED)
        self.assertEqual(job.storage, str(expected))
        self.assertEqual(
            sorted(p.name for p in expected.iterdir()), [f"{job.name}.mkv"]
        )
        self.assertFalse(self.harness.pipeline.work_dir(job).exists())

    def test_sweep_removes_cleanup_patterns_but_not_scratch_files_that_were_never_moved(self):
        job = self.queued_job()

        with patch("ytdlparr.fetcher.run", FakeFetch()):
            self.harness.pipeline.download(job)

        self.harness.pipeline.move(job)

        moved = list(Path(job.storage).iterdir())
        self.assertFalse(any(p.suffix == ".description" for p in moved))
        self.assertFalse(any(p.suffix == ".part" for p in moved))

    def test_retries_then_fails_with_the_reason(self):
        job = self.queued_job()
        fetch = FakeFetch(fail_times=5)

        with patch("ytdlparr.fetcher.run", fetch):
            self.harness.pipeline.download(job)
            self.assertEqual(job.state, jobs.QUEUED)
            self.assertEqual(job.attempts, 1)

            self.harness.store.update(job.id, state=jobs.DOWNLOADING)
            self.harness.pipeline.download(job)

        self.assertEqual(job.state, jobs.FAILED)
        self.assertEqual(job.fail_message, "HTTP Error 403")
        self.assertFalse(self.harness.pipeline.work_dir(job).exists())

    def test_a_retry_that_succeeds_clears_nothing_it_should_not(self):
        job = self.queued_job()

        with patch("ytdlparr.fetcher.run", FakeFetch(fail_times=1)):
            self.harness.pipeline.download(job)
            self.harness.store.update(job.id, state=jobs.DOWNLOADING)
            self.harness.pipeline.download(job)

        self.assertEqual(job.state, jobs.WAITING_TO_MOVE)

    def test_cancel_mid_download_discards_the_job_and_its_files(self):
        job = self.queued_job()
        self.harness.pipeline.cancel(job.id)

        with patch("ytdlparr.fetcher.run", FakeFetch(cancel_at=1)):
            self.harness.pipeline.download(job)

        self.assertIsNone(self.harness.store.get(job.id))
        self.assertFalse(self.harness.pipeline.work_dir(job).exists())

    def test_move_failure_is_reported_not_swallowed(self):
        job = self.queued_job()

        with patch("ytdlparr.fetcher.run", FakeFetch()):
            self.harness.pipeline.download(job)

        # Make the destination unwritable by making it a file.
        complete = Path(self.harness.config["paths"]["complete"])
        complete.parent.mkdir(parents=True, exist_ok=True)
        complete.write_text("not a directory")

        self.harness.pipeline.move(job)

        self.assertEqual(job.state, jobs.FAILED)
        self.assertIn("move failed", job.fail_message)


class WorkDirTest(unittest.TestCase):
    def setUp(self):
        self.harness = Harness()

    def tearDown(self):
        self.harness.close()

    def test_named_after_the_job_and_remembered(self):
        job = self.harness.store.get(self.harness.addfile(name="Show - S01E01 - Pilot")["nzo_ids"][0])

        path = self.harness.pipeline.work_dir(job)

        self.assertEqual(path.name, "Show - S01E01 - Pilot")
        self.assertEqual(job.work_dir, str(path))

    def test_a_second_job_with_the_same_name_gets_the_id_suffix(self):
        first = self.harness.store.get(self.harness.addfile(name="Same")["nzo_ids"][0])
        second = self.harness.store.get(self.harness.addfile(name="Same")["nzo_ids"][0])
        self.harness.pipeline.work_dir(first)

        self.assertEqual(self.harness.pipeline.work_dir(second).name, f"Same [{second.id[-6:]}]")

    def test_a_directory_renamed_by_hand_is_adopted_not_suffixed(self):
        job = self.harness.store.get(self.harness.addfile(name="Renamed")["nzo_ids"][0])
        (Path(self.harness.config["paths"]["incomplete"]) / "Renamed").mkdir(parents=True)

        self.assertEqual(self.harness.pipeline.work_dir(job).name, "Renamed")

    def test_a_job_from_before_keeps_its_id_directory(self):
        job = self.harness.store.get(self.harness.addfile(name="Old")["nzo_ids"][0])
        legacy = Path(self.harness.config["paths"]["incomplete"]) / job.id
        legacy.mkdir(parents=True)

        self.assertEqual(self.harness.pipeline.work_dir(job), legacy)

    def test_slashes_cannot_escape_the_incomplete_dir(self):
        job = self.harness.store.get(self.harness.addfile(name="../evil/../x")["nzo_ids"][0])

        self.assertEqual(self.harness.pipeline.work_dir(job).parent, Path(self.harness.config["paths"]["incomplete"]))
