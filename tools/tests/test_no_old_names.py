"""No pre-rename name in what the factory ships (decisions F16 and F17).

Version 0.2.0 renamed the plugin to ``bsri-sw-factory``, its skills and subagents (``setup-project``, ``intake``,
``orchestrate``, ``task-loop``, ``review-panel``, ``merge-gate``; ``lens-reviewer``, ``doc-reviewer``, ``tester``,
``researcher``), the CLI to ``sw-factory``, and what it creates in a project to ``sw-factory.yaml``, ``sw-factory/``,
``sw-factory/<ID>-<slug>`` branches, ``<!-- sw-factory:begin -->`` markers and ``~/.sw-factory/staging``; the design's
planned skills follow the same scheme (``deploy``, ``maintain``, ``dashboard``, ``evals``). A skill, prompt, template,
setting, manifest, guide, design page or line of code that still says an old name would send a session or a person to
a file, folder, branch, skill or command that no longer exists, so the scan fails on any of them.

Version 0.3.0 renamed the largest track from Program to Epic (F17): the track id ``epic``, ids ``EPIC-<n>``, the
templates in ``templates/work/epic/`` and the ``epic:`` key under ``tracks:`` in ``REVIEW.md``. The scan fails on the
old id (``--track program``, ``"program"``), the ``PRG`` prefix, the old templates folder and ``REVIEW.md`` key, and
the display name where it means the track ("a Program", "**Program**", "(Program)", "Fix, Feature or Program",
...). In the guidance that sessions and people read most (``BROAD_FOLDERS``: skills, templates, prompts, agents and
the guides) any capitalised "Program" or "Programs" fails, except in ``Program Files``. The word "program" in lower
case is prose ("a POSIX program", "a document of steps rather than a program") and never fails.

Unchanged on purpose: the Python package ``swfactory``, the ``Factory-*`` commit trailers, the ``FACTORY_*``
environment variables, the repository ``Battlespace-Research/software-factory``, the ``bsri-factory`` marketplace and
the word "factory" in prose. These never match a pattern below. The kept names that look like old ones are allowed
explicitly (``KEPT``): the review-file stamp ``<!-- factory: ... -->``, the dashboard's ``factory-data`` id and
``factory-dashboard/0.1`` schema, the file names ``factory-map.html``, ``factory-dashboard.html`` and those of the
v1 records (``*-factory-core-v1*.md``), and the placeholder ``<factory-clone>`` for the folder where someone cloned
this repository (F19). So are the F-rows of the append-only decision log, and the install guide's
"Upgrading from 0.1.0" and "Upgrading from 0.2.0" sections, which have to name what they replace.
"""
from __future__ import annotations

import re
from pathlib import Path

from swfactory.config import factory_home

SCANNED_FOLDERS = (
    "skills", "agents", "prompts", "templates", "config", "tools/swfactory",
    "docs/guide", "docs/map", "docs/dashboard", "docs/specs",
)
SCANNED_FILES = (
    "README.md",
    ".claude-plugin/plugin.json", ".claude-plugin/marketplace.json",
    ".codex-plugin/plugin.json", ".agents/plugins/marketplace.json",
    "docs/acceptance/v1-checklist.md", "docs/decisions.md",
)
BINARY_SUFFIXES = (".png", ".pyc")

