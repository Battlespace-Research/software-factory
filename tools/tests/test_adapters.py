"""Tests for swfactory.adapters (invocation planning and headless runs) and the ``factory role`` CLI."""
from __future__ import annotations

import dataclasses
import json
import os
import re
import time

import pytest
import yaml

from swfactory import cli
from swfactory.adapters import (
    AGENT_FOR_ROLE,
    CLAUDE_PRINT_FLAGS,
    CODEX_EXEC_FLAGS,
    plan_invocation,
    run_invocation,
)
from swfactory.config import ConfigError
from swfactory.roles import resolve_role

# What the fake CLI prints (see tools/tests/fakes/fake_cli.py), without the trailing newline.
PASS_TEXT = "VERDICT: PASS\nChecked: caf\u00e9 \u2713"

PROVENANCE = re.compile(
    r"^<!-- factory: role=(?P<role>\S+) vendor=(?P<vendor>\S+) model=(?P<model>\S+) effort=(?P<effort>\S+) "
    r"sandbox=(?P<sandbox>\S+) at=(?P<at>\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)(?P<extra> extra=.*)? -->$"
)


def lens(table, tmp_path, role="lens-security", host="claude", author="claude", out="o.md", schema=None):
    """Resolve and plan a role; returns (resolved role, invocation, output path)."""
    r = resolve_role(table, role, host, author)
    out_path = tmp_path / out
    return r, plan_invocation(r, host, out_path, schema), out_path


# --------------------------------------------------------------------------- plan_invocation

def test_codex_argv_for_claude_host_lens(table, tmp_path):
    inv = plan_invocation(resolve_role(table, "lens-security", "claude", "claude"), "claude", tmp_path / "o.md", tmp_path / "s.json")
    assert inv.kind == "command" and inv.argv[:4] == ["codex", "exec", "-m", "gpt-6.1-sol"] and "read-only" in inv.argv and inv.argv[-1] == "-"


def test_codex_argv_is_the_documented_command(table, tmp_path):
    out, schema = tmp_path / "o.md", tmp_path / "s.json"
    inv = plan_invocation(resolve_role(table, "lens-security", "claude", "claude"), "claude", out, schema)
    assert inv.argv == [
        "codex", "exec", "-m", "gpt-6.1-sol", "-c", 'model_reasoning_effort="xhigh"',
        "-s", "read-only", "-o", str(out), "--output-schema", str(schema), "-",
    ]
    assert (inv.agent, inv.model_alias, inv.effort, inv.sandbox) == (None, None, "xhigh", "read-only")


def test_codex_argv_without_a_schema_has_no_schema_flag(table, tmp_path):
    _, inv, out = lens(table, tmp_path)
    assert inv.argv == [
        "codex", "exec", "-m", "gpt-6.1-sol", "-c", 'model_reasoning_effort="xhigh"',
        "-s", "read-only", "-o", str(out), "-",
    ]


def test_codex_sandbox_is_workspace_write_only_for_write_access(table, tmp_path):
    r, _, out = lens(table, tmp_path)
    inv = plan_invocation(dataclasses.replace(r, access="write"), "claude", out)
    assert inv.sandbox == "workspace-write"
    assert inv.argv[inv.argv.index("-s") + 1] == "workspace-write"


def test_no_product_edits_roles_run_codex_read_only(table, tmp_path):
    # The tester on a Codex host runs through codex exec; no-product-edits is not write access.
    r, inv, _ = lens(table, tmp_path, role="tester", host="openai", author=None)
    assert (r.vendor, r.access) == ("openai", "no-product-edits")
    assert inv.kind == "command" and inv.sandbox == "read-only"
    assert inv.argv[inv.argv.index("-s") + 1] == "read-only"


def test_codex_roles_on_a_codex_host_also_run_through_codex_exec(table, tmp_path):
    r, inv, _ = lens(table, tmp_path, role="lens-spec", host="openai", author=None)
    assert r.vendor == "openai"
    assert inv.kind == "command" and inv.argv[:4] == ["codex", "exec", "-m", "gpt-6.1-sol"]
    assert 'model_reasoning_effort="high"' in inv.argv


