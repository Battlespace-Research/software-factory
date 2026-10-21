"""Project setup: scaffold a project for the factory, and find the project's own rules that conflict with factory defaults.

``setup_project`` writes ``sw-factory.yaml`` and ``REVIEW.md`` from ``templates/project/``, creates the ``sw-factory/``
folders, ignores the generated dashboard, and adds the factory's marked section to ``CLAUDE.md`` and ``AGENTS.md``.
It never runs git and never commits: the owner reviews the result first. Everything that can refuse is checked
before the first file is written, and a write that fails is undone, so setup either finishes or leaves the project
as it was (``sw-factory.yaml``, whose presence means "already set up", is written last).
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from swfactory.config import PROJECT_CONFIG, PROJECT_FOLDER, ConfigError, factory_home, load_project_config

BEGIN_MARKER = "<!-- sw-factory:begin -->"
END_MARKER = "<!-- sw-factory:end -->"
GITIGNORE_ENTRY = f"{PROJECT_FOLDER}/dashboard/"

_BEGIN = BEGIN_MARKER.encode("utf-8")
_END = END_MARKER.encode("utf-8")
# Ways a .gitignore line can already ignore the generated dashboard.
_IGNORES_DASHBOARD = {f"{lead}{PROJECT_FOLDER}/dashboard{trail}" for lead in ("", "/") for trail in ("", "/")}
_SECTION_FILES = ("CLAUDE.md", "AGENTS.md")  # the factory section goes into each that exists
_CONFLICT_FILES = ("AGENTS.md", "CLAUDE.md")  # the project's own rules are read from these, in this order
_CONFLICT_RULES = re.compile(
    r"do not create (git )?branches|do not build|commit (directly )?to main|never push", re.IGNORECASE
)
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


class SetupError(RuntimeError):
    """Setup refuses to go ahead, or a write failed; the message says why. The project is as it was (a message that
    says setup could not undo something names it)."""


@dataclass
class SetupAnswers:
    """What the owner decides at setup. ``work_branch`` is used only when ``branching`` is ``single``."""

    project: str
    verify_quick: str
    verify_full: str
    approvers: list[str]
    base_branch: str = "main"
    branching: str = "per-item"
    work_branch: str | None = None
    builds: str = "allow"
    mode: str = "review"


@dataclass
class SetupResult:
    """The files setup made (``created``) and changed (``updated``), and the project's own rules that conflict with
    factory defaults (``conflicts``, each as ``<file>:<line>: <text>``)."""

    created: list[Path]
    updated: list[Path]
    conflicts: list[str]


# --------------------------------------------------------------------------- templates and values

def _template(name: str) -> str:
    """The text of ``templates/project/<name>`` in the factory checkout, with LF line endings."""
    path = factory_home() / "templates" / "project" / name
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as err:
        raise SetupError(f"cannot read the template {path}: {err}") from None


def _fill(text: str, values: dict[str, str]) -> str:
    """Replace each ``{{name}}`` with its value in one pass, so a value that looks like a placeholder stays as typed."""
    return _PLACEHOLDER.sub(lambda match: values[match.group(1)], text)


def _quote(value: str) -> str:
    """``value`` as a double-quoted YAML scalar. PyYAML escapes quotes, backslashes, control characters and line
    breaks, and never wraps the line, so any text the owner types stays one safe value."""
    return yaml.safe_dump(value, default_style='"', allow_unicode=True, width=sys.maxsize).rstrip("\n")


def _code_span(text: str) -> str:
    """``text`` as a Markdown inline code span that holds however many backticks it contains."""
    text = re.sub(r"\s*[\r\n]+\s*", " ", text)
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _render_sw_factory_yaml(answers: SetupAnswers) -> str:
    """The project's ``sw-factory.yaml``; raises ``SetupError`` listing every problem when the answers are not valid."""
    work_branch = answers.work_branch if answers.branching == "single" else None
    text = _fill(_template(PROJECT_CONFIG), {
        "project": _quote(answers.project),
        "base_branch": _quote(answers.base_branch),
        "branching": _quote(answers.branching),
        "work_branch": "null" if work_branch is None else _quote(work_branch),
        "mode": _quote(answers.mode),
        "approvers": "[" + ", ".join(_quote(name) for name in answers.approvers) + "]",
        "verify_quick": _quote(answers.verify_quick),
        "verify_full": _quote(answers.verify_full),
        "builds": _quote(answers.builds),
    })
    with tempfile.TemporaryDirectory() as scratch:
        probe = Path(scratch) / PROJECT_CONFIG
        probe.write_text(text, encoding="utf-8", newline="\n")
        try:
            load_project_config(probe)
        except ConfigError as err:
            raise SetupError(f"the answers do not make a valid {PROJECT_CONFIG}:\n" + "\n".join(err.problems)) from None
    return text


# --------------------------------------------------------------------------- edits to existing files

