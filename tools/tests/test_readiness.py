"""Tests for swfactory.readiness (skill search paths, installed skills, the readiness report), ``sw-factory skills
check`` and ``sw-factory skills path``.

Every test builds its own home folder under ``tmp_path`` and passes it explicitly; the autouse fixture also
points the environment at empty folders, so nothing reads the real home or the real ``%APPDATA%``.
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import pytest
import yaml

from swfactory import cli, readiness
from swfactory.config import SkillReq, factory_home, load_skills_registry
from swfactory.readiness import (
    ReadinessReport,
    check_readiness,
    find_installed_skills,
    skill_search_paths,
)

# --------------------------------------------------------------------------- helpers


@pytest.fixture(autouse=True)
def _hermetic_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No test sees the developer's skill folders: home variables and ``%APPDATA%`` point at empty folders."""
    empty = tmp_path / "ambient-home"
    appdata = tmp_path / "ambient-appdata"
    empty.mkdir()
    appdata.mkdir()
    monkeypatch.setenv("HOME", str(empty))
    monkeypatch.setenv("USERPROFILE", str(empty))
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.delenv("FACTORY_SKILL_PATHS", raising=False)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    folder = tmp_path / "home"
    folder.mkdir()
    return folder


def make_skill(root: Path, folder: str, name: str | None = None, raw: str | bytes | None = None) -> Path:
    """Create ``root/folder/SKILL.md`` (front matter naming ``name`` when given, else ``raw`` verbatim)."""
    skill = root / folder
    skill.mkdir(parents=True, exist_ok=True)
    if raw is None:
        raw = "---\n" + (f"name: {name}\n" if name else "") + "description: a test skill\n---\n# Body\n"
    skill_md = skill / "SKILL.md"
    skill_md.write_bytes(raw if isinstance(raw, bytes) else raw.encode("utf-8"))
    return skill


def req(name: str, *, required: bool = True, fallback: str | None = None, interactive: bool = False) -> SkillReq:
    return SkillReq(name=name, required=required, fallback=fallback, interactive=interactive)


def agents_skills(home: Path) -> Path:
    return home / ".agents" / "skills"


def try_symlink(link: Path, target: Path) -> None:
    """Make ``link`` a symlink to the folder ``target``, or skip the test where the platform refuses (Windows
    without developer mode)."""
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError) as err:
        pytest.skip(f"cannot create a symlink here: {err}")


# --------------------------------------------------------------------------- the readiness report


def test_present_and_missing(home, table):
    make_skill(agents_skills(home), "tdd")
    registry = {"implementer": [req("tdd"), req("grilling", fallback="grill-me")]}
    rep = check_readiness(registry, table, "claude", home, None)
    assert rep.host == "claude"
    assert rep.present == ["tdd"] and rep.missing == [("grilling", "grill-me")]
    assert rep.interactive_in_unattended == []


def test_plugin_cache_nested(home, table):
    make_skill(home / ".claude/plugins/cache/x/superpowers/7.0.0/skills", "writing-plans")
    rep = check_readiness({"author": [req("writing-plans")]}, table, "claude", home, None)
    assert rep.present == ["writing-plans"] and rep.missing == []


def test_hosts_search_their_own_plugin_caches_only(home, table):
    make_skill(home / ".claude/plugins/cache/x/superpowers/7.0.0/skills", "claude-only")
    make_skill(home / ".codex/plugins/cache/y/bundle/1.0.0/skills", "codex-only")
    registry = {"author": [req("claude-only"), req("codex-only")]}
    claude = check_readiness(registry, table, "claude", home, None)
    codex = check_readiness(registry, table, "codex", home, None)
    assert (claude.present, claude.missing) == (["claude-only"], [("codex-only", None)])
    assert (codex.present, codex.missing) == (["codex-only"], [("claude-only", None)])


def test_frontmatter_name_wins(home, table):
    make_skill(agents_skills(home), "wp", name="writing-plans")
    rep = check_readiness({"author": [req("writing-plans"), req("wp")]}, table, "codex", home, None)
    assert rep.present == ["writing-plans"] and rep.missing == [("wp", None)]