def test_subagent_for_claude_lens_on_claude_host(table, tmp_path):
    inv = plan_invocation(resolve_role(table, "lens-spec", "claude", "claude"), "claude", tmp_path / "o.md")
    assert (inv.kind, inv.agent, inv.model_alias) == ("subagent", "factory-lens-reviewer", "opus")


def test_subagent_invocation_carries_effort_and_sandbox_but_no_argv(table, tmp_path):
    _, inv, _ = lens(table, tmp_path, role="lens-spec")
    assert (inv.argv, inv.effort, inv.sandbox) == (None, "high", "read-only")


@pytest.mark.parametrize("role, agent, alias", [
    ("lens-maintainability", "factory-lens-reviewer", "sonnet"),
    ("lens-spec", "factory-lens-reviewer", "opus"),
    ("doc-reviewer-a", "factory-doc-reviewer", "opus"),
    ("tester", "factory-tester", "sonnet"),
    ("researcher", "factory-researcher", "sonnet"),
])
def test_claude_roles_on_a_claude_host_run_as_the_mapped_subagent(table, tmp_path, role, agent, alias):
    _, inv, _ = lens(table, tmp_path, role=role)
    assert (inv.kind, inv.agent, inv.model_alias) == ("subagent", agent, alias)


def test_headless_doc_reviewer_b_goes_to_the_other_vendor(table, tmp_path):
    r, inv, _ = lens(table, tmp_path, role="doc-reviewer-b")
    assert (r.vendor, inv.kind, inv.argv[:4]) == ("openai", "command", ["codex", "exec", "-m", "gpt-6-astra"])


def test_agent_mapping_is_the_designed_one():
    assert AGENT_FOR_ROLE == {
        "lens-*": "factory-lens-reviewer",
        "doc-reviewer-*": "factory-doc-reviewer",
        "tester": "factory-tester",
        "researcher": "factory-researcher",
    }


def test_subagent_role_without_a_mapped_agent_has_no_agent_name(table, tmp_path):
    r, inv, _ = lens(table, tmp_path, role="release-checker")
    assert (r.run_as, inv.kind, inv.agent, inv.model_alias) == ("headless", "subagent", None, "sonnet")


def test_alias_comes_from_the_fallback_model_when_the_fallback_is_used(table, tmp_path):
    r = resolve_role(table, "lens-maintainability", "claude", "claude", use_fallback=True)
    assert r.model == "claude-opus-5-5"
    assert plan_invocation(r, "claude", tmp_path / "o.md").model_alias == "opus"


def test_alias_that_cannot_be_derived_is_a_config_error(table, tmp_path):
    r = dataclasses.replace(resolve_role(table, "lens-spec", "claude", "claude"), model="x")
    with pytest.raises(ConfigError) as e:
        plan_invocation(r, "claude", tmp_path / "o.md")
    assert "'x'" in e.value.problems[0]


@pytest.mark.parametrize("host", ["claude", "openai"])
def test_main_roles_are_done_by_the_session_itself(table, tmp_path, host):
    r, inv, _ = lens(table, tmp_path, role="implementer", host=host, author=None)
    assert r.vendor == host
    assert (inv.kind, inv.argv, inv.agent, inv.model_alias) == ("main", None, None, None)
    assert (inv.effort, inv.sandbox) == ("xhigh", "workspace-write")


def test_claude_argv_on_openai_host(table, tmp_path):
    r, inv, _ = lens(table, tmp_path, role="lens-correctness", host="openai", author="openai")
    assert r.vendor == "claude"
    assert inv.kind == "command" and inv.argv[0:2] == ["claude", "-p"]
    assert inv.argv == [
        "claude", "-p", "--model", "claude-opus-5-5", "--effort", "xhigh", "--output-format", "json",
        "--permission-mode", "plan", "--allowedTools", "Read,Grep,Glob",
    ]
    assert (inv.agent, inv.model_alias, inv.effort, inv.sandbox) == (None, None, "xhigh", "read-only")


def test_claude_argv_runs_at_the_bindings_effort(table, tmp_path):
    # lens-spec (same vendor as the Claude author) is high, lens-correctness (other than the OpenAI author)
    # is xhigh: the flag follows the binding.
    _, high, _ = lens(table, tmp_path, role="lens-spec", host="openai", author="claude")
    _, xhigh, _ = lens(table, tmp_path, role="lens-correctness", host="openai", author="openai")
    assert high.argv[high.argv.index("--effort") + 1] == "high" == high.effort
    assert xhigh.argv[xhigh.argv.index("--effort") + 1] == "xhigh" == xhigh.effort


