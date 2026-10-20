"""Approvals: the owner's go-ahead at a checkpoint, kept in the work item (design F13, section 8.4).

The owner approves in chat. The factory then records the owner's exact words, the time, the host and
session, and the commit approved, in ``<work folder>/approvals/<artifact>-rev<rev>.md``::

    Artifact: spec
    Revision: 2
    Approved by: Cord
    When: 2026-10-20T15:42:07Z
    Host: claude
    Session: <session id, may be empty>
    Commit: <the commit approved>

    Quote:

    > the owner's words, one ">" line for each line they wrote

No record is written without a non-empty quote and an approver named in ``factory.yaml``, and a record is
never overwritten: a changed artifact is approved again as a new revision.
"""
from __future__ import annotations

import contextlib
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from swfactory.config import ProjectConfig

__all__ = ["Approval", "ApprovalError", "latest_approval", "record_approval"]

_APPROVALS = "approvals"
_ARTIFACT = re.compile(r"[a-z0-9][a-z0-9-]*")
_REV = r"[0-9]+(?:\.[0-9]+)*"  # 2, 10, 2.1 (ASCII digits only; "\d" would take other scripts' digits)
_WHEN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_NEWLINE = re.compile(r"\r\n|\r|\n")
_QUOTE_LABEL = "Quote:"
_HEADERS = ("Artifact", "Revision", "Approved by", "When", "Host", "Session", "Commit")


class ApprovalError(ValueError):
    """An approval cannot be recorded (or an existing record cannot be read); the message says why."""


@dataclass
class Approval:
    """One record in ``approvals/``. ``when`` is UTC, ``host`` and ``session`` may be empty, and ``quote`` holds
    the owner's words with ``\\n`` between lines."""

    artifact: str
    rev: str
    approver: str
    when: str
    host: str
    session: str
    commit: str
    quote: str
    path: Path


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _check_artifact(artifact: str) -> None:
    if not _ARTIFACT.fullmatch(artifact):
        raise ApprovalError(
            f"artifact {artifact!r} is not allowed: use a-z, 0-9 and -, starting with a letter or digit (such as 'spec')"
        )


def _check_rev(rev: str) -> None:
    if not re.fullmatch(_REV, rev):
        raise ApprovalError(f"rev {rev!r} is not allowed: use digits separated by dots (such as '2' or '1.1')")


def _clean_line(name: str, value: str, *, required: bool) -> str:
    """``value`` trimmed; it must be a single line, so it cannot add a line to the record."""
    value = value.strip()
    if not value:
        if required:
            raise ApprovalError(f"{name} must not be empty")
        return ""
    if len(value.splitlines()) != 1:
        raise ApprovalError(f"{name} must be one line of text")
    return value


def _quote_lines(quote: str) -> list[str]:
    """The lines of ``quote`` with line endings normalised to one per line and the blank lines around the words
    dropped. Everything else (indentation, blank lines between the words, trailing spaces) is kept as typed."""
    lines = _NEWLINE.split(quote)
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        raise ApprovalError("the quote is empty: an approval needs the owner's own words, so none was recorded")
    return lines


def _check_approver(cfg: ProjectConfig, approver: str) -> str:
    """The trimmed approver, who must be one of ``cfg.approvers`` (exact, case-sensitive)."""
    name = _clean_line("approver", approver, required=True)
    allowed = [entry.strip() for entry in cfg.approvers]
    if name not in allowed:
        raise ApprovalError(
            f"{name!r} is not one of the approvers listed in factory.yaml; allowed: {', '.join(allowed)}"
        )
    return name


def _exists_error(target: Path) -> ApprovalError:
    return ApprovalError(
        f"{target} already exists; an approval record is never overwritten (approve the changed artifact as a new revision)"
    )


def _write_new(target: Path, data: bytes) -> None:
    """Write ``data`` to ``target``, which must not exist: into a new temp file beside it, then renamed into place,
    so the record is whole or absent. The temp file is removed on any failure."""
    temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o666)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if os.path.lexists(target):  # a record that appeared since the first check is not replaced
            raise _exists_error(target)
        os.replace(temp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp)
        raise


