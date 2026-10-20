"""A stand-in for the ``codex`` and ``claude`` command-line tools, used by the adapter tests.

The shims the ``fake_cli`` fixture puts on PATH run this script as
``fake_cli.py <tool> <arguments...>``. Behaviour comes from the environment:

* ``FAKE_CLI_MODE`` picks what the fake does (see ``MODES``); the default is ``pass``.
* ``FAKE_CLI_LOG``, when set, names a file that receives one JSON line per call holding the
  tool name, the arguments the tool received, and the prompt it read from stdin.

Nothing here talks to a model.
"""
from __future__ import annotations

import json
import os
import sys
import time

PASS_TEXT = "VERDICT: PASS\nChecked: café ✓\n"

MODES = {
    "pass": "text on stdout and, when -o FILE is given, in FILE (the way codex exec behaves)",
    "nofile": "text on stdout only; -o FILE is never written",
    "fail7": "partial text in -o FILE and on stdout, a message on stderr, exit code 7",
    "json": "one JSON object on stdout with a result field (the way claude -p --output-format json behaves)",
    "json-error": "a JSON object on stdout with is_error true, exit code 0",
    "badjson": "text that is not JSON on stdout, exit code 0",
    "empty": "no output at all, exit code 0",
    "sleep": "sleeps for a minute (for the timeout test)",
}


def _write_stdout(text: str) -> None:
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.buffer.flush()


def _write_file(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def main() -> int:
    tool, args = sys.argv[1], sys.argv[2:]
    prompt = sys.stdin.buffer.read().decode("utf-8")
    log = os.environ.get("FAKE_CLI_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"tool": tool, "argv": args, "prompt": prompt}) + "\n")

    mode = os.environ.get("FAKE_CLI_MODE", "pass")
    out_file = args[args.index("-o") + 1] if "-o" in args else None

    if mode == "pass":
        if out_file:
            _write_file(out_file, PASS_TEXT)
        _write_stdout(PASS_TEXT)
        return 0
    if mode == "nofile":
        _write_stdout(PASS_TEXT)
        return 0
    if mode == "fail7":
        if out_file:
            _write_file(out_file, "VERDICT: PA")
        _write_stdout("VERDICT: PA")
        sys.stderr.write("fake-cli: simulated failure\n")
        return 7
    if mode == "json":
        _write_stdout(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                                  "result": PASS_TEXT, "session_id": "fake"}))
        return 0
    if mode == "json-error":
        _write_stdout(json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True,
                                  "result": "stopped early"}))
        return 0
    if mode == "badjson":
        _write_stdout("this is not json")
        return 0
    if mode == "empty":
        if out_file:
            _write_file(out_file, "")
        return 0
    if mode == "sleep":
        time.sleep(60)
        return 0
    sys.stderr.write(f"fake-cli: unknown FAKE_CLI_MODE {mode!r}\n")
    return 99


if __name__ == "__main__":
    raise SystemExit(main())