def test_claude_argv_ignores_the_schema_and_keeps_the_variadic_tool_list_last(table, tmp_path):
    _, inv, _ = lens(table, tmp_path, role="lens-correctness", host="openai", author="openai", schema=tmp_path / "s.json")
    assert "--output-schema" not in inv.argv and str(tmp_path / "s.json") not in inv.argv
    assert inv.argv[-2:] == ["--allowedTools", "Read,Grep,Glob"]


def test_flag_spellings_live_in_the_constants(table, tmp_path, monkeypatch):
    monkeypatch.setitem(CODEX_EXEC_FLAGS, "model", "--model")
    monkeypatch.setitem(CLAUDE_PRINT_FLAGS, "model", "--use-model")
    monkeypatch.setitem(CLAUDE_PRINT_FLAGS, "effort", "--thinking")
    _, codex, _ = lens(table, tmp_path)
    assert codex.argv[2] == "--model"
    _, claude, _ = lens(table, tmp_path, role="lens-correctness", host="openai", author="openai")
    assert claude.argv[2] == "--use-model" and claude.argv[4] == "--thinking"


def test_invocation_is_frozen(table, tmp_path):
    _, inv, _ = lens(table, tmp_path)
    with pytest.raises(dataclasses.FrozenInstanceError):
        inv.kind = "main"  # type: ignore[misc]


# --------------------------------------------------------------------------- run_invocation

def test_run_invocation_success_with_fake_cli(tmp_path, table, fake_cli):
    r, inv, out = lens(table, tmp_path)
    res = run_invocation(inv, r, "prompt", out)
    assert res.ok and out.read_text(encoding="utf-8").splitlines()[0] == "VERDICT: PASS"
    assert out.read_text(encoding="utf-8").rstrip().splitlines()[-1].startswith("<!-- factory: role=lens-security")
    assert (res.exit_code, res.out_path, res.suggest_fallback) == (0, out, False)


def test_run_invocation_stamps_role_model_effort_sandbox_and_time(tmp_path, table, fake_cli):
    r, inv, out = lens(table, tmp_path)
    run_invocation(inv, r, "prompt", out)
    last = out.read_text(encoding="utf-8").splitlines()[-1]
    m = PROVENANCE.match(last)
    assert m, last
    assert (m["role"], m["vendor"], m["model"], m["effort"], m["sandbox"]) == (
        "lens-security", "openai", "gpt-6.1-sol", "xhigh", "read-only")
    assert not m["extra"]


def test_run_invocation_keeps_the_reviewers_text_intact_with_the_stamp_last(tmp_path, table, fake_cli):
    r, inv, out = lens(table, tmp_path)
    run_invocation(inv, r, "prompt", out)
    text = out.read_text(encoding="utf-8")
    assert text.startswith(PASS_TEXT + "\n")
    assert text.endswith("-->\n") and "\r" not in text
    assert text.count("<!-- factory:") == 1


def test_run_invocation_sends_the_prompt_on_stdin_and_the_planned_argv(tmp_path, table, fake_cli):
    r, inv, out = lens(table, tmp_path, schema=tmp_path / "s.json")
    prompt = "Review this \u2014 caf\u00e9 \u2713\nsecond line\n"
    assert run_invocation(inv, r, prompt, out).ok
    (call,) = fake_cli.calls()
    assert call["tool"] == "codex"
    assert call["argv"] == inv.argv[1:]          # exactly what was planned, quotes and all
    assert call["prompt"] == prompt              # UTF-8, and no CRLF translation


def test_run_invocation_uses_stdout_when_the_cli_does_not_write_the_output_file(tmp_path, table, fake_cli):
    fake_cli.mode("nofile")
    r, inv, out = lens(table, tmp_path)
    out.write_text("STALE", encoding="utf-8")
    assert run_invocation(inv, r, "prompt", out).ok
    text = out.read_text(encoding="utf-8")
    assert text.startswith(PASS_TEXT + "\n") and "STALE" not in text


