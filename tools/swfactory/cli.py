"""The ``sw-factory`` command line.

``COMMANDS`` lists every top-level subcommand; the skill lint checks the commands that
factory skills mention against it, so a task that adds a subcommand adds its name here.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import signal
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path
from typing import get_args

from swfactory import __version__
from swfactory.adapters import plan_invocation, run_invocation
from swfactory.approvals import ApprovalError, record_approval
from swfactory.config import (
    BRANCHING,
    BUILDS,
    MODES,
    PROJECT_CONFIG,
    ConfigError,
    RoleTable,
    check_config,
    factory_home,
    load_project_config,
    load_role_table,
    load_skills_registry,
)
from swfactory.gitutil import GitError
from swfactory.mergegate import run_gate, write_gate_report
from swfactory.panel import combine, latest_passing_commit, render_panel_summary, write_panel_summary
from swfactory.prompts import MissingValue, render, required_placeholders
from swfactory.readiness import SKILL_FILE, SkillHost, check_readiness, find_installed_skills, skill_search_paths
from swfactory.reference import NONE, ReferenceError, none_section, reference_markers, reference_section
from swfactory.reviewmd import load_review_md
from swfactory.roles import Vendor, resolve_role
from swfactory.setup import SetupAnswers, SetupError, setup_project
from swfactory.tracks import TRACK_PREFIX, find_project_root, track_for_id
from swfactory.workitems import Status, WorkItem, create_work_item, find_work_item, read_status, write_status

COMMANDS: set[str] = {"config", "role", "panel", "work", "approve", "gate", "setup", "skills", "prompt"}

# Exit codes of ``sw-factory role run`` besides 0 (done) and 1 (the run failed; retry with --fallback).
EXIT_USAGE = 2
EXIT_NOT_A_COMMAND = 3  # a main or subagent role: the caller has to act on what was printed

# Exit codes of ``sw-factory panel combine`` are the panel verdict. A problem that stops it reaching a verdict
# (not a work folder, no REVIEW.md, no git to name the commit) exits EXIT_USAGE with the reason on stderr.
_PANEL_EXIT = {"PASS": 0, "CHANGES": 1, "INCOMPLETE": 2}

# ``sw-factory role resolve`` prints the invocation without a real output file; this stands in.
_PLACEHOLDER_OUT = Path("<out-file>")


@contextlib.contextmanager
def sigterm_as_system_exit() -> Iterator[None]:
    """While active, SIGTERM raises ``SystemExit`` so cleanup code runs (``role run`` kills its child and
    deletes its partial output). The previous handler is restored afterwards. Where a handler cannot be
    installed (a thread other than the main one, a platform without SIGTERM) this does nothing."""

    def handler(signum: int, frame: object) -> None:
        raise SystemExit(128 + signum)

    try:
        previous = signal.signal(signal.SIGTERM, handler)
    except (ValueError, OSError, AttributeError):
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def _cmd_config_check(args: argparse.Namespace) -> int:
    problems = check_config(args.project)
    if problems:
        for problem in problems:
            print(problem)
        return 1
    print("config ok")
    return 0


def _print_problems(problems: list[str]) -> int:
    for problem in problems:
        print(problem, file=sys.stderr)
    return EXIT_USAGE


def _load_role_table(project: Path | None) -> RoleTable:
    """The default role table, with the role overrides of a project's sw-factory.yaml when one is given."""
    overrides = None
    if project is not None:
        sw_factory_yaml = project / PROJECT_CONFIG if project.is_dir() else project
        overrides = load_project_config(sw_factory_yaml).role_overrides
    return load_role_table(factory_home() / "config" / "roles.yaml", overrides)


def _cmd_role_help(args: argparse.Namespace) -> int:
    args.role_parser.print_help()
    return EXIT_USAGE


def _cmd_role_resolve(args: argparse.Namespace) -> int:
    try:
        table = _load_role_table(args.project)
        r = resolve_role(table, args.role, args.host, args.author, args.fallback)
        inv = plan_invocation(r, args.host, _PLACEHOLDER_OUT)
    except ConfigError as err:
        return _print_problems(err.problems)
    print(json.dumps({"resolved": asdict(r), "invocation": asdict(inv)}, indent=2))
    return 0


