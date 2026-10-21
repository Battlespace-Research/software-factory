"""Tests for swfactory.gitutil, swfactory.workitems, swfactory.trailers and the ``sw-factory work`` commands.

Every git repository here is created under pytest's ``tmp_path`` with its own identity and with the
machine's git configuration hidden (see ``isolated_git_config`` in conftest.py).
"""
from __future__ import annotations

import dataclasses
import json
import re
import stat
from pathlib import Path

import pytest

from swfactory import cli
from swfactory.gitutil import GitError, run_git
from swfactory.trailers import format_trailers
from swfactory.tracks import TRACK_PREFIX
from swfactory.workitems import (
    WorkItem,
    create_work_item,
    find_work_item,
    ids_in_use,
    next_id,
    read_status,
    with_letter_suffix,
    write_status,
)

# --------------------------------------------------------------------------- helpers

STATUS_LINE = re.compile(r"Status: (?P<stage>.+) · (?P<state>.+) · next: (?P<next>.+) · updated \S+")
UTC_STAMP = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"


def commit_file(repo: Path, rel: str, text: str = "x\n", message: str = "Add a file") -> None:
    """Write ``rel`` under ``repo`` and commit it on the current branch."""
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    run_git(repo, "add", "--", rel)
    run_git(repo, "commit", "-q", "-m", message)


def commit_work_folder(repo: Path, name: str) -> None:
    commit_file(repo, f"sw-factory/work/{name}/progress.md", "Status: plan · open · next: x · updated 2026-01-01\n")


def branch_names(repo: Path) -> list[str]:
    return run_git(repo, "branch", "--format=%(refname:short)").splitlines()


def write_progress(folder: Path, status: str = "Status: plan · open · next: write it · updated 2026-10-20", log: str = "- 2026-10-20 — Opened") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "progress.md"
    path.write_text(f"{status}\n\n## Log\n\n{log}\n", encoding="utf-8", newline="\n")
    return path


def write_sw_factory_yaml(repo: Path) -> None:
    (repo / "sw-factory.yaml").write_text(
        "project: demo\napprovers: [owner]\nverify:\n  quick: pytest -q\n  full: pytest\n", encoding="utf-8", newline="\n"
    )


# --------------------------------------------------------------------------- run_git

def test_run_git_returns_stdout_without_the_trailing_newline(git_repo):
    assert run_git(git_repo, "log", "-1", "--format=%s") == "Initial commit"


def test_run_git_failure_carries_command_and_stderr(git_repo):
    with pytest.raises(GitError) as caught:
        run_git(git_repo, "switch", "no-such-branch")
    assert caught.value.command[:2] == ["git", "switch"] and "no-such-branch" in caught.value.command
    assert "no-such-branch" in caught.value.stderr
    assert "no-such-branch" in str(caught.value)


def test_run_git_in_a_missing_folder_is_a_git_error(tmp_path):
    with pytest.raises(GitError):
        run_git(tmp_path / "missing", "status")


def test_git_error_is_a_runtime_error():
    assert issubclass(GitError, RuntimeError)


# --------------------------------------------------------------------------- ids

def test_next_id_empty_repo_is_1(git_repo_no_remote_master):
    assert next_id(git_repo_no_remote_master, "fix", "master") == "FIX-1"


def test_next_id_in_a_repo_without_commits_is_1(tmp_path, isolated_git_config):
    run_git(tmp_path, "init", "-q", "-b", "main")
    assert ids_in_use(tmp_path, "main") == set()
    assert next_id(tmp_path, "program", "main") == "PRG-1"


def test_next_id_sees_remote_branch(git_repo):
    run_git(git_repo, "switch", "-q", "-c", "sw-factory/FEA-4-x")
    run_git(git_repo, "commit", "-q", "--allow-empty", "-m", "work")
    run_git(git_repo, "push", "-q", "origin", "sw-factory/FEA-4-x")
    run_git(git_repo, "switch", "-q", "main")
    run_git(git_repo, "branch", "-q", "-D", "sw-factory/FEA-4-x")
    assert "sw-factory/FEA-4-x" not in branch_names(git_repo)
    assert next_id(git_repo, "feature", "main") == "FEA-5"


def test_next_id_sees_local_branch(git_repo):
    run_git(git_repo, "branch", "sw-factory/FIX-6-y")
    assert next_id(git_repo, "fix", "main") == "FIX-7"


def test_next_id_sees_base_tree(git_repo):
    commit_work_folder(git_repo, "FIX-9-y")
    assert next_id(git_repo, "fix", "main") == "FIX-10"