def test_run_invocation_failure_leaves_no_output(tmp_path, table, fake_cli):
    fake_cli.mode("fail7")
    r, inv, out = lens(table, tmp_path)
    res = run_invocation(inv, r, "prompt", out)
    assert not res.ok and res.suggest_fallback and not out.exists()
    assert res.exit_code == 7 and "simulated failure" in res.stderr_tail


def test_run_invocation_failure_also_removes_an_earlier_output(tmp_path, table, fake_cli):
    fake_cli.mode("fail7")
    r, inv, out = lens(table, tmp_path)
    out.write_text("VERDICT: PASS\nfrom an earlier run\n", encoding="utf-8")
    assert not run_invocation(inv, r, "prompt", out).ok
    assert not out.exists()


def test_missing_executable(tmp_path, table):
    r, inv, out = lens(table, tmp_path)
    missing = dataclasses.replace(inv, argv=["no-such-cli-xyz", *inv.argv[1:]])
    res = run_invocation(missing, r, "prompt", out)
    assert not res.ok and res.suggest_fallback and not out.exists()
    assert res.exit_code is None and "no-such-cli-xyz" in res.stderr_tail


def test_missing_codex_on_path_suggests_the_fallback(tmp_path, table, monkeypatch):
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    r, inv, out = lens(table, tmp_path)
    res = run_invocation(inv, r, "prompt", out)
    assert not res.ok and res.suggest_fallback and not out.exists() and "codex" in res.stderr_tail


def test_a_run_that_produces_no_text_is_a_failure(tmp_path, table, fake_cli):
    fake_cli.mode("empty")
    r, inv, out = lens(table, tmp_path)
    res = run_invocation(inv, r, "prompt", out)
    assert not res.ok and res.suggest_fallback and not out.exists()
    assert res.exit_code == 0 and "no output" in res.stderr_tail


def test_timeout_is_a_failure_and_does_not_wait_for_the_child(tmp_path, table, fake_cli):
    fake_cli.mode("sleep")
    r, inv, out = lens(table, tmp_path)
    started = time.monotonic()
    res = run_invocation(inv, r, "prompt", out, timeout_s=1)
    # The fake sleeps 60 s behind a shim process. Killing only the shim leaves the pipes open and the
    # run would wait out the drain limit (10 s); killing the whole tree returns within a second or two.
    assert time.monotonic() - started < 8
    assert not res.ok and res.suggest_fallback and not out.exists()
    assert res.exit_code is None and "timed out" in res.stderr_tail


def test_paths_with_spaces(tmp_path, table, fake_cli):
    out = tmp_path / "dir with space" / "review out.md"          # the folder does not exist yet
    r = resolve_role(table, "lens-security", "claude", "claude")
    schema = tmp_path / "schema dir" / "s.json"
    inv = plan_invocation(r, "claude", out, schema)
    res = run_invocation(inv, r, "prompt", out)
    assert res.ok and out.read_text(encoding="utf-8").splitlines()[0] == "VERDICT: PASS"
    (call,) = fake_cli.calls()
    assert str(out) in call["argv"] and str(schema) in call["argv"]


def test_claude_command_writes_the_json_result_field(tmp_path, table, fake_cli):
    fake_cli.mode("json")
    r, inv, out = lens(table, tmp_path, role="lens-correctness", host="openai", author="openai")
    res = run_invocation(inv, r, "prompt", out)
    text = out.read_text(encoding="utf-8")
    assert res.ok and text.startswith(PASS_TEXT + "\n")
    last = PROVENANCE.match(text.splitlines()[-1])
    assert last and (last["role"], last["vendor"], last["model"], last["sandbox"]) == (
        "lens-correctness", "claude", "claude-opus-5-5", "read-only")
    (call,) = fake_cli.calls()
    assert call["tool"] == "claude" and call["argv"][0] == "-p" and call["prompt"] == "prompt"


@pytest.mark.parametrize("mode", ["json-error", "badjson"])
def test_claude_command_with_an_unusable_result_is_a_failure(tmp_path, table, fake_cli, mode):
    fake_cli.mode(mode)
    r, inv, out = lens(table, tmp_path, role="lens-correctness", host="openai", author="openai")
    res = run_invocation(inv, r, "prompt", out)
    assert not res.ok and res.suggest_fallback and not out.exists() and res.stderr_tail


