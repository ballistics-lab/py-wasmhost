"""`wasmhost run [HOST OPTIONS] module.wasm [PROGRAM ARGUMENTS]`: run a module as `wasmtime run` does.

The options before the module belong to the host; everything after the module (a leading `--` is dropped) goes to the
program unparsed. A module with `_start` is a WASI command and its exit code is the command's; `--invoke NAME` calls
an exported function instead, the arguments after the module being its parameters, and prints each result on a line
of its own, as `wasmtime run --invoke` does.
"""

from __future__ import annotations

import argparse
import os
import re
import struct
import sys
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from . import BACKENDS, Instance, Module, Trap, WasmError, get_backend, set_backend
from .wasi.preview1 import WasiExit, Wasip1

# The options that take a value: the command line is split before argparse sees it, to find the module.
VALUE_OPTIONS = frozenset(
    {"--argv0", "--backend", "--runtime", "--dir", "--env", "--invoke", "--max-memory", "--timeout", "--fuel"}
)
INFO_OPTIONS = frozenset({"-h", "--help"})
TRAP_EXIT = 3 if os.name == "nt" else 134  # what wasmtime exits with on a trap (abort(): SIGABRT, or 3 on Windows)


def split_command(argv: Sequence[str]) -> tuple[list[str], str, list[str]] | None:
    """`(host options, module, program arguments)` of what follows `run`, or None if there is no module (the line is
    empty or asks for help before any module)."""
    i = 0
    while i < len(argv):
        token = argv[i]
        if token in INFO_OPTIONS:
            return None
        if token.startswith("-") and token != "-":
            i += 2 if token in VALUE_OPTIONS else 1  # `--opt=value` is one token
            continue
        rest = list(argv[i + 1 :])
        if rest[:1] == ["--"]:
            rest = rest[1:]
        return list(argv[:i]), token, rest
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wasmhost run",
        usage="wasmhost run [OPTIONS] MODULE.wasm [-- PROGRAM ARGUMENTS]",
        description="Run a WebAssembly module: a WASI command (_start), or one exported function with --invoke.",
    )
    parser.add_argument(
        "--backend",
        "--runtime",
        choices=sorted(BACKENDS),
        help="the backend (default: $WASMHOST_BACKEND, else the first that starts)",
    )
    parser.add_argument(
        "--dir", action="append", default=[], metavar="HOST[::GUEST]", help="preopen a directory (GUEST is its name)"
    )
    parser.add_argument("--readonly", action="store_true", help="the preopened directories can't be changed")
    parser.add_argument(
        "--env", action="append", default=[], metavar="NAME[=VALUE]", help="set a variable (NAME alone: copy it)"
    )
    parser.add_argument("--argv0", metavar="ARGV0", help="the program's argv[0] (default: the module's path)")
    parser.add_argument("--invoke", metavar="FUNCTION", help="call an exported function with the arguments")
    parser.add_argument("--max-memory", type=int, metavar="PAGES", help="the most 64 KiB pages a memory may have")
    parser.add_argument("--timeout", type=float, metavar="SECONDS", help="stop the program after that long")
    parser.add_argument("--fuel", type=int, metavar="UNITS", help="stop the program after that much work")
    return parser


_DECIMAL = re.compile(r"[+-]?[0-9]+")
_HEX = re.compile(r"[0-9a-fA-F]+")
_FLOAT = re.compile(r"[+-]?(?:inf|infinity|nan|(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)", re.IGNORECASE)


def parse_value(kind: str, text: str) -> int | float:
    """An argument as `wasmtime` reads it (run.rs): decimal or `0x` hexadecimal for an integer, which must fit the type
    (no wrapping), the text of a Rust float for a float."""
    if kind in ("i32", "i64"):
        bits = 32 if kind == "i32" else 64
        if text[:2] in ("0x", "0X"):
            if not _HEX.fullmatch(text[2:]):
                raise ValueError("invalid digit found in string")
            number = int(text[2:], 16)
        elif _DECIMAL.fullmatch(text):
            number = int(text)
        else:
            raise ValueError("invalid digit found in string")
        if not -(1 << (bits - 1)) <= number < (1 << (bits - 1)):
            raise ValueError("number too large to fit in target type")
        return number
    if not _FLOAT.fullmatch(text):
        raise ValueError("invalid float literal")
    return float(text)


