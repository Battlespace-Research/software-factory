"""The ``factory`` command line.

``COMMANDS`` lists every top-level subcommand; the skill lint checks the commands that
factory skills mention against it, so a task that adds a subcommand adds its name here.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from swfactory import __version__
from swfactory.config import check_config

COMMANDS: set[str] = {"config"}


def _cmd_config_check(args: argparse.Namespace) -> int:
    problems = check_config(args.project)
    if problems:
        for problem in problems:
            print(problem)
        return 1
    print("config ok")
    return 0


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
