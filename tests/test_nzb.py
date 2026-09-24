import unittest

from ytdlparr.nzb import NotAJobSpec, extract_spec

FROM_INDEXER = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE nzb PUBLIC "-//newzBin//DTD NZB 1.1//EN" "http://www.newzbin.com/DTD/nzb/nzb-1.1.dtd">
<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">
  <head>
    <meta type="name">Show - S01E01 - Pilot</meta>
    <meta type="ytdlpspec">{"url": "https://example.com/watch/abc123", "name": "Show - S01E01 - Pilot", "format": "bestvideo[height&lt;=1080]+bestaudio/best", "subs": ["en"]}</meta>
  </head>
</nzb>
"""

REAL_NZB = b"""<?xml version="1.0" encoding="UTF-8"?>
<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">
  <head><meta type="name">Something.Real</meta></head>
  <file poster="x" date="1" subject="y"><segments/></file>
</nzb>
"""


class ExtractSpecTest(unittest.TestCase):
    def test_reads_the_spec_an_indexer_emits(self):
        spec = extract_spec(FROM_INDEXER)

        self.assertEqual(spec["url"], "https://example.com/watch/abc123")
        self.assertEqual(spec["format"], "bestvideo[height<=1080]+bestaudio/best")
        self.assertEqual(spec["subs"], ["en"])

    def test_rejects_a_real_nzb(self):
        with self.assertRaises(NotAJobSpec):
            extract_spec(REAL_NZB)

    def test_rejects_non_xml(self):
        with self.assertRaises(NotAJobSpec):
            extract_spec(b"<html>login</html")

    def test_rejects_a_spec_without_a_url(self):
        with self.assertRaises(NotAJobSpec):
            extract_spec(
                b'<nzb><head><meta type="ytdlpspec">{"name":"x"}</meta>'
                b'</head></nzb>'
            )
