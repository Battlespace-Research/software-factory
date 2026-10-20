"""Lint the factory's own skills (design sections 6.3 and 13).

A factory skill is a SKILL.md that a Claude Code or Codex session loads and follows, calling the ``factory`` CLI
for every deterministic step. These checks keep the skills honest about what they call:

- front matter with ``name`` (the folder's name) and a ``description`` that says when to use the skill;
- every ``factory …`` call names a real command, subcommand and option of the CLI;
- one ``Uses skills:`` line per skill, naming only third-party skills registered in ``config/skills.yaml``;
- every ``references/…`` file a skill points to exists, and the hard-stop reference lists every default id.
"""
from __future__ import annotations

import argparse
import re
import shlex
from pathlib import Path

import pytest
import yaml

from swfactory.cli import COMMANDS, build_parser
from swfactory.config import DEFAULT_HARD_STOPS, factory_home, load_skills_registry

EXPECTED_SKILLS = {
    "factory-setup",
    "factory-triage",
    "factory-run",
    "factory-task-loop",
    "factory-review-panel",
    "factory-merge-gate",
}

_USES_SKILLS = re.compile(r"^Uses skills:(.*)$", re.MULTILINE)
_COMMAND = re.compile(r"`factory ([a-z][a-z-]*)")
_CALL_SPAN = re.compile(r"`(factory [^`\n]+)`")
_FENCE = re.compile(r"^(```|~~~)")
# ``references/x.md`` in the skill's own folder, or ``../<other skill>/references/x.md``.
_REFERENCE = re.compile(r"(?:\.\./[A-Za-z0-9_-]+/)?references/[A-Za-z0-9_.-]+\.md")
_NAME_TOKEN = re.compile(r"[a-z][a-z-]*")


def skills_dir() -> Path:
    return factory_home() / "skills"


def skill_files() -> list[Path]:
    return sorted(skills_dir().glob("*/SKILL.md"))


def skill_markdown() -> list[Path]:
    """Every Markdown file of every skill: the SKILL.md files and their references."""
    return sorted(skills_dir().glob("*/**/*.md"))


def skill_names() -> set[str]:
    return {path.parent.name for path in skill_files()}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")


def front_matter(path: Path) -> dict:
    lines = read(path).split("\n")
    assert lines[0] == "---", f"{path}: must start with YAML front matter"
    end = lines.index("---", 1)
    data = yaml.safe_load("\n".join(lines[1:end]))
    assert isinstance(data, dict), f"{path}: the front matter is not a mapping"
    return data


def used_skills(text: str) -> set[str]:
    """The names on the skill's ``Uses skills:`` line (backticks and spaces around each name dropped)."""
    lines = _USES_SKILLS.findall(text)
    assert len(lines) == 1, f"expected exactly one 'Uses skills:' line, found {len(lines)}"
    return {name.strip().strip("`").strip() for name in lines[0].split(",") if name.strip()}


def registered_skill_names() -> set[str]:
    registry = load_skills_registry(factory_home() / "config" / "skills.yaml")
    return {skill.name for entries in registry.values() for skill in entries}


def factory_calls(text: str) -> list[str]:
    """Every ``factory …`` call in the text: inline code spans, and fenced-code lines that start with one."""
    calls = list(_CALL_SPAN.findall(text))
    fenced = False
    for line in text.split("\n"):
        if _FENCE.match(line.strip()):
            fenced = not fenced
        elif fenced and line.strip().startswith("factory "):
            calls.append(line.strip())
    return calls


def _subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser] | None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return None


def _options(parser: argparse.ArgumentParser) -> set[str]:
    return {option for action in parser._actions for option in action.option_strings}


