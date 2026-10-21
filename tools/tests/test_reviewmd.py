"""Tests for swfactory.reviewmd: loading a project's REVIEW.md and checking its lens sets."""
from __future__ import annotations

import pytest

from swfactory.config import ConfigError, load_role_table, factory_home
from swfactory.reviewmd import ReviewConfig, check_panel_vendor_mix, load_review_md

REVIEW_MD = """\
---
lenses: [correctness, security, maintainability, spec]
tracks:
  fix: [correctness, security]
  feature: [correctness, security, maintainability]
  epic: [correctness, security, maintainability, spec]
nit_cap: 4
ignore: [generated/**]
always_flag:
  - never change XML serialization
---
# Review checklist

Read the lens section for your lens only.

## Lens: correctness
- Logic and edge cases.
- Tests prove the behaviour.

## Lens: security

### Inputs
- Validate input at every boundary.

## Lens: maintainability
- Duplication and naming.

## Lens: spec
- Every requirement is addressed.

## Notes
Prose that belongs to no lens.
"""


def write(path, text):
    path.write_text(text, encoding="utf-8", newline="")
    return path


def test_load_review_md_tracks(tmp_path):
    cfg = load_review_md(write(tmp_path / "REVIEW.md", REVIEW_MD))
    assert cfg.tracks["fix"] == ["correctness", "security"] and "## Lens" not in cfg.lenses["security"]
    assert list(cfg.lenses) == ["correctness", "security", "maintainability", "spec"]
    assert cfg.tracks["epic"] == ["correctness", "security", "maintainability", "spec"]


def test_load_review_md_reads_settings_and_checklists(tmp_path):
    cfg = load_review_md(write(tmp_path / "REVIEW.md", REVIEW_MD))
    assert isinstance(cfg, ReviewConfig)
    assert (cfg.nit_cap, cfg.ignore, cfg.always_flag) == (4, ["generated/**"], ["never change XML serialization"])
    assert cfg.lenses["correctness"] == "- Logic and edge cases.\n- Tests prove the behaviour."
    # A lens ends at the next level-2 heading; its own sub-headings stay.
    assert cfg.lenses["security"] == "### Inputs\n- Validate input at every boundary."
    assert "Prose that belongs to no lens" not in cfg.lenses["spec"]


def test_load_review_md_defaults(tmp_path):
    text = "---\nlenses: [correctness]\ntracks: {fix: [correctness]}\n---\n## Lens: correctness\n- check\n"
    cfg = load_review_md(write(tmp_path / "REVIEW.md", text))
    assert (cfg.nit_cap, cfg.ignore, cfg.always_flag) == (5, [], [])


def test_load_review_md_crlf_and_bom_match_lf(tmp_path):
    lf = load_review_md(write(tmp_path / "lf.md", REVIEW_MD))
    crlf = tmp_path / "crlf.md"
    crlf.write_bytes(b"\xef\xbb\xbf" + REVIEW_MD.replace("\n", "\r\n").encode("utf-8"))
    assert load_review_md(crlf) == lf


def test_load_review_md_a_lens_name_in_a_heading_is_case_insensitive(tmp_path):
    text = "---\nlenses: [security]\ntracks: {fix: [security]}\n---\n## Lens: Security\n- check\n"
    assert load_review_md(write(tmp_path / "REVIEW.md", text)).lenses == {"security": "- check"}


def problems_of(tmp_path, text) -> list[str]:
    with pytest.raises(ConfigError) as err:
        load_review_md(write(tmp_path / "REVIEW.md", text))
    return err.value.problems


def test_load_review_md_missing_file(tmp_path):
    with pytest.raises(ConfigError) as err:
        load_review_md(tmp_path / "REVIEW.md")
    assert "file not found" in err.value.problems[0]


def test_load_review_md_needs_front_matter(tmp_path):
    problems = problems_of(tmp_path, "# Review\n\n## Lens: correctness\n- check\n")
    assert len(problems) == 1 and "front matter" in problems[0]