def _cmd_role_run(args: argparse.Namespace) -> int:
    try:
        table = _load_role_table(args.project)
        r = resolve_role(table, args.role, args.host, args.author, args.fallback)
        inv = plan_invocation(r, args.host, args.out, args.schema)
        if inv.kind == "main":
            print(f"run role {r.role} in the main session")
            return EXIT_NOT_A_COMMAND
        if inv.kind == "subagent":
            if inv.agent is None:
                print(f"spawn subagent for role {r.role} with model {inv.model_alias} "
                      "(no dedicated agent is defined for this role)")
            else:
                print(f"spawn subagent {inv.agent} with model {inv.model_alias}")
            return EXIT_NOT_A_COMMAND
        try:
            prompt_text = args.prompt.read_text(encoding="utf-8-sig")
        except OSError as err:
            return _print_problems([f"cannot read the prompt file {args.prompt}: {err}"])
        result = run_invocation(inv, r, prompt_text, args.out)
    except ConfigError as err:
        return _print_problems(err.problems)
    if result.ok:
        print(f"wrote {result.out_path}")
        return 0
    print(f"role run failed: {r.role} ({r.model}): {result.stderr_tail}", file=sys.stderr)
    if result.suggest_fallback and not args.fallback:
        print("retry with --fallback to use the role's fallback binding", file=sys.stderr)
    return 1


def _cmd_panel_help(args: argparse.Namespace) -> int:
    args.panel_parser.print_help()
    return EXIT_USAGE


def _head_commit(root: Path) -> str:
    """The full SHA of HEAD in the project at ``root``; raises ``RuntimeError`` saying why there is none."""
    problem = f"cannot read HEAD of {root} to name the reviewed commit (pass --commit)"
    try:
        done = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as err:
        raise RuntimeError(f"{problem}: {err}") from None
    sha = done.stdout.strip()
    if done.returncode != 0 or not sha:
        raise RuntimeError(f"{problem}: {done.stderr.strip() or 'git printed nothing'}")
    return sha


def _print_text(text: str) -> None:
    """Print ``text``, replacing any character the console's encoding cannot show. An uncaught
    ``UnicodeEncodeError`` would exit 1, which ``panel combine`` uses to say CHANGES."""
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    sys.stdout.write(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))


def _cmd_panel_combine(args: argparse.Namespace) -> int:
    work_dir: Path = args.work_dir
    if not work_dir.is_dir():
        return _print_problems([f"{work_dir} is not a folder"])
    try:
        track = track_for_id(work_dir.resolve().name)
        root = find_project_root(work_dir)
        cfg = load_review_md(root / "REVIEW.md")
        lenses = cfg.tracks.get(track)
        if lenses is None:
            raise ConfigError([f"{root / 'REVIEW.md'} lists no lenses for the '{track}' track (add it under tracks)"])
        commit = args.commit if args.commit is not None else _head_commit(root)
        result = combine(work_dir, args.task, args.round, lenses, commit, cfg.nit_cap)
        path = write_panel_summary(work_dir, args.task, args.round, result)
    except ConfigError as err:
        return _print_problems(err.problems)
    except (ValueError, OSError, RuntimeError) as err:
        return _print_problems([str(err)])
    _print_text(render_panel_summary(result))
    _print_text(f"wrote {path}\n")
    return _PANEL_EXIT[result.verdict]


def _cmd_work_help(args: argparse.Namespace) -> int:
    args.work_parser.print_help()
    return EXIT_USAGE


def _work_project_root(project: Path | None) -> Path:
    """The project the ``work`` commands act on: ``--project`` (a folder or its sw-factory.yaml), else the one
    around the current folder."""
    if project is None:
        return find_project_root(Path.cwd())
    return find_project_root(project.parent if project.is_file() else project)


def _cmd_work_new(args: argparse.Namespace) -> int:
    try:
        root = _work_project_root(args.project)
        item = create_work_item(root, load_project_config(root / PROJECT_CONFIG), args.track, args.slug, args.title)
    except ConfigError as err:
        return _print_problems(err.problems)
    except (ValueError, OSError, GitError) as err:
        return _print_problems([str(err)])
    folder = item.path.relative_to(root).as_posix()
    print(json.dumps({"id": item.id, "track": item.track, "slug": item.slug, "title": item.title,
                      "branch": item.branch, "folder": folder}, indent=2))
    return 0


