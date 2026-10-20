"""Host adapters: plan how a resolved role runs on a host, and run the roles that need a vendor CLI.

A role runs one of three ways: the session itself does it (``main``), the host spawns a subagent
(``subagent``), or a vendor CLI runs it headless (``command``): ``codex exec`` for OpenAI models,
``claude -p`` for Claude models on a Codex host. The prompt goes in on stdin and the reviewer's
text comes back as a file the adapter stamps with where it came from.

Every flag spelling the adapters pass to a vendor CLI is in ``CODEX_EXEC_FLAGS`` or
``CLAUDE_PRINT_FLAGS``, so a CLI rename is a one-line fix. No model name is written here; the
Claude subagent alias is derived from the model string the role table supplies.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from swfactory.config import ConfigError
from swfactory.roles import ResolvedRole, Vendor

CODEX_EXE = "codex"
CLAUDE_EXE = "claude"

CODEX_EXEC_FLAGS = {
    "command": "exec",
    "model": "-m",
    "config": "-c",
    "sandbox": "-s",
    "output_file": "-o",
    "output_schema": "--output-schema",
    "stdin": "-",  # the prompt argument that means "read the prompt from stdin"
}
CODEX_EFFORT_KEY = "model_reasoning_effort"

CLAUDE_PRINT_FLAGS = {
    "print": "-p",
    "model": "--model",
    "output_format": "--output-format",
    "permission_mode": "--permission-mode",
    "allowed_tools": "--allowedTools",  # variadic: keep it the last option on the command line
}
CLAUDE_OUTPUT_FORMAT = "json"
CLAUDE_PERMISSION_MODE = "plan"
CLAUDE_READ_ONLY_TOOLS = "Read,Grep,Glob"

# Environment variable holding extra arguments for every ``codex exec`` run, for example the
# Windows workaround ``-c windows.sandbox="unelevated"`` when Codex's elevated sandbox fails.
CODEX_ARGS_ENV = "FACTORY_CODEX_ARGS"

# Role-name globs (first match wins) to the subagent definition that runs them on a Claude host.
AGENT_FOR_ROLE: dict[str, str] = {
    "lens-*": "factory-lens-reviewer",
    "doc-reviewer-*": "factory-doc-reviewer",
    "tester": "factory-tester",
    "researcher": "factory-researcher",
}

_MODEL_ALIAS = re.compile(r"^claude-([a-z]+)-")

_STDERR_TAIL_LINES = 20
_STDERR_TAIL_CHARS = 2000
_KILL_DRAIN_S = 10


# --------------------------------------------------------------------------- planning

@dataclass(frozen=True)
class Invocation:
    """How to run a resolved role.

    ``main``: the session does it. ``subagent``: spawn ``agent`` with ``model_alias``.
    ``command``: run ``argv`` with the prompt on stdin. ``sandbox`` is the access the run gets
    (``read-only`` or ``workspace-write``); it is stamped into the result header.
    """

    kind: Literal["main", "subagent", "command"]
    argv: list[str] | None
    agent: str | None
    model_alias: str | None
    effort: str
    sandbox: str


def _sandbox_for(access: str) -> str:
    return "workspace-write" if access == "write" else "read-only"


def _agent_for(role: str) -> str | None:
    for pattern, agent in AGENT_FOR_ROLE.items():
        if fnmatch.fnmatchcase(role, pattern):
            return agent
    return None


def _model_alias(model: str) -> str:
    found = _MODEL_ALIAS.match(model)
    if found is None:
        raise ConfigError([
            f"cannot derive a Claude subagent alias from model {model!r}: "
            f"the model id must match {_MODEL_ALIAS.pattern}"
        ])
    return found.group(1)


def _codex_argv(r: ResolvedRole, sandbox: str, out_path: Path, schema_path: Path | None) -> list[str]:
    flags = CODEX_EXEC_FLAGS
    argv = [
        CODEX_EXE, flags["command"],
        flags["model"], r.model,
        flags["config"], f'{CODEX_EFFORT_KEY}="{r.effort}"',
        flags["sandbox"], sandbox,
        flags["output_file"], str(out_path),
    ]
    if schema_path is not None:
        argv += [flags["output_schema"], str(schema_path)]
    return argv + [flags["stdin"]]


def _claude_argv(r: ResolvedRole) -> list[str]:
    flags = CLAUDE_PRINT_FLAGS
    return [
        CLAUDE_EXE, flags["print"],
        flags["model"], r.model,
        flags["output_format"], CLAUDE_OUTPUT_FORMAT,
        flags["permission_mode"], CLAUDE_PERMISSION_MODE,
        flags["allowed_tools"], CLAUDE_READ_ONLY_TOOLS,
    ]


def plan_invocation(r: ResolvedRole, host: Vendor, out_path: Path, schema_path: Path | None = None) -> Invocation:
    """Decide how ``r`` runs on ``host``.

    A ``main`` role is done by the session. Any other OpenAI role runs through ``codex exec``.
    A Claude role on a Codex host runs through ``claude -p`` (always read-only). A Claude role on
    a Claude host is a subagent.
    """
    sandbox = _sandbox_for(r.access)
    if r.run_as == "main":
        return Invocation("main", None, None, None, r.effort, sandbox)
    if r.vendor == "openai":
        return Invocation("command", _codex_argv(r, sandbox, out_path, schema_path), None, None, r.effort, sandbox)
    if host == "openai":
        return Invocation("command", _claude_argv(r), None, None, r.effort, "read-only")
    return Invocation("subagent", None, _agent_for(r.role), _model_alias(r.model), r.effort, sandbox)


# --------------------------------------------------------------------------- running

@dataclass
class RunResult:
    """The outcome of ``run_invocation``. On failure there is no file at ``out_path``."""

    ok: bool
    exit_code: int | None
    out_path: Path
    stderr_tail: str
    suggest_fallback: bool


def _tail(text: str) -> str:
    lines = text.strip().splitlines()[-_STDERR_TAIL_LINES:]
    return "\n".join(lines)[-_STDERR_TAIL_CHARS:]


def _extra_codex_args() -> list[str]:
    raw = os.environ.get(CODEX_ARGS_ENV, "")
    if not raw.strip():
        return []
    try:
        # Windows keeps the quotes (a TOML string such as "unelevated" needs them); POSIX strips them.
        return shlex.split(raw, posix=(os.name != "nt"))
    except ValueError as err:
        raise ConfigError([f"{CODEX_ARGS_ENV}: cannot split {raw!r} into arguments: {err}"]) from None


def _with_extra(argv: list[str], extra: list[str]) -> list[str]:
    """Insert ``extra`` just before the trailing stdin marker (or at the end when there is none)."""
    if not extra:
        return list(argv)
    stdin_marker = CODEX_EXEC_FLAGS["stdin"]
    if argv and argv[-1] == stdin_marker:
        return argv[:-1] + extra + argv[-1:]
    return argv + extra


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the process and everything it started (a ``.cmd`` shim leaves its child running)."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except OSError:  # already gone, or no taskkill: the proc.kill() below still ends the process itself
        pass
    proc.kill()


def _run_process(argv: list[str], stdin_text: str, timeout_s: int) -> tuple[int | None, str, str]:
    """Run ``argv`` with ``stdin_text`` as UTF-8 stdin. Returns (exit code or None on timeout, stdout, stderr)."""
    popen_args: dict = {} if os.name == "nt" else {"start_new_session": True}
    proc = subprocess.Popen(
        argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **popen_args
    )
    timed_out = False
    try:
        out, err = proc.communicate(stdin_text.encode("utf-8"), timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=_KILL_DRAIN_S)
        except subprocess.TimeoutExpired:
            out, err = b"", b""
    text_out = out.decode("utf-8", errors="replace").replace("\r\n", "\n")
    text_err = err.decode("utf-8", errors="replace").replace("\r\n", "\n")
    return (None if timed_out else proc.returncode), text_out, text_err


def _reviewer_text(r: ResolvedRole, out_path: Path, stdout: str) -> tuple[str, str]:
    """The reviewer's text and, when there is none, the reason. Codex writes ``-o``; Claude prints JSON."""
    if r.vendor == "openai":
        if out_path.is_file():
            written = out_path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
            if written.strip():
                return written.strip(), ""
        return stdout.strip(), "" if stdout.strip() else "the run produced no output"
    try:
        data = json.loads(stdout)
    except ValueError:
        return "", f"claude -p did not print JSON: {_tail(stdout) or 'no output'}"
    if not isinstance(data, dict) or data.get("is_error") or not isinstance(data.get("result"), str):
        detail = data.get("result") if isinstance(data, dict) else None
        return "", f"claude -p reported an error: {detail or _tail(stdout)}"
    text = data["result"].replace("\r\n", "\n").strip()
    return text, "" if text else "the run produced no output"