_CLI_COMMANDS = "config|role|panel|work|approve|gate|setup|skills|prompt|--version"
OLD_NAMES = {
    "the old project file factory.yaml": r"(?<![\w-])factory\.yaml",
    "the old project folder or branch prefix factory/": r"(?<![\w.-])factory/",
    "the old staging folder .factory": r"(?<![\w-])\.factory/",
    "the old section markers": r"(?<![\w-])factory:(?:begin|end)\b",
    "the old plugin name software-factory": r"software-factory[:@]|`software-factory`|\"name\": \"software-factory\"",
    "an old skill name": r"(?<![\w-])factory-(?:setup|triage|run|task-loop|review-panel|merge-gate)(?![\w-])",
    "an old subagent name": r"(?<![\w-])factory-(?:lens-reviewer|doc-reviewer|tester|researcher)(?![\w-])",
    "a factory- name (old skill, agent or planned skill)": r"(?<![\w.-])factory-[a-z]",
    "the old command name factory": r"`factory[` ]|<code>factory[ <]|(?<![\w-])[\"']factory[\"']",
}
# The epic track's old name (F17).
TRACK_WHAT = "the old track name Program"
OLD_NAMES |= {
    "the old work-id prefix PRG": r"(?<![\w-])PRG(?!\w)|(?<![\w-])prg-\d",
    "the old track id program": r"--track[ =]program\b|\|program>|`program`|[\"']program[\"']",
    "the old REVIEW.md tracks key program:": r"^\s*program:\s*\[",
    "the old templates folder templates/work/program": r"templates/work/program\b",
    TRACK_WHAT: (
        r"\b[Aa] Programs?\b|\bPrograms?(?:'s)? (?:track|milestones?|tasks?|items?|row)\b|\*\*Programs?:?\*\*"
        r"|\| Program \||\(Programs?[,)]|, Program\)| and Program\)|\b(?:Fix|Feature),? (?:or |and )?Program\b"
        r"|\bProgram, (?:Feature|Fix)\b|[\"'>]Programs?[\"'<]|^\s*- Programs?:|\bup to Program\b"
    ),
}
PATTERNS = {what: re.compile(source) for what, source in OLD_NAMES.items()}
# Where any capitalised "Program" is the old track name: the guidance that sessions and people read most.
BROAD_FOLDERS = ("skills/", "templates/", "prompts/", "agents/", "docs/guide/")
_BROAD_TRACK = re.compile(r"(?<![\w\\/-])Programs?(?![\w-])")

# Kept identifiers that look like old names: removed from a line before it is checked.
KEPT = {
    "the review-file stamp": r"<!--\s*factory:(?!(?:begin|end)\b).*?-->",  # not the old markers
    "the dashboard's data id": r"(?<![\w-])factory-data(?![\w-])",
    "the dashboard's data schema": r"(?<![\w-])factory-dashboard/\d+(?:\.\d+)*",
    "the map and dashboard file names": r"(?<![\w-])factory-(?:map|dashboard)\.html",
    "the v1 records' file names": r"(?<![\w-])factory-core-v1(?:-rulings)?\.md",
    "the Windows folder Program Files": r"\bProgram Files\b",  # not the old track name
    "the placeholder for the folder of a factory clone": r"<factory-clone>",  # not a skill name (F19)
}
_KEPT = re.compile("|".join(f"(?:{source})" for source in KEPT.values()))
# Lines that may name old identifiers as they are: the decision log's rows (append-only; F16 and F17 record the
# renames).
ALLOWED_LINES = {"docs/decisions.md": re.compile(r"^\| F\d+ \|")}
# Sections that may name them: the upgrade steps say what each old name becomes.
ALLOWED_SECTIONS = {"docs/guide/install.md": ("## Upgrading from 0.1.0", "## Upgrading from 0.2.0")}

# A line of a fenced code block that runs the old command, such as ``factory config check``.
_FENCE = re.compile(r"^\s*(```|~~~)")
_FENCED_CALL = re.compile(rf"^\s*factory (?:{_CLI_COMMANDS})(?![\w-])")


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


def find_old_names(root: Path) -> list[str]:
    """``<path>:<line>: <what>: <text>`` for each old name in the scanned files under ``root``."""
    offenders: list[str] = []
    for path in scanned_files(root):
        rel = path.relative_to(root).as_posix()
        allowed_line = ALLOWED_LINES.get(rel)
        allowed_sections = ALLOWED_SECTIONS.get(rel, ())
        broad = rel.startswith(BROAD_FOLDERS)
        text = path.read_bytes().decode("utf-8", errors="replace")
        fenced = in_allowed_section = False
        for number, line in enumerate(text.splitlines(), start=1):
            if line.startswith("## "):
                in_allowed_section = line.strip() in allowed_sections
            if _FENCE.match(line):
                fenced = not fenced
                continue
            if in_allowed_section or (allowed_line is not None and allowed_line.match(line)):
                continue
            checked = _KEPT.sub("", line)
            found = [what for what, pattern in PATTERNS.items() if pattern.search(checked)]
            if broad and TRACK_WHAT not in found and _BROAD_TRACK.search(checked):
                found.append(TRACK_WHAT)
            if fenced and _FENCED_CALL.match(checked):
                found.append("the old command name factory")
            offenders += [f"{rel}:{number}: {what}: {line.strip()}" for what in found]
    return offenders