def _f32(value: float) -> str:
    """The shortest text that is the same f32 (as Rust prints it)."""
    for digits in range(1, 10):
        text = f"{value:.{digits}g}"
        if struct.unpack("<f", struct.pack("<f", float(text)))[0] == value:
            return text
    return repr(value)  # pragma: no cover -- 9 digits always do


def format_value(kind: str, value: Any) -> str:
    """A result as `wasmtime` prints it: an integer as it is, a float as Rust's `{}` does (`5`, `0.5`, `NaN`, `inf`)."""
    if kind in ("i32", "i64"):
        return str(int(value))
    number = float(value)
    if number != number:
        return "NaN"
    if number in (float("inf"), float("-inf")):
        return "inf" if number > 0 else "-inf"
    text = _f32(number) if kind == "f32" else repr(number)
    if "e" in text or "E" in text:
        text = format(Decimal(text), "f")
    return text[:-2] if text.endswith(".0") else text


def _preopens(specs: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for spec in specs:
        host, _, guest = spec.partition("::")
        result[guest or host] = host
    return result


def _environment(specs: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for spec in specs:
        name, has_value, value = spec.partition("=")
        if has_value:
            result[name] = value
        elif name in os.environ:
            result[name] = os.environ[name]
    return result


def _write(stream: Any) -> Any:
    def write(data: bytes) -> None:
        out = getattr(stream, "buffer", stream)
        out.write(data)
        out.flush()

    return write


def _invoke(instance: Instance, module: Module, name: str, words: Sequence[str]) -> list[str]:
    for export in Module.exports(module):
        if export.name == name and export.kind == "function":
            break
    else:
        raise WasmError(f"no function named `{name}` is exported")
    func_type: Any = export.type
    kinds = [str(k) for k in func_type.parameters]
    if len(words) != len(kinds):
        raise WasmError(f"`{name}` takes {len(kinds)} argument(s) ({', '.join(kinds)}), {len(words)} given")
    values = [parse_value(kind, word) for kind, word in zip(kinds, words, strict=True)]
    result = getattr(instance.exports, name)(*values)
    kinds_out = [str(k) for k in func_type.results]
    if not kinds_out:
        return []
    items = result if len(kinds_out) > 1 else [result]
    return [format_value(kind, item) for kind, item in zip(kinds_out, items, strict=True)]


def _start(wasi: Wasip1, instance: Instance) -> int:
    """As run.rs: `_initialize` (a reactor) first if there is one, then `_start`; a module with neither exits 0."""
    try:
        initialize = getattr(instance.exports, "_initialize", None)
        if initialize is not None:
            initialize()
        start = getattr(instance.exports, "_start", None)
        if start is not None:
            start()
    except WasiExit as exit_:
        return exit_.code
    return 0


def run(host_args: Sequence[str], path: str, program_args: Sequence[str]) -> int:
    args = build_parser().parse_args(host_args)
    try:
        wasm = Path(path).read_bytes()
    except OSError as exc:
        cause = f"{exc.strerror or exc} (os error {exc.errno})"
        print(f'Error: failed to open wasm module "{path}"\n\nCaused by:\n    {cause}', file=sys.stderr)  # as wasmtime
        return 1
    options: dict[str, Any] = {
        key: value
        for key, value in (("max_memory", args.max_memory), ("timeout", args.timeout), ("fuel", args.fuel))
        if value is not None
    }
    try:
        if args.backend:
            set_backend(args.backend)
        else:
            get_backend()
        module = Module(wasm)
        wasi = Wasip1(
            args=[args.argv0 if args.argv0 is not None else os.path.basename(path), *program_args],
            env=_environment(args.env),
            preopens=_preopens(args.dir),
            stdin=sys.stdin.buffer,
            stdout=_write(sys.stdout),
            stderr=_write(sys.stderr),
            readonly=args.readonly,
        )
        with wasi:
            instance = Instance(module, wasi.imports(), **options)
            try:
                wasi.bind(instance)
            except AttributeError:  # no memory exported: nothing for the calls to read
                pass
            if args.invoke:
                try:
                    lines = _invoke(instance, module, args.invoke, program_args)
                except WasiExit as exit_:
                    return exit_.code
                for line in lines:
                    print(line)
                return 0
            return _start(wasi, instance)
    except Trap as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return TRAP_EXIT
    except (WasmError, OSError, ValueError, TypeError, ImportError, NotImplementedError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