def record_approval(
    work_dir: Path,
    cfg: ProjectConfig,
    artifact: str,
    rev: str,
    quote: str,
    approver: str,
    commit: str,
    host: str = "",
    session: str = "",
) -> Approval:
    """Record the owner's approval of ``artifact`` revision ``rev`` as ``approvals/<artifact>-rev<rev>.md``.

    ``quote`` is the owner's message, kept line for line; ``approver`` must be one of ``cfg.approvers`` (trimmed,
    then exact and case-sensitive); ``commit`` is the commit that holds the approved revision. The record is UTF-8
    with LF newlines and is written atomically. The caller commits it.

    Raises ``ApprovalError`` for a bad ``artifact`` (``a-z``, ``0-9`` and ``-``) or ``rev`` (digits and dots), an
    empty quote, an approver who is not listed, a missing commit, a value that is not a single line, a folder that
    is not a work folder, or a record that already exists (nothing is overwritten).
    """
    _check_artifact(artifact)
    _check_rev(rev)
    lines = _quote_lines(quote)
    approver = _check_approver(cfg, approver)
    commit = _clean_line("commit", commit, required=True)
    host = _clean_line("host", host, required=False)
    session = _clean_line("session", session, required=False)
    work_dir = Path(work_dir)
    if not work_dir.is_dir():
        raise ApprovalError(f"{work_dir} is not a work folder (it is missing or is not a folder)")

    when = _now()
    target = work_dir / _APPROVALS / f"{artifact}-rev{rev}.md"
    header = [("Artifact", artifact), ("Revision", rev), ("Approved by", approver), ("When", when),
              ("Host", host), ("Session", session), ("Commit", commit)]
    text = "".join(f"{name}: {value}".rstrip() + "\n" for name, value in header)
    text += f"\n{_QUOTE_LABEL}\n\n" + "".join((f"> {line}" if line else ">") + "\n" for line in lines)
    try:
        data = text.encode("utf-8")
    except UnicodeEncodeError:
        raise ApprovalError("the quote or another field holds text that is not valid Unicode, so nothing was recorded") from None

    if os.path.lexists(target):
        raise _exists_error(target)
    target.parent.mkdir(exist_ok=True)
    _write_new(target, data)
    return Approval(artifact, rev, approver, when, host, session, commit, "\n".join(lines), target)


def _parse(path: Path, artifact: str, rev: str) -> Approval:
    """Read a record written by ``record_approval`` (a BOM or CRLF line endings, from an editor, are tolerated)."""

    def bad(why: str) -> ApprovalError:
        return ApprovalError(f"{path} is not a valid approval record: {why}")

    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        raise bad("it is not UTF-8 text") from None
    lines = _NEWLINE.sub("\n", text).split("\n")
    fields: dict[str, str] = {}
    index = 0
    while index < len(lines) and lines[index] != _QUOTE_LABEL:
        line = lines[index]
        if line.strip():
            name, colon, value = line.partition(":")
            if not colon or name not in _HEADERS or name in fields:
                raise bad(f"line {index + 1} is not one of the header lines")
            fields[name] = value.strip()
        index += 1
    if index == len(lines):
        raise bad(f"there is no {_QUOTE_LABEL} line")
    missing = [name for name in _HEADERS if name not in fields]
    if missing:
        raise bad(f"missing {', '.join(missing)}")

    body = lines[index + 1:]
    if body and body[0] == "":
        body.pop(0)
    while body and body[-1] == "":
        body.pop()
    quoted: list[str] = []
    for line in body:
        if line == ">":
            quoted.append("")
        elif line.startswith("> "):
            quoted.append(line[2:])
        else:
            raise bad("the quote is not a block of '>' lines")
    quote = "\n".join(quoted)
    if not quote.strip():
        raise bad("it holds no quoted words")

    if fields["Artifact"] != artifact or fields["Revision"] != rev:
        raise bad(f"it says {fields['Artifact']} revision {fields['Revision']}, not {artifact} revision {rev}")
    if not fields["Approved by"] or not fields["Commit"]:
        raise bad("it names no approver or no commit")
    if not _WHEN.fullmatch(fields["When"]):
        raise bad(f"When is not a UTC time such as 2026-10-20T15:42:07Z: {fields['When']!r}")
    return Approval(artifact, rev, fields["Approved by"], fields["When"], fields["Host"], fields["Session"],
                    fields["Commit"], quote, path)


def latest_approval(work_dir: Path, artifact: str) -> Approval | None:
    """The record of ``artifact`` with the highest revision, or ``None`` when it has none.

    Revisions compare as numbers part by part (``10`` is after ``2``; ``2.1`` is after ``2``). Other files in
    ``approvals/`` (a ``.gitkeep``, other artifacts) are ignored. Raises ``ApprovalError`` if the record it picks
    is not a valid one, rather than quietly falling back to an older approval.
    """
    _check_artifact(artifact)
    folder = Path(work_dir) / _APPROVALS
    if not folder.is_dir():
        return None
    name = re.compile(rf"{re.escape(artifact)}-rev(?P<rev>{_REV})\.md")
    found = []
    for entry in folder.iterdir():
        match = name.fullmatch(entry.name)
        if match and entry.is_file():
            found.append((tuple(int(part) for part in match["rev"].split(".")), entry.name, entry, match["rev"]))
    if not found:
        return None
    _, _, path, rev = max(found, key=lambda item: item[:2])
    return _parse(path, artifact, rev)
