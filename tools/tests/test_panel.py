"""Tests for swfactory.panel (combining lens reviews into one verdict) and the ``factory panel`` command."""
from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from swfactory import cli
from swfactory.panel import (
    Finding,
    PanelResult,
    combine,
    latest_passing_commit,
    render_panel_summary,
    review_path,
    write_panel_summary,
)
from swfactory.verdict import panel_path

HEADER = "| ID | Severity | Location | Finding | Fix |\n|---|---|---|---|---|\n"
PASS = "VERDICT: PASS\n"
STAMP = (
    "<!-- factory: role=lens-security vendor=openai model=gpt-6.1-sol effort=xhigh "
    "sandbox=read-only at=2026-10-20T12:00:00Z -->"
)


def changes(*rows: str) -> str:
    return "VERDICT: CHANGES\n## Findings\n" + HEADER + "".join(row + "\n" for row in rows)


def passing(*rows: str) -> str:
    return "VERDICT: PASS\n## Findings\n" + HEADER + "".join(row + "\n" for row in rows)


def row(id_: str, severity: str, summary: str = "it is broken", where: str = "a.py:1", fix: str = "fix it") -> str:
    return f"| {id_} | {severity} | {where} | {summary} | {fix} |"


def write_review(work: Path, task: str, rnd: int, lens: str, text: str | bytes) -> Path:
    path = review_path(work, task, rnd, lens)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    return path


FIX_LENSES = ["correctness", "security"]
FEATURE_LENSES = ["correctness", "security", "maintainability"]


# --------------------------------------------------------------------------- combine

def test_combine_all_lenses_pass(tmp_path):
    write_review(tmp_path, "T1", 1, "correctness", PASS)
    write_review(tmp_path, "T1", 1, "security", PASS)
    result = combine(tmp_path, "T1", 1, FIX_LENSES, "abc123")
    assert result == PanelResult("PASS", [], [], [], {}, "abc123", [])


def test_combine_missing_lens_incomplete(tmp_path):
    write_review(tmp_path, "T1", 1, "correctness", PASS)
    result = combine(tmp_path, "T1", 1, FIX_LENSES, "abc")
    assert result.verdict == "INCOMPLETE" and result.missing == ["security"] and result.invalid == {}


def test_combine_invalid_lens_incomplete(tmp_path):
    write_review(tmp_path, "T1", 1, "correctness", PASS)
    write_review(tmp_path, "T1", 1, "security", "oops")
    result = combine(tmp_path, "T1", 1, FIX_LENSES, "abc")
    assert result.verdict == "INCOMPLETE" and "security" in result.invalid and result.missing == []
    assert "T1-r1-security.md" in result.invalid["security"] and "VERDICT" in result.invalid["security"]


def test_combine_empty_file_is_invalid_not_a_pass(tmp_path):
    write_review(tmp_path, "T1", 1, "correctness", PASS)
    write_review(tmp_path, "T1", 1, "security", b"")
    assert combine(tmp_path, "T1", 1, FIX_LENSES, "abc").verdict == "INCOMPLETE"


def test_combine_findings_written_as_a_bullet_list_are_invalid_not_a_pass(tmp_path):
    write_review(tmp_path, "T1", 1, "correctness", "VERDICT: PASS\n## Findings\n- R1 major a.py:1 the loop never ends\n")
    write_review(tmp_path, "T1", 1, "security", PASS)
    result = combine(tmp_path, "T1", 1, FIX_LENSES, "abc")
    assert result.verdict == "INCOMPLETE" and result.missing == [] and result.blocking == []
    assert list(result.invalid) == ["correctness"]
    assert "T1-r1-correctness.md" in result.invalid["correctness"] and "write a table" in result.invalid["correctness"]


def test_combine_file_that_is_not_utf8_is_invalid(tmp_path):
    write_review(tmp_path, "T1", 1, "correctness", PASS)
    write_review(tmp_path, "T1", 1, "security", b"VERDICT: PASS\n\xff\xfe")
    result = combine(tmp_path, "T1", 1, FIX_LENSES, "abc")
    assert result.verdict == "INCOMPLETE" and "UTF-8" in result.invalid["security"]


