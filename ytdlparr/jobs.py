"""Jobs: what Sonarr asked for, where it got to, and where it ended
up. Persisted as one JSON file, trimmed to a retention limit."""

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Queue states, in the order a job moves through them.
QUEUED = "queued"
DOWNLOADING = "downloading"
POSTPROCESSING = "postprocessing"
WAITING_TO_MOVE = "waiting_to_move"
MOVING = "moving"
# Terminal states - these are what history shows.
COMPLETED = "completed"
FAILED = "failed"

ACTIVE = (QUEUED, DOWNLOADING, POSTPROCESSING, WAITING_TO_MOVE, MOVING)
FINISHED = (COMPLETED, FAILED)

PRIORITIES = {"low": -1, "normal": 0, "high": 1, "force": 2}


def new_id():
    return f"SABnzbd_nzo_{uuid.uuid4().hex[:12]}"


@dataclass
class Job:
    id: str
    name: str
    category: str
    spec: dict
    priority: str = "normal"
    state: str = QUEUED
    added: float = field(default_factory=time.time)
    started: float = 0.0
    finished: float = 0.0
    bytes_done: int = 0
    bytes_total: int = 0
    speed: float = 0.0            # bytes per second, from yt-dlp
    eta: int = 0                  # seconds, from yt-dlp
    attempts: int = 0
    storage: str = ""             # the final path Sonarr imports from
    fail_message: str = ""

    def is_active(self):
        return self.state in ACTIVE

    def is_finished(self):
        return self.state in FINISHED

    def is_force(self):
        return self.priority == "force"

    def sort_key(self):
        """Force first, then by priority, then arrival."""
        return (-PRIORITIES.get(self.priority, 0), self.added)


class JobStore:
    """All jobs, in memory, mirrored to disk on every change. One
    lock; the volume of jobs here never justifies more."""

    def __init__(self, path, keep_jobs):
        self.path = Path(path)
        self.keep_jobs = keep_jobs
        self.lock = threading.RLock()
        self.jobs = {job.id: job for job in self._read()}

    def _read(self):
        if not self.path.exists():
            return []

        try:
            return [Job(**row) for row in json.loads(self.path.read_text())]
        except (OSError, ValueError, TypeError):
            return []

    def _write(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rows = [asdict(job) for job in self.jobs.values()]
        self.path.write_text(json.dumps(rows, ensure_ascii=False, indent=2))

    def add(self, job):
        with self.lock:
            self.jobs[job.id] = job
            self._trim()
            self._write()

        return job

    def get(self, job_id):
        return self.jobs.get(job_id)

    def update(self, job_id, **changes):
        """Mutate one job in place and persist it."""
        with self.lock:
            job = self.jobs[job_id]

            for key, value in changes.items():
                setattr(job, key, value)

            self._write()

        return job

    def remove(self, job_id):
        with self.lock:
            removed = self.jobs.pop(job_id, None)

            if removed is not None:
                self._write()

        return removed

    def active(self):
        return sorted(
            (job for job in self.jobs.values() if job.is_active()),
            key=Job.sort_key,
        )

    def finished(self):
        return sorted(
            (job for job in self.jobs.values() if job.is_finished()),
            key=lambda job: job.finished,
            reverse=True,
        )

    def claim_next(self, state_from=QUEUED, state_to=DOWNLOADING):
        """Atomically take the next job in one state into another, so
        two workers cannot start the same job."""
        with self.lock:
            candidates = [
                job for job in self.active() if job.state == state_from
            ]

            if not candidates:
                return None

            job = candidates[0]
            job.state = state_to
            self._write()

            return job

    def _trim(self):
        """History retention: keep the newest N finished jobs. Active
        jobs are never trimmed."""
        surplus = self.finished()[self.keep_jobs:]

        for job in surplus:
            self.jobs.pop(job.id, None)