def test_next_id_sees_the_remote_copy_of_the_base_branch(git_repo):
    commit_work_folder(git_repo, "FIX-9-y")
    run_git(git_repo, "push", "-q", "origin", "main")
    run_git(git_repo, "reset", "-q", "--hard", "HEAD~1")  # local main has not pulled it
    assert ids_in_use(git_repo, "main") == {"FIX-9"}
    assert next_id(git_repo, "fix", "main") == "FIX-10"


def test_next_id_sees_work_folders_on_the_checked_out_branch(git_repo):
    """With single branching, items live on the work branch, not on base and not on a sw-factory/ branch."""
    run_git(git_repo, "switch", "-q", "-c", "factory-work")
    commit_work_folder(git_repo, "FIX-3-z")
    assert next_id(git_repo, "fix", "main") == "FIX-4"


def test_next_id_numbers_each_track_separately(git_repo):
    run_git(git_repo, "branch", "sw-factory/FIX-1-a")
    run_git(git_repo, "branch", "sw-factory/FIX-2-b")
    assert next_id(git_repo, "fix", "main") == "FIX-3"
    assert next_id(git_repo, "feature", "main") == "FEA-1"
    assert next_id(git_repo, "program", "main") == "PRG-1"


def test_next_id_counts_a_lettered_id_by_its_number(git_repo):
    commit_work_folder(git_repo, "FEA-12b-x")
    assert ids_in_use(git_repo, "main") == {"FEA-12b"}
    assert next_id(git_repo, "feature", "main") == "FEA-13"


def test_ids_in_use_ignores_unrelated_branches(git_repo):
    for name in ("feature/FIX-99-x", "fix/FEA-50", "sw-factory/notes", "release/PRG-7", "other/sw-factory/FEA-77-x"):
        run_git(git_repo, "branch", name)
    run_git(git_repo, "push", "-q", "origin", "other/sw-factory/FEA-77-x")
    run_git(git_repo, "branch", "sw-factory/FIX-5-real")
    assert ids_in_use(git_repo, "main") == {"FIX-5"}


def test_ids_in_use_ignores_non_work_entries_in_the_work_folder(git_repo):
    commit_file(git_repo, "sw-factory/work/README.md")
    commit_file(git_repo, "sw-factory/work/draft/notes.md")
    commit_file(git_repo, "sw-factory/work/ABC-3-x/f.md")  # not a known track prefix
    assert ids_in_use(git_repo, "main") == set()


def test_ids_in_use_with_an_unknown_base_branch_still_reads_the_factory_branches(git_repo):
    run_git(git_repo, "branch", "sw-factory/FIX-2-a")
    assert ids_in_use(git_repo, "develop") == {"FIX-2"}


def test_next_id_unknown_track_is_a_value_error(git_repo):
    with pytest.raises(ValueError, match="epic"):
        next_id(git_repo, "epic", "main")


def test_letter_suffix():
    assert with_letter_suffix("FEA-12", {"FEA-12", "FEA-12b"}) == "FEA-12c"


def test_letter_suffix_starts_at_b():
    assert with_letter_suffix("FEA-12", {"FEA-12"}) == "FEA-12b"


def test_letter_suffix_of_a_lettered_id_keeps_the_number():
    assert with_letter_suffix("FEA-12b", {"FEA-12", "FEA-12b"}) == "FEA-12c"


def test_letter_suffix_runs_out_after_z():
    taken = {"FEA-12"} | {f"FEA-12{chr(c)}" for c in range(ord("b"), ord("z") + 1)}
    with pytest.raises(ValueError, match="FEA-12"):
        with_letter_suffix("FEA-12", taken)


def test_letter_suffix_rejects_a_bad_id():
    with pytest.raises(ValueError, match="nonsense"):
        with_letter_suffix("nonsense", set())


def test_track_prefix_is_the_one_from_tracks():
    assert TRACK_PREFIX == {"program": "PRG", "feature": "FEA", "fix": "FIX"}


# --------------------------------------------------------------------------- create_work_item

def test_create_fix_item_files(git_repo, cfg):
    repo = git_repo
    w = create_work_item(repo, cfg, "fix", "licence-crash", "Crash when licence missing")
    assert isinstance(w, WorkItem)
    assert w.id == "FIX-1" and w.track == "fix" and w.slug == "licence-crash" and w.title == "Crash when licence missing"
    assert w.branch == "sw-factory/FIX-1-licence-crash" and (w.path / "ticket.md").exists() and (w.path / "reviews").is_dir()
    assert w.path == repo / "sw-factory" / "work" / "FIX-1-licence-crash"
    assert "Factory-Work: FIX-1" in run_git(repo, "log", "-1", "--format=%B")
    assert {p.name for p in w.path.iterdir()} == {"ticket.md", "progress.md", "decisions.md", "reviews", "approvals"}
    assert run_git(repo, "branch", "--show-current") == "sw-factory/FIX-1-licence-crash"


