"""Tests for swfactory.mergegate and the ``sw-factory gate`` command.

Every git repository is created under pytest's ``tmp_path`` (the ``git_repo`` fixtures in conftest.py) with
the machine's git configuration hidden. The verify command is a real shell command run by the gate, so these
tests use this interpreter as a small scriptable command.
"""
from __future__ import annotations

import dataclasses
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from swfactory import cli
from swfactory.gitutil import run_git
from swfactory.mergegate import GateCheck, GateReport, run_gate, write_gate_report
from swfactory.panel import PanelResult, write_panel_summary
from swfactory.workitems import WorkItem, create_work_item, write_status

CHECK_NAMES = ["base-merged", "reviewed-commit", "size-cap", "id-unused-on-base", "verify-full", "spot-check"]
SPOT = "AC1: the licence dialog now opens (seen on the demo project)"


def py(code: str) -> str:
    """A shell command that runs ``code`` with this interpreter (single quotes only inside ``code``)."""
    return f'"{sys.executable}" -c "{code}"'


PASSING_VERIFY = py("print('all green')")


# --------------------------------------------------------------------------- helpers

def numbered(count: int, prefix: str = "line") -> str:
    return "".join(f"{prefix} {n}\n" for n in range(count))


def commit_file(repo: Path, rel: str, text: str = "x\n", message: str = "Add a file") -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    run_git(repo, "add", "--", rel)
    run_git(repo, "commit", "-q", "-m", message)


def head(repo: Path) -> str:
    return run_git(repo, "rev-parse", "HEAD")


def advance_base(repo: Path, rel: str, text: str = "base change\n", push: bool = True, base: str = "main") -> None:
    """Commit a file on the base branch (as if another work item had merged), then come back to the branch."""
    branch = run_git(repo, "branch", "--show-current")
    run_git(repo, "switch", "-q", base)
    commit_file(repo, rel, text, message="Merge another work item")
    if push:
        run_git(repo, "push", "-q", "origin", base)
    run_git(repo, "switch", "-q", branch)


@dataclass
class Work:
    """A branch ``sw-factory/FEA-1-demo`` cut from main with one small commit on it."""

    repo: Path
    item: WorkItem
    head: str


def make_work(repo: Path, base: str = "main") -> Work:
    run_git(repo, "switch", "-q", "-c", "sw-factory/FEA-1-demo", base)
    commit_file(repo, "src/app.py", numbered(10), message="FEA-1: add the app")
    item = WorkItem(
        id="FEA-1",
        track="feature",
        slug="demo",
        title="Demo",
        branch="sw-factory/FEA-1-demo",
        path=repo / "sw-factory" / "work" / "FEA-1-demo",
    )
    return Work(repo, item, head(repo))


@pytest.fixture
def work(git_repo: Path) -> Work:
    return make_work(git_repo)


@pytest.fixture
def gate_cfg(cfg):
    """The shared project settings with a verify command that passes."""
    return dataclasses.replace(cfg, verify_full=PASSING_VERIFY)


def gate(work: Work, cfg, **kwargs) -> GateReport:
    reviewed = kwargs.pop("reviewed_commit", work.head)
    spot = kwargs.pop("spot_check", SPOT)
    return run_gate(work.repo, cfg, work.item, reviewed, spot, **kwargs)


def check(report: GateReport, name: str) -> GateCheck:
    return next(c for c in report.checks if c.name == name)


# --------------------------------------------------------------------------- the whole gate

def test_gate_passes_clean_branch(work, gate_cfg):
    report = gate(work, gate_cfg)
    assert report.ok, [(c.name, c.detail) for c in report.checks if not c.ok]
    assert [c.name for c in report.checks] == CHECK_NAMES
    assert all(c.ok for c in report.checks)


def test_every_check_is_reported_when_several_fail(work, gate_cfg):
    advance_base(work.repo, "other.txt")
    commit_file(work.repo, "src/more.py", numbered(500), message="after the review")
    failing = dataclasses.replace(gate_cfg, verify_full=py("raise SystemExit(1)"))
    report = gate(work, failing, spot_check="")
    assert [c.name for c in report.checks] == CHECK_NAMES
    assert not report.ok
    assert [c.name for c in report.checks if not c.ok] == [
        "base-merged", "reviewed-commit", "size-cap", "verify-full", "spot-check"
    ]


def test_a_folder_that_is_not_a_git_repo_fails_every_git_check_without_raising(tmp_path, gate_cfg):
    folder = tmp_path / "plain"
    folder.mkdir()
    item = WorkItem("FEA-1", "feature", "demo", "Demo", None, folder / "sw-factory" / "work" / "FEA-1-demo")
    report = run_gate(folder, gate_cfg, item, "abc123", SPOT)
    assert [c.name for c in report.checks] == CHECK_NAMES
    assert [c.ok for c in report.checks] == [False, False, False, False, True, True]
    assert all(c.detail for c in report.checks)


# --------------------------------------------------------------------------- base-merged

def test_base_not_merged_fails(work, gate_cfg):
    advance_base(work.repo, "other.txt")
    result = check(gate(work, gate_cfg), "base-merged")
    assert not result.ok
    assert "origin/main" in result.detail


def test_base_merged_after_merging_the_base_in(work, gate_cfg):
    advance_base(work.repo, "other.txt")
    run_git(work.repo, "merge", "-q", "--no-edit", "main")
    reviewed = head(work.repo)
    result = check(gate(work, gate_cfg, reviewed_commit=reviewed), "base-merged")
    assert result.ok


def test_base_merged_names_the_remote_ref_when_it_exists(work, gate_cfg):
    result = check(gate(work, gate_cfg), "base-merged")
    assert result.ok and "origin/main" in result.detail