def test_combine_a_review_path_that_is_a_folder_is_invalid(tmp_path):
    write_review(tmp_path, "T1", 1, "correctness", PASS)
    review_path(tmp_path, "T1", 1, "security").mkdir()
    result = combine(tmp_path, "T1", 1, FIX_LENSES, "abc")
    assert result.verdict == "INCOMPLETE" and "security" in result.invalid


def test_combine_every_lens_missing(tmp_path):
    result = combine(tmp_path, "T1", 1, FIX_LENSES, "abc")
    assert (result.verdict, result.missing) == ("INCOMPLETE", ["correctness", "security"])


def test_combine_major_blocks(tmp_path):
    write_review(tmp_path, "T1", 1, "correctness", changes(row("R1", "major")))
    write_review(tmp_path, "T1", 1, "security", PASS)
    result = combine(tmp_path, "T1", 1, FIX_LENSES, "abc")
    assert result.verdict == "CHANGES" and result.blocking[0][0] == "correctness"
    assert result.blocking == [("correctness", Finding("R1", "major", "a.py:1", "it is broken", "fix it"))]


def test_combine_blocker_blocks(tmp_path):
    write_review(tmp_path, "T1", 1, "correctness", PASS)
    write_review(tmp_path, "T1", 1, "security", changes(row("R1", "blocker")))
    assert combine(tmp_path, "T1", 1, FIX_LENSES, "abc").verdict == "CHANGES"


def test_combine_blocking_lists_follow_lens_order_then_file_order(tmp_path):
    write_review(tmp_path, "T1", 1, "correctness", changes(row("R1", "major"), row("R2", "blocker")))
    write_review(tmp_path, "T1", 1, "security", changes(row("R1", "major", where="b.py:2")))
    result = combine(tmp_path, "T1", 1, ["security", "correctness"], "abc")
    assert [(lens, f.id) for lens, f in result.blocking] == [("security", "R1"), ("correctness", "R1"), ("correctness", "R2")]


def test_minor_and_nit_findings_do_not_block(tmp_path):
    write_review(tmp_path, "T1", 1, "correctness", passing(row("R1", "minor"), row("R2", "nit")))
    write_review(tmp_path, "T1", 1, "security", PASS)
    result = combine(tmp_path, "T1", 1, FIX_LENSES, "abc")
    assert (result.verdict, result.blocking, result.follow_ups) == ("PASS", [], [])


def test_a_pass_verdict_with_a_major_finding_still_blocks(tmp_path):
    write_review(tmp_path, "T1", 1, "correctness", passing(row("R1", "major")))
    write_review(tmp_path, "T1", 1, "security", PASS)
    result = combine(tmp_path, "T1", 1, FIX_LENSES, "abc")
    assert result.verdict == "CHANGES" and len(result.blocking) == 1


def test_changes_without_a_blocker_or_major_warns_and_is_still_changes(tmp_path):
    write_review(tmp_path, "T1", 1, "correctness", changes(row("R1", "minor")))
    write_review(tmp_path, "T1", 1, "security", PASS)
    result = combine(tmp_path, "T1", 1, FIX_LENSES, "abc")
    assert result.verdict == "CHANGES" and result.blocking == []
    assert len(result.warnings) == 1 and result.warnings[0].startswith("correctness:")


def test_changes_with_no_findings_at_all_warns_and_is_still_changes(tmp_path):
    write_review(tmp_path, "T1", 1, "correctness", "VERDICT: CHANGES\n")
    write_review(tmp_path, "T1", 1, "security", PASS)
    result = combine(tmp_path, "T1", 1, FIX_LENSES, "abc")
    assert result.verdict == "CHANGES" and len(result.warnings) == 1


def test_changes_with_a_major_finding_raises_no_warning(tmp_path):
    write_review(tmp_path, "T1", 1, "correctness", changes(row("R1", "major")))
    write_review(tmp_path, "T1", 1, "security", PASS)
    assert combine(tmp_path, "T1", 1, FIX_LENSES, "abc").warnings == []


def test_maintainability_major_becomes_follow_up(tmp_path):
    write_review(tmp_path, "T1", 1, "correctness", PASS)
    write_review(tmp_path, "T1", 1, "security", PASS)
    write_review(tmp_path, "T1", 1, "maintainability", changes(row("R1", "major")))
    result = combine(tmp_path, "T1", 1, FEATURE_LENSES, "abc")
    assert result.verdict == "PASS" and result.blocking == []
    assert result.follow_ups == [("maintainability", Finding("R1", "major", "a.py:1", "it is broken", "fix it"))]


