import unittest

from ytdlparr import mux
from ytdlparr.tracks import InvalidSpec, Track, is_tracks_spec, plan

SPEC = {
    "url": "https://site/v/1",
    "name": "Show - S01E01",
    "video": {"format": "bestvideo[height<=1080]"},
    "audio": [
        {"format": "bestaudio[channels=6]", "title": "5.1", "language": "nob", "flags": ["default"], "optional": True},
        {"format": "bestaudio[channels=2]", "title": "Stereo", "language": "nob"},
        {"url": "https://site/v/1AD", "format": "bestaudio", "title": "Described", "language": "nob", "flags": ["visual_impaired"], "optional": True},
    ],
    "subtitles": [
        {"url": "https://cdn/s0.m3u8", "title": "Full", "language": "nob", "flags": ["default"]},
        {"select": "nb-nor", "title": "Forced", "language": "nob", "flags": ["forced"]},
    ],
}


class PlanTest(unittest.TestCase):
    def test_legacy_specs_are_not_tracks_specs(self):
        self.assertFalse(is_tracks_spec({"url": "u", "format": "best", "subs": ["all"]}))
        self.assertTrue(is_tracks_spec(SPEC))

    def test_tracks_in_output_order_with_sources_resolved(self):
        tracks = plan(SPEC)

        self.assertEqual([t.kind for t in tracks], ["video", "audio", "audio", "audio", "subtitle", "subtitle"])
        self.assertEqual(tracks[0].url, "https://site/v/1")
        self.assertEqual(tracks[3].url, "https://site/v/1AD")
        self.assertEqual(tracks[4].direct_url, "https://cdn/s0.m3u8")
        self.assertEqual(tracks[5].select, "nb-nor")
        self.assertEqual([t.stem() for t in tracks], ["v", "a0", "a1", "a2", "s0", "s1"])

    def test_optional_and_flags_carry_through(self):
        tracks = plan(SPEC)

        self.assertTrue(tracks[1].optional)
        self.assertFalse(tracks[2].optional)
        self.assertEqual(tracks[3].flags, ("visual_impaired",))

    def test_unknown_flag_is_refused(self):
        with self.assertRaises(InvalidSpec):
            plan({**SPEC, "audio": [{"format": "bestaudio", "flags": ["loud"]}]})

    def test_subtitle_without_a_source_is_refused(self):
        with self.assertRaises(InvalidSpec):
            plan({**SPEC, "subtitles": [{"title": "x"}]})


class MuxTest(unittest.TestCase):
    """The ffmpeg command is the contract with the file players see."""

    def fetched(self):
        video = Track(kind="video", index=0, path="v.mp4")
        audio = [
            Track(kind="audio", index=0, path="a0.m4a", title="5.1", language="nob", flags=("default",)),
            Track(kind="audio", index=1, path="a2.m4a", title="Described", language="nob", flags=("visual_impaired",)),
        ]
        subtitles = [
            Track(kind="subtitle", index=0, path="s0.srt", title="Full", language="nob", flags=("default",)),
            Track(kind="subtitle", index=1, path="s1.vtt", title="Forced", language="nob", flags=("forced",)),
        ]
        return video, audio, subtitles

    def test_maps_streams_in_track_order_and_copies(self):
        video, audio, subtitles = self.fetched()
        command = mux.arguments("ffmpeg", video, audio, subtitles, "out.mkv", thumbnail="cover.jpg")

        self.assertEqual(command[command.index("-i") + 1], "v.mp4")
        maps = [command[i + 1] for i, a in enumerate(command) if a == "-map"]
        self.assertEqual(maps, ["0:v:0", "1:a:0", "2:a:0", "3:s:0", "4:s:0"])
        self.assertIn("-c:v", command)
        self.assertEqual(command[command.index("-c:s") + 1], "srt")
        self.assertEqual(command[-1], "out.mkv")

    def test_titles_languages_and_dispositions_land_on_the_right_streams(self):
        video, audio, subtitles = self.fetched()
        command = mux.arguments("ffmpeg", video, audio, subtitles, "out.mkv")
        joined = " ".join(command)

        self.assertIn("-metadata:s:a:0 title=5.1", joined)
        self.assertIn("-metadata:s:a:1 title=Described", joined)
        self.assertIn("-disposition:a:0 default", joined)
        self.assertIn("-disposition:a:1 visual_impaired", joined)
        self.assertIn("-metadata:s:s:1 language=nob", joined)
        self.assertIn("-disposition:s:1 forced", joined)

    def test_no_audio_tracks_keeps_the_videos_own_audio(self):
        video, _, subtitles = self.fetched()
        command = mux.arguments("ffmpeg", video, [], subtitles, "out.mkv")
        maps = [command[i + 1] for i, a in enumerate(command) if a == "-map"]

        self.assertEqual(maps, ["0:v:0", "0:a?", "1:s:0", "2:s:0"])

    def test_empty_flags_clear_inherited_dispositions(self):
        video, _, _ = self.fetched()
        plain = [Track(kind="audio", index=0, path="a.m4a")]
        command = mux.arguments("ffmpeg", video, plain, [], "out.mkv")

        self.assertIn("-disposition:a:0 0", " ".join(command))

    def test_thumbnail_is_attached_with_a_mimetype(self):
        video, audio, subtitles = self.fetched()
        joined = " ".join(mux.arguments("ffmpeg", video, audio, subtitles, "out.mkv", thumbnail="/w/x.webp"))

        self.assertIn("-attach /w/x.webp", joined)
        self.assertIn("mimetype=image/webp", joined)

    def test_sidecar_names_follow_plex_conventions(self):
        forced = Track(kind="subtitle", index=0, language="nob", flags=("forced",))
        sdh = Track(kind="subtitle", index=1, language="nob", flags=("hearing_impaired", "default"))

        self.assertEqual(mux.sidecar_name("Show - S01E01", forced), "Show - S01E01.nob.forced.srt")
        self.assertEqual(mux.sidecar_name("Show - S01E01", sdh), "Show - S01E01.nob.sdh.srt")