def test_load_review_md_unclosed_front_matter(tmp_path):
    problems = problems_of(tmp_path, "---\nlenses: [correctness]\n## Lens: correctness\n- check\n")
    assert len(problems) == 1 and "front matter" in problems[0]


def test_load_review_md_invalid_yaml(tmp_path):
    problems = problems_of(tmp_path, "---\nlenses: [correctness\n---\n")
    assert len(problems) == 1 and "invalid YAML" in problems[0]


def test_load_review_md_reports_every_problem_at_once(tmp_path):
    text = """\
---
lenses: [correctness, security, ../escape]
tracks:
  fix: [correctness, ux]
  feature: []
  hotfix: [correctness]
nit_cap: -1
always_flag: rule
colour: blue
---
## Lens: correctness
- check
## Lens: correctness
- again
## Lens: spec
- check
"""
    joined = "\n".join(problems_of(tmp_path, text))
    for expected in (
        "'../escape'",  # a lens name is a file-name part
        "lens 'security' has no '## Lens: security' section",
        "'## Lens: correctness' appears more than once",
        "'## Lens: spec' is not listed in lenses",
        "track 'fix' uses lens 'ux', which is not listed in lenses",
        "track 'feature' needs at least one lens",
        "unknown track 'hotfix'",
        "nit_cap",
        "always_flag",
        "unknown key 'colour'",
    ):
        assert expected in joined, expected


def test_the_old_program_track_is_unknown(tmp_path):
    """A REVIEW.md from before F17 names the epic track by its old name: the check says which tracks it expects."""
    joined = "\n".join(problems_of(tmp_path, REVIEW_MD.replace("  epic:", "  program:")))
    assert "unknown track 'program' (expected epic, feature, fix)" in joined


def test_load_review_md_requires_lenses_and_tracks(tmp_path):
    joined = "\n".join(problems_of(tmp_path, "---\nnit_cap: 3\n---\n"))
    assert "lenses is required" in joined and "tracks is required" in joined


def test_load_review_md_empty_checklist_is_a_problem(tmp_path):
    text = "---\nlenses: [correctness]\ntracks: {fix: [correctness]}\n---\n## Lens: correctness\n\n## Notes\nx\n"
    assert any("no checklist" in p for p in problems_of(tmp_path, text))


# --------------------------------------------------------------------------- check_panel_vendor_mix

def cfg_with(tracks: dict[str, list[str]]) -> ReviewConfig:
    names = sorted({lens for lenses in tracks.values() for lens in lenses})
    return ReviewConfig(lenses={n: "- check" for n in names}, tracks=tracks, nit_cap=5, ignore=[], always_flag=[])


def test_track_without_other_vendor_lens_flagged(table):
    problems = check_panel_vendor_mix(cfg_with({"fix": ["maintainability"]}), table)
    assert len(problems) == 1 and "fix" in problems[0] and "other" in problems[0]


def test_default_lens_sets_all_have_an_other_vendor_lens(table, tmp_path):
    cfg = load_review_md(write(tmp_path / "REVIEW.md", REVIEW_MD))
    assert check_panel_vendor_mix(cfg, table) == []


def test_one_problem_per_offending_track_in_file_order(table):
    cfg = cfg_with({"epic": ["spec"], "fix": ["correctness"], "feature": ["maintainability", "spec"]})
    problems = check_panel_vendor_mix(cfg, table)
    assert len(problems) == 2
    assert "epic" in problems[0] and "feature" in problems[1]


def test_a_lens_without_a_role_does_not_count(table):
    # "ux" has no lens-ux role in the table, so it cannot supply the other vendor.
    assert len(check_panel_vendor_mix(cfg_with({"fix": ["ux"]}), table)) == 1
    assert check_panel_vendor_mix(cfg_with({"fix": ["ux", "security"]}), table) == []


def test_vendor_mix_follows_the_table_it_is_given():
    overridden = load_role_table(
        factory_home() / "config" / "roles.yaml", {"lens-maintainability": {"vendor": "other"}}
    )
    assert check_panel_vendor_mix(cfg_with({"fix": ["maintainability"]}), overridden) == []
