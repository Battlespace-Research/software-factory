"""Tests for swfactory.verdict: parsing a lens reviewer's file into a Review."""
from __future__ import annotations

import dataclasses
import json

import pytest

from swfactory.adapters import plan_invocation, run_invocation
from swfactory.config import factory_home
from swfactory.roles import resolve_role
from swfactory.verdict import (
    PRIOR_STATUSES,
    SEVERITIES,
    Finding,
    PriorStatus,
    Review,
    ReviewFormatError,
    panel_path,
    parse_review_json,
    parse_review_markdown,
    read_review_file,
    review_path,
)

HEADER = "| ID | Severity | Location | Finding | Fix |\n|---|---|---|---|---|\n"

SAMPLE_MD = (
    "VERDICT: CHANGES\n"
    "## Earlier findings\n"
    "| ID | Status |\n"
    "| R1 | fixed |\n"
    "| R2 | not fixed |\n"
    "| R3 | no longer applies |\n"
    "## Findings\n" + HEADER +
    "| R4 | major | src/x.py:40 | The loop never ends | Add a bound |\n"
    "| R5 | nit | src/y.py:2 | The name is vague | Rename it |\n"
)

SAMPLE_JSON = {
    "verdict": "CHANGES",
    "findings": [
        {"id": "R4", "severity": "major", "location": "src/x.py:40", "summary": "The loop never ends", "fix": "Add a bound"},
        {"id": "R5", "severity": "nit", "location": "src/y.py:2", "summary": "The name is vague", "fix": "Rename it"},
    ],
    "prior": [
        {"id": "R1", "status": "fixed"},
        {"id": "R2", "status": "not fixed"},
        {"id": "R3", "status": "no longer applies"},
    ],
}

STAMP = (
    "<!-- factory: role=lens-security vendor=openai model=gpt-6.1-sol effort=xhigh "
    "sandbox=read-only at=2026-10-20T12:00:00Z -->"
)


def rows(*lines: str) -> str:
    return "".join(line + "\n" for line in lines)


# --------------------------------------------------------------------------- markdown form

def test_parse_pass_minimal():
    assert parse_review_markdown("VERDICT: PASS\n", "security").verdict == "PASS"


def test_parse_returns_a_review_with_findings_and_prior():
    review = parse_review_markdown(SAMPLE_MD, "correctness")
    assert review == Review(
        lens="correctness",
        verdict="CHANGES",
        findings=[
            Finding("R4", "major", "src/x.py:40", "The loop never ends", "Add a bound"),
            Finding("R5", "nit", "src/y.py:2", "The name is vague", "Rename it"),
        ],
        prior=[PriorStatus("R1", "fixed"), PriorStatus("R2", "not fixed"), PriorStatus("R3", "no longer applies")],
        warnings=[],
    )


def test_parse_crlf_and_bom():
    text = (
        "﻿VERDICT: CHANGES\r\n## Findings\r\n| ID | Severity | Location | Finding | Fix |\r\n"
        "|---|---|---|---|---|\r\n| R1 | major | a.py:1 | x | y |\r\n"
    )
    assert parse_review_markdown(text, "correctness").findings[0].severity == "major"


def test_crlf_and_bom_parse_exactly_like_lf():
    crlf = "﻿" + SAMPLE_MD.replace("\n", "\r\n")
    assert parse_review_markdown(crlf, "correctness") == parse_review_markdown(SAMPLE_MD, "correctness")


def test_first_line_not_verdict_raises():
    with pytest.raises(ReviewFormatError):
        parse_review_markdown("Looks good!\nVERDICT: PASS", "spec")


@pytest.mark.parametrize(
    "text",
    [
        "",
        "\n",
        "\nVERDICT: PASS\n",  # the verdict is the first line, not the first non-blank line
        "  VERDICT: PASS\n",
        "VERDICT: INCOMPLETE\n",  # only the panel summary says INCOMPLETE
        "VERDICT: MAYBE\n",
        "VERDICT: PASS - looks fine\n",
        "VERDICT:\n",
        "VERDICT PASS\n",
        "**VERDICT: PASS**\n",
    ],
)
def test_a_first_line_that_is_not_exactly_a_verdict_raises(text):
    with pytest.raises(ReviewFormatError):
        parse_review_markdown(text, "spec")


def test_verdict_is_case_insensitive_and_stored_upper_case():
    assert parse_review_markdown("verdict: changes  \n", "spec").verdict == "CHANGES"


def test_prior_statuses():
    review = parse_review_markdown("VERDICT: PASS\n## Earlier findings\n| ID | Status |\n| R1 | fixed |\n", "spec")
    assert review.prior == [PriorStatus("R1", "fixed")]