def _print_status(item: WorkItem, status: Status) -> None:
    """The work item's Status as JSON. Like ``role resolve`` this is ASCII-only, so it reads the same whatever
    encoding the console or pipe uses (a Status line printed raw would carry a middle dot)."""
    print(json.dumps({"id": item.id, **asdict(status)}, indent=2))


def _cmd_work_status(args: argparse.Namespace) -> int:
    try:
        item = find_work_item(_work_project_root(args.project), args.id)
        status = read_status(item.path)
    except (ValueError, OSError) as err:
        return _print_problems([str(err)])
    _print_status(item, status)
    return 0


def _cmd_work_set_status(args: argparse.Namespace) -> int:
    try:
        item = find_work_item(_work_project_root(args.project), args.id)
        status = write_status(item.path, args.stage, args.state, args.next, args.note)
    except (ValueError, OSError) as err:
        return _print_problems([str(err)])
    _print_status(item, status)
    return 0


def _cmd_approve(args: argparse.Namespace) -> int:
    quote = args.quote
    if args.quote_file is not None:  # the words come from a file, so no shell has quoted them on the way in
        try:
            quote = args.quote_file.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            return _print_problems([f"cannot read the quote file {args.quote_file}: it is not UTF-8 text"])
        except OSError as err:
            return _print_problems([f"cannot read the quote file {args.quote_file}: {err}"])
    try:
        root = find_project_root(Path.cwd())
        cfg = load_project_config(root / PROJECT_CONFIG)
        item = find_work_item(root, args.id)
        record = record_approval(
            item.path, cfg, args.artifact, args.rev, quote, args.approver, args.commit, args.host, args.session
        )
    except ApprovalError as err:  # refused: exit 1, so a caller can tell this from a command that could not run
        print(err, file=sys.stderr)
        return 1
    except ConfigError as err:
        return _print_problems(err.problems)
    except (ValueError, OSError) as err:
        return _print_problems([str(err)])
    print(json.dumps({"path": str(record.path)}, indent=2))
    return 0


def _cmd_gate(args: argparse.Namespace) -> int:
    try:
        root = find_project_root(Path.cwd())
        cfg = load_project_config(root / PROJECT_CONFIG)
        item = find_work_item(root, args.id)
        reviewed = args.reviewed_commit
        if reviewed is None:
            reviewed = latest_passing_commit(item.path)
            if reviewed is None:  # exit 1, like a failed gate: the work is not ready to merge
                print(
                    f"no passing panel summary found for {item.id} under {item.path / 'reviews'} "
                    "(run the review panel, or name the reviewed commit with --reviewed-commit)",
                    file=sys.stderr,
                )
                return 1
        report = run_gate(
            root, cfg, item, reviewed, args.spot_check, run_verify=not args.no_verify, builds_approved=args.builds_approved
        )
        path = write_gate_report(item.path, report)
    except ConfigError as err:
        return _print_problems(err.problems)
    except (ValueError, OSError, GitError) as err:
        return _print_problems([str(err)])
    _print_text(f"{path}\n")
    _print_text(f"GATE: {'PASS' if report.ok else 'FAIL'}\n")
    return 0 if report.ok else 1


def _cmd_setup(args: argparse.Namespace) -> int:
    repo = Path.cwd()
    answers = SetupAnswers(
        project=args.project,
        verify_quick=args.verify_quick,
        verify_full=args.verify_full,
        approvers=args.approver,
        base_branch=args.base,
        branching=args.branching,
        work_branch=args.work_branch,
        builds=args.builds,
        mode=args.mode,
    )
    try:
        result = setup_project(repo, answers)
    except (SetupError, OSError) as err:
        return _print_problems([str(err)])
    print(json.dumps({
        "created": [path.relative_to(repo).as_posix() for path in result.created],
        "updated": [path.relative_to(repo).as_posix() for path in result.updated],
        "conflicts": result.conflicts,
    }, indent=2))
    return 0


def _cmd_skills_help(args: argparse.Namespace) -> int:
    args.skills_parser.print_help()
    return EXIT_USAGE


