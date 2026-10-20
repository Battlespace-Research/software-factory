"""The merge gate: what must hold before the owner merges a piece of work.

``run_gate`` runs six checks, always all of them so one report shows everything that is wrong:

1. ``base-merged``: the base branch is already merged into the branch being merged.
2. ``reviewed-commit``: HEAD is the commit the review panel passed (or later commits changed nothing but
   factory bookkeeping under ``factory/work/``), and no tracked file has uncommitted changes.
3. ``size-cap``: the change is under the project's line cap. Generated files, binaries and everything under
   ``factory/work/`` (the factory's own specs, plans and notes, not product code) do not count.
4. ``id-unused-on-base``: no work folder on the base uses this work id (two branches took the same id). The
   item's own folder, ``<ID>-<slug>`` exactly, is not counted: a Program merges once per milestone.
5. ``verify-full``: the project's full verify command passes.
6. ``spot-check``: someone checked one acceptance criterion by hand and said so.

``write_gate_report`` writes the result to ``merge-gate.md`` in the work folder; its first line is
``GATE: PASS`` or ``GATE: FAIL``. The gate only reads the repository and runs the verify command. It never
fetches: the base is read from ``origin/<base>`` when that ref exists (the skill fetches before it runs the
gate) and from the local ``<base>`` otherwise.
"""
from __future__ import annotations

import contextlib
import fnmatch
import os
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from swfactory.config import ProjectConfig
from swfactory.gitutil import GitError, ref_exists, run_git
from swfactory.workitems import WorkItem, ids_in_use, with_letter_suffix

__all__ = ["GATE_FILE", "GateCheck", "GateReport", "render_gate_report", "run_gate", "write_gate_report"]

GATE_FILE = "merge-gate.md"

_REMOTE = "origin"
_VERIFY_TAIL_LINES = 40
_LISTED_PATHS = 5  # paths a detail names before it says "and N more"
_WORK_ROOT = "factory/work/"  # where the factory keeps work folders; git paths always use "/"
_DASH = "—"  # between a check's result and its detail in the report


@dataclass(frozen=True)
class GateCheck:
    """One check: its name, whether it held, and a detail (the reason, or what was measured). A detail may
    run over several lines."""

    name: str
    ok: bool
    detail: str


@dataclass
class GateReport:
    """The checks in the order they ran, and whether every one held."""

    checks: list[GateCheck]
    ok: bool


# --------------------------------------------------------------------------- the base branch

@dataclass(frozen=True)
class _BaseRef:
    """A ref that stands for the base branch: the full ref name for git, and the short name for people."""

    full: str
    label: str


def _base_refs(repo: Path, base: str) -> list[_BaseRef]:
    """The refs of the base branch that exist, the remote copy first and then the local branch."""
    candidates = (
        _BaseRef(f"refs/remotes/{_REMOTE}/{base}", f"{_REMOTE}/{base}"),
        _BaseRef(f"refs/heads/{base}", base),
    )
    return [ref for ref in candidates if ref_exists(repo, ref.full)]


def _missing_base(base: str) -> tuple[bool, str]:
    return False, (
        f"the base branch '{base}' was not found (no {_REMOTE}/{base} and no local {base}); "
        "fetch it, or fix base_branch in factory.yaml"
    )


# --------------------------------------------------------------------------- the checks (ok, detail)

def _base_merged(repo: Path, base: str, refs: list[_BaseRef]) -> tuple[bool, str]:
    if not refs:
        return _missing_base(base)
    ref = refs[0]  # origin/<base> when there is one; the skill fetched it just before
    # The base is merged when it is an ancestor of HEAD, that is when no commit is reachable from the base
    # but not from HEAD. That is what ``git merge-base --is-ancestor`` tests; counting the commits (rather
    # than reading an exit status) also says how far behind the branch is.
    behind = int(run_git(repo, "rev-list", "--count", f"HEAD..{ref.full}"))
    if behind:
        plural = "commit" if behind == 1 else "commits"
        return False, f"{ref.label} has {behind} {plural} that HEAD does not contain; merge {ref.label} in first"
    return True, f"HEAD contains {ref.label}"


