"""The ``factory`` command line.

``COMMANDS`` lists every top-level subcommand; the skill lint checks the commands that
factory skills mention against it, so a task that adds a subcommand adds its name here.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import get_args

from swfactory import __version__
from swfactory.adapters import plan_invocation, run_invocation
from swfactory.config import ConfigError, check_config, factory_home, load_role_table
from swfactory.roles import Vendor, resolve_role

COMMANDS: set[str] = {"config", "role"}

# Exit codes of ``factory role run`` besides 0 (done) and 1 (the run failed; retry with --fallback).
EXIT_USAGE = 2
EXIT_NOT_A_COMMAND = 3  # a main or subagent role: the caller has to act on what was printed

# ``factory role resolve`` prints the invocation without a real output file; this stands in.
_PLACEHOLDER_OUT = Path("<out-file>")


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


def _cmd_role_help(args: argparse.Namespace) -> int:
    args.role_parser.print_help()
    return EXIT_USAGE


def _cmd_role_resolve(args: argparse.Namespace) -> int:
    try:
        table = load_role_table(factory_home() / "config" / "roles.yaml")
        r = resolve_role(table, args.role, args.host, args.author, args.fallback)
        inv = plan_invocation(r, args.host, _PLACEHOLDER_OUT)
    except ConfigError as err:
        return _print_problems(err.problems)
    print(json.dumps({"resolved": asdict(r), "invocation": asdict(inv)}, indent=2))
    return 0


def _cmd_role_run(args: argparse.Namespace) -> int:
    try:
        table = load_role_table(factory_home() / "config" / "roles.yaml")
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
    run.add_argument("--prompt", required=True, type=Path, metavar="FILE", help="the prompt, sent on stdin")
    run.add_argument("--out", required=True, type=Path, metavar="FILE", help="where the reviewer's text is written")
    run.add_argument("--schema", type=Path, metavar="FILE", help="a JSON schema for the reply (Codex only)")
    resolve.set_defaults(handler=_cmd_role_resolve)
    run.set_defaults(handler=_cmd_role_run)
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
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