def call_problems(call: str) -> list[str]:
    """Why ``call`` (``factory <command> [<subcommand>] …``) is not a real command line of the CLI, if it is not.

    Placeholders such as ``<ID>`` and ``…`` are allowed anywhere; every ``--option`` must belong to the
    (sub)command it follows."""
    try:
        tokens = shlex.split(call)
    except ValueError:  # an apostrophe or an unpaired quote in a placeholder: whitespace is good enough
        tokens = call.split()
    parser = build_parser()
    rest = tokens[1:]
    path = "factory"
    while rest and _NAME_TOKEN.fullmatch(rest[0]) and (choices := _subcommands(parser)) is not None:
        name = rest.pop(0)
        if name not in choices:
            return [f"{call!r}: '{path}' has no subcommand '{name}' (it has {', '.join(sorted(choices))})"]
        parser, path = choices[name], f"{path} {name}"
    problems = []
    for token in rest:
        if token.startswith("--"):
            option = token.split("=", 1)[0]
            if option not in _options(parser):
                problems.append(f"{call!r}: '{path}' has no option '{option}'")
    return problems


# --------------------------------------------------------------------------- the checks


def test_expected_skills_present():
    assert EXPECTED_SKILLS <= skill_names()


@pytest.mark.parametrize("skill_md", skill_files(), ids=lambda p: p.parent.name)
def test_skill_front_matter(skill_md):
    fm = front_matter(skill_md)
    assert fm["name"] == skill_md.parent.name
    assert isinstance(fm["description"], str) and len(fm["description"]) > 40


@pytest.mark.parametrize("md", skill_markdown(), ids=lambda p: p.relative_to(skills_dir()).as_posix())
def test_cli_commands_exist(md):
    assert set(_COMMAND.findall(read(md))) <= COMMANDS


@pytest.mark.parametrize("md", skill_markdown(), ids=lambda p: p.relative_to(skills_dir()).as_posix())
def test_cli_subcommands_and_options_exist(md):
    calls = factory_calls(read(md))
    problems = [problem for call in calls for problem in call_problems(call)]
    assert problems == []


@pytest.mark.parametrize("skill_md", skill_files(), ids=lambda p: p.parent.name)
def test_used_skills_registered(skill_md):
    assert used_skills(read(skill_md)) <= registered_skill_names()


@pytest.mark.parametrize("skill_md", skill_files(), ids=lambda p: p.parent.name)
def test_referenced_files_exist(skill_md):
    missing = [ref for ref in _REFERENCE.findall(read(skill_md)) if not (skill_md.parent / ref).is_file()]
    assert missing == []


def test_hard_stop_reference_lists_every_default_id():
    text = read(skills_dir() / "factory-run" / "references" / "hard-stops.md")
    assert [stop for stop in DEFAULT_HARD_STOPS if f"`{stop}`" not in text] == []


# --------------------------------------------------------------------------- the lint itself


def test_call_problems_accepts_real_calls():
    for call in (
        "factory --version",
        "factory work new --track fix --slug <slug> --title \"<title>\"",
        "factory approve <ID> spec --rev 2 --quote-file <file> --approver \"<name>\" --commit <sha>",
        "factory prompt render <factory>/prompts/document-review.md --set revision=2 --out <file>",
        "factory gate <ID> --spot-check=\"<criterion: evidence>\"",
        "factory setup --project x --verify-quick \"pytest -q --no-header\" --verify-full pytest --approver a",
        "factory role",
    ):
        assert call_problems(call) == [], call


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        ("factory work open --track fix", "has no subcommand 'open'"),
        ("factory approve <ID> spec --quote_file x", "has no option '--quote_file'"),
        ("factory role resolve tester --vendor claude", "has no option '--vendor'"),
        ("factory --verbose", "has no option '--verbose'"),
    ],
)
def test_call_problems_rejects_unreal_calls(call, expected):
    problems = call_problems(call)
    assert len(problems) == 1 and expected in problems[0]


def test_factory_calls_reads_spans_and_fenced_lines():
    text = "Run `factory work status <ID>` first.\n\n```text\nfactory gate <ID> --spot-check x\ngit status\n```\n"
    assert factory_calls(text) == ["factory work status <ID>", "factory gate <ID> --spot-check x"]


def test_used_skills_needs_exactly_one_line():
    assert used_skills("x\nUses skills: grilling, `research`\n") == {"grilling", "research"}
    with pytest.raises(AssertionError):
        used_skills("no such line\n")