def test_no_old_names_in_what_the_factory_ships():
    assert find_old_names(factory_home()) == []


def test_the_scan_finds_each_old_name_and_leaves_the_new_ones_alone(tmp_path):
    old = {
        "skills/a/SKILL.md": "Settings are in `factory.yaml`.\n",
        "skills/b/SKILL.md": "Hand the task to the `factory-task-loop` skill.\n",
        "skills/b/references/x.md": "Spawn `software-factory:factory-tester`.\n",
        "agents/x.md": "---\nname: factory-lens-reviewer\n---\n",
        "prompts/x.md": "The diff leaves out factory/work/.\n",
        "templates/project/x.md": "<!-- factory:begin -->\n",
        "config/x.yaml": "# A project may override a role in its factory.yaml.\n",
        "tools/swfactory/m.py": 'parser = argparse.ArgumentParser(prog="factory")\n',
        "docs/guide/a.md": "Run `factory work status <ID>`.\n",
        "docs/guide/b.md": "```text\nfactory config check --project .\n```\n",
        "docs/guide/c.md": "Write it to `~/.factory/staging/<project>/`.\n",
        "docs/guide/install.md": (  # checked outside the upgrade section, not inside it
            "## Step 1\n\nCopy factory.exe and `factory.yaml`.\n\n## Upgrading from 0.1.0\n\n"
            "```bash\nclaude plugin uninstall software-factory@bsri-factory\n```\n"
            "Move `~/.factory/` to `~/.sw-factory/`; `factory.yaml` is not read.\n\n"
            "## Troubleshooting\n\nDelete `~/.factory/` too.\n\n## Upgrading from 0.2.0\n\n"
            "Rename `program:` to `epic:`; finish `PRG-<n>` items first; the track was called Program.\n\n"
            "## After\n\nA Program is now an Epic.\n"
        ),
        "docs/specs/design.md": "| `factory-evals` | Runs the factory's own evals |\n",
        "docs/map/map.html": "<code>factory skills check</code>\n",
        "docs/dashboard/d.html": '{ "file": "factory/work/FIX-13/spec.md" }\n',
        "docs/acceptance/v1-checklist.md": "Install with `claude plugin install software-factory@bsri-factory`.\n",
        "docs/decisions.md": (  # checked outside its F-rows, not in them
            "Append-only log; projects keep factory.yaml.\n"
            "| F13 | 2026 | `factory.yaml` lists who may approve. | ok | Owner |\n"
            "| F17 | 2026 | The track Program is now Epic: `PRG-<n>` becomes `EPIC-<n>`, `--track program` "
            "becomes `--track epic`. | ok | Owner |\n"
        ),
        ".claude-plugin/plugin.json": '{\n  "name": "software-factory"\n}\n',
        "README.md": "claude plugin install software-factory@bsri-factory\n",
        # The epic track's old name (F17).
        "skills/d/SKILL.md": "Open it: `sw-factory work new --track program --slug x --title \"X\"`.\n",
        "skills/e/references/y.md": "Programs run milestone demos.\n",  # only the broad check finds this
        "templates/project/z.md": "tracks:\n  fix: [correctness]\n  program: [correctness, spec]\n",
        "prompts/z.md": "In a Program, the tester runs each milestone's demo.\n",
        "tools/swfactory/t.py": 'TRACK_PREFIX = {"program": "PRG", "feature": "FEA", "fix": "FIX"}\n',
        "docs/guide/f.md": "Ids look like `EPIC-2`; the templates are in `templates/work/program/`.\n",
        "docs/guide/g.md": "Its id looks like `PRG-2`.\n",
        "docs/specs/u.md": "| | Program | Feature | Fix |\n",
        "docs/specs/w.md": "Triage picks the Fix, Feature or Program track.\n",
        "docs/map/m2.html": '<text class="t-name" x="220" y="106" text-anchor="middle">Program</text>\n',
        "docs/dashboard/e.html": '{ "id": "EPIC-1", "track": "Program" }\n',
        "docs/dashboard/g.html": '{ "facts": [["Branch", "prg-01-equipment"]] }\n',
    }
    new = {
        "skills/c/SKILL.md": (
            "Settings are in `sw-factory.yaml`; work is under `sw-factory/work/` on `sw-factory/<ID>-<slug>`.\n"
            "Hand the task to the `task-loop` skill, or spawn `bsri-sw-factory:tester`.\n"
            "Run `sw-factory work status <ID>`; stage files under `~/.sw-factory/staging/`.\n"
        ),
        "templates/project/y.md": "<!-- sw-factory:begin -->\nThe software factory pauses at each checkpoint.\n",
        "prompts/y.md": "<!-- factory: role=lens-security vendor=openai -->\nFactory-Work: FIX-1\n",
        "tools/swfactory/n.py": 'HOME = os.environ.get("FACTORY_HOME")\nfrom swfactory.config import factory_home\n',
        "docs/guide/d.md": (
            "See docs/map/factory-map.html, the bsri-factory marketplace and\n"
            "https://github.com/Battlespace-Research/software-factory, cloned to ~/src/software-factory/.\n"
            "```text\nsw-factory config check --project .\n```\n"
            "Add the marketplace with `claude plugin marketplace add <factory-clone>`.\n"
        ),
        "docs/guide/e.md": "factory skills call the command; outside a code block this line is prose.\n",
        "docs/specs/s.md": "Template: `docs/dashboard/factory-dashboard.html`, its `factory-dashboard/0.1` block.\n",
        "docs/dashboard/x.html": "<script id=\"factory-data\">{}</script>\ngetElementById('factory-data')\n",
        "docs/specs/t.md": (
            "Each lens file ends `<!-- factory: role=lens-<lens> vendor=openai model=… -->`.\n"
            "See [the plan](../plans/2026-10-20-factory-core-v1.md) and its rulings, 2026-10-20-factory-core-v1-rulings.md.\n"
        ),
        ".codex-plugin/plugin.json": '{\n  "name": "bsri-sw-factory",\n  "repository": '
                                     '"https://github.com/Battlespace-Research/software-factory"\n}\n',
        "config/y.yaml": "# `sw-factory skills check` reads this file.\n",
        # The epic track's new name, and "program" where it is not the track (F17).
        "skills/g/SKILL.md": (
            "An Epic has milestones: open it with `sw-factory work new --track epic`; its id is `EPIC-<n>`.\n"
            "If the demo is a document of steps rather than a program, carry out each step.\n"
        ),
        "templates/project/v.md": "tracks:\n  epic: [correctness, security, maintainability, spec]\n",
        "tools/swfactory/q.py": (
            'TRACK_PREFIX = {"epic": "EPIC", "feature": "FEA", "fix": "FIX"}\n'
            "# Anything started directly (an .exe, a POSIX program) is not affected.\n"
        ),
        "docs/guide/h.md": "Edge is in `C:\\Program Files (x86)\\Microsoft\\Edge`; the program writes a log there.\n",
        "docs/dashboard/f.html": "var TRACK = { Epic: 'EPIC', Feature: 'FEA', Fix: 'FIX' };\n",
        "docs/specs/v.md": (
            "Status: rev 2.2 · track Program renamed Epic per F17\nfrom a bug fix to a multi-week epic\n"
        ),
        "docs/map/o.html": (
            '<tr><td class="mono">F17</td><td>The largest track is renamed from Program to Epic.</td></tr>\n'
        ),
    }
    for name, text in {**old, **new}.items():
        if name in old and name in new:
            raise AssertionError(f"{name} is in both sets")
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    offenders = find_old_names(tmp_path)
    flagged = {line.split(":", 1)[0] for line in offenders}
    assert flagged == set(old), offenders
    # Only the lines outside the allowed section and the F-rows count.
    assert [line for line in offenders if line.startswith(("docs/guide/install.md", "docs/decisions.md"))] == [
        "docs/guide/install.md:3: the old project file factory.yaml: Copy factory.exe and `factory.yaml`.",
        "docs/guide/install.md:14: the old staging folder .factory: Delete `~/.factory/` too.",
        "docs/guide/install.md:22: the old track name Program: A Program is now an Epic.",
        "docs/decisions.md:1: the old project file factory.yaml: Append-only log; projects keep factory.yaml.",
    ]
