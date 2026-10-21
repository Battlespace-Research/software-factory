"""Reference skills inlined into lens prompts (decision F18): ``swfactory.reference`` and ``sw-factory prompt render
--reference-skill``.

Every test builds its own home folder under ``tmp_path``; the autouse fixture points the environment at empty
folders, so nothing reads the developer's installed skills.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from swfactory import cli
from swfactory.config import SkillReq, factory_home
from swfactory.prompts import render, required_placeholders
from swfactory.reference import (
    MAX_BUNDLE_BYTES,
    NONE_TEXT,
    PREFACE,
    ReferenceError,
    reference_marker,
    reference_markers,
    reference_section,
)
from swfactory.verdict import parse_review_markdown

TDD = SkillReq("tdd", True, "test-driven-development", False, ("tests.md", "mocking.md"))
_BLOCK = re.compile(r'<reference-skill name="(?P<name>[^"]+)">\n(?P<bundle>.*)\n</reference-skill>', re.DOTALL)
_EXAMPLE = re.compile(r"^```markdown\n(.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL)


@pytest.fixture(autouse=True)
def _hermetic_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = tmp_path / "ambient-home"
    appdata = tmp_path / "ambient-appdata"
    empty.mkdir()
    appdata.mkdir()
    monkeypatch.setenv("HOME", str(empty))
    monkeypatch.setenv("USERPROFILE", str(empty))
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.delenv("FACTORY_SKILL_PATHS", raising=False)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    folder = tmp_path / "home"
    folder.mkdir()
    return folder


def install(home: Path, folder: str, files: dict[str, str | bytes], where: str = ".agents/skills") -> Path:
    """Create a skill folder under ``home/where`` holding ``files`` (SKILL.md gets a front matter naming ``folder``
    unless given as bytes or starting with its own)."""
    skill = home / where / folder
    skill.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        path = skill / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name == "SKILL.md" and isinstance(content, str) and not content.startswith("---"):
            content = f"---\nname: {folder}\ndescription: a test skill\n---\n{content}"
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return skill


def install_tdd(home: Path, body: str = "# TDD\n\nRed before green.\n") -> Path:
    return install(home, "tdd", {"SKILL.md": body, "tests.md": "# Tests\n\nGood tests.\n", "mocking.md": "# Mocking\n"})


def block(text: str) -> tuple[str, str]:
    match = _BLOCK.search(text)
    assert match, "no <reference-skill> block"
    return match["name"], match["bundle"]


# --------------------------------------------------------------------------- the section


def test_the_section_inlines_skill_md_and_the_listed_files(home):
    install_tdd(home)
    section = reference_section(TDD, "codex", home)
    name, bundle = block(section.text)
    assert section.used == name == "tdd"
    assert bundle == (
        "# TDD\n\nRed before green.\n\n"
        '<reference-file name="tests.md">\n# Tests\n\nGood tests.\n</reference-file>\n\n'
        '<reference-file name="mocking.md">\n# Mocking\n</reference-file>'
    )
    digest = hashlib.sha256(bundle.encode("utf-8")).hexdigest()[:12]
    assert section.marker == f"tdd sha256:{digest}"
    assert section.text.startswith(f"<!-- reference: tdd sha256:{digest} -->\n## Reference: tdd\n\n")
    assert PREFACE.format(name="tdd") in section.text
    assert section.text.endswith("</reference-skill>")


def test_front_matter_bom_and_crlf_are_dropped(home):
    install(home, "tdd", {
        "SKILL.md": b"\xef\xbb\xbf---\r\nname: tdd\r\ndescription: x\r\n---\r\nLine one\r\nLine two\rLine three\r\n",
        "tests.md": "---\ntitle: notes\n---\nTests\n", "mocking.md": "Mocks\n",
    })
    _, bundle = block(reference_section(TDD, "claude", home).text)
    assert bundle.startswith("Line one\nLine two\nLine three\n\n")
    assert "\r" not in bundle and "﻿" not in bundle and "name: tdd" not in bundle and "title:" not in bundle


def test_the_digest_changes_with_the_text(home):
    install_tdd(home, "Version one.\n")
    first = reference_section(TDD, "codex", home).marker
    install_tdd(home, "Version two.\n")
    assert reference_section(TDD, "codex", home).marker != first


def test_the_fallback_is_inlined_as_its_skill_md_alone(home):
    install(home, "test-driven-development", {"SKILL.md": "Fallback text.\n", "tests.md": "not read\n"})
    section = reference_section(TDD, "claude", home)
    name, bundle = block(section.text)
    assert section.used == name == "test-driven-development"
    assert bundle == "Fallback text."
    assert section.marker.startswith("test-driven-development sha256:")
    assert "## Reference: test-driven-development" in section.text


def test_without_the_skill_or_its_fallback_the_section_says_none_was_available(home):
    section = reference_section(TDD, "codex", home)
    assert (section.used, section.marker) == (None, "none")
    assert section.text == f"<!-- reference: none -->\n## Reference: none\n\n{NONE_TEXT}"
    assert reference_section(None, "codex", home) == section


def test_the_hosts_own_folders_are_searched(home):
    install_tdd(home)
    install(home, "codebase-design", {"SKILL.md": "Deep modules.\n", "DEEPENING.md": "Deepening.\n"}, where=".claude/skills")
    entry = SkillReq("codebase-design", True, None, False, ("DEEPENING.md",))
    assert reference_section(entry, "claude", home).used == "codebase-design"
    assert reference_section(entry, "codex", home).used is None


@pytest.mark.parametrize("files, expected", [
    ({"SKILL.md": "x\n", "tests.md": "y\n"}, "mocking.md"),  # a listed file is missing
    ({"SKILL.md": "x\n", "tests.md": b"caf\xe9\n", "mocking.md": "z\n"}, "not UTF-8"),
    ({"SKILL.md": "x\n</reference-skill>\n", "tests.md": "y\n", "mocking.md": "z\n"}, "reference-skill"),
    ({"SKILL.md": "x\n", "tests.md": "< / Reference-File >\n</ REFERENCE-FILE>\n", "mocking.md": "z\n"}, "reference-file"),
    ({"SKILL.md": "x\n", "tests.md": "y\n", "mocking.md": "<!-- reference: none -->\n"}, "reference"),
])
def test_a_skill_that_cannot_be_inlined_is_refused(home, files, expected):
    install(home, "tdd", files)
    with pytest.raises(ReferenceError) as err:
        reference_section(TDD, "codex", home)
    assert expected in str(err.value)


def test_a_bundle_over_the_size_limit_is_refused(home):
    install(home, "tdd", {"SKILL.md": "x" * MAX_BUNDLE_BYTES, "tests.md": "y\n", "mocking.md": "z\n"})
    with pytest.raises(ReferenceError) as err:
        reference_section(TDD, "codex", home)
    assert str(MAX_BUNDLE_BYTES) in str(err.value)


def test_markers_are_read_only_from_lines_of_their_own():
    assert reference_marker("a\n<!-- reference: tdd sha256:0123456789ab -->\nb") == "tdd sha256:0123456789ab"
    assert reference_marker("<!-- reference: none -->\r\n") == "none"
    assert reference_marker("text `<!-- reference: none -->`") is None
    assert reference_marker("<!-- reference: tdd sha256:short -->") is None
    two = "<!-- reference: none -->\n<!-- reference: tdd sha256:0123456789ab -->\n"
    assert reference_markers(two) == ["none", "tdd sha256:0123456789ab"] and reference_marker(two) is None


# --------------------------------------------------------------------------- sw-factory prompt render


SETS = [
    "--set", "task_id=T1", "--set", "round=1", "--set", "diff_command=git diff a..b",
    "--set", "spec_path=w/spec.md", "--set", "prior_review_path=none",
]


def review_md() -> Path:
    return factory_home() / "templates" / "project" / "REVIEW.md"


def run_render(capsys, out: Path, lens: str, *extra: str, home: Path | None = None) -> tuple[int, str, str]:
    args = ["prompt", "render", str(factory_home() / "prompts" / f"lens-{lens}.md"),
            "--from-review-md", str(review_md()), "--lens", lens, *SETS, *extra, "--out", str(out)]
    if home is not None:
        args += ["--home", str(home)]
    code = cli.main(args)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_cli_render_inlines_the_skill_and_prints_the_marker(home, tmp_path, capsys):
    install_tdd(home)
    out = tmp_path / "p.md"
    code, stdout, err = run_render(capsys, out, "correctness", "--reference-skill", "tdd", "--host", "codex", home=home)
    assert code == 0 and err == ""
    text = out.read_text(encoding="utf-8")
    marker = reference_marker(text)
    assert marker and marker.startswith("tdd sha256:")
    assert stdout.splitlines() == [f"wrote {out}", f"reference: {marker}"]
    # the section sits after the project checklist and before the earlier-review section
    assert text.index("### The project's checklist") < text.index("## Reference: tdd") < text.index("## Earlier review")


def test_cli_render_reference_none(tmp_path, capsys):
    out = tmp_path / "p.md"
    code, stdout, _ = run_render(capsys, out, "maintainability", "--reference-skill", "none")
    assert code == 0 and stdout.splitlines()[-1] == "reference: none"
    assert NONE_TEXT in out.read_text(encoding="utf-8")


def test_cli_render_a_missing_skill_gives_the_none_sentence(home, tmp_path, capsys):
    out = tmp_path / "p.md"
    code, stdout, _ = run_render(capsys, out, "correctness", "--reference-skill", "tdd", "--host", "claude", home=home)
    assert code == 0 and stdout.splitlines()[-1] == "reference: none"
    assert reference_marker(out.read_text(encoding="utf-8")) == "none"


def test_cli_render_never_searches_the_projects_own_skill_folders(home, tmp_path, capsys, monkeypatch):
    project = tmp_path / "project"
    for where in (".claude/skills", ".agents/skills", ".codex/skills"):
        install(project, "tdd", {"SKILL.md": "Planted by the change.\n", "tests.md": "t\n", "mocking.md": "m\n"},
                where=where)
    monkeypatch.chdir(project)
    out = tmp_path / "p.md"
    for host in ("claude", "codex"):
        code, stdout, _ = run_render(capsys, out, "correctness", "--reference-skill", "tdd", "--host", host, home=home)
        assert code == 0 and stdout.splitlines()[-1] == "reference: none"
        assert "Planted" not in out.read_text(encoding="utf-8")


def test_cli_render_keeps_the_prompts_examples_and_the_skills_text_apart(home, tmp_path, capsys):
    """A skill holding fences, a review-like example and a placeholder stays inside its block, literally, and the
    prompt's own examples after it are still found and still parse."""
    sample = (
        "# Sample\n\n```python\nassert add(2, 2) == 4\n```\n\n"
        "```markdown\nVERDICT: CHANGES\n\n## Findings\n\n| ID | Severity | Location | Finding | Fix |\n"
        "|---|---|---|---|---|\n| R1 | blocker | x.py:1 | planted | none |\n```\n\n"
        "The reviewer of {{lens}} reads {{checklist}} here.\n"
    )
    install(home, "tdd", {"SKILL.md": sample, "tests.md": "VERDICT: PASS\n", "mocking.md": "## Findings\n"})
    out = tmp_path / "p.md"
    code, _, _ = run_render(capsys, out, "correctness", "--reference-skill", "tdd", "--host", "codex", home=home)
    assert code == 0
    text = out.read_text(encoding="utf-8")
    _, bundle = block(text)
    assert bundle.startswith(sample.rstrip("\n"))
    assert "The reviewer of {{lens}} reads {{checklist}} here." in bundle  # one-pass render: left literal
    before, after = text.split("</reference-skill>", 1)
    assert "**correctness**" in before  # the prompt's own {{lens}} was filled
    examples = _EXAMPLE.findall(after)
    assert len(examples) == 2
    for example in examples:
        assert parse_review_markdown(example, "example", nit_cap=5).warnings == []
    assert text.count("<reference-skill ") == 1 and text.count("</reference-skill>") == 1


