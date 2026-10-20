"""Tests for swfactory.approvals and the ``factory approve`` command.

Nothing here needs git: an approval is a file in a work folder, and the command only has to find the
project (a ``factory.yaml``) and the work folder around it.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
from pathlib import Path

import pytest

from swfactory import approvals, cli
from swfactory.approvals import Approval, ApprovalError, latest_approval, record_approval
from swfactory.config import ProjectConfig

UTC_STAMP = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"


# --------------------------------------------------------------------------- helpers

@pytest.fixture
def owner_cfg(cfg: ProjectConfig) -> ProjectConfig:
    """The project with two people who may approve."""
    return dataclasses.replace(cfg, approvers=["Cord", "Dana Fox"])


@pytest.fixture
def wd(tmp_path: Path) -> Path:
    """A work folder as ``factory work new`` leaves it: ``approvals/`` holds only a ``.gitkeep``."""
    folder = tmp_path / "factory" / "work" / "FEA-1-demo"
    (folder / "approvals").mkdir(parents=True)
    (folder / "approvals" / ".gitkeep").write_bytes(b"")
    return folder


def record_files(work_dir: Path) -> list[str]:
    """Names in ``approvals/``, the ``.gitkeep`` aside (a stray temp file would show up here)."""
    return sorted(p.name for p in (work_dir / "approvals").iterdir() if p.name != ".gitkeep")


# --------------------------------------------------------------------------- record_approval

def test_record_writes_verbatim_quote(wd, owner_cfg):
    a = record_approval(wd, owner_cfg, "spec", "2", 'Approved — "ship it"\nthanks', "Cord", "abc123")
    text = a.path.read_text(encoding="utf-8")
    assert '> Approved — "ship it"' in text and "> thanks" in text


def test_record_file_has_the_exact_layout(wd, owner_cfg, monkeypatch):
    monkeypatch.setattr(approvals, "_now", lambda: "2026-10-20T15:42:07Z")
    a = record_approval(wd, owner_cfg, "spec", "2", "Looks good.\nShip it.", "Cord", "abc123", host="claude", session="s-9")
    assert a.path == wd / "approvals" / "spec-rev2.md"
    assert a.path.read_bytes() == (
        b"Artifact: spec\n"
        b"Revision: 2\n"
        b"Approved by: Cord\n"
        b"When: 2026-10-20T15:42:07Z\n"
        b"Host: claude\n"
        b"Session: s-9\n"
        b"Commit: abc123\n"
        b"\n"
        b"Quote:\n"
        b"\n"
        b"> Looks good.\n"
        b"> Ship it.\n"
    )


def test_record_returns_what_it_wrote(wd, owner_cfg):
    a = record_approval(wd, owner_cfg, "plan", "1.2", "ok", "Cord", "deadbeef", host="codex", session="s1")
    assert (a.artifact, a.rev, a.approver, a.commit, a.host, a.session, a.quote) == (
        "plan", "1.2", "Cord", "deadbeef", "codex", "s1", "ok")
    assert re.fullmatch(UTC_STAMP, a.when)
    assert a.path == wd / "approvals" / "plan-rev1.2.md"


def test_when_is_utc_with_seconds_and_a_z_suffix(wd, owner_cfg):
    a = record_approval(wd, owner_cfg, "spec", "1", "ok", "Cord", "abc")
    assert re.fullmatch(UTC_STAMP, a.when)
    assert f"When: {a.when}\n" in a.path.read_text(encoding="utf-8")


def test_host_and_session_default_to_empty_lines(wd, owner_cfg):
    a = record_approval(wd, owner_cfg, "spec", "1", "ok", "Cord", "abc")
    lines = a.path.read_text(encoding="utf-8").split("\n")
    assert "Host:" in lines and "Session:" in lines  # no trailing space after the colon
    assert (a.host, a.session) == ("", "")


def test_file_is_utf8_with_lf_newlines(wd, owner_cfg):
    a = record_approval(wd, owner_cfg, "spec", "1", "Güt — 承認\r\nzwei\rdrei", "Cord", "abc")
    data = a.path.read_bytes()
    assert b"\r" not in data and not data.startswith(b"\xef\xbb\xbf")
    assert data.endswith(b"\n") and not data.endswith(b"\n\n")
    assert "> Güt — 承認\n> zwei\n> drei\n".encode("utf-8") in data


def test_every_quote_line_is_preserved(wd, owner_cfg):
    quote = "First\n\n    indented  \n\n\n- a list\n> already quoted\n#heading\nlast"
    a = record_approval(wd, owner_cfg, "spec", "1", quote, "Cord", "abc")
    block = a.path.read_text(encoding="utf-8").split("Quote:\n\n", 1)[1]
    assert block == "> First\n>\n>     indented  \n>\n>\n> - a list\n> > already quoted\n> #heading\n> last\n"
    assert latest_approval(wd, "spec").quote == quote


def test_surrounding_blank_lines_are_not_part_of_the_quote(wd, owner_cfg):
    a = record_approval(wd, owner_cfg, "spec", "1", "\n\n  Approved.\n\n", "Cord", "abc")
    assert a.quote == "  Approved."
    assert a.path.read_text(encoding="utf-8").endswith("Quote:\n\n>   Approved.\n")


@pytest.mark.parametrize("quote", ["", "   ", "\n", " \r\n \t \n"])
def test_empty_quote_refused(wd, owner_cfg, quote):
    with pytest.raises(ApprovalError, match="quote"):
        record_approval(wd, owner_cfg, "spec", "1", quote, "Cord", "abc")
    assert record_files(wd) == []


def test_unknown_approver_refused(wd, owner_cfg):
    with pytest.raises(ApprovalError) as caught:
        record_approval(wd, owner_cfg, "spec", "1", "ship it", "Mallory", "abc")
    message = str(caught.value)
    assert "Mallory" in message and "Cord" in message and "Dana Fox" in message  # names the allowed approvers
    assert record_files(wd) == []


def test_approver_matching_is_case_sensitive(wd, owner_cfg):
    with pytest.raises(ApprovalError):
        record_approval(wd, owner_cfg, "spec", "1", "ship it", "cord", "abc")
    assert record_files(wd) == []


def test_approver_is_trimmed_before_matching_and_written_trimmed(wd, owner_cfg):
    a = record_approval(wd, owner_cfg, "spec", "1", "ship it", "  Dana Fox \t", "abc")
    assert a.approver == "Dana Fox"
    assert "Approved by: Dana Fox\n" in a.path.read_text(encoding="utf-8")


@pytest.mark.parametrize("approver", ["", "   ", "Cord Fox", "Dana"])
def test_approver_must_match_a_listed_name_exactly(wd, owner_cfg, approver):
    with pytest.raises(ApprovalError):
        record_approval(wd, owner_cfg, "spec", "1", "ship it", approver, "abc")
    assert record_files(wd) == []


def test_existing_record_not_overwritten(wd, owner_cfg):
    first = record_approval(wd, owner_cfg, "spec", "2", "first words", "Cord", "abc123")
    before = first.path.read_bytes()
    with pytest.raises(ApprovalError, match="already"):
        record_approval(wd, owner_cfg, "spec", "2", "other words", "Dana Fox", "def456")
    assert first.path.read_bytes() == before
    assert record_files(wd) == ["spec-rev2.md"]  # and no temp file is left behind


def test_a_new_revision_does_not_touch_the_old_record(wd, owner_cfg):
    first = record_approval(wd, owner_cfg, "spec", "1", "first", "Cord", "abc")
    before = first.path.read_bytes()
    record_approval(wd, owner_cfg, "spec", "2", "second", "Cord", "def")
    assert first.path.read_bytes() == before
    assert record_files(wd) == ["spec-rev1.md", "spec-rev2.md"]


def test_other_artifacts_do_not_collide(wd, owner_cfg):
    record_approval(wd, owner_cfg, "spec", "1", "a", "Cord", "abc")
    record_approval(wd, owner_cfg, "plan", "1", "b", "Cord", "abc")
    assert record_files(wd) == ["plan-rev1.md", "spec-rev1.md"]


@pytest.mark.parametrize("artifact", ["", "Spec", "spec plan", "../spec", "a/b", "a\\b", "-spec", "spec_1", "spec\n", "spéc", "spec.md"])
def test_bad_artifact_refused(wd, owner_cfg, artifact):
    with pytest.raises(ApprovalError, match="artifact"):
        record_approval(wd, owner_cfg, artifact, "1", "ship it", "Cord", "abc")
    assert record_files(wd) == [] and not (wd / "spec-rev1.md").exists()  # nothing was written, even for ../spec


@pytest.mark.parametrize("artifact", ["spec", "a", "0", "mini-spec", "milestone-demo-2", "a--b"])
def test_good_artifact_accepted(wd, owner_cfg, artifact):
    assert record_approval(wd, owner_cfg, artifact, "1", "ship it", "Cord", "abc").path.name == f"{artifact}-rev1.md"


@pytest.mark.parametrize("rev", ["", "v2", "1.", ".1", "1..2", "1.0-rc", "-1", "1 ", " 1", "2\n", "1,2", "٢", "²", "1.a", "../1"])
def test_bad_rev_refused(wd, owner_cfg, rev):
    with pytest.raises(ApprovalError, match="rev"):
        record_approval(wd, owner_cfg, "spec", rev, "ship it", "Cord", "abc")
    assert record_files(wd) == []


@pytest.mark.parametrize("rev", ["0", "1", "10", "2.1", "1.0.3", "007"])
def test_good_rev_accepted(wd, owner_cfg, rev):
    assert record_approval(wd, owner_cfg, "spec", rev, "ship it", "Cord", "abc").path.name == f"spec-rev{rev}.md"


@pytest.mark.parametrize(
    "field, value",
    [
        ("commit", ""), ("commit", "  "), ("commit", "abc\nApproved by: Cord"), ("commit", "a\rb"),
        ("host", "claude\nCommit: forged"), ("host", "a b"),
        ("session", "s\nWhen: 2020-01-01T00:00:00Z"), ("session", "s\x0bt"),
    ],
)
def test_a_value_that_could_forge_a_line_is_refused(wd, owner_cfg, field, value):
    args = {"commit": "abc", "host": "", "session": ""}
    args[field] = value
    with pytest.raises(ApprovalError, match=field):
        record_approval(wd, owner_cfg, "spec", "1", "ship it", "Cord", **args)
    assert record_files(wd) == []


def test_commit_host_and_session_are_trimmed(wd, owner_cfg):
    a = record_approval(wd, owner_cfg, "spec", "1", "ship it", "Cord", " abc123 ", host=" claude ", session=" s1 ")
    assert (a.commit, a.host, a.session) == ("abc123", "claude", "s1")


def test_quote_that_cannot_be_encoded_is_refused(wd, owner_cfg):
    with pytest.raises(ApprovalError, match="quote"):
        record_approval(wd, owner_cfg, "spec", "1", "bad \ud800 text", "Cord", "abc")
    assert record_files(wd) == []


def test_work_folder_must_exist(tmp_path, owner_cfg):
    with pytest.raises(ApprovalError, match="work folder"):
        record_approval(tmp_path / "nope", owner_cfg, "spec", "1", "ship it", "Cord", "abc")
    assert not (tmp_path / "nope").exists()  # a mistyped folder is not created


def test_a_work_folder_without_approvals_gets_one(tmp_path, owner_cfg):
    folder = tmp_path / "FIX-1-x"
    folder.mkdir()
    a = record_approval(folder, owner_cfg, "spec", "1", "ship it", "Cord", "abc")
    assert a.path == folder / "approvals" / "spec-rev1.md" and a.path.is_file()


def test_a_failed_write_leaves_nothing_behind(wd, owner_cfg, monkeypatch):
    def boom(src, dst):
        raise OSError("disk on fire")

    monkeypatch.setattr(approvals.os, "replace", boom)
    with pytest.raises(OSError, match="disk on fire"):
        record_approval(wd, owner_cfg, "spec", "1", "ship it", "Cord", "abc")
    assert record_files(wd) == []


def test_record_goes_through_a_temp_file_in_the_same_folder(wd, owner_cfg, monkeypatch):
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append((Path(src), Path(dst)))
        assert Path(src).is_file() and not Path(dst).exists()
        real_replace(src, dst)

    monkeypatch.setattr(approvals.os, "replace", spy)
    a = record_approval(wd, owner_cfg, "spec", "1", "ship it", "Cord", "abc")
    assert len(seen) == 1 and seen[0][0].parent == seen[0][1].parent == wd / "approvals" and seen[0][1] == a.path
    assert seen[0][0] != a.path and not seen[0][0].exists()


def test_a_record_that_appears_during_the_write_is_not_replaced(wd, owner_cfg, monkeypatch):
    """The target is checked again just before the rename, so a record written in the meantime survives."""
    target = wd / "approvals" / "spec-rev1.md"
    real_open = os.open

    def racing_open(path, flags, mode=0o777, **kwargs):
        fd = real_open(path, flags, mode, **kwargs)
        if str(path).endswith(".tmp") and not target.exists():
            target.write_bytes(b"someone else's record\n")
        return fd

    monkeypatch.setattr(approvals.os, "open", racing_open)
    with pytest.raises(ApprovalError, match="already"):
        record_approval(wd, owner_cfg, "spec", "1", "ship it", "Cord", "abc")
    assert target.read_bytes() == b"someone else's record\n"
    assert record_files(wd) == ["spec-rev1.md"]


# --------------------------------------------------------------------------- latest_approval

def test_latest_approval_picks_highest_rev(wd, owner_cfg):
    for rev in ("2", "10", "2.1"):
        record_approval(wd, owner_cfg, "spec", rev, f"words for {rev}", "Cord", "abc")
    latest = latest_approval(wd, "spec")
    assert latest is not None and latest.rev == "10" and latest.quote == "words for 10"
    assert latest.path == wd / "approvals" / "spec-rev10.md"


def test_latest_approval_compares_dotted_parts_numerically(wd, owner_cfg):
    for rev in ("1.9", "1.10", "1.2.5", "1"):
        record_approval(wd, owner_cfg, "plan", rev, "ok", "Cord", "abc")
    assert latest_approval(wd, "plan").rev == "1.10"


def test_latest_approval_a_longer_revision_with_the_same_start_is_higher(wd, owner_cfg):
    for rev in ("2", "2.0.1"):
        record_approval(wd, owner_cfg, "spec", rev, "ok", "Cord", "abc")
    assert latest_approval(wd, "spec").rev == "2.0.1"


def test_latest_approval_reads_every_field_back(wd, owner_cfg, monkeypatch):
    monkeypatch.setattr(approvals, "_now", lambda: "2026-10-20T15:42:07Z")
    written = record_approval(wd, owner_cfg, "spec", "3", "Yes.\n\nShip — it.", "Dana Fox", "0123abc", host="claude", session="s-1")
    assert latest_approval(wd, "spec") == written
    assert latest_approval(wd, "spec") == Approval(
        "spec", "3", "Dana Fox", "2026-10-20T15:42:07Z", "claude", "s-1", "0123abc", "Yes.\n\nShip — it.", wd / "approvals" / "spec-rev3.md")


def test_latest_approval_reads_empty_host_and_session(wd, owner_cfg):
    written = record_approval(wd, owner_cfg, "spec", "1", "ok", "Cord", "abc")
    assert latest_approval(wd, "spec") == written


def test_latest_approval_ignores_other_artifacts_and_other_files(wd, owner_cfg):
    record_approval(wd, owner_cfg, "spec", "1", "ok", "Cord", "abc")
    record_approval(wd, owner_cfg, "spec-plan", "9", "other artifact", "Cord", "abc")
    record_approval(wd, owner_cfg, "plan", "7", "other artifact", "Cord", "abc")
    approvals_dir = wd / "approvals"
    for stray in ("spec-rev12.txt", "spec-rev.md", "spec-rev3-extra.md", "notes.md", "spec-rev99.md.bak", "SPEC-rev50.md"):
        (approvals_dir / stray).write_text("not a record\n", encoding="utf-8")
    (approvals_dir / "spec-rev40.md").mkdir()  # a folder is not a record
    assert latest_approval(wd, "spec").rev == "1"


def test_latest_approval_is_none_when_there_is_no_record(wd, owner_cfg):
    assert latest_approval(wd, "spec") is None  # only the .gitkeep
    record_approval(wd, owner_cfg, "plan", "1", "ok", "Cord", "abc")
    assert latest_approval(wd, "spec") is None


def test_latest_approval_is_none_without_an_approvals_folder(tmp_path):
    folder = tmp_path / "FIX-1-x"
    folder.mkdir()
    assert latest_approval(folder, "spec") is None
    assert latest_approval(tmp_path / "missing", "spec") is None


def test_latest_approval_refuses_a_bad_artifact_name(wd):
    with pytest.raises(ApprovalError, match="artifact"):
        latest_approval(wd, "../spec")


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Artifact: spec\nRevision: 1\n",  # no approver, time, commit or quote
        "Artifact: plan\nRevision: 1\nApproved by: Cord\nWhen: 2026-10-20T15:42:07Z\nHost:\nSession:\nCommit: abc\n\nQuote:\n\n> ok\n",  # names another artifact
        "Artifact: spec\nRevision: 2\nApproved by: Cord\nWhen: 2026-10-20T15:42:07Z\nHost:\nSession:\nCommit: abc\n\nQuote:\n\n> ok\n",  # names another revision
        "Artifact: spec\nRevision: 1\nApproved by: Cord\nWhen: 2026-10-20T15:42:07Z\nHost:\nSession:\nCommit: abc\n\nQuote:\n",  # no words
        "Artifact: spec\nRevision: 1\nApproved by: Cord\nWhen: 2026-10-20T15:42:07Z\nHost:\nSession:\nCommit: abc\n\nQuote:\n\nnot quoted\n",
        "Artifact: spec\nRevision: 1\nApproved by: Cord\nWhen: 2026-10-20T15:42:07Z\nHost:\nSession:\n\nQuote:\n\n> ok\n",  # no commit
    ],
)
def test_latest_approval_refuses_a_malformed_record(wd, text):
    (wd / "approvals" / "spec-rev1.md").write_text(text, encoding="utf-8", newline="\n")
    with pytest.raises(ApprovalError, match="spec-rev1.md"):
        latest_approval(wd, "spec")


def test_latest_approval_reads_a_record_saved_with_a_bom_and_crlf(wd, owner_cfg):
    written = record_approval(wd, owner_cfg, "spec", "1", "line one\nline two", "Cord", "abc", host="claude")
    text = written.path.read_text(encoding="utf-8")
    written.path.write_bytes(b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8"))
    assert latest_approval(wd, "spec") == written


# --------------------------------------------------------------------------- factory approve (CLI)

@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project folder (a factory.yaml and a work folder FIX-3-crash) that is the current folder."""
    (tmp_path / "factory.yaml").write_text(
        "project: demo\napprovers: [Cord, Dana Fox]\nverify:\n  quick: pytest -q\n  full: pytest\n",
        encoding="utf-8", newline="\n",
    )
    folder = tmp_path / "factory" / "work" / "FIX-3-crash"
    (folder / "approvals").mkdir(parents=True)
    (folder / "approvals" / ".gitkeep").write_bytes(b"")
    (folder / "ticket.md").write_text("# FIX-3: Crash\n", encoding="utf-8", newline="\n")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def run_cli(capsys, *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def approve_args(**overrides: str) -> list[str]:
    values = {"--rev": "1", "--quote": "Approved, go ahead.", "--approver": "Cord", "--commit": "abc123", **overrides}
    return [part for pair in values.items() for part in pair]


def test_cli_approve_writes_the_record_and_prints_its_path(project, capsys):
    code, out, err = run_cli(capsys, "approve", "FIX-3", "spec", *approve_args(**{"--rev": "2"}))
    assert code == 0 and err == ""
    path = Path(json.loads(out)["path"])
    assert set(json.loads(out)) == {"path"}
    assert path == project.resolve() / "factory" / "work" / "FIX-3-crash" / "approvals" / "spec-rev2.md"
    text = path.read_text(encoding="utf-8")
    assert "Approved by: Cord\n" in text and "Commit: abc123\n" in text and "> Approved, go ahead.\n" in text


def test_cli_approve_records_host_session_and_the_exact_words(project, capsys):
    quote = 'Approved — "ship it"\n\n  thanks, Dana ☺'
    code, out, err = run_cli(
        capsys, "approve", "FIX-3", "plan",
        "--rev", "1.1", f"--quote={quote}", "--approver", "Dana Fox", "--commit", "0f0f0f", "--host", "claude", "--session", "s-42",
    )
    assert code == 0, err
    assert out.isascii()  # JSON, ASCII-escaped, whatever the console encoding is
    found = latest_approval(project / "factory" / "work" / "FIX-3-crash", "plan")
    assert (found.approver, found.host, found.session, found.commit, found.quote) == ("Dana Fox", "claude", "s-42", "0f0f0f", quote)
    assert Path(json.loads(out)["path"]) == found.path


def test_cli_output_path_is_ascii_whatever_the_folder_is_called(tmp_path, monkeypatch, capsys):
    (tmp_path / "Ünï").mkdir()
    (tmp_path / "Ünï" / "factory.yaml").write_text(
        "project: demo\napprovers: [Cord]\nverify:\n  quick: q\n  full: f\n", encoding="utf-8", newline="\n")
    (tmp_path / "Ünï" / "factory" / "work" / "FIX-3-crash" / "approvals").mkdir(parents=True)
    monkeypatch.chdir(tmp_path / "Ünï")
    code, out, err = run_cli(capsys, "approve", "FIX-3", "spec", *approve_args())
    assert code == 0, err
    assert out.isascii() and Path(json.loads(out)["path"]).is_file()


def test_cli_approve_finds_the_project_from_a_subfolder(project, capsys, monkeypatch):
    sub = project / "factory" / "work" / "FIX-3-crash" / "approvals"
    monkeypatch.chdir(sub)
    code, out, err = run_cli(capsys, "approve", "FIX-3-crash", "spec", *approve_args())
    assert code == 0, err
    assert Path(json.loads(out)["path"]).name == "spec-rev1.md"


def test_cli_approve_writes_only_the_record(project, capsys):
    before = sorted(p.relative_to(project).as_posix() for p in project.rglob("*"))
    assert run_cli(capsys, "approve", "FIX-3", "spec", *approve_args())[0] == 0
    after = sorted(p.relative_to(project).as_posix() for p in project.rglob("*"))
    assert [p for p in after if p not in before] == ["factory/work/FIX-3-crash/approvals/spec-rev1.md"]


def test_cli_approve_refuses_an_unlisted_approver(project, capsys):
    code, out, err = run_cli(capsys, "approve", "FIX-3", "spec", *approve_args(**{"--approver": "Mallory"}))
    assert code == 1 and out == ""
    assert "Mallory" in err and "Cord" in err and "Dana Fox" in err
    assert not (project / "factory/work/FIX-3-crash/approvals/spec-rev1.md").exists()


def test_cli_approve_refuses_an_empty_quote(project, capsys):
    code, out, err = run_cli(capsys, "approve", "FIX-3", "spec", *approve_args(**{"--quote": "  "}))
    assert code == 1 and out == "" and "quote" in err
    assert not (project / "factory/work/FIX-3-crash/approvals/spec-rev1.md").exists()


def test_cli_approve_refuses_to_overwrite(project, capsys):
    assert run_cli(capsys, "approve", "FIX-3", "spec", *approve_args())[0] == 0
    path = project / "factory/work/FIX-3-crash/approvals/spec-rev1.md"
    before = path.read_bytes()
    code, out, err = run_cli(capsys, "approve", "FIX-3", "spec", *approve_args(**{"--quote": "different"}))
    assert code == 1 and out == "" and "already" in err
    assert path.read_bytes() == before


@pytest.mark.parametrize("artifact, rev", [("Spec", "1"), ("spec", "v1"), ("../spec", "1")])
def test_cli_approve_refuses_a_bad_artifact_or_rev(project, capsys, artifact, rev):
    code, out, err = run_cli(capsys, "approve", "FIX-3", artifact, *approve_args(**{"--rev": rev}))
    assert code == 1 and out == "" and err.strip()


def test_cli_approve_unknown_work_item_is_a_usage_problem(project, capsys):
    code, out, err = run_cli(capsys, "approve", "FIX-9", "spec", *approve_args())
    assert code == 2 and out == "" and "FIX-9" in err


def test_cli_approve_with_a_bad_id(project, capsys):
    code, out, err = run_cli(capsys, "approve", "banana", "spec", *approve_args())
    assert code == 2 and out == "" and "banana" in err


def test_cli_approve_without_a_project(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, out, err = run_cli(capsys, "approve", "FIX-3", "spec", *approve_args())
    assert code == 2 and out == "" and "factory.yaml" in err


def test_cli_approve_with_an_invalid_factory_yaml(project, capsys):
    (project / "factory.yaml").write_text("project: demo\n", encoding="utf-8", newline="\n")
    code, out, err = run_cli(capsys, "approve", "FIX-3", "spec", *approve_args())
    assert code == 2 and out == "" and "approvers" in err


@pytest.mark.parametrize("missing", ["--rev", "--quote", "--approver", "--commit"])
def test_cli_approve_needs_every_option(missing):
    args = approve_args()
    index = args.index(missing)
    del args[index:index + 2]
    with pytest.raises(SystemExit) as caught:
        cli.main(["approve", "FIX-3", "spec", *args])
    assert caught.value.code == 2


# --------------------------------------------------------------------------- factory approve --quote-file

def quote_file_args(path: Path, **overrides: str) -> list[str]:
    """The options of an approval whose words come from ``path`` instead of ``--quote``."""
    args = approve_args(**overrides)
    index = args.index("--quote")
    del args[index:index + 2]
    return [*args, "--quote-file", str(path)]


# Everything a shell would change on the way in: both kinds of quote, $, %, !, a backtick, a backslash, a dash.
AWKWARD_WORDS = (
    'Approved: "ship it" and \'go\'\n'
    "$HOME stays, %PATH% stays, wow! `date` \\n is two characters\n"
    "- a dash-led last line, café ☺"
)


def record_for(project: Path, artifact: str = "spec") -> Approval | None:
    return latest_approval(project / "factory" / "work" / "FIX-3-crash", artifact)


def test_cli_quote_file_records_the_words_verbatim(project, tmp_path, capsys):
    words = tmp_path / "words.txt"
    words.write_text(AWKWARD_WORDS, encoding="utf-8", newline="\n")
    code, out, err = run_cli(capsys, "approve", "FIX-3", "spec", *quote_file_args(words))
    assert code == 0 and err == ""
    found = record_for(project)
    assert found.quote == AWKWARD_WORDS and Path(json.loads(out)["path"]) == found.path
    text = found.path.read_text(encoding="utf-8")
    assert '> Approved: "ship it" and \'go\'\n' in text and "> $HOME stays, %PATH% stays, wow! `date` \\n is two characters\n" in text
    assert text.endswith("> - a dash-led last line, café ☺\n")


def test_cli_quote_file_is_processed_exactly_like_quote(project, tmp_path, capsys):
    words = tmp_path / "words.txt"
    words.write_bytes(("\n\n" + AWKWARD_WORDS + "\n\n").encode("utf-8"))  # an editor's blank lines around the words
    assert run_cli(capsys, "approve", "FIX-3", "spec", *quote_file_args(words))[0] == 0
    assert run_cli(capsys, "approve", "FIX-3", "plan", *approve_args(**{"--quote": "\n\n" + AWKWARD_WORDS + "\n\n"}))[0] == 0
    from_file, from_option = record_for(project, "spec"), record_for(project, "plan")
    assert from_file.quote == from_option.quote == AWKWARD_WORDS
    # the two records differ only in the artifact name and the time
    assert from_file.path.read_text(encoding="utf-8").split("Quote:")[1] == from_option.path.read_text(encoding="utf-8").split("Quote:")[1]


def test_cli_quote_file_drops_a_leading_bom_and_reads_crlf(project, tmp_path, capsys):
    words = tmp_path / "words.txt"
    words.write_bytes(b"\xef\xbb\xbf" + "Yes — ship it.\r\nThanks.\r\n".encode("utf-8"))
    code, _, err = run_cli(capsys, "approve", "FIX-3", "spec", *quote_file_args(words))
    assert code == 0, err
    assert record_for(project).quote == "Yes — ship it.\nThanks."
    assert b"\xef\xbb\xbf" not in record_for(project).path.read_bytes()


def test_cli_quote_file_may_be_given_as_a_relative_path(project, tmp_path, capsys):
    (project / "words.txt").write_text("Approved.\n", encoding="utf-8", newline="\n")
    code, _, err = run_cli(capsys, "approve", "FIX-3", "spec", *quote_file_args(Path("words.txt")))
    assert code == 0, err
    assert record_for(project).quote == "Approved."


def test_cli_quote_and_quote_file_together_is_a_usage_error(project, tmp_path, capsys):
    words = tmp_path / "words.txt"
    words.write_text("Approved.\n", encoding="utf-8", newline="\n")
    with pytest.raises(SystemExit) as caught:
        cli.main(["approve", "FIX-3", "spec", *approve_args(), "--quote-file", str(words)])
    assert caught.value.code == 2
    assert "not allowed with" in capsys.readouterr().err
    assert record_for(project) is None


def test_cli_neither_quote_nor_quote_file_is_a_usage_error(project, capsys):
    args = approve_args()
    index = args.index("--quote")
    del args[index:index + 2]
    with pytest.raises(SystemExit) as caught:
        cli.main(["approve", "FIX-3", "spec", *args])
    assert caught.value.code == 2
    err = capsys.readouterr().err
    assert "--quote" in err and "--quote-file" in err and "required" in err
    assert record_for(project) is None


def test_cli_quote_file_that_is_missing_is_a_usage_problem(project, tmp_path, capsys):
    code, out, err = run_cli(capsys, "approve", "FIX-3", "spec", *quote_file_args(tmp_path / "nope.txt"))
    assert code == 2 and out == "" and "nope.txt" in err
    assert record_for(project) is None


def test_cli_quote_file_that_is_not_utf8_is_a_usage_problem(project, tmp_path, capsys):
    words = tmp_path / "words.txt"
    words.write_bytes(b"Approved \xff\xfe.\n")
    code, out, err = run_cli(capsys, "approve", "FIX-3", "spec", *quote_file_args(words))
    assert code == 2 and out == "" and "words.txt" in err and "UTF-8" in err
    assert record_for(project) is None


@pytest.mark.parametrize("content", [b"", b"\n", b"  \r\n \t\r\n", b"\xef\xbb\xbf\n"])
def test_cli_quote_file_without_words_is_refused(project, tmp_path, capsys, content):
    words = tmp_path / "words.txt"
    words.write_bytes(content)
    code, out, err = run_cli(capsys, "approve", "FIX-3", "spec", *quote_file_args(words))
    assert code == 1 and out == "" and "quote" in err
    assert record_for(project) is None


def test_cli_quote_file_still_needs_an_approver_from_the_list(project, tmp_path, capsys):
    words = tmp_path / "words.txt"
    words.write_text("Approved.\n", encoding="utf-8", newline="\n")
    code, _, err = run_cli(capsys, "approve", "FIX-3", "spec", *quote_file_args(words, **{"--approver": "Mallory"}))
    assert code == 1 and "Mallory" in err
    assert record_for(project) is None
