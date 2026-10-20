"""Tests for swfactory.setup: scaffolding a project for the factory, and finding conflicts with its own rules."""
from __future__ import annotations

import errno
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

from swfactory import cli
from swfactory.config import DEFAULT_HARD_STOPS, factory_home, load_project_config, load_role_table
from swfactory.reviewmd import check_panel_vendor_mix, load_review_md
from swfactory.setup import SetupAnswers, SetupError, SetupResult, find_conflicts, setup_project

BEGIN = "<!-- factory:begin -->"
END = "<!-- factory:end -->"

# What an owner might type: quotes, colons, '#', braces and backslashes, none of which may break the YAML.
AWKWARD_COMMAND = r"""python -c "print('a: b # c')" && pytest -k 'x or y' --opt={a,b} #tail \ {{not_a_placeholder}} {}"""


def make_answers(**overrides) -> SetupAnswers:
    values = dict(project="demo", verify_quick="pytest -q", verify_full="pytest", approvers=["owner"])
    values.update(overrides)
    return SetupAnswers(**values)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    folder = tmp_path / "repo"
    folder.mkdir()
    return folder


def put(path: Path, data: str | bytes) -> Path:
    """Write a file exactly as given (bytes untouched; text as UTF-8 with no newline translation)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return path


def relative(repo: Path, paths: list[Path]) -> list[str]:
    return [p.relative_to(repo).as_posix() for p in paths]


def tree(repo: Path) -> set[str]:
    return {p.relative_to(repo).as_posix() for p in repo.rglob("*")}


# --------------------------------------------------------------------------- what setup creates

def test_setup_creates_files(repo):
    res = setup_project(repo, make_answers())
    assert (repo / "factory.yaml").exists() and (repo / "REVIEW.md").exists()
    assert (repo / "factory/baseline/expected-failures.md").exists()
    assert load_project_config(repo / "factory.yaml").verify_full == "pytest"
    assert load_review_md(repo / "REVIEW.md").tracks["fix"] == ["correctness", "security"]
    assert (repo / "factory/work").is_dir()
    assert (repo / "factory/lessons.md").is_file()
    assert (repo / ".gitignore").read_text(encoding="utf-8") == "factory/dashboard/\n"
    assert isinstance(res, SetupResult)
    assert relative(repo, res.created) == [
        "factory.yaml",
        "REVIEW.md",
        "factory/work",
        "factory/baseline/expected-failures.md",
        "factory/lessons.md",
        ".gitignore",
        "CLAUDE.md",
        "AGENTS.md",
    ]
    assert res.updated == [] and res.conflicts == []


def test_setup_fills_the_answers_into_factory_yaml(repo):
    setup_project(repo, make_answers(
        project="Range Tool", approvers=["Cord", "Alex"], base_branch="develop", builds="ask", mode="auto",
        verify_quick="make quick", verify_full="make all",
    ))
    cfg = load_project_config(repo / "factory.yaml")
    assert (cfg.project, cfg.approvers, cfg.base_branch) == ("Range Tool", ["Cord", "Alex"], "develop")
    assert (cfg.builds, cfg.mode, cfg.verify_quick, cfg.verify_full) == ("ask", "auto", "make quick", "make all")
    assert (cfg.branching, cfg.work_branch) == ("per-item", None)


def test_factory_yaml_writes_every_default_explicitly(repo):
    setup_project(repo, make_answers())
    text = (repo / "factory.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert data["version"] == 1
    assert data["hard_stops"] == DEFAULT_HARD_STOPS
    assert data["roles"] == {} and data["github_mirror"] is False
    assert data["merge"] == {"size_cap": 400, "generated": []}
    assert data["review"] == {"max_rounds": 3}
    assert data["work_branch"] is None and data["builds"] == "allow" and data["mode"] == "review"
    assert "#" in text  # every key is explained by a comment
    cfg = load_project_config(repo / "factory.yaml")
    assert (cfg.size_cap, cfg.max_rounds, cfg.hard_stops) == (400, 3, DEFAULT_HARD_STOPS)


def test_single_branching_writes_the_work_branch_and_per_item_writes_null(repo, tmp_path):
    setup_project(repo, make_answers(branching="single", work_branch="factory-work"))
    cfg = load_project_config(repo / "factory.yaml")
    assert (cfg.branching, cfg.work_branch) == ("single", "factory-work")

    other = tmp_path / "other"
    other.mkdir()
    setup_project(other, make_answers(branching="per-item", work_branch="ignored"))
    assert load_project_config(other / "factory.yaml").work_branch is None


def test_awkward_values_survive_the_round_trip(repo):
    setup_project(repo, make_answers(
        project='My "Project": v2 #1', verify_quick=AWKWARD_COMMAND, verify_full=AWKWARD_COMMAND + " --full",
        approvers=["Ann: the \"owner\"", "b#c"], base_branch="release/{1}",
    ))
    cfg = load_project_config(repo / "factory.yaml")
    assert cfg.project == 'My "Project": v2 #1'
    assert cfg.verify_quick == AWKWARD_COMMAND and cfg.verify_full == AWKWARD_COMMAND + " --full"
    assert cfg.approvers == ["Ann: the \"owner\"", "b#c"] and cfg.base_branch == "release/{1}"
    section = (repo / "CLAUDE.md").read_text(encoding="utf-8")
    assert AWKWARD_COMMAND in section and AWKWARD_COMMAND + " --full" in section


def test_values_with_backticks_get_a_safe_code_span(repo):
    setup_project(repo, make_answers(verify_quick="echo `date`", verify_full="``x``"))
    text = (repo / "CLAUDE.md").read_text(encoding="utf-8")
    assert "`` echo `date` ``" in text  # a longer fence than any backtick run inside, padded where it ends in one
    assert "``` ``x`` ```" in text


def test_invalid_answers_raise_and_write_nothing(repo):
    for bad in (
        {"branching": "single"},  # work_branch is required
        {"branching": "single", "work_branch": "main"},  # and must not be the base branch
        {"approvers": []},
        {"mode": "turbo"},
        {"verify_full": "  "},
        {"project": ""},
    ):
        with pytest.raises(SetupError, match="factory.yaml"):
            setup_project(repo, make_answers(**bad))
        assert tree(repo) == set(), bad


def test_setup_error_lists_every_problem(repo):
    with pytest.raises(SetupError) as raised:
        setup_project(repo, make_answers(mode="turbo", branching="single"))
    assert "mode" in str(raised.value) and "work_branch" in str(raised.value)


def test_a_missing_template_raises_before_anything_is_written(repo, tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "templates/project").mkdir(parents=True)
    for template in (factory_home() / "templates/project").iterdir():
        if template.name != "lessons.md":
            (home / "templates/project" / template.name).write_bytes(template.read_bytes())
    monkeypatch.setenv("FACTORY_HOME", str(home))
    with pytest.raises(SetupError, match=r"cannot read the template .*lessons\.md"):
        setup_project(repo, make_answers())
    assert tree(repo) == set()


def test_setup_needs_an_existing_folder(tmp_path):
    with pytest.raises(SetupError, match="not a folder"):
        setup_project(tmp_path / "missing", make_answers())


def test_setup_never_runs_git_or_commits(repo, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError(f"setup started a process: {args!r}")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    setup_project(repo, make_answers())
    assert not (repo / ".git").exists()


# --------------------------------------------------------------------------- the templates

def test_review_md_template_loads_with_the_default_lenses(repo):
    setup_project(repo, make_answers())
    cfg = load_review_md(repo / "REVIEW.md")
    assert list(cfg.lenses) == ["correctness", "security", "maintainability", "spec"]
    assert cfg.tracks == {
        "fix": ["correctness", "security"],
        "feature": ["correctness", "security", "maintainability"],
        "program": ["correctness", "security", "maintainability", "spec"],
    }
    assert (cfg.nit_cap, cfg.ignore, cfg.always_flag) == (5, [], [])
    assert check_panel_vendor_mix(cfg, load_role_table(factory_home() / "config/roles.yaml")) == []


def test_review_md_template_lens_checklists_cover_the_topics(repo):
    setup_project(repo, make_answers())
    lenses = {name: text.lower() for name, text in load_review_md(repo / "REVIEW.md").lenses.items()}
    topics = {
        "correctness": ("logic", "edge cases", "error handling", "tests", "concurrency"),
        "security": ("input validation", "authentication", "authorization", "secrets", "injection",
                     "deserialization", "dependencies"),
        "maintainability": ("duplication", "module boundaries", "naming", "dead code", "documentation"),
        "spec": ("requirement", "acceptance criteri", "outside", "plan task"),
    }
    for lens, words in topics.items():
        for word in words:
            assert word in lenses[lens], (lens, word)
    assert "never block" in lenses["maintainability"]


def test_review_md_template_says_the_review_format_lives_in_the_prompts(repo):
    setup_project(repo, make_answers())
    text = (repo / "REVIEW.md").read_text(encoding="utf-8")
    assert "prompts" in text and "not here" in text


def test_expected_failures_template_states_the_exit_code_contract(repo):
    setup_project(repo, make_answers())
    text = (repo / "factory/baseline/expected-failures.md").read_text(encoding="utf-8")
    assert "exits 0" in text and "exit code" in text and "verify" in text


def test_no_template_names_a_model():
    table = load_role_table(factory_home() / "config/roles.yaml")
    models = {b.model for spec in table.roles.values() for b in (*spec.bindings.values(), *spec.fallback.values())}
    assert models
    for template in (factory_home() / "templates/project").iterdir():
        text = template.read_text(encoding="utf-8").lower()
        for model in models:
            assert model.lower() not in text, (template.name, model)
        for vendor_word in ("claude-", "gpt-", "opus", "sonnet", "fable"):
            assert vendor_word not in text, (template.name, vendor_word)


def test_files_are_utf8_with_lf_newlines(repo):
    setup_project(repo, make_answers())
    for path in (p for p in repo.rglob("*") if p.is_file()):
        data = path.read_bytes()
        assert b"\r" not in data and not data.startswith(b"\xef\xbb\xbf"), path
        data.decode("utf-8")


# --------------------------------------------------------------------------- re-running setup

def test_setup_refuses_second_run(repo):
    setup_project(repo, make_answers())
    before = {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    with pytest.raises(SetupError, match="already set up"):
        setup_project(repo, make_answers(project="other"))
    assert {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()} == before


def test_setup_refuses_when_factory_yaml_exists_whatever_it_holds(repo):
    put(repo / "factory.yaml", "not even yaml: [")
    with pytest.raises(SetupError, match="already set up"):
        setup_project(repo, make_answers())
    assert tree(repo) == {"factory.yaml"}


@pytest.mark.parametrize("name", ["REVIEW.md", "factory/lessons.md", "factory/baseline/expected-failures.md"])
def test_setup_will_not_overwrite_an_existing_file(repo, name):
    put(repo / name, "the project's own text\n")
    with pytest.raises(SetupError, match=r"already exists"):
        setup_project(repo, make_answers())
    assert (repo / name).read_text(encoding="utf-8") == "the project's own text\n"
    assert not (repo / "factory.yaml").exists() and not (repo / "CLAUDE.md").exists()


# --------------------------------------------------------------------------- a write that fails

def snapshot(repo: Path) -> dict[str, bytes | None]:
    """Every file (its bytes) and folder (None) under ``repo``, temporary files included."""
    return {p.relative_to(repo).as_posix(): (p.read_bytes() if p.is_file() else None) for p in repo.rglob("*")}


def project_with_files_to_edit(repo: Path) -> None:
    """A .gitignore, CLAUDE.md and AGENTS.md that setup edits (in that order), each in a different state."""
    put(repo / ".gitignore", b"*.pyc\r\n")
    put(repo / "CLAUDE.md", "# C\nuser text\n")
    put(repo / "AGENTS.md", b"# A\r\nrules\r\n")


def failing_replace(monkeypatch, name: str, exc: BaseException | None = None):
    """Make ``os.replace`` fail for the file called ``name``; returns the bytes that file held at that moment."""
    real = os.replace
    seen: list[bytes] = []

    def replace(src, dst, *args, **kwargs):
        if Path(dst).name == name:
            seen.append(Path(dst).read_bytes())
            raise exc or PermissionError(errno.EACCES, "Permission denied", str(dst))
        return real(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", replace)
    return seen


@pytest.mark.parametrize("name", ["factory", "factory/baseline", "factory/work"])
def test_a_plain_file_where_a_factory_folder_goes_is_refused_before_any_write(repo, name):
    put(repo / "AGENTS.md", "# A\n")
    put(repo / name, "not a folder")
    before = snapshot(repo)
    with pytest.raises(SetupError, match=r"exists and is not a folder"):
        setup_project(repo, make_answers())
    assert snapshot(repo) == before


def test_a_read_only_file_is_refused_and_a_retry_after_making_it_writable_succeeds(repo):
    project_with_files_to_edit(repo)
    agents = repo / "AGENTS.md"
    os.chmod(agents, stat.S_IREAD)
    try:
        if os.access(agents, os.W_OK):
            pytest.skip("this account can write read-only files")
        before = snapshot(repo)
        with pytest.raises(SetupError, match=r"AGENTS\.md is read-only"):
            setup_project(repo, make_answers())
        assert snapshot(repo) == before  # nothing left behind, every original byte-identical
    finally:
        os.chmod(agents, stat.S_IREAD | stat.S_IWRITE)
    res = setup_project(repo, make_answers())
    assert {"AGENTS.md", "CLAUDE.md", ".gitignore"} <= set(relative(repo, res.updated))
    assert (repo / "AGENTS.md").read_text(encoding="utf-8").count(BEGIN) == 1 and (repo / "factory.yaml").is_file()


def test_a_failed_edit_undoes_everything_and_a_retry_succeeds(repo, monkeypatch):
    project_with_files_to_edit(repo)
    before = snapshot(repo)
    seen = failing_replace(monkeypatch, "AGENTS.md")  # the last edit, after every new file and the other edits
    with pytest.raises(SetupError) as raised:
        setup_project(repo, make_answers())
    message = str(raised.value)
    assert "AGENTS.md" in message and "Permission denied" in message and "undid" in message
    assert isinstance(raised.value.__cause__, PermissionError)
    assert seen == [before["AGENTS.md"]]  # the file was untouched up to the swap: it cannot be left truncated
    assert snapshot(repo) == before  # no new file or folder, no temporary file, every original byte-identical
    monkeypatch.undo()
    res = setup_project(repo, make_answers())
    assert (repo / "factory.yaml").is_file() and "AGENTS.md" in relative(repo, res.updated)


def test_a_failed_swap_of_a_read_only_file_leaves_no_temporary_file(repo, monkeypatch):
    # The temporary copy takes the original's read-only mode, and Windows will not delete such a file as it is.
    project_with_files_to_edit(repo)
    agents = repo / "AGENTS.md"
    os.chmod(agents, stat.S_IREAD)
    try:
        before = snapshot(repo)
        monkeypatch.setattr(os, "access", lambda *args, **kwargs: True)  # skip the pre-check: the swap itself fails
        failing_replace(monkeypatch, "AGENTS.md")
        with pytest.raises(SetupError, match=r"AGENTS\.md"):
            setup_project(repo, make_answers())
        monkeypatch.undo()
        assert snapshot(repo) == before
    finally:
        os.chmod(agents, stat.S_IREAD | stat.S_IWRITE)


def test_a_failed_folder_creation_removes_what_was_made_before_it(repo, monkeypatch):
    project_with_files_to_edit(repo)
    before = snapshot(repo)
    real = Path.mkdir

    def mkdir(self, *args, **kwargs):
        if self.name == "work":
            raise OSError(errno.ENOSPC, "No space left on device", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    with pytest.raises(SetupError, match=r"cannot write .*work: No space left on device"):
        setup_project(repo, make_answers())
    assert snapshot(repo) == before


def test_a_failed_last_write_leaves_no_partial_factory_yaml_and_restores_the_edits(repo, monkeypatch):
    project_with_files_to_edit(repo)
    before = snapshot(repo)
    real = Path.write_bytes

    def write_bytes(self, data):
        if self.name == "factory.yaml":
            real(self, data[:10])  # a partial file, as a full disk would leave
            raise OSError(errno.ENOSPC, "No space left on device", str(self))
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", write_bytes)
    with pytest.raises(SetupError, match=r"cannot write .*factory\.yaml: No space left on device"):
        setup_project(repo, make_answers())
    monkeypatch.undo()
    assert snapshot(repo) == before


def test_an_unexpected_error_also_undoes_everything_and_is_not_disguised(repo, monkeypatch):
    project_with_files_to_edit(repo)
    before = snapshot(repo)
    failing_replace(monkeypatch, "AGENTS.md", RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        setup_project(repo, make_answers())
    assert snapshot(repo) == before


def test_what_setup_could_not_undo_is_named(repo, monkeypatch):
    project_with_files_to_edit(repo)
    before = snapshot(repo)
    real = os.replace
    swaps_of_claude_md: list[int] = []

    def replace(src, dst, *args, **kwargs):
        name = Path(dst).name
        if name == "AGENTS.md" or (name == "CLAUDE.md" and swaps_of_claude_md):  # the edit fails; so does CLAUDE.md's undo
            raise PermissionError(errno.EACCES, "Permission denied", str(dst))
        if name == "CLAUDE.md":
            swaps_of_claude_md.append(1)
        return real(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", replace)
    with pytest.raises(SetupError, match=r"could not undo .*CLAUDE\.md"):
        setup_project(repo, make_answers())
    after = snapshot(repo)
    assert not any("factory-tmp" in name for name in after)  # the failed undo left no temporary file
    assert BEGIN.encode() in after["CLAUDE.md"]  # the one file it could not restore is still edited
    for name in before:
        if name != "CLAUDE.md":  # everything else was put back or removed
            assert after[name] == before[name], name
    assert set(after) == set(before)


def test_factory_yaml_is_written_last(repo, monkeypatch):
    put(repo / "AGENTS.md", "# A\n")
    events: list[str] = []
    real_write, real_replace = Path.write_bytes, os.replace

    def write_bytes(self, data):
        events.append(self.name)
        return real_write(self, data)

    def replace(src, dst, *args, **kwargs):
        events.append(Path(dst).name)
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(Path, "write_bytes", write_bytes)
    monkeypatch.setattr(os, "replace", replace)
    setup_project(repo, make_answers())
    assert events[-1] == "factory.yaml"
    assert {"REVIEW.md", "lessons.md", "expected-failures.md", ".gitignore", "AGENTS.md"} <= set(events)


def test_a_symlinked_claude_md_stays_a_link(repo):
    put(repo / "AGENTS.md", "# A\n")
    try:
        os.symlink("AGENTS.md", repo / "CLAUDE.md")
    except (OSError, NotImplementedError):
        pytest.skip("cannot create symbolic links here")
    setup_project(repo, make_answers())
    text = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert (repo / "CLAUDE.md").is_symlink() and text.count(BEGIN) == 1
    assert (repo / "CLAUDE.md").read_text(encoding="utf-8") == text


@pytest.mark.skipif(os.name == "nt", reason="permission bits are not the same on Windows")
def test_an_edited_file_keeps_its_permissions(repo):
    put(repo / "AGENTS.md", "# A\n")
    os.chmod(repo / "AGENTS.md", 0o640)
    setup_project(repo, make_answers())
    assert stat.S_IMODE((repo / "AGENTS.md").stat().st_mode) == 0o640


# --------------------------------------------------------------------------- the CLAUDE.md / AGENTS.md section

def test_section_markers_idempotent(repo):
    put(repo / "CLAUDE.md", f"# Mine\n\nuser text\n\n{BEGIN}\nold factory text\n{END}\n\nmore user text\n")
    res = setup_project(repo, make_answers())
    text = (repo / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.count(BEGIN) == 1 and text.count(END) == 1
    assert "user text" in text and "more user text" in text and "old factory text" not in text
    assert text.startswith("# Mine\n\nuser text\n\n" + BEGIN + "\n") and text.endswith(END + "\n\nmore user text\n")
    assert relative(repo, res.updated) == ["CLAUDE.md"]


def test_section_replaced_in_place_keeps_every_byte_outside_the_markers(repo):
    before = b"\xef\xbb\xbf# Mine\r\n\r\nuser \xe2\x80\x94 text\r\n\r\n"
    after = b"\r\n\r\nmore user text, no final newline"
    put(repo / "CLAUDE.md", before + BEGIN.encode() + b"\r\nold\r\n" + END.encode() + after)
    setup_project(repo, make_answers())
    data = (repo / "CLAUDE.md").read_bytes()
    assert data.startswith(before + BEGIN.encode() + b"\r\n") and data.endswith(END.encode() + after)
    assert data.count(b"\n") == data.count(b"\r\n")  # the section took the file's CRLF style


def test_section_appended_to_a_crlf_file_keeps_crlf(repo):
    original = b"# Project rules\r\n\r\nBe kind.\r\n"
    put(repo / "AGENTS.md", original)
    res = setup_project(repo, make_answers())
    data = (repo / "AGENTS.md").read_bytes()
    assert data.startswith(original)
    assert data.count(b"\n") == data.count(b"\r\n") and data.endswith(END.encode() + b"\r\n")
    assert relative(repo, res.updated) == ["AGENTS.md"] and "CLAUDE.md" not in relative(repo, res.created)
    assert not (repo / "CLAUDE.md").exists()


def test_section_appended_to_a_file_without_a_final_newline(repo):
    put(repo / "CLAUDE.md", "# Rules\nBe kind.")
    setup_project(repo, make_answers())
    text = (repo / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.startswith("# Rules\nBe kind.\n\n" + BEGIN + "\n") and text.endswith(END + "\n")


def test_section_goes_into_both_files_when_both_exist(repo):
    put(repo / "CLAUDE.md", "# C\n")
    put(repo / "AGENTS.md", "# A\n")
    res = setup_project(repo, make_answers())
    for name in ("CLAUDE.md", "AGENTS.md"):
        assert (repo / name).read_text(encoding="utf-8").count(BEGIN) == 1
    assert sorted(relative(repo, res.updated)) == ["AGENTS.md", "CLAUDE.md"]
    assert not any(p.name in ("CLAUDE.md", "AGENTS.md") for p in res.created)


def test_creates_claude_md_and_agents_md_when_neither_exists(repo):
    # Claude Code reads CLAUDE.md and Codex reads AGENTS.md: a session on either host must see the section.
    res = setup_project(repo, make_answers())
    claude = (repo / "CLAUDE.md").read_text(encoding="utf-8")
    assert claude.startswith(BEGIN + "\n") and claude.endswith(END + "\n") and claude.count(BEGIN) == 1
    assert (repo / "AGENTS.md").read_text(encoding="utf-8") == claude
    assert {"CLAUDE.md", "AGENTS.md"} <= set(relative(repo, res.created)) and res.updated == []


def test_only_the_existing_file_gets_the_section_when_one_exists(repo):
    put(repo / "CLAUDE.md", "# C\n")
    res = setup_project(repo, make_answers())
    assert (repo / "CLAUDE.md").read_text(encoding="utf-8").count(BEGIN) == 1
    assert not (repo / "AGENTS.md").exists() and relative(repo, res.updated) == ["CLAUDE.md"]


def test_section_says_where_work_lives_how_to_resume_and_how_to_verify(repo):
    setup_project(repo, make_answers(verify_quick="make quick", verify_full="make all"))
    text = (repo / "CLAUDE.md").read_text(encoding="utf-8")
    for expected in ("factory/work/", "factory work status", "progress.md", "`make quick`", "`make all`",
                     "factory.yaml", "REVIEW.md", "expected-failures.md", "never edit product code"):
        assert expected in text, expected


@pytest.mark.parametrize(
    "broken",
    [
        f"{BEGIN}\nno end\n",
        f"no begin\n{END}\n",
        f"{END}\nbackwards\n{BEGIN}\n",
        f"{BEGIN}\na\n{END}\n{BEGIN}\nb\n{END}\n",
    ],
)
def test_broken_markers_refuse_and_change_nothing(repo, broken):
    put(repo / "CLAUDE.md", broken)
    with pytest.raises(SetupError, match=r"CLAUDE\.md.*factory:"):
        setup_project(repo, make_answers())
    assert tree(repo) == {"CLAUDE.md"} and (repo / "CLAUDE.md").read_text(encoding="utf-8") == broken


def test_a_section_already_current_is_not_reported_as_updated(repo, tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    setup_project(scratch, make_answers())
    put(repo / "CLAUDE.md", (scratch / "CLAUDE.md").read_bytes())
    res = setup_project(repo, make_answers())
    assert "CLAUDE.md" not in relative(repo, res.updated + res.created)


# --------------------------------------------------------------------------- .gitignore

@pytest.mark.parametrize("line", ["factory/dashboard/", "/factory/dashboard/", "factory/dashboard", "  factory/dashboard/  "])
def test_gitignore_entry_once(repo, line):
    put(repo / ".gitignore", f"*.pyc\n{line}\nbuild/\n")
    res = setup_project(repo, make_answers())
    assert (repo / ".gitignore").read_text(encoding="utf-8") == f"*.pyc\n{line}\nbuild/\n"
    assert ".gitignore" not in relative(repo, res.updated + res.created)


def test_gitignore_entry_is_appended_once_and_keeps_the_rest(repo):
    put(repo / ".gitignore", b"*.pyc\r\nbuild/")
    res = setup_project(repo, make_answers())
    assert (repo / ".gitignore").read_bytes() == b"*.pyc\r\nbuild/\r\nfactory/dashboard/\r\n"
    assert ".gitignore" in relative(repo, res.updated)


def test_gitignore_comment_naming_the_folder_does_not_count(repo):
    put(repo / ".gitignore", "# factory/dashboard/ goes below\n")
    setup_project(repo, make_answers())
    lines = (repo / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert lines == ["# factory/dashboard/ goes below", "factory/dashboard/"]


# --------------------------------------------------------------------------- conflicts

def test_conflicts_detected_from_agents_md(repo):
    put(repo / "AGENTS.md", "# Rules\n\n- Do not create git branches unless explicitly asked.\n- Be kind.\n")
    res = setup_project(repo, make_answers())
    assert res.conflicts == ["AGENTS.md:3: - Do not create git branches unless explicitly asked."]


def test_find_conflicts_matches_every_pattern_case_insensitively_in_file_order(repo):
    put(repo / "AGENTS.md", "Do not build the solution.\nplain\nDO NOT CREATE BRANCHES\n")
    put(repo / "CLAUDE.md", "never push   \r\nCommit directly to main when asked\r\n  commit to main\r\nNEVER PUSH\r\n")
    assert find_conflicts(repo) == [
        "AGENTS.md:1: Do not build the solution.",
        "AGENTS.md:3: DO NOT CREATE BRANCHES",
        "CLAUDE.md:1: never push",
        "CLAUDE.md:2: Commit directly to main when asked",
        "CLAUDE.md:3:   commit to main",
        "CLAUDE.md:4: NEVER PUSH",
    ]


def test_find_conflicts_ignores_other_files_and_clean_rules(repo):
    put(repo / "docs/AGENTS.md", "Do not build anything.\n")
    put(repo / "README.md", "never push\n")
    put(repo / "CLAUDE.md", "Build often. Create branches freely. Always push after review.\n")
    assert find_conflicts(repo) == []


def test_find_conflicts_without_either_file_is_empty(repo):
    assert find_conflicts(repo) == []


def test_find_conflicts_reads_a_file_with_a_bom_and_stray_bytes(repo):
    put(repo / "AGENTS.md", b"\xef\xbb\xbfDo not build\n\xff\xfe odd bytes\nnever push\n")
    assert find_conflicts(repo) == ["AGENTS.md:1: Do not build", "AGENTS.md:3: never push"]


def test_the_factory_section_does_not_conflict_with_itself(repo):
    setup_project(repo, make_answers())
    assert find_conflicts(repo) == []


def test_conflict_line_numbers_refer_to_the_files_as_setup_leaves_them(repo):
    put(repo / "CLAUDE.md", f"{BEGIN}\nnever push\n{END}\nDo not build.\n")  # the stale section is replaced
    res = setup_project(repo, make_answers())
    lines = (repo / "CLAUDE.md").read_text(encoding="utf-8").splitlines()
    assert res.conflicts == [f"CLAUDE.md:{lines.index('Do not build.') + 1}: Do not build."]
    assert lines.index("Do not build.") > 3  # the new section is longer than the old one, so the line moved


# --------------------------------------------------------------------------- the CLI

def run_cli(monkeypatch, repo: Path, *argv: str) -> int:
    monkeypatch.chdir(repo)
    return cli.main(["setup", *argv])


BASE_ARGV = ("--project", "demo", "--verify-quick", "pytest -q", "--verify-full", "pytest", "--approver", "owner")


def test_cli_setup_prints_the_result_as_json(repo, monkeypatch, capsys):
    put(repo / "AGENTS.md", "- Do not build the solution.\n")
    assert run_cli(monkeypatch, repo, *BASE_ARGV) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["created"] == ["factory.yaml", "REVIEW.md", "factory/work", "factory/baseline/expected-failures.md",
                              "factory/lessons.md", ".gitignore"]
    assert out["updated"] == ["AGENTS.md"]
    assert out["conflicts"] == ["AGENTS.md:1: - Do not build the solution."]
    assert load_project_config(repo / "factory.yaml").approvers == ["owner"]


def test_cli_setup_takes_every_option(repo, monkeypatch, capsys):
    argv = ["--project", "Range Tool", "--verify-quick", "q", "--verify-full", "f",
            "--approver", "Cord", "--approver", "Alex", "--base", "develop", "--branching", "single",
            "--work-branch", "factory-work", "--builds", "ask", "--mode", "auto"]
    assert run_cli(monkeypatch, repo, *argv) == 0
    capsys.readouterr()
    cfg = load_project_config(repo / "factory.yaml")
    assert (cfg.project, cfg.approvers, cfg.base_branch, cfg.branching) == ("Range Tool", ["Cord", "Alex"], "develop", "single")
    assert (cfg.work_branch, cfg.builds, cfg.mode) == ("factory-work", "ask", "auto")


def test_cli_setup_second_run_exits_2_and_says_why(repo, monkeypatch, capsys):
    assert run_cli(monkeypatch, repo, *BASE_ARGV) == 0
    capsys.readouterr()
    assert run_cli(monkeypatch, repo, *BASE_ARGV) == 2
    captured = capsys.readouterr()
    assert "already set up" in captured.err and captured.out == ""


def test_cli_setup_invalid_answers_exit_2_and_write_nothing(repo, monkeypatch, capsys):
    argv = ["--project", "demo", "--verify-quick", "q", "--verify-full", "f", "--approver", "o", "--branching", "single"]
    assert run_cli(monkeypatch, repo, *argv) == 2
    assert "work_branch" in capsys.readouterr().err
    assert tree(repo) == set()


def test_cli_setup_requires_the_questions_it_asks(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    with pytest.raises(SystemExit) as raised:
        cli.main(["setup", "--project", "demo"])
    assert raised.value.code == 2
    assert "--verify-quick" in capsys.readouterr().err


def test_cli_setup_output_is_ascii_even_for_non_ascii_rules(repo, monkeypatch, capsys):
    put(repo / "CLAUDE.md", "Never push — ever.\n")
    assert run_cli(monkeypatch, repo, *BASE_ARGV) == 0
    out = capsys.readouterr().out
    out.encode("ascii")
    assert json.loads(out)["conflicts"] == ["CLAUDE.md:1: Never push — ever."]
