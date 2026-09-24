"""Tracks mode: a spec that names every stream of the output.

The indexer says what - a URL and a yt-dlp selector (or a direct
subtitle URL) per track, plus the title, language and disposition
flags each stream should carry. The client fetches each track on its
own, then muxes them in order with ffmpeg. Nothing here knows a site.

    "video":     {"format": "bestvideo[height<=1080]"}
    "audio":     [{"format": "bestaudio[channels=6]", "title": "…",
                   "language": "nob", "flags": ["default"], "optional": true},
                  {"url": "<another programme>", "format": "bestaudio", …}]
    "subtitles": [{"url": "<hls playlist or vtt>", "title": "…", …},
                  {"select": "<yt-dlp subtitle key>", …}]

A track's url defaults to the spec's. Flags are ffmpeg disposition
names, verbatim. optional means: skip silently when the selector
matches nothing.
"""

from dataclasses import dataclass, field

FLAGS = (
    "default", "forced", "hearing_impaired", "visual_impaired",
    "commentary", "original", "dub",
)


class InvalidSpec(Exception):
    """The tracks description cannot be acted on."""


def is_tracks_spec(spec):
    return any(key in spec for key in ("video", "audio", "subtitles"))


@dataclass
class Track:
    kind: str                      # video | audio | subtitle
    index: int                     # position within its kind
    url: str = ""                  # source, for yt-dlp fetches
    selector: str = ""             # yt-dlp format selector (video/audio)
    select: str = ""               # yt-dlp subtitle key
    direct_url: str = ""           # subtitle file or playlist fetched by ffmpeg
    title: str = ""
    language: str = ""
    flags: tuple = ()
    optional: bool = False
    path: str = ""                 # set once fetched

    def stem(self):
        return {"video": "v", "audio": f"a{self.index}", "subtitle": f"s{self.index}"}[self.kind]


def plan(spec):
    """The ordered list of tracks a spec asks for."""
    base_url = spec["url"]
    video = spec.get("video") or {}
    tracks = [Track(
        kind="video", index=0, url=video.get("url") or base_url,
        selector=video.get("format") or "bestvideo",
    )]

    for index, entry in enumerate(spec.get("audio") or []):
        if not entry.get("format"):
            raise InvalidSpec(f"audio track {index} has no format selector")

        tracks.append(Track(
            kind="audio", index=index, url=entry.get("url") or base_url,
            selector=entry["format"], **_metadata(entry),
        ))

    for index, entry in enumerate(spec.get("subtitles") or []):
        if not (entry.get("url") or entry.get("select")):
            raise InvalidSpec(f"subtitle track {index} has neither url nor select")

        tracks.append(Track(
            kind="subtitle", index=index, url=base_url,
            select=entry.get("select") or "",
            direct_url=entry.get("url") or "",
            **_metadata(entry),
        ))

    return tracks


def _metadata(entry):
    flags = tuple(flag for flag in entry.get("flags") or () if flag in FLAGS)
    unknown = set(entry.get("flags") or ()) - set(FLAGS)

    if unknown:
        raise InvalidSpec(f"unknown flags {sorted(unknown)}; known: {FLAGS}")

    return {
        "title": entry.get("title") or "",
        "language": entry.get("language") or "",
        "flags": flags,
        "optional": bool(entry.get("optional")),
    }