def _cmd_skills_check(args: argparse.Namespace) -> int:
    project: Path | None = args.project
    try:
        table = _load_role_table(project)  # a project's role overrides can change who runs unattended
        registry = load_skills_registry(factory_home() / "config" / "skills.yaml")
    except ConfigError as err:
        return _print_problems(err.problems)
    if project is not None and project.is_file():
        project = project.parent  # --project was the sw-factory.yaml; the skill folders sit next to it
    home = args.home if args.home is not None else Path.home()
    report = check_readiness(registry, table, args.host, home, project)
    for skill in report.present:
        print(f"present {skill}")
    for skill, fallback in report.missing:
        if fallback is None:
            note = "no fallback"
        else:
            note = f"fallback {fallback}: {'installed' if fallback in report.installed_fallbacks else 'not installed'}"
        print(f"missing {skill} ({note})")
    for skill in report.interactive_in_unattended:
        print(f"interactive {skill} (registered for a subagent or headless role)")
    print("skills ok" if report.ok else "skills not ready")
    return 0 if report.ok else 1


def _cmd_skills_path(args: argparse.Namespace) -> int:
    project: Path | None = args.project
    if project is not None:
        if not project.exists():
            return _print_problems([f"--project {project}: not found"])
        if project.is_file():
            project = project.parent  # --project was the sw-factory.yaml; the skill folders sit next to it
    home = args.home if args.home is not None else Path.home()
    folder = find_installed_skills(skill_search_paths(args.host, home, project)).get(args.name)
    if folder is None:
        print("none")
        return 1
    path = os.path.abspath(folder / SKILL_FILE)
    try:
        print(path)
    except UnicodeEncodeError:  # a path with a character replaced would name the wrong file
        return _print_problems([f"the path of {args.name} cannot be shown in this console's encoding: {path!a}"])
    return 0


def _cmd_prompt_help(args: argparse.Namespace) -> int:
    args.prompt_parser.print_help()
    return EXIT_USAGE


_SET_ITEM = re.compile(r"(\w+)=(.*)", re.ASCII | re.DOTALL)


def _template_path(raw: Path) -> Path:
    """TEMPLATE as given when it is absolute or exists from the current folder; otherwise the same relative path
    in the factory checkout, so a skill can name ``prompts/tester-brief.md`` from inside any project."""
    if raw.is_absolute() or raw.exists():
        return raw
    return factory_home() / raw


_REFERENCE_PLACEHOLDER = "reference_skill"


def _reference_value(args: argparse.Namespace) -> tuple[str | None, list[str]]:
    """The text that fills ``{{reference_skill}}`` from ``--reference-skill``, or the problems that stop it.

    The skill must be registered for the lens's role in ``config/skills.yaml``; its registry entry says which
    extra files to inline and which fallback to use. Only the host app's user-level skill folders are searched."""
    name = args.reference_skill
    if name == NONE:
        return none_section().text, []
    if args.lens is None:
        return None, ["--reference-skill needs --lens (and --from-review-md): the lens's role registers the skill"]
    if args.host is None:
        return None, ["--reference-skill needs --host claude or --host codex: the app whose skill folders to search"]
    try:
        registry = load_skills_registry(factory_home() / "config" / "skills.yaml")
    except ConfigError as err:
        return None, err.problems
    role = f"lens-{args.lens}"
    entry = next((skill for skill in registry.get(role, []) if skill.name == name), None)
    if entry is None:
        return None, [f"--reference-skill {name}: config/skills.yaml registers no skill '{name}' for {role}"]
    home = args.home if args.home is not None else Path.home()
    try:
        return reference_section(entry, args.host, home).text, []
    except ReferenceError as err:
        return None, [f"--reference-skill {name}: {err}"]


