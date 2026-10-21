"""Skills readiness: are the third-party skills the factory's roles need installed on a host?

The factory references skills that are installed in Claude Code and Codex; it never copies them
(design section 13). For one host this module finds the installed skills, compares them with
``config/skills.yaml`` and reports what is present, what is missing (with the fallback to use
instead), and which interactive skills are bound to a role that runs unattended.

Finding skills never raises: a folder or file that cannot be read, a broken or looping symlink
and a malformed SKILL.md front matter are skipped or fall back to the folder name. Nothing here
names a model.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

from swfactory.config import RoleTable, SkillReq

SkillHost = Literal["claude", "codex"]

# A role that runs as one of these has no person to talk to, so it must not use an interactive skill.
UNATTENDED_RUN_AS = ("subagent", "headless")

SKILL_FILE = "SKILL.md"
# Extra folders to search, separated by os.pathsep; each is a folder of skill folders.
SKILL_PATHS_ENV = "FACTORY_SKILL_PATHS"

_SKILL_SCAN_DEPTH = 3  # below a search path: path/<group>/<group>/<skill>
_PLUGIN_SCAN_DEPTH = 6  # below a plugin cache: cache/<marketplace>/<plugin>/<version>/skills is 4
_PRUNED_FOLDERS = frozenset({".git", "node_modules"})
_HEAD_CHARS = 64 * 1024  # a SKILL.md's front matter is at the top; the rest is never needed


# --------------------------------------------------------------------------- folder walking


def _children(folder: Path) -> list[Path]:
    """The sub-folders of ``folder`` (a symlink to a folder counts), by name; none when it cannot be listed."""
    try:
        with os.scandir(folder) as entries:
            found = sorted(entries, key=lambda entry: entry.name)
    except OSError:
        return []
    children: list[Path] = []
    for entry in found:
        try:
            if entry.is_dir():  # follows symlinks; false for a broken one
                children.append(Path(entry.path))
        except OSError:
            continue
    return children


def _real(folder: Path) -> str | None:
    """The folder's resolved path, the key that spots a symlink loop; ``None`` when it cannot be resolved."""
    try:
        return os.path.realpath(folder)
    except (OSError, ValueError):
        return None


def _skills_folders_under(root: Path) -> list[Path]:
    """Every folder named ``skills`` under ``root`` (a plugin cache), not looking inside one.

    The search is bounded: it stops ``_PLUGIN_SCAN_DEPTH`` levels down, keeps out of ``.git`` and
    ``node_modules``, and visits each real folder once, so symlink loops end.
    """
    found: list[Path] = []
    seen: set[str] = set()

    def walk(folder: Path, depth: int) -> None:
        real = _real(folder)
        if real is None or real in seen:
            return
        seen.add(real)
        for child in _children(folder):
            if child.name == "skills":
                found.append(child)
            elif depth + 1 < _PLUGIN_SCAN_DEPTH and child.name not in _PRUNED_FOLDERS:
                walk(child, depth + 1)

    walk(root, 0)
    return found


# --------------------------------------------------------------------------- search paths


def skill_search_paths(host: SkillHost, home: Path, project: Path | None = None) -> list[Path]:
    """The folders ``host`` loads skills from, in the order it prefers them.

    Each is a folder of skill folders (``<path>/<skill>/SKILL.md``). Fixed folders are listed
    whether or not they exist; plugin-cache folders are listed only where found. ``project``, when
    given, adds the project's own skill folders. The entries of ``FACTORY_SKILL_PATHS``
    (``os.pathsep``-separated) come last for both hosts. Raises ``ValueError`` for another host.
    """
    home = Path(home)
    paths: list[Path]
    if host == "claude":
        paths = [home / ".claude" / "skills", home / ".agents" / "skills"]
        paths += _skills_folders_under(home / ".claude" / "plugins" / "cache")
        appdata = os.environ.get("APPDATA")
        if sys.platform == "win32" and appdata:  # the Claude desktop app's synced plugins
            paths += _skills_folders_under(Path(appdata) / "Claude" / "rpm")
        if project is not None:
            paths.append(Path(project) / ".claude" / "skills")
    elif host == "codex":
        paths = [home / ".agents" / "skills", home / ".codex" / "skills"]
        paths += _skills_folders_under(home / ".codex" / "plugins" / "cache")
        if project is not None:
            paths += [Path(project) / ".agents" / "skills", Path(project) / ".codex" / "skills"]
    else:
        raise ValueError(f"unknown host {host!r}: expected claude or codex")
    for entry in os.environ.get(SKILL_PATHS_ENV, "").split(os.pathsep):
        if entry.strip():
            paths.append(Path(entry.strip()))
    return paths


# --------------------------------------------------------------------------- installed skills


def _read_head(skill_md: Path) -> str | None:
    """The first part of a SKILL.md as text (UTF-8, a BOM tolerated, bytes that are not UTF-8 replaced);
    ``None`` when the file cannot be opened or read."""
    try:
        with open(skill_md, "r", encoding="utf-8-sig", errors="replace") as handle:
            return handle.read(_HEAD_CHARS)
    except OSError:
        return None


