"""Running git for the factory: one argv-list call that raises ``GitError`` with the command and git's own message."""
from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

_TIMEOUT_SECONDS = 120


class GitError(RuntimeError):
    """A git command failed, or the repository is not in a state the factory can work with.

    ``command`` is the argv that failed (empty when the problem was found without running git) and
    ``stderr`` is what git printed.
    """

    def __init__(self, message: str, command: Sequence[str] = (), stderr: str = "") -> None:
        super().__init__(message)
        self.command = list(command)
        self.stderr = stderr


def run_git(repo: Path, *args: str) -> str:
    """Run ``git <args>`` in ``repo`` (no shell) and return its stdout without the trailing newline.

    Raises ``GitError`` when git cannot be run, times out, or exits non-zero.
    """
    command = ["git", *args]
    try:
        done = subprocess.run(
            command,
            cwd=repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        raise GitError(f"cannot run {' '.join(command)}: git is not installed or {repo} does not exist", command) from None
    except (OSError, subprocess.TimeoutExpired) as err:
        raise GitError(f"cannot run {' '.join(command)} in {repo}: {err}", command) from None
    if done.returncode != 0:
        stderr = done.stderr.strip()
        raise GitError(
            f"{' '.join(command)} failed in {repo} (exit {done.returncode}): {stderr or done.stdout.strip() or 'no message'}",
            command,
            stderr,
        )
    return done.stdout.rstrip("\r\n")


def ref_exists(repo: Path, ref: str) -> bool:
    """Whether ``ref`` names a commit in ``repo`` (false for a branch that is missing or has no commits)."""
    try:
        run_git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    except GitError:
        return False
    return True


def current_branch(repo: Path) -> str | None:
    """The checked-out branch of ``repo``, or ``None`` when HEAD is detached."""
    return run_git(repo, "branch", "--show-current") or None
