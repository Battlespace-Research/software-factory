"""No BSI product or project name, and no one machine's local path or username, in what the factory ships or documents
(decision F19).

The factory is meant for any project and for sharing beyond the team that built it, so its README, its documentation
(all of ``docs/``, the decision log and the v1 records included), its skills, prompts, subagents, templates, config
and code say "an earlier project", "a project that builds only when instructed" and the like, never the name of a
specific BSI product or project. The scan fails on any of those names as a whole word, in any case; nothing is
allowed. The company names (BSI, BSRI, Battlespace-Research) and the repository path stay. This file is under
``tools/tests``, which is not scanned, so it could name them, but it spells them backwards instead: a search for the
names across the repository then finds only real mentions.

The same scanned files also say ``<factory-clone>`` and ``~/.factory/...``, never where one teammate cloned the
repository or what their account is called. A second scan fails on the owner's username (as a whole word, in any
case), on a path into a ``BSRI`` folder on a drive (``X:\\``, ``X:/``, ``/x/`` or ``/mnt/x/``, for any letter ``x``) and
on a Windows user-profile path with a name in it (``X:\\Users\\<name>``; a placeholder such as ``<you>`` is not a
name). The username and the paths are built from pieces below, for the same reason as the product names.
"""
from __future__ import annotations

import re
from pathlib import Path

from swfactory.config import factory_home

SCANNED_FOLDERS = ("docs", "skills", "prompts", "agents", "templates", "config", "tools/swfactory")
SCANNED_FILES = ("README.md",)
BINARY_SUFFIXES = (".png", ".pyc")
# Five BSI product names, spelled backwards (see the module docstring).
_BACKWARDS = ("2CGN", "ECAM", "nilreM", "ROMRA", "TCOM")
PRODUCT_NAMES = tuple(name[::-1] for name in _BACKWARDS)
PATTERN = re.compile(r"\b(?:%s)\b" % "|".join(PRODUCT_NAMES), re.IGNORECASE)
PROTOTYPE, DESKTOP, STANDALONE, SUITE, TOOL = PRODUCT_NAMES

# The owner's username, split so that this file does not contain it (see the module docstring).
OWNER_USERNAME = "cor" + "dc"
ORG_FOLDER = "BS" + "RI"
# A drive letter that does not end a longer word (so a URL scheme, as in "https://", is not a drive), then the
# separator: a backslash or a slash, doubled when the path sits in a string or in JSON.
_DRIVE = r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]{1,2}"
_END_OF_NAME = r"(?![A-Za-z0-9])"
LOCAL_PATH_PATTERNS = (
    # The owner's username as a whole word, in any case, wherever it sits (a home folder, a project folder name).
    re.compile(r"(?<![A-Za-z0-9])%s%s" % (OWNER_USERNAME, _END_OF_NAME), re.IGNORECASE),
    # A path into the BSRI folder on a drive: Windows (either slash), Git Bash (/x/...) and WSL (/mnt/x/...).
    re.compile(_DRIVE + ORG_FOLDER + _END_OF_NAME, re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9_.~-])/[A-Za-z]/%s%s" % (ORG_FOLDER, _END_OF_NAME), re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9])/mnt/[A-Za-z]/%s%s" % (ORG_FOLDER, _END_OF_NAME), re.IGNORECASE),
    # A Windows user-profile path with a name in it; a placeholder such as <you> does not start with a letter or digit.
    re.compile(_DRIVE + r"Users[\\/]{1,2}[A-Za-z0-9]", re.IGNORECASE),
)


def scanned_files(root: Path) -> list[Path]:
    """Every text file under the scanned folders (``__pycache__`` aside) and the scanned files under ``root``."""
    files: list[Path] = []
    for folder in SCANNED_FOLDERS:
        base = root / folder
        if base.is_dir():
            files += [
                path for path in sorted(base.rglob("*"))
                if path.is_file() and "__pycache__" not in path.parts and path.suffix not in BINARY_SUFFIXES
            ]
    files += [root / name for name in SCANNED_FILES if (root / name).is_file()]
    return files