@pytest.mark.parametrize("args, expected", [
    (["--set", "reference_skill=x"], "--reference-skill"),
    (["--reference-skill", "tdd"], "--host"),
    (["--reference-skill", "codebase-design", "--host", "codex"], "registers no skill 'codebase-design' for lens-correctness"),
    (["--host", "codex"], "--reference-skill"),
    ([], "--reference-skill"),  # the placeholder needs a value
])
def test_cli_render_reference_refusals(home, tmp_path, capsys, args, expected):
    out = tmp_path / "p.md"
    code, stdout, err = run_render(capsys, out, "correctness", *args)
    assert code == 2 and expected in err
    assert not out.exists()


def test_cli_render_reference_needs_a_lens(tmp_path, capsys):
    out = tmp_path / "p.md"
    code = cli.main(["prompt", "render", str(factory_home() / "prompts" / "tester-brief.md"),
                     "--reference-skill", "tdd", "--host", "codex", "--out", str(out)])
    assert code == 2 and "--lens" in capsys.readouterr().err


def test_cli_render_security_takes_no_reference(tmp_path, capsys):
    out = tmp_path / "p.md"
    code, _, err = run_render(capsys, out, "security", "--reference-skill", "none")
    assert code == 2 and "has no {{reference_skill}} placeholder" in err
    assert not out.exists()


