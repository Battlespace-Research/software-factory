"""Prompt templates, ``sw-factory prompt render`` and the subagent definitions."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from swfactory import cli
from swfactory.adapters import AGENT_FOR_ROLE
from swfactory.config import factory_home
from swfactory.prompts import MissingValue, render, required_placeholders
from swfactory.reviewmd import load_review_md
from swfactory.verdict import BLOCKING_SEVERITIES, PRIOR_STATUSES, parse_review_markdown

LENSES = ["correctness", "security", "maintainability", "spec"]
LENS_PLACEHOLDERS = {
    "lens", "checklist", "task_id", "round", "diff_command", "spec_path", "prior_review_path", "nit_cap",
}
DOC_PLACEHOLDERS = {"document_path", "revision", "decisions_path", "prior_review_path"}
TESTER_PLACEHOLDERS = {"work_id", "task_id", "commits", "verify_command", "demo_script", "report_path"}
REVIEW_PROMPTS = [f"lens-{lens}.md" for lens in LENSES] + ["document-review.md"]
FINDINGS_HEADER = "| ID | Severity | Location | Finding | Fix |"

_EXAMPLE = re.compile(r"^```markdown\n(.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL)


def prompt(name: str) -> Path:
    return factory_home() / "prompts" / name


def dummy_values(names: set[str]) -> dict[str, str]:
    values = {name: f"<{name}>" for name in names}
    values.update({"nit_cap": "5", "round": "2"})
    return {name: value for name, value in values.items() if name in names}


def front_matter(path: Path) -> dict:
    lines = path.read_text(encoding="utf-8").splitlines()  # a checkout may have CRLF line endings
    assert lines[0] == "---", f"{path.name} must start with YAML front matter"
    end = lines.index("---", 1)
    data = yaml.safe_load("\n".join(lines[1:end]))
    assert isinstance(data, dict), f"{path.name}: the front matter is not a mapping"
    return data


# --------------------------------------------------------------------------- the templates

@pytest.mark.parametrize("lens", LENSES)
def test_each_lens_prompt_has_contract(lens):
    t = (factory_home() / f"prompts/lens-{lens}.md").read_text(encoding="utf-8")
    assert "VERDICT:" in t and FINDINGS_HEADER in t and "{{prior_review_path}}" in t
    assert "| ID | Status |" in t and "None." in t and "\\|" in t and "{{diff_command}}" in t


@pytest.mark.parametrize("lens", LENSES)
def test_lens_prompt_placeholders(lens):
    assert required_placeholders(prompt(f"lens-{lens}.md")) == LENS_PLACEHOLDERS


def test_document_review_placeholders():
    p = prompt("document-review.md")
    assert required_placeholders(p) == DOC_PLACEHOLDERS
    t = p.read_text(encoding="utf-8")
    assert t.splitlines()[0].startswith("# ") and FINDINGS_HEADER in t and "| ID | Status |" in t


def test_tester_brief_placeholders():
    p = prompt("tester-brief.md")
    assert required_placeholders(p) == {"work_id", "task_id", "commits", "verify_command", "demo_script", "report_path"}


def test_maintainability_prompt_says_its_findings_never_block():
    t = prompt("lens-maintainability.md").read_text(encoding="utf-8")
    assert "never block" in t and "follow-up" in t


def test_security_prompt_has_its_own_checklist():
    t = prompt("lens-security.md").read_text(encoding="utf-8").lower()
    for topic in ("input validation", "authentication", "authorization", "secrets", "injection",
                  "unsafe deserialization", "dependency"):
        assert topic in t, topic


@pytest.mark.parametrize("name", REVIEW_PROMPTS)
def test_prompt_examples_parse_and_follow_the_rules(name):
    """Every example review in a review prompt parses, and obeys the rules the prompt states, so the prompt
    and the parser cannot drift apart."""
    text = render(prompt(name), dummy_values(required_placeholders(prompt(name))))
    examples = _EXAMPLE.findall(text)
    assert examples, f"{name} has no ```markdown example"
    statuses: set[str] = set()
    for example in examples:
        review = parse_review_markdown(example, "example", nit_cap=5)
        assert review.warnings == []
        blocking = any(f.severity in BLOCKING_SEVERITIES for f in review.findings)
        assert (review.verdict == "CHANGES") == blocking
        ids = [p.id for p in review.prior] + [f.id for f in review.findings]
        assert len(ids) == len(set(ids)), "an id is reused"
        assert all(re.fullmatch(r"R\d+", id_) for id_ in ids)
        statuses |= {p.status for p in review.prior}
        if any(p.status == "not fixed" for p in review.prior):
            # an earlier finding that is not fixed is repeated as a current one, under a new id
            assert any("not fixed" in f.summary for f in review.findings)
    assert statuses == set(PRIOR_STATUSES), "the examples show every earlier-finding status"
    assert any("|" in f.summary + f.fix for e in examples for f in parse_review_markdown(e, "x").findings), \
        "one example shows an escaped | inside a cell"


# --------------------------------------------------------------------------- render

def test_render_missing_placeholder_raises():
    p = prompt("lens-correctness.md")
    with pytest.raises(MissingValue) as e:
        render(p, {"lens": "x"})
    assert "checklist" in str(e.value)
    assert e.value.names == sorted(LENS_PLACEHOLDERS - {"lens"})
    assert isinstance(e.value, KeyError)


def test_render_fills_every_placeholder_in_one_pass(tmp_path):
    t = tmp_path / "t.md"
    t.write_bytes("\ufeff{{a}} and {{b}}\r\n{{a}}\r\n".encode("utf-8"))
    assert required_placeholders(t) == {"a", "b"}
    assert render(t, {"a": "{{b}}", "b": "x"}) == "{{b}} and x\n{{b}}\n"


def test_render_tester_brief():
    out = render(prompt("tester-brief.md"), dummy_values(TESTER_PLACEHOLDERS))
    assert "{{" not in out and "<verify_command>" in out and "<report_path>" in out


# --------------------------------------------------------------------------- sw-factory prompt render

LENS_SETS = [
    "--set", "task_id=T2",
    "--set", "round=1",
    "--set", "diff_command=git diff main...HEAD",
    "--set", "spec_path=sw-factory/work/FEA-1-demo/spec.md",
    "--set", "prior_review_path=none",
]


def review_md() -> Path:
    return factory_home() / "templates" / "project" / "REVIEW.md"


def test_cli_prompt_render_from_review_md(tmp_path, capsys):
    out = tmp_path / "sub" / "prompt.md"
    code = cli.main([
        "prompt", "render", str(prompt("lens-security.md")),
        "--from-review-md", str(review_md()), "--lens", "security", *LENS_SETS, "--out", str(out),
    ])
    assert code == 0
    assert str(out) in capsys.readouterr().out
    text = out.read_bytes().decode("utf-8")
    assert "\r" not in text and "{{" not in text
    assert load_review_md(review_md()).lenses["security"] in text
    assert "git diff main...HEAD" in text and "**security**" in text and "At most 5 nits" in text


def test_cli_prompt_render_relative_template_from_the_factory(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # a project folder with no prompts/ of its own
    sets = [f"{name}=v-{name}" for name in sorted(TESTER_PLACEHOLDERS)]
    args = ["prompt", "render", "prompts/tester-brief.md", "--out", "brief.md"]
    for item in sets:
        args += ["--set", item]
    assert cli.main(args) == 0
    assert "v-verify_command" in (tmp_path / "brief.md").read_text(encoding="utf-8")


def test_cli_prompt_render_lists_every_missing_value(tmp_path, capsys):
    out = tmp_path / "p.md"
    code = cli.main(["prompt", "render", str(prompt("lens-spec.md")), "--set", "lens=spec", "--out", str(out)])
    assert code == 2
    err = capsys.readouterr().err
    for name in LENS_PLACEHOLDERS - {"lens"}:
        assert name in err
    assert not out.exists()


@pytest.mark.parametrize("args, expected", [
    (["--set", "nonsense"], "NAME=VALUE"),
    (["--set", "lens=a", "--set", "lens=b"], "twice"),
    (["--lens", "security"], "--from-review-md"),
    (["--from-review-md", "REVIEW.md"], "--lens"),
    (["--set", "colour=blue"], "colour"),
])
def test_cli_prompt_render_refuses_bad_values(tmp_path, capsys, args, expected):
    out = tmp_path / "p.md"
    assert cli.main(["prompt", "render", str(prompt("lens-spec.md")), *args, "--out", str(out)]) == 2
    assert expected in capsys.readouterr().err
    assert not out.exists()


def test_cli_prompt_render_unknown_lens(tmp_path, capsys):
    out = tmp_path / "p.md"
    code = cli.main(["prompt", "render", str(prompt("lens-spec.md")), "--from-review-md", str(review_md()),
                     "--lens", "ux", *LENS_SETS, "--out", str(out)])
    assert code == 2
    assert "ux" in capsys.readouterr().err
    assert not out.exists()


def test_cli_prompt_render_review_md_value_clashes_with_set(tmp_path, capsys):
    out = tmp_path / "p.md"
    code = cli.main(["prompt", "render", str(prompt("lens-spec.md")), "--from-review-md", str(review_md()),
                     "--lens", "spec", "--set", "nit_cap=9", *LENS_SETS, "--out", str(out)])
    assert code == 2
    assert "nit_cap" in capsys.readouterr().err


def test_cli_prompt_render_review_md_on_a_template_without_a_checklist(tmp_path, capsys):
    out = tmp_path / "p.md"
    code = cli.main(["prompt", "render", str(prompt("tester-brief.md")), "--from-review-md", str(review_md()),
                     "--lens", "spec", "--out", str(out)])
    assert code == 2
    assert "checklist" in capsys.readouterr().err


def test_cli_prompt_render_missing_template(tmp_path, capsys):
    code = cli.main(["prompt", "render", str(tmp_path / "nope.md"), "--out", str(tmp_path / "p.md")])
    assert code == 2
    assert "nope.md" in capsys.readouterr().err


# --------------------------------------------------------------------------- subagent definitions

def agent_files() -> list[Path]:
    return sorted((factory_home() / "agents").glob("*.md"))


def test_agents_have_no_model_field():
    files = agent_files()
    assert files
    for path in files:
        data = front_matter(path)
        assert "model" not in data, path.name
        assert set(data) == {"name", "description", "tools"}, path.name
        assert data["name"] == path.stem
        assert isinstance(data["tools"], str) and isinstance(data["description"], str)


def test_every_mapped_agent_is_defined():
    assert {path.stem for path in agent_files()} == set(AGENT_FOR_ROLE.values())


@pytest.mark.parametrize("agent", ["lens-reviewer", "doc-reviewer", "tester"])
def test_reviewer_and_tester_tools(agent):
    data = front_matter(factory_home() / "agents" / f"{agent}.md")
    assert data["tools"] == "Read, Grep, Glob, Bash"


def test_cli_commands_include_prompt():
    assert "prompt" in cli.COMMANDS
