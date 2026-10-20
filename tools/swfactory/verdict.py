"""Review files: one lens reviewer's verdict and findings, parsed from markdown or JSON into a ``Review``.

A review file starts with ``VERDICT: PASS`` or ``VERDICT: CHANGES`` and may continue with two tables::

    VERDICT: CHANGES
    ## Earlier findings
    | ID | Status |
    | R1 | fixed |
    ## Findings
    | ID | Severity | Location | Finding | Fix |
    | R2 | major | src/x.py:40 | ... | ... |

The same content can arrive as JSON (``schemas/review.schema.json``), which is what Codex returns when it is
given the schema. The parsers are deliberately strict: anything that is not clearly a review raises
``ReviewFormatError`` so the panel reports INCOMPLETE and never a PASS read out of garbled text. Windows
files (CRLF line endings, a UTF-8 BOM) parse exactly like Unix ones.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

VERDICTS = ("PASS", "CHANGES")
SEVERITIES = ("blocker", "major", "minor", "nit")
BLOCKING_SEVERITIES = ("blocker", "major")
PRIOR_STATUSES = ("fixed", "not fixed", "no longer applies")
DEFAULT_NIT_CAP = 5

FINDING_COLUMNS = ("ID", "Severity", "Location", "Finding", "Fix")
PRIOR_COLUMNS = ("ID", "Status")

_NAME_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_LENS_PART = re.compile(r"[a-z0-9][a-z0-9_-]*")


class ReviewFormatError(ValueError):
    """A review file does not follow the review format."""


@dataclass(frozen=True)
class Finding:
    id: str
    severity: str  # one of SEVERITIES
    location: str
    summary: str
    fix: str


@dataclass(frozen=True)
class PriorStatus:
    id: str
    status: str  # one of PRIOR_STATUSES


@dataclass
class Review:
    lens: str
    verdict: str  # PASS or CHANGES
    findings: list[Finding]
    prior: list[PriorStatus]
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- where review files live

def _check_name(what: str, value: str, pattern: re.Pattern[str]) -> None:
    if not pattern.fullmatch(value):
        raise ValueError(f"{what} {value!r} cannot be part of a review file name")


def review_path(work_dir: Path, task: str, rnd: int, lens: str) -> Path:
    """``<work_dir>/reviews/<task>-r<rnd>-<lens>.md``."""
    _check_name("lens", lens, _LENS_PART)
    return _reviews_file(work_dir, task, rnd, lens)


def panel_path(work_dir: Path, task: str, rnd: int) -> Path:
    """``<work_dir>/reviews/<task>-r<rnd>-panel.md``: the combined result of a round."""
    return _reviews_file(work_dir, task, rnd, "panel")


def _reviews_file(work_dir: Path, task: str, rnd: int, tail: str) -> Path:
    _check_name("task", task, _NAME_PART)
    if rnd < 1:
        raise ValueError(f"round must be 1 or more, not {rnd}")
    return Path(work_dir) / "reviews" / f"{task}-r{rnd}-{tail}.md"


# --------------------------------------------------------------------------- validation shared by both forms

def _make_finding(where: str, id_: str, severity: str, location: str, summary: str, fix: str) -> Finding:
    if not id_:
        raise ReviewFormatError(f"{where}: the finding has no ID")
    level = severity.lower()
    if level not in SEVERITIES:
        raise ReviewFormatError(f"{where}: severity {severity!r} is not one of {', '.join(SEVERITIES)}")
    if not summary:
        raise ReviewFormatError(f"{where}: finding {id_} has no finding text")
    return Finding(id_, level, location, summary, fix)


def _make_prior(where: str, id_: str, status: str) -> PriorStatus:
    if not id_:
        raise ReviewFormatError(f"{where}: the earlier finding has no ID")
    kind = " ".join(status.lower().split())
    if kind not in PRIOR_STATUSES:
        raise ReviewFormatError(f"{where}: status {status!r} is not one of {', '.join(PRIOR_STATUSES)}")
    return PriorStatus(id_, kind)


def _assemble(lens: str, verdict: str, findings: list[Finding], prior: list[PriorStatus], nit_cap: int) -> Review:
    warnings: list[str] = []
    nits = sum(1 for finding in findings if finding.severity == "nit")
    if nits > nit_cap:
        warnings.append(f"{nits} nits listed; the cap is {nit_cap}")
    return Review(lens, verdict, findings, prior, warnings)


# --------------------------------------------------------------------------- markdown form

_VERDICT_LINE = re.compile(r"VERDICT:[ \t]*(PASS|CHANGES)", re.IGNORECASE)
_HEADING = re.compile(r"#{1,6}[ \t]+(.+?)[ \t]*#*")
_SEPARATOR_CELL = re.compile(r":?-+:?")
_UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")
_SECTION_FOR_HEADING = {"earlier findings": "prior", "findings": "findings"}


def _normalize(text: str) -> str:
    """Drop a leading BOM and make every line break ``\\n``."""
    return re.sub(r"\r\n?", "\n", text.removeprefix("﻿"))


def _split_row(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    return [cell.replace("\\|", "|").strip() for cell in _UNESCAPED_PIPE.split(body)]


def _table_rows(section: str, rows: list[tuple[int, str]], columns: tuple[str, ...]) -> list[tuple[int, list[str]]]:
    """The data rows of a section's table as ``(line number, cells)``; the header row is checked, a separator row skipped."""
    if not rows:
        return []
    number, first = rows[0]
    if [cell.lower() for cell in _split_row(first)] != [column.lower() for column in columns]:
        raise ReviewFormatError(f"line {number}: the {section} table must start with the header | {' | '.join(columns)} |")
    data: list[tuple[int, list[str]]] = []
    for number, line in rows[1:]:
        cells = _split_row(line)
        if all(_SEPARATOR_CELL.fullmatch(cell) for cell in cells):
            continue
        if len(cells) != len(columns):
            raise ReviewFormatError(
                f"line {number}: expected {len(columns)} cells ({', '.join(columns)}) but found {len(cells)}; "
                "write a literal | inside a cell as \\|"
            )
        data.append((number, cells))
    return data