def test_base_merged_prefers_the_remote_ref_over_a_stale_or_ahead_local_one(work, gate_cfg):
    advance_base(work.repo, "other.txt")  # origin/main and main both move ahead of the branch
    run_git(work.repo, "merge", "-q", "--no-edit", "origin/main")
    # A local commit that was never pushed does not have to be in the branch: the gate looks at origin/main.
    advance_base(work.repo, "local-only.txt", push=False)
    result = check(gate(work, gate_cfg, reviewed_commit=head(work.repo)), "base-merged")
    assert result.ok and "origin/main" in result.detail


def test_base_merged_uses_the_local_branch_without_a_remote(git_repo_no_remote_master, cfg):
    cfg = dataclasses.replace(cfg, base_branch="master", verify_full=PASSING_VERIFY)
    work = make_work(git_repo_no_remote_master, base="master")
    ok = check(gate(work, cfg), "base-merged")
    assert ok.ok and "master" in ok.detail and "origin" not in ok.detail
    run_git(work.repo, "switch", "-q", "master")
    commit_file(work.repo, "other.txt", message="Merge another work item")
    run_git(work.repo, "switch", "-q", "sw-factory/FEA-1-demo")
    failed = check(gate(work, cfg), "base-merged")
    assert not failed.ok and "master" in failed.detail and "origin" not in failed.detail


def test_a_missing_base_branch_fails_the_checks_that_need_it(work, gate_cfg):
    report = gate(work, dataclasses.replace(gate_cfg, base_branch="nowhere"))
    for name in ("base-merged", "size-cap", "id-unused-on-base"):
        assert not check(report, name).ok
        assert "nowhere" in check(report, name).detail
    assert check(report, "verify-full").ok  # the checks that do not need the base still ran


# --------------------------------------------------------------------------- reviewed-commit

def test_unreviewed_commit_fails(work, gate_cfg):
    commit_file(work.repo, "src/late.py", message="a change after the review")
    result = check(gate(work, gate_cfg), "reviewed-commit")
    assert not result.ok
    assert work.head[:7] in result.detail and head(work.repo)[:7] in result.detail


def test_reviewed_commit_may_be_an_abbreviated_sha(work, gate_cfg):
    assert check(gate(work, gate_cfg, reviewed_commit=work.head[:10]), "reviewed-commit").ok


def test_reviewed_commit_that_is_not_in_the_repository_fails(work, gate_cfg):
    result = check(gate(work, gate_cfg, reviewed_commit="0123456789abcdef0123456789abcdef01234567"), "reviewed-commit")
    assert not result.ok and "0123456789abcdef" in result.detail


@pytest.mark.parametrize("value", ["", "  ", "--help", "-x"])
def test_reviewed_commit_that_is_not_a_name_for_a_commit_fails(work, gate_cfg, value):
    assert not check(gate(work, gate_cfg, reviewed_commit=value), "reviewed-commit").ok


def test_a_modified_tracked_file_fails_reviewed_commit(work, gate_cfg):
    (work.repo / "src" / "app.py").write_text("changed after the review\n", encoding="utf-8", newline="\n")
    report = gate(work, gate_cfg)
    result = check(report, "reviewed-commit")
    assert not result.ok
    assert "uncommitted changes in 1 tracked file(s)" in result.detail and "src/app.py" in result.detail
    assert [c.name for c in report.checks] == CHECK_NAMES
    assert check(report, "verify-full").ok and check(report, "size-cap").ok  # the other checks still run on the commit


def test_a_staged_but_uncommitted_change_fails_reviewed_commit(work, gate_cfg):
    (work.repo / "src" / "app.py").write_text("staged\n", encoding="utf-8", newline="\n")
    run_git(work.repo, "add", "--", "src/app.py")
    assert "src/app.py" in check(gate(work, gate_cfg), "reviewed-commit").detail


def test_an_untracked_file_alone_does_not_fail_reviewed_commit(work, gate_cfg):
    (work.repo / "notes.txt").write_text("scratch\n", encoding="utf-8", newline="\n")
    folder = work.repo / "sw-factory" / "work" / "FEA-1-demo"
    folder.mkdir(parents=True)
    (folder / "merge-gate.md").write_text("GATE: FAIL\n", encoding="utf-8", newline="\n")  # an earlier report
    assert check(gate(work, gate_cfg), "reviewed-commit").ok


def test_a_deleted_tracked_file_fails_reviewed_commit(work, gate_cfg):
    (work.repo / "src" / "app.py").unlink()
    result = check(gate(work, gate_cfg), "reviewed-commit")
    assert not result.ok and "src/app.py" in result.detail


def test_a_renamed_tracked_file_counts_once_under_its_new_path(work, gate_cfg):
    run_git(work.repo, "mv", "src/app.py", "src/my app.py")
    result = check(gate(work, gate_cfg), "reviewed-commit")
    assert not result.ok
    assert "uncommitted changes in 1 tracked file(s)" in result.detail and "src/my app.py" in result.detail
    assert "src/app.py" not in result.detail  # the old path is not listed as a second file


def test_uncommitted_changes_list_at_most_five_paths_but_count_them_all(work, gate_cfg):
    for n in range(8):
        commit_file(work.repo, f"src/f{n}.py", "x\n", message=f"add f{n}")
    reviewed = head(work.repo)
    for n in range(8):
        (work.repo / "src" / f"f{n}.py").write_text("y\n", encoding="utf-8", newline="\n")
    detail = check(gate(work, gate_cfg, reviewed_commit=reviewed), "reviewed-commit").detail
    assert "uncommitted changes in 8 tracked file(s)" in detail
    assert sum(f"src/f{n}.py" in detail for n in range(8)) == 5


def test_a_wrong_commit_and_uncommitted_changes_are_both_reported(work, gate_cfg):
    (work.repo / "src" / "app.py").write_text("changed\n", encoding="utf-8", newline="\n")
    older = run_git(work.repo, "rev-parse", "HEAD~1")
    result = check(gate(work, gate_cfg, reviewed_commit=older), "reviewed-commit")
    assert not result.ok
    assert "run the panel again" in result.detail and "uncommitted changes in 1 tracked file(s)" in result.detail


