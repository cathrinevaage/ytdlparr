"""Free-space checks. Both paths are checked because they are
different disks: incomplete is local scratch, complete the library."""

import shutil
from pathlib import Path


def free_bytes(path):
    target = Path(path)

    while not target.exists() and target != target.parent:
        target = target.parent

    return shutil.disk_usage(target).free


def has_room(path, minimum_bytes):
    return free_bytes(path) >= minimum_bytes
