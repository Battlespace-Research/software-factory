"""Work items: ids, branches, folders and the Status line of ``progress.md`` (design section 8).

A piece of work gets an id (``FIX-1``, ``FEA-12``, ``PRG-3``), numbered per track and never reused, a branch
``factory/<ID>-<slug>`` and a folder ``factory/work/<ID>-<slug>/`` of template files. The Status line on the
first line of its ``progress.md`` is the resume point for any session that picks the work up.
"""
from __future__ import annotations

import re
import shutil
import string
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from swfactory.config import ProjectConfig, factory_home
from swfactory.gitutil import GitError, current_branch, ref_exists, run_git
from swfactory.tracks import TRACK_PREFIX, track_for_id
from swfactory.trailers import work_trailer

__all__ = [
    "GitError",
    "TRACK_PREFIX",
    "Status",
    "WorkItem",
    "create_work_item",
    "find_work_item",
    "ids_in_use",
    "next_id",
    "read_status",
    "run_git",
    "with_letter_suffix",
    "write_status",
]

BRANCH_PREFIX = "factory/"
_WORK_ROOT = ("factory", "work")  # the folder of work folders, below the project root
_WORK_ROOT_GIT = "/".join(_WORK_ROOT) + "/"

# A work id is ``<PREFIX>-<number>`` with an optional letter for a collision (``FEA-12b``); a work folder or
# branch name continues with ``-<slug>``. The ``-`` after the number or letter keeps the two apart.
_NAME = re.compile(
    rf"(?P<id>(?P<prefix>{'|'.join(sorted(TRACK_PREFIX.values()))})-(?P<number>[0-9]+)(?P<letter>[a-z])?)"
    r"(?:-(?P<slug>.+))?"
)
_SLUG = re.compile(r"[a-z0-9-]{1,40}")

# ``refs/heads/factory/<name>`` and ``refs/remotes/<remote>/factory/<name>``.
_FACTORY_REF = re.compile(rf"refs/(?:heads|remotes/[^/]+)/{re.escape(BRANCH_PREFIX)}(?P<name>.+)")

_PLACEHOLDER = re.compile(r"\{\{(id|title|track|date)\}\}")

_SEP = " · "  # between the fields of the Status line
_DASH = "—"  # between a log entry's time and its note
_STATUS_LINE = re.compile(
    rf"Status: (?P<stage>.+?){_SEP}(?P<state>.+?){_SEP}next: (?P<next>.*){_SEP}updated (?P<updated>\S+)"
)
_LOG_HEADING = "## Log"


def _prefix_for(track: str) -> str:
    prefix = TRACK_PREFIX.get(track)
    if prefix is None:
        raise ValueError(f"unknown track {track!r} (expected one of {', '.join(TRACK_PREFIX)})")
    return prefix


def _is_single_line(value: str) -> bool:
    return len(value.splitlines()) == 1


# --------------------------------------------------------------------------- ids

@dataclass(frozen=True)
class _Ref:
    """A local or remote-tracking branch: its full name, the commit it points at, and whether it is checked out."""

    name: str
    commit: str
    checked_out: bool


def _refs(repo: Path) -> list[_Ref]:
    listing = run_git(repo, "for-each-ref", "--format=%(refname)\t%(objectname)\t%(HEAD)", "refs/heads", "refs/remotes")
    refs = []
    for line in listing.splitlines():
        name, commit, head = line.split("\t")
        refs.append(_Ref(name, commit, head.strip() == "*"))
    return refs


def _work_folder_names(repo: Path, tree: str) -> list[str]:
    """The names of the entries of ``factory/work/`` in the tree of ``tree`` (none if there is no such folder)."""
    listing = run_git(repo, "ls-tree", "-z", "--name-only", tree, "--", _WORK_ROOT_GIT)
    return [entry.rsplit("/", 1)[-1] for entry in listing.split("\0") if entry]