# The factory commits its own bookkeeping after a review passes (panel summary, progress.md, approvals, the
# gate report), all inside the work folder under sw-factory/work/. That must not make the gate unpassable.

WORK_FOLDER = "sw-factory/work/FEA-1-demo"


def test_bookkeeping_commits_after_the_review_keep_reviewed_commit_ok(work, gate_cfg):
    commit_file(work.repo, f"{WORK_FOLDER}/reviews/T1-r1-panel.md", "VERDICT: PASS\n", message="FEA-1: panel summary")
    commit_file(work.repo, f"{WORK_FOLDER}/progress.md", "Status: build\n", message="FEA-1: progress")
    result = check(gate(work, gate_cfg), "reviewed-commit")  # reviewed = the commit before both
    assert result.ok
    assert "HEAD is 2 commit(s) after the reviewed commit" in result.detail
    assert "later changes are factory bookkeeping only" in result.detail


def test_one_bookkeeping_commit_touching_several_files_is_fine(work, gate_cfg):
    for name in ("reviews/T1-r1-panel.md", "approvals/spec-rev1.md", "progress.md"):
        path = work.repo / WORK_FOLDER / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n", encoding="utf-8", newline="\n")
    run_git(work.repo, "add", "-A")
    run_git(work.repo, "commit", "-q", "-m", "FEA-1: bookkeeping")
    result = check(gate(work, gate_cfg), "reviewed-commit")
    assert result.ok and "HEAD is 1 commit(s) after the reviewed commit" in result.detail


def test_bookkeeping_in_another_items_folder_counts_too(work, gate_cfg):
    commit_file(work.repo, "sw-factory/work/FIX-3-other/progress.md", "Status: build\n", message="FIX-3: progress")
    assert check(gate(work, gate_cfg), "reviewed-commit").ok


def test_a_product_commit_after_the_review_fails_naming_the_file(work, gate_cfg):
    commit_file(work.repo, f"{WORK_FOLDER}/progress.md", "Status: build\n", message="FEA-1: progress")
    commit_file(work.repo, "src/late.py", message="a change after the review")
    result = check(gate(work, gate_cfg), "reviewed-commit")
    assert not result.ok
    assert "src/late.py" in result.detail and "progress.md" not in result.detail
    assert "bookkeeping only" not in result.detail


def test_a_commit_mixing_bookkeeping_and_product_files_names_only_the_product_ones(work, gate_cfg):
    (work.repo / WORK_FOLDER).mkdir(parents=True)
    (work.repo / WORK_FOLDER / "progress.md").write_text("x\n", encoding="utf-8", newline="\n")
    (work.repo / "src" / "app.py").write_text("sneaked in with the bookkeeping\n", encoding="utf-8", newline="\n")
    run_git(work.repo, "add", "-A")
    run_git(work.repo, "commit", "-q", "-m", "FEA-1: progress and a fix")
    result = check(gate(work, gate_cfg), "reviewed-commit")
    assert not result.ok and "src/app.py" in result.detail and "progress.md" not in result.detail


def test_a_product_file_moved_into_the_work_folder_is_not_bookkeeping(work, gate_cfg):
    (work.repo / WORK_FOLDER).mkdir(parents=True)
    run_git(work.repo, "mv", "src/app.py", f"{WORK_FOLDER}/app.py")
    run_git(work.repo, "commit", "-q", "-m", "hide a product file in the work folder")
    result = check(gate(work, gate_cfg), "reviewed-commit")
    assert not result.ok and "src/app.py" in result.detail  # the deletion of the old path shows


def test_paths_that_only_look_like_the_work_folder_are_product_files(work, gate_cfg):
    commit_file(work.repo, "sw-factory/workshop/tool.py", message="not the work folder")
    commit_file(work.repo, "docs/sw-factory/work/note.md", message="not at the root")
    result = check(gate(work, gate_cfg), "reviewed-commit")
    assert not result.ok
    assert "sw-factory/workshop/tool.py" in result.detail and "docs/sw-factory/work/note.md" in result.detail


def test_product_changes_since_the_review_list_at_most_five_paths_but_count_them_all(work, gate_cfg):
    for n in range(8):
        commit_file(work.repo, f"src/late{n}.py", message=f"late {n}")
    result = check(gate(work, gate_cfg), "reviewed-commit")
    assert not result.ok
    assert sum(f"src/late{n}.py" in result.detail for n in range(8)) == 5 and "and 3 more" in result.detail


def test_a_reviewed_commit_that_is_not_an_ancestor_fails(work, gate_cfg):
    run_git(work.repo, "switch", "-q", "-c", "elsewhere", "main")
    commit_file(work.repo, "other.txt", message="a commit on another branch")
    elsewhere = head(work.repo)
    run_git(work.repo, "switch", "-q", "sw-factory/FEA-1-demo")
    result = check(gate(work, gate_cfg, reviewed_commit=elsewhere), "reviewed-commit")
    assert not result.ok and "not an ancestor" in result.detail
    assert elsewhere[:7] in result.detail and work.head[:7] in result.detail


def test_a_review_of_a_later_commit_than_head_fails(work, gate_cfg):
    commit_file(work.repo, f"{WORK_FOLDER}/progress.md", "Status: build\n", message="FEA-1: progress")
    later = head(work.repo)
    run_git(work.repo, "reset", "-q", "--hard", "HEAD~1")  # HEAD went back behind the reviewed commit
    result = check(gate(work, gate_cfg, reviewed_commit=later), "reviewed-commit")
    assert not result.ok and "not an ancestor" in result.detail