def _offending_lines(root: Path, patterns: tuple[re.Pattern[str], ...]) -> list[str]:
    """``<path>:<line>: <text>`` for each line that matches any of ``patterns`` in the scanned files under ``root``."""
    offenders: list[str] = []
    for path in scanned_files(root):
        rel = path.relative_to(root).as_posix()
        text = path.read_bytes().decode("utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            if any(pattern.search(line) for pattern in patterns):
                offenders.append(f"{rel}:{number}: {line.strip()}")
    return offenders


def find_product_names(root: Path) -> list[str]:
    """``<path>:<line>: <text>`` for each line with a product name in the scanned files under ``root``."""
    return _offending_lines(root, (PATTERN,))


def find_local_paths(root: Path) -> list[str]:
    """``<path>:<line>: <text>`` for each line with the owner's username or a local path in the scanned files."""
    return _offending_lines(root, LOCAL_PATH_PATTERNS)


def test_no_product_names_in_what_the_factory_ships():
    assert find_product_names(factory_home()) == []


def test_no_local_paths_or_usernames_in_what_the_factory_ships():
    assert find_local_paths(factory_home()) == []


def test_the_scan_finds_each_name_as_a_whole_word_in_any_case_and_only_there(tmp_path):
    old = {
        "README.md": f"An earlier project, {PROTOTYPE}, ran on a runbook.\n",
        "docs/specs/a.md": f"As in {DESKTOP}, with many submodules.\n",
        "docs/decisions.md": f"| F99 | 2026 | The {STANDALONE} standalone app is a reference. | ok | Owner |\n",
        "docs/map/m.html": f"<p>the {SUITE} program</p>\n",
        "docs/plans/p.md": (
            f"Never write to E:/BSI/GitHub/{DESKTOP}-{PROTOTYPE}-Prototype and not to {TOOL.lower()}-v3.\n"
            f"or to a {DESKTOP.lower()} folder\n"
        ),
        "docs/acceptance/r.md": f"run under {PROTOTYPE.lower()} lanes\n",
        "skills/s/SKILL.md": f"Ask for the {TOOL.capitalize()} tool.\n",
        "prompts/p.md": f"({DESKTOP})\n",
        "agents/a.md": f"{STANDALONE.lower()}\n",
        "templates/project/t.md": f"{PROTOTYPE}: x\n",
        "config/c.yaml": f"# for {DESKTOP}\n",
        "tools/swfactory/m.py": f'"""The {PROTOTYPE} dashboard."""\n',
    }
    new = {
        "docs/guide/g.md": "An earlier multi-model project at BSI; BSRI hosts the repo.\n",
        "docs/specs/b.md": "A pace, a mason, a surmacer and the word armory or armored; maceration is a process.\n",
        "docs/map/n.html": "<p>Battlespace-Research / software-factory</p>\n",
        "tools/tests/t.py": f"{PROTOTYPE} is not scanned here, it is a test file.\n",
        "examples/sample-app/README.md": "A sample outside the scanned folders.\n",
    }
    for name, text in {**old, **new}.items():
        if name in old and name in new:
            raise AssertionError(f"{name} is in both sets")
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    offenders = find_product_names(tmp_path)
    flagged = {line.split(":", 1)[0] for line in offenders}
    assert flagged == set(old), offenders
    # A path that joins names with hyphens still counts, and so does a lower-case name on the next line.
    assert [line for line in offenders if line.startswith("docs/plans/p.md")] == [
        f"docs/plans/p.md:1: Never write to E:/BSI/GitHub/{DESKTOP}-{PROTOTYPE}-Prototype and not to {TOOL.lower()}-v3.",
        f"docs/plans/p.md:2: or to a {DESKTOP.lower()} folder",
    ]


def test_the_local_path_scan_finds_the_username_and_each_kind_of_local_path_and_only_those(tmp_path):
    drive = "e"  # a lower-case letter is a drive in a Git Bash or WSL path; an upper-case one in a Windows path
    clone = f"{drive.upper()}:\\{ORG_FOLDER}\\GitHub\\software-factory"
    users = "Users"
    profile = f"C:\\{users}\\{OWNER_USERNAME}\\AppData\\Local\\Temp"
    old = {
        "README.md": f"Hello from {OWNER_USERNAME.upper()}.\n",
        "docs/acceptance/r.md": f"| Factory | `{clone}`, branch main |\n",
        "docs/decisions.md": f"Local clone {drive.upper()}:/{ORG_FOLDER}/GitHub/software-factory.\n",
        "docs/map/m.html": f"<span>Repo (local): {drive.lower()}:\\\\{ORG_FOLDER}\\\\GitHub</span>\n",
        "docs/plans/p.md": (
            f"cd /{drive}/{ORG_FOLDER}/GitHub/software-factory\n"
            f"tar -C /mnt/{drive}/{ORG_FOLDER.lower()}/GitHub/software-factory -cf - .\n"
            f"See ~/.claude/projects/C--Users-{OWNER_USERNAME}-AppData-Local-Temp-x/ for the transcript.\n"
        ),
        "docs/specs/s.md": f"Logs go to {profile}\\sf.log, and to c:/{users.lower()}/Someone/logs.\n",
        "skills/s/SKILL.md": f"Run it as {OWNER_USERNAME}: ask {OWNER_USERNAME.capitalize()}.\n",
        "prompts/p.md": f"The path {clone}\\prompts is local.\n",
        "agents/a.md": f"{OWNER_USERNAME}\nsf_{OWNER_USERNAME}_home\n",  # an underscore does not end the name
        "templates/project/t.md": f"/{drive}/{ORG_FOLDER}/x\n",
        "config/c.yaml": f"# D:/{ORG_FOLDER}/factory\n",
        "tools/swfactory/m.py": f'"""Reads {clone}\\\\.venv."""\n',
    }
    new = {
        "docs/guide/g.md": (
            "Clone it to `<factory-clone>`; in WSL that is `/mnt/<drive>/<path to factory-clone>`.\n"
            "The temp folder is `%TEMP%\\sf-rehearsal-<timestamp>`; staging is `~/.factory/staging/<project>/FIX-1/`.\n"
            "The transcript is in `~/.claude/projects/<project-folder>/`.\n"
        ),
        "docs/guide/install.md": "writable_roots = ['C:\\Users\\<you>\\.sw-factory']    # a placeholder is not a name\n",
        "docs/specs/b.md": (
            f"BSRI hosts https://github.com/Battlespace-Research/software-factory and https://{ORG_FOLDER}.example/x.\n"
            f"A relative path docs/{drive}/{ORG_FOLDER}/notes.md, and the words {OWNER_USERNAME}x and "
            f"x{OWNER_USERNAME} are not the username as a whole word.\n"
        ),
        "docs/map/n.html": "<span>Repo: github.com/Battlespace-Research/software-factory</span>\n",
        "tools/tests/t.py": f"{clone} and {OWNER_USERNAME} are not scanned here, it is a test file.\n",
        "examples/sample-app/README.md": f"{profile} is outside the scanned folders.\n",
    }
    for name, text in {**old, **new}.items():
        if name in old and name in new:
            raise AssertionError(f"{name} is in both sets")
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    offenders = find_local_paths(tmp_path)
    flagged = {line.split(":", 1)[0] for line in offenders}
    assert flagged == set(old), offenders
    # Each offending line of a file is reported on its own, with its number.
    assert [line.split(": ", 1)[0] for line in offenders if line.startswith("docs/plans/p.md")] == [
        "docs/plans/p.md:1",
        "docs/plans/p.md:2",
        "docs/plans/p.md:3",
    ]
    assert [line for line in offenders if line.startswith("docs/specs/s.md")] == [
        f"docs/specs/s.md:1: Logs go to {profile}\\sf.log, and to c:/{users.lower()}/Someone/logs.",
    ]