def _modified_tracked_files(repo: Path) -> list[str]:
    """The tracked files with changes that are not committed (edited, staged, deleted, renamed); untracked
    files are not listed. A renamed or copied file is one entry, under its new path."""
    # ``--no-optional-locks`` keeps this read-only: a plain ``git status`` may refresh the index.
    status = run_git(repo, "--no-optional-locks", "status", "--porcelain", "-z", "--untracked-files=no")
    fields = status.split("\0")
    paths = []
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if len(entry) < 4:  # "XY <path>"; the empty field after the last NUL has none
            continue
        paths.append(entry[3:])
        if "R" in entry[:2] or "C" in entry[:2]:  # the old path follows as a field of its own
            index += 1
    return paths


def _name_paths(paths: list[str]) -> str:
    """The first few paths for a detail, then how many more there are."""
    listed = ", ".join(paths[:_LISTED_PATHS])
    return f"{listed} and {len(paths) - _LISTED_PATHS} more" if len(paths) > _LISTED_PATHS else listed


def _paths_changed_since(repo: Path, reviewed: str) -> list[str]:
    """Every path that differs between the commit ``reviewed`` and HEAD. ``--no-renames`` lists both the old and
    the new path of a move, so a product file moved into ``factory/work/`` still shows up under its old name."""
    listing = run_git(repo, "diff", "--no-renames", "--name-only", "-z", f"{reviewed}..HEAD")
    return [path for path in listing.split("\0") if path]


def _since_the_review(repo: Path, head: str, reviewed: str) -> tuple[str | None, str | None]:
    """How HEAD relates to the reviewed commit: ``(problem, None)`` or ``(None, what to say when it is fine)``.

    Fine when they are the same commit, or when the reviewed commit is an ancestor of HEAD and the commits
    since changed only paths under ``factory/work/``: the factory commits its bookkeeping (panel summaries, the
    tester report, progress notes, approvals, this gate's report) after a review has passed, and none of that
    is product code the panel would have to look at again.
    """
    if head == reviewed:
        return None, f"HEAD {head[:12]} is the commit the panel passed"
    if int(run_git(repo, "rev-list", "--count", f"HEAD..{reviewed}")) != 0:  # the reviewed commit has commits HEAD lacks
        return (
            f"HEAD is {head[:12]} but the panel passed {reviewed[:12]}, which is not an ancestor of HEAD; "
            "run the panel again on HEAD",
            None,
        )
    later = int(run_git(repo, "rev-list", "--count", f"{reviewed}..HEAD"))
    outside = [path for path in _paths_changed_since(repo, reviewed) if not path.startswith(_WORK_ROOT)]
    if outside:
        return (
            f"HEAD is {head[:12]}, {later} commit(s) after the reviewed commit {reviewed[:12]}, and they changed "
            f"files outside {_WORK_ROOT}: {_name_paths(outside)}; run the panel again on HEAD",
            None,
        )
    return None, (
        f"HEAD is {later} commit(s) after the reviewed commit; later changes are factory bookkeeping only "
        f"(reviewed {reviewed[:12]}, HEAD {head[:12]})"
    )


def _reviewed_commit(repo: Path, reviewed_commit: str) -> tuple[bool, str]:
    """HEAD is the commit the panel passed (see ``_since_the_review``), and no tracked file differs from HEAD:
    the full verify runs on the working tree, so an uncommitted change would let it pass on something that is
    not in the merged commit. Both kinds of failure are reported together."""
    head = run_git(repo, "rev-parse", "--verify", "HEAD^{commit}")
    problems = []
    fine = None
    wanted = reviewed_commit.strip()
    if not wanted or wanted.startswith("-"):
        problems.append(f"{reviewed_commit!r} does not name the commit the panel passed")
    else:
        try:
            reviewed = run_git(repo, "rev-parse", "--verify", "--quiet", f"{wanted}^{{commit}}")
        except GitError:
            problems.append(f"the reviewed commit {wanted} is not a commit in this repository")
        else:
            problem, fine = _since_the_review(repo, head, reviewed)
            if problem:
                problems.append(problem)
    modified = _modified_tracked_files(repo)
    if modified:
        problems.append(f"uncommitted changes in {len(modified)} tracked file(s): {_name_paths(modified)}")
    if problems:
        return False, "; ".join(problems)
    return True, f"{fine}; no uncommitted changes in tracked files"