def test_bookkeeping_after_the_review_and_an_uncommitted_edit_are_both_reported(work, gate_cfg):
    commit_file(work.repo, f"{WORK_FOLDER}/progress.md", "Status: build\n", message="FEA-1: progress")
    (work.repo / "src" / "app.py").write_text("edited\n", encoding="utf-8", newline="\n")
    result = check(gate(work, gate_cfg), "reviewed-commit")
    assert not result.ok and "uncommitted changes in 1 tracked file(s): src/app.py" in result.detail
    assert "bookkeeping only" not in result.detail  # a pass is not claimed beside a failure


def test_a_product_change_after_the_review_and_an_uncommitted_edit_are_both_reported(work, gate_cfg):
    commit_file(work.repo, "src/late.py", message="a change after the review")
    (work.repo / "src" / "app.py").write_text("edited\n", encoding="utf-8", newline="\n")
    detail = check(gate(work, gate_cfg), "reviewed-commit").detail
    assert "src/late.py" in detail and "uncommitted changes in 1 tracked file(s)" in detail


def test_a_clean_tree_says_so_in_the_detail(work, gate_cfg):
    result = check(gate(work, gate_cfg), "reviewed-commit")
    assert result.ok and "no uncommitted changes" in result.detail


# --------------------------------------------------------------------------- size-cap

def test_size_cap_counts_and_excludes_generated(work, gate_cfg):
    commit_file(work.repo, "src/big.py", numbered(290), message="more code")  # 10 + 290 = 300 in src
    commit_file(work.repo, "gen/x.g.cs", numbered(500), message="generated")
    cfg = dataclasses.replace(gate_cfg, size_cap=400, generated=["gen/*"])
    result = check(gate(work, cfg, reviewed_commit=head(work.repo)), "size-cap")
    assert result.ok and result.detail == "300 lines (cap 400)"


def test_size_cap_counts_generated_files_that_are_not_listed(work, gate_cfg):
    commit_file(work.repo, "gen/x.g.cs", numbered(500), message="generated")
    result = check(gate(work, gate_cfg, reviewed_commit=head(work.repo)), "size-cap")
    assert not result.ok and result.detail.startswith("510 lines (cap 400)")


def test_size_cap_exceeded_suggests_split(work, gate_cfg):
    commit_file(work.repo, "src/big.py", numbered(440), message="more code")  # 10 + 440 = 450
    result = check(gate(work, gate_cfg, reviewed_commit=head(work.repo)), "size-cap")
    assert not result.ok
    assert "450 lines (cap 400)" in result.detail and "split the work" in result.detail


def test_size_cap_allows_exactly_the_cap(work, gate_cfg):
    commit_file(work.repo, "src/big.py", numbered(390), message="more code")  # 10 + 390 = 400
    result = check(gate(work, gate_cfg, reviewed_commit=head(work.repo)), "size-cap")
    assert result.ok and result.detail == "400 lines (cap 400)"


def test_size_cap_counts_deleted_lines_too(git_repo, gate_cfg):
    commit_file(git_repo, "docs/big.txt", numbered(50, "old"), message="a file on the base")
    run_git(git_repo, "push", "-q", "origin", "main")
    work = make_work(git_repo)
    (git_repo / "docs" / "big.txt").write_text(numbered(20, "new"), encoding="utf-8", newline="\n")
    run_git(git_repo, "commit", "-q", "-a", "-m", "rewrite")
    result = check(gate(work, gate_cfg, reviewed_commit=head(git_repo)), "size-cap")
    assert result.detail == f"{10 + 50 + 20} lines (cap 400)"


def test_binary_files_ignored(work, gate_cfg):
    path = work.repo / "assets" / "logo.bin"
    path.parent.mkdir()
    path.write_bytes(bytes(range(256)) * 200)
    run_git(work.repo, "add", "--", "assets/logo.bin")
    run_git(work.repo, "commit", "-q", "-m", "add a picture")
    result = check(gate(work, gate_cfg, reviewed_commit=head(work.repo)), "size-cap")
    assert result.ok and result.detail == "10 lines (cap 400)"


def test_size_cap_measures_the_branch_not_what_the_base_gained_since(work, gate_cfg):
    advance_base(work.repo, "big-merge.txt", numbered(900))  # not merged into the branch
    result = check(gate(work, gate_cfg), "size-cap")
    assert result.ok and result.detail == "10 lines (cap 400)"


def test_a_renamed_file_with_spaces_counts_only_its_edits(git_repo, gate_cfg):
    commit_file(git_repo, "docs/old name.txt", numbered(300, "doc"), message="a file on the base")
    run_git(git_repo, "push", "-q", "origin", "main")
    work = make_work(git_repo)
    run_git(git_repo, "mv", "docs/old name.txt", "docs/new name.txt")
    with (git_repo / "docs" / "new name.txt").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("one more\ntwo more\n")
    run_git(git_repo, "add", "-A")
    run_git(git_repo, "commit", "-q", "-m", "rename and edit")
    result = check(gate(work, gate_cfg, reviewed_commit=head(git_repo)), "size-cap")
    assert result.detail == "12 lines (cap 400)"  # 10 for src/app.py and 2 appended; the move itself is free


def test_a_pure_rename_counts_nothing(git_repo, gate_cfg):
    commit_file(git_repo, "docs/old.txt", numbered(300, "doc"), message="a file on the base")
    run_git(git_repo, "push", "-q", "origin", "main")
    work = make_work(git_repo)
    run_git(git_repo, "mv", "docs/old.txt", "docs/new.txt")
    run_git(git_repo, "commit", "-q", "-m", "rename")
    assert check(gate(work, gate_cfg, reviewed_commit=head(git_repo)), "size-cap").detail == "10 lines (cap 400)"


def test_a_pure_rename_is_free_even_if_the_user_turned_rename_detection_off(git_repo, gate_cfg):
    commit_file(git_repo, "docs/old.txt", numbered(300, "doc"), message="a file on the base")
    run_git(git_repo, "push", "-q", "origin", "main")
    work = make_work(git_repo)
    run_git(git_repo, "config", "diff.renames", "false")
    run_git(git_repo, "mv", "docs/old.txt", "docs/new.txt")
    run_git(git_repo, "commit", "-q", "-m", "rename")
    assert check(gate(work, gate_cfg, reviewed_commit=head(git_repo)), "size-cap").detail == "10 lines (cap 400)"


