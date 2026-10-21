"""Tracks and work ids: the id prefix of each track, and locating the project a work folder belongs to."""
from __future__ import annotations

import re
from pathlib import Path

from swfactory.config import PROJECT_CONFIG

TRACK_PREFIX = {"program": "PRG", "feature": "FEA", "fix": "FIX"}

_TRACK_FOR_PREFIX = {prefix: track for track, prefix in TRACK_PREFIX.items()}

# An id is ``<PREFIX>-<number>`` with an optional single-letter suffix for a collision (``FEA-12b``); a work
# folder name continues with ``-<slug>``.
_WORK_ID = re.compile(r"(?P<prefix>[A-Z]{3})-\d+[a-z]?(?:-.+)?")


def track_for_id(id_: str) -> str:
    """The track of a work id or work-folder name such as ``FIX-3`` or ``FIX-3-licence-crash``.

    Raises ``ValueError`` for anything that is not one.
    """
    match = _WORK_ID.fullmatch(id_)
    track = _TRACK_FOR_PREFIX.get(match.group("prefix")) if match else None
    if track is None:
        prefixes = ", ".join(f"{prefix}-<n>" for prefix in TRACK_PREFIX.values())
        raise ValueError(f"{id_!r} is not a work id or work folder name (expected {prefixes}, optionally followed by -<slug>)")
    return track


def find_project_root(start: Path) -> Path:
    """The nearest folder at or above ``start`` that holds a ``sw-factory.yaml``.

    Raises ``FileNotFoundError`` naming ``start`` when there is none.
    """
    here = Path(start).resolve()
    for folder in (here, *here.parents):
        if (folder / PROJECT_CONFIG).is_file():
            return folder
    raise FileNotFoundError(f"no {PROJECT_CONFIG} in {start} or any folder above it")
