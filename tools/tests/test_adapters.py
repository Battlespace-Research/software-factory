"""Tests for swfactory.adapters (invocation planning and headless runs) and the ``factory role`` CLI."""
from __future__ import annotations

import dataclasses
import json
import os
import re
import signal
import subprocess
import sys
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


def partial_of(out):
    """The sibling file the vendor CLI writes before the adapter stamps it and renames it onto ``out``."""
    return out.with_name(out.name + ".partial")


def run_argv(inv, out):
    """The arguments the vendor CLI really receives: planned argv with ``-o`` pointing at the partial file."""
    argv = list(inv.argv[1:])
    if "-o" in argv:
        assert argv[argv.index("-o") + 1] == str(out)
        argv[argv.index("-o") + 1] = str(partial_of(out))
    return argv


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


@pytest.mark.parametrize("access, sandbox", [
    ("write", "workspace-write"),
    ("no-product-edits", "workspace-write"),   # the tester must be able to build and run verify
    ("read-only", "read-only"),
])
def test_codex_sandbox_follows_the_roles_access(table, tmp_path, access, sandbox):
    r, _, out = lens(table, tmp_path)
    inv = plan_invocation(dataclasses.replace(r, access=access), "claude", out)
    assert inv.sandbox == sandbox
    assert inv.argv[inv.argv.index("-s") + 1] == sandbox


def test_no_product_edits_roles_run_codex_workspace_write(table, tmp_path):
    # The tester on a Codex host runs through codex exec; a read-only sandbox would block the
    # writes a build needs, and its no-product-edits rule is carried by its prompt and agent.
    r, inv, _ = lens(table, tmp_path, role="tester", host="openai", author=None)
    assert (r.vendor, r.access) == ("openai", "no-product-edits")
    assert inv.kind == "command" and inv.sandbox == "workspace-write"
    assert inv.argv[inv.argv.index("-s") + 1] == "workspace-write"


def test_claude_command_stays_read_only_whatever_the_access(table, tmp_path):
    r, _, out = lens(table, tmp_path, role="lens-correctness", host="openai", author="openai")
    inv = plan_invocation(dataclasses.replace(r, access="write"), "openai", out)
    assert inv.sandbox == "read-only" and "--permission-mode" in inv.argv


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
    assert call["argv"] == run_argv(inv, out)    # what was planned, quotes and all, but for the -o file
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


def test_timeout_is_a_failure_and_kills_the_whole_process_tree(tmp_path, table, fake_cli, monkeypatch):
    # The fake runs behind a shim process (cmd.exe on Windows), creates the marker 3 s after it has read
    # the prompt, then sleeps a minute. Killing only the shim would leave it running to create the marker.
    marker_after_s = 3
    marker = tmp_path / "still-running.txt"
    fake_cli.mode("sleep")
    monkeypatch.setenv("FAKE_CLI_SLEEP", str(marker_after_s))
    monkeypatch.setenv("FAKE_CLI_MARKER", str(marker))
    r, inv, out = lens(table, tmp_path)
    started = time.monotonic()
    res = run_invocation(inv, r, "prompt", out, timeout_s=1)
    assert time.monotonic() - started < 60            # generous: it never waits out the fake's minute
    assert not res.ok and res.suggest_fallback and not out.exists()
    assert res.exit_code is None and "timed out" in res.stderr_tail
    assert len(fake_cli.calls()) == 1                  # the fake was running when the timeout hit
    time.sleep(marker_after_s + 2)                     # past the moment a surviving fake would create it
    assert not marker.exists(), "the vendor CLI outlived the timeout: the process tree was not killed"


def test_paths_with_spaces(tmp_path, table, fake_cli):
    out = tmp_path / "dir with space" / "review out.md"          # the folder does not exist yet
    r = resolve_role(table, "lens-security", "claude", "claude")
    schema = tmp_path / "schema dir" / "s.json"
    inv = plan_invocation(r, "claude", out, schema)
    res = run_invocation(inv, r, "prompt", out)
    assert res.ok and out.read_text(encoding="utf-8").splitlines()[0] == "VERDICT: PASS"
    (call,) = fake_cli.calls()
    assert str(partial_of(out)) in call["argv"] and str(schema) in call["argv"]
    assert not partial_of(out).exists()


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
    assert not partial_of(out).exists()


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