def test_paths_with_spaces_and_non_ascii_names_are_counted_and_matched(work, gate_cfg):
    commit_file(work.repo, "gen/my file.g.cs", numbered(500), message="generated, with a space")
    commit_file(work.repo, "src/Ünï code.py", numbered(25), message="code, with an accent")
    cfg = dataclasses.replace(gate_cfg, generated=["gen/*"])
    result = check(gate(work, cfg, reviewed_commit=head(work.repo)), "size-cap")
    assert result.detail == "35 lines (cap 400)"


def test_a_generated_pattern_without_a_folder_matches_any_depth(work, gate_cfg):
    commit_file(work.repo, "deep/er/api.g.cs", numbered(500), message="generated")
    cfg = dataclasses.replace(gate_cfg, generated=["*.g.cs"])
    assert check(gate(work, cfg, reviewed_commit=head(work.repo)), "size-cap").detail == "10 lines (cap 400)"


def test_size_cap_ignores_the_work_folder(work, gate_cfg):
    commit_file(work.repo, "sw-factory/work/FEA-1-demo/spec.md", numbered(450, "spec"), message="FEA-1: spec")
    commit_file(work.repo, "src/big.py", numbered(40), message="more code")  # 10 + 40 = 50 product lines
    result = check(gate(work, gate_cfg, reviewed_commit=head(work.repo)), "size-cap")
    assert result.ok and result.detail == "50 lines (cap 400)"


def test_the_work_folder_is_left_out_whatever_generated_says(work, gate_cfg):
    commit_file(work.repo, "sw-factory/work/FEA-1-demo/plan.md", numbered(450, "plan"), message="FEA-1: plan")
    cfg = dataclasses.replace(gate_cfg, generated=[])
    assert check(gate(work, cfg, reviewed_commit=head(work.repo)), "size-cap").detail == "10 lines (cap 400)"


def test_only_the_root_work_folder_is_left_out_of_the_size(work, gate_cfg):
    commit_file(work.repo, "sw-factory/workshop/tool.py", numbered(100), message="not the work folder")
    commit_file(work.repo, "docs/sw-factory/work/note.md", numbered(100), message="not at the root")
    result = check(gate(work, gate_cfg, reviewed_commit=head(work.repo)), "size-cap")
    assert result.detail == "210 lines (cap 400)"


def test_a_file_renamed_into_the_work_folder_is_not_counted_there(git_repo, gate_cfg):
    commit_file(git_repo, "docs/design.md", numbered(300, "doc"), message="a doc on the base")
    run_git(git_repo, "push", "-q", "origin", "main")
    work = make_work(git_repo)
    (git_repo / "sw-factory" / "work" / "FEA-1-demo").mkdir(parents=True)
    run_git(git_repo, "mv", "docs/design.md", "sw-factory/work/FEA-1-demo/design.md")
    run_git(git_repo, "commit", "-q", "-m", "move the doc into the work folder")
    assert check(gate(work, gate_cfg, reviewed_commit=head(git_repo)), "size-cap").detail == "10 lines (cap 400)"


# --------------------------------------------------------------------------- id-unused-on-base

def test_id_unused_when_the_base_has_no_folder_for_it(work, gate_cfg):
    result = check(gate(work, gate_cfg), "id-unused-on-base")
    assert result.ok


def test_id_in_use_on_the_base_fails_and_suggests_the_next_free_id(work, gate_cfg):
    advance_base(work.repo, "sw-factory/work/FEA-1-other-thing/progress.md")
    result = check(gate(work, gate_cfg), "id-unused-on-base")
    assert not result.ok
    assert "FEA-1-other-thing" in result.detail and "FEA-1b" in result.detail


def test_the_items_own_folder_already_on_the_base_is_not_a_collision(work, gate_cfg):
    """An Epic merges once per milestone: from milestone 2 the base holds this item's own folder."""
    advance_base(work.repo, "sw-factory/work/FEA-1-demo/progress.md")  # exactly <ID>-<slug>
    result = check(gate(work, gate_cfg), "id-unused-on-base")
    assert result.ok, result.detail


def test_the_same_id_with_another_slug_on_the_base_fails(work, gate_cfg):
    advance_base(work.repo, "sw-factory/work/FEA-1-demo/progress.md")  # this item's own folder: exempt
    advance_base(work.repo, "sw-factory/work/FEA-1-demo-2/progress.md")  # the same id, another slug: a clash
    result = check(gate(work, gate_cfg), "id-unused-on-base")
    assert not result.ok
    assert "sw-factory/work/FEA-1-demo-2 " in result.detail and "sw-factory/work/FEA-1-demo " not in result.detail


def test_the_own_folder_exemption_needs_the_exact_name(work, gate_cfg):
    advance_base(work.repo, "sw-factory/work/FEA-1-Demo/progress.md")  # differs only in case
    assert not check(gate(work, gate_cfg), "id-unused-on-base").ok


def test_id_in_use_on_the_local_base_only_still_fails(work, gate_cfg):
    advance_base(work.repo, "sw-factory/work/FEA-1-other-thing/progress.md", push=False)
    assert not check(gate(work, gate_cfg), "id-unused-on-base").ok


def test_suggested_id_skips_letters_already_in_use(work, gate_cfg):
    advance_base(work.repo, "sw-factory/work/FEA-1-other-thing/progress.md")
    advance_base(work.repo, "sw-factory/work/FEA-1b-third/progress.md")
    result = check(gate(work, gate_cfg), "id-unused-on-base")
    assert not result.ok and "FEA-1c" in result.detail and "FEA-1b" not in result.detail