def test_every_maintainability_finding_is_a_follow_up_whatever_its_severity(tmp_path):
    write_review(tmp_path, "T1", 1, "correctness", PASS)
    write_review(tmp_path, "T1", 1, "security", PASS)
    write_review(tmp_path, "T1", 1, "maintainability",
                 changes(row("R1", "blocker"), row("R2", "major"), row("R3", "minor"), row("R4", "nit")))
    result = combine(tmp_path, "T1", 1, FEATURE_LENSES, "abc")
    assert result.verdict == "PASS" and [f.id for _, f in result.follow_ups] == ["R1", "R2", "R3", "R4"]


def test_maintainability_changes_with_only_minor_findings_does_not_block(tmp_path):
    write_review(tmp_path, "T1", 1, "correctness", PASS)
    write_review(tmp_path, "T1", 1, "security", PASS)
    write_review(tmp_path, "T1", 1, "maintainability", changes(row("R1", "minor")))
    result = combine(tmp_path, "T1", 1, FEATURE_LENSES, "abc")
    assert result.verdict == "PASS" and len(result.follow_ups) == 1 and len(result.warnings) == 1


def test_other_lenses_still_block_next_to_maintainability(tmp_path):
    write_review(tmp_path, "T1", 1, "correctness", changes(row("R1", "major")))
    write_review(tmp_path, "T1", 1, "security", PASS)
    write_review(tmp_path, "T1", 1, "maintainability", changes(row("R1", "major")))
    result = combine(tmp_path, "T1", 1, FEATURE_LENSES, "abc")
    assert result.verdict == "CHANGES" and len(result.blocking) == 1 and len(result.follow_ups) == 1


def test_a_project_lens_blocks_like_any_other_non_maintainability_lens(tmp_path):
    write_review(tmp_path, "T1", 1, "correctness", PASS)
    write_review(tmp_path, "T1", 1, "ux", changes(row("R1", "major")))
    assert combine(tmp_path, "T1", 1, ["correctness", "ux"], "abc").verdict == "CHANGES"


def test_incomplete_wins_over_changes_but_keeps_what_was_found(tmp_path):
    write_review(tmp_path, "T1", 1, "correctness", changes(row("R1", "major")))
    result = combine(tmp_path, "T1", 1, FIX_LENSES, "abc")
    assert result.verdict == "INCOMPLETE" and result.missing == ["security"] and len(result.blocking) == 1


def test_combine_reads_only_the_named_task_and_round(tmp_path):
    write_review(tmp_path, "T1", 1, "correctness", changes(row("R1", "major")))
    write_review(tmp_path, "T1", 1, "security", changes(row("R1", "major")))
    write_review(tmp_path, "T1", 2, "correctness", PASS)
    write_review(tmp_path, "T1", 2, "security", PASS)
    write_review(tmp_path, "T2", 2, "correctness", changes(row("R1", "major")))
    assert combine(tmp_path, "T1", 2, FIX_LENSES, "abc").verdict == "PASS"


def test_combine_reads_stamped_markdown_and_stamped_json_files(tmp_path):
    write_review(tmp_path, "T1", 1, "correctness", changes(row("R1", "major")) + "\n" + STAMP + "\n")
    as_json = {"verdict": "PASS", "findings": [], "prior": [{"id": "R1", "status": "fixed"}]}
    write_review(tmp_path, "T1", 1, "security", json.dumps(as_json) + "\n" + STAMP + "\n")
    result = combine(tmp_path, "T1", 1, FIX_LENSES, "abc")
    assert result.verdict == "CHANGES" and result.invalid == {} and len(result.blocking) == 1


def test_combine_reads_crlf_and_bom_files(tmp_path):
    text = "﻿" + changes(row("R1", "major")).replace("\n", "\r\n")
    write_review(tmp_path, "T1", 1, "correctness", text)
    write_review(tmp_path, "T1", 1, "security", "﻿VERDICT: PASS\r\n")
    assert combine(tmp_path, "T1", 1, FIX_LENSES, "abc").verdict == "CHANGES"


