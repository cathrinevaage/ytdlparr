"""A configured app over temp dirs, with the fetcher swapped out."""

import tempfile
from pathlib import Path

from ytdlparr import fetcher
from ytdlparr.app import create_app
from ytdlparr.config import DEFAULTS, merge
from ytdlparr.jobs import JobStore
from ytdlparr.notify import Notifier
from ytdlparr.pipeline import Pipeline
from ytdlparr.worker import Worker

NZB_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb"><head>
<meta type="name">{name}</meta>
<meta type="ytdlpspec">{{"url": "https://example.test/v/1", "name": "{name}", "container": "mkv"}}</meta>
</head></nzb>"""


def NZB(name):
    return NZB_TEMPLATE.format(name=name).encode()


class Harness:
    def __init__(self, **overrides):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.config = merge(DEFAULTS, merge({
            "server": {"api_key": "k"},
            "paths": {
                "incomplete": str(root / "incomplete"),
                "complete": str(root / "complete"),
                "state": str(root / "config" / "jobs.json"),
            },
            "limits": {"retries": 2, "retry_backoff": 0},
            "disk": {"min_free_incomplete": "0", "min_free_complete": "0"},
            "categories": {"tv": {"dir": "tv"}},
            "notifications": {"apprise_urls": [], "on": []},
        }, overrides))
        self.store = JobStore(self.config["paths"]["state"], 200)
        self.pipeline = Pipeline(self.config, self.store)
        self.notifier = Notifier([], [])
        self.worker = Worker(self.config, self.store, self.pipeline, self.notifier)
        self.app = create_app(self.config, self.store, self.pipeline, self.worker)
        self.client = self.app.test_client()

    def close(self):
        self.directory.cleanup()

    def api(self, **params):
        return self.client.get("/api", query_string={"apikey": "k", "output": "json", **params}).json

    def addfile(self, name="Show - S01E01 - Pilot", category="tv", priority="0"):
        response = self.client.post(
            "/api",
            data={"apikey": "k", "mode": "addfile", "cat": category, "priority": priority,
                  "name": (__import__("io").BytesIO(NZB(name=name)), f"{name}.nzb")},
            content_type="multipart/form-data",
        )

        return response.json


class FakeFetch:
    """Stands in for fetcher.run: writes a file, or fails N times."""

    def __init__(self, fail_times=0, cancel_at=None):
        self.fail_times = fail_times
        self.cancel_at = cancel_at
        self.calls = 0

    def __call__(self, url, params, on_progress, on_postprocess, is_cancelled):
        self.calls += 1
        work_dir = Path(params["outtmpl"]).parent

        on_progress({"filename": "a", "downloaded_bytes": 50, "total_bytes": 100, "speed": 10.0, "eta": 5})

        if self.cancel_at is not None and self.calls >= self.cancel_at:
            raise fetcher.Cancelled()

        if self.calls <= self.fail_times:
            raise fetcher.FetchFailed("HTTP Error 403")

        on_progress({"filename": "a", "downloaded_bytes": 100, "total_bytes": 100, "speed": 0, "eta": 0})
        on_postprocess({"status": "started", "postprocessor": "Merger"})

        stem = params["outtmpl"].rsplit(".", 1)[0]
        Path(stem + ".mkv").write_bytes(b"video")
        Path(stem + ".description").write_text("junk")
        (work_dir / "leftover.part").write_bytes(b"")

        return {}