def test_other_ids_with_the_same_digits_or_a_letter_do_not_collide(work, gate_cfg):
    advance_base(work.repo, "sw-factory/work/FEA-12-x/progress.md")
    advance_base(work.repo, "sw-factory/work/FEA-1b-x/progress.md")
    advance_base(work.repo, "sw-factory/work/FIX-1-x/progress.md")
    assert check(gate(work, gate_cfg), "id-unused-on-base").ok


def test_the_items_own_folder_on_the_branch_is_not_a_collision(work, gate_cfg):
    commit_file(work.repo, "sw-factory/work/FEA-1-demo/progress.md", "Status: build\n", message="FEA-1: open work item")
    assert check(gate(work, gate_cfg, reviewed_commit=head(work.repo)), "id-unused-on-base").ok


def test_a_file_that_looks_like_a_work_folder_is_not_a_collision(work, gate_cfg):
    advance_base(work.repo, "sw-factory/work/FEA-1-notes", "just a file\n")
    assert check(gate(work, gate_cfg), "id-unused-on-base").ok


# --------------------------------------------------------------------------- verify-full

def test_verify_runs_the_project_command_and_reports_its_output(work, gate_cfg):
    result = check(gate(work, gate_cfg), "verify-full")
    assert result.ok and result.detail == "all green"


def test_verify_skipped_when_asked(work, gate_cfg):
    failing = dataclasses.replace(gate_cfg, verify_full=py("raise SystemExit(1)"))
    result = check(gate(work, failing, run_verify=False), "verify-full")
    assert result.ok and result.detail == "skipped"


def test_builds_ask_without_approval_fails(work, gate_cfg):
    cfg = dataclasses.replace(gate_cfg, builds="ask", verify_full=py("print('built'); raise SystemExit(1)"))
    result = check(gate(work, cfg), "verify-full")
    assert not result.ok and "ask the owner" in result.detail
    assert "built" not in result.detail  # the command was not run


def test_builds_ask_with_approval_runs_the_command(work, gate_cfg):
    cfg = dataclasses.replace(gate_cfg, builds="ask")
    result = check(gate(work, cfg, builds_approved=True), "verify-full")
    assert result.ok and result.detail == "all green"


def test_builds_ask_does_not_matter_when_verify_is_skipped(work, gate_cfg):
    cfg = dataclasses.replace(gate_cfg, builds="ask")
    assert check(gate(work, cfg, run_verify=False), "verify-full").detail == "skipped"


def test_builds_allow_ignores_the_approval_flag(work, gate_cfg):
    assert check(gate(work, gate_cfg, builds_approved=False), "verify-full").ok


def test_verify_failure_reports_tail(work, gate_cfg):
    cfg = dataclasses.replace(gate_cfg, verify_full=py("print('boom'); raise SystemExit(1)"))
    result = check(gate(work, cfg), "verify-full")
    assert not result.ok and "boom" in result.detail


def test_verify_detail_is_the_last_40_lines(work, gate_cfg):
    cfg = dataclasses.replace(gate_cfg, verify_full=py("print(*range(100), sep='\\n')"))
    result = check(gate(work, cfg), "verify-full")
    assert result.ok
    assert result.detail.splitlines() == [str(n) for n in range(60, 100)]


def test_verify_merges_stderr_into_the_detail(work, gate_cfg):
    code = "import sys; print('from stdout'); print('from stderr', file=sys.stderr); raise SystemExit(2)"
    result = check(gate(work, dataclasses.replace(gate_cfg, verify_full=py(code))), "verify-full")
    assert not result.ok
    assert result.detail.splitlines() == ["from stdout", "from stderr"]


def test_verify_without_output_says_the_exit_code(work, gate_cfg):
    result = check(gate(work, dataclasses.replace(gate_cfg, verify_full=py("raise SystemExit(3)"))), "verify-full")
    assert not result.ok and result.detail == "exit 3, no output"


def test_verify_output_that_is_not_utf8_is_replaced_not_fatal(work, gate_cfg):
    code = "import sys; sys.stdout.buffer.write(b'caf\\xe9 \\xff\\xfe boom\\n')"
    result = check(gate(work, dataclasses.replace(gate_cfg, verify_full=py(code))), "verify-full")
    assert result.ok and "boom" in result.detail and "�" in result.detail


