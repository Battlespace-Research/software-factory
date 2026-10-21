"""No pre-rename name in what the factory ships (decision F16).

Version 0.2.0 renamed the plugin to ``bsri-sw-factory``, its skills and subagents (``setup-project``, ``intake``,
``orchestrate``, ``task-loop``, ``review-panel``, ``merge-gate``; ``lens-reviewer``, ``doc-reviewer``, ``tester``,
``researcher``), the CLI to ``sw-factory``, and what it creates in a project to ``sw-factory.yaml``, ``sw-factory/``,
``sw-factory/<ID>-<slug>`` branches, ``<!-- sw-factory:begin -->`` markers and ``~/.sw-factory/staging``. A skill,
prompt, template, guide or line of code that still says an old name would send a session to a file, folder, branch,
skill or command that no longer exists, so the scan fails on any of them.

Unchanged on purpose, and not flagged: the Python package ``swfactory``, the ``Factory-*`` commit trailers, the
``FACTORY_*`` environment variables, the repository ``Battlespace-Research/software-factory``, the ``bsri-factory``
marketplace, the review-file stamp ``<!-- factory: ... -->`` and the word "factory" in prose.
"""
from __future__ import annotations

import re
from pathlib import Path

from swfactory.config import factory_home

SCANNED_FOLDERS = ("skills", "agents", "prompts", "templates", "tools/swfactory", "docs/guide")
SCANNED_FILES = ("README.md",)

_CLI_COMMANDS = "config|role|panel|work|approve|gate|setup|skills|prompt|--version"
OLD_NAMES = {
    "the old project file factory.yaml": r"(?<![\w-])factory\.yaml",
    "the old project folder or branch prefix factory/": r"(?<![\w.-])factory/",
    "the old staging folder .factory": r"(?<![\w-])\.factory/",
    "the old section markers": r"(?<![\w-])factory:(?:begin|end)\b",
    "the old plugin name software-factory": r"software-factory[:@]|`software-factory`",
    "an old skill name": r"(?<![\w-])factory-(?:setup|triage|run|task-loop|review-panel|merge-gate)(?![\w-])",
    "an old subagent name": r"(?<![\w-])factory-(?:lens-reviewer|doc-reviewer|tester|researcher)(?![\w-])",
    "the old command name factory": r"`factory[` ]|<code>factory[ <]|(?<![\w-])[\"']factory[\"']",
}
PATTERNS = {what: re.compile(source) for what, source in OLD_NAMES.items()}
# A line of a fenced code block that runs the old command, such as ``factory config check``.
_FENCE = re.compile(r"^\s*(```|~~~)")
_FENCED_CALL = re.compile(rf"^\s*factory (?:{_CLI_COMMANDS})(?![\w-])")


def scanned_files(root: Path) -> list[Path]:
    """Every file under the scanned folders (``__pycache__`` aside) and the scanned top-level files under ``root``."""
    files: list[Path] = []
    for folder in SCANNED_FOLDERS:
        base = root / folder
        if base.is_dir():
            files += [path for path in sorted(base.rglob("*")) if path.is_file() and "__pycache__" not in path.parts]
    files += [root / name for name in SCANNED_FILES if (root / name).is_file()]
    return files


def find_old_names(root: Path) -> list[str]:
    """``<path>:<line>: <what>: <text>`` for each old name in the scanned files under ``root``."""
    offenders: list[str] = []
    for path in scanned_files(root):
        text = path.read_bytes().decode("utf-8", errors="replace")
        fenced = False
        for number, line in enumerate(text.splitlines(), start=1):
            found = [what for what, pattern in PATTERNS.items() if pattern.search(line)]
            if _FENCE.match(line):
                fenced = not fenced
            elif fenced and _FENCED_CALL.match(line):
                found.append("the old command name factory")
            offenders += [f"{path.relative_to(root).as_posix()}:{number}: {what}: {line.strip()}" for what in found]
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
        "tools/swfactory/m.py": 'parser = argparse.ArgumentParser(prog="factory")\n',
        "docs/guide/a.md": "Run `factory work status <ID>`.\n",
        "docs/guide/b.md": "```text\nfactory config check --project .\n```\n",
        "docs/guide/c.md": "Write it to `~/.factory/staging/<project>/`.\n",
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
    }
    for name, text in {**old, **new}.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    flagged = {line.split(":", 1)[0] for line in find_old_names(tmp_path)}
    assert flagged == set(old)
