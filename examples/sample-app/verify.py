"""Verify the sample app.

    python verify.py --quick   the unit tests
    python verify.py           the unit tests, then a byte-compile of every module

Runs from any folder. Exits 0 when everything passes, non-zero otherwise.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run(command: list[str]) -> int:
    print("$ " + " ".join(command), flush=True)
    return subprocess.run(command, cwd=ROOT).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the sample app.")
    parser.add_argument("--quick", action="store_true", help="run the unit tests only")
    args = parser.parse_args(argv)

    code = run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v"])
    if code == 0 and not args.quick:
        code = run([sys.executable, "-m", "compileall", "-q", "rangering", "tests", "verify.py"])
    print(f"verify {'quick' if args.quick else 'full'}: {'PASS' if code == 0 else 'FAIL'}", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