def test_combine_carries_parse_warnings_with_the_lens_name(tmp_path):
    nits = "".join(row(f"R{i}", "nit") + "\n" for i in range(1, 4))
    write_review(tmp_path, "T1", 1, "correctness", "VERDICT: PASS\n## Findings\n" + HEADER + nits)
    write_review(tmp_path, "T1", 1, "security", PASS)
    assert combine(tmp_path, "T1", 1, FIX_LENSES, "abc").warnings == []
    result = combine(tmp_path, "T1", 1, FIX_LENSES, "abc", nit_cap=2)
    assert len(result.warnings) == 1 and result.warnings[0].startswith("correctness:") and "3 nits" in result.warnings[0]


def test_combine_a_repeated_lens_counts_once(tmp_path):
    write_review(tmp_path, "T1", 1, "correctness", changes(row("R1", "major")))
    result = combine(tmp_path, "T1", 1, ["correctness", "correctness"], "abc")
    assert len(result.blocking) == 1


def test_combine_without_lenses_is_an_error_not_a_pass(tmp_path):
    with pytest.raises(ValueError, match="lens"):
        combine(tmp_path, "T1", 1, [], "abc")


# --------------------------------------------------------------------------- summary

def result_with_everything() -> PanelResult:
    return PanelResult(
        verdict="CHANGES",
        blocking=[("correctness", Finding("R1", "major", "a.py:1", "it is broken", "fix it"))],
        follow_ups=[("maintainability", Finding("R2", "nit", "b.py:2", "name", "rename"))],
        missing=[],
        invalid={},
        commit="abc123",
    )


def test_summary_records_commit_and_latest_passing(tmp_path):
    write_panel_summary(tmp_path, "T1", 1, PanelResult("PASS", [], [], [], {}, "abc"))
    assert latest_passing_commit(tmp_path) == "abc"


def test_summary_layout(tmp_path):
    path = write_panel_summary(tmp_path, "T1", 2, result_with_everything())
    assert path == panel_path(tmp_path, "T1", 2) == tmp_path / "reviews" / "T1-r2-panel.md"
    raw = path.read_bytes()
    assert b"\r" not in raw and raw.endswith(b"\n")
    lines = raw.decode("utf-8").splitlines()
    assert lines[0] == "VERDICT: CHANGES" and lines[1] == "Commit: abc123" and lines[2] == ""
    assert lines.index("## Blocking") < lines.index("## Follow-ups") < lines.index("## Missing")
    assert "| correctness | R1 | major | a.py:1 | it is broken | fix it |" in lines
    assert "| maintainability | R2 | nit | b.py:2 | name | rename |" in lines


def test_summary_without_findings_says_none(tmp_path):
    text = write_panel_summary(tmp_path, "T1", 1, PanelResult("PASS", [], [], [], {}, "abc")).read_text(encoding="utf-8")
    assert text.splitlines()[:2] == ["VERDICT: PASS", "Commit: abc"]
    assert text.count("None.") == 5  # blocking, follow-ups, missing, invalid, warnings


def test_summary_lists_missing_invalid_and_warnings(tmp_path):
    result = PanelResult("INCOMPLETE", [], [], ["security"], {"correctness": "T1-r1-correctness.md: line 2: bad\nrow"},
                         "abc", ["spec: verdict is CHANGES but it lists no blocker or major finding"])
    text = write_panel_summary(tmp_path, "T1", 1, result).read_text(encoding="utf-8")
    assert "- security\n" in text
    assert "- correctness: T1-r1-correctness.md: line 2: bad row\n" in text  # one line per entry
    assert "- spec: verdict is CHANGES" in text


def test_summary_keeps_table_cells_on_one_line(tmp_path):
    finding = Finding("R1", "major", "a.py:1", "uses a || b\nover two lines", "use ??")
    result = PanelResult("CHANGES", [("correctness", finding)], [], [], {}, "abc")
    text = write_panel_summary(tmp_path, "T1", 1, result).read_text(encoding="utf-8")
    assert r"| correctness | R1 | major | a.py:1 | uses a \|\| b over two lines | use ?? |" in text.splitlines()