def parse_review_markdown(text: str, lens: str, nit_cap: int = DEFAULT_NIT_CAP) -> Review:
    """Parse a review in the markdown format; raise ``ReviewFormatError`` unless it is well formed."""
    lines = _normalize(text).split("\n")
    first = _VERDICT_LINE.fullmatch(lines[0].rstrip())
    if first is None:
        if not text.strip():
            raise ReviewFormatError("the review is empty; its first line must be 'VERDICT: PASS' or 'VERDICT: CHANGES'")
        shown = lines[0] if len(lines[0]) <= 60 else lines[0][:57] + "..."
        raise ReviewFormatError(f"the first line must be 'VERDICT: PASS' or 'VERDICT: CHANGES', not {shown!r}")

    sections: dict[str, list[tuple[int, str]]] = {}
    current: str | None = None  # None before the first heading; "" under a heading that is neither table's
    for number, line in enumerate(lines[1:], start=2):
        heading = _HEADING.fullmatch(line.rstrip())
        if heading:
            key = _SECTION_FOR_HEADING.get(" ".join(heading.group(1).lower().rstrip(":").split()), "")
            if key:
                if key in sections:
                    raise ReviewFormatError(f"line {number}: the '{heading.group(1)}' section appears twice")
                sections[key] = []
            current = key
        elif line.lstrip().startswith("|"):
            if not current:  # a table nobody reads would hide findings
                raise ReviewFormatError(
                    f"line {number}: a table must sit under '## Earlier findings' or '## Findings'"
                )
            sections[current].append((number, line))

    findings = [
        _make_finding(f"line {number}", *cells)
        for number, cells in _table_rows("Findings", sections.get("findings", []), FINDING_COLUMNS)
    ]
    prior = [
        _make_prior(f"line {number}", *cells)
        for number, cells in _table_rows("Earlier findings", sections.get("prior", []), PRIOR_COLUMNS)
    ]
    return _assemble(lens, first.group(1).upper(), findings, prior, nit_cap)