def test_tables_need_no_separator_row_and_no_trailing_pipe():
    text = rows("VERDICT: CHANGES", "## Findings", "| ID | Severity | Location | Finding | Fix",
                "| R2 | major | src/x.py:40 | a | b")
    assert parse_review_markdown(text, "spec").findings == [Finding("R2", "major", "src/x.py:40", "a", "b")]


def test_prose_outside_the_findings_sections_is_ignored():
    # Prose before the first heading, and under a heading that is neither table's, is not a finding list.
    text = ("VERDICT: PASS\n\nSummary: all good.\n\n## Findings\nNone.\n\n"
            "## Earlier findings\nnone\n\n## Notes\n- Checked twice.\nSee the spec.\n")
    review = parse_review_markdown(text, "spec")
    assert (review.verdict, review.findings, review.prior) == ("PASS", [], [])


@pytest.mark.parametrize("none", ["None.", "None", "none", "NONE.", "  None.  "])
def test_none_marks_an_empty_findings_section(none):
    review = parse_review_markdown(f"VERDICT: PASS\n## Findings\n{none}\n## Earlier findings\n{none}\n", "spec")
    assert (review.findings, review.prior) == ([], [])


def test_none_may_sit_beside_a_table_and_blank_lines_are_fine():
    text = "VERDICT: CHANGES\n## Findings\n\nNone.\n\n" + HEADER + "| R1 | major | a.py:1 | x | y |\n\n"
    assert parse_review_markdown(text, "spec").findings == [Finding("R1", "major", "a.py:1", "x", "y")]


@pytest.mark.parametrize(
    "section, body",
    [
        ("Findings", "- R1 major a.py:1 the loop never ends\n"),  # findings as a bullet list, not a table
        ("Findings", "* R1 (blocker) a.py:1 the loop never ends\n"),
        ("Findings", "R1: major - the loop never ends\n"),
        ("Findings", "There are no findings.\n"),
        ("Findings", "None found.\n"),
        ("Findings", "Nonexistent.\n"),
        ("Findings", "```\n| R1 | major | a.py:1 | x | y |\n```\n"),
        ("Findings", "None.\n- R1 major a.py:1 the loop never ends\n"),  # "None." does not excuse the list after it
        ("Earlier findings", "- R1 not fixed\n"),
    ],
)
def test_a_findings_section_that_is_not_a_table_or_none_raises(section, body):
    # Read as "no findings", a bullet list of blockers would let a PASS through.
    with pytest.raises(ReviewFormatError, match=rf"under '## {section}' write a table"):
        parse_review_markdown(f"VERDICT: PASS\n## {section}\n{body}", "correctness")


def test_the_bullet_list_case_names_its_line():
    text = "VERDICT: PASS\n## Findings\n- R1 major a.py:1 the loop never ends\n"
    with pytest.raises(ReviewFormatError, match=r"line 3: .*the loop never ends"):
        parse_review_markdown(text, "correctness")


def test_headings_are_case_insensitive_and_any_level():
    text = rows("VERDICT: PASS", "### EARLIER FINDINGS:", "| id | status |", "| R1 | fixed |",
                "# findings ##", "| Id | SEVERITY | location | Finding | fix |", "| R2 | nit | a.py | x | y |")
    review = parse_review_markdown(text, "spec")
    assert review.prior == [PriorStatus("R1", "fixed")] and review.findings[0].id == "R2"


def test_an_escaped_pipe_stays_inside_its_cell():
    text = "VERDICT: CHANGES\n## Findings\n" + HEADER + r"| R1 | major | a.py:3 | uses `a \|\| b` wrongly | use `a ?? b` |" + "\n"
    finding = parse_review_markdown(text, "spec").findings[0]
    assert finding.summary == "uses `a || b` wrongly" and finding.fix == "use `a ?? b`"


def test_severity_and_status_are_case_insensitive_and_stored_lower_case():
    text = (
        "VERDICT: CHANGES\n## Earlier findings\n| ID | Status |\n| R1 | Not  Fixed |\n"
        "## Findings\n" + HEADER + "| R2 | MAJOR | a.py:1 | x | y |\n"
    )
    review = parse_review_markdown(text, "spec")
    assert review.findings[0].severity == "major" and review.prior[0].status == "not fixed"


def test_severity_and_status_constants():
    assert SEVERITIES == ("blocker", "major", "minor", "nit")
    assert PRIOR_STATUSES == ("fixed", "not fixed", "no longer applies")


