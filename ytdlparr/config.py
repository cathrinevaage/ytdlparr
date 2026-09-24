"""Configuration: operational only. What to download comes from the
job spec; this says where things go, how many at once, and when."""

import os
from copy import deepcopy
from pathlib import Path

import yaml

from . import env

DEFAULTS = {
    "server": {
        "host": "0.0.0.0",
        "port": 9120,
        "api_key": "changeme",
        "url_base": "",
    },
    "paths": {
        "incomplete": "/downloads/incomplete",
        "complete": "/downloads/complete",
        "state": "/config/jobs.json",
    },
    "limits": {
        "max_concurrent": 2,
        "rate_limit": "",
        "retries": 5,
        "retry_backoff": 30,
        "nice": 10,
        "ionice": "2:4",
        "pause_downloads_during_postprocess": True,
    },
    "disk": {
        "min_free_incomplete": "20G",
        "min_free_complete": "50G",
        "on_low_space": "fail",
    },
    "schedule": {
        "timezone": os.environ.get("TZ", "UTC"),
        "download": [
            {"days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
             "from": "00:00", "to": "24:00"},
        ],
        "move": [
            {"days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
             "from": "00:00", "to": "24:00"},
        ],
    },
    "history": {
        "keep_jobs": 200,
    },
    "cleanup": ["*.description", "*.info.json", "*.jpg"],
    "permissions": {
        "chmod": "",
        "chown": "",
    },
    "cookies": {},
    "categories": {
        "*": {
            "dir": "",
            "format": "bestvideo+bestaudio/best",
            "container": "mkv",
            "subs": ["all"],
            "embed": ["subs", "chapters", "thumbnail", "metadata"],
            "sidecar": [],
            "sponsorblock": [],
            "priority": "normal",
        },
    },
    "notifications": {
        "apprise_urls": [],
        "on": ["job_failed", "low_space", "queue_paused"],
    },
}

SIZE_UNITS = {"K": 1 << 10, "M": 1 << 20, "G": 1 << 30, "T": 1 << 40}


def merge(base, override):
    """Deep-merge two mappings, preferring values from override."""
    merged = deepcopy(base)

    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge(merged[key], value)
        else:
            merged[key] = value

    return merged


def load(path):
    """Defaults, then the YAML file, then environment overrides."""
    source = Path(path)
    from_file = yaml.safe_load(source.read_text()) if source.exists() else {}

    return env.apply(merge(DEFAULTS, from_file))


def parse_size(text):
    """"20G" -> bytes. Bare numbers are bytes already."""
    text = str(text).strip().upper().rstrip("B")

    if text[-1:] in SIZE_UNITS:
        return int(float(text[:-1]) * SIZE_UNITS[text[-1]])

    return int(float(text or 0))
