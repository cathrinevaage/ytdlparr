"""The final ffmpeg pass: every fetched track, in order, stream-copied
into one container with its title, language and dispositions."""


def arguments(ffmpeg, video, audio, subtitles, output, thumbnail=None):
    """The ffmpeg command line. video/audio/subtitles are fetched
    Tracks; audio and subtitles may be empty. When there are no audio
    tracks the video's own audio streams are kept."""
    inputs = [video, *audio, *subtitles]
    command = [str(ffmpeg), "-y", "-nostdin", "-hide_banner", "-loglevel", "error"]

    for track in inputs:
        command += ["-i", track.path]

    if audio:
        command += ["-map", "0:v:0"]
    else:
        command += ["-map", "0:v:0", "-map", "0:a?"]

    for offset, _ in enumerate(audio, start=1):
        command += ["-map", f"{offset}:a:0"]

    for offset, _ in enumerate(subtitles, start=1 + len(audio)):
        command += ["-map", f"{offset}:s:0"]

    command += [
        "-map_metadata", "0", "-map_chapters", "0",
        "-c:v", "copy", "-c:a", "copy", "-c:s", "srt",
    ]

    for position, track in enumerate(audio):
        command += _stream_metadata("a", position, track)

    for position, track in enumerate(subtitles):
        command += _stream_metadata("s", position, track)

    if thumbnail:
        command += [
            "-attach", str(thumbnail),
            "-metadata:s:t:0", f"mimetype={_mimetype(thumbnail)}",
            "-metadata:s:t:0", "filename=cover" + _suffix(thumbnail),
        ]

    return command + [str(output)]


def _stream_metadata(kind, position, track):
    arguments = []

    if track.title:
        arguments += [f"-metadata:s:{kind}:{position}", f"title={track.title}"]

    if track.language:
        arguments += [f"-metadata:s:{kind}:{position}", f"language={track.language}"]

    # Set explicitly even when empty, so nothing inherited from the
    # source (a stray default flag) survives.
    arguments += [f"-disposition:{kind}:{position}", "+".join(track.flags) or "0"]

    return arguments


def _suffix(path):
    name = str(path)

    return name[name.rfind("."):] if "." in name else ""


def _mimetype(path):
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp",
    }.get(_suffix(path).lower(), "application/octet-stream")


def sidecar_name(name, track):
    """<name>.<language>[.forced][.sdh].srt - the shape Plex reads."""
    parts = [name, track.language or "und"]

    if "forced" in track.flags:
        parts.append("forced")

    if "hearing_impaired" in track.flags:
        parts.append("sdh")

    return ".".join(parts) + ".srt"