def test_claude_command_failure_exit_code_is_reported(tmp_path, table, fake_cli):
    fake_cli.mode("fail7")
    r, inv, out = lens(table, tmp_path, role="lens-correctness", host="openai", author="openai")
    res = run_invocation(inv, r, "prompt", out)
    assert not res.ok and res.exit_code == 7 and not out.exists()


# --------------------------------------------------------------------------- FACTORY_CODEX_ARGS

def test_extra_codex_args_go_before_the_trailing_dash_and_into_the_stamp(tmp_path, table, fake_cli, monkeypatch):
    monkeypatch.setenv("FACTORY_CODEX_ARGS", '-c windows.sandbox="unelevated"')
    r, inv, out = lens(table, tmp_path)
    assert run_invocation(inv, r, "prompt", out).ok
    # On Windows the value is split without stripping quotes, so TOML strings keep theirs.
    sandbox_value = 'windows.sandbox="unelevated"' if os.name == "nt" else "windows.sandbox=unelevated"
    (call,) = fake_cli.calls()
    assert call["argv"][-3:] == ["-c", sandbox_value, "-"]
    assert call["argv"][:-3] == inv.argv[1:-1]       # nothing else changed
    stamp = PROVENANCE.match(out.read_text(encoding="utf-8").splitlines()[-1])
    assert stamp and "windows.sandbox" in stamp["extra"]


def test_planned_argv_does_not_include_the_extra_args(tmp_path, table, monkeypatch):
    monkeypatch.setenv("FACTORY_CODEX_ARGS", "--profile quick")
    _, inv, _ = lens(table, tmp_path)
    assert "--profile" not in inv.argv


def test_extra_codex_args_are_not_given_to_claude(tmp_path, table, fake_cli, monkeypatch):
    monkeypatch.setenv("FACTORY_CODEX_ARGS", "--profile quick")
    fake_cli.mode("json")
    r, inv, out = lens(table, tmp_path, role="lens-correctness", host="openai", author="openai")
    assert run_invocation(inv, r, "prompt", out).ok
    (call,) = fake_cli.calls()
    assert call["argv"] == inv.argv[1:]
    assert not PROVENANCE.match(out.read_text(encoding="utf-8").splitlines()[-1])["extra"]


def test_empty_extra_codex_args_change_nothing(tmp_path, table, fake_cli, monkeypatch):
    monkeypatch.setenv("FACTORY_CODEX_ARGS", "   ")
    r, inv, out = lens(table, tmp_path)
    assert run_invocation(inv, r, "prompt", out).ok
    (call,) = fake_cli.calls()
    assert call["argv"] == inv.argv[1:]


def test_unbalanced_quotes_in_extra_codex_args_are_a_config_error(tmp_path, table, fake_cli, monkeypatch):
    monkeypatch.setenv("FACTORY_CODEX_ARGS", '-c "oops')
    r, inv, out = lens(table, tmp_path)
    with pytest.raises(ConfigError) as e:
        run_invocation(inv, r, "prompt", out)
    assert "FACTORY_CODEX_ARGS" in e.value.problems[0]
    assert fake_cli.calls() == []


def test_run_invocation_refuses_a_non_command_invocation(tmp_path, table):
    r, inv, out = lens(table, tmp_path, role="lens-spec")
    assert inv.kind == "subagent"
    with pytest.raises(ValueError):
        run_invocation(inv, r, "prompt", out)


# --------------------------------------------------------------------------- CLI

