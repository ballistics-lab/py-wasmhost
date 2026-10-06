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

from ._selftest import DESCRIPTION, add_arguments, run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m wasmhost", description="WebAssembly from Python.")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    selftest = commands.add_parser(
        "selftest", aliases=["self-test"], help="check that wasmhost works here", description=DESCRIPTION
    )
    add_arguments(selftest)
    selftest.set_defaults(run=run)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = getattr(args, "run", None)
    if command is None:  # no command
        parser.print_help(sys.stderr)
        return 2
    return command(args)