# --------------------------------------------------------------------------- JSON form

_JSON_FINDING_KEYS = ("id", "severity", "location", "summary", "fix")
_JSON_PRIOR_KEYS = ("id", "status")


def _json_object(where: str, value: Any, allowed: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReviewFormatError(f"{where} must be an object")
    for key in value:
        if key not in allowed:
            raise ReviewFormatError(f"{where} has unknown key {key!r} (allowed: {', '.join(allowed)})")
    return value


def _json_text(where: str, item: dict[str, Any], key: str, required: bool) -> str:
    value = item.get(key)
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise ReviewFormatError(f"{where}.{key} must be a string")
    return value.strip()


def parse_review_json(data: dict, lens: str, nit_cap: int = DEFAULT_NIT_CAP) -> Review:
    """Parse a review in the JSON form (``verdict``, ``findings``, ``prior``); raise ``ReviewFormatError`` unless it fits."""
    review = _json_object("the review", data, ("verdict", "findings", "prior"))
    verdict = review.get("verdict")
    if not isinstance(verdict, str) or verdict.strip().upper() not in VERDICTS:
        raise ReviewFormatError(f"verdict {verdict!r} is not one of {', '.join(VERDICTS)}")

    findings: list[Finding] = []
    for index, raw in enumerate(_json_list("findings", review), start=1):
        where = f"findings[{index}]"
        item = _json_object(where, raw, _JSON_FINDING_KEYS)
        findings.append(_make_finding(
            where,
            _json_text(where, item, "id", True),
            _json_text(where, item, "severity", True),
            _json_text(where, item, "location", False),
            _json_text(where, item, "summary", True),
            _json_text(where, item, "fix", False),
        ))
    prior: list[PriorStatus] = []
    for index, raw in enumerate(_json_list("prior", review), start=1):
        where = f"prior[{index}]"
        item = _json_object(where, raw, _JSON_PRIOR_KEYS)
        prior.append(_make_prior(where, _json_text(where, item, "id", True), _json_text(where, item, "status", True)))
    return _assemble(lens, verdict.strip().upper(), findings, prior, nit_cap)


def _json_list(key: str, review: dict[str, Any]) -> list[Any]:
    value = review.get(key, [])
    if not isinstance(value, list):
        raise ReviewFormatError(f"{key} must be a list")
    return value


# --------------------------------------------------------------------------- files

_STAMP_LINE = re.compile(r"<!--\s*factory:.*-->")


def _without_stamp(text: str) -> str:
    """Drop trailing blank lines and, when the last line is the adapter's ``<!-- factory: ... -->`` stamp, that line too."""
    lines = text.split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and _STAMP_LINE.fullmatch(lines[-1].strip()):
        lines.pop()
        while lines and not lines[-1].strip():
            lines.pop()
    return "\n".join(lines)


def read_review_file(path: Path, lens: str, nit_cap: int = DEFAULT_NIT_CAP) -> Review:
    """Read a reviewer's output file, as the role adapters leave it, and parse it.

    The file is UTF-8 (a BOM and CRLF line endings are fine). The adapters' trailing provenance stamp is ignored.
    Text that starts with ``{`` is the JSON form, anything else the markdown form. Raises ``ReviewFormatError``
    when it does not parse and ``OSError`` (``FileNotFoundError`` when absent) when it cannot be read.
    """
    try:
        text = Path(path).read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise ReviewFormatError("the file is not valid UTF-8") from None
    text = _without_stamp(_normalize(text))
    if text.lstrip().startswith("{"):
        try:
            data = json.loads(text)
        except ValueError as err:
            raise ReviewFormatError(f"invalid JSON: {err}") from None
        return parse_review_json(data, lens, nit_cap)
    return parse_review_markdown(text, lens, nit_cap)
