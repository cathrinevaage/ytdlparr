import unittest
from unittest.mock import patch

from tests.support import Harness
from ytdlparr import jobs


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.harness = Harness()

    def tearDown(self):
        self.harness.close()

    def test_wrong_key_is_refused_the_way_sab_refuses(self):
        response = self.harness.client.get("/api?mode=version&apikey=wrong")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json["error"], "API Key Incorrect")

    def test_version_and_config_carry_what_sonarr_checks(self):
        self.assertEqual(self.harness.api(mode="version"), {"version": "4.2.0"})

        config = self.harness.api(mode="get_config")["config"]

        self.assertEqual(config["misc"]["complete_dir"], self.harness.config["paths"]["complete"])
        self.assertEqual(config["misc"]["enable_tv_sorting"], 0)
        self.assertEqual([c["name"] for c in config["categories"]], ["*", "tv"])
        self.assertEqual(config["categories"][1]["dir"], "tv")

    def test_addfile_queues_a_job_from_the_faux_nzb(self):
        result = self.harness.addfile(priority="2")

        self.assertTrue(result["status"])
        job = self.harness.store.get(result["nzo_ids"][0])
        self.assertEqual(job.spec["url"], "https://example.test/v/1")
        self.assertEqual(job.category, "tv")
        self.assertEqual(job.priority, "force")

        slot = self.harness.api(mode="queue")["queue"]["slots"][0]
        self.assertEqual(slot["status"], "Queued")
        self.assertEqual(slot["nzo_id"], job.id)
        self.assertEqual(slot["cat"], "tv")

    def test_addfile_rejects_a_real_nzb(self):
        import io
        response = self.harness.client.post("/api", data={
            "apikey": "k", "mode": "addfile", "cat": "tv",
            "name": (io.BytesIO(b"<nzb><file/></nzb>"), "real.nzb"),
        }, content_type="multipart/form-data")

        self.assertFalse(response.json["status"])

    def test_queue_delete_removes_a_queued_job(self):
        job_id = self.harness.addfile()["nzo_ids"][0]

        self.harness.api(mode="queue", name="delete", value=job_id, del_files="1")

        self.assertIsNone(self.harness.store.get(job_id))

    def test_history_delete_removes_a_finished_job(self):
        job_id = self.harness.addfile()["nzo_ids"][0]
        self.harness.store.update(job_id, state=jobs.COMPLETED, finished=1)

        self.harness.api(mode="history", name="delete", value=job_id)

        self.assertIsNone(self.harness.store.get(job_id))

    def test_history_filters_by_category(self):
        a = self.harness.addfile(name="a", category="tv")["nzo_ids"][0]
        b = self.harness.addfile(name="b", category="movies")["nzo_ids"][0]
        self.harness.store.update(a, state=jobs.COMPLETED, finished=1)
        self.harness.store.update(b, state=jobs.COMPLETED, finished=2)

        slots = self.harness.api(mode="history", category="tv")["history"]["slots"]

        self.assertEqual([s["name"] for s in slots], ["a"])
