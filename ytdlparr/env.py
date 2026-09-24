"""Environment overrides: YTDLPARR_<SECTION>_<KEY> sets one scalar or
list value, on top of the config file. Nested tables (anything whose
value is itself a mapping) are file-only."""

import os

PREFIX = "YTDLPARR_"
TRUE_WORDS = ("1", "true", "yes", "on")


def coerce(raw, current):
    """Take the type from the value being replaced."""
    if isinstance(current, bool):
        return raw.strip().lower() in TRUE_WORDS

    if isinstance(current, int):
        return int(raw)

    if isinstance(current, float):
        return float(raw)

    if isinstance(current, list):
        return [item.strip() for item in raw.split(",") if item.strip()]

    return raw


def is_structured(value):
    """A table, or a list of tables such as schedule windows: too
    shaped for one environment string."""
    if isinstance(value, dict):
        return True

    return isinstance(value, list) and any(
        isinstance(item, dict) for item in value
    )


def apply(config, environ=None):
    """Mutate config with every YTDLPARR* variable that names a known
    section and key. Unknown names are ignored so a typo cannot
    silently create a setting nothing reads."""
    for name, raw in (environ or os.environ).items():
        if not name.startswith(PREFIX):
            continue

        section, _, key = name[len(PREFIX):].lower().partition("_")
        table = config.get(section)

        if not isinstance(table, dict) or key not in table:
            continue

        if is_structured(table[key]):
            continue

        table[key] = coerce(raw, table[key])

    return config
