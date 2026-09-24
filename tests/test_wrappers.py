"""The ffmpeg wrapper must hand ffmpeg exactly the arguments yt-dlp
gave it. With ionice present, an earlier version prepended ionice to
the arguments instead of the command, and ffmpeg read "ionice" as a
file name."""

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ytdlparr.fetcher import write_ffmpeg_wrappers


def stub(directory, name, body):
    script = directory / name
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


class WrapperTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "calls.log"
        # A fake ffmpeg that records exactly the arguments it received.
        self.ffmpeg = stub(self.bin, "ffmpeg", f'printf "%s\\n" "$@" > "{self.log}"\n')
        stub(self.bin, "ffprobe", f'printf "%s\\n" "$@" > "{self.log}"\n')

    def tearDown(self):
        self.directory.cleanup()

    def run_wrapper(self, with_ionice):
        if with_ionice:
            # A fake ionice that drops its own flags and runs the rest.
            stub(self.bin, "ionice", 'shift 4; exec "$@"\n')

        with patch("ytdlparr.fetcher.shutil.which", side_effect=lambda tool: str(self.bin / tool)):
            wrappers = write_ffmpeg_wrappers(self.root / "wrappers", 10, "2:4")

        environment = {**os.environ, "PATH": f"{self.bin}:{os.environ.get('PATH', '')}"}
        subprocess.run([str(wrappers / "ffmpeg"), "-y", "-i", "in.mp4", "out.mkv"], env=environment, check=True)

        return self.log.read_text().splitlines()

    def test_arguments_reach_ffmpeg_untouched_with_ionice(self):
        self.assertEqual(self.run_wrapper(with_ionice=True), ["-y", "-i", "in.mp4", "out.mkv"])

    def test_arguments_reach_ffmpeg_untouched_without_ionice(self):
        self.assertEqual(self.run_wrapper(with_ionice=False), ["-y", "-i", "in.mp4", "out.mkv"])

    def test_wrapper_names_the_real_binary_absolutely(self):
        with patch("ytdlparr.fetcher.shutil.which", side_effect=lambda tool: f"/usr/bin/{tool}"):
            wrappers = write_ffmpeg_wrappers(self.root / "w2", 10, "2:4")

        self.assertIn('"/usr/bin/ffmpeg" "$@"', (wrappers / "ffmpeg").read_text())
        self.assertIn("ionice -c 2 -n 4", (wrappers / "ffmpeg").read_text())
