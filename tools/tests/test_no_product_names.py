"""No BSI product or project name in what the factory ships or documents (decision F19).

The factory is meant for any project and for sharing beyond the team that built it, so its README, its documentation
(all of ``docs/``, the decision log and the v1 records included), its skills, prompts, subagents, templates, config
and code say "an earlier project", "a project that builds only when instructed" and the like, never the name of a
specific BSI product or project. The scan fails on any of those names as a whole word, in any case; nothing is
allowed. The company names (BSI, BSRI, Battlespace-Research) and the repository path stay. This file is under
``tools/tests``, which is not scanned, so it could name them, but it spells them backwards instead: a search for the
names across the repository then finds only real mentions.
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


def find_product_names(root: Path) -> list[str]:
    """``<path>:<line>: <text>`` for each line with a product name in the scanned files under ``root``."""
    offenders: list[str] = []
    for path in scanned_files(root):
        rel = path.relative_to(root).as_posix()
        text = path.read_bytes().decode("utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            if PATTERN.search(line):
                offenders.append(f"{rel}:{number}: {line.strip()}")
    return offenders


def test_no_product_names_in_what_the_factory_ships():
    assert find_product_names(factory_home()) == []


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