def test_env_extra_path(home, table, tmp_path, monkeypatch):
    extra = tmp_path / "extra-skills"
    other = tmp_path / "other-skills"
    make_skill(extra, "handoff")
    make_skill(other, "research")
    monkeypatch.setenv("FACTORY_SKILL_PATHS", os.pathsep.join([str(extra), "", str(other)]))
    registry = {"*": [req("handoff")], "researcher": [req("research")]}
    for host in ("claude", "codex"):
        rep = check_readiness(registry, table, host, home, None)
        assert rep.present == ["handoff", "research"] and rep.missing == []


def test_interactive_in_unattended_flagged(home, table):
    registry = {"tester": [req("grilling", interactive=True)]}  # tester runs as a subagent
    rep = check_readiness(registry, table, "claude", home, None)
    assert table.roles["tester"].run_as == "subagent"
    assert rep.interactive_in_unattended == ["grilling"]
    assert not rep.ok


def test_interactive_in_headless_role_flagged(home, table):
    registry = {"lens-correctness": [req("grilling", required=False, interactive=True)]}
    assert table.roles["lens-correctness"].run_as == "headless"
    rep = check_readiness(registry, table, "codex", home, None)
    assert rep.interactive_in_unattended == ["grilling"]  # an optional interactive skill counts too


def test_interactive_in_main_role_not_flagged(home, table):
    make_skill(agents_skills(home), "grilling")
    registry = {"interviewer": [req("grilling", interactive=True)]}
    assert table.roles["interviewer"].run_as == "main"
    rep = check_readiness(registry, table, "claude", home, None)
    assert rep.interactive_in_unattended == [] and rep.ok


def test_interactive_skill_for_every_role_is_flagged(home, table):
    rep = check_readiness({"*": [req("grilling", required=False, interactive=True)]}, table, "claude", home, None)
    assert rep.interactive_in_unattended == ["grilling"]


def test_interactive_skill_for_unknown_role_is_not_flagged(home, table):
    rep = check_readiness({"no-such-role": [req("grilling", required=False, interactive=True)]}, table, "claude", home, None)
    assert rep.interactive_in_unattended == []


def test_interactive_in_unattended_is_reported_once_and_sorted(home, table):
    registry = {
        "tester": [req("zeta", required=False, interactive=True), req("grilling", required=False, interactive=True)],
        "researcher": [req("grilling", required=False, interactive=True)],
    }
    rep = check_readiness(registry, table, "claude", home, None)
    assert rep.interactive_in_unattended == ["grilling", "zeta"]


def test_optional_missing_not_reported(home, table):
    make_skill(agents_skills(home), "tdd")
    registry = {"implementer": [req("tdd"), req("diagram-design", required=False), req("wayfinder", required=False, fallback="x")]}
    rep = check_readiness(registry, table, "claude", home, None)
    assert rep.present == ["tdd"] and rep.missing == [] and rep.ok


def test_skill_required_by_several_roles_is_reported_once(home, table):
    make_skill(agents_skills(home), "handoff")
    registry = {
        "tester": [req("code-review"), req("verification-before-completion")],
        "lens-spec": [req("code-review")],
        "lens-security": [req("code-review"), req("security-review", required=False)],
        "*": [req("handoff")],
        "implementer": [req("handoff")],
    }
    rep = check_readiness(registry, table, "claude", home, None)
    assert rep.present == ["handoff"]
    assert rep.missing == [("code-review", None), ("verification-before-completion", None)]  # once each, by name


def test_present_and_missing_are_sorted_by_skill_name(home, table):
    for name in ("zz", "mm"):
        make_skill(agents_skills(home), name)
    registry = {"author": [req("zz"), req("yy"), req("mm"), req("aa")]}
    rep = check_readiness(registry, table, "claude", home, None)
    assert rep.present == ["mm", "zz"]
    assert rep.missing == [("aa", None), ("yy", None)]


def test_required_in_one_role_and_optional_in_another_is_required(home, table):
    registry = {"author": [req("research", required=False)], "researcher": [req("research")]}
    rep = check_readiness(registry, table, "claude", home, None)
    assert rep.missing == [("research", None)]


