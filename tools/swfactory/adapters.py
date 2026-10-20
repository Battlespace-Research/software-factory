"""Host adapters: plan how a resolved role runs on a host, and run the roles that need a vendor CLI.

A role runs one of three ways: the session itself does it (``main``), the host spawns a subagent
(``subagent``), or a vendor CLI runs it headless (``command``): ``codex exec`` for OpenAI models,
``claude -p`` for Claude models on a Codex host. The prompt goes in on stdin and the reviewer's
text comes back as a file the adapter stamps with where it came from. The vendor CLI never
writes the result file itself: it writes a sibling ``<file>.partial``, and the adapter stamps that
and renames it onto the result file only when the run succeeded, so a failed, timed-out or
interrupted run can never leave something that looks like a verdict.

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
    "effort": "--effort",
    "output_format": "--output-format",
    "permission_mode": "--permission-mode",
    "allowed_tools": "--allowedTools",  # variadic: keep it the last option on the command line
}
CLAUDE_OUTPUT_FORMAT = "json"
CLAUDE_PERMISSION_MODE = "plan"  # the read-only guard of a claude -p run (see plan_invocation)
CLAUDE_READ_ONLY_TOOLS = "Read,Grep,Glob"  # pre-approved so the run needs no prompt; not a limit on its tools

# Environment variable holding extra arguments for every ``codex exec`` run, for example the
# Windows workaround ``-c windows.sandbox="unelevated"`` when Codex's elevated sandbox fails.
CODEX_ARGS_ENV = "FACTORY_CODEX_ARGS"

# Options the extra arguments may not carry, as a whole argument or its start (``--sandbox=...``, ``-sread-only``,
# every ``--dangerously-bypass...``): they would replace the sandbox, the approval policy, the output file or
# schema, or the working folder the factory chose for the run. ``-c key=value`` overrides stay allowed.
REFUSED_CODEX_ARGS = (
    "-s", "--sandbox",
    "-a", "--ask-for-approval", "--full-auto", "--approve-for-me", "--dangerously-bypass",
    "-o", "--output-last-message", "--output-schema",
    "-C", "--cd",
)

# Role-name globs (first match wins) to the subagent definition that runs them on a Claude host.
AGENT_FOR_ROLE: dict[str, str] = {
    "lens-*": "factory-lens-reviewer",
    "doc-reviewer-*": "factory-doc-reviewer",
    "tester": "factory-tester",
    "researcher": "factory-researcher",
}

_MODEL_ALIAS = re.compile(r"^claude-([a-z]+)-")

# Batch files (the npm shims of the vendor CLIs are ``.cmd``) are run by cmd.exe, which re-reads the
# arguments: these characters would be treated as syntax, so such a run is refused instead.
_BATCH_SUFFIXES = (".cmd", ".bat")
_CMD_METACHARACTERS = "&|<>^%!\r\n"

PARTIAL_SUFFIX = ".partial"

_STDERR_TAIL_LINES = 20
_STDERR_TAIL_CHARS = 2000
_KILL_DRAIN_S = 10


# --------------------------------------------------------------------------- planning

@dataclass(frozen=True)
class Invocation:
    """How to run a resolved role.

    ``main``: the session does it. ``subagent``: spawn ``agent`` with ``model_alias``.
    ``command``: run ``argv`` with the prompt on stdin. ``sandbox`` is the access the run gets
    (``read-only`` or ``workspace-write``); it is stamped into the result header. Only a
    ``read-only`` role is held to ``read-only``: a ``write`` role and a ``no-product-edits`` role
    (the tester, who must build and run verify, and is kept from product edits by its prompt and
    agent) both get ``workspace-write``.
    """

    kind: Literal["main", "subagent", "command"]
    argv: list[str] | None
    agent: str | None
    model_alias: str | None
    effort: str
    sandbox: str


def _sandbox_for(access: str) -> str:
    return "read-only" if access == "read-only" else "workspace-write"


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
        flags["effort"], r.effort,
        flags["output_format"], CLAUDE_OUTPUT_FORMAT,
        flags["permission_mode"], CLAUDE_PERMISSION_MODE,
        flags["allowed_tools"], CLAUDE_READ_ONLY_TOOLS,
    ]


def plan_invocation(r: ResolvedRole, host: Vendor, out_path: Path, schema_path: Path | None = None) -> Invocation:
    """Decide how ``r`` runs on ``host``.

    A ``main`` role is done by the session. Any other OpenAI role runs through ``codex exec``.
    A Claude role on a Codex host runs through ``claude -p`` (always read-only). A Claude role on
    a Claude host is a subagent.

    What keeps ``claude -p`` read-only is plan mode (``--permission-mode plan``), not the tool list:
    ``--allowedTools Read,Grep,Glob`` pre-approves those tools, it does not restrict the run to them,
    and the v1 rehearsal saw such a reviewer run read-only shell commands (``git diff``, ``git show``)
    through Bash, which the lens needs. Denying writing tools by name (``--disallowedTools``) and
    capping the turns (``--max-turns``) are plan-2 items.
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
    """The arguments in ``FACTORY_CODEX_ARGS``; ``ConfigError`` for unbalanced quotes or a refused option."""
    raw = os.environ.get(CODEX_ARGS_ENV, "")
    if not raw.strip():
        return []
    try:
        # Windows keeps the quotes (a TOML string such as "unelevated" needs them); POSIX strips them.
        extra = shlex.split(raw, posix=(os.name != "nt"))
    except ValueError as err:
        raise ConfigError([f"{CODEX_ARGS_ENV}: cannot split {raw!r} into arguments: {err}"]) from None
    refused = [arg for arg in extra if arg.strip("\"'").startswith(REFUSED_CODEX_ARGS)]
    if refused:
        raise ConfigError([
            f"{CODEX_ARGS_ENV}: {arg!r} is not allowed: it would override the sandbox, approval, output-file or "
            "working-folder settings the factory gives every codex exec run (use -c key=value for other settings)"
            for arg in refused
        ])
    return extra


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