def run_cli(capsys, *argv: object):
    code = cli.main([str(a) for a in argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_cli_role_resolve_prints_resolved_role_and_invocation_as_json(capsys):
    code, out, _ = run_cli(capsys, "role", "resolve", "lens-correctness", "--host", "claude", "--author", "claude")
    assert code == 0
    data = json.loads(out)
    assert data["resolved"] == {
        "role": "lens-correctness", "vendor": "openai", "model": "gpt-6.1-sol", "effort": "xhigh",
        "run_as": "headless", "access": "read-only", "fallback_used": False,
    }
    assert data["invocation"]["kind"] == "command"
    assert data["invocation"]["argv"][:4] == ["codex", "exec", "-m", "gpt-6.1-sol"]
    assert data["invocation"]["argv"][-1] == "-"


def test_cli_role_resolve_subagent_and_fallback(capsys):
    code, out, _ = run_cli(capsys, "role", "resolve", "lens-spec", "--host", "claude", "--author", "claude", "--fallback")
    data = json.loads(out)
    assert code == 0 and data["resolved"]["fallback_used"] is True
    assert (data["invocation"]["kind"], data["invocation"]["agent"], data["invocation"]["model_alias"]) == (
        "subagent", "factory-lens-reviewer", "sonnet")
    assert data["invocation"]["argv"] is None


def test_cli_role_resolve_author_is_optional(capsys):
    code, out, _ = run_cli(capsys, "role", "resolve", "lens-security", "--host", "openai")
    assert code == 0 and json.loads(out)["resolved"]["vendor"] == "claude"


def write_factory_yaml(folder, **settings):
    """A valid minimal factory.yaml in ``folder`` plus extra settings; returns its path."""
    data = {"project": "demo", "approvers": ["owner"], "verify": {"quick": "pytest -q", "full": "pytest"}, **settings}
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "factory.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


OVERRIDE = {"lens-security": {"bindings": {"openai": {"model": "override-model", "effort": "low"}}}}


@pytest.mark.parametrize("as_file", [False, True], ids=["project-folder", "factory-yaml-file"])
def test_cli_role_resolve_project_override_changes_the_resolved_model(tmp_path, capsys, as_file):
    yaml_path = write_factory_yaml(tmp_path / "my project", roles=OVERRIDE)
    project = yaml_path if as_file else yaml_path.parent
    code, out, _ = run_cli(capsys, "role", "resolve", "lens-security", "--host", "claude", "--author", "claude",
                           "--project", project)
    data = json.loads(out)
    assert code == 0
    assert (data["resolved"]["model"], data["resolved"]["effort"]) == ("override-model", "low")
    assert data["invocation"]["argv"][2:4] == ["-m", "override-model"]
    # Without --project the factory default applies.
    _, out, _ = run_cli(capsys, "role", "resolve", "lens-security", "--host", "claude", "--author", "claude")
    assert json.loads(out)["resolved"]["model"] == "gpt-6.1-sol"


def test_cli_role_run_project_override_reaches_the_command(tmp_path, capsys, fake_cli):
    project = write_factory_yaml(tmp_path / "proj", roles=OVERRIDE).parent
    prompt, out = tmp_path / "p.md", tmp_path / "o.md"
    prompt.write_text("x", encoding="utf-8")
    code, _, _ = run_cli(capsys, "role", "run", "lens-security", "--host", "claude", "--author", "claude",
                         "--prompt", prompt, "--out", out, "--project", project)
    assert code == 0
    (call,) = fake_cli.calls()
    assert call["argv"][1:3] == ["-m", "override-model"]
    assert "model=override-model" in out.read_text(encoding="utf-8").splitlines()[-1]


@pytest.mark.parametrize("command", ["resolve", "run"])
def test_cli_role_with_a_bad_project_exits_2_listing_the_problems(tmp_path, capsys, command):
    project = write_factory_yaml(tmp_path / "proj", roles={"ghost": {"vendor": "claude"}}).parent
    extra = ["--prompt", tmp_path / "p.md", "--out", tmp_path / "o.md"] if command == "run" else []
    code, out, err = run_cli(capsys, "role", command, "lens-security", "--host", "claude", "--project", project, *extra)
    assert code == 2 and out == "" and "ghost" in err
    code, _, err = run_cli(capsys, "role", command, "lens-security", "--host", "claude",
                           "--project", tmp_path / "missing", *extra)
    assert code == 2 and "missing" in err and "file not found" in err


def test_cli_role_resolve_unknown_role_exits_2_on_stderr(capsys):
    code, out, err = run_cli(capsys, "role", "resolve", "ghost", "--host", "claude")
    assert code == 2 and out == "" and "ghost" in err


def test_cli_role_resolve_rejects_an_unknown_host():
    with pytest.raises(SystemExit) as e:
        cli.main(["role", "resolve", "tester", "--host", "gemini"])
    assert e.value.code == 2


def test_cli_role_run_command_role_writes_the_output(tmp_path, capsys, fake_cli):
    prompt, out = tmp_path / "prompt.md", tmp_path / "reviews" / "r1-security.md"
    prompt.write_text("Please review.\n", encoding="utf-8")
    code, stdout, _ = run_cli(capsys, "role", "run", "lens-security", "--host", "claude", "--author", "claude",
                              "--prompt", prompt, "--out", out)
    assert code == 0 and str(out) in stdout
    assert out.read_text(encoding="utf-8").splitlines()[0] == "VERDICT: PASS"
    (call,) = fake_cli.calls()
    assert call["prompt"] == "Please review.\n"


def test_cli_role_run_passes_the_schema_and_fallback_binding(tmp_path, capsys, fake_cli):
    prompt, out, schema = tmp_path / "p.md", tmp_path / "o.md", tmp_path / "s.json"
    prompt.write_text("x", encoding="utf-8")
    code, _, _ = run_cli(capsys, "role", "run", "lens-security", "--host", "claude", "--author", "claude",
                         "--prompt", prompt, "--out", out, "--schema", schema, "--fallback")
    assert code == 0
    (call,) = fake_cli.calls()
    assert call["argv"][1:4] == ["-m", "gpt-6-astra", "-c"] and str(schema) in call["argv"]
    assert "model=gpt-6-astra" in out.read_text(encoding="utf-8").splitlines()[-1]


def test_cli_role_run_failure_exits_1_with_no_output_and_suggests_the_fallback(tmp_path, capsys, fake_cli):
    fake_cli.mode("fail7")
    prompt, out = tmp_path / "p.md", tmp_path / "o.md"
    prompt.write_text("x", encoding="utf-8")
    code, stdout, err = run_cli(capsys, "role", "run", "lens-security", "--host", "claude", "--author", "claude",
                                "--prompt", prompt, "--out", out)
    assert code == 1 and not out.exists()
    assert "--fallback" in err and "simulated failure" in err


def test_cli_role_run_subagent_role_prints_the_spawn_line_and_exits_3(tmp_path, capsys, fake_cli):
    prompt, out = tmp_path / "p.md", tmp_path / "o.md"
    prompt.write_text("x", encoding="utf-8")
    code, stdout, _ = run_cli(capsys, "role", "run", "lens-spec", "--host", "claude", "--author", "claude",
                              "--prompt", prompt, "--out", out)
    assert code == 3
    assert stdout.strip() == "spawn subagent factory-lens-reviewer with model opus"
    assert not out.exists() and fake_cli.calls() == []


def test_cli_role_run_main_role_exits_3_without_running_anything(tmp_path, capsys, fake_cli):
    prompt, out = tmp_path / "p.md", tmp_path / "o.md"
    prompt.write_text("x", encoding="utf-8")
    code, stdout, _ = run_cli(capsys, "role", "run", "implementer", "--host", "claude",
                              "--prompt", prompt, "--out", out)
    assert code == 3 and "implementer" in stdout and "main session" in stdout
    assert not out.exists() and fake_cli.calls() == []


def test_cli_role_run_missing_prompt_file_exits_2(tmp_path, capsys, fake_cli):
    code, _, err = run_cli(capsys, "role", "run", "lens-security", "--host", "claude", "--author", "claude",
                           "--prompt", tmp_path / "nope.md", "--out", tmp_path / "o.md")
    assert code == 2 and "nope.md" in err and fake_cli.calls() == []


def test_cli_role_run_reads_a_prompt_with_a_bom(tmp_path, capsys, fake_cli):
    prompt, out = tmp_path / "p.md", tmp_path / "o.md"
    prompt.write_bytes(b"\xef\xbb\xbfhello\r\n")
    code, _, _ = run_cli(capsys, "role", "run", "lens-security", "--host", "claude", "--author", "claude",
                         "--prompt", prompt, "--out", out)
    assert code == 0
    assert fake_cli.calls()[0]["prompt"].startswith("hello")


def test_cli_role_run_unknown_role_exits_2(tmp_path, capsys):
    prompt = tmp_path / "p.md"
    prompt.write_text("x", encoding="utf-8")
    code, _, err = run_cli(capsys, "role", "run", "ghost", "--host", "claude", "--prompt", prompt, "--out", tmp_path / "o.md")
    assert code == 2 and "ghost" in err


def test_cli_role_without_subcommand_exits_2(capsys):
    code, out, _ = run_cli(capsys, "role")
    assert code == 2 and "resolve" in out and "run" in out