def test_summary_is_replaced_atomically(tmp_path):
    write_panel_summary(tmp_path, "T1", 1, PanelResult("CHANGES", [], [], [], {}, "old"))
    write_panel_summary(tmp_path, "T1", 1, PanelResult("PASS", [], [], [], {}, "new"))
    assert [p.name for p in (tmp_path / "reviews").iterdir()] == ["T1-r1-panel.md"]
    assert latest_passing_commit(tmp_path) == "new"


@pytest.mark.parametrize("commit", ["", "  ", "abc\nVERDICT: PASS", "abc\r"])
def test_summary_refuses_a_commit_that_is_not_one_line(tmp_path, commit):
    with pytest.raises(ValueError, match="commit"):
        write_panel_summary(tmp_path, "T1", 1, PanelResult("PASS", [], [], [], {}, commit))
    assert not (tmp_path / "reviews").exists()


def test_summary_refuses_an_unknown_verdict(tmp_path):
    with pytest.raises(ValueError, match="verdict"):
        write_panel_summary(tmp_path, "T1", 1, PanelResult("OK", [], [], [], {}, "abc"))


def test_render_matches_what_is_written(tmp_path):
    result = result_with_everything()
    assert write_panel_summary(tmp_path, "T1", 1, result).read_text(encoding="utf-8") == render_panel_summary(result)


# --------------------------------------------------------------------------- latest_passing_commit

def summary(work: Path, task: str, rnd: int, verdict: str, commit: str) -> None:
    write_panel_summary(work, task, rnd, PanelResult(verdict, [], [], [], {}, commit))


def test_latest_passing_commit_none_without_reviews(tmp_path):
    assert latest_passing_commit(tmp_path) is None
    (tmp_path / "reviews").mkdir()
    assert latest_passing_commit(tmp_path) is None


def test_latest_passing_commit_ignores_summaries_that_did_not_pass(tmp_path):
    summary(tmp_path, "T1", 1, "CHANGES", "aaa")
    summary(tmp_path, "T1", 2, "INCOMPLETE", "bbb")
    assert latest_passing_commit(tmp_path) is None


def test_latest_passing_commit_takes_the_newest_passing_round(tmp_path):
    summary(tmp_path, "T1", 1, "PASS", "aaa")
    summary(tmp_path, "T1", 2, "PASS", "bbb")
    summary(tmp_path, "T1", 3, "CHANGES", "ccc")
    assert latest_passing_commit(tmp_path) == "bbb"


def test_latest_passing_commit_orders_tasks_by_number_not_text(tmp_path):
    summary(tmp_path, "T9", 1, "PASS", "nine")
    summary(tmp_path, "T10", 1, "PASS", "ten")
    summary(tmp_path, "T2", 3, "PASS", "two")
    assert latest_passing_commit(tmp_path) == "ten"


def test_latest_passing_commit_orders_milestone_tasks(tmp_path):
    summary(tmp_path, "M1-T3", 1, "PASS", "m1t3")
    summary(tmp_path, "M2-T1", 1, "PASS", "m2t1")
    summary(tmp_path, "M1-T3a", 1, "PASS", "m1t3a")
    assert latest_passing_commit(tmp_path) == "m2t1"
    summary(tmp_path, "M2-T1", 2, "CHANGES", "later")
    assert latest_passing_commit(tmp_path) == "m2t1"


def test_latest_passing_commit_skips_files_that_are_not_valid_summaries(tmp_path):
    summary(tmp_path, "T1", 1, "PASS", "good")
    reviews = tmp_path / "reviews"
    (reviews / "T2-r1-panel.md").write_text("VERDICT: PASS\n", encoding="utf-8")  # no Commit line
    (reviews / "T3-r1-panel.md").write_text("VERDICT: PASS\nCommit:\n", encoding="utf-8")
    (reviews / "T4-r1-panel.md").write_text("nonsense", encoding="utf-8")
    (reviews / "T5-r1-panel.md").write_bytes(b"VERDICT: PASS\nCommit: \xff\n")
    (reviews / "T6-r1-correctness.md").write_text("VERDICT: PASS\nCommit: not-a-panel\n", encoding="utf-8")
    (reviews / "notes-panel.md").write_text("VERDICT: PASS\nCommit: not-a-round\n", encoding="utf-8")
    assert latest_passing_commit(tmp_path) == "good"


