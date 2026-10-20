"""A project's ``REVIEW.md``: the lenses its reviewers use, their checklists, and which lenses run on each track.

The file opens with YAML front matter and continues with one ``## Lens: <name>`` section per lens::

    ---
    lenses: [correctness, security, maintainability, spec]
    tracks: {fix: [correctness, security], feature: [correctness, security, maintainability], ...}
    nit_cap: 5
    ignore: []
    always_flag: []
    ---
    ## Lens: correctness
    - ...checklist...

A lens section runs to the next heading of level one or two; its own sub-headings stay part of the checklist.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from swfactory.config import ConfigError, RoleTable, _is_int, _is_str_list
from swfactory.tracks import TRACK_PREFIX
from swfactory.verdict import DEFAULT_NIT_CAP

_FRONT_MATTER_KEYS = ("lenses", "tracks", "nit_cap", "ignore", "always_flag")
# A lens name becomes part of a review file name and of a role name (``lens-<name>``).
_LENS_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*")
_LENS_HEADING = re.compile(r"##[ \t]+Lens:[ \t]*(\S.*?)[ \t]*", re.IGNORECASE)
_SECTION_END = re.compile(r"#{1,2}(?:[ \t]|$)")


@dataclass
class ReviewConfig:
    lenses: dict[str, str]  # lens name -> its checklist, in the order the front matter lists them
    tracks: dict[str, list[str]]  # track -> the lenses that run on it
    nit_cap: int
    ignore: list[str]
    always_flag: list[str]


def _split_front_matter(file: str, text: str) -> tuple[str, list[str]]:
    """The front matter's YAML text and the lines after it; raise ``ConfigError`` when there is none."""
    lines = text.split("\n")
    if lines[0].rstrip() != "---":
        raise ConfigError([f"{file}: must start with YAML front matter (a '---' line, the settings, a closing '---' line)"])
    for index in range(1, len(lines)):
        if lines[index].rstrip() == "---":
            return "\n".join(lines[1:index]), lines[index + 1:]
    raise ConfigError([f"{file}: the front matter is not closed (no second '---' line)"])


def _lens_sections(body: list[str], problems: list[str], file: str) -> dict[str, str]:
    """Each ``## Lens: <name>`` section's checklist, keyed by lower-cased name."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in body:
        heading = _LENS_HEADING.fullmatch(line.rstrip())
        if heading:
            current = heading.group(1).lower()
            if current in sections:
                problems.append(f"{file}: '## Lens: {current}' appears more than once")
                current = None  # keep the first section's checklist
            else:
                sections[current] = []
        elif _SECTION_END.match(line):
            current = None
        elif current is not None:
            sections[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in sections.items()}


def load_review_md(path: Path) -> ReviewConfig:
    """Load a project's ``REVIEW.md``. Raises ``ConfigError`` listing every problem found."""
    path = Path(path)
    file = path.name
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise ConfigError([f"{path}: file not found"]) from None
    except (OSError, UnicodeDecodeError) as err:
        raise ConfigError([f"{path}: cannot read file: {err}"]) from None

    front, body = _split_front_matter(file, re.sub(r"\r\n?", "\n", text))
    try:
        data: Any = yaml.safe_load(front)
    except yaml.YAMLError as err:
        raise ConfigError([f"{file}: invalid YAML in the front matter: {' '.join(str(err).split())}"]) from None
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError([f"{file}: the front matter must be a mapping of settings"])

    problems: list[str] = []
    for key in data:
        if key not in _FRONT_MATTER_KEYS:
            problems.append(f"{file}: unknown key {key!r} (expected {', '.join(_FRONT_MATTER_KEYS)})")

    names = _check_lens_names(file, data.get("lenses"), problems)
    sections = _lens_sections(body, problems, file)
    for name in names:
        if name not in sections:
            problems.append(f"{file}: lens '{name}' has no '## Lens: {name}' section")
        elif not sections[name]:
            problems.append(f"{file}: lens '{name}' has no checklist under its '## Lens: {name}' heading")
    for name in sections:
        if name not in names:
            problems.append(f"{file}: '## Lens: {name}' is not listed in lenses")

    tracks = _check_tracks(file, data.get("tracks"), names, problems)

    nit_cap = data.get("nit_cap")
    if nit_cap is None:
        nit_cap = DEFAULT_NIT_CAP
    elif not _is_int(nit_cap) or nit_cap < 0:
        problems.append(f"{file}: nit_cap must be a whole number, 0 or more")
    lists: dict[str, list[str]] = {}
    for key in ("ignore", "always_flag"):
        value = data.get(key)
        if value is None:
            lists[key] = []
        elif not _is_str_list(value):
            problems.append(f"{file}: {key} must be a list of non-empty strings")
        else:
            lists[key] = list(value)

    if problems:
        raise ConfigError(problems)
    return ReviewConfig(
        lenses={name: sections[name] for name in names},
        tracks=tracks,
        nit_cap=nit_cap,
        ignore=lists["ignore"],
        always_flag=lists["always_flag"],
    )


def _check_lens_names(file: str, raw: Any, problems: list[str]) -> list[str]:
    if raw is None:
        problems.append(f"{file}: lenses is required (a list of lens names)")
        return []
    if not _is_str_list(raw) or not raw:
        problems.append(f"{file}: lenses must be a non-empty list of lens names")
        return []
    names: list[str] = []
    for name in raw:
        if not _LENS_NAME.fullmatch(name):
            problems.append(f"{file}: lens name {name!r} must be lower case letters, digits, '-' or '_'")
        elif name in names:
            problems.append(f"{file}: lens '{name}' is listed twice")
        else:
            names.append(name)
    return names


def _check_tracks(file: str, raw: Any, names: list[str], problems: list[str]) -> dict[str, list[str]]:
    if raw is None:
        problems.append(f"{file}: tracks is required (a mapping of track to its lenses)")
        return {}
    if not isinstance(raw, dict):
        problems.append(f"{file}: tracks must be a mapping of track to a list of lenses")
        return {}
    tracks: dict[str, list[str]] = {}
    for track, lenses in raw.items():
        where = f"{file}: track '{track}'"
        if track not in TRACK_PREFIX:
            problems.append(f"{file}: unknown track {track!r} (expected {', '.join(TRACK_PREFIX)})")
        elif not _is_str_list(lenses) or not lenses:
            problems.append(f"{where} needs at least one lens (a list of lens names)")
        else:
            for lens in lenses:
                if lens not in names:
                    problems.append(f"{where} uses lens '{lens}', which is not listed in lenses")
            if len(set(lenses)) != len(lenses):
                problems.append(f"{where} lists a lens twice")
            tracks[track] = list(lenses)
    return tracks


def check_panel_vendor_mix(cfg: ReviewConfig, table: RoleTable) -> list[str]:
    """One problem for each track none of whose lenses runs on the other vendor.

    A lens runs on the other vendor when the table has a role ``lens-<name>`` with ``vendor: other``; the
    review panel should never be one vendor reviewing its own work.
    """
    problems: list[str] = []
    for track, lenses in cfg.tracks.items():
        if not any(_runs_on_other_vendor(table, lens) for lens in lenses):
            problems.append(
                f"track '{track}': none of its lenses ({', '.join(lenses)}) runs on the other vendor "
                "(a role lens-<name> with vendor: other)"
            )
    return problems


def _runs_on_other_vendor(table: RoleTable, lens: str) -> bool:
    role = table.roles.get(f"lens-{lens}")
    return role is not None and role.vendor == "other"
