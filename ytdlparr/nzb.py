"""Reading the job spec out of the faux NZB an indexer hands over."""

import json
from xml.etree import ElementTree

NZB_NAMESPACE = "{http://www.newzbin.com/DTD/2003/nzb}"
SPEC_META = "ytdlpspec"


class NotAJobSpec(Exception):
    """The uploaded file carried no ytdlpspec meta tag."""


def extract_spec(nzb_bytes):
    """The spec from <meta type="ytdlpspec">, or raise NotAJobSpec."""
    try:
        root = ElementTree.fromstring(nzb_bytes)
    except ElementTree.ParseError as error:
        raise NotAJobSpec(f"not XML: {error}") from error

    for meta in root.iter():
        if not meta.tag.endswith("meta"):
            continue

        if meta.get("type") == SPEC_META:
            return _parse(meta.text or "")

    raise NotAJobSpec("no ytdlpspec meta tag")


def _parse(text):
    try:
        spec = json.loads(text)
    except ValueError as error:
        raise NotAJobSpec(f"spec is not JSON: {error}") from error

    if not isinstance(spec, dict) or "url" not in spec:
        raise NotAJobSpec("spec has no url")

    return spec