def test_create_feature_item_files(git_repo, cfg):
    w = create_work_item(git_repo, cfg, "feature", "export", "Export to CSV")
    assert w.id == "FEA-1" and w.branch == "sw-factory/FEA-1-export"
    assert {p.name for p in w.path.iterdir()} == {
        "intent.md", "spec.md", "plan.md", "progress.md", "decisions.md", "retro.md", "reviews", "approvals",
    }
    assert not (w.path / "ticket.md").exists()


def test_create_program_item_files(git_repo, cfg):
    w = create_work_item(git_repo, cfg, "program", "ngc2", "Next-gen command and control")
    assert w.id == "PRG-1"
    assert {p.name for p in w.path.iterdir()} == {
        "intent.md", "spec.md", "plan.md", "progress.md", "decisions.md", "retro.md",
        "reviews", "approvals", "research", "demo",
    }


def test_created_empty_folders_survive_a_checkout(git_repo, cfg):
    """git does not track empty folders, so each carries a .gitkeep; a fresh clone then has all of them."""
    w = create_work_item(git_repo, cfg, "program", "p", "A program")
    tracked = set(run_git(git_repo, "ls-tree", "-r", "--name-only", "HEAD").splitlines())
    for folder in ("reviews", "approvals", "research", "demo"):
        assert f"sw-factory/work/{w.path.name}/{folder}/.gitkeep" in tracked
    assert (w.path / "reviews").is_dir() and not any(
        p for p in (w.path / "reviews").iterdir() if p.name != ".gitkeep"
    )


def test_commit_message_is_subject_blank_line_trailer(git_repo, cfg):
    create_work_item(git_repo, cfg, "fix", "licence-crash", "Crash when licence missing")
    assert run_git(git_repo, "log", "-1", "--format=%B") == "FIX-1: open work item\n\nFactory-Work: FIX-1"
    assert run_git(git_repo, "log", "-1", "--format=%(trailers:key=Factory-Work,valueonly)") == "FIX-1"