def test_verify_runs_in_the_project_root(work, gate_cfg, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # not the repo
    cfg = dataclasses.replace(gate_cfg, verify_full=py("import os; print(os.getcwd())"))
    result = check(gate(work, cfg), "verify-full")
    assert Path(result.detail).resolve() == work.repo.resolve()


def test_verify_does_not_wait_for_input(work, gate_cfg):
    cfg = dataclasses.replace(gate_cfg, verify_full=py("import sys; print(repr(sys.stdin.read()))"))
    assert check(gate(work, cfg), "verify-full").detail == "''"


def test_a_command_the_shell_cannot_find_fails_the_check(work, gate_cfg):
    cfg = dataclasses.replace(gate_cfg, verify_full="no-such-command-for-the-gate-test")
    result = check(gate(work, cfg), "verify-full")
    assert not result.ok and result.detail


# --------------------------------------------------------------------------- spot-check

def test_spot_check_with_words_passes(work, gate_cfg):
    result = check(gate(work, gate_cfg), "spot-check")
    assert result.ok and SPOT in result.detail


@pytest.mark.parametrize("text", ["", "   ", "\n\t \n"])
def test_empty_spot_check_fails(work, gate_cfg, text):
    result = check(gate(work, gate_cfg, spot_check=text), "spot-check")
    assert not result.ok and result.detail


# --------------------------------------------------------------------------- report file

def report_with(*checks: GateCheck) -> GateReport:
    return GateReport(list(checks), all(c.ok for c in checks))


def test_report_file_first_line(tmp_path):
    failing = report_with(GateCheck("base-merged", True, "fine"), GateCheck("size-cap", False, "450 lines (cap 400); split the work"))
    path = write_gate_report(tmp_path, failing)
    assert path == tmp_path / "merge-gate.md"
    assert path.read_text(encoding="utf-8").splitlines()[0] == "GATE: FAIL"
    passing = write_gate_report(tmp_path, report_with(GateCheck("base-merged", True, "fine")))
    assert passing.read_text(encoding="utf-8").splitlines()[0] == "GATE: PASS"


def test_report_never_says_pass_over_a_failed_check(tmp_path):
    inconsistent = GateReport([GateCheck("size-cap", False, "450 lines (cap 400); split the work")], ok=True)
    assert write_gate_report(tmp_path, inconsistent).read_text(encoding="utf-8").splitlines()[0] == "GATE: FAIL"
    assert write_gate_report(tmp_path, GateReport([GateCheck("size-cap", True, "ok")], ok=False)).read_text(
        encoding="utf-8"
    ).splitlines()[0] == "GATE: FAIL"


def test_report_lists_one_line_per_check(tmp_path):
    report = report_with(
        GateCheck("base-merged", True, "HEAD contains origin/main"),
        GateCheck("size-cap", False, "450 lines (cap 400); split the work"),
    )
    text = write_gate_report(tmp_path, report).read_text(encoding="utf-8")
    assert text == (
        "GATE: FAIL\n"
        "- base-merged: ok — HEAD contains origin/main\n"
        "- size-cap: FAIL — 450 lines (cap 400); split the work\n"
    )


def test_report_indents_a_multi_line_detail_under_its_check(tmp_path):
    report = report_with(GateCheck("verify-full", False, "FAILED a\n\n  nested\nlast"), GateCheck("spot-check", True, "seen"))
    lines = write_gate_report(tmp_path, report).read_text(encoding="utf-8").splitlines()
    assert lines == [
        "GATE: FAIL",
        "- verify-full: FAIL — FAILED a",
        "",
        "    nested",
        "  last",
        "- spot-check: ok — seen",
    ]


def test_report_is_utf8_with_lf_and_no_bom(tmp_path):
    raw = write_gate_report(tmp_path, report_with(GateCheck("spot-check", True, "café\r\nsecond"))).read_bytes()
    assert b"\r" not in raw and not raw.startswith(b"\xef\xbb\xbf") and raw.endswith(b"\n")
    raw.decode("utf-8")


def test_report_replaces_an_earlier_one_and_leaves_no_temp_files(tmp_path):
    write_gate_report(tmp_path, report_with(GateCheck("spot-check", False, "first")))
    write_gate_report(tmp_path, report_with(GateCheck("spot-check", True, "second")))
    assert [p.name for p in tmp_path.iterdir()] == ["merge-gate.md"]
    assert (tmp_path / "merge-gate.md").read_text(encoding="utf-8").startswith("GATE: PASS\n")


def test_a_failed_report_write_leaves_no_temp_file(tmp_path):
    (tmp_path / "merge-gate.md").mkdir()  # a folder in the way: the rename cannot succeed
    with pytest.raises(OSError):
        write_gate_report(tmp_path, report_with(GateCheck("spot-check", True, "seen")))
    assert [p.name for p in tmp_path.iterdir()] == ["merge-gate.md"]


def test_report_for_a_real_run_names_every_check(work, gate_cfg, tmp_path):
    text = write_gate_report(tmp_path, gate(work, gate_cfg)).read_text(encoding="utf-8")
    assert text.splitlines()[0] == "GATE: PASS"
    assert [line.split(":")[0] for line in text.splitlines()[1:]] == [f"- {name}" for name in CHECK_NAMES]


# --------------------------------------------------------------------------- sw-factory gate

def yaml_string(text: str) -> str:
    return json.dumps(text)  # a JSON string is a YAML string


def write_sw_factory_yaml(repo: Path, verify_full: str = PASSING_VERIFY, builds: str = "allow") -> None:
    (repo / "sw-factory.yaml").write_text(
        "project: demo\n"
        "approvers: [owner]\n"
        f"builds: {builds}\n"
        "verify:\n"
        "  quick: pytest -q\n"
        f"  full: {yaml_string(verify_full)}\n",
        encoding="utf-8",
        newline="\n",
    )


@pytest.fixture
def opened(git_repo: Path, cfg, monkeypatch) -> Work:
    """A project with a sw-factory.yaml and a work item FEA-1 opened by ``create_work_item``, plus one commit
    after it. The current folder is the project."""
    write_sw_factory_yaml(git_repo)
    run_git(git_repo, "add", "sw-factory.yaml")
    run_git(git_repo, "commit", "-q", "-m", "Add sw-factory.yaml")
    run_git(git_repo, "push", "-q", "origin", "main")
    item = create_work_item(git_repo, cfg, "feature", "demo", "Demo")
    assert item.id == "FEA-1"
    commit_file(git_repo, "src/app.py", numbered(10), message="FEA-1: add the app")
    monkeypatch.chdir(git_repo)
    return Work(git_repo, item, head(git_repo))


def reconfigure(work: Work, **settings: str) -> None:
    """Change sw-factory.yaml and commit it (a modified tracked file would fail ``reviewed-commit``)."""
    write_sw_factory_yaml(work.repo, **settings)
    run_git(work.repo, "commit", "-q", "-a", "-m", "Change sw-factory.yaml")
    work.head = head(work.repo)


def pass_panel(work: Work, commit: str | None = None) -> None:
    write_panel_summary(work.item.path, "T1", 1, PanelResult("PASS", [], [], [], {}, commit or work.head))


def test_gate_is_a_registered_command():
    assert "gate" in cli.COMMANDS


def test_cli_gate_passes_and_writes_the_report(opened, capsys):
    pass_panel(opened)
    assert cli.main(["gate", "FEA-1", "--spot-check", SPOT]) == 0
    out = capsys.readouterr().out.splitlines()
    report = opened.item.path / "merge-gate.md"
    assert len(out) == 2 and Path(out[0]).resolve() == report.resolve() and out[1] == "GATE: PASS"
    assert report.read_text(encoding="utf-8").splitlines()[0] == "GATE: PASS"


def test_cli_gate_fail_exits_1_and_says_so(opened, capsys):
    pass_panel(opened)
    assert cli.main(["gate", "FEA-1", "--spot-check", "  "]) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[-1] == "GATE: FAIL"
    assert (opened.item.path / "merge-gate.md").read_text(encoding="utf-8").splitlines()[0] == "GATE: FAIL"


def test_cli_gate_without_a_passing_panel_summary_exits_1(opened, capsys):
    write_panel_summary(opened.item.path, "T1", 1, PanelResult("CHANGES", [], [], [], {}, opened.head))
    assert cli.main(["gate", "FEA-1", "--spot-check", SPOT]) == 1
    captured = capsys.readouterr()
    assert "no passing panel summary found" in captured.err
    assert not (opened.item.path / "merge-gate.md").exists()


def test_cli_gate_defaults_to_the_commit_of_the_newest_passing_summary(opened, capsys):
    pass_panel(opened, commit=opened.head)
    commit_file(opened.repo, "src/late.py", message="after the review")
    assert cli.main(["gate", "FEA-1", "--spot-check", SPOT]) == 1
    report = (opened.item.path / "merge-gate.md").read_text(encoding="utf-8")
    assert "- reviewed-commit: FAIL" in report


def test_cli_gate_reviewed_commit_option_replaces_the_panel_summary(opened, capsys):
    # no panel summary at all: the owner names the commit
    assert cli.main(["gate", "FEA-1", "--spot-check", SPOT, "--reviewed-commit", opened.head]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "GATE: PASS"
    pass_panel(opened)
    assert cli.main(["gate", "FEA-1", "--spot-check", SPOT, "--reviewed-commit", "0" * 40]) == 1


def test_cli_gate_no_verify_skips_the_full_command(opened, capsys):
    reconfigure(opened, verify_full=py("raise SystemExit(1)"))
    pass_panel(opened)
    assert cli.main(["gate", "FEA-1", "--spot-check", SPOT]) == 1
    assert cli.main(["gate", "FEA-1", "--spot-check", SPOT, "--no-verify"]) == 0
    assert "- verify-full: ok — skipped" in (opened.item.path / "merge-gate.md").read_text(encoding="utf-8")


def test_cli_gate_builds_ask_needs_the_flag(opened, capsys):
    reconfigure(opened, builds="ask")
    pass_panel(opened)
    assert cli.main(["gate", "FEA-1", "--spot-check", SPOT]) == 1
    assert "ask the owner" in (opened.item.path / "merge-gate.md").read_text(encoding="utf-8")
    assert cli.main(["gate", "FEA-1", "--spot-check", SPOT, "--builds-approved"]) == 0


def test_cli_gate_passes_after_the_factory_committed_its_bookkeeping(opened, capsys):
    """The whole loop: the panel passes at a commit, the summary and a progress note are committed, then the gate runs."""
    pass_panel(opened)  # reviews/T1-r1-panel.md names the commit that was reviewed
    write_status(opened.item.path, "build", "reviewed", "run the gate", "panel passed")
    run_git(opened.repo, "add", "--", "sw-factory/work")
    run_git(opened.repo, "commit", "-q", "-m", "FEA-1: panel summary and progress")
    assert head(opened.repo) != opened.head
    assert cli.main(["gate", "FEA-1", "--spot-check", SPOT]) == 0
    report = (opened.item.path / "merge-gate.md").read_text(encoding="utf-8")
    assert "- reviewed-commit: ok — HEAD is 1 commit(s) after the reviewed commit" in report


def test_cli_gate_fails_on_uncommitted_changes_to_a_tracked_file(opened, capsys):
    pass_panel(opened)
    (opened.repo / "src" / "app.py").write_text("changed after the review\n", encoding="utf-8", newline="\n")
    assert cli.main(["gate", "FEA-1", "--spot-check", SPOT]) == 1
    report = (opened.item.path / "merge-gate.md").read_text(encoding="utf-8")
    assert report.splitlines()[0] == "GATE: FAIL"
    assert "- reviewed-commit: FAIL" in report and "src/app.py" in report


def test_cli_gate_finds_the_project_from_a_subfolder(opened, monkeypatch, capsys):
    pass_panel(opened)
    monkeypatch.chdir(opened.item.path)
    assert cli.main(["gate", "FEA-1", "--spot-check", SPOT]) == 0


def test_cli_gate_unknown_work_id_exits_2(opened, capsys):
    assert cli.main(["gate", "FEA-99", "--spot-check", SPOT]) == 2
    assert "FEA-99" in capsys.readouterr().err


def test_cli_gate_bad_work_id_exits_2(opened, capsys):
    assert cli.main(["gate", "nonsense", "--spot-check", SPOT]) == 2
    assert "nonsense" in capsys.readouterr().err


def test_cli_gate_without_a_project_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["gate", "FEA-1", "--spot-check", SPOT]) == 2
    assert "sw-factory.yaml" in capsys.readouterr().err


def test_cli_gate_with_a_broken_sw_factory_yaml_exits_2(opened, capsys):
    (opened.repo / "sw-factory.yaml").write_text("project: demo\n", encoding="utf-8")
    assert cli.main(["gate", "FEA-1", "--spot-check", SPOT]) == 2
    assert "approvers" in capsys.readouterr().err


def test_cli_gate_requires_a_spot_check(opened):
    with pytest.raises(SystemExit) as caught:
        cli.main(["gate", "FEA-1"])
    assert caught.value.code == 2


def test_cli_gate_accepts_a_spot_check_that_starts_with_a_dash(opened, capsys):
    pass_panel(opened)
    assert cli.main(["gate", "FEA-1", "--spot-check=- checked AC1 by hand"]) == 0
