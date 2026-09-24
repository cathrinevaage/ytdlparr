"""The queue consumer. Download workers take QUEUED jobs; one mover
takes WAITING_TO_MOVE jobs when the move window is open. Separate
threads so a job waiting hours for the window does not hold a
download slot."""

import logging
import threading
import time

from . import jobs, notify
from .config import parse_size
from .disk import has_room
from .windows import is_open

log = logging.getLogger(__name__)

POLL_SECONDS = 1.0


class Worker:
    def __init__(self, config, store, pipeline, notifier):
        self.config = config
        self.store = store
        self.pipeline = pipeline
        self.notifier = notifier
        self.stopping = threading.Event()
        self.paused = False
        self.pause_reason = ""
        self.threads = []

    # -- lifecycle -----------------------------------------------------

    def start(self):
        self.recover()

        count = self.config["limits"]["max_concurrent"]
        self.threads = [
            threading.Thread(target=self.download_loop, name=f"download-{index}", daemon=True)
            for index in range(count)
        ] + [threading.Thread(target=self.move_loop, name="mover", daemon=True)]

        for thread in self.threads:
            thread.start()

    def stop(self):
        self.stopping.set()

    def recover(self):
        """Anything mid-flight when the process died goes back to the
        start of its stage. Downloads resume from .part files."""
        for job in self.store.active():
            if job.state in (jobs.DOWNLOADING, jobs.POSTPROCESSING):
                self.store.update(job.id, state=jobs.QUEUED)
            elif job.state == jobs.MOVING:
                self.store.update(job.id, state=jobs.WAITING_TO_MOVE)

    # -- gates ---------------------------------------------------------

    def download_allowed(self, job):
        """Force ignores the paused state and the schedule."""
        if job.is_force():
            return True

        if self.paused:
            return False

        schedule = self.config["schedule"]

        if not is_open(schedule["download"], schedule["timezone"]):
            return False

        if self.config["limits"]["pause_downloads_during_postprocess"]:
            if any(j.state == jobs.POSTPROCESSING for j in self.store.active()):
                return False

        return True

    def move_allowed(self, job):
        if job.is_force():
            return True

        schedule = self.config["schedule"]

        return is_open(schedule["move"], schedule["timezone"])

    def room_for(self, which, job):
        """Disk check for one stage. on_low_space decides whether the
        job fails or the whole queue pauses."""
        path = self.config["paths"][which]
        minimum = parse_size(self.config["disk"][f"min_free_{which}"])

        if has_room(path, minimum):
            return True

        message = f"less than {self.config['disk'][f'min_free_{which}']} free on {path}"
        self.notifier.send(notify.LOW_SPACE, "Low disk space", message)

        if self.config["disk"]["on_low_space"] == "pause":
            self.pause(message)
        else:
            self.store.update(
                job.id, state=jobs.FAILED, fail_message=message,
                finished=time.time(),
            )

        return False

    def pause(self, reason):
        if not self.paused:
            self.paused = True
            self.pause_reason = reason
            self.notifier.send(notify.QUEUE_PAUSED, "Queue paused", reason)

    def resume(self):
        self.paused = False
        self.pause_reason = ""

    # -- loops ---------------------------------------------------------

    def next_download(self):
        """The first QUEUED job whose gates are open and whose retry
        backoff has elapsed."""
        now = time.time()

        for job in self.store.active():
            if job.state != jobs.QUEUED or job.added > now:
                continue

            if not self.download_allowed(job):
                continue

            claimed = self.store.update(job.id, state=jobs.DOWNLOADING)

            return claimed

        return None

    def download_loop(self):
        while not self.stopping.is_set():
            if not self.guarded(self.download_tick):
                time.sleep(POLL_SECONDS)

    def guarded(self, tick):
        """One poll iteration. A bug in a gate - a bad timezone name, a
        path that cannot be statted - must log and retry, never end the
        thread while the API keeps answering as if nothing happened."""
        try:
            return tick()
        except Exception:
            log.exception("%s: poll failed", threading.current_thread().name)
            return False

    def download_tick(self):
        """Claim and run one job. True when a job ran."""
        with self.store.lock:
            job = self.next_download()

            if job is not None and not self.room_for("incomplete", job):
                if job.state == jobs.DOWNLOADING:
                    self.store.update(job.id, state=jobs.QUEUED)

                job = None

        if job is None:
            return False

        self.run_download(job)

        return True

    def run_download(self, job):
        log.info("downloading %s", job.name)

        try:
            self.pipeline.download(job)
        except Exception as error:  # a bug, not a fetch failure
            log.exception("%s crashed", job.name)
            self.store.update(
                job.id, state=jobs.FAILED, fail_message=f"internal error: {error}",
                finished=time.time(),
            )

        refreshed = self.store.get(job.id)

        if refreshed is not None and refreshed.state == jobs.FAILED:
            self.notifier.send(
                notify.JOB_FAILED, f"Failed: {job.name}", refreshed.fail_message
            )

    def move_loop(self):
        while not self.stopping.is_set():
            if not self.guarded(self.move_tick):
                time.sleep(POLL_SECONDS)

    def move_tick(self):
        job = self.next_move()

        if job is None:
            return False

        log.info("moving %s", job.name)
        self.pipeline.move(job)

        return True

    def next_move(self):
        with self.store.lock:
            for job in self.store.active():
                if job.state != jobs.WAITING_TO_MOVE:
                    continue

                if not self.move_allowed(job):
                    continue

                if not self.room_for("complete", job):
                    return None

                return self.store.update(job.id, state=jobs.MOVING)

        return None