# --------------------------------------------------------------------------- the vendor CLI never writes out_path

def test_codex_writes_a_sibling_partial_file_that_is_renamed_onto_the_output(tmp_path, table, fake_cli):
    r, inv, out = lens(table, tmp_path)
    assert str(out) in inv.argv                       # the plan still names the real output file
    assert run_invocation(inv, r, "prompt", out).ok
    (call,) = fake_cli.calls()
    assert call["argv"][call["argv"].index("-o") + 1] == str(out) + ".partial"
    assert out.exists() and not partial_of(out).exists()
    assert [p.name for p in tmp_path.iterdir()] == ["o.md"]


def test_failed_run_after_partial_output_leaves_neither_the_output_nor_the_partial_file(tmp_path, table, fake_cli):
    fake_cli.mode("fail7")                            # the fake writes partial text to its -o file, then exits 7
    r, inv, out = lens(table, tmp_path)
    res = run_invocation(inv, r, "prompt", out)
    (call,) = fake_cli.calls()
    assert call["argv"][call["argv"].index("-o") + 1] == str(partial_of(out))    # it did write somewhere
    assert not res.ok and res.exit_code == 7
    assert not out.exists() and not partial_of(out).exists()


def test_stale_partial_file_from_an_earlier_run_is_not_taken_for_this_runs_output(tmp_path, table, fake_cli):
    fake_cli.mode("nofile")
    r, inv, out = lens(table, tmp_path)
    partial_of(out).write_text("STALE PARTIAL", encoding="utf-8")
    assert run_invocation(inv, r, "prompt", out).ok
    assert "STALE" not in out.read_text(encoding="utf-8") and not partial_of(out).exists()


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_interrupt_kills_the_cli_and_leaves_no_file_even_when_it_would_write_later(
        tmp_path, table, fake_cli, monkeypatch, interruption):
    fake_cli.mode("partial-late")                     # partial text now, a "late verdict" 2 s later
    monkeypatch.setenv("FAKE_CLI_SLEEP", "2")
    r, inv, out = lens(table, tmp_path)
    real_communicate = subprocess.Popen.communicate

    def interrupted(self, input=None, timeout=None):
        if input is None:
            return real_communicate(self, input, timeout)
        # Send the prompt exactly once (a second call with input raises ValueError), then poll for the
        # partial file the fake writes as soon as it has read the prompt.
        try:
            real_communicate(self, input, timeout=0.1)
        except subprocess.TimeoutExpired:
            pass
        deadline = time.monotonic() + 15
        while not partial_of(out).exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert partial_of(out).exists()
        raise interruption

    monkeypatch.setattr(subprocess.Popen, "communicate", interrupted)
    with pytest.raises(interruption):
        run_invocation(inv, r, "prompt", out)
    assert not out.exists() and not partial_of(out).exists()
    time.sleep(3.5)                                   # a CLI left running would write its late verdict by now
    assert not out.exists() and not partial_of(out).exists()


def test_sigterm_is_turned_into_system_exit_while_a_command_runs_and_restored_after():
    before = signal.getsignal(signal.SIGTERM)
    with cli.sigterm_as_system_exit():
        assert signal.getsignal(signal.SIGTERM) is not before
        with pytest.raises(SystemExit) as e:
            signal.raise_signal(signal.SIGTERM)
        assert e.value.code == 128 + signal.SIGTERM
    assert signal.getsignal(signal.SIGTERM) == before


# --------------------------------------------------------------------------- batch shims and cmd.exe

CMD_METACHARACTERS = ["&", "|", "<", ">", "^", "%", "!", "\n"]
ON_WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="the fake tools are .cmd batch shims only on Windows")