def _front_matter_name(head: str) -> str | None:
    """The ``name:`` of the leading ``---`` YAML block of a SKILL.md, or ``None`` when the file has no such
    block, the block is not valid YAML, or it holds no usable name. Only the first block is read."""
    lines = head.split("\n")
    if lines[0].rstrip() != "---":
        return None
    for end in range(1, len(lines)):
        if lines[end].rstrip() == "---":
            block = "\n".join(lines[1:end])
            break
    else:
        return None
    try:
        data = yaml.safe_load(block)
    except (yaml.YAMLError, ValueError, OverflowError, RecursionError):
        return None
    name = data.get("name") if isinstance(data, dict) else None
    return name.strip() if isinstance(name, str) and name.strip() else None


def _scan(folder: Path, depth: int, seen: set[str], found: dict[str, Path]) -> None:
    real = _real(folder)
    if real is None or real in seen:
        return
    seen.add(real)
    skill_md = folder / SKILL_FILE
    try:
        is_skill = skill_md.is_file()
    except OSError:
        is_skill = False
    if is_skill:
        head = _read_head(skill_md)
        if head is not None:
            found.setdefault(_front_matter_name(head) or folder.name, folder)
        return  # what lies inside a skill folder belongs to that skill
    if depth < _SKILL_SCAN_DEPTH:
        for child in _children(folder):
            _scan(child, depth + 1, seen, found)


def find_installed_skills(paths: list[Path]) -> dict[str, Path]:
    """The skills installed under ``paths``: skill name to the skill's folder.

    A folder holding a SKILL.md is a skill, found up to ``_SKILL_SCAN_DEPTH`` levels below a path.
    Its name is the ``name:`` in the front matter of the SKILL.md, else the folder's name. A skill
    on an earlier path wins over one with the same name on a later path. Symlinks to folders are
    followed. A path that is missing, and anything unreadable or broken, is skipped without error.
    """
    found: dict[str, Path] = {}
    for root in paths:
        _scan(Path(root), 0, set(), found)
    return found


# --------------------------------------------------------------------------- the report


@dataclass
class ReadinessReport:
    """What ``check_readiness`` found for one host.

    ``present`` are the required skills that are installed. ``missing`` are the required skills
    that are not, as ``(skill, fallback)``; a missing skill whose fallback is installed is still
    listed, and ``installed_fallbacks`` says which fallbacks are. A missing optional skill is not
    reported. ``interactive_in_unattended`` names the interactive skills registered for a role that
    runs as a subagent or headless. All lists are sorted by skill name, each skill once.
    """

    host: str
    present: list[str]
    missing: list[tuple[str, str | None]]
    interactive_in_unattended: list[str]
    installed_fallbacks: frozenset[str] = field(default=frozenset(), compare=False)

    @property
    def blocking_missing(self) -> list[str]:
        """The missing required skills that have no installed fallback to use instead."""
        return [skill for skill, fallback in self.missing if fallback is None or fallback not in self.installed_fallbacks]

    @property
    def ok(self) -> bool:
        """Ready to run: nothing required is missing without a usable fallback, and no interactive skill is
        bound to an unattended role. A fallback in use is a warning, not a failure."""
        return not self.blocking_missing and not self.interactive_in_unattended


def _required_skills(registry: dict[str, list[SkillReq]]) -> dict[str, str | None]:
    """The skills any role requires, each once, with the first fallback the registry names for it."""
    required: dict[str, str | None] = {}
    for skills in registry.values():
        for skill in skills:
            if skill.required:
                required[skill.name] = required.get(skill.name) or skill.fallback
    return required


def _interactive_in_unattended(registry: dict[str, list[SkillReq]], table: RoleTable) -> list[str]:
    """Interactive skills registered for a role that runs unattended; ``"*"`` stands for every role in
    the table, and a role the table does not have is left to ``sw-factory config check``."""
    unattended = {name for name, spec in table.roles.items() if spec.run_as in UNATTENDED_RUN_AS}
    flagged: set[str] = set()
    for role, skills in registry.items():
        applies = bool(unattended) if role == "*" else role in unattended
        if applies:
            flagged.update(skill.name for skill in skills if skill.interactive)
    return sorted(flagged)


def check_readiness(
    registry: dict[str, list[SkillReq]],
    table: RoleTable,
    host: SkillHost,
    home: Path,
    project: Path | None = None,
) -> ReadinessReport:
    """Check ``host`` against the skills registry (see ``ReadinessReport`` for what is reported).

    ``table`` supplies each role's ``run_as``; pass the table with the project's overrides applied
    to judge a project. A skill required by several roles is reported once.
    """
    installed = find_installed_skills(skill_search_paths(host, home, project))
    required = _required_skills(registry)
    present = sorted(name for name in required if name in installed)
    missing = sorted(((name, fallback) for name, fallback in required.items() if name not in installed), key=lambda item: item[0])
    return ReadinessReport(
        host=host,
        present=present,
        missing=missing,
        interactive_in_unattended=_interactive_in_unattended(registry, table),
        installed_fallbacks=frozenset(fallback for _, fallback in missing if fallback is not None and fallback in installed),
    )