def test_fallback_is_the_first_one_named_for_the_skill(home, table):
    registry = {
        "author": [req("tdd")],
        "implementer": [req("tdd", fallback="test-driven-development")],
        "tester": [req("tdd", fallback="other-fallback")],
    }
    rep = check_readiness(registry, table, "claude", home, None)
    assert rep.missing == [("tdd", "test-driven-development")]


def test_installed_fallback_is_a_warning_not_a_failure(home, table):
    make_skill(agents_skills(home), "test-driven-development")
    registry = {"implementer": [req("tdd", fallback="test-driven-development")]}
    rep = check_readiness(registry, table, "claude", home, None)
    assert rep.present == [] and rep.missing == [("tdd", "test-driven-development")]  # still listed as missing
    assert rep.installed_fallbacks == {"test-driven-development"}
    assert rep.blocking_missing == [] and rep.ok


def test_missing_fallback_still_fails(home, table):
    registry = {"implementer": [req("tdd", fallback="test-driven-development"), req("handoff")]}
    rep = check_readiness(registry, table, "claude", home, None)
    assert rep.installed_fallbacks == frozenset()
    assert rep.blocking_missing == ["handoff", "tdd"] and not rep.ok


def test_project_skills_count_when_a_project_is_given(home, table, tmp_path):
    project = tmp_path / "project"
    make_skill(project / ".claude/skills", "claude-project-skill")
    make_skill(project / ".agents/skills", "codex-project-skill")
    registry = {"author": [req("claude-project-skill"), req("codex-project-skill")]}
    assert check_readiness(registry, table, "claude", home, project).present == ["claude-project-skill"]
    assert check_readiness(registry, table, "codex", home, project).present == ["codex-project-skill"]
    assert check_readiness(registry, table, "claude", home, None).present == []


def test_report_equality_ignores_the_installed_fallbacks_detail():
    a = ReadinessReport("claude", ["tdd"], [("x", "y")], [], installed_fallbacks=frozenset({"y"}))
    b = ReadinessReport("claude", ["tdd"], [("x", "y")], [])
    assert a == b


# --------------------------------------------------------------------------- search paths


def test_search_paths_for_claude(home, tmp_path):
    project = tmp_path / "project"
    cache = home / ".claude/plugins/cache"
    (cache / "mp/plugin-b/1.0/skills").mkdir(parents=True)
    (cache / "mp/plugin-a/2.0/skills").mkdir(parents=True)
    (cache / "mp/plugin-a/2.0/commands").mkdir(parents=True)  # not a skills folder
    codex_cache = home / ".codex/plugins/cache/mp/other/1.0/skills"
    codex_cache.mkdir(parents=True)
    assert skill_search_paths("claude", home, project) == [
        home / ".claude" / "skills",
        home / ".agents" / "skills",
        cache / "mp/plugin-a/2.0/skills",
        cache / "mp/plugin-b/1.0/skills",
        project / ".claude" / "skills",
    ]


def test_search_paths_for_codex(home, tmp_path):
    project = tmp_path / "project"
    cache = home / ".codex/plugins/cache"
    (cache / "mp/plugin/1.0/skills").mkdir(parents=True)
    (home / ".claude/plugins/cache/mp/other/1.0/skills").mkdir(parents=True)
    assert skill_search_paths("codex", home, project) == [
        home / ".agents" / "skills",
        home / ".codex" / "skills",
        cache / "mp/plugin/1.0/skills",
        project / ".agents" / "skills",
        project / ".codex" / "skills",
    ]


def test_search_paths_without_a_project_or_plugin_caches(home):
    assert skill_search_paths("claude", home, None) == [home / ".claude" / "skills", home / ".agents" / "skills"]
    assert skill_search_paths("codex", home, None) == [home / ".agents" / "skills", home / ".codex" / "skills"]


def test_search_paths_append_env_entries_last(home, tmp_path, monkeypatch):
    first, second = tmp_path / "a", tmp_path / "b"
    monkeypatch.setenv("FACTORY_SKILL_PATHS", os.pathsep.join([str(first), " ", str(second)]))
    paths = skill_search_paths("codex", home, tmp_path / "project")
    assert paths[-2:] == [first, second]
    assert paths[:2] == [home / ".agents" / "skills", home / ".codex" / "skills"]