@pytest.mark.parametrize("severity", ["high", "critical", "", "**major**", "major."])
def test_unknown_severity_raises(severity):
    text = "VERDICT: CHANGES\n## Findings\n" + HEADER + f"| R1 | {severity} | a.py:1 | x | y |\n"
    with pytest.raises(ReviewFormatError, match="severity"):
        parse_review_markdown(text, "spec")


@pytest.mark.parametrize("status", ["done", "", "wontfix", "partly fixed"])
def test_unknown_prior_status_raises(status):
    text = f"VERDICT: PASS\n## Earlier findings\n| ID | Status |\n| R1 | {status} |\n"
    with pytest.raises(ReviewFormatError, match="status"):
        parse_review_markdown(text, "spec")


def test_nit_overflow_warning():
    nits = "".join(f"| R{i} | nit | a.py:{i} | x | y |\n" for i in range(1, 7))
    review = parse_review_markdown("VERDICT: PASS\n## Findings\n" + HEADER + nits, "spec", nit_cap=5)
    assert len(review.warnings) == 1 and "6 nits" in review.warnings[0]
    assert len(review.findings) == 6  # nothing is dropped


def test_nits_at_the_cap_raise_no_warning():
    nits = "".join(f"| R{i} | nit | a.py:{i} | x | y |\n" for i in range(1, 6))
    assert parse_review_markdown("VERDICT: PASS\n## Findings\n" + HEADER + nits, "spec").warnings == []


def test_the_nit_cap_is_a_parameter():
    nits = "| R1 | nit | a.py:1 | x | y |\n| R2 | nit | a.py:2 | x | y |\n"
    text = "VERDICT: PASS\n## Findings\n" + HEADER + nits
    assert "2 nits" in parse_review_markdown(text, "spec", nit_cap=1).warnings[0]


@pytest.mark.parametrize(
    "body, message",
    [
        ("## Findings\n| ID | Severity | Fix |\n| R1 | major | y |\n", "header"),
        ("## Findings\n| ID | Severity | Location | Finding | Fix |\n| R1 | major | a.py | x |\n", "cells"),
        ("## Findings\n" + HEADER + "| R1 | major | a.py | uses a | b | y |\n", "cells"),
        ("## Earlier findings\n| ID | Status | Note |\n| R1 | fixed | ok |\n", "header"),
        ("## Findings\n" + HEADER + "|  | major | a.py | x | y |\n", "ID"),
        ("## Findings\n" + HEADER + "| R1 | major | a.py |  | y |\n", "finding text"),
        ("| ID | Status |\n| R1 | fixed |\n", "table"),  # a table before any heading
        ("## Summary\n| ID | Severity | Location | Finding | Fix |\n| R1 | major | a | b | c |\n", "table"),
        ("## Findings\n## Findings\n", "twice"),
    ],
)
def test_a_malformed_body_raises(body, message):
    with pytest.raises(ReviewFormatError, match=message):
        parse_review_markdown("VERDICT: CHANGES\n" + body, "spec")


def test_format_errors_name_the_line():
    text = "VERDICT: CHANGES\n## Findings\n" + HEADER + "| R1 | bad | a.py | x | y |\n"
    with pytest.raises(ReviewFormatError, match="line 5"):
        parse_review_markdown(text, "spec")


def test_review_format_error_is_a_value_error():
    assert issubclass(ReviewFormatError, ValueError)


# --------------------------------------------------------------------------- JSON form

def test_json_form_matches_markdown_form():
    assert parse_review_json(SAMPLE_JSON, "correctness") == parse_review_markdown(SAMPLE_MD, "correctness")


def test_json_without_lists_is_an_empty_review():
    review = parse_review_json({"verdict": "pass"}, "spec")
    assert (review.verdict, review.findings, review.prior) == ("PASS", [], [])


def test_json_severity_and_status_are_case_insensitive():
    data = {"verdict": "CHANGES",
            "findings": [{"id": "R1", "severity": "Blocker", "location": "a.py", "summary": "x", "fix": "y"}],
            "prior": [{"id": "R0", "status": "No Longer Applies"}]}
    review = parse_review_json(data, "spec")
    assert review.findings[0].severity == "blocker" and review.prior[0].status == "no longer applies"


def test_json_nit_overflow_warning():
    findings = [{"id": f"R{i}", "severity": "nit", "location": "", "summary": "x", "fix": ""} for i in range(1, 8)]
    review = parse_review_json({"verdict": "PASS", "findings": findings}, "spec", nit_cap=5)
    assert len(review.warnings) == 1 and "7 nits" in review.warnings[0]