def test_latest_passing_commit_reads_a_hand_edited_summary(tmp_path):
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    (reviews / "T1-r1-panel.md").write_bytes(b"\xef\xbb\xbfVERDICT: PASS\r\nCommit: abc\r\n")
    assert latest_passing_commit(tmp_path) == "abc"


# --------------------------------------------------------------------------- the factory panel command

REVIEW_MD = """\
---
lenses: [correctness, security, maintainability, spec]
tracks:
  fix: [correctness, security]
  feature: [correctness, security, maintainability]
  program: [correctness, security, maintainability, spec]
nit_cap: %d
---
## Lens: correctness
- logic
## Lens: security
- inputs
## Lens: maintainability
- naming
## Lens: spec
- requirements
"""


def make_project(tmp_path: Path, work_name: str = "FIX-1-demo", nit_cap: int = 5) -> Path:
    root = tmp_path / "proj"
    (root / "factory" / "work" / work_name).mkdir(parents=True)
    (root / "factory.yaml").write_text("project: demo\n", encoding="utf-8")
    (root / "REVIEW.md").write_text(REVIEW_MD % nit_cap, encoding="utf-8")
    return root / "factory" / "work" / work_name


def run_panel(work: Path, *extra: str, task: str = "T1", rnd: str = "1") -> int:
    return cli.main(["panel", "combine", str(work), "--task", task, "--round", rnd, *extra])


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def test_cli_commands_include_panel():
    assert "panel" in cli.COMMANDS


def test_cli_panel_pass_exits_0(tmp_path, capsys):
    work = make_project(tmp_path)
    write_review(work, "T1", 1, "correctness", PASS)
    write_review(work, "T1", 1, "security", PASS)
    assert run_panel(work, "--commit", "abc123") == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "VERDICT: PASS" and "T1-r1-panel.md" in out
    assert latest_passing_commit(work) == "abc123"


def test_cli_panel_changes_exits_1(tmp_path, capsys):
    work = make_project(tmp_path)
    write_review(work, "T1", 1, "correctness", changes(row("R1", "major")))
    write_review(work, "T1", 1, "security", PASS)
    assert run_panel(work, "--commit", "abc") == 1
    assert capsys.readouterr().out.splitlines()[0] == "VERDICT: CHANGES"
    assert panel_path(work, "T1", 1).read_text(encoding="utf-8").startswith("VERDICT: CHANGES\nCommit: abc\n")


def test_cli_panel_incomplete_exits_2(tmp_path, capsys):
    work = make_project(tmp_path)
    write_review(work, "T1", 1, "correctness", PASS)
    assert run_panel(work, "--commit", "abc") == 2
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "VERDICT: INCOMPLETE" and "- security" in out


def test_cli_panel_uses_the_lens_set_of_the_work_items_track(tmp_path):
    work = make_project(tmp_path, "FEA-2-thing")
    write_review(work, "T1", 1, "correctness", PASS)
    write_review(work, "T1", 1, "security", PASS)
    assert run_panel(work, "--commit", "abc") == 2  # a feature also needs maintainability
    write_review(work, "T1", 1, "maintainability", changes(row("R1", "major")))
    assert run_panel(work, "--commit", "abc") == 0  # and its findings are follow-ups, not blockers
    assert "| maintainability | R1 | major |" in panel_path(work, "T1", 1).read_text(encoding="utf-8")


def test_cli_panel_honours_the_projects_nit_cap(tmp_path, capsys):
    work = make_project(tmp_path, nit_cap=1)
    write_review(work, "T1", 1, "correctness", passing(row("R1", "nit"), row("R2", "nit")))
    write_review(work, "T1", 1, "security", PASS)
    assert run_panel(work, "--commit", "abc") == 0
    assert "2 nits" in capsys.readouterr().out


def test_cli_panel_commit_defaults_to_the_full_sha_of_head(tmp_path):
    work = make_project(tmp_path)
    root = tmp_path / "proj"
    git(root, "init", "-q")
    git(root, "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
        "commit", "--allow-empty", "-q", "-m", "first")
    sha = git(root, "rev-parse", "HEAD")
    write_review(work, "T1", 1, "correctness", PASS)
    write_review(work, "T1", 1, "security", PASS)
    assert run_panel(work) == 0
    assert len(sha) in (40, 64) and latest_passing_commit(work) == sha