def _newline_of(data: bytes) -> bytes:
    """The line ending ``data`` mostly uses (LF when it has none or they tie)."""
    crlf = data.count(b"\r\n")
    return b"\r\n" if crlf > data.count(b"\n") - crlf else b"\n"


def _with_section(name: str, data: bytes, section: str) -> bytes:
    """``data`` (the bytes of a ``CLAUDE.md`` or ``AGENTS.md``) with the factory section in place of the old one, or
    added at the end when there is none. Everything outside the markers keeps its bytes; the section takes the
    file's own line ending. Raises ``SetupError`` when the markers are not one begin line followed by one end line."""
    eol = _newline_of(data)
    block = eol.join(line.encode("utf-8") for line in (BEGIN_MARKER, *section.split("\n"), END_MARKER))
    begins, ends = data.count(_BEGIN), data.count(_END)
    if begins == 0 and ends == 0:
        text = data if data.endswith(b"\n") or not data else data + eol
        if text and not text.endswith(eol * 2):
            text += eol
        return text + block + eol
    first, last = data.find(_BEGIN), data.find(_END)
    if begins != 1 or ends != 1 or last < first:
        raise SetupError(
            f"{name} has a factory section setup cannot replace safely: it needs exactly one {BEGIN_MARKER} "
            f"followed by one {END_MARKER} (fix or remove the markers, then run setup again)"
        )
    return data[:first] + block + data[last + len(_END):]


def _with_ignore_entry(data: bytes) -> bytes | None:
    """``data`` (the bytes of a ``.gitignore``) with the dashboard folder added, or ``None`` when it is already ignored."""
    if any(line.strip() in _IGNORES_DASHBOARD for line in data.decode("utf-8", errors="replace").splitlines()):
        return None
    eol = _newline_of(data)
    text = data if data.endswith(b"\n") or not data else data + eol
    return text + GITIGNORE_ENTRY.encode("utf-8") + eol


def _write_atomically(path: Path, data: bytes) -> None:
    """Replace the existing file ``path`` with ``data`` all at once: the bytes go to a temporary file beside it and
    that file takes its place, so a failure part way cannot truncate the original. The file keeps its permissions,
    and a symbolic link is followed (the file it points to is replaced, the link stays)."""
    target = Path(os.path.realpath(path))
    descriptor, temporary = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=f".{PROJECT_FOLDER}-tmp")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        shutil.copymode(target, temporary)
        os.replace(temporary, target)
    except BaseException:
        try:
            os.chmod(temporary, stat.S_IREAD | stat.S_IWRITE)  # Windows will not delete a read-only copy of a read-only file
            os.unlink(temporary)
        except OSError:
            pass  # best effort: the error being raised is the one that matters
        raise


class _Undo:
    """What setup has done so far, so that a failure can put the project back as it was."""

    def __init__(self) -> None:
        self.made: list[Path] = []  # files and folders created, oldest first (a file is listed before it is written)
        self.changed: list[tuple[Path, bytes]] = []  # existing files rewritten, with their original bytes

    def roll_back(self) -> list[Path]:
        """Restore the changed files and delete what was created, newest first. Returns the paths it could not put back."""
        stuck: list[Path] = []
        for path, original in reversed(self.changed):
            try:
                _write_atomically(path, original)
            except OSError:
                stuck.append(path)
        for path in reversed(self.made):
            try:
                if path.is_dir():
                    path.rmdir()
                else:
                    path.unlink(missing_ok=True)
            except OSError:
                stuck.append(path)
        return stuck


# --------------------------------------------------------------------------- setup