def _prompt_values(args: argparse.Namespace) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """The values from ``--set``, ``--from-review-md`` and ``--reference-skill``, where each came from, and every
    problem with them."""
    values: dict[str, str] = {}
    source: dict[str, str] = {}
    problems: list[str] = []
    for item in args.set:
        match = _SET_ITEM.fullmatch(item)
        if match is None:
            problems.append(f"--set {item!r}: write NAME=VALUE, NAME being letters, digits and _")
            continue
        name, value = match.groups()
        if name in values:
            problems.append(f"--set {name} is given twice")
        if name == _REFERENCE_PLACEHOLDER:
            problems.append(f"--set {name}: the reference skill is filled by --reference-skill NAME (or none)")
        values[name], source[name] = value, f"--set {name}"
    if (args.from_review_md is None) != (args.lens is None):
        problems.append("--from-review-md and --lens go together: the lens names the checklist to fill")
    if (args.host is not None or args.home is not None) and args.reference_skill is None:
        problems.append("--host and --home go with --reference-skill: they say where to find the skill")
    if args.reference_skill is not None and not problems:
        text, reference_problems = _reference_value(args)
        problems += reference_problems
        if text is not None:
            values[_REFERENCE_PLACEHOLDER], source[_REFERENCE_PLACEHOLDER] = text, "--reference-skill"
    if problems or args.from_review_md is None:
        return values, source, problems
    try:
        cfg = load_review_md(args.from_review_md)
    except ConfigError as err:
        return values, source, err.problems
    if args.lens not in cfg.lenses:
        return values, source, [f"{args.from_review_md}: it has no lens '{args.lens}' (it has {', '.join(cfg.lenses)})"]
    for name, value in (("lens", args.lens), ("checklist", cfg.lenses[args.lens]), ("nit_cap", str(cfg.nit_cap))):
        if name in values:
            problems.append(f"--set {name} clashes with --from-review-md, which fills {name}")
        values[name], source[name] = value, "--from-review-md"
    return values, source, problems


