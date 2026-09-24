"""Tracks mode end to end with yt-dlp and ffmpeg stubbed: each track
fetched on its own, optional ones skipped, one mux, intermediates
gone, sidecars kept."""

import html
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support import Harness
from ytdlparr import fetcher, jobs

SPEC = {
    "url": "https://site/v/1",
    "name": "Show - S01E01 - Pilot",
    "video": {"format": "bestvideo[height<=1080]"},
    "audio": [
        {"format": "bestaudio[channels=6]", "title": "5.1", "language": "nob", "flags": ["default"], "optional": True},
        {"format": "bestaudio[channels=2]", "title": "Stereo", "language": "nob"},
        {"url": "https://site/v/1AD", "format": "bestaudio", "title": "Described", "language": "nob",
         "flags": ["visual_impaired"], "optional": True},
    ],
    "subtitles": [
        {"url": "https://cdn/s0.m3u8", "title": "Full", "language": "nob", "flags": ["default"]},
        {"select": "nb-nor", "title": "Forced", "language": "nob", "flags": ["forced"]},
    ],
}


def nzb(spec):
    return (
        '<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb"><head>'
        f'<meta type="name">{spec["name"]}</meta>'
        f'<meta type="ytdlpspec">{html.escape(json.dumps(spec))}</meta>'
        '</head><file poster="x" date="1" subject="y"><segments/></file></nzb>'
    ).encode()


class FakeYtDlp:
    """Stands in for fetcher.run per track. Writes the file yt-dlp
    would, or raises the way yt-dlp does for an unmatched selector."""

    def __init__(self, missing=()):
        self.missing = set(missing)
        self.calls = []

    def __call__(self, url, params, on_progress, on_postprocess, is_cancelled):
        self.calls.append((url, params.get("format"), params.get("subtitleslangs")))
        selector = params.get("format")

        if selector in self.missing:
            raise fetcher.FetchFailed("Requested format is not available. Use --list-formats")

        stem = params["outtmpl"].replace(".%(ext)s", "")

        if params.get("skip_download"):
            Path(f"{stem}.{params['subtitleslangs'][0]}.vtt").write_text("WEBVTT\n")
            return {}

        on_progress({"filename": stem, "downloaded_bytes": 10, "total_bytes": 10})
        Path(f"{stem}.{'mp4' if 'video' in selector else 'm4a'}").write_bytes(b"data")

        if params.get("writethumbnail"):
            Path(f"{stem}.webp").write_bytes(b"img")

        return {}


class FakeFfmpeg:
    """Records commands; a mux writes its output, a subtitle fetch its SRT."""

    def __init__(self):
        self.commands = []

    def __call__(self, command, timeout):
        self.commands.append(command)
        Path(command[-1]).write_text("out")


class TracksModeTest(unittest.TestCase):
    def setUp(self):
        self.harness = Harness(categories={"tv": {"dir": "tv", "embed": ["thumbnail", "chapters", "metadata"]}})

    def tearDown(self):
        self.harness.close()

    def queue(self, spec):
        response = self.harness.client.post("/api", data={
            "apikey": "k", "mode": "addfile", "cat": "tv", "priority": "0",
            "name": (io.BytesIO(nzb(spec)), "x.nzb"),
        }, content_type="multipart/form-data")
        job = self.harness.store.get(response.json["nzo_ids"][0])
        return self.harness.store.update(job.id, state=jobs.DOWNLOADING)

    def run_job(self, spec, missing=()):
        job = self.queue(spec)
        ytdlp, ffmpeg = FakeYtDlp(missing), FakeFfmpeg()

        with patch("ytdlparr.fetcher.run", ytdlp), patch.object(self.harness.pipeline, "run_ffmpeg", ffmpeg):
            self.harness.pipeline.download(job)

        return job, ytdlp, ffmpeg

    def test_each_track_is_its_own_fetch_from_its_own_source(self):
        job, ytdlp, ffmpeg = self.run_job(SPEC)

        self.assertEqual(job.state, jobs.WAITING_TO_MOVE, job.fail_message)
        self.assertEqual(
            [(url, fmt) for url, fmt, _ in ytdlp.calls if fmt],
            [("https://site/v/1", "bestvideo[height<=1080]"),
             ("https://site/v/1", "bestaudio[channels=6]"),
             ("https://site/v/1", "bestaudio[channels=2]"),
             ("https://site/v/1AD", "bestaudio")],
        )
        self.assertIn(("https://site/v/1", None, ["nb-nor"]), ytdlp.calls)

    def test_direct_subtitle_urls_go_through_ffmpeg_and_one_mux_follows(self):
        job, _, ffmpeg = self.run_job(SPEC)

        subtitle_fetches = [c for c in ffmpeg.commands if "https://cdn/s0.m3u8" in c]
        muxes = [c for c in ffmpeg.commands if c[-1].endswith(".mkv")]
        self.assertEqual(len(subtitle_fetches), 1)
        self.assertEqual(len(muxes), 1)
        self.assertIn("-disposition:s:1", muxes[0])

    def test_optional_tracks_that_match_nothing_are_skipped(self):
        job, _, ffmpeg = self.run_job(SPEC, missing={"bestaudio[channels=6]", "bestaudio"})
        mux = [c for c in ffmpeg.commands if c[-1].endswith(".mkv")][0]

        self.assertEqual(job.state, jobs.WAITING_TO_MOVE, job.fail_message)
        maps = [mux[i + 1] for i, a in enumerate(mux) if a == "-map"]
        self.assertEqual(maps, ["0:v:0", "1:a:0", "2:s:0", "3:s:0"])
        self.assertIn("title=Stereo", " ".join(mux))
        self.assertNotIn("title=5.1", " ".join(mux))

    def test_a_required_track_that_matches_nothing_fails_the_attempt(self):
        job, _, ffmpeg = self.run_job(SPEC, missing={"bestaudio[channels=2]"})

        self.assertEqual(job.state, jobs.QUEUED)  # retry scheduled
        self.assertIn("audio 1", job.fail_message)
        self.assertEqual([c for c in ffmpeg.commands if c[-1].endswith(".mkv")], [])

    def test_only_the_output_remains_in_the_work_dir(self):
        job, _, _ = self.run_job(SPEC)
        left = sorted(p.name for p in self.harness.pipeline.work_dir(job).iterdir())

        self.assertEqual(left, ["Show - S01E01 - Pilot.mkv"])

    def test_sidecar_subs_are_kept_with_plex_names(self):
        self.harness.config["categories"]["tv"]["sidecar"] = ["subs"]
        job, _, _ = self.run_job(SPEC)
        left = sorted(p.name for p in self.harness.pipeline.work_dir(job).iterdir())

        self.assertEqual(left, [
            "Show - S01E01 - Pilot.mkv",
            "Show - S01E01 - Pilot.nob.forced.srt",
            "Show - S01E01 - Pilot.nob.srt",
        ])

    def test_an_invalid_spec_fails_without_retrying(self):
        job, _, _ = self.run_job({**SPEC, "subtitles": [{"title": "no source"}]})

        self.assertEqual(job.state, jobs.FAILED)
        self.assertIn("invalid spec", job.fail_message)

    def test_legacy_specs_still_take_the_old_path(self):
        from tests.support import FakeFetch
        job = self.queue({"url": "https://site/v/1", "name": "Legacy - S01E01", "container": "mkv"})

        with patch("ytdlparr.fetcher.run", FakeFetch()):
            self.harness.pipeline.download(job)

        self.assertEqual(job.state, jobs.WAITING_TO_MOVE)