def test_work_item_commit_holds_only_the_work_folder(git_repo, cfg):
    (git_repo / "staged.txt").write_text("staged\n", encoding="utf-8")
    run_git(git_repo, "add", "staged.txt")
    (git_repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    w = create_work_item(git_repo, cfg, "feature", "export", "Export to CSV")
    files = run_git(git_repo, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert files and all(f.startswith(f"sw-factory/work/{w.path.name}/") for f in files)
    status = run_git(git_repo, "status", "--porcelain").splitlines()
    assert "A  staged.txt" in status and "?? untracked.txt" in status


def test_base_branch_is_left_untouched(git_repo, cfg):
    before = run_git(git_repo, "rev-parse", "main")
    create_work_item(git_repo, cfg, "fix", "a", "A fix")
    assert run_git(git_repo, "rev-parse", "main") == before
    assert run_git(git_repo, "ls-tree", "main", "--", "sw-factory/work/") == ""


def test_templates_are_filled_in(git_repo, cfg):
    w = create_work_item(git_repo, cfg, "fix", "licence-crash", "Crash when licence missing")
    ticket = (w.path / "ticket.md").read_text(encoding="utf-8")
    assert ticket.splitlines()[0] == "# FIX-1: Crash when licence missing"
    for heading in ("## Problem", "## Repro steps", "## Acceptance criteria", "## Out of scope"):
        assert heading in ticket
    for path in w.path.rglob("*"):
        if path.is_file():
            assert "{{" not in path.read_text(encoding="utf-8"), path
    progress = read_status(w.path)
    assert progress.stage == "plan" and progress.state == "open" and progress.next
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", progress.updated)


@pytest.mark.parametrize("track", sorted(TRACK_PREFIX))
def test_every_template_opens_with_its_title_and_a_status_line(git_repo, cfg, track):
    w = create_work_item(git_repo, cfg, track, "t", "Some title")
    assert STATUS_LINE.fullmatch(((w.path / "progress.md").read_text(encoding="utf-8")).splitlines()[0])
    assert "## Log" in (w.path / "progress.md").read_text(encoding="utf-8")
    docs = [p for p in w.path.glob("*.md") if p.name != "progress.md"]
    assert docs
    for doc in docs:
        text = doc.read_text(encoding="utf-8")
        assert text.splitlines()[0].startswith("# "), doc
        assert text.count("\n## ") >= 1, doc


def test_title_text_that_looks_like_a_placeholder_is_not_expanded(git_repo, cfg):
    w = create_work_item(git_repo, cfg, "fix", "a", "Use {{id}} in the docs")
    assert (w.path / "ticket.md").read_text(encoding="utf-8").splitlines()[0] == "# FIX-1: Use {{id}} in the docs"


def test_second_item_takes_the_next_id(git_repo, cfg):
    create_work_item(git_repo, cfg, "fix", "a", "First")
    run_git(git_repo, "switch", "-q", "main")
    second = create_work_item(git_repo, cfg, "fix", "b", "Second")
    assert second.id == "FIX-2" and second.branch == "sw-factory/FIX-2-b"
    assert branch_names(git_repo) == ["main", "sw-factory/FIX-1-a", "sw-factory/FIX-2-b"]  # git lists them by name


def test_per_item_branching_from_a_detached_head_is_fine(git_repo, cfg):
    run_git(git_repo, "switch", "-q", "--detach", "HEAD")
    w = create_work_item(git_repo, cfg, "fix", "a", "A fix")
    assert w.branch == "sw-factory/FIX-1-a"
    assert run_git(git_repo, "branch", "--show-current") == "sw-factory/FIX-1-a"


def test_bad_slug_rejected(git_repo, cfg):
    with pytest.raises(ValueError):
        create_work_item(git_repo, cfg, "fix", "Bad Slug!", "A fix")


@pytest.mark.parametrize("slug", ["", "UPPER", "with_underscore", "has space", "dot.dot", "a" * 41, "trailing\n", "éclair"])
def test_bad_slugs_are_rejected_before_anything_is_created(git_repo, cfg, slug):
    with pytest.raises(ValueError, match="slug"):
        create_work_item(git_repo, cfg, "fix", slug, "A fix")
    assert branch_names(git_repo) == ["main"] and not (git_repo / "sw-factory").exists()


@pytest.mark.parametrize("slug", ["a" * 40, "0-fix-it-2"])  # the longest allowed, and digits and hyphens
def test_good_slugs_are_accepted(git_repo, cfg, slug):
    assert create_work_item(git_repo, cfg, "fix", slug, "A fix").slug == slug


def test_unknown_track_rejected(git_repo, cfg):
    with pytest.raises(ValueError, match="epic"):
        create_work_item(git_repo, cfg, "epic", "a", "A thing")


@pytest.mark.parametrize("title", ["", "   ", "two\nlines"])
def test_bad_titles_are_rejected(git_repo, cfg, title):
    with pytest.raises(ValueError, match="title"):
        create_work_item(git_repo, cfg, "fix", "a", title)
    assert branch_names(git_repo) == ["main"]


def test_single_branching_requires_work_branch(git_repo, cfg_single):
    with pytest.raises(GitError, match="factory-work"):
        create_work_item(git_repo, cfg_single, "fix", "a", "A fix")
    assert not (git_repo / "sw-factory").exists()


def test_single_branching_works_on_the_work_branch(git_repo, cfg_single):
    run_git(git_repo, "switch", "-q", "-c", "factory-work")
    first = create_work_item(git_repo, cfg_single, "fix", "a", "First")
    second = create_work_item(git_repo, cfg_single, "feature", "b", "Second")
    third = create_work_item(git_repo, cfg_single, "fix", "c", "Third")
    assert (first.id, second.id, third.id) == ("FIX-1", "FEA-1", "FIX-2")
    assert first.branch is None and (first.path / "ticket.md").is_file()
    assert run_git(git_repo, "branch", "--show-current") == "factory-work"
    assert branch_names(git_repo) == ["factory-work", "main"]
    assert run_git(git_repo, "log", "-1", "--format=%B") == "FIX-2: open work item\n\nFactory-Work: FIX-2"


def test_detached_head_error_message(git_repo, cfg_single):
    run_git(git_repo, "switch", "-q", "--detach", "HEAD")
    with pytest.raises(GitError, match="detached HEAD") as caught:
        create_work_item(git_repo, cfg_single, "fix", "a", "A fix")
    assert "factory-work" in str(caught.value)
    assert not (git_repo / "sw-factory").exists()


def test_missing_base_branch_is_a_git_error_and_leaves_no_trace(git_repo, cfg):
    with pytest.raises(GitError, match="nosuch"):
        create_work_item(git_repo, dataclasses.replace(cfg, base_branch="nosuch"), "fix", "a", "A fix")
    assert branch_names(git_repo) == ["main"] and not (git_repo / "sw-factory").exists()
    assert run_git(git_repo, "branch", "--show-current") == "main"


def test_existing_work_folder_is_refused_not_overwritten(git_repo, cfg):
    stray = git_repo / "sw-factory" / "work" / "FIX-1-a"
    stray.mkdir(parents=True)
    (stray / "ticket.md").write_text("mine\n", encoding="utf-8")
    with pytest.raises(FileExistsError, match="FIX-1-a"):
        create_work_item(git_repo, cfg, "fix", "a", "A fix")
    assert (stray / "ticket.md").read_text(encoding="utf-8") == "mine\n"
    assert branch_names(git_repo) == ["main"]


def test_missing_template_folder_is_reported_before_anything_changes(git_repo, cfg, tmp_path, monkeypatch):
    home = tmp_path / "empty-home"
    home.mkdir()
    monkeypatch.setenv("FACTORY_HOME", str(home))
    with pytest.raises(FileNotFoundError, match="templates"):
        create_work_item(git_repo, cfg, "fix", "a", "A fix")
    assert branch_names(git_repo) == ["main"]


def block_commits(repo: Path) -> Path:
    """Install a pre-commit hook that refuses every commit; returns the hook so a test can remove it."""
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'blocked by test hook' >&2\nexit 1\n", encoding="utf-8", newline="\n")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
    return hook


def local_branches(repo: Path) -> list[str]:
    """Local branch names, without the ``(HEAD detached ...)`` line that ``git branch`` adds."""
    return run_git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines()


def test_failed_commit_leaves_no_branch_and_no_folder(git_repo, cfg):
    hook = block_commits(git_repo)
    with pytest.raises(GitError, match="blocked by test hook"):
        create_work_item(git_repo, cfg, "fix", "a", "A fix")
    assert run_git(git_repo, "branch", "--show-current") == "main"
    assert branch_names(git_repo) == ["main"]
    assert not (git_repo / "sw-factory").exists()
    assert run_git(git_repo, "status", "--porcelain") == ""
    hook.unlink()
    assert next_id(git_repo, "fix", "main") == "FIX-1"  # the failed attempt did not burn an id


def test_failed_commit_from_a_detached_head_returns_to_that_commit(git_repo, cfg):
    run_git(git_repo, "switch", "-q", "--detach", "HEAD")
    commit = run_git(git_repo, "rev-parse", "HEAD")
    block_commits(git_repo)
    with pytest.raises(GitError, match="blocked by test hook"):
        create_work_item(git_repo, cfg, "fix", "a", "A fix")
    assert run_git(git_repo, "branch", "--show-current") == "" and run_git(git_repo, "rev-parse", "HEAD") == commit
    assert local_branches(git_repo) == ["main"] and not (git_repo / "sw-factory").exists()


def test_failed_commit_in_single_branching_removes_the_folder(git_repo, cfg_single):
    run_git(git_repo, "switch", "-q", "-c", "factory-work")
    block_commits(git_repo)
    with pytest.raises(GitError):
        create_work_item(git_repo, cfg_single, "fix", "a", "A fix")
    assert run_git(git_repo, "branch", "--show-current") == "factory-work"
    assert not (git_repo / "sw-factory").exists() and run_git(git_repo, "status", "--porcelain") == ""


# --------------------------------------------------------------------------- find_work_item

def test_find_work_item_round_trip(git_repo, cfg):
    created = create_work_item(git_repo, cfg, "feature", "export-csv", "Export to CSV")
    assert find_work_item(git_repo, "FEA-1") == created


def test_find_work_item_accepts_the_folder_name(git_repo, cfg):
    created = create_work_item(git_repo, cfg, "fix", "licence-crash", "Crash")
    assert find_work_item(git_repo, "FIX-1-licence-crash") == created


def test_find_work_item_in_single_branching_has_no_branch(git_repo, cfg_single):
    run_git(git_repo, "switch", "-q", "-c", "factory-work")
    created = create_work_item(git_repo, cfg_single, "fix", "a", "A fix")
    assert find_work_item(git_repo, "FIX-1") == created and created.branch is None


def test_find_work_item_does_not_confuse_ids_sharing_a_prefix(git_repo):
    for name in ("FEA-1-a", "FEA-12-b", "FEA-12b-c"):
        write_progress(git_repo / "sw-factory" / "work" / name)
    assert find_work_item(git_repo, "FEA-1").slug == "a"
    assert find_work_item(git_repo, "FEA-12").slug == "b"
    assert find_work_item(git_repo, "FEA-12b").slug == "c"


def test_find_work_item_missing_names_the_branch_that_holds_it(git_repo, cfg):
    create_work_item(git_repo, cfg, "fix", "licence-crash", "Crash")
    run_git(git_repo, "switch", "-q", "main")
    with pytest.raises(FileNotFoundError, match=r"FIX-1.*sw-factory/FIX-1-licence-crash"):
        find_work_item(git_repo, "FIX-1")


def test_find_work_item_unknown_id(git_repo):
    with pytest.raises(FileNotFoundError, match="FIX-9"):
        find_work_item(git_repo, "FIX-9")


def test_find_work_item_bad_id(git_repo):
    with pytest.raises(ValueError, match="nonsense"):
        find_work_item(git_repo, "nonsense")


# --------------------------------------------------------------------------- status

def test_status_roundtrip(tmp_path):
    path = write_progress(tmp_path)
    written = write_status(tmp_path, "build", "in progress", "T3 verify", "Finished T2")
    got = read_status(tmp_path)
    assert (got.stage, got.state, got.next) == ("build", "in progress", "T3 verify") and got == written
    log = path.read_text(encoding="utf-8").split("## Log", 1)[1].strip().splitlines()
    assert "Finished T2" in log[0] and "Opened" in log[1]


def test_write_status_rewrites_line_one_in_the_documented_format(tmp_path):
    path = write_progress(tmp_path)
    write_status(tmp_path, "test", "blocked", "ask the owner", "Waiting")
    first = path.read_text(encoding="utf-8").splitlines()[0]
    assert re.fullmatch(rf"Status: test · blocked · next: ask the owner · updated {UTC_STAMP}", first)
    assert read_status(tmp_path).updated == first.rsplit("updated ", 1)[1]


def test_log_is_newest_first_and_the_rest_of_the_file_is_kept(tmp_path):
    path = write_progress(tmp_path)
    path.write_text(path.read_text(encoding="utf-8") + "\n## Notes\n\nkeep me\n", encoding="utf-8", newline="\n")
    write_status(tmp_path, "plan", "open", "a", "first note")
    write_status(tmp_path, "design", "open", "b", "second note")
    text = path.read_text(encoding="utf-8")
    assert text.index("second note") < text.index("first note") < text.index("Opened") < text.index("## Notes")
    assert text.endswith("keep me\n") and text.count("## Log") == 1
    assert re.search(rf"## Log\n\n- {UTC_STAMP} — second note\n- {UTC_STAMP} — first note\n- 2026-10-20 — Opened\n", text)
    assert read_status(tmp_path).stage == "design"


def test_a_next_text_containing_the_separator_round_trips(tmp_path):
    write_progress(tmp_path)
    write_status(tmp_path, "build", "in progress", "T3 · then T4 · then updated 5", "note")
    got = read_status(tmp_path)
    assert (got.stage, got.state, got.next) == ("build", "in progress", "T3 · then T4 · then updated 5")


def test_a_multi_line_note_stays_inside_its_log_entry(tmp_path):
    path = write_progress(tmp_path)
    write_status(tmp_path, "build", "open", "x", "line one\nline two")
    assert "- " in path.read_text(encoding="utf-8")
    assert re.search(rf"## Log\n\n- {UTC_STAMP} — line one\n  line two\n- 2026", path.read_text(encoding="utf-8"))


def test_write_status_adds_the_log_heading_when_missing(tmp_path):
    path = tmp_path / "progress.md"
    path.write_text("Status: plan · open · next: x · updated 2026-10-20\n", encoding="utf-8", newline="\n")
    write_status(tmp_path, "plan", "open", "y", "a note")
    text = path.read_text(encoding="utf-8")
    assert text.splitlines()[0].startswith("Status: plan · open · next: y")
    assert re.search(r"\n## Log\n\n- \S+ — a note\n$", text)


@pytest.mark.parametrize(
    "stage, state, next_, note",
    [
        ("pl · an", "open", "x", "n"),
        ("plan", "op · en", "x", "n"),
        ("", "open", "x", "n"),
        ("plan", "  ", "x", "n"),
        ("plan", "open", "", "n"),
        ("plan", "open", "two\nlines", "n"),
        ("plan", "op\ren", "x", "n"),
        ("plan", "open", "x", ""),
    ],
)
def test_write_status_rejects_a_field_that_would_break_the_line(tmp_path, stage, state, next_, note):
    path = write_progress(tmp_path)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        write_status(tmp_path, stage, state, next_, note)
    assert path.read_bytes() == before


def test_write_status_refuses_when_line_one_is_not_a_status(tmp_path):
    path = tmp_path / "progress.md"
    path.write_text("# My own notes\n\n## Log\n", encoding="utf-8", newline="\n")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="progress.md"):
        write_status(tmp_path, "plan", "open", "x", "n")
    assert path.read_bytes() == before


def test_status_files_with_a_byte_order_mark_and_crlf_are_read(tmp_path):
    (tmp_path / "progress.md").write_bytes(
        "﻿Status: plan · open · next: x · updated 2026-10-20\r\n\r\n## Log\r\n".encode("utf-8")
    )
    assert read_status(tmp_path).stage == "plan"
    write_status(tmp_path, "plan", "open", "y", "n")
    assert read_status(tmp_path).next == "y"


def test_read_status_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="progress.md"):
        read_status(tmp_path)