def test_search_paths_reject_an_unknown_host(home):
    with pytest.raises(ValueError, match="openai"):
        skill_search_paths("openai", home, None)  # type: ignore[arg-type]


def test_windows_claude_desktop_plugins_are_searched(home, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    appdata = tmp_path / "roaming"
    monkeypatch.setenv("APPDATA", str(appdata))
    rpm_skills = appdata / "Claude/rpm/0fc4aa59/3b6f23a5/plugin_01abc/skills"
    make_skill(rpm_skills, "legal-brief")
    assert rpm_skills in skill_search_paths("claude", home, None)
    assert rpm_skills not in skill_search_paths("codex", home, None)
    assert "legal-brief" in find_installed_skills(skill_search_paths("claude", home, None))


def test_claude_desktop_plugins_are_not_searched_off_windows(home, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    appdata = tmp_path / "roaming"
    monkeypatch.setenv("APPDATA", str(appdata))
    make_skill(appdata / "Claude/rpm/a/b/plugin_01abc/skills", "legal-brief")
    assert skill_search_paths("claude", home, None) == [home / ".claude" / "skills", home / ".agents" / "skills"]


def test_windows_without_appdata_skips_claude_desktop_plugins(home, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("APPDATA")
    assert skill_search_paths("claude", home, None) == [home / ".claude" / "skills", home / ".agents" / "skills"]


def test_plugin_cache_search_is_bounded_and_skips_vendored_trees(home):
    cache = home / ".claude/plugins/cache"
    (cache / "a/b/c/d/e/skills").mkdir(parents=True)  # six levels down: found
    (cache / "a/b/c/d/e/f/skills").mkdir(parents=True)  # seven levels down: too deep
    (cache / "mp/plugin/1.0/node_modules/pkg/skills").mkdir(parents=True)
    (cache / "mp/plugin/1.0/.git/skills").mkdir(parents=True)
    (cache / "mp/plugin/1.0/skills/nested/skills").mkdir(parents=True)  # inside a skills folder: left to the scan
    assert skill_search_paths("claude", home, None)[2:] == [cache / "a/b/c/d/e/skills", cache / "mp/plugin/1.0/skills"]


# --------------------------------------------------------------------------- finding installed skills


def test_find_installed_skills_maps_names_to_their_folders(tmp_path):
    root = tmp_path / "skills"
    folder = make_skill(root, "tdd")
    assert find_installed_skills([root]) == {"tdd": folder}


def test_a_folder_without_skill_md_is_not_a_skill(tmp_path):
    root = tmp_path / "skills"
    (root / "empty").mkdir(parents=True)
    (root / "notes").mkdir()
    (root / "notes" / "README.md").write_text("hi", encoding="utf-8")
    (root / "SKILL.md").mkdir()  # a folder named SKILL.md is not a skill file
    assert find_installed_skills([root]) == {}


def test_missing_search_paths_are_ignored(tmp_path):
    assert find_installed_skills([tmp_path / "nowhere", tmp_path / "file.txt"]) == {}
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    assert find_installed_skills([tmp_path / "file.txt"]) == {}


def test_earlier_search_path_wins_a_duplicate_name(tmp_path):
    first = make_skill(tmp_path / "one", "tdd")
    make_skill(tmp_path / "two", "tdd")
    assert find_installed_skills([tmp_path / "one", tmp_path / "two"]) == {"tdd": first}
    assert find_installed_skills([tmp_path / "two", tmp_path / "one"]) == {"tdd": tmp_path / "two" / "tdd"}


def test_grouped_skills_are_found_below_a_group_folder(tmp_path):
    root = tmp_path / "skills"
    deep = make_skill(root / "group", "inner")
    assert find_installed_skills([root]) == {"inner": deep}


def test_the_scan_stops_below_a_skill_and_below_the_depth_limit(tmp_path):
    root = tmp_path / "skills"
    make_skill(root, "outer")
    make_skill(root / "outer" / "assets", "bundled-example")  # inside a skill: not another skill
    make_skill(root / "a" / "b", "edge")  # three levels below the search path: still found
    make_skill(root / "a" / "b" / "c", "too-deep")
    assert set(find_installed_skills([root])) == {"outer", "edge"}


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("---\nname: from-front-matter\ndescription: x\n---\nbody", "from-front-matter"),
        ("---\r\nname: from-crlf\r\ndescription: x\r\n---\r\nbody\r\n", "from-crlf"),
        (b"\xef\xbb\xbf---\nname: from-bom\n---\nbody", "from-bom"),
        ("---\nname: '  spaced  '\n---\n", "spaced"),
        ("---\nname: later\nnote: |\n  ---\n  not the end of the block\n---\n", "later"),
        ("# no front matter\nname: not-this\n", "folder"),
        ("---\ndescription: no name here\n---\n", "folder"),
        ("---\nname: \"\"\n---\n", "folder"),
        ("---\nname: 12\n---\n", "folder"),
        ("---\nname: [a, b]\n---\n", "folder"),
        ("---\n- just\n- a list\n---\n", "folder"),
        ("---\nname: [unclosed\ndescription: x\n---\n", "folder"),
        ("---\nname: never-closed\ndescription: x\n", "folder"),
        ("---\nname: tab\n\tbroken: [\n---\n", "folder"),
        (" ---\nname: indented-opening\n---\n", "folder"),
        ("name: before\n---\nname: after\n---\n", "folder"),
        (b"---\nname: ok-name\n---\n\xff\xfe not UTF-8 in the body", "ok-name"),
        (b"", "folder"),
    ],
)
def test_frontmatter_forms(tmp_path, raw, expected):
    root = tmp_path / "skills"
    make_skill(root, "folder", raw=raw)
    assert list(find_installed_skills([root])) == [expected]


def test_only_the_leading_front_matter_block_is_read(tmp_path):
    root = tmp_path / "skills"
    make_skill(root, "folder", raw="---\ndescription: x\n---\n\n---\nname: in-the-body\n---\n")
    assert list(find_installed_skills([root])) == ["folder"]


def test_symlinked_skill_folders_are_followed(tmp_path):
    shared = tmp_path / "shared"
    make_skill(shared, "tdd")
    claude = tmp_path / "claude-skills"
    claude.mkdir()
    try_symlink(claude / "tdd", shared / "tdd")
    assert find_installed_skills([claude]) == {"tdd": claude / "tdd"}


def test_a_broken_symlink_is_skipped(tmp_path):
    root = tmp_path / "skills"
    make_skill(root, "ok")
    try_symlink(root / "dangling", tmp_path / "does-not-exist")
    assert set(find_installed_skills([root])) == {"ok"}


def test_a_symlink_loop_does_not_hang(tmp_path):
    root = tmp_path / "skills"
    make_skill(root, "ok")
    try_symlink(root / "loop", root)
    assert set(find_installed_skills([root])) == {"ok"}


def test_an_unreadable_skill_file_is_skipped(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    make_skill(root, "ok")
    make_skill(root, "locked")
    real = readiness._read_head

    def read_head(path: Path):
        if path.parent.name == "locked":
            return None  # what _read_head reports for a file it cannot open
        return real(path)

    monkeypatch.setattr(readiness, "_read_head", read_head)
    assert set(find_installed_skills([root])) == {"ok"}


def test_read_head_reports_a_file_it_cannot_open_as_none(tmp_path, monkeypatch):
    skill = make_skill(tmp_path / "skills", "locked")

    def deny(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(readiness, "open", deny, raising=False)  # the module's own open()
    assert readiness._read_head(skill / "SKILL.md") is None


def test_an_unreadable_folder_is_skipped(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    make_skill(root, "ok")
    make_skill(root / "locked", "hidden")
    make_skill(root, "zz-also-ok")
    real_scandir = os.scandir

    def scandir(path):
        if Path(path).name == "locked":
            raise PermissionError(13, "Permission denied")
        return real_scandir(path)

    monkeypatch.setattr(readiness.os, "scandir", scandir)
    assert set(find_installed_skills([root])) == {"ok", "zz-also-ok"}


def test_plugin_cache_search_survives_an_unreadable_folder(home, monkeypatch):
    cache = home / ".claude/plugins/cache"
    make_skill(cache / "mp/ok/1.0/skills", "fine")
    (cache / "mp/locked/1.0/skills").mkdir(parents=True)
    real_scandir = os.scandir

    def scandir(path):
        if Path(path).name == "locked":
            raise PermissionError(13, "Permission denied")
        return real_scandir(path)

    monkeypatch.setattr(readiness.os, "scandir", scandir)
    assert skill_search_paths("claude", home, None)[2:] == [cache / "mp/ok/1.0/skills"]


# --------------------------------------------------------------------------- the skills check command


def real_registry() -> dict[str, list[SkillReq]]:
    return load_skills_registry(factory_home() / "config" / "skills.yaml")


def registered_fallback(name: str) -> str:
    """The fallback the readiness report names for ``name``: the first one any role registers for it."""
    return next(s.fallback for skills in real_registry().values() for s in skills if s.name == name and s.fallback)


def install_every_required_skill(home: Path, *, leave_out: set[str] = frozenset()) -> None:
    for skills in real_registry().values():
        for skill in skills:
            if skill.required and skill.name not in leave_out:
                make_skill(agents_skills(home), skill.name)


def run_check(capsys, home: Path, *extra: str, host: str = "claude") -> tuple[int, str, str]:
    code = cli.main(["skills", "check", "--host", host, "--home", str(home), *extra])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def lines(output: str) -> list[str]:
    return output.splitlines()


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_cli_check_all_present_exits_0(home, capsys, host):
    install_every_required_skill(home)
    code, out, err = run_check(capsys, home, host=host)
    assert code == 0 and err == ""
    assert "present tdd" in lines(out)
    assert not any(line.startswith("missing") for line in lines(out))
    assert lines(out)[-1] == "skills ok"


def test_cli_check_names_a_missing_skill_with_no_fallback_and_exits_1(home, capsys):
    install_every_required_skill(home, leave_out={"handoff"})
    code, out, _ = run_check(capsys, home)
    assert code == 1
    assert "missing handoff (no fallback)" in lines(out)
    assert lines(out)[-1] == "skills not ready"


def test_cli_check_missing_skill_with_installed_fallback_is_a_warning(home, capsys):
    fallback_of_tdd = registered_fallback("tdd")
    install_every_required_skill(home, leave_out={"tdd"})
    make_skill(agents_skills(home), fallback_of_tdd)
    code, out, _ = run_check(capsys, home)
    assert code == 0
    assert f"missing tdd (fallback {fallback_of_tdd}: installed)" in lines(out)
    assert lines(out)[-1] == "skills ok"


def test_cli_check_missing_skill_with_missing_fallback_exits_1(home, capsys):
    fallback_of_tdd = registered_fallback("tdd")
    install_every_required_skill(home, leave_out={"tdd"})
    code, out, _ = run_check(capsys, home)
    assert code == 1
    assert f"missing tdd (fallback {fallback_of_tdd}: not installed)" in lines(out)


def test_cli_check_on_an_empty_home_lists_every_required_skill_once(home, capsys):
    code, out, _ = run_check(capsys, home)
    required = {s.name for skills in real_registry().values() for s in skills if s.required}
    missing = [line.split()[1] for line in lines(out) if line.startswith("missing ")]
    assert code == 1
    assert sorted(missing) == sorted(required)  # each once, although several roles require some of them
    assert missing == sorted(missing)


def make_factory(tmp_path: Path, skills: dict) -> Path:
    """A factory checkout with the real role table and the given ``roles`` section of skills.yaml."""
    root = tmp_path / "factory-checkout"
    (root / "config").mkdir(parents=True)
    (root / "config" / "roles.yaml").write_bytes((factory_home() / "config" / "roles.yaml").read_bytes())
    (root / "config" / "skills.yaml").write_text(yaml.safe_dump({"version": 1, "roles": skills}), encoding="utf-8")
    return root


def test_cli_check_flags_an_interactive_skill_in_an_unattended_role(home, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("FACTORY_HOME", str(make_factory(tmp_path, {
        "tester": [{"name": "grilling", "required": True, "interactive": True}],
    })))
    make_skill(agents_skills(home), "grilling")
    code, out, _ = run_check(capsys, home)
    assert code == 1
    assert "interactive grilling (registered for a subagent or headless role)" in lines(out)
    assert lines(out)[-1] == "skills not ready"


def test_cli_check_interactive_skill_in_a_main_role_is_fine(home, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("FACTORY_HOME", str(make_factory(tmp_path, {
        "interviewer": [{"name": "grilling", "required": True, "interactive": True}],
    })))
    make_skill(agents_skills(home), "grilling")
    code, out, _ = run_check(capsys, home)
    assert code == 0 and lines(out) == ["present grilling", "skills ok"]


def test_cli_check_with_a_project_reads_its_skills_and_role_overrides(home, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("FACTORY_HOME", str(make_factory(tmp_path, {
        "interviewer": [{"name": "grilling", "required": True, "interactive": True},
                        {"name": "project-skill", "required": True}],
    })))
    make_skill(agents_skills(home), "grilling")
    project = tmp_path / "project"
    make_skill(project / ".claude" / "skills", "project-skill")
    (project / "sw-factory.yaml").write_text(yaml.safe_dump({
        "project": "demo", "approvers": ["owner"], "verify": {"quick": "q", "full": "f"},
        "roles": {"interviewer": {"run_as": "subagent"}},
    }), encoding="utf-8")
    for given in (project, project / "sw-factory.yaml"):
        code, out, _ = run_check(capsys, home, "--project", str(given))
        assert code == 1  # the project's override makes the interviewer unattended
        assert lines(out) == [
            "present grilling",
            "present project-skill",
            "interactive grilling (registered for a subagent or headless role)",
            "skills not ready",
        ]
    code, out, _ = run_check(capsys, home)  # without the project: no project skill, and the default run_as
    assert code == 1 and "missing project-skill (no fallback)" in lines(out)
    assert not any(line.startswith("interactive") for line in lines(out))


def test_cli_check_a_bad_project_exits_2(home, tmp_path, capsys):
    code, out, err = run_check(capsys, home, "--project", str(tmp_path / "no-such-project"))
    assert code == 2 and out == ""
    assert "no-such-project" in err and "not found" in err


def test_cli_check_a_bad_registry_exits_2(home, tmp_path, capsys, monkeypatch):
    root = make_factory(tmp_path, {"tester": [{"name": "x", "bogus": True}]})
    monkeypatch.setenv("FACTORY_HOME", str(root))
    code, out, err = run_check(capsys, home)
    assert code == 2 and out == ""
    assert "skills.yaml" in err and "bogus" in err


def test_cli_check_defaults_the_home_to_the_users_home(tmp_path, capsys):
    default_home = Path.home()  # the autouse fixture points HOME and USERPROFILE at an empty folder
    assert default_home == tmp_path / "ambient-home"
    make_skill(agents_skills(default_home), "handoff")
    code = cli.main(["skills", "check", "--host", "codex"])
    assert code == 1
    assert "present handoff" in capsys.readouterr().out.splitlines()


def test_cli_skills_requires_a_subcommand_and_a_host(home, capsys):
    assert cli.main(["skills"]) == 2
    assert "check" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["skills", "check", "--home", str(home)])
    assert exit_info.value.code == 2
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["skills", "check", "--host", "openai", "--home", str(home)])
    assert exit_info.value.code == 2


# --------------------------------------------------------------------------- the skills path command


def run_path(capsys, home: Path, name: str, *extra: str, host: str = "claude") -> tuple[int, str, str]:
    code = cli.main(["skills", "path", name, "--host", host, "--home", str(home), *extra])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_cli_path_prints_the_skill_md_of_an_installed_skill(home, capsys, host):
    folder = make_skill(agents_skills(home), "codebase-design")
    code, out, err = run_path(capsys, home, "codebase-design", host=host)
    assert code == 0 and err == ""
    assert lines(out) == [str(folder / "SKILL.md")]
    assert Path(lines(out)[0]).is_absolute() and Path(lines(out)[0]).is_file()


def test_cli_path_finds_a_skill_by_its_front_matter_name(home, capsys):
    folder = make_skill(agents_skills(home), "wp", name="writing-plans")
    code, out, _ = run_path(capsys, home, "writing-plans")
    assert code == 0 and lines(out) == [str(folder / "SKILL.md")]


def test_cli_path_of_a_skill_that_is_not_installed_prints_none_and_exits_1(home, capsys):
    make_skill(agents_skills(home), "tdd")
    code, out, err = run_path(capsys, home, "domain-modeling")
    assert code == 1 and out == "none\n" and err == ""


def test_cli_path_finds_a_plugin_cache_install_on_its_own_host_only(home, capsys):
    folder = make_skill(home / ".claude/plugins/cache/mp/superpowers/7.0.0/skills", "tdd")
    code, out, _ = run_path(capsys, home, "tdd", host="claude")
    assert code == 0 and lines(out) == [str(folder / "SKILL.md")]
    code, out, _ = run_path(capsys, home, "tdd", host="codex")
    assert code == 1 and lines(out) == ["none"]


def test_cli_path_takes_the_skill_from_the_hosts_first_folder(home, capsys):
    shared = make_skill(agents_skills(home), "tdd")
    claude_own = make_skill(home / ".claude" / "skills", "tdd")  # first for Claude Code
    make_skill(home / ".codex" / "skills", "tdd")  # after ~/.agents/skills for Codex
    assert lines(run_path(capsys, home, "tdd", host="claude")[1]) == [str(claude_own / "SKILL.md")]
    assert lines(run_path(capsys, home, "tdd", host="codex")[1]) == [str(shared / "SKILL.md")]


def test_cli_path_searches_a_projects_skill_folders(home, tmp_path, capsys):
    project = tmp_path / "project"
    folder = make_skill(project / ".agents" / "skills", "project-skill")
    (project / "sw-factory.yaml").write_text("project: demo\n", encoding="utf-8")
    for given in (project, project / "sw-factory.yaml"):
        code, out, _ = run_path(capsys, home, "project-skill", "--project", str(given), host="codex")
        assert code == 0 and lines(out) == [str(folder / "SKILL.md")]
    assert run_path(capsys, home, "project-skill", host="codex")[:2] == (1, "none\n")


def test_cli_path_a_missing_project_exits_2(home, tmp_path, capsys):
    code, out, err = run_path(capsys, home, "tdd", "--project", str(tmp_path / "no-such-project"))
    assert code == 2 and out == ""
    assert "no-such-project" in err and "not found" in err


def test_cli_path_with_a_relative_home_prints_an_absolute_path(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    folder = make_skill(tmp_path / "relative-home" / ".agents" / "skills", "tdd")
    assert cli.main(["skills", "path", "tdd", "--host", "codex", "--home", "relative-home"]) == 0
    assert capsys.readouterr().out.splitlines() == [str(folder / "SKILL.md")]


def test_cli_path_defaults_the_home_to_the_users_home(capsys):
    folder = make_skill(agents_skills(Path.home()), "tdd")  # the autouse fixture's empty home
    assert cli.main(["skills", "path", "tdd", "--host", "claude"]) == 0
    assert capsys.readouterr().out.splitlines() == [str(folder / "SKILL.md")]


def test_cli_path_refuses_a_path_the_console_cannot_show(home, capsys, monkeypatch):
    make_skill(agents_skills(home), "tdd-é", name="tdd")
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(io.BytesIO(), encoding="ascii"))
    code = cli.main(["skills", "path", "tdd", "--host", "codex", "--home", str(home)])
    assert code == 2  # a path with the character replaced would name another file
    assert "encoding" in capsys.readouterr().err


def test_cli_skills_path_needs_a_name_and_a_host(home, capsys):
    assert cli.main(["skills"]) == 2
    assert "path" in capsys.readouterr().out
    for argv in (
        ["skills", "path", "--host", "claude"],
        ["skills", "path", "tdd"],
        ["skills", "path", "tdd", "--host", "openai"],
    ):
        with pytest.raises(SystemExit) as exit_info:
            cli.main([*argv, "--home", str(home)])
        assert exit_info.value.code == 2