@ON_WINDOWS_ONLY
def test_batch_shim_with_a_metacharacter_in_the_output_path_is_refused_and_nothing_is_created(
        tmp_path, table, fake_cli):
    out = tmp_path / "R&D" / "o.md"
    r = resolve_role(table, "lens-security", "claude", "claude")
    res = run_invocation(plan_invocation(r, "claude", out), r, "prompt", out)
    assert not res.ok and res.suggest_fallback is False and res.exit_code is None
    assert "R&D" in res.stderr_tail and "&" in res.stderr_tail
    assert fake_cli.calls() == []                     # cmd.exe was never started
    assert list(tmp_path.iterdir()) == []             # no R&D folder, and no stray file at the path cmd.exe would cut


@ON_WINDOWS_ONLY
@pytest.mark.parametrize("char", CMD_METACHARACTERS)
def test_batch_shim_refuses_every_cmd_metacharacter_in_any_argument(tmp_path, table, fake_cli, char):
    r = resolve_role(table, "lens-security", "claude", "claude")
    odd = dataclasses.replace(r, model=f"m{char}x")
    out = tmp_path / "o.md"
    res = run_invocation(plan_invocation(odd, "claude", out), odd, "prompt", out)
    assert not res.ok and res.suggest_fallback is False and not out.exists()
    assert repr(f"m{char}x") in res.stderr_tail and fake_cli.calls() == []


@ON_WINDOWS_ONLY
def test_batch_shim_check_also_covers_extra_codex_args(tmp_path, table, fake_cli, monkeypatch):
    monkeypatch.setenv("FACTORY_CODEX_ARGS", "--profile a&b")
    r, inv, out = lens(table, tmp_path)
    res = run_invocation(inv, r, "prompt", out)
    assert not res.ok and res.suggest_fallback is False and "a&b" in res.stderr_tail and fake_cli.calls() == []


@ON_WINDOWS_ONLY
def test_a_refused_run_also_removes_an_earlier_output(tmp_path, table, fake_cli):
    r = resolve_role(table, "lens-security", "claude", "claude")
    out = tmp_path / "o.md"
    out.write_text("VERDICT: PASS\nfrom an earlier run\n", encoding="utf-8")
    odd = dataclasses.replace(r, model="m&x")
    assert not run_invocation(plan_invocation(odd, "claude", out), odd, "prompt", out).ok
    assert not out.exists()


def test_a_real_executable_is_not_restricted_by_the_batch_file_rule(tmp_path, table):
    # python.exe is started directly (no cmd.exe), so & in the path and in an argument is fine.
    r = resolve_role(table, "lens-security", "claude", "claude")
    out = tmp_path / "R&D" / "o.md"
    code = "import sys; open(sys.argv[sys.argv.index('-o') + 1], 'w').write('VERDICT: PASS')"
    inv = dataclasses.replace(
        plan_invocation(r, "claude", out), argv=[sys.executable, "-c", code, "-o", str(out), "--note", "a&b"])
    res = run_invocation(inv, r, "prompt", out)
    assert res.ok and out.read_text(encoding="utf-8").splitlines()[0] == "VERDICT: PASS"
    assert not partial_of(out).exists()


# --------------------------------------------------------------------------- FACTORY_CODEX_ARGS

def test_extra_codex_args_go_before_the_trailing_dash_and_into_the_stamp(tmp_path, table, fake_cli, monkeypatch):
    monkeypatch.setenv("FACTORY_CODEX_ARGS", '-c windows.sandbox="unelevated"')
    r, inv, out = lens(table, tmp_path)
    assert run_invocation(inv, r, "prompt", out).ok
    # On Windows the value is split without stripping quotes, so TOML strings keep theirs.
    sandbox_value = 'windows.sandbox="unelevated"' if os.name == "nt" else "windows.sandbox=unelevated"
    (call,) = fake_cli.calls()
    assert call["argv"][-3:] == ["-c", sandbox_value, "-"]
    assert call["argv"][:-3] == run_argv(inv, out)[:-1]       # nothing else changed
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
    assert call["argv"] == run_argv(inv, out)