def _changed_lines(numstat: str, generated: list[str]) -> int:
    """Lines added plus lines deleted in the output of ``git diff --numstat -z``, leaving out binary files,
    everything under ``factory/work/`` (the factory's own artifacts are not product code, so the size cap
    does not count them) and paths that match a pattern in ``generated`` (``fnmatch`` globs; ``*`` also
    crosses ``/``).

    With ``-z`` a record is ``<added> TAB <deleted> TAB <path> NUL``, or for a rename or copy
    ``<added> TAB <deleted> TAB NUL <old path> NUL <new path> NUL``; the new path is the one that is matched.
    A binary file has ``-`` for both counts.
    """
    fields = numstat.split("\0")
    total = 0
    index = 0
    while index < len(fields):
        record = fields[index]
        index += 1
        if not record:
            continue
        try:
            added, deleted, path = record.split("\t", 2)
            if not path:  # a rename or copy: the old and the new path are the next two fields
                path = fields[index + 1]
                index += 2
            lines = None if added == "-" and deleted == "-" else int(added) + int(deleted)
        except (ValueError, IndexError):
            raise GitError(f"cannot read the output of git diff --numstat: unexpected record {record!r}") from None
        left_out = path.startswith(_WORK_ROOT) or any(fnmatch.fnmatchcase(path, pattern) for pattern in generated)
        if lines is not None and not left_out:
            total += lines
    return total


def _size_cap(repo: Path, cfg: ProjectConfig, refs: list[_BaseRef]) -> tuple[bool, str]:
    if not refs:
        return _missing_base(cfg.base_branch)
    # ``base...HEAD`` is the change since the two diverged, so what the base gained since is not counted.
    # ``-M`` makes a moved file cost only its edits whatever the user's ``diff.renames`` says.
    numstat = run_git(repo, "diff", "--numstat", "-z", "-M", f"{refs[0].full}...HEAD")
    lines = _changed_lines(numstat, cfg.generated)
    measured = f"{lines} lines (cap {cfg.size_cap})"
    if lines <= cfg.size_cap:
        return True, measured
    return False, f"{measured}; split the work"


def _work_folders(repo: Path, ref: str) -> list[str]:
    """The names of the folders directly inside ``factory/work/`` in the tree of ``ref``."""
    listing = run_git(repo, "ls-tree", "-z", ref, "--", _WORK_ROOT)
    names = []
    for entry in listing.split("\0"):
        meta, _, path = entry.partition("\t")  # "<mode> <type> <object>" TAB "<path>"
        if meta.split()[1:2] == ["tree"]:
            names.append(path.rsplit("/", 1)[-1])
    return names


def _id_unused(repo: Path, cfg: ProjectConfig, item: WorkItem, refs: list[_BaseRef]) -> tuple[bool, str]:
    if not refs:
        return _missing_base(cfg.base_branch)
    prefix = f"{item.id}-"  # the dash keeps FEA-1 apart from FEA-12 and FEA-1b
    # This item's own folder, already merged by an earlier merge of the same item (a Program merges once per
    # milestone), is not a clash; any other folder with the id is, whatever its slug.
    own = f"{item.id}-{item.slug}"
    found: dict[str, str] = {}  # folder name -> the first base ref it was seen on
    for ref in refs:  # the remote copy and the local branch: either one holding the id is a clash
        for folder in _work_folders(repo, ref.full):
            if folder.startswith(prefix) and folder != own:
                found.setdefault(folder, ref.label)
    if not found:
        return True, f"no other factory/work/{item.id}-* folder than {own} on {' or '.join(ref.label for ref in refs)}"
    taken = ", ".join(f"factory/work/{folder} on {label}" for folder, label in found.items())
    try:
        suggestion = f"; the next free id is {with_letter_suffix(item.id, ids_in_use(repo, cfg.base_branch))}"
    except (ValueError, GitError):
        suggestion = ""
    return False, f"{item.id} is already used by {taken}{suggestion}"