def ids_in_use(repo: Path, base: str) -> set[str]:
    """Every work id already taken in ``repo``.

    Read from the ``factory/work/*`` folders in the trees of the base branch (and its copy on each remote)
    and of the checked-out commit (with single branching the items live on the work branch), and from the
    names of all local and remote ``factory/*`` branches. Anything else is ignored. A missing base branch,
    remote, commit or ``factory/work`` folder just contributes nothing.
    """
    refs = _refs(repo)
    remote_base = re.compile(rf"refs/remotes/[^/]+/{re.escape(base)}")
    names: list[str] = []
    to_read: dict[str, str] = {}  # commit -> a ref to read its tree from; one read per distinct commit
    for ref in refs:
        branch = _FACTORY_REF.fullmatch(ref.name)
        if branch:
            names.append(branch["name"])
        if ref.name == f"refs/heads/{base}" or remote_base.fullmatch(ref.name) or ref.checked_out:
            to_read.setdefault(ref.commit, ref.name)
    if not any(ref.checked_out for ref in refs):  # a detached HEAD (or none yet: an empty repo)
        try:
            to_read.setdefault(run_git(repo, "rev-parse", "--verify", "--quiet", "HEAD"), "HEAD")
        except GitError:
            pass
    for tree in to_read.values():
        names.extend(_work_folder_names(repo, tree))
    return _ids_in(names)


def _ids_in(names: Iterable[str]) -> set[str]:
    return {match["id"] for match in map(_NAME.fullmatch, names) if match}


def next_id(repo: Path, track: str, base: str) -> str:
    """The next id of ``track``: one more than the highest number in use for it, or ``<PREFIX>-1`` when none.

    A lettered id (``FEA-12b``) counts as its number. Tracks are numbered separately.
    """
    prefix = _prefix_for(track)
    numbers = [int(m["number"]) for m in map(_NAME.fullmatch, ids_in_use(repo, base)) if m and m["prefix"] == prefix]
    return f"{prefix}-{max(numbers, default=0) + 1}"


def with_letter_suffix(id_: str, taken: set[str]) -> str:
    """``id_`` with the first free letter after its number: ``FEA-12`` becomes ``FEA-12b``, then ``FEA-12c``.

    Used when an id turns out to be taken on the base branch at merge. Raises ``ValueError`` for anything that
    is not a work id, or when ``b`` to ``z`` are all taken.
    """
    match = _NAME.fullmatch(id_)
    if match is None or match["slug"] is not None:
        raise ValueError(f"{id_!r} is not a work id such as FEA-12")
    stem = f"{match['prefix']}-{match['number']}"
    for letter in string.ascii_lowercase[1:]:
        if stem + letter not in taken:
            return stem + letter
    raise ValueError(f"every letter suffix b to z of {stem} is already taken")


# --------------------------------------------------------------------------- work items

@dataclass
class WorkItem:
    """A work item: ``branch`` is None when the project works on one branch for every item."""

    id: str
    track: str
    slug: str
    title: str
    branch: str | None
    path: Path


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _copy_templates(source: Path, target: Path, values: dict[str, str]) -> None:
    """Copy the template tree into ``target``, filling ``{{id}}``, ``{{title}}``, ``{{track}}`` and ``{{date}}`` in
    Markdown files in one pass (so a title that looks like a placeholder stays as typed). LF line endings."""
    target.mkdir(parents=True)
    for item in sorted(source.rglob("*")):
        destination = target / item.relative_to(source)
        if item.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif item.suffix == ".md":
            text = _PLACEHOLDER.sub(lambda m: values[m.group(1)], item.read_text(encoding="utf-8-sig"))
            destination.write_text(text, encoding="utf-8", newline="\n")
        else:
            shutil.copyfile(item, destination)


def _abandon(repo: Path, folder: Path, branch: str | None, return_to: str, detached: bool) -> None:
    """Best effort: undo a ``create_work_item`` that failed after it began changing the repository, so no empty
    branch is left to take up an id and no half-made folder is left behind."""
    try:
        run_git(repo, "reset", "-q", "--", folder.relative_to(repo).as_posix())
    except GitError:
        pass
    shutil.rmtree(folder, ignore_errors=True)
    for parent in (folder.parent, folder.parent.parent):  # factory/work, then factory, only if now empty
        try:
            parent.rmdir()
        except OSError:
            break
    if branch is not None:
        back = ["switch", "-q", "--detach", return_to] if detached else ["switch", "-q", return_to]
        try:
            run_git(repo, *back)
            run_git(repo, "branch", "-q", "-D", branch)
        except GitError:
            pass