def test_unbalanced_quotes_in_extra_codex_args_are_a_config_error(tmp_path, table, fake_cli, monkeypatch):
    monkeypatch.setenv("FACTORY_CODEX_ARGS", '-c "oops')
    r, inv, out = lens(table, tmp_path)
    with pytest.raises(ConfigError) as e:
        run_invocation(inv, r, "prompt", out)
    assert "FACTORY_CODEX_ARGS" in e.value.problems[0]
    assert fake_cli.calls() == []


REFUSED_EXTRA_ARGS = [
    "-s danger-full-access", "-sdanger-full-access", "--sandbox danger-full-access",
    "--sandbox=danger-full-access", "-s=workspace-write",
    "-a never", "--ask-for-approval never", "--ask-for-approval=on-request",
    "--full-auto", "--approve-for-me",
    "--dangerously-bypass-approvals-and-sandbox", "--dangerously-bypass-hook-trust", "--dangerously-bypass",
    "-o elsewhere.md", "--output-last-message elsewhere.md", "--output-last-message=elsewhere.md",
    "--output-schema other.json", "--output-schema=other.json",
    "-C ..", "--cd ..", "--cd=..",
    '-c windows.sandbox="unelevated" --sandbox workspace-write',   # a refused option after an allowed one
    '"--sandbox" danger-full-access',                               # quoted, as Windows keeps the quotes
]


@pytest.mark.parametrize("extra", REFUSED_EXTRA_ARGS)
def test_extra_codex_args_cannot_override_the_sandbox_approvals_output_or_folder(
        tmp_path, table, fake_cli, monkeypatch, extra):
    monkeypatch.setenv("FACTORY_CODEX_ARGS", extra)
    r = resolve_role(table, "lens-security", "claude", "claude")
    out = tmp_path / "reviews" / "o.md"                             # its folder does not exist yet
    inv = plan_invocation(r, "claude", out)
    with pytest.raises(ConfigError) as e:
        run_invocation(inv, r, "prompt", out)
    (problem,) = e.value.problems
    assert "FACTORY_CODEX_ARGS" in problem and "not allowed" in problem
    assert fake_cli.calls() == [] and list(tmp_path.iterdir()) == []   # nothing ran, nothing was created


def test_a_refused_extra_arg_does_not_delete_an_earlier_output(tmp_path, table, fake_cli, monkeypatch):
    monkeypatch.setenv("FACTORY_CODEX_ARGS", "--full-auto")
    r, inv, out = lens(table, tmp_path)
    out.write_text("VERDICT: PASS\nfrom an earlier run\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        run_invocation(inv, r, "prompt", out)
    assert out.read_text(encoding="utf-8") == "VERDICT: PASS\nfrom an earlier run\n"


@pytest.mark.parametrize("extra", [
    '-c windows.sandbox="unelevated"',
    "-c windows.sandbox=unelevated",
    "--config windows.sandbox=unelevated",
    "--profile quick",
])
def test_config_overrides_and_other_options_stay_allowed_in_extra_codex_args(
        tmp_path, table, fake_cli, monkeypatch, extra):
    monkeypatch.setenv("FACTORY_CODEX_ARGS", extra)
    r, inv, out = lens(table, tmp_path)
    assert run_invocation(inv, r, "prompt", out).ok
    (call,) = fake_cli.calls()
    assert call["argv"][-1] == "-" and len(call["argv"]) == len(run_argv(inv, out)) + 2


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


@pytest.mark.skipif(os.name != "nt", reason="the fake tools are .cmd batch shims only on Windows")
def test_cli_role_run_refused_batch_file_run_exits_1_without_suggesting_the_fallback(tmp_path, capsys, fake_cli):
    prompt, out = tmp_path / "p.md", tmp_path / "R&D" / "o.md"
    prompt.write_text("x", encoding="utf-8")
    code, stdout, err = run_cli(capsys, "role", "run", "lens-security", "--host", "claude", "--author", "claude",
                                "--prompt", prompt, "--out", out)
    assert code == 1 and "batch file" in err and "R&D" in err
    assert "--fallback" not in err and fake_cli.calls() == []
    assert not out.parent.exists()


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
