"""The command line: `wasmhost COMMAND ...`.

`wasmhost run [OPTIONS] module.wasm [-- ARGUMENTS]` runs a module as `wasmtime run` does (`_run.py`): the options
before the module are the host's, everything after it (after `--` too) goes to the program unparsed.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Protocol

from . import _bench, _run
from ._selftest import DESCRIPTION as SELFTEST_DESCRIPTION
from ._selftest import add_arguments as add_selftest_arguments
from ._selftest import run as run_selftest


def _installed_version() -> str:
    try:
        return version("wasmhost")
    except PackageNotFoundError:  # run from a source tree that was never installed
        return "unknown"


_VERSION = _installed_version()
SELF_DESCRIPTION = "manage the wasmhost executable"


class _SubparsersLike(Protocol):
    def add_parser(self, name: str, **kwargs: Any) -> argparse.ArgumentParser: ...


def add_version_command(subparser: _SubparsersLike) -> None:
    ver = subparser.add_parser("version", help="show program's version number and exit")

    def run_version(_args: argparse.Namespace) -> int:
        print(_VERSION)
        return 0

    ver.set_defaults(run=run_version)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m wasmhost",
        description=(
            "Run WebAssembly from Python with the JavaScript WebAssembly API, "
            "on JavaScriptCore, Node, Bun, wasmtime or wasm3."
        ),
    )
    parser.add_argument("-V", "--version", action="version", version=_VERSION)

    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    self_command = commands.add_parser("self", help=SELF_DESCRIPTION, description=SELF_DESCRIPTION)

    def run_self_usage(_args: argparse.Namespace) -> int:
        self_command.print_usage(sys.stderr)  # no subcommand: the same as no command at all
        sys.exit(2)

    self_command.set_defaults(run=run_self_usage)

    self_commands = self_command.add_subparsers(
        dest="self_command", metavar="COMMAND", description=SELFTEST_DESCRIPTION
    )
    add_version_command(self_commands)
    selftest = self_commands.add_parser("test", help="check that wasmhost works here")
    add_selftest_arguments(selftest)
    selftest.set_defaults(run=run_selftest)

    run = commands.add_parser(
        "run", help="run a module: a WASI command, or a function with --invoke", add_help=False
    )  # parsed by _run, which splits the line at the module; here for the help only
    run.set_defaults(run=None)

    bench = commands.add_parser(
        "bench", help="time the backends: a call, a batch, the engine", description=_bench.DESCRIPTION
    )
    _bench.add_arguments(bench)
    bench.set_defaults(run=_bench.run)

    add_version_command(commands)

    help = commands.add_parser("help", help="show this help message and exit")

    def run_help(_args: argparse.Namespace) -> int:
        parser.print_help()
        return 0

    help.set_defaults(run=run_help)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    line = list(sys.argv[1:] if argv is None else argv)
    if line[:1] == ["run"]:  # the line is split at the module, so argparse never sees the program's arguments
        running = _run.split_command(line[1:])
        if running is None:
            _run.build_parser().print_help(sys.stderr if len(line) == 1 else sys.stdout)
            return 2 if len(line) == 1 else 0
        return _run.run(*running)
    args = parser.parse_args(line)
    command = getattr(args, "run", None)
    if command is None:  # no command
        parser.print_help(sys.stderr)
        return 2
    return command(args)