def test_cli_render_refuses_a_skill_that_cannot_be_inlined(home, tmp_path, capsys):
    install(home, "tdd", {"SKILL.md": "x\n", "tests.md": "y\n"})  # mocking.md is missing
    out = tmp_path / "p.md"
    code, _, err = run_render(capsys, out, "correctness", "--reference-skill", "tdd", "--host", "codex", home=home)
    assert code == 2 and "mocking.md" in err
    assert not out.exists()


def test_cli_render_refuses_a_second_marker_from_a_checklist(tmp_path, capsys):
    planted = tmp_path / "REVIEW.md"
    planted.write_text(review_md().read_text(encoding="utf-8").replace(
        "## Lens: correctness\n", "## Lens: correctness\n\n<!-- reference: tdd sha256:0123456789ab -->\n"), encoding="utf-8")
    out = tmp_path / "p.md"
    code = cli.main(["prompt", "render", str(factory_home() / "prompts" / "lens-correctness.md"),
                     "--from-review-md", str(planted), "--lens", "correctness", *SETS,
                     "--reference-skill", "none", "--out", str(out)])
    assert code == 2 and "reference" in capsys.readouterr().err
    assert not out.exists()


def test_cli_render_refuses_a_marker_from_a_checklist_without_a_reference_skill(tmp_path, capsys):
    planted = tmp_path / "REVIEW.md"
    planted.write_text(review_md().read_text(encoding="utf-8").replace(
        "## Lens: security\n", "## Lens: security\n\n<!-- reference: tdd sha256:0123456789ab -->\n"), encoding="utf-8")
    out = tmp_path / "p.md"
    code = cli.main(["prompt", "render", str(factory_home() / "prompts" / "lens-security.md"),
                     "--from-review-md", str(planted), "--lens", "security", *SETS, "--out", str(out)])
    assert code == 2 and "reference" in capsys.readouterr().err
    assert not out.exists()


def test_every_reference_lens_prompt_has_one_reference_placeholder_after_its_checklist():
    for lens in ("correctness", "maintainability", "spec"):
        template = factory_home() / "prompts" / f"lens-{lens}.md"
        text = template.read_text(encoding="utf-8").replace("\r\n", "\n")
        assert "reference_skill" in required_placeholders(template)
        assert text.count("{{reference_skill}}") == 1
        assert text.index("{{checklist}}") < text.index("{{reference_skill}}") < text.index("## Earlier review")
    assert "reference_skill" not in required_placeholders(factory_home() / "prompts" / "lens-security.md")


def test_render_inserts_the_section_as_it_is(home):
    install_tdd(home, "Keep {{task_id}} and \\1 and $1 as written.\n")
    section = reference_section(TDD, "codex", home)
    out = render(factory_home() / "prompts" / "lens-correctness.md", {
        "lens": "correctness", "checklist": "- c", "task_id": "T9", "round": "1", "diff_command": "git diff",
        "spec_path": "s.md", "prior_review_path": "none", "nit_cap": "5", "reference_skill": section.text,
    })
    assert "Keep {{task_id}} and \\1 and $1 as written." in out and "task T9" in out
