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

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")


def safe_directory_name(name):
    """A job name as a single path component."""
    cleaned = name.replace("/", "-").replace("\\", "-").strip().lstrip(".")

    return cleaned or "job"

from . import fetcher, jobs, mux, tracks
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
        """<incomplete>/<job name>, SAB's convention; the id is added
        only when another job already holds that name. Chosen once and
        remembered on the job, so a rename never strands files. Jobs
        from before this naming keep their id-named directory."""
        if job.work_dir:
            return Path(job.work_dir)

        incomplete = Path(self.config["paths"]["incomplete"])
        legacy = incomplete / job.id
        chosen = incomplete / safe_directory_name(job.name)

        if legacy.exists():
            chosen = legacy
        elif chosen.exists():
            chosen = incomplete / f"{safe_directory_name(job.name)} [{job.id[-6:]}]"

        self.store.update(job.id, work_dir=str(chosen))

        return chosen

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
        if tracks.is_tracks_spec(job.spec):
            return self.download_tracks(job)

        return self.download_legacy(job)

    def download_legacy(self, job):
        """A spec with only format/subs: one yt-dlp run does it all."""
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

    # -- tracks mode ---------------------------------------------------

    def download_tracks(self, job):
        """Fetch every track the spec names, then mux them in order."""
        options = resolve(self.config["categories"], job.category, job.spec)
        work_dir = self.work_dir(job)
        work_dir.mkdir(parents=True, exist_ok=True)
        name = job.spec["name"]

        try:
            planned = tracks.plan(job.spec)
        except tracks.InvalidSpec as error:
            self.store.update(
                job.id, state=jobs.FAILED, fail_message=f"invalid spec: {error}",
                finished=time.time(),
            )
            return

        self.store.update(job.id, started=job.started or time.time())
        totals = {}
        progress = self._progress_reporter(job, totals)
        fetched = []

        for track in planned:
            if job.id in self.cancelled:
                self.discard(job)
                return

            try:
                path = self.fetch_track(track, name, options, work_dir, progress)
            except fetcher.Cancelled:
                self.discard(job)
                return
            except fetcher.FetchFailed as error:
                self.fail_or_retry(job, f"{track.kind} {track.index}: {error}")
                return

            if path is None:
                log.info("%s: %s %d skipped (optional, nothing matched)", name, track.kind, track.index)
                continue

            track.path = str(path)
            fetched.append(track)

        self.store.update(job.id, state=jobs.POSTPROCESSING, speed=0.0, eta=0)

        try:
            output = self.mux_tracks(fetched, name, options, work_dir)
        except fetcher.FetchFailed as error:
            self.fail_or_retry(job, str(error))
            return

        self.keep_only(work_dir, output, fetched, name, options)
        self.store.update(
            job.id, state=jobs.WAITING_TO_MOVE,
            bytes_done=job.bytes_total or job.bytes_done, speed=0.0, eta=0,
        )

    def _progress_reporter(self, job, totals):
        def on_progress(event):
            filename = event.get("filename", "")
            totals[filename] = (
                event.get("downloaded_bytes", 0),
                event.get("total_bytes") or event.get("total_bytes_estimate") or 0,
            )
            self.store.update(
                job.id,
                bytes_done=sum(done for done, _ in totals.values()),
                bytes_total=sum(total for _, total in totals.values()),
                speed=event.get("speed") or 0.0,
                eta=event.get("eta") or 0,
            )

        return on_progress

    def fetch_track(self, track, name, options, work_dir, on_progress):
        """One track to one file. Returns its path, or None when an
        optional track matched nothing."""
        if track.kind == "subtitle" and track.direct_url:
            return self.fetch_subtitle_url(track, name, work_dir)

        params = fetcher.build_track_options(
            track, name, options.get("embed", []), self.config["limits"],
            self.config["cookies"], work_dir, self.ffmpeg_dir,
        )

        try:
            fetcher.run(track.url, params, on_progress, lambda event: None, lambda: False)
        except fetcher.FetchFailed as error:
            if track.optional and fetcher.is_missing_format(error):
                return None

            raise

        found = self.produced_file(work_dir, f"{name}.{track.stem()}", track.kind)

        if found is None:
            if track.optional:
                return None

            raise fetcher.FetchFailed(f"{track.kind} {track.index}: nothing was written")

        return found

    def fetch_subtitle_url(self, track, name, work_dir):
        """A subtitle playlist or file, through ffmpeg, to SRT."""
        output = Path(work_dir) / f"{name}.{track.stem()}.srt"
        command = [
            str(self.ffmpeg()), "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
            "-user_agent", "Mozilla/5.0", "-i", track.direct_url, str(output),
        ]

        try:
            self.run_ffmpeg(command, timeout=600)
        except fetcher.FetchFailed:
            if track.optional:
                return None

            raise

        return output

    def mux_tracks(self, fetched, name, options, work_dir):
        video = next((t for t in fetched if t.kind == "video"), None)

        if video is None:
            raise fetcher.FetchFailed("no video track was fetched")

        audio = [t for t in fetched if t.kind == "audio"]
        subtitles = [t for t in fetched if t.kind == "subtitle"]
        thumbnail = self.produced_file(work_dir, f"{name}.v", "image")
        output = Path(work_dir) / f"{name}.{options.get('container', 'mkv')}"
        wanted_thumbnail = thumbnail if "thumbnail" in set(options.get("embed", [])) else None

        self.run_ffmpeg(
            mux.arguments(self.ffmpeg(), video, audio, subtitles, output, wanted_thumbnail),
            timeout=3600,
        )

        return output

    def keep_only(self, work_dir, output, fetched, name, options):
        """Drop the per-track intermediates; keep the output and any
        sidecars asked for."""
        sidecar = set(options.get("sidecar", []))
        keep = {output.resolve()}

        if "subs" in sidecar:
            for track in fetched:
                if track.kind == "subtitle":
                    target = Path(work_dir) / mux.sidecar_name(name, track)
                    shutil.copyfile(track.path, target) if track.path.endswith(".srt") \
                        else self.run_ffmpeg([str(self.ffmpeg()), "-y", "-nostdin", "-loglevel", "error", "-i", track.path, str(target)], timeout=120)
                    keep.add(target.resolve())

        if "thumbnail" in sidecar:
            thumbnail = self.produced_file(work_dir, f"{name}.v", "image")

            if thumbnail:
                target = Path(work_dir) / f"{name}{thumbnail.suffix}"
                shutil.move(str(thumbnail), str(target))
                keep.add(target.resolve())

        for item in Path(work_dir).iterdir():
            if item.is_file() and item.resolve() not in keep:
                item.unlink()

    def produced_file(self, work_dir, stem, kind):
        """The file a track run left behind, by its stem."""
        candidates = sorted(
            item for item in Path(work_dir).iterdir()
            if item.is_file() and item.name.startswith(stem + ".")
            and not item.name.endswith((".part", ".ytdl"))
        )

        if kind == "image":
            candidates = [c for c in candidates if c.suffix.lower() in IMAGE_SUFFIXES]
        elif kind == "subtitle":
            candidates = [c for c in candidates if c.suffix.lower() in (".vtt", ".srt", ".ass", ".ttml")]
        else:
            candidates = [c for c in candidates if c.suffix.lower() not in IMAGE_SUFFIXES + (".vtt", ".srt", ".ass", ".ttml", ".json", ".description")]

        return candidates[0] if candidates else None

    def ffmpeg(self):
        return Path(self.ffmpeg_dir) / "ffmpeg" if self.ffmpeg_dir else "ffmpeg"

    def run_ffmpeg(self, command, timeout):
        """A subprocess boundary the tests replace."""
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)

        if completed.returncode != 0:
            tail = (completed.stderr or "").strip().splitlines()[-3:]
            raise fetcher.FetchFailed("ffmpeg failed: " + " | ".join(tail)[:400])

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
