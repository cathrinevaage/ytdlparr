"""One job, start to finish: fetch and mux into the incomplete dir on
local disk, then move the finished result to the complete dir on the
library storage. Faster muxing, one network write, and Sonarr sees a file that
appeared whole."""

import fnmatch
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

from . import fetcher, jobs
from .categories import resolve

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, config, store, ffmpeg_dir=None):
        self.config = config
        self.store = store
        self.ffmpeg_dir = ffmpeg_dir
        self.cancelled = set()

    # -- paths ---------------------------------------------------------

    def work_dir(self, job):
        return Path(self.config["paths"]["incomplete"]) / job.id

    def destination(self, job):
        """<complete>/<category dir>/<job name>/ - SAB's layout, and
        the folder Sonarr imports from."""
        options = resolve(self.config["categories"], job.category, job.spec)

        return (
            Path(self.config["paths"]["complete"])
            / options.get("dir", "")
            / job.name
        )

    # -- download stage ------------------------------------------------

    def download(self, job):
        """Fetch and post-process into the work dir. Moves the job to
        WAITING_TO_MOVE on success, FAILED when retries are exhausted,
        or removes it when it was cancelled."""
        options = resolve(self.config["categories"], job.category, job.spec)
        work_dir = self.work_dir(job)
        work_dir.mkdir(parents=True, exist_ok=True)

        params = fetcher.build_options(
            job.spec, options, self.config["limits"], self.config["cookies"],
            work_dir, self.ffmpeg_dir,
        )

        totals = {}

        def on_progress(event):
            filename = event.get("filename", "")
            totals[filename] = (
                event.get("downloaded_bytes", 0),
                event.get("total_bytes")
                or event.get("total_bytes_estimate")
                or 0,
            )
            self.store.update(
                job.id,
                bytes_done=sum(done for done, _ in totals.values()),
                bytes_total=sum(total for _, total in totals.values()),
                speed=event.get("speed") or 0.0,
                eta=event.get("eta") or 0,
            )

        def on_postprocess(event):
            if job.state != jobs.POSTPROCESSING:
                self.store.update(job.id, state=jobs.POSTPROCESSING, speed=0.0, eta=0)

        try:
            self.store.update(job.id, started=job.started or time.time())
            fetcher.run(
                job.spec["url"], params, on_progress, on_postprocess,
                lambda: job.id in self.cancelled,
            )
        except fetcher.Cancelled:
            self.discard(job)
            return
        except fetcher.FetchFailed as error:
            self.fail_or_retry(job, str(error))
            return

        self.store.update(
            job.id,
            state=jobs.WAITING_TO_MOVE,
            bytes_done=job.bytes_total or job.bytes_done,
            speed=0.0,
            eta=0,
        )

    def fail_or_retry(self, job, message):
        attempts = job.attempts + 1
        limit = self.config["limits"]["retries"]

        if attempts >= limit:
            log.error("%s failed after %d attempts: %s", job.name, attempts, message)
            self.store.update(
                job.id, state=jobs.FAILED, attempts=attempts,
                fail_message=message, finished=time.time(),
            )
            shutil.rmtree(self.work_dir(job), ignore_errors=True)
            return

        backoff = self.config["limits"]["retry_backoff"] * (2 ** (attempts - 1))
        log.warning(
            "%s attempt %d/%d failed: %s - retrying in %ds",
            job.name, attempts, limit, message, backoff,
        )
        self.store.update(
            job.id, state=jobs.QUEUED, attempts=attempts,
            fail_message=message, added=time.time() + backoff,
        )

    # -- move stage ----------------------------------------------------

    def move(self, job):
        """The noisy step: write the finished files to the library, then
        sweep, set permissions, and report where they went."""
        source = self.work_dir(job)
        destination = self.destination(job)

        try:
            destination.mkdir(parents=True, exist_ok=True)

            for item in self.outputs(source):
                shutil.move(str(item), str(destination / item.name))

            self.sweep(destination)
            self.apply_permissions(destination)
            shutil.rmtree(source, ignore_errors=True)
        except OSError as error:
            log.error("%s move failed: %s", job.name, error)
            self.store.update(
                job.id, state=jobs.FAILED, fail_message=f"move failed: {error}",
                finished=time.time(),
            )
            return

        self.store.update(
            job.id, state=jobs.COMPLETED, storage=str(destination),
            finished=time.time(),
        )

    def outputs(self, work_dir):
        """Everything yt-dlp left behind except its own scratch."""
        return [
            item for item in sorted(work_dir.iterdir())
            if item.is_file() and not item.name.endswith((".part", ".ytdl"))
        ]

    def sweep(self, directory):
        """The cleanup list: yt-dlp leaves .description, .info.json
        and loose thumbnails behind."""
        for item in directory.iterdir():
            if any(
                fnmatch.fnmatch(item.name, pattern)
                for pattern in self.config["cleanup"]
            ):
                item.unlink(missing_ok=True)

    def apply_permissions(self, directory):
        """Empty means don't force - folders keep what the umask gives
        them. Forcing a mode is what breaks shared-storage permissions."""
        chmod = self.config["permissions"]["chmod"]
        chown = self.config["permissions"]["chown"]
        targets = [directory, *directory.iterdir()]

        if chmod:
            for target in targets:
                os.chmod(target, int(str(chmod), 8))

        if chown:
            subprocess.run(["chown", "-R", chown, str(directory)], check=False)

    # -- removal -------------------------------------------------------

    def cancel(self, job_id):
        """Flag a running download; the progress hook aborts it."""
        self.cancelled.add(job_id)

    def discard(self, job, delete_files=True):
        self.cancelled.discard(job.id)

        if delete_files:
            shutil.rmtree(self.work_dir(job), ignore_errors=True)

        self.store.remove(job.id)
