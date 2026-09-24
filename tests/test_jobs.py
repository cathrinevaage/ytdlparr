import tempfile
import unittest
from pathlib import Path

from ytdlparr import jobs
from ytdlparr.jobs import Job, JobStore


def job(name, **overrides):
    fields = {"id": jobs.new_id(), "name": name, "category": "tv",
              "spec": {"url": "u"}}

    return Job(**{**fields, **overrides})


class JobStoreTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "state" / "jobs.json"

    def tearDown(self):
        self.directory.cleanup()

    def test_persists_across_restarts(self):
        JobStore(self.path, 200).add(job("a"))

        self.assertEqual([j.name for j in JobStore(self.path, 200).active()], ["a"])

    def test_force_jumps_the_queue(self):
        store = JobStore(self.path, 200)
        store.add(job("first", added=1))
        store.add(job("urgent", added=2, priority="force"))
        store.add(job("high", added=3, priority="high"))

        self.assertEqual(
            [j.name for j in store.active()], ["urgent", "high", "first"]
        )

    def test_claim_next_moves_exactly_one_job(self):
        store = JobStore(self.path, 200)
        store.add(job("a", added=1))
        store.add(job("b", added=2))

        claimed = store.claim_next()

        self.assertEqual(claimed.name, "a")
        self.assertEqual(claimed.state, jobs.DOWNLOADING)
        self.assertEqual(store.claim_next().name, "b")
        self.assertIsNone(store.claim_next())

    def test_retention_trims_only_finished_jobs(self):
        store = JobStore(self.path, keep_jobs=2)
        store.add(job("active", added=0))

        for index in range(4):
            store.add(job(f"done{index}", state=jobs.COMPLETED, finished=index))

        self.assertEqual([j.name for j in store.finished()], ["done3", "done2"])
        self.assertEqual([j.name for j in store.active()], ["active"])

    def test_survives_a_corrupt_state_file(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("[{not json")

        self.assertEqual(JobStore(self.path, 200).active(), [])