@pytest.mark.parametrize(
    "data",
    [
        [],
        "VERDICT: PASS",
        {},
        {"verdict": "INCOMPLETE"},
        {"verdict": 1},
        {"verdict": "PASS", "findngs": []},  # a typo'd key must not hide findings
        {"verdict": "PASS", "findings": "none"},
        {"verdict": "PASS", "findings": ["x"]},
        {"verdict": "PASS", "findings": [{"id": "R1", "severity": "high", "location": "", "summary": "x", "fix": ""}]},
        {"verdict": "PASS", "findings": [{"id": "R1", "severity": "major", "location": "", "summary": "x", "fix": "", "extra": 1}]},
        {"verdict": "PASS", "findings": [{"id": "R1", "severity": "major", "location": 3, "summary": "x", "fix": ""}]},
        {"verdict": "PASS", "findings": [{"id": "", "severity": "major", "location": "", "summary": "x", "fix": ""}]},
        {"verdict": "PASS", "findings": [{"id": "R1", "severity": "major", "location": "", "summary": "", "fix": ""}]},
        {"verdict": "PASS", "prior": [{"id": "R1", "status": "done"}]},
        {"verdict": "PASS", "prior": [{"id": "R1"}]},
        {"verdict": "PASS", "prior": [{"id": "R1", "status": "fixed", "extra": 1}]},
    ],
)
def test_json_that_does_not_fit_raises(data):
    with pytest.raises(ReviewFormatError):
        parse_review_json(data, "spec")


# --------------------------------------------------------------------------- the schema

def schema_errors(schema: dict, value, where: str = "$") -> list[str]:
    """A small validator for the schema subset ``review.schema.json`` uses (jsonschema is not a dependency)."""
    errors: list[str] = []
    kind = schema.get("type")
    checks = {"object": dict, "array": list, "string": str}
    if kind in checks and not isinstance(value, checks[kind]):
        return [f"{where}: expected {kind}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: {value!r} is not one of {schema['enum']}")
    if kind == "object":
        properties = schema.get("properties", {})
        errors += [f"{where}: missing {key}" for key in schema.get("required", []) if key not in value]
        if schema.get("additionalProperties") is False:
            errors += [f"{where}: unexpected {key}" for key in value if key not in properties]
        for key, sub in properties.items():
            if key in value:
                errors += schema_errors(sub, value[key], f"{where}.{key}")
    if kind == "array":
        for index, item in enumerate(value):
            errors += schema_errors(schema["items"], item, f"{where}[{index}]")
    return errors


@pytest.fixture
def schema() -> dict:
    return json.loads((factory_home() / "schemas" / "review.schema.json").read_text(encoding="utf-8"))


def test_review_schema_accepts_sample(schema):
    assert schema_errors(schema, SAMPLE_JSON) == []
    assert set(SAMPLE_JSON) == set(schema["properties"]) == {"verdict", "findings", "prior"}
    assert schema["properties"]["verdict"]["enum"] == ["PASS", "CHANGES"]
    # ...and what the schema accepts, the parser accepts.
    assert parse_review_json(SAMPLE_JSON, "spec").verdict == "CHANGES"


def test_review_schema_rejects_what_the_parser_rejects(schema):
    bad_severity = {**SAMPLE_JSON, "findings": [{**SAMPLE_JSON["findings"][0], "severity": "high"}]}
    assert schema_errors(schema, bad_severity)
    assert schema_errors(schema, {**SAMPLE_JSON, "verdict": "INCOMPLETE"})
    assert schema_errors(schema, {"verdict": "PASS", "findings": []})  # every key is required (strict mode)
    assert schema_errors(schema, {**SAMPLE_JSON, "notes": "x"})


def test_review_schema_agrees_with_the_code(schema):
    finding = schema["properties"]["findings"]["items"]
    prior = schema["properties"]["prior"]["items"]
    assert set(finding["properties"]) == {f.name for f in dataclasses.fields(Finding)} == set(finding["required"])
    assert set(prior["properties"]) == {f.name for f in dataclasses.fields(PriorStatus)} == set(prior["required"])
    assert finding["properties"]["severity"]["enum"] == list(SEVERITIES)
    assert prior["properties"]["status"]["enum"] == list(PRIOR_STATUSES)
    for node in (schema, finding, prior):  # OpenAI structured output needs closed objects
        assert node["additionalProperties"] is False


# --------------------------------------------------------------------------- read_review_file

def write_bytes(tmp_path, data: bytes, name: str = "T1-r1-security.md"):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_read_stamped_markdown_file(tmp_path):
    path = write_bytes(tmp_path, (SAMPLE_MD + "\n" + STAMP + "\n").encode("utf-8"))
    assert read_review_file(path, "security") == parse_review_markdown(SAMPLE_MD, "security")


