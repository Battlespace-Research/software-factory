"""No pre-rename name in what the factory ships (decision F16).

Version 0.2.0 renamed the plugin to ``bsri-sw-factory``, its skills and subagents (``setup-project``, ``intake``,
``orchestrate``, ``task-loop``, ``review-panel``, ``merge-gate``; ``lens-reviewer``, ``doc-reviewer``, ``tester``,
``researcher``), the CLI to ``sw-factory``, and what it creates in a project to ``sw-factory.yaml``, ``sw-factory/``,
``sw-factory/<ID>-<slug>`` branches, ``<!-- sw-factory:begin -->`` markers and ``~/.sw-factory/staging``; the design's
planned skills follow the same scheme (``deploy``, ``maintain``, ``dashboard``, ``evals``). A skill, prompt, template,
setting, manifest, guide, design page or line of code that still says an old name would send a session or a person to
a file, folder, branch, skill or command that no longer exists, so the scan fails on any of them.

Unchanged on purpose: the Python package ``swfactory``, the ``Factory-*`` commit trailers, the ``FACTORY_*``
environment variables, the repository ``Battlespace-Research/software-factory``, the ``bsri-factory`` marketplace and
the word "factory" in prose. These never match a pattern below. The kept names that look like old ones are allowed
explicitly (``KEPT``): the review-file stamp ``<!-- factory: ... -->``, the dashboard's ``factory-data`` id and
``factory-dashboard/0.1`` schema, and the file names ``factory-map.html``, ``factory-dashboard.html`` and those of the
v1 records (``*-factory-core-v1*.md``). So are the F-rows of the append-only decision log, and the install guide's
"Upgrading from 0.1.0" section, which has to name what it replaces.
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
PATTERNS = {what: re.compile(source) for what, source in OLD_NAMES.items()}

# Kept identifiers that look like old names: removed from a line before it is checked.
KEPT = {
    "the review-file stamp": r"<!--\s*factory:(?!(?:begin|end)\b).*?-->",  # not the old markers
    "the dashboard's data id": r"(?<![\w-])factory-data(?![\w-])",
    "the dashboard's data schema": r"(?<![\w-])factory-dashboard/\d+(?:\.\d+)*",
    "the map and dashboard file names": r"(?<![\w-])factory-(?:map|dashboard)\.html",
    "the v1 records' file names": r"(?<![\w-])factory-core-v1(?:-rulings)?\.md",
}
_KEPT = re.compile("|".join(f"(?:{source})" for source in KEPT.values()))
# Lines that may name old identifiers as they are: the decision log's rows (append-only; F16 records the rename).
ALLOWED_LINES = {"docs/decisions.md": re.compile(r"^\| F\d+ \|")}
# Sections that may name them: the upgrade steps say what each old name becomes.
ALLOWED_SECTIONS = {"docs/guide/install.md": "## Upgrading from 0.1.0"}

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
        allowed_section = ALLOWED_SECTIONS.get(rel)
        text = path.read_bytes().decode("utf-8", errors="replace")
        fenced = in_allowed_section = False
        for number, line in enumerate(text.splitlines(), start=1):
            if line.startswith("## "):
                in_allowed_section = line.strip() == allowed_section
            if _FENCE.match(line):
                fenced = not fenced
                continue
            if in_allowed_section or (allowed_line is not None and allowed_line.match(line)):
                continue
            checked = _KEPT.sub("", line)
            found = [what for what, pattern in PATTERNS.items() if pattern.search(checked)]
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
            "## Troubleshooting\n\nDelete `~/.factory/` too.\n"
        ),
        "docs/specs/design.md": "| `factory-evals` | Runs the factory's own evals |\n",
        "docs/map/map.html": "<code>factory skills check</code>\n",
        "docs/dashboard/d.html": '{ "file": "factory/work/FIX-13/spec.md" }\n',
        "docs/acceptance/v1-checklist.md": "Install with `claude plugin install software-factory@bsri-factory`.\n",
        "docs/decisions.md": (  # checked outside its F-rows, not in them
            "Append-only log; projects keep factory.yaml.\n"
            "| F13 | 2026 | `factory.yaml` lists who may approve. | ok | Owner |\n"
        ),
        ".claude-plugin/plugin.json": '{\n  "name": "software-factory"\n}\n',
        "README.md": "claude plugin install software-factory@bsri-factory\n",
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
            "https://github.com/Battlespace-Research/software-factory, cloned to E:/BSRI/GitHub/software-factory/.\n"
            "```text\nsw-factory config check --project .\n```\n"
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
        "docs/decisions.md:1: the old project file factory.yaml: Append-only log; projects keep factory.yaml.",
    ]
