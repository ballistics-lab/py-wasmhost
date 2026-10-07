"""The command line: `python -m wasmhost COMMAND ...`.

Each command is a subcommand, so that the first positional argument stays free. It is where the module to run
will go, as in `wasmtime myapp.wasm -- arg1 arg2 --verbose`: the options before the module are the host's, and
everything after it (after `--` too) belongs to the program, which is not to look at it. That command is not here
yet (it needs WASI for the program's arguments, see BACKLOG.md); when it comes, the names of the commands are
tried first, and anything else is a module.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from importlib.metadata import version
from typing import Any, Protocol

from . import _bench
from ._selftest import DESCRIPTION as SELFTEST_DESCRIPTION
from ._selftest import add_arguments as add_selftest_arguments
from ._selftest import run as run_selftest

_VERSION = version("wasmhost")
SELF_DESCRIPTION = "manage the wasmhost executable"


class _SubparsersLike(Protocol):
    def add_parser(self, name: str, **kwargs: Any) -> argparse.ArgumentParser: ...


def add_version_command(subparser: _SubparsersLike) -> None:
    ver = subparser.add_parser("version", help="show program's version number and exit")

    def run_version(_args: argparse.Namespace) -> str:
        return _VERSION

    ver.set_defaults(run=run_version)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m wasmhost", description="WebAssembly from Python.")
    parser.add_argument("-V", "--version", action="version", version=_VERSION)

    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    self_command = commands.add_parser("self", help=SELF_DESCRIPTION, description=SELF_DESCRIPTION)

    def run_self_usage(_args: argparse.Namespace) -> str:
        return self_command.format_usage()

    self_command.set_defaults(run=run_self_usage)

    self_commands = self_command.add_subparsers(
        dest="self_command", metavar="COMMAND", description=SELFTEST_DESCRIPTION
    )
    add_version_command(self_commands)
    selftest = self_commands.add_parser("test", help="check that wasmhost works here")
    add_selftest_arguments(selftest)
    selftest.set_defaults(run=run_selftest)

    bench = commands.add_parser(
        "bench", help="time the backends: a call, a batch, the engine", description=_bench.DESCRIPTION
    )
    _bench.add_arguments(bench)
    bench.set_defaults(run=_bench.run)

    add_version_command(commands)

    help = commands.add_parser("help", help="show this help message and exit")

    def run_help(_args: argparse.Namespace) -> str:
        return parser.format_help()

    help.set_defaults(run=run_help)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = getattr(args, "run", None)
    if command is None:  # no command
        parser.print_help(sys.stderr)
        return 2
    return command(args)