def test_read_stamped_json_file(tmp_path):
    text = json.dumps(SAMPLE_JSON, indent=2) + "\n\n" + STAMP + "\n\n"
    path = write_bytes(tmp_path, text.encode("utf-8"))
    assert read_review_file(path, "security") == parse_review_json(SAMPLE_JSON, "security")


def test_read_file_with_crlf_and_bom_and_stamp(tmp_path):
    text = "﻿" + (SAMPLE_MD + STAMP + "\n").replace("\n", "\r\n")
    assert read_review_file(write_bytes(tmp_path, text.encode("utf-8")), "spec") == parse_review_markdown(SAMPLE_MD, "spec")


def test_read_unstamped_files(tmp_path):
    assert read_review_file(write_bytes(tmp_path, SAMPLE_MD.encode()), "spec").verdict == "CHANGES"
    path = write_bytes(tmp_path, json.dumps(SAMPLE_JSON).encode(), "j.md")
    assert read_review_file(path, "spec").findings[0].id == "R4"


def test_only_a_trailing_factory_comment_is_dropped(tmp_path):
    middle = "VERDICT: PASS\n<!-- factory: note -->\nSee above.\n"
    assert read_review_file(write_bytes(tmp_path, middle.encode()), "spec").verdict == "PASS"
    # A comment that is not a factory stamp is left alone (it is prose, so it still parses).
    other = "VERDICT: PASS\n<!-- reviewed twice -->\n"
    assert read_review_file(write_bytes(tmp_path, other.encode()), "spec").verdict == "PASS"


def test_a_stamp_alone_is_an_empty_review(tmp_path):
    with pytest.raises(ReviewFormatError):
        read_review_file(write_bytes(tmp_path, (STAMP + "\n").encode()), "spec")


def test_an_empty_file_raises(tmp_path):
    with pytest.raises(ReviewFormatError):
        read_review_file(write_bytes(tmp_path, b""), "spec")


def test_invalid_json_raises(tmp_path):
    with pytest.raises(ReviewFormatError, match="JSON"):
        read_review_file(write_bytes(tmp_path, b'{"verdict": "PASS",\n' + STAMP.encode() + b"\n"), "spec")


def test_a_file_that_is_not_utf8_raises(tmp_path):
    with pytest.raises(ReviewFormatError, match="UTF-8"):
        read_review_file(write_bytes(tmp_path, b"VERDICT: PASS\n\xff\xfe\n"), "spec")


def test_a_missing_file_is_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_review_file(tmp_path / "absent.md", "spec")


def test_the_nit_cap_reaches_read_review_file(tmp_path):
    text = "VERDICT: PASS\n## Findings\n" + HEADER + "| R1 | nit | a | x | y |\n| R2 | nit | a | x | y |\n"
    review = read_review_file(write_bytes(tmp_path, text.encode()), "spec", nit_cap=1)
    assert "2 nits" in review.warnings[0]


def test_a_file_the_real_adapter_stamped_parses(tmp_path, table, fake_cli):
    out = tmp_path / "T1-r1-security.md"
    resolved = resolve_role(table, "lens-security", host="claude", author="claude")
    result = run_invocation(plan_invocation(resolved, "claude", out), resolved, "prompt", out)
    assert result.ok and out.read_text(encoding="utf-8").rstrip().splitlines()[-1].startswith("<!-- factory:")
    assert read_review_file(out, "security").verdict == "PASS"


# --------------------------------------------------------------------------- paths

def test_review_and_panel_paths(tmp_path):
    assert review_path(tmp_path, "M1-T2a", 3, "security") == tmp_path / "reviews" / "M1-T2a-r3-security.md"
    assert panel_path(tmp_path, "T1", 1) == tmp_path / "reviews" / "T1-r1-panel.md"


@pytest.mark.parametrize("task", ["", "../x", "a/b", "a\\b", ".hidden", "T 1"])
def test_review_path_rejects_a_task_that_is_not_a_file_name_part(tmp_path, task):
    with pytest.raises(ValueError, match="task"):
        review_path(tmp_path, task, 1, "security")


@pytest.mark.parametrize("lens", ["", "../x", "a/b", "Security"])
def test_review_path_rejects_a_lens_that_is_not_a_file_name_part(tmp_path, lens):
    with pytest.raises(ValueError, match="lens"):
        review_path(tmp_path, "T1", 1, lens)


def test_review_path_rejects_a_round_below_one(tmp_path):
    with pytest.raises(ValueError, match="round"):
        review_path(tmp_path, "T1", 0, "security")
