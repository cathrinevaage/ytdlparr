"""The yt-dlp embed. Builds a YoutubeDL from one job's resolved
options, runs it, and reports progress back through the hooks."""

import logging
import os
import shutil
import stat
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from yt_dlp.utils import DownloadCancelled

log = logging.getLogger(__name__)

SIZE_SUFFIXES = {"K": 1 << 10, "M": 1 << 20, "G": 1 << 30}


class Cancelled(Exception):
    """The job was deleted while it was downloading."""


class FetchFailed(Exception):
    """yt-dlp gave up. The message is what Sonarr shows."""


def parse_rate(text):
    """"5M" -> bytes per second, or None when unset."""
    if not text:
        return None

    text = str(text).strip().upper()

    if text[-1] in SIZE_SUFFIXES:
        return int(float(text[:-1]) * SIZE_SUFFIXES[text[-1]])

    return int(float(text))


def cookie_file_for(url, cookies):
    """Cookies are keyed by host on the client, never in the spec."""
    host = urlparse(url).hostname or ""

    return cookies.get(host) or cookies.get(host.removeprefix("www."))


def postprocessors(options):
    """yt-dlp's postprocessor chain for the embed and sidecar lists.
    Order matters: convert subs before embedding, embed metadata and
    chapters together, thumbnail last."""
    embed = set(options.get("embed", []))
    sidecar = set(options.get("sidecar", []))
    sponsorblock = options.get("sponsorblock", [])
    chain = []

    if sponsorblock:
        chain.append({"key": "SponsorBlock", "categories": sponsorblock})
        chain.append({
            "key": "ModifyChapters",
            "remove_sponsor_segments": sponsorblock,
        })

    if options.get("subs"):
        chain.append({"key": "FFmpegSubtitlesConvertor", "format": "srt"})

    if "subs" in embed:
        chain.append({
            "key": "FFmpegEmbedSubtitle",
            "already_have_subtitle": "subs" in sidecar,
        })

    if embed & {"metadata", "chapters"}:
        chain.append({
            "key": "FFmpegMetadata",
            "add_metadata": "metadata" in embed,
            "add_chapters": "chapters" in embed,
        })

    if "thumbnail" in embed:
        chain.append({
            "key": "EmbedThumbnail",
            "already_have_thumbnail": "thumbnail" in sidecar,
        })

    return chain


def build_options(spec, options, limits, cookies, work_dir, ffmpeg_dir=None):
    """Everything yt-dlp needs for one job, as a YoutubeDL params dict.
    The hooks are added by the caller because they close over the job."""
    subtitle_langs = options.get("subs") or []
    embed = set(options.get("embed", []))
    sidecar = set(options.get("sidecar", []))

    params = {
        "format": options["format"],
        "merge_output_format": options.get("container", "mkv"),
        "outtmpl": str(Path(work_dir) / f"{spec['name']}.%(ext)s"),
        "writesubtitles": bool(subtitle_langs),
        "subtitleslangs": subtitle_langs,
        "writethumbnail": bool({"thumbnail"} & (embed | sidecar)),
        "writeinfojson": False,
        "postprocessors": postprocessors(options),
        "retries": limits["retries"],
        "fragment_retries": limits["retries"],
        "continuedl": True,
        "noprogress": True,
        "quiet": True,
        "no_warnings": False,
        "logger": log,
        "restrictfilenames": False,
        "windowsfilenames": False,
        "overwrites": True,
    }

    rate = parse_rate(limits.get("rate_limit"))

    if rate:
        params["ratelimit"] = rate

    cookie_file = cookie_file_for(spec["url"], cookies)

    if cookie_file:
        params["cookiefile"] = cookie_file

    if ffmpeg_dir:
        params["ffmpeg_location"] = str(ffmpeg_dir)

    return params


