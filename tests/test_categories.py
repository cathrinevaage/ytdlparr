import unittest

from ytdlparr.categories import advertised, resolve

CATEGORIES = {
    "*": {"dir": "", "format": "best", "container": "mkv", "subs": ["all"],
          "priority": "normal"},
    "tv": {"dir": "tv"},
    "tv-example": {"dir": "tv", "format": "bestvideo[height<=1080]+bestaudio/best",
                   "subs": ["en"]},
}


class ResolveTest(unittest.TestCase):
    def test_named_category_overrides_the_star_block(self):
        options = resolve(CATEGORIES, "tv-example", {"url": "u"})

        self.assertEqual(options["subs"], ["en"])
        self.assertEqual(options["container"], "mkv")

    def test_spec_overrides_the_category(self):
        options = resolve(CATEGORIES, "tv-example", {"url": "u", "container": "mp4"})

        self.assertEqual(options["container"], "mp4")

    def test_unknown_category_falls_back_to_star(self):
        self.assertEqual(resolve(CATEGORIES, "movies", {"url": "u"})["dir"], "")

    def test_spec_keys_that_are_not_options_do_not_leak(self):
        options = resolve(CATEGORIES, "tv", {"url": "u", "name": "n"})

        self.assertNotIn("url", options)
        self.assertNotIn("name", options)

    def test_advertised_hides_star(self):
        self.assertEqual(advertised(CATEGORIES), ["tv", "tv-example"])