def create_work_item(repo: Path, cfg: ProjectConfig, track: str, slug: str, title: str) -> WorkItem:
    """Open a work item: allocate its id, give it a branch, copy its track's templates and commit them.

    With per-item branching the branch ``factory/<ID>-<slug>`` is cut from the base branch and checked out.
    With single branching the repo must already be on the project's work branch, which gets the commit.
    The commit is ``<ID>: open work item`` with a ``Factory-Work: <ID>`` trailer, and holds only the new folder.

    Raises ``ValueError`` for a bad track, slug (``a-z``, ``0-9`` and ``-``, 1 to 40 characters) or title,
    ``FileExistsError`` if the folder is already there, and ``GitError`` for a git problem. A failure after
    the repository was touched is rolled back.
    """
    _prefix_for(track)  # validates the track
    if not _SLUG.fullmatch(slug):
        raise ValueError(f"slug {slug!r} is not allowed: use 1 to 40 characters from a-z, 0-9 and -")
    title = title.strip()
    if not title or not _is_single_line(title):
        raise ValueError("title must be one non-empty line of text")
    repo = Path(repo)
    templates = factory_home() / "templates" / "work" / track
    if not templates.is_dir():
        raise FileNotFoundError(f"no templates for the {track} track: {templates} is missing")

    per_item = cfg.branching == "per-item"
    start = current_branch(repo)
    if not per_item and start != cfg.work_branch:
        where = "detached HEAD" if start is None else f"branch '{start}'"
        raise GitError(
            f"cannot open a work item on {where}: branching is 'single', so switch to the work branch "
            f"'{cfg.work_branch}' first"
        )

    id_ = next_id(repo, track, cfg.base_branch)
    name = f"{id_}-{slug}"
    folder = repo.joinpath(*_WORK_ROOT, name)
    if folder.exists():
        raise FileExistsError(f"{folder} already exists; remove it or pick another slug")
    branch = f"{BRANCH_PREFIX}{name}" if per_item else None

    return_to = start or run_git(repo, "rev-parse", "HEAD")  # where a failed attempt returns: a branch, or a commit
    if branch is not None:
        run_git(repo, "switch", "-q", "-c", branch, cfg.base_branch)
    try:
        _copy_templates(templates, folder, {"id": id_, "title": title, "track": track, "date": _today()})
        relative = folder.relative_to(repo).as_posix()
        run_git(repo, "add", "--", relative)
        run_git(repo, "commit", "-q", "-m", f"{id_}: open work item", "-m", work_trailer(id_), "--", relative)
    except BaseException:
        _abandon(repo, folder, branch, return_to, detached=start is None)
        raise
    return WorkItem(id=id_, track=track, slug=slug, title=title, branch=branch, path=folder)


def _read_title(folder: Path, id_: str, slug: str) -> str:
    """The title from the heading ``# <ID>: <title>`` of the folder's ticket or intent; the slug if there is none."""
    for name in ("ticket.md", "intent.md"):
        try:
            first = (folder / name).read_text(encoding="utf-8-sig").splitlines()[0]
        except (OSError, IndexError):
            continue
        match = re.fullmatch(rf"#\s+{re.escape(id_)}:\s*(.+?)\s*", first)
        if match:
            return match.group(1)
    return slug


def _branches_of(repo: Path, id_: str) -> list[str]:
    """Local and remote ``factory/`` branch names (without the remote) that belong to ``id_``."""
    try:
        refs = _refs(repo)
    except GitError:
        return []
    found: dict[str, None] = {}
    for ref in refs:
        branch = _FACTORY_REF.fullmatch(ref.name)
        match = _NAME.fullmatch(branch["name"]) if branch else None
        if match and match["id"] == id_:
            found[BRANCH_PREFIX + branch["name"]] = None
    return list(found)