def setup_project(repo: Path, answers: SetupAnswers) -> SetupResult:
    """Scaffold the project at ``repo`` for the factory. Does not run git and does not commit.

    Creates ``sw-factory.yaml``, ``REVIEW.md``, ``sw-factory/work/``, ``sw-factory/baseline/expected-failures.md`` and
    ``sw-factory/lessons.md``; adds ``sw-factory/dashboard/`` to ``.gitignore`` unless it is already ignored; and puts the
    factory section between ``<!-- sw-factory:begin -->`` and ``<!-- sw-factory:end -->`` in ``CLAUDE.md`` and ``AGENTS.md``
    wherever they exist (both, new, when neither does: Claude Code reads ``CLAUDE.md`` and Codex reads
    ``AGENTS.md``), replacing a section already there.

    Raises ``SetupError``, having written nothing, when ``sw-factory.yaml`` already exists ("already set up"), when
    another file setup would create exists, when ``sw-factory`` (or a folder in it) exists as a file, when a file setup
    must change is read-only, when the answers do not make a valid ``sw-factory.yaml``, or when the markers in an
    existing ``CLAUDE.md`` or ``AGENTS.md`` are not one begin line followed by one end line. If a write fails
    anyway, everything written so far is removed or restored (existing files are replaced atomically, never
    truncated) and ``SetupError`` names the path that failed; ``sw-factory.yaml`` is written last, so a failed run
    never leaves the project looking set up.
    """
    repo = Path(repo)
    if not repo.is_dir():
        raise SetupError(f"{repo} is not a folder")
    sw_factory_yaml = repo / PROJECT_CONFIG
    if sw_factory_yaml.exists():
        raise SetupError(f"already set up: {sw_factory_yaml} exists (setup runs once for a project)")
    review_md = repo / "REVIEW.md"
    project_folder = repo / PROJECT_FOLDER
    expected_failures = project_folder / "baseline" / "expected-failures.md"
    lessons = project_folder / "lessons.md"
    for path in (review_md, expected_failures, lessons):
        if path.exists():
            raise SetupError(f"{path} already exists; setup will not overwrite it (move it aside, then run setup again)")
    work_folder = project_folder / "work"
    folders = [project_folder, project_folder / "baseline", work_folder]
    for folder in folders:
        if folder.exists() and not folder.is_dir():
            raise SetupError(
                f"{folder} exists and is not a folder; setup needs a folder there (move it aside, then run setup again)"
            )
    new_folders = [folder for folder in folders if not folder.exists()]
    sw_factory_yaml_text = _render_sw_factory_yaml(answers)
    new_files = [
        (review_md, _template("REVIEW.md")),
        (expected_failures, _template("expected-failures.md")),
        (lessons, _template("lessons.md")),
    ]

    # Edits to files that may exist: (file, its bytes now or None when it is new, its bytes afterwards).
    edits: list[tuple[Path, bytes | None, bytes]] = []
    gitignore = repo / ".gitignore"
    ignore_before = gitignore.read_bytes() if gitignore.is_file() else None
    ignore_after = _with_ignore_entry(ignore_before or b"")
    if ignore_after is not None:
        edits.append((gitignore, ignore_before, ignore_after))
    section = _fill(_template("claude-section.md"), {
        "verify_quick": _code_span(answers.verify_quick),
        "verify_full": _code_span(answers.verify_full),
    }).strip("\n")
    holders = [repo / name for name in _SECTION_FILES if (repo / name).is_file()]
    for path in holders:
        before = path.read_bytes()
        after = _with_section(path.name, before, section)
        if after != before:
            edits.append((path, before, after))
    if not holders:  # a new file for each host: Claude Code reads CLAUDE.md, Codex reads AGENTS.md
        for name in _SECTION_FILES:
            edits.append((repo / name, None, _with_section(name, b"", section)))
    for path, before, _ in edits:
        if before is not None and not os.access(path, os.W_OK):
            raise SetupError(f"{path} is read-only, so setup cannot add to it (make it writable, then run setup again)")

    undo = _Undo()
    current = repo  # the path being worked on, named if its write fails
    try:
        for folder in new_folders:
            current = folder
            folder.mkdir()
            undo.made.append(folder)
        for path, text in new_files:
            current = path
            undo.made.append(path)
            path.write_bytes(text.encode("utf-8"))
        for path, before, after in edits:
            current = path
            if before is None:
                undo.made.append(path)
                path.write_bytes(after)
            else:
                _write_atomically(path, after)
                undo.changed.append((path, before))
        current = sw_factory_yaml
        undo.made.append(sw_factory_yaml)
        sw_factory_yaml.write_bytes(sw_factory_yaml_text.encode("utf-8"))
        conflicts = find_conflicts(repo)
    except BaseException as err:
        stuck = undo.roll_back()
        if not isinstance(err, OSError):
            raise
        reason = f"cannot write {current}: {err.strerror or err}."
        if stuck:
            raise SetupError(
                f"{reason} Setup could not undo {', '.join(map(str, stuck))}: "
                "remove or restore these by hand before running setup again."
            ) from err
        raise SetupError(
            f"{reason} Setup undid what it had written, so the project is as it was; fix the problem and run setup again."
        ) from err

    created = [sw_factory_yaml, review_md]
    if work_folder in new_folders:
        created.append(work_folder)
    created += [expected_failures, lessons]
    created += [path for path, before, _ in edits if before is None]
    updated = [path for path, before, _ in edits if before is not None]
    return SetupResult(created=created, updated=updated, conflicts=conflicts)


# --------------------------------------------------------------------------- conflicts

def find_conflicts(repo: Path) -> list[str]:
    """The lines of ``AGENTS.md`` and ``CLAUDE.md`` at the root of ``repo`` that look like rules the factory's
    defaults would break (no extra branches, no builds, commit straight to main, never push).

    Each is ``"<file>:<line>: <text>"``: the file name, the 1-based line number and the line without trailing
    whitespace; ``AGENTS.md`` is read first, and lines come in file order. The owner settles each in ``sw-factory.yaml``.
    """
    found: list[str] = []
    for name in _CONFLICT_FILES:
        path = Path(repo) / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        for number, line in enumerate(text.split("\n"), start=1):
            if _CONFLICT_RULES.search(line):
                found.append(f"{name}:{number}: {line.rstrip()}")
    return found