def _verify_full(repo: Path, cfg: ProjectConfig, run_verify: bool, builds_approved: bool) -> tuple[bool, str]:
    if not run_verify:
        return True, "skipped"
    if cfg.builds == "ask" and not builds_approved:
        return False, "ask the owner before the full build"
    try:
        done = subprocess.run(cfg.verify_full, shell=True, cwd=repo, stdin=subprocess.DEVNULL, capture_output=True)
    except OSError as err:
        return False, f"cannot run the verify command {cfg.verify_full!r}: {err}"
    output = (done.stdout + done.stderr).decode("utf-8", errors="replace")
    lines = [line.rstrip() for line in output.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    detail = "\n".join(lines[-_VERIFY_TAIL_LINES:]) if lines else f"exit {done.returncode}, no output"
    return done.returncode == 0, detail


def _spot_check(spot_check: str) -> tuple[bool, str]:
    text = spot_check.strip()
    if text:
        return True, text
    return False, "no acceptance criterion was spot-checked; say which one you checked and what you saw"


def _run_check(name: str, check: Callable[[], tuple[bool, str]]) -> GateCheck:
    """Run one check. A git problem fails that check, with git's message as the detail, instead of stopping the gate."""
    try:
        ok, detail = check()
    except GitError as err:
        return GateCheck(name, False, f"git failed: {err}")
    return GateCheck(name, ok, detail)


def run_gate(
    repo: Path,
    cfg: ProjectConfig,
    item: WorkItem,
    reviewed_commit: str,
    spot_check: str,
    run_verify: bool = True,
    builds_approved: bool = False,
) -> GateReport:
    """Run every check for ``item`` in the project at ``repo`` (its root, with the branch to merge checked
    out) and return the report; a check that fails does not stop the ones after it.

    ``reviewed_commit`` is the commit the panel passed. ``run_verify=False`` skips the full verify command.
    With ``builds: ask`` in the project settings the command only runs when ``builds_approved`` is true.
    A problem with the repository fails the check that hit it, with the reason as its detail.
    """
    repo = Path(repo)
    refs = _base_refs(repo, cfg.base_branch)
    checks = [
        _run_check("base-merged", lambda: _base_merged(repo, cfg.base_branch, refs)),
        _run_check("reviewed-commit", lambda: _reviewed_commit(repo, reviewed_commit)),
        _run_check("size-cap", lambda: _size_cap(repo, cfg, refs)),
        _run_check("id-unused-on-base", lambda: _id_unused(repo, cfg, item, refs)),
        _run_check("verify-full", lambda: _verify_full(repo, cfg, run_verify, builds_approved)),
        _run_check("spot-check", lambda: _spot_check(spot_check)),
    ]
    return GateReport(checks, all(check.ok for check in checks))


# --------------------------------------------------------------------------- the report file

def render_gate_report(report: GateReport) -> str:
    """The text of ``merge-gate.md``: ``GATE: PASS`` or ``GATE: FAIL``, then a line per check
    (``- <name>: ok|FAIL — <detail>``) with the rest of a multi-line detail indented under it. It says PASS only
    if the report is ok and every check in it held."""
    passed = report.ok and all(check.ok for check in report.checks)
    lines = ["GATE: PASS" if passed else "GATE: FAIL"]
    for check in report.checks:
        first, *more = check.detail.splitlines() or [""]
        head = f"- {check.name}: {'ok' if check.ok else 'FAIL'}"
        lines.append(f"{head} {_DASH} {first.rstrip()}" if first.strip() else head)
        lines.extend(f"  {line}".rstrip() for line in more)
    return "\n".join(lines) + "\n"


def write_gate_report(work_dir: Path, report: GateReport) -> Path:
    """Write ``merge-gate.md`` in ``work_dir`` (UTF-8, LF line endings, replaced in one step so a reader never
    sees half a report) and return its path. Raises ``OSError`` if it cannot be written."""
    path = Path(work_dir) / GATE_FILE
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temp, "wb") as handle:
            handle.write(render_gate_report(report).encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp)
        raise
    return path
