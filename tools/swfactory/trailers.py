"""Commit trailers that tie a commit to the work item and the role that made it (design section 9)."""
from __future__ import annotations


def _single_line(name: str, value: str) -> str:
    """``value`` as it may appear in a trailer: not blank, and on one line (a newline would forge another trailer)."""
    if not value.strip() or "\n" in value or "\r" in value:
        raise ValueError(f"trailer {name} must be a non-empty single line (got {value!r})")
    return value


def work_trailer(work: str) -> str:
    """The ``Factory-Work`` line for a work id or a task of one, such as ``FEA-7`` or ``FEA-7/T3``."""
    return f"Factory-Work: {_single_line('work', work)}"


def format_trailers(work: str, role: str, model: str, effort: str) -> str:
    """The three factory trailers for a commit made by ``role`` on ``model`` at ``effort``.

    Append the result to a commit message after a blank line. It has no trailing newline.
    """
    return "\n".join(
        (
            work_trailer(work),
            f"Factory-Role: {_single_line('role', role)}",
            f"Factory-Model: {_single_line('model', model)} ({_single_line('effort', effort)})",
        )
    )
