"""Tests for swfactory.config (role table, skills registry, project config) and the config CLI."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from swfactory import __version__, cli
from swfactory.config import (
    DEFAULT_HARD_STOPS,
    Binding,
    ConfigError,
    SkillReq,
    check_config,
    factory_home,
    load_project_config,
    load_role_table,
    load_skills_registry,
)

# --------------------------------------------------------------------------- helpers

MINIMAL_PROJECT = {
    "project": "demo",
    "approvers": ["owner"],
    "verify": {"quick": "pytest -q", "full": "pytest"},
}

# role: (stage, vendor, run_as, access, claude binding, openai binding)
EXPECTED_ROLES = {
    "interviewer": ("plan", "host", "main", "write", ("claude-fable-5-1", "xhigh"), ("gpt-6.1-sol", "xhigh")),
    "researcher": ("plan", "host", "subagent", "read-only", ("claude-sonnet-5-5", "high"), ("gpt-6.1-sol", "high")),
    "author": ("design", "host", "main", "write", ("claude-fable-5-1", "xhigh"), ("gpt-6.1-sol", "xhigh")),
    "doc-reviewer-a": ("design", "same", "subagent", "read-only", ("claude-opus-5-5", "xhigh"), ("gpt-6.1-sol", "xhigh")),
    "doc-reviewer-b": ("design", "other", "headless", "read-only", ("claude-opus-5-5", "xhigh"), ("gpt-6-astra", "xhigh")),
    "implementer": ("build", "host", "main", "write", ("claude-opus-5-5", "xhigh"), ("gpt-6.1-sol", "xhigh")),
    "lens-correctness": ("build", "other", "headless", "read-only", ("claude-opus-5-5", "xhigh"), ("gpt-6.1-sol", "xhigh")),
    "lens-security": ("build", "other", "headless", "read-only", ("claude-opus-5-5", "xhigh"), ("gpt-6.1-sol", "xhigh")),
    "lens-maintainability": ("build", "same", "subagent", "read-only", ("claude-sonnet-5-5", "high"), ("gpt-6.1-sol", "high")),
    "lens-spec": ("build", "same", "subagent", "read-only", ("claude-opus-5-5", "high"), ("gpt-6.1-sol", "high")),
    "tester": ("test", "host", "subagent", "no-product-edits", ("claude-sonnet-5-5", "high"), ("gpt-6.1-sol", "high")),
    "release-checker": ("deploy", "host", "headless", "read-only", ("claude-sonnet-5-5", "high"), ("gpt-6.1-sol", "high")),
    "triage": ("maintain", "host", "headless", "read-only", ("claude-sonnet-5-5", "high"), ("gpt-6.1-sol", "high")),
}

# Every skill name later tasks (lint, readiness) may reference.
REQUIRED_REGISTERED_SKILLS = {
    "grilling", "grill-with-docs", "research", "domain-modeling", "last30days", "to-spec",
    "codebase-design", "diagram-design", "writing-plans", "subagent-driven-development",
    "to-tickets", "wayfinder", "implement", "tdd", "diagnosing-bugs",
    "verification-before-completion", "code-review", "security-review", "deploy-checklist",
    "triage", "incident-response", "handoff",
}


def default_roles_path() -> Path:
    return factory_home() / "config/roles.yaml"


def default_skills_path() -> Path:
    return factory_home() / "config/skills.yaml"


def real_roles_data() -> dict:
    return yaml.safe_load(default_roles_path().read_text(encoding="utf-8"))


def write_yaml(path: Path, data: object) -> Path:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def write_project(tmp_path: Path, **overrides: object) -> Path:
    """Write a factory.yaml (minimal valid plus overrides; a None value drops the key)."""
    data = {**MINIMAL_PROJECT, **overrides}
    data = {k: v for k, v in data.items() if v is not None}
    return write_yaml(tmp_path / "factory.yaml", data)


# --------------------------------------------------------------------------- factory_home

def test_factory_home_defaults_to_repo_root():
    home = factory_home()
    assert (home / "tools/swfactory/config.py").is_file()
    assert (home / "config/roles.yaml").is_file()


def test_factory_home_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("FACTORY_HOME", str(tmp_path))
    assert factory_home() == tmp_path


def test_factory_home_empty_env_is_ignored(monkeypatch):
    monkeypatch.setenv("FACTORY_HOME", "")
    assert (factory_home() / "config/roles.yaml").is_file()


# --------------------------------------------------------------------------- role table

def test_default_role_table_loads():
    t = load_role_table(factory_home() / "config/roles.yaml")
    assert t.roles["lens-correctness"].vendor == "other"
    assert t.roles["implementer"].bindings["claude"] == Binding("claude-opus-5-5", "xhigh")


def test_every_role_has_binding_and_fallback_for_both_vendors():
    t = load_role_table(factory_home() / "config/roles.yaml")
    for r in t.roles.values():
        assert set(r.bindings) == {"claude", "openai"} and set(r.fallback) == {"claude", "openai"}


def test_default_role_table_has_exactly_the_designed_roles():
    t = load_role_table(default_roles_path())
    assert set(t.roles) == set(EXPECTED_ROLES)


@pytest.mark.parametrize("role", sorted(EXPECTED_ROLES))
def test_default_role_values_match_design(role):
    stage, vendor, run_as, access, claude, openai = EXPECTED_ROLES[role]
    r = load_role_table(default_roles_path()).roles[role]
    assert (r.name, r.stage, r.vendor, r.run_as, r.access) == (role, stage, vendor, run_as, access)
    assert r.bindings["claude"] == Binding(*claude)
    assert r.bindings["openai"] == Binding(*openai)


@pytest.mark.parametrize("role", sorted(EXPECTED_ROLES))
def test_default_fallbacks_step_down(role):
    r = load_role_table(default_roles_path()).roles[role]
    # A fallback is never the same binding it replaces, on either vendor.
    for vendor in ("claude", "openai"):
        assert r.fallback[vendor] != r.bindings[vendor]
    # Opus and Fable roles fall back to Sonnet; the OpenAI side falls back to gpt-6-astra
    # (doc-reviewer-b already runs on it, so its fallback is gpt-6.1-sol).
    if r.bindings["claude"].model != "claude-sonnet-5-5":
        assert r.fallback["claude"].model == "claude-sonnet-5-5"
    expected_openai = "gpt-6.1-sol" if r.bindings["openai"].model == "gpt-6-astra" else "gpt-6-astra"
    assert r.fallback["openai"].model == expected_openai
    assert r.fallback["openai"].effort == r.bindings["openai"].effort


def test_unknown_vendor_rejected(tmp_path):
    data = real_roles_data()
    data["roles"]["lens-correctness"]["vendor"] = "banana"
    path = write_yaml(tmp_path / "roles.yaml", data)
    with pytest.raises(ConfigError) as e:
        load_role_table(path)
    assert any("lens-correctness" in p and "vendor" in p for p in e.value.problems)


def test_all_problems_collected_before_raising(tmp_path):
    data = real_roles_data()
    data["roles"]["lens-correctness"]["vendor"] = "banana"
    data["roles"]["tester"]["run_as"] = "sideways"
    data["roles"]["author"]["access"] = "root"
    del data["roles"]["implementer"]["bindings"]["openai"]
    data["roles"]["triage"]["fallback"]["claude"] = {"model": "", "effort": "high"}
    path = write_yaml(tmp_path / "roles.yaml", data)
    with pytest.raises(ConfigError) as e:
        load_role_table(path)
    problems = e.value.problems
    assert len(problems) == 5
    for role, word in [("lens-correctness", "vendor"), ("tester", "run_as"), ("author", "access"),
                       ("implementer", "openai"), ("triage", "model")]:
        assert any(role in p and word in p for p in problems), (role, problems)


def test_role_missing_a_vendor_binding_or_fallback_rejected(tmp_path):
    data = real_roles_data()
    del data["roles"]["tester"]["fallback"]["openai"]
    path = write_yaml(tmp_path / "roles.yaml", data)
    with pytest.raises(ConfigError) as e:
        load_role_table(path)
    assert any("tester" in p and "fallback" in p and "openai" in p for p in e.value.problems)


def test_unknown_role_field_and_stage_rejected(tmp_path):
    data = real_roles_data()
    data["roles"]["tester"]["colour"] = "red"
    data["roles"]["triage"]["stage"] = "party"
    path = write_yaml(tmp_path / "roles.yaml", data)
    with pytest.raises(ConfigError) as e:
        load_role_table(path)
    assert any("tester" in p and "colour" in p for p in e.value.problems)
    assert any("triage" in p and "stage" in p for p in e.value.problems)


@pytest.mark.parametrize("content", ["", "- a list\n- not a mapping\n", "roles: {}\n", "roles: [unclosed\n"])
def test_roles_file_with_wrong_shape_rejected(tmp_path, content):
    path = tmp_path / "roles.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_role_table(path)
    assert e.value.problems


def test_roles_file_missing_rejected(tmp_path):
    with pytest.raises(ConfigError) as e:
        load_role_table(tmp_path / "nope.yaml")
    assert len(e.value.problems) == 1 and "nope.yaml" in e.value.problems[0]


def test_roles_file_with_crlf_and_bom_loads_the_same(tmp_path):
    text = default_roles_path().read_text(encoding="utf-8")
    path = tmp_path / "dir with spaces"
    path.mkdir()
    path = path / "roles.yaml"
    path.write_bytes(b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8"))
    assert load_role_table(path) == load_role_table(default_roles_path())


def test_project_override_replaces_one_binding():
    overrides = {"tester": {"bindings": {"claude": {"model": "x", "effort": "low"}}}}
    t = load_role_table(factory_home() / "config/roles.yaml", overrides=overrides)
    assert t.roles["tester"].bindings["claude"] == Binding("x", "low")
    assert t.roles["tester"].bindings["openai"].model == "gpt-6.1-sol"


def test_project_override_can_change_one_field_and_leaves_other_roles_alone():
    base = load_role_table(default_roles_path())
    t = load_role_table(default_roles_path(), overrides={
        "tester": {"bindings": {"claude": {"effort": "low"}}},
        "lens-security": {"vendor": "claude"},
    })
    assert t.roles["tester"].bindings["claude"] == Binding("claude-sonnet-5-5", "low")
    assert t.roles["lens-security"].vendor == "claude"
    untouched = set(base.roles) - {"tester", "lens-security"}
    assert all(t.roles[n] == base.roles[n] for n in untouched)


def test_project_override_does_not_change_the_default_table():
    load_role_table(default_roles_path(), overrides={"tester": {"vendor": "claude"}})
    assert load_role_table(default_roles_path()).roles["tester"].vendor == "host"


def test_project_override_problems_collected_and_name_the_override():
    overrides = {
        "ghost": {"vendor": "claude"},
        "tester": {"vendor": "banana", "colour": "red"},
        "triage": "not a mapping",
    }
    with pytest.raises(ConfigError) as e:
        load_role_table(default_roles_path(), overrides=overrides)
    problems = e.value.problems
    assert len(problems) == 4
    assert any("ghost" in p and "unknown role" in p for p in problems)
    assert any("tester" in p and "colour" in p and "override" in p for p in problems)
    assert any("tester" in p and "vendor" in p and "override" in p for p in problems)
    assert any("triage" in p and "mapping" in p for p in problems)


# --------------------------------------------------------------------------- skills registry

def test_skills_registry_loads_and_registers_every_skill():
    reg = load_skills_registry(default_skills_path())
    names = {s.name for skills in reg.values() for s in skills}
    assert REQUIRED_REGISTERED_SKILLS <= names


def test_skills_registry_marks_interactive_skills():
    reg = load_skills_registry(default_skills_path())
    interactive = {s.name for skills in reg.values() for s in skills if s.interactive}
    assert interactive == {"grilling", "grill-with-docs"}
    grilling = next(s for s in reg["interviewer"] if s.name == "grilling")
    assert grilling == SkillReq("grilling", True, "grill-me", True)


def test_skills_registry_assigns_skills_to_roles():
    reg = load_skills_registry(default_skills_path())

    def names(role: str) -> set[str]:
        return {s.name for s in reg[role]}

    assert {"grilling", "research", "domain-modeling"} <= names("interviewer")
    assert {"to-spec", "codebase-design", "diagram-design", "writing-plans", "to-tickets", "wayfinder"} <= names("author")
    assert {"tdd", "implement", "subagent-driven-development", "diagnosing-bugs"} <= names("implementer")
    assert {"verification-before-completion", "tdd"} <= names("tester")
    for lens in ("lens-correctness", "lens-security", "lens-maintainability", "lens-spec"):
        assert "code-review" in names(lens)
    assert "security-review" in names("lens-security")
    assert "deploy-checklist" in names("release-checker")
    assert {"triage", "incident-response"} <= names("triage")
    assert "handoff" in names("*")


def test_interactive_skills_only_in_interactive_roles():
    # Design section 13: interactive skills are never used inside unattended roles.
    table = load_role_table(default_roles_path())
    reg = load_skills_registry(default_skills_path())
    for role, skills in reg.items():
        if role == "*":
            assert not any(s.interactive for s in skills)
        elif any(s.interactive for s in skills):
            assert table.roles[role].run_as == "main"


def test_skills_registry_roles_exist_in_role_table():
    reg = load_skills_registry(default_skills_path())
    table = load_role_table(default_roles_path())
    assert set(reg) - {"*"} <= set(table.roles)


def test_skills_registry_fills_entry_defaults(tmp_path):
    path = write_yaml(tmp_path / "skills.yaml", {"roles": {"tester": [{"name": "tdd"}]}})
    assert load_skills_registry(path) == {"tester": [SkillReq("tdd", False, None, False)]}


def test_skills_registry_problems_collected(tmp_path):
    data = {"roles": {
        "tester": [
            {"required": True},                                  # no name
            {"name": "tdd", "required": "yes"},                  # not a bool
            {"name": "a", "colour": "red"},                      # unknown key
            {"name": "dup"}, {"name": "dup"},                    # duplicate
            "not-a-mapping",
        ],
        "implementer": "tdd",                                    # not a list
    }}
    path = write_yaml(tmp_path / "skills.yaml", data)
    with pytest.raises(ConfigError) as e:
        load_skills_registry(path)
    problems = e.value.problems
    assert len(problems) == 6
    assert any("tester" in p and "name" in p for p in problems)
    assert any("tester" in p and "required" in p for p in problems)
    assert any("tester" in p and "colour" in p for p in problems)
    assert any("tester" in p and "dup" in p for p in problems)
    assert any("tester" in p and "mapping" in p for p in problems)
    assert any("implementer" in p and "list" in p for p in problems)


# --------------------------------------------------------------------------- project config

def test_project_config_defaults(tmp_path):
    p = write_project(tmp_path)
    c = load_project_config(p)
    assert (c.size_cap, c.max_rounds, c.mode, c.branching, c.builds) == (400, 3, "review", "per-item", "allow")
    assert c.hard_stops == DEFAULT_HARD_STOPS
    assert (c.project, c.base_branch, c.work_branch) == ("demo", "main", None)
    assert (c.approvers, c.verify_quick, c.verify_full) == (["owner"], "pytest -q", "pytest")
    assert (c.generated, c.role_overrides, c.github_mirror) == ([], {}, False)


def test_default_hard_stops_are_the_ten_designed_ids():
    assert DEFAULT_HARD_STOPS == [
        "main-branch", "delete-data", "credentials", "installers-admin", "purchases",
        "auth-money-schema", "new-dependency", "production-data", "third-failed-round",
        "business-assumption",
    ]


def test_project_config_hard_stops_default_is_a_copy(tmp_path):
    c = load_project_config(write_project(tmp_path))
    c.hard_stops.append("mutated")
    assert "mutated" not in DEFAULT_HARD_STOPS


def test_project_config_reads_every_key(tmp_path):
    p = write_project(
        tmp_path,
        version=1,
        base_branch="develop",
        branching="single",
        work_branch="factory/work",
        mode="auto",
        approvers=["a", "b"],
        builds="ask",
        merge={"size_cap": 250, "generated": ["**/*.g.cs", "docs/gen/**"]},
        review={"max_rounds": 2},
        hard_stops=["main-branch", "xml-serialization"],
        roles={"tester": {"vendor": "claude"}},
        github_mirror=True,
    )
    c = load_project_config(p)
    assert (c.base_branch, c.branching, c.work_branch, c.mode) == ("develop", "single", "factory/work", "auto")
    assert (c.approvers, c.builds, c.size_cap, c.max_rounds) == (["a", "b"], "ask", 250, 2)
    assert c.generated == ["**/*.g.cs", "docs/gen/**"]
    assert c.hard_stops == ["main-branch", "xml-serialization"]
    assert c.role_overrides == {"tester": {"vendor": "claude"}}
    assert c.github_mirror is True


def test_project_config_rejects_bad_mode_and_single_without_branch(tmp_path):
    p = write_project(tmp_path, mode="turbo", branching="single")
    with pytest.raises(ConfigError) as e:
        load_project_config(p)
    problems = e.value.problems
    assert len(problems) == 2
    assert any("mode" in x and "turbo" in x for x in problems)
    assert any("work_branch" in x and "single" in x for x in problems)


@pytest.mark.parametrize("base", [None, "develop"])  # None: base_branch left to its default, main
def test_project_config_rejects_single_branching_on_the_base_branch(tmp_path, base):
    p = write_project(tmp_path, base_branch=base, branching="single", work_branch=base or "main")
    with pytest.raises(ConfigError) as e:
        load_project_config(p)
    problems = e.value.problems
    assert len(problems) == 1
    assert "work_branch" in problems[0] and "base_branch" in problems[0] and (base or "main") in problems[0]


def test_project_config_per_item_ignores_a_work_branch_equal_to_the_base(tmp_path):
    c = load_project_config(write_project(tmp_path, branching="per-item", work_branch="main"))
    assert (c.branching, c.base_branch) == ("per-item", "main")


def test_project_config_missing_required_keys_all_reported(tmp_path):
    p = write_yaml(tmp_path / "factory.yaml", {"version": 1})
    with pytest.raises(ConfigError) as e:
        load_project_config(p)
    problems = e.value.problems
    assert len(problems) == 4
    for key in ("project", "approvers", "verify.quick", "verify.full"):
        assert any(key in x for x in problems), (key, problems)


def test_project_config_empty_approvers_rejected(tmp_path):
    with pytest.raises(ConfigError) as e:
        load_project_config(write_project(tmp_path, approvers=[]))
    assert any("approvers" in x for x in e.value.problems)


@pytest.mark.parametrize("overrides, word", [
    ({"branching": "tree"}, "branching"),
    ({"builds": "never"}, "builds"),
    ({"version": 2}, "version"),
    ({"merge": {"size_cap": 0}}, "size_cap"),
    ({"merge": {"size_cap": True}}, "size_cap"),
    ({"merge": {"size_cap": "big"}}, "size_cap"),
    ({"merge": {"generated": "*.g.cs"}}, "generated"),
    ({"review": {"max_rounds": 0}}, "max_rounds"),
    ({"review": {"max_rounds": 2.5}}, "max_rounds"),
    ({"hard_stops": "main-branch"}, "hard_stops"),
    ({"roles": ["tester"]}, "roles"),
    ({"github_mirror": "yes"}, "github_mirror"),
    ({"approvers": "owner"}, "approvers"),
    ({"verify": {"quick": "", "full": "pytest"}}, "verify.quick"),
])
def test_project_config_rejects_bad_values(tmp_path, overrides, word):
    with pytest.raises(ConfigError) as e:
        load_project_config(write_project(tmp_path, **overrides))
    assert any(word in x for x in e.value.problems), e.value.problems


def test_project_config_unknown_top_level_keys_are_ignored(tmp_path):
    # factory.yaml also carries keys later plans add (design 6.2: tracks, deploy paths, signals).
    c = load_project_config(write_project(tmp_path, deploy={"installer": "build.ps1"}))
    assert c.project == "demo"


@pytest.mark.parametrize("content", ["", "- a\n- b\n", "project: [unclosed\n"])
def test_project_config_wrong_shape_rejected(tmp_path, content):
    p = tmp_path / "factory.yaml"
    p.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_project_config(p)
    assert e.value.problems


def test_project_config_missing_file_rejected(tmp_path):
    with pytest.raises(ConfigError) as e:
        load_project_config(tmp_path / "factory.yaml")
    assert "factory.yaml" in e.value.problems[0]


def test_project_config_crlf_bom_and_spaces_in_path(tmp_path):
    folder = tmp_path / "my project"
    folder.mkdir()
    text = yaml.safe_dump(MINIMAL_PROJECT, sort_keys=False)
    (folder / "factory.yaml").write_bytes(b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8"))
    assert load_project_config(folder / "factory.yaml").project == "demo"


def test_config_error_message_lists_every_problem():
    err = ConfigError(["one", "two"])
    assert err.problems == ["one", "two"]
    assert str(err).splitlines() == ["one", "two"]


# --------------------------------------------------------------------------- check_config

def test_check_config_factory_defaults_ok():
    assert check_config() == []


def test_check_config_project_dir_or_file_ok(tmp_path):
    folder = tmp_path / "my project"
    folder.mkdir()
    write_project(folder)
    assert check_config(folder) == []
    assert check_config(folder / "factory.yaml") == []


def test_check_config_validates_project_role_overrides(tmp_path):
    write_project(tmp_path, roles={"tester": {"vendor": "banana"}, "ghost": {}})
    problems = check_config(tmp_path)
    assert any("tester" in p and "vendor" in p for p in problems)
    assert any("ghost" in p for p in problems)


LENSES = ("correctness", "security", "maintainability", "spec")
PANEL_TRACKS = {
    "fix": ["correctness", "security"],
    "feature": ["correctness", "security", "maintainability"],
    "program": ["correctness", "security", "maintainability", "spec"],
}


def write_review_md(folder: Path, tracks: dict[str, list[str]] = PANEL_TRACKS) -> Path:
    """A valid REVIEW.md with all four lenses and the given tracks."""
    front = yaml.safe_dump({"lenses": list(LENSES), "tracks": tracks}, sort_keys=False)
    sections = "".join(f"## Lens: {lens}\n- check the {lens}\n\n" for lens in LENSES)
    path = folder / "REVIEW.md"
    path.write_text(f"---\n{front}---\n{sections}", encoding="utf-8")
    return path


def test_check_config_project_with_a_good_review_md_ok(tmp_path):
    write_project(tmp_path)
    write_review_md(tmp_path)
    assert check_config(tmp_path) == []
    assert check_config(tmp_path / "factory.yaml") == []


def test_check_config_reports_review_md_load_problems(tmp_path):
    write_project(tmp_path)
    (tmp_path / "REVIEW.md").write_text("just prose, no front matter\n", encoding="utf-8")
    problems = check_config(tmp_path)
    assert len(problems) == 1 and "REVIEW.md" in problems[0] and "front matter" in problems[0]


def test_check_config_reports_a_track_with_no_other_vendor_lens(tmp_path):
    write_project(tmp_path)
    write_review_md(tmp_path, {**PANEL_TRACKS, "fix": ["maintainability"], "feature": ["spec"]})
    problems = check_config(tmp_path)
    assert len(problems) == 2
    assert all(p.startswith("REVIEW.md: track ") and "other vendor" in p for p in problems)
    assert any("'fix'" in p for p in problems) and any("'feature'" in p for p in problems)


def test_check_config_panel_check_uses_the_projects_role_overrides(tmp_path):
    write_project(tmp_path, roles={"lens-maintainability": {"vendor": "other"}})
    write_review_md(tmp_path, {**PANEL_TRACKS, "fix": ["maintainability"]})
    assert check_config(tmp_path) == []


def test_check_config_review_md_is_found_next_to_the_factory_yaml_only(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    write_project(folder)
    write_review_md(tmp_path, {"fix": ["maintainability"]})  # beside the project folder, not in it
    assert check_config(folder) == []


def test_check_config_skips_the_panel_check_when_the_factory_yaml_is_invalid(tmp_path):
    write_project(tmp_path, mode="turbo")
    write_review_md(tmp_path, {**PANEL_TRACKS, "fix": ["maintainability"]})
    problems = check_config(tmp_path)
    assert len(problems) == 1 and "mode" in problems[0]


def test_check_config_reports_review_md_load_problems_even_when_the_factory_yaml_is_invalid(tmp_path):
    write_project(tmp_path, mode="turbo")
    (tmp_path / "REVIEW.md").write_text("no front matter\n", encoding="utf-8")
    problems = check_config(tmp_path)
    assert any("mode" in p for p in problems) and any("REVIEW.md" in p for p in problems)


def test_check_config_reports_unknown_skill_role(monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    (home / "config/roles.yaml").write_bytes(default_roles_path().read_bytes())
    write_yaml(home / "config/skills.yaml", {"roles": {"ghost": [{"name": "tdd"}], "*": [{"name": "handoff"}]}})
    monkeypatch.setenv("FACTORY_HOME", str(home))
    problems = check_config()
    assert len(problems) == 1 and "ghost" in problems[0]


def test_check_config_collects_problems_from_every_file(monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    (home / "config/roles.yaml").write_text("roles: {}\n", encoding="utf-8")
    (home / "config/skills.yaml").write_text("roles: [oops\n", encoding="utf-8")
    monkeypatch.setenv("FACTORY_HOME", str(home))
    project = tmp_path / "proj"
    project.mkdir()
    write_yaml(project / "factory.yaml", {"version": 1})
    problems = check_config(project)
    assert len(problems) >= 6
    for fragment in ("project", "roles.yaml", "skills.yaml"):
        assert any(fragment in p for p in problems), (fragment, problems)


# --------------------------------------------------------------------------- CLI

def test_cli_version(capsys):
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "0.1.0" == __version__


def test_cli_without_command_prints_usage(capsys):
    assert cli.main([]) == 2
    assert "factory" in capsys.readouterr().out


def test_cli_commands_set_matches_parser():
    parser = cli.build_parser()
    registered = {
        name
        for action in parser._actions
        if action.__class__.__name__ == "_SubParsersAction"
        for name in action.choices
    }
    assert registered == cli.COMMANDS == {
        "config", "role", "panel", "work", "approve", "gate", "setup", "skills", "prompt",
    }


def test_cli_config_check_ok(capsys):
    assert cli.main(["config", "check"]) == 0
    assert capsys.readouterr().out.strip() == "config ok"


def test_cli_config_check_project_problems_one_per_line(tmp_path, capsys):
    write_project(tmp_path, mode="turbo", branching="single")
    assert cli.main(["config", "check", "--project", str(tmp_path)]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert any("mode" in x for x in lines) and any("work_branch" in x for x in lines)


def test_cli_config_check_project_ok(tmp_path, capsys):
    write_project(tmp_path)
    assert cli.main(["config", "check", "--project", str(tmp_path / "factory.yaml")]) == 0
    assert capsys.readouterr().out.strip() == "config ok"


def test_cli_config_check_reports_review_md_problems_one_per_line(tmp_path, capsys):
    write_project(tmp_path)
    write_review_md(tmp_path, {**PANEL_TRACKS, "fix": ["maintainability"], "feature": ["spec"]})
    assert cli.main(["config", "check", "--project", str(tmp_path)]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2 and all(x.startswith("REVIEW.md: ") and "other vendor" in x for x in lines)
    assert any("track 'fix'" in x for x in lines) and any("track 'feature'" in x for x in lines)


def test_cli_config_check_reports_a_review_md_that_does_not_load(tmp_path, capsys):
    write_project(tmp_path)
    write_review_md(tmp_path, {**PANEL_TRACKS, "feature": ["bogus"]})
    assert cli.main(["config", "check", "--project", str(tmp_path)]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1 and lines[0].startswith("REVIEW.md: ") and "bogus" in lines[0]


def test_cli_config_check_project_with_review_md_ok(tmp_path, capsys):
    write_project(tmp_path)
    write_review_md(tmp_path)
    assert cli.main(["config", "check", "--project", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == "config ok"


def test_python_dash_m_runs_the_cli():
    done = subprocess.run([sys.executable, "-m", "swfactory", "--version"], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and done.stdout.strip() == "0.1.0"