def _stamp(r: ResolvedRole, inv: Invocation, extra: list[str]) -> str:
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = (f"<!-- factory: role={r.role} vendor={r.vendor} model={r.model} effort={r.effort} "
            f"sandbox={inv.sandbox} at={at}")
    if extra:
        line += f" extra={json.dumps(' '.join(extra))}"
    return line + " -->"


def run_invocation(inv: Invocation, r: ResolvedRole, prompt_text: str, out_path: Path, timeout_s: int = 3600) -> RunResult:
    """Run a ``command`` invocation: prompt on stdin, the reviewer's text to ``out_path``.

    The file ends with a ``<!-- factory: ... -->`` stamp naming the role, vendor, model, effort,
    sandbox and time (and any extra Codex arguments). A missing executable, a non-zero exit, a
    timeout or an empty result deletes any partial file and returns ``ok=False`` with
    ``suggest_fallback=True``. Extra Codex arguments come from the ``FACTORY_CODEX_ARGS``
    environment variable; unbalanced quotes there raise ``ConfigError``.
    """
    if inv.kind != "command" or not inv.argv:
        raise ValueError(f"run_invocation runs command invocations only, not {inv.kind!r}")
    out_path = Path(out_path)
    extra = _extra_codex_args() if r.vendor == "openai" else []
    argv = _with_extra(inv.argv, extra)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.unlink(missing_ok=True)  # nothing of an earlier run may pass for this run's output

    def failed(exit_code: int | None, why: str) -> RunResult:
        out_path.unlink(missing_ok=True)
        return RunResult(ok=False, exit_code=exit_code, out_path=out_path, stderr_tail=why, suggest_fallback=True)

    executable = shutil.which(argv[0])
    if executable is None:
        return failed(None, f"executable not found on PATH: {argv[0]}")
    try:
        exit_code, stdout, stderr = _run_process([executable, *argv[1:]], prompt_text, timeout_s)
    except OSError as err:
        return failed(None, f"cannot start {argv[0]}: {err}")
    if exit_code is None:
        return failed(None, f"timed out after {timeout_s} s\n{_tail(stderr)}".strip())
    if exit_code != 0:
        return failed(exit_code, _tail(stderr) or f"{argv[0]} exited with code {exit_code}")

    text, problem = _reviewer_text(r, out_path, stdout)
    if problem:
        return failed(exit_code, problem)
    out_path.write_text(f"{text}\n\n{_stamp(r, inv, extra)}\n", encoding="utf-8", newline="\n")
    return RunResult(ok=True, exit_code=exit_code, out_path=out_path, stderr_tail=_tail(stderr), suggest_fallback=False)