def _cmd_prompt_render(args: argparse.Namespace) -> int:
    values, source, problems = _prompt_values(args)
    if problems:
        return _print_problems(problems)
    template = _template_path(args.template)
    try:
        unused = sorted(values.keys() - required_placeholders(template))
        if unused:  # a typo, or the wrong template: either way the prompt would not say what the caller meant
            return _print_problems(
                [f"{template.name} has no {{{{{name}}}}} placeholder (given by {source[name]})" for name in unused]
            )
        text = render(template, values)
    except MissingValue as err:
        hint = "pass --set NAME=VALUE for each"
        if _REFERENCE_PLACEHOLDER in err.names:
            hint += ", and --reference-skill NAME (or none) for reference_skill"
        return _print_problems([f"{err} ({hint})"])
    except (OSError, UnicodeDecodeError) as err:
        return _print_problems([f"cannot read the template {template}: {err}"])
    markers = reference_markers(text)
    expected = 0 if args.reference_skill is None else 1
    if len(markers) != expected:  # a checklist or value with a marker of its own would fake the review's provenance
        return _print_problems([
            f"the rendered prompt has {len(markers)} '<!-- reference: ... -->' lines; it may have {expected}, "
            "the one --reference-skill writes"
        ])
    try:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8", newline="\n")
    except OSError as err:
        return _print_problems([f"cannot write {args.out}: {err}"])
    _print_text(f"wrote {args.out}\n")
    if args.reference_skill is not None:
        _print_text(f"reference: {markers[0] if markers else NONE}\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sw-factory", description="Command-line tools for the BSRI software factory.")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    commands = parser.add_subparsers(dest="command", metavar="<command>")

    config = commands.add_parser("config", help="inspect and validate factory configuration")
    config_commands = config.add_subparsers(dest="config_command", metavar="<subcommand>")
    check = config_commands.add_parser(
        "check",
        help="validate roles.yaml and skills.yaml, and a project's sw-factory.yaml; exit 1 listing every problem",
    )
    check.add_argument(
        "--project",
        type=Path,
        metavar="PATH",
        help="a project folder or sw-factory.yaml to validate as well (its role overrides are applied)",
    )
    check.set_defaults(handler=_cmd_config_check)

    role = commands.add_parser("role", help="resolve a role to a model and run it headless")
    role.set_defaults(handler=_cmd_role_help, role_parser=role)
    role_commands = role.add_subparsers(dest="role_command", metavar="<subcommand>")
    resolve = role_commands.add_parser(
        "resolve",
        help="print the role's vendor, model and effort, and how it runs on the host, as JSON",
    )
    run = role_commands.add_parser(
        "run",
        help="run a headless role: prompt file in, review file out (exit 0 done, 1 failed, 3 not a command)",
    )
    for sub in (resolve, run):
        sub.add_argument("role", metavar="ROLE", help="a role name from roles.yaml")
        sub.add_argument("--host", required=True, choices=get_args(Vendor), help="the vendor of the session")
        sub.add_argument(
            "--author",
            choices=get_args(Vendor),
            help="the vendor that wrote the work under review (default: the host's)",
        )
        sub.add_argument("--fallback", action="store_true", help="use the role's fallback binding")
        sub.add_argument(
            "--project",
            type=Path,
            metavar="PATH",
            help="a project folder or sw-factory.yaml whose role overrides are applied",
        )
    run.add_argument("--prompt", required=True, type=Path, metavar="FILE", help="the prompt, sent on stdin")
    run.add_argument("--out", required=True, type=Path, metavar="FILE", help="where the reviewer's text is written")
    run.add_argument("--schema", type=Path, metavar="FILE", help="a JSON schema for the reply (Codex only)")
    resolve.set_defaults(handler=_cmd_role_resolve)
    run.set_defaults(handler=_cmd_role_run)

    panel = commands.add_parser("panel", help="combine the lens reviews of a task round into one verdict")
    panel.set_defaults(handler=_cmd_panel_help, panel_parser=panel)
    panel_commands = panel.add_subparsers(dest="panel_command", metavar="<subcommand>")
    combine_cmd = panel_commands.add_parser(
        "combine",
        help="read the round's lens reviews, write the panel summary (exit 0 PASS, 1 CHANGES, 2 INCOMPLETE)",
    )
    combine_cmd.add_argument(
        "work_dir",
        type=Path,
        metavar="WORK_DIR",
        help="the work item's folder, sw-factory/work/<ID>-<slug>; its name gives the track, and its lenses come "
        "from the project's REVIEW.md",
    )
    combine_cmd.add_argument("--task", required=True, metavar="T", help="the task id, as in reviews/<T>-r<N>-<lens>.md")
    combine_cmd.add_argument("--round", required=True, type=int, metavar="N", help="the review round, 1 or more")
    combine_cmd.add_argument(
        "--commit",
        metavar="SHA",
        help="the commit that was reviewed (default: HEAD of the project, as a full SHA)",
    )
    combine_cmd.set_defaults(handler=_cmd_panel_combine)

    work = commands.add_parser("work", help="open work items and read or update their Status line")
    work.set_defaults(handler=_cmd_work_help, work_parser=work)
    work_commands = work.add_subparsers(dest="work_command", metavar="<subcommand>")
    new = work_commands.add_parser(
        "new",
        help="open a work item: allocate its id, branch, folder of template files and first commit; prints JSON",
    )
    new.add_argument("--track", required=True, choices=sorted(TRACK_PREFIX), help="the track the work belongs to")
    new.add_argument("--slug", required=True, metavar="S", help="short name for the branch and folder: a-z, 0-9 and -, up to 40")
    new.add_argument("--title", required=True, metavar="T", help="one line saying what the work is")
    new.set_defaults(handler=_cmd_work_new)
    status = work_commands.add_parser("status", help="print the Status line of a work item's progress.md, as JSON")
    set_status = work_commands.add_parser(
        "set-status",
        help="rewrite the Status line and log a note at the top of ## Log, then print the new Status as JSON "
        "(does not commit)",
    )
    for sub in (status, set_status):
        sub.add_argument("id", metavar="ID", help="the work id, such as FIX-3; its folder must be in the checked-out tree")
    set_status.add_argument("--stage", required=True, metavar="S", help="the stage, such as build")
    set_status.add_argument("--state", required=True, metavar="S", help="the state within the stage, such as 'in progress'")
    set_status.add_argument("--next", required=True, metavar="TEXT", help="what the next session should do")
    set_status.add_argument("--note", required=True, metavar="TEXT", help="the log entry")
    status.set_defaults(handler=_cmd_work_status)
    set_status.set_defaults(handler=_cmd_work_set_status)
    for sub in (new, status, set_status):
        sub.add_argument(
            "--project",
            type=Path,
            metavar="PATH",
            help="a project folder or sw-factory.yaml (default: the project around the current folder)",
        )
    approve = commands.add_parser(
        "approve",
        help="record the owner's approval of a work item's artifact from their chat message; prints the "
        "record's path as JSON (does not commit; exit 1 if refused)",
    )
    approve.add_argument("id", metavar="ID", help="the work id, such as FEA-12; its folder must be in the checked-out tree")
    approve.add_argument("artifact", metavar="ARTIFACT", help="what was approved, such as spec or plan: a-z, 0-9 and -")
    approve.add_argument("--rev", required=True, metavar="R", help="the revision approved, digits and dots, such as 2 or 1.1")
    quote_source = approve.add_mutually_exclusive_group(required=True)
    quote_source.add_argument(
        "--quote",
        metavar="Q",
        help="the owner's message, word for word (write --quote=TEXT if it could start with a dash; "
        "for words with quotes, $, %% or line breaks prefer --quote-file)",
    )
    quote_source.add_argument(
        "--quote-file",
        type=Path,
        metavar="PATH",
        help="a UTF-8 file holding the owner's message, word for word; read exactly as --quote would be, "
        "and safe from shell quoting",
    )
    approve.add_argument("--approver", required=True, metavar="A", help="who approved; must be listed under approvers in sw-factory.yaml")
    approve.add_argument("--commit", required=True, metavar="C", help="the commit that holds the approved revision")
    approve.add_argument("--host", default="", metavar="H", help="the host of the session, such as claude or codex")
    approve.add_argument("--session", default="", metavar="S", help="the session id, if the host has one")
    approve.set_defaults(handler=_cmd_approve)
    gate = commands.add_parser(
        "gate",
        help="run the merge gate on a work item and write merge-gate.md in its folder; prints the report's path "
        "and GATE: PASS or GATE: FAIL (exit 0 PASS, 1 FAIL, 2 could not run)",
    )
    gate.add_argument("id", metavar="ID", help="the work id, such as FEA-12; its folder must be in the checked-out tree")
    gate.add_argument(
        "--spot-check",
        required=True,
        metavar="TEXT",
        help="the acceptance criterion you checked by hand and what you saw (write --spot-check=TEXT if it could "
        "start with a dash); empty text fails the gate",
    )
    gate.add_argument(
        "--reviewed-commit",
        metavar="SHA",
        help="the commit the panel passed (default: the commit of the newest passing panel summary of the work item)",
    )
    gate.add_argument("--no-verify", action="store_true", help="skip the full verify command (reported as skipped)")
    gate.add_argument(
        "--builds-approved",
        action="store_true",
        help="the owner agreed to the full build (needed when sw-factory.yaml says builds: ask)",
    )
    gate.set_defaults(handler=_cmd_gate)

    setup = commands.add_parser(
        "setup",
        help="set up the project in the current folder for the factory: sw-factory.yaml, REVIEW.md, the sw-factory/ "
        "folders and a factory section in CLAUDE.md/AGENTS.md; prints what it created and updated, and the project "
        "rules that conflict with factory defaults, as JSON (does not commit; exit 2 if it refuses)",
    )
    setup.add_argument("--project", required=True, metavar="NAME", help="the project's name, as shown in reports")
    setup.add_argument(
        "--verify-quick",
        required=True,
        metavar="CMD",
        help="the verify command for one task: builds and tests what the task touched",
    )
    setup.add_argument(
        "--verify-full",
        required=True,
        metavar="CMD",
        help="the verify command for the whole build and test suite",
    )
    setup.add_argument(
        "--approver",
        required=True,
        action="append",
        metavar="NAME",
        help="who may approve a checkpoint in chat; repeat for more than one",
    )
    setup.add_argument(
        "--base",
        default=SetupAnswers.base_branch,
        metavar="B",
        help="the branch finished work merges into (default: %(default)s)",
    )
    setup.add_argument(
        "--branching",
        choices=BRANCHING,
        default=SetupAnswers.branching,
        help="one branch per work item, or all work on one branch (default: %(default)s)",
    )
    setup.add_argument(
        "--work-branch",
        metavar="B",
        help="the one branch all work goes on; used only with --branching single",
    )
    setup.add_argument(
        "--builds",
        choices=BUILDS,
        default=SetupAnswers.builds,
        help="allow the full build, or ask the owner before each run (default: %(default)s)",
    )
    setup.add_argument(
        "--mode",
        choices=MODES,
        default=SetupAnswers.mode,
        help="stop at each checkpoint for approval, or carry on (default: %(default)s)",
    )
    setup.set_defaults(handler=_cmd_setup)

    skills = commands.add_parser(
        "skills", help="check that the third-party skills the roles need are installed, and find where one is"
    )
    skills.set_defaults(handler=_cmd_skills_help, skills_parser=skills)
    skills_commands = skills.add_subparsers(dest="skills_command", metavar="<subcommand>")
    skills_check = skills_commands.add_parser(
        "check",
        help="list the required skills that are present or missing on a host, with the fallback for each missing "
        "one, and the interactive skills bound to unattended roles (exit 0 ready, 1 not ready, 2 could not run)",
    )
    skills_check.add_argument("--host", required=True, choices=get_args(SkillHost), help="the host whose skills to check")
    skills_check.add_argument(
        "--project",
        type=Path,
        metavar="PATH",
        help="a project folder or sw-factory.yaml: its own skill folders are searched and its role overrides applied",
    )
    skills_check.add_argument(
        "--home",
        type=Path,
        metavar="PATH",
        help="the home folder that holds the host's skill folders (default: the user's home)",
    )
    skills_check.set_defaults(handler=_cmd_skills_check)
    skills_path = skills_commands.add_parser(
        "path",
        help="print the absolute path of an installed skill's SKILL.md, found where the host loads skills from "
        "(exit 0 found; 1 not installed, printing 'none'; 2 could not run)",
    )
    skills_path.add_argument("name", metavar="NAME", help="the skill's name, such as codebase-design")
    skills_path.add_argument(
        "--host", required=True, choices=get_args(SkillHost), help="the app whose skill folders to search"
    )
    skills_path.add_argument(
        "--project",
        type=Path,
        metavar="PATH",
        help="a project folder or sw-factory.yaml: its own skill folders are searched too",
    )
    skills_path.add_argument(
        "--home",
        type=Path,
        metavar="PATH",
        help="the home folder that holds the host's skill folders (default: the user's home)",
    )
    skills_path.set_defaults(handler=_cmd_skills_path)

    prompt = commands.add_parser("prompt", help="fill in the prompt templates the factory's roles read")
    prompt.set_defaults(handler=_cmd_prompt_help, prompt_parser=prompt)
    prompt_commands = prompt.add_subparsers(dest="prompt_command", metavar="<subcommand>")
    render_cmd = prompt_commands.add_parser(
        "render",
        help="fill every {{name}} placeholder of a template and write the prompt to --out (exit 2, listing every "
        "placeholder without a value, if any is missing)",
    )
    render_cmd.add_argument(
        "template",
        type=Path,
        metavar="TEMPLATE",
        help="the template, such as prompts/lens-security.md; a relative path that does not exist from the current "
        "folder is taken from the factory checkout",
    )
    render_cmd.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="the value of the placeholder {{NAME}}; repeat for each placeholder",
    )
    render_cmd.add_argument(
        "--from-review-md",
        type=Path,
        metavar="PATH",
        help="a project's REVIEW.md: fills lens, checklist (that lens's section) and nit_cap for a lens prompt; "
        "needs --lens",
    )
    render_cmd.add_argument("--lens", metavar="L", help="the lens whose checklist --from-review-md fills in")
    render_cmd.add_argument(
        "--reference-skill",
        metavar="NAME",
        help="fill {{reference_skill}} with the installed skill NAME, registered for the lens in config/skills.yaml: "
        "its SKILL.md and listed files, or its fallback's SKILL.md, or a sentence that none was available ('none' "
        "gives that sentence). Needs --lens and --host",
    )
    render_cmd.add_argument(
        "--host",
        choices=get_args(SkillHost),
        help="the app (not the vendor) whose user-level skill folders --reference-skill searches; a project's own "
        "skill folders are never searched",
    )
    render_cmd.add_argument(
        "--home",
        type=Path,
        metavar="PATH",
        help="the home folder that holds the host's skill folders (default: the user's home)",
    )
    render_cmd.add_argument("--out", required=True, type=Path, metavar="FILE", help="where the prompt is written (UTF-8)")
    render_cmd.set_defaults(handler=_cmd_prompt_render)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(__version__)
        return 0
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 2
    with sigterm_as_system_exit():
        return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
