"""The ``factory`` command line.

``COMMANDS`` lists every top-level subcommand; the skill lint checks the commands that
factory skills mention against it, so a task that adds a subcommand adds its name here.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import signal
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path
from typing import get_args

from swfactory import __version__
from swfactory.adapters import plan_invocation, run_invocation
from swfactory.config import (
    ConfigError,
    RoleTable,
    check_config,
    factory_home,
    load_project_config,
    load_role_table,
)
from swfactory.panel import combine, render_panel_summary, write_panel_summary
from swfactory.reviewmd import load_review_md
from swfactory.roles import Vendor, resolve_role
from swfactory.tracks import find_project_root, track_for_id

COMMANDS: set[str] = {"config", "role", "panel"}

# Exit codes of ``factory role run`` besides 0 (done) and 1 (the run failed; retry with --fallback).
EXIT_USAGE = 2
EXIT_NOT_A_COMMAND = 3  # a main or subagent role: the caller has to act on what was printed

# Exit codes of ``factory panel combine`` are the panel verdict. A problem that stops it reaching a verdict
# (not a work folder, no REVIEW.md, no git to name the commit) exits EXIT_USAGE with the reason on stderr.
_PANEL_EXIT = {"PASS": 0, "CHANGES": 1, "INCOMPLETE": 2}

# ``factory role resolve`` prints the invocation without a real output file; this stands in.
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
    """The default role table, with the role overrides of a project's factory.yaml when one is given."""
    overrides = None
    if project is not None:
        factory_yaml = project / "factory.yaml" if project.is_dir() else project
        overrides = load_project_config(factory_yaml).role_overrides
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="factory", description="Command-line tools for the BSRI software factory.")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    commands = parser.add_subparsers(dest="command", metavar="<command>")

    config = commands.add_parser("config", help="inspect and validate factory configuration")
    config_commands = config.add_subparsers(dest="config_command", metavar="<subcommand>")
    check = config_commands.add_parser(
        "check",
        help="validate roles.yaml and skills.yaml, and a project's factory.yaml; exit 1 listing every problem",
    )
    check.add_argument(
        "--project",
        type=Path,
        metavar="PATH",
        help="a project folder or factory.yaml to validate as well (its role overrides are applied)",
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
            help="a project folder or factory.yaml whose role overrides are applied",
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
        help="the work item's folder, factory/work/<ID>-<slug>; its name gives the track, and its lenses come "
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
