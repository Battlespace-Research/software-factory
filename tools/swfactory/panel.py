"""The review panel: combine each lens's review of a task round into one verdict, and record it.

Rules (design section 9.3): the round ends only when every lens has reported, so a lens with no file or an
unparseable one makes the panel INCOMPLETE. Every maintainability finding is a follow-up, whatever its severity;
blocker and major findings from any other lens block. The panel says CHANGES when something blocks or a lens
other than maintainability says CHANGES, and PASS otherwise. Nothing here can turn missing or garbled reviewer
output into a PASS.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from swfactory.verdict import (
    BLOCKING_SEVERITIES,
    DEFAULT_NIT_CAP,
    Finding,
    Review,
    ReviewFormatError,
    panel_path,
    read_review_file,
    review_path,
)

MAINTAINABILITY = "maintainability"
PANEL_VERDICTS = ("PASS", "CHANGES", "INCOMPLETE")

__all__ = [
    "Finding",
    "MAINTAINABILITY",
    "PanelResult",
    "combine",
    "latest_passing_commit",
    "render_panel_summary",
    "review_path",
    "write_panel_summary",
]


@dataclass
class PanelResult:
    verdict: str  # PASS, CHANGES or INCOMPLETE
    blocking: list[tuple[str, Finding]]  # (lens, finding): blocker and major findings of the lenses that can block
    follow_ups: list[tuple[str, Finding]]  # (lens, finding): every maintainability finding
    missing: list[str]  # lenses with no review file
    invalid: dict[str, str]  # lens -> why its review file could not be read
    commit: str  # the commit that was reviewed
    warnings: list[str] = field(default_factory=list)  # "<lens>: ..." things a person should look at


def combine(
    work_dir: Path, task: str, rnd: int, lenses: list[str], commit: str, nit_cap: int = DEFAULT_NIT_CAP
) -> PanelResult:
    """Read each lens's review of ``task`` round ``rnd`` under ``work_dir`` and combine them.

    ``nit_cap`` is the project's cap on nits per review (a longer list only produces a warning).
    """
    lenses = list(dict.fromkeys(lenses))
    if not lenses:
        raise ValueError("the panel needs at least one lens; a panel of none would pass everything")

    reviews: dict[str, Review] = {}
    missing: list[str] = []
    invalid: dict[str, str] = {}
    for lens in lenses:
        path = review_path(work_dir, task, rnd, lens)
        try:
            reviews[lens] = read_review_file(path, lens, nit_cap)
        except FileNotFoundError:
            missing.append(lens)
        except ReviewFormatError as err:
            invalid[lens] = f"{path.name}: {err}"
        except OSError as err:
            invalid[lens] = f"{path.name}: cannot be read: {err}"

    blocking: list[tuple[str, Finding]] = []
    follow_ups: list[tuple[str, Finding]] = []
    warnings: list[str] = []
    says_changes = False
    for lens in lenses:
        review = reviews.get(lens)
        if review is None:
            continue
        warnings.extend(f"{lens}: {warning}" for warning in review.warnings)
        if review.verdict == "CHANGES" and not any(f.severity in BLOCKING_SEVERITIES for f in review.findings):
            warnings.append(f"{lens}: the verdict is CHANGES but it lists no blocker or major finding")
        if lens == MAINTAINABILITY:
            follow_ups.extend((lens, finding) for finding in review.findings)
        else:
            blocking.extend((lens, f) for f in review.findings if f.severity in BLOCKING_SEVERITIES)
            says_changes = says_changes or review.verdict == "CHANGES"

    if missing or invalid:
        verdict = "INCOMPLETE"
    elif blocking or says_changes:
        verdict = "CHANGES"
    else:
        verdict = "PASS"
    return PanelResult(verdict, blocking, follow_ups, missing, invalid, commit, warnings)


# --------------------------------------------------------------------------- the summary file

def _cell(text: str) -> str:
    """Text safe inside a table cell: one line, with a literal ``|`` escaped."""
    return " ".join(text.split()).replace("|", "\\|")


def _line(text: str) -> str:
    return " ".join(text.split())


def _finding_table(pairs: list[tuple[str, Finding]]) -> list[str]:
    if not pairs:
        return ["None."]
    lines = ["| Lens | ID | Severity | Location | Finding | Fix |", "|---|---|---|---|---|---|"]
    for lens, f in pairs:
        lines.append("| " + " | ".join(_cell(part) for part in (lens, f.id, f.severity, f.location, f.summary, f.fix)) + " |")
    return lines


def render_panel_summary(result: PanelResult) -> str:
    """The summary text: ``VERDICT: ...``, ``Commit: <sha>``, then blocking, follow-ups, missing, invalid and warnings."""
    lines = [f"VERDICT: {result.verdict}", f"Commit: {result.commit}", ""]
    lines += ["## Blocking", *_finding_table(result.blocking), ""]
    lines += ["## Follow-ups", *_finding_table(result.follow_ups), ""]
    lines += ["## Missing", *[f"- {lens}" for lens in result.missing], *([] if result.missing else ["None."]), ""]
    lines += ["## Invalid", *[f"- {lens}: {_line(why)}" for lens, why in result.invalid.items()],
              *([] if result.invalid else ["None."]), ""]
    lines += ["## Warnings", *[f"- {_line(warning)}" for warning in result.warnings],
              *([] if result.warnings else ["None."])]
    return "\n".join(lines) + "\n"


def write_panel_summary(work_dir: Path, task: str, rnd: int, result: PanelResult) -> Path:
    """Write ``reviews/<task>-r<rnd>-panel.md`` (LF line endings, replaced in one step) and return its path."""
    if result.verdict not in PANEL_VERDICTS:
        raise ValueError(f"verdict {result.verdict!r} is not one of {', '.join(PANEL_VERDICTS)}")
    if not result.commit.strip() or re.search(r"[\r\n]", result.commit):
        raise ValueError(f"commit {result.commit!r} must be one non-empty line")
    path = panel_path(work_dir, task, rnd)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Written beside the target and renamed over it, so a reader never sees half a summary: a cut-off
    # "VERDICT: PASS" with a cut-off commit would otherwise look like a pass.
    partial = path.with_name(path.name + ".partial")
    try:
        partial.write_text(render_panel_summary(result), encoding="utf-8", newline="\n")
        os.replace(partial, path)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return path


_PANEL_FILE = re.compile(r"(?P<task>.+)-r(?P<round>\d+)-panel\.md")
_NUMBER = re.compile(r"(\d+)")


def _task_order(task: str) -> tuple[tuple[int, int, str], ...]:
    """Sort key putting ``T2`` before ``T10`` and ``M1-T3`` before ``M1-T3a`` before ``M2-T1``."""
    return tuple((0, int(part), "") if part.isdigit() else (1, 0, part) for part in _NUMBER.split(task) if part)


def _passing_commit(path: Path) -> str | None:
    """The commit a panel summary records, or None unless it is a well-formed PASS."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    lines = re.sub(r"\r\n?", "\n", text.removeprefix("﻿")).split("\n")
    if len(lines) < 2 or lines[0].rstrip() != "VERDICT: PASS" or not lines[1].startswith("Commit:"):
        return None
    return lines[1].removeprefix("Commit:").strip() or None


def latest_passing_commit(work_dir: Path) -> str | None:
    """The commit recorded by the newest passing panel summary under ``work_dir/reviews``, or None.

    Newest means the highest task (numbers compared as numbers) and, within it, the highest round; the
    modification time is not used because a checkout resets it.
    """
    best: tuple[tuple[tuple[int, int, str], ...], int] | None = None
    commit: str | None = None
    for path in (Path(work_dir) / "reviews").glob("*-panel.md"):
        match = _PANEL_FILE.fullmatch(path.name)
        passed = _passing_commit(path) if match else None
        if passed is None:
            continue
        key = (_task_order(match.group("task")), int(match.group("round")))
        if best is None or key > best:
            best, commit = key, passed
    return commit