def test_read_status_without_a_status_line(tmp_path):
    (tmp_path / "progress.md").write_text("## Log\n", encoding="utf-8")
    with pytest.raises(ValueError, match="progress.md"):
        read_status(tmp_path)


# --------------------------------------------------------------------------- trailers

def test_format_trailers():
    assert format_trailers("FEA-7/T3", "implementer", "m", "xhigh").splitlines() == [
        "Factory-Work: FEA-7/T3",
        "Factory-Role: implementer",
        "Factory-Model: m (xhigh)",
    ]


@pytest.mark.parametrize("args", [("", "r", "m", "e"), ("w", " ", "m", "e"), ("w", "r", "", "e"), ("w", "r", "m", ""),
                                  ("w\nFactory-Role: x", "r", "m", "e"), ("w", "r", "m", "e\n")])
def test_format_trailers_rejects_values_that_would_forge_a_trailer(args):
    with pytest.raises(ValueError):
        format_trailers(*args)


def test_format_trailers_are_recognised_by_git(git_repo):
    message = "Do a thing\n\n" + format_trailers("FEA-7/T3", "implementer", "m", "xhigh")
    run_git(git_repo, "commit", "-q", "--allow-empty", "-m", message)
    assert run_git(git_repo, "log", "-1", "--format=%(trailers:key=Factory-Model,valueonly)") == "m (xhigh)"