def _stop(proc: subprocess.Popen) -> None:
    """Kill the process tree and wait (briefly) for it to be gone, so it cannot write any more."""
    _kill_tree(proc)
    try:
        proc.wait(timeout=_KILL_DRAIN_S)
    except subprocess.TimeoutExpired:
        pass


def _run_process(argv: list[str], stdin_text: str, timeout_s: int) -> tuple[int | None, str, str]:
    """Run ``argv`` with ``stdin_text`` as UTF-8 stdin. Returns (exit code or None on timeout, stdout, stderr).

    Whatever ends the wait early (a timeout, ``KeyboardInterrupt``, ``SystemExit`` from a SIGTERM
    handler) kills the whole process tree first; the interrupts are then re-raised.
    """
    popen_args: dict = {} if os.name == "nt" else {"start_new_session": True}
    proc = subprocess.Popen(
        argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **popen_args
    )
    timed_out = False
    try:
        try:
            out, err = proc.communicate(stdin_text.encode("utf-8"), timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(proc)
            try:
                out, err = proc.communicate(timeout=_KILL_DRAIN_S)
            except subprocess.TimeoutExpired:
                out, err = b"", b""
    except BaseException:
        _stop(proc)
        raise
    text_out = out.decode("utf-8", errors="replace").replace("\r\n", "\n")
    text_err = err.decode("utf-8", errors="replace").replace("\r\n", "\n")
    return (None if timed_out else proc.returncode), text_out, text_err


def _batch_file_problem(executable: str, argv: list[str]) -> str:
    """Why a batch-file run must be refused (empty when it may go ahead).

    cmd.exe re-reads the command line of a ``.cmd`` or ``.bat`` file, so an argument holding one of
    its metacharacters (an output path such as ``R&D\\o.md``) would be cut up and run as other
    commands. Anything started directly (an ``.exe``, a POSIX program) is not affected.
    """
    if Path(executable).suffix.lower() not in _BATCH_SUFFIXES:
        return ""
    for arg in [executable, *argv[1:]]:
        for char in _CMD_METACHARACTERS:
            if char in arg:
                return (
                    f"not run: {executable} is a batch file, so cmd.exe re-reads its arguments, and the argument "
                    f"{arg!r} contains {char!r}. Use paths and values without & | < > ^ % ! or a line break."
                )
    return ""


def _redirect_output(argv: list[str], out_path: Path, partial: Path) -> list[str]:
    """Point the CLI's own output-file option (``-o``) at ``partial`` instead of ``out_path``."""
    flag = CODEX_EXEC_FLAGS["output_file"]
    redirected = list(argv)
    for index in range(len(redirected) - 1):
        if redirected[index] == flag and redirected[index + 1] == str(out_path):
            redirected[index + 1] = str(partial)
    return redirected


def _discard(*paths: Path) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:  # nothing more can be done; never hide the error that led here
            pass


def _reviewer_text(r: ResolvedRole, partial: Path, stdout: str) -> tuple[str, str]:
    """The reviewer's text and, when there is none, the reason. Codex writes ``-o``; Claude prints JSON."""
    if r.vendor == "openai":
        if partial.is_file():
            written = partial.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
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


def _execute(
    inv: Invocation, r: ResolvedRole, prompt_text: str, out_path: Path, partial: Path,
    argv: list[str], extra: list[str], timeout_s: int,
) -> RunResult:
    def failed(exit_code: int | None, why: str, suggest_fallback: bool = True) -> RunResult:
        _discard(out_path, partial)
        return RunResult(ok=False, exit_code=exit_code, out_path=out_path, stderr_tail=why,
                         suggest_fallback=suggest_fallback)

    executable = shutil.which(argv[0])
    if executable is None:
        return failed(None, f"executable not found on PATH: {argv[0]}")
    refusal = _batch_file_problem(executable, argv)
    if refusal:
        return failed(None, refusal, suggest_fallback=False)  # the same arguments would fail with any binding

    out_path.parent.mkdir(parents=True, exist_ok=True)
    _discard(out_path, partial)  # nothing of an earlier run may pass for this run's output
    try:
        exit_code, stdout, stderr = _run_process([executable, *argv[1:]], prompt_text, timeout_s)
    except OSError as err:
        return failed(None, f"cannot start {argv[0]}: {err}")
    if exit_code is None:
        return failed(None, f"timed out after {timeout_s} s\n{_tail(stderr)}".strip())
    if exit_code != 0:
        return failed(exit_code, _tail(stderr) or f"{argv[0]} exited with code {exit_code}")

    text, problem = _reviewer_text(r, partial, stdout)
    if problem:
        return failed(exit_code, problem)
    partial.write_text(f"{text}\n\n{_stamp(r, inv, extra)}\n", encoding="utf-8", newline="\n")
    os.replace(partial, out_path)
    return RunResult(ok=True, exit_code=exit_code, out_path=out_path, stderr_tail=_tail(stderr), suggest_fallback=False)


def run_invocation(inv: Invocation, r: ResolvedRole, prompt_text: str, out_path: Path, timeout_s: int = 3600) -> RunResult:
    """Run a ``command`` invocation: prompt on stdin, the reviewer's text to ``out_path``.

    The vendor CLI is pointed at ``<out_path>.partial``, never at ``out_path``. On success the text
    gets a trailing ``<!-- factory: ... -->`` stamp (role, vendor, model, effort, sandbox, time and
    any extra Codex arguments) and the partial file is renamed onto ``out_path``. A missing
    executable, a non-zero exit, a timeout or an empty result deletes both files and returns
    ``ok=False`` with ``suggest_fallback=True``; so does an interrupt (``KeyboardInterrupt``,
    ``SystemExit``), after the process tree is killed, and the interrupt is re-raised.

    A batch file (``.cmd``, ``.bat``; the npm shim of a vendor CLI is one) is refused with
    ``suggest_fallback=False`` and nothing is created when an argument holds a character cmd.exe
    would re-interpret (``& | < > ^ % !`` or a line break). Extra Codex arguments come from the
    ``FACTORY_CODEX_ARGS`` environment variable; unbalanced quotes there, or an option in
    ``REFUSED_CODEX_ARGS`` (sandbox, approvals, output file or schema, working folder), raise
    ``ConfigError`` before anything is started, created or deleted.
    """
    if inv.kind != "command" or not inv.argv:
        raise ValueError(f"run_invocation runs command invocations only, not {inv.kind!r}")
    out_path = Path(out_path)
    partial = out_path.with_name(out_path.name + PARTIAL_SUFFIX)
    extra = _extra_codex_args() if r.vendor == "openai" else []
    argv = _redirect_output(_with_extra(inv.argv, extra), out_path, partial)
    try:
        return _execute(inv, r, prompt_text, out_path, partial, argv, extra, timeout_s)
    except BaseException:
        _discard(out_path, partial)
        raise