def test_cli_panel_without_a_commit_outside_git_is_an_error(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    work = make_project(tmp_path)
    write_review(work, "T1", 1, "correctness", PASS)
    write_review(work, "T1", 1, "security", PASS)
    assert run_panel(work) == 2
    assert "HEAD" in capsys.readouterr().err
    assert not panel_path(work, "T1", 1).exists()


@pytest.mark.parametrize("work_name", ["notes", "factory", "fix-3"])
def test_cli_panel_needs_a_work_folder_name(tmp_path, capsys, work_name):
    work = make_project(tmp_path, work_name)
    assert run_panel(work, "--commit", "abc") == 2
    assert "work id" in capsys.readouterr().err


def test_cli_panel_names_the_work_folder_through_dots_in_the_path(tmp_path):
    work = make_project(tmp_path)
    write_review(work, "T1", 1, "correctness", PASS)
    write_review(work, "T1", 1, "security", PASS)
    assert run_panel(work / ".." / work.name, "--commit", "abc") == 0


def test_cli_panel_outside_a_project_is_an_error(tmp_path, capsys):
    work = tmp_path / "FIX-1-demo"
    work.mkdir()
    assert run_panel(work, "--commit", "abc") == 2
    assert "factory.yaml" in capsys.readouterr().err


def test_cli_panel_missing_review_md_is_an_error(tmp_path, capsys):
    work = make_project(tmp_path)
    (tmp_path / "proj" / "REVIEW.md").unlink()
    assert run_panel(work, "--commit", "abc") == 2
    assert "REVIEW.md" in capsys.readouterr().err


def test_cli_panel_track_missing_from_review_md_is_an_error(tmp_path, capsys):
    work = make_project(tmp_path)
    (tmp_path / "proj" / "REVIEW.md").write_text(
        "---\nlenses: [correctness]\ntracks: {feature: [correctness]}\n---\n## Lens: correctness\n- x\n", encoding="utf-8")
    assert run_panel(work, "--commit", "abc") == 2
    assert "fix" in capsys.readouterr().err


def test_cli_panel_a_work_folder_that_does_not_exist_is_an_error_and_creates_nothing(tmp_path, capsys):
    make_project(tmp_path)
    missing = tmp_path / "proj" / "factory" / "work" / "FIX-9-gone"
    assert run_panel(missing, "--commit", "abc") == 2
    assert not missing.exists() and "FIX-9-gone" in capsys.readouterr().err


def test_cli_panel_a_commit_that_is_not_one_line_is_an_error(tmp_path, capsys):
    work = make_project(tmp_path)
    write_review(work, "T1", 1, "correctness", PASS)
    write_review(work, "T1", 1, "security", PASS)
    assert run_panel(work, "--commit", "abc\ndef") == 2
    assert "commit" in capsys.readouterr().err and not panel_path(work, "T1", 1).exists()


def test_cli_panel_bad_round_is_an_error(tmp_path, capsys):
    work = make_project(tmp_path)
    assert run_panel(work, "--commit", "abc", rnd="0") == 2
    assert "round" in capsys.readouterr().err


def test_cli_panel_prints_even_when_the_console_cannot_show_a_character(tmp_path, monkeypatch):
    work = make_project(tmp_path)
    write_review(work, "T1", 1, "correctness", changes(row("R1", "major", summary="loops forever → hang")))
    write_review(work, "T1", 1, "security", PASS)
    raw = io.BytesIO()
    console = io.TextIOWrapper(raw, encoding="ascii", write_through=True)
    monkeypatch.setattr(sys, "stdout", console)
    assert run_panel(work, "--commit", "abc") == 1  # the exit code carries the verdict; printing must not crash
    assert "loops forever ? hang" in raw.getvalue().decode("ascii")
    assert "→" in panel_path(work, "T1", 1).read_text(encoding="utf-8")  # the file keeps the real text


def test_cli_panel_without_a_subcommand_prints_help(capsys):
    assert cli.main(["panel"]) == 2
    assert "combine" in capsys.readouterr().out