# --------------------------------------------------------------------------- sw-factory work (CLI)

def run_cli(capsys, *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_cli_work_help_without_a_subcommand(capsys):
    code, out, _ = run_cli(capsys, "work")
    assert code == 2 and "new" in out and "set-status" in out


def test_cli_work_new(git_repo, capsys):
    write_sw_factory_yaml(git_repo)
    code, out, err = run_cli(
        capsys, "work", "new", "--project", str(git_repo),
        "--track", "fix", "--slug", "licence-crash", "--title", "Crash when licence missing",
    )
    assert code == 0, err
    assert json.loads(out) == {
        "id": "FIX-1", "track": "fix", "slug": "licence-crash", "title": "Crash when licence missing",
        "branch": "sw-factory/FIX-1-licence-crash", "folder": "sw-factory/work/FIX-1-licence-crash",
    }
    assert (git_repo / "sw-factory/work/FIX-1-licence-crash/ticket.md").is_file()
    assert run_git(git_repo, "branch", "--show-current") == "sw-factory/FIX-1-licence-crash"


def test_cli_output_is_ascii_whatever_the_title_says(git_repo, capsys):
    """A pipe on Windows is not UTF-8, so the JSON escapes non-ASCII text instead of printing it raw."""
    write_sw_factory_yaml(git_repo)
    code, out, err = run_cli(capsys, "work", "new", "--project", str(git_repo), "--track", "fix", "--slug", "a",
                             "--title", "Café crash")
    assert code == 0, err
    assert out.isascii() and json.loads(out)["title"] == "Café crash"
    code, out, _ = run_cli(capsys, "work", "status", "FIX-1", "--project", str(git_repo))
    assert code == 0 and out.isascii()


def test_cli_work_new_in_single_branching_reports_no_branch(git_repo, capsys):
    (git_repo / "sw-factory.yaml").write_text(
        "project: demo\napprovers: [owner]\nbranching: single\nwork_branch: factory-work\n"
        "verify:\n  quick: q\n  full: f\n", encoding="utf-8", newline="\n",
    )
    run_git(git_repo, "switch", "-q", "-c", "factory-work")
    code, out, err = run_cli(capsys, "work", "new", "--project", str(git_repo), "--track", "fix", "--slug", "a", "--title", "T")
    assert code == 0, err
    assert json.loads(out)["branch"] is None


def test_cli_work_new_finds_the_project_from_the_current_folder(git_repo, capsys, monkeypatch):
    write_sw_factory_yaml(git_repo)
    (git_repo / "sub").mkdir()
    monkeypatch.chdir(git_repo / "sub")
    code, out, err = run_cli(capsys, "work", "new", "--track", "feature", "--slug", "x", "--title", "An X")
    assert code == 0, err
    assert json.loads(out)["id"] == "FEA-1"


def test_cli_work_new_reports_a_bad_slug(git_repo, capsys):
    write_sw_factory_yaml(git_repo)
    code, out, err = run_cli(
        capsys, "work", "new", "--project", str(git_repo), "--track", "fix", "--slug", "Bad Slug!", "--title", "T",
    )
    assert code == 2 and out == "" and "slug" in err
    assert branch_names(git_repo) == ["main"]


def test_cli_work_new_reports_git_problems(git_repo, capsys):
    write_sw_factory_yaml(git_repo)
    run_git(git_repo, "switch", "-q", "--detach", "HEAD")
    (git_repo / "sw-factory.yaml").write_text(
        "project: demo\napprovers: [owner]\nbranching: single\nwork_branch: factory-work\n"
        "verify:\n  quick: q\n  full: f\n", encoding="utf-8", newline="\n",
    )
    code, _, err = run_cli(capsys, "work", "new", "--project", str(git_repo), "--track", "fix", "--slug", "a", "--title", "T")
    assert code == 2 and "detached HEAD" in err


def test_cli_work_new_without_a_project(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, _, err = run_cli(capsys, "work", "new", "--track", "fix", "--slug", "a", "--title", "T")
    assert code == 2 and "sw-factory.yaml" in err


def test_cli_work_new_rejects_an_unknown_track():
    with pytest.raises(SystemExit) as caught:
        cli.main(["work", "new", "--track", "epic", "--slug", "a", "--title", "T"])
    assert caught.value.code == 2


def test_cli_work_status_and_set_status(git_repo, capsys):
    write_sw_factory_yaml(git_repo)
    project = str(git_repo)
    run_cli(capsys, "work", "new", "--project", project, "--track", "fix", "--slug", "a", "--title", "T")
    code, out, _ = run_cli(capsys, "work", "status", "FIX-1", "--project", project)
    opened = json.loads(out)
    assert code == 0 and opened["id"] == "FIX-1" and (opened["stage"], opened["state"]) == ("plan", "open")
    assert set(opened) == {"id", "stage", "state", "next", "updated"}
    code, out, err = run_cli(
        capsys, "work", "set-status", "FIX-1", "--project", project,
        "--stage", "build", "--state", "in progress", "--next", "T1 failing test", "--note", "Reproduced the crash",
    )
    assert code == 0, err
    changed = json.loads(out)
    assert changed["id"] == "FIX-1" and (changed["stage"], changed["state"], changed["next"]) == (
        "build", "in progress", "T1 failing test")
    assert re.fullmatch(UTC_STAMP, changed["updated"])
    code, again, _ = run_cli(capsys, "work", "status", "FIX-1", "--project", project)
    assert code == 0 and json.loads(again) == changed
    assert "Reproduced the crash" in (git_repo / "sw-factory/work/FIX-1-a/progress.md").read_text(encoding="utf-8")


def test_cli_work_status_unknown_id(git_repo, capsys):
    write_sw_factory_yaml(git_repo)
    code, out, err = run_cli(capsys, "work", "status", "FIX-9", "--project", str(git_repo))
    assert code == 2 and out == "" and "FIX-9" in err


def test_cli_work_set_status_rejects_a_bad_field(git_repo, capsys):
    write_sw_factory_yaml(git_repo)
    project = str(git_repo)
    run_cli(capsys, "work", "new", "--project", project, "--track", "fix", "--slug", "a", "--title", "T")
    code, _, err = run_cli(
        capsys, "work", "set-status", "FIX-1", "--project", project,
        "--stage", "build", "--state", "x · y", "--next", "n", "--note", "note",
    )
    assert code == 2 and "state" in err


def test_cli_work_set_status_needs_every_option():
    with pytest.raises(SystemExit) as caught:
        cli.main(["work", "set-status", "FIX-1", "--stage", "build"])
    assert caught.value.code == 2