def find_work_item(repo: Path, id_: str) -> WorkItem:
    """The work item ``id_`` (``FIX-3``, or its folder name) in the checked-out tree of ``repo``.

    Raises ``ValueError`` if ``id_`` is not an id, and ``FileNotFoundError`` if the checkout has no folder for
    it; the message names the branch that holds it when there is one.
    """
    match = _NAME.fullmatch(id_)
    if match is None:
        raise ValueError(f"{id_!r} is not a work id such as FIX-3 or FIX-3-licence-crash")
    wanted = match["id"]
    repo = Path(repo)
    root = repo.joinpath(*_WORK_ROOT)
    folders: list[tuple[Path, re.Match[str]]] = []
    if root.is_dir():
        for folder in sorted(root.iterdir()):
            parsed = _NAME.fullmatch(folder.name)
            if parsed and parsed["slug"] and parsed["id"] == wanted and folder.is_dir():
                folders.append((folder, parsed))
    if not folders:
        message = f"no work folder for {wanted} under {root}"
        branches = _branches_of(repo, wanted)
        if branches:
            message += f"; it is on {', '.join(branches)}, so check that branch out first"
        raise FileNotFoundError(message)
    if len(folders) > 1:
        raise ValueError(f"{wanted} has more than one work folder: {', '.join(f.name for f, _ in folders)}")
    folder, parsed = folders[0]
    local_branch = f"{BRANCH_PREFIX}{folder.name}"
    return WorkItem(
        id=wanted,
        track=track_for_id(wanted),
        slug=parsed["slug"],
        title=_read_title(folder, wanted, parsed["slug"]),
        branch=local_branch if ref_exists(repo, f"refs/heads/{local_branch}") else None,
        path=folder,
    )


# --------------------------------------------------------------------------- status

@dataclass
class Status:
    """The Status line of ``progress.md``: where the work is, and what the next session should do."""

    stage: str
    state: str
    next: str
    updated: str


def _status_line(status: Status) -> str:
    """The Status line as written to ``progress.md``: stage, state, next and updated, joined by a middle dot."""
    return f"Status: {status.stage}{_SEP}{status.state}{_SEP}next: {status.next}{_SEP}updated {status.updated}"


def _progress_lines(work_dir: Path) -> tuple[Path, list[str], re.Match[str]]:
    """The path of ``progress.md``, its lines, and the match of its Status line (the first line must be one)."""
    path = Path(work_dir) / "progress.md"
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise FileNotFoundError(f"{path} not found") from None
    lines = text.split("\n")
    status = _STATUS_LINE.fullmatch(lines[0].rstrip())
    if status is None:
        raise ValueError(
            f"{path}: line 1 is not a Status line "
            f"(expected 'Status: <stage>{_SEP}<state>{_SEP}next: <next>{_SEP}updated <time>')"
        )
    return path, lines, status


def read_status(work_dir: Path) -> Status:
    """The Status line of the work folder's ``progress.md``."""
    _, _, match = _progress_lines(work_dir)
    return Status(match["stage"], match["state"], match["next"], match["updated"])


def _log_heading_index(lines: list[str]) -> int | None:
    return next((index for index, line in enumerate(lines) if line.strip() == _LOG_HEADING), None)


def _clean(name: str, value: str, *, allow_separator: bool = True) -> str:
    value = value.strip()
    if not value or not _is_single_line(value):
        raise ValueError(f"{name} must be one non-empty line of text")
    if not allow_separator and "·" in value:
        raise ValueError(f"{name} must not contain the separator · (got {value!r})")
    return value


def write_status(work_dir: Path, stage: str, state: str, next: str, note: str) -> Status:  # noqa: A002
    """Rewrite line 1 of ``progress.md`` as the Status line and add ``note`` as the newest entry under ``## Log``.

    ``stage`` and ``state`` must not contain the separator ``·``; ``next`` may. ``note`` may run over several
    lines. The ``updated`` time is now, in UTC. Raises ``ValueError`` (and leaves the file as it was) for a field
    that would break the line or when line 1 of the file is not a Status line.
    """
    stage = _clean("stage", stage, allow_separator=False)
    state = _clean("state", state, allow_separator=False)
    next = _clean("next", next)  # noqa: A001
    note = note.strip()
    if not note:
        raise ValueError("note must not be empty")
    path, lines, _ = _progress_lines(work_dir)

    stamp = _now()
    status = Status(stage, state, next, stamp)
    first, *more = note.splitlines()
    entry = [f"- {stamp} {_DASH} {first.rstrip()}", *(f"  {line}".rstrip() for line in more)]

    heading = _log_heading_index(lines)
    if heading is None:
        while lines and not lines[-1].strip():
            lines.pop()
        head, rest = [*lines, "", _LOG_HEADING], []
    else:
        head, rest = lines[: heading + 1], lines[heading + 1 :]
        while rest and not rest[0].strip():
            rest.pop(0)
    head[0] = _status_line(status)
    text = "\n".join([*head, "", *entry, *rest])
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8", newline="\n")
    return status
