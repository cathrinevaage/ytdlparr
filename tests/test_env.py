import unittest
from copy import deepcopy

from ytdlparr.config import DEFAULTS
from ytdlparr.env import apply


class EnvOverrideTest(unittest.TestCase):
    def test_paths_and_keys(self):
        config = apply(deepcopy(DEFAULTS), {
            "YTDLPARR_SERVER_API_KEY": "s3cret",
            "YTDLPARR_PATHS_COMPLETE": "/data/downloads/ytdlparr",
            "YTDLPARR_PATHS_INCOMPLETE": "/downloads/incomplete",
        })

        self.assertEqual(config["server"]["api_key"], "s3cret")
        self.assertEqual(config["paths"]["complete"], "/data/downloads/ytdlparr")
        self.assertEqual(config["paths"]["incomplete"], "/downloads/incomplete")

    def test_booleans_and_integers_keep_their_types(self):
        config = apply(deepcopy(DEFAULTS), {
            "YTDLPARR_LIMITS_MAX_CONCURRENT": "4",
            "YTDLPARR_LIMITS_PAUSE_DOWNLOADS_DURING_POSTPROCESS": "false",
        })

        self.assertEqual(config["limits"]["max_concurrent"], 4)
        self.assertIs(config["limits"]["pause_downloads_during_postprocess"], False)

    def test_nested_tables_are_file_only(self):
        before = deepcopy(DEFAULTS)
        config = apply(deepcopy(DEFAULTS), {
            "YTDLPARR_CATEGORIES_TV": "nope",
            "YTDLPARR_SCHEDULE_MOVE": "nope",
        })

        self.assertEqual(config["categories"], before["categories"])
        self.assertEqual(config["schedule"]["move"], before["schedule"]["move"])

    def test_schedule_timezone_is_overridable(self):
        config = apply(deepcopy(DEFAULTS), {"YTDLPARR_SCHEDULE_TIMEZONE": "Europe/Berlin"})

        self.assertEqual(config["schedule"]["timezone"], "Europe/Berlin")