def base_options(url, limits, cookies, ffmpeg_dir=None):
    """What every yt-dlp run gets, whichever track it fetches."""
    params = {
        "retries": limits["retries"],
        "fragment_retries": limits["retries"],
        "continuedl": True,
        "noprogress": True,
        "quiet": True,
        "no_warnings": False,
        "logger": log,
        "restrictfilenames": False,
        "windowsfilenames": False,
        "overwrites": True,
    }

    rate = parse_rate(limits.get("rate_limit"))

    if rate:
        params["ratelimit"] = rate

    cookie_file = cookie_file_for(url, cookies)

    if cookie_file:
        params["cookiefile"] = cookie_file

    if ffmpeg_dir:
        params["ffmpeg_location"] = str(ffmpeg_dir)

    return params


def build_track_options(track, name, embed, limits, cookies, work_dir, ffmpeg_dir=None):
    """yt-dlp params for one track of a tracks-mode spec. Video carries
    the chapters/metadata postprocessors and the thumbnail; audio is a
    bare selector; a subtitle by key is a subtitles-only run."""
    params = base_options(track.url, limits, cookies, ffmpeg_dir)
    params["outtmpl"] = str(Path(work_dir) / f"{name}.{track.stem()}.%(ext)s")

    if track.kind == "video":
        embed = set(embed)
        params["format"] = track.selector
        params["writethumbnail"] = "thumbnail" in embed
        params["postprocessors"] = [{
            "key": "FFmpegMetadata",
            "add_metadata": "metadata" in embed,
            "add_chapters": "chapters" in embed,
        }] if embed & {"metadata", "chapters"} else []

    elif track.kind == "audio":
        params["format"] = track.selector

    elif track.kind == "subtitle":
        params["skip_download"] = True
        params["writesubtitles"] = True
        params["writeautomaticsub"] = False
        params["subtitleslangs"] = [track.select]

    return params


def is_missing_format(error):
    """yt-dlp's wording when a selector matches nothing."""
    return "Requested format is not available" in str(error)


def write_ffmpeg_wrappers(directory, nice, ionice):
    """yt-dlp subprocesses ffmpeg for every mux; SAB runs its heavy
    tools under nice and ionice, and a wrapper directory pointed at by
    ffmpeg_location is how the same applies here. The real binaries
    are resolved now and written in as absolute paths, so the scripts
    never look themselves up. ionice is Linux only and is used only
    where it exists."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    ionice_class, _, ionice_level = str(ionice or "").partition(":")
    ionice_prefix = (
        f"ionice -c {int(ionice_class)} -n {int(ionice_level or 4)} "
        if ionice_class else ""
    )
    nice_prefix = f"nice -n {int(nice or 0)} "

    for tool in ("ffmpeg", "ffprobe"):
        real = shutil.which(tool) or f"/usr/bin/{tool}"
        script = directory / tool
        script.write_text(
            "#!/bin/sh\n"
            + (
                "if command -v ionice >/dev/null 2>&1; then\n"
                f'  exec {nice_prefix}{ionice_prefix}"{real}" "$@"\n'
                "fi\n"
                if ionice_prefix else ""
            )
            + f'exec {nice_prefix}"{real}" "$@"\n'
        )
        script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)

    return directory


def run(url, params, on_progress, on_postprocess, is_cancelled):
    """Download one URL. Raises Cancelled or FetchFailed; on success
    returns the info dict yt-dlp produced."""
    def progress_hook(event):
        if is_cancelled():
            raise DownloadCancelled("deleted")

        on_progress(event)

    def postprocessor_hook(event):
        on_postprocess(event)

    params = {
        **params,
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [postprocessor_hook],
    }

    try:
        with yt_dlp.YoutubeDL(params) as downloader:
            return downloader.extract_info(url, download=True)
    except DownloadCancelled as error:
        raise Cancelled() from error
    except yt_dlp.utils.DownloadError as error:
        raise FetchFailed(_clean_message(str(error))) from error


def _clean_message(message):
    """yt-dlp prefixes "ERROR: " and sometimes the extractor name."""
    return message.removeprefix("ERROR: ").split("\n")[0][:500]
