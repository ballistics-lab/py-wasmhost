"""A self-test to run on the device: `python -m wasmhost self test`, or `wasmhost.selftest()` from a console.

Made for Pythonista (or any iOS Python app), where nothing else can be run to see whether wasmhost works: it
walks through the things that could go wrong there -- the Objective-C bridge, `WebAssembly` and `BigInt` in the
engine, calls, memory, globals, memories and tables made on their own, custom sections, types, traps, batches -- prints
one line for each, and ends with a summary and the cost of a call. If something fails, send the whole output.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import math
import os
import platform
import sys
import sysconfig
import tempfile
import time
import traceback
from collections.abc import Callable
from typing import Any

from ._api import Global, Instance, Memory, Module, Table, get_backend, validate
from ._api import compile as compile_async
from ._api import instantiate as instantiate_async
from ._backend import Backend
from ._binary import FuncType, GlobalType, MemoryType, TableType, i32, i64
from ._errors import CompileError, LinkError, OutOfFuel, Timeout, Trap
from ._js import JSBackend
from ._registry import AUTO_ORDER, BACKENDS
from .wasi.preview1 import Preview1

__all__ = ("selftest",)

# A module with: add(i32, i32) -> i32, add64(i64, i64) -> i64, fadd(f64, f64) -> f64, trap(), dup(i32) -> (i32, i32),
# store8(ptr, value), grow(pages) -> i32, twice(f32) -> f32, a memory of 1 page (at most 4), a mutable i32 global
# `counter` (= 7) and an immutable one `ten` (= 11). Written out section by section so it needs no compiler.
MODULE = bytes.fromhex(
    "0061736d01000000"  # magic, version
    "012b0860027f7f017f60027e7e017e60027c7c017c60000060017f027f7f60027f7f0060017f017f"  # types
    "60017d017d"
    "0309080001020304050607"  # functions: 8, one type each
    "050401010104"  # memory: 1 page, at most 4
    "060b027f0141070b7f00410b0b"  # globals: counter (mutable), ten (immutable)
    "07540b03616464000005616464363400010466616464000204747261700003036475700004067374"  # exports
    "6f72653800050467726f7700060574776963650007066d656d6f7279020007636f756e7465720300"
    "0374656e0301"
    "0a3d080700200020016a0b0700200020017c0b070020002001a00b0300000b0600200020000b0900"  # code
    "200020013a00000b0600200040000b070020002000920b"
)

# A module that imports four host functions and calls them from its own exports: env.plus(i32, i32) -> i32,
# env.note(i64), env.half(f64) -> f64 and env.pair(i32) -> (i32, i32); its exports call_plus, call_note, call_half
# and call_pair call one each, and twice(a) = plus(plus(a, 1), 1).
CALLBACKS = bytes.fromhex(
    "0061736d01000000"  # magic, version
    "011b0560027f7f017f60017e0060017c017c60017f027f7f60017f017f"  # types
    "022d0403656e7604706c7573000003656e76046e6f7465000103656e760468616c66000203656e76"  # imports: env.plus, env.note,
    "04706169720003"  # ... env.half, env.pair
    "0306050001020304"  # functions: 5
    "0739050963616c6c5f706c757300040963616c6c5f6e6f746500050963616c6c5f68616c66000609"  # exports
    "63616c6c5f7061697200070574776963650008"
    "0a2c0508002000200110000b0600200010010b0600200010020b0600200010030b0c002000410110"  # code
    "00410110000b"
)

# A module that imports the mutable i32 global env.g: get() reads it, inc() adds 1 to it.
GLOBAL_IMPORT = bytes.fromhex(
    "0061736d01000000"  # magic, version
    "0108026000017f600000"  # types
    "020a0103656e760167037f01"  # imports: env.g, a mutable i32 global
    "0303020001"  # functions: 2
    "070d0203676574000003696e630001"  # exports: get, inc
    "0a1002040023000b0900230041016a24000b"  # code
)


# A module that imports the memory env.memory (min 1 page): load(addr) -> i32 reads a byte, and
# store(addr, byte) writes one.
MEMORY_USER = bytes.fromhex(
    "0061736d01000000010b0260017f017f60027f7f00020f0103656e76066d656d6f72790200010303020001071002046c"
    "6f616400000573746f726500010a1302070020002d00000b0900200020013a00000b"
)

# A module that imports the memory env.memory (min 1 page): grow(pages) -> i32 is memory.grow (the old size, or -1 past
# the memory's maximum) and size() -> i32 is memory.size.
GROWS_MEMORY = bytes.fromhex(
    "0061736d01000000010a0260017f017f6000017f020f0103656e76066d656d6f72790200010303020001070f020467726f"
    "7700000473697a6500010a0d020600200040000b04003f000b"
)

# A module that makes its own memory (1 page, no maximum) and exports it as "memory", with grow(pages) -> i32
# (memory.grow: the old size, or -1 past the maximum) and size() -> i32.
OWNS_MEMORY = bytes.fromhex(
    "0061736d01000000010a0260017f017f6000017f030302000105030100010718030467726f7700000473697a650001066d"
    "656d6f727902000a0d020600200040000b04003f000b"
)

# A module with spin() that loops forever, quick() -> i32 that returns 7, and busy(n) -> i32 that counts to n.
SPINNER = bytes.fromhex(
    "0061736d01000000010d036000006000017f60017f017f030403000102071703047370696e000005717569636b00010462"
    "75737900020a2603070003400c000b0b040041070b1701017f0340200141016a210120012000490d000b20010b"
)

# A module with a table of two funcref entries that it exports as "table", and add(a, b), mul(a, b) and
# call(a, b, index) = table[index](a, b) (call_indirect).
TABLE_OWN = bytes.fromhex(
    "0061736d01000000010e0260027f7f017f60037f7f7f017f030403000001040401700002071c04036164640000036d75"
    "6c00010463616c6c0002057461626c6501000a1d030700200020016a0b0700200020016c0b0b00200020012002110000"
    "0b"
)

# Two functions (add, mul) that no one exports, put in the slots 0 and 1 of the exported table by an `elem` segment.
TABLE_ELEM = bytes.fromhex(
    "0061736d01000000010a0260027f7f017f600000030403000001040401700002070901057461626c6501000908010041000b"
    "0200010a14030700200020016a0b0700200020016c0b02000b"
)

# The same, with a start function, so that no one tells the type of what the table holds.
TABLE_ELEM_START = bytes.fromhex(
    "0061736d01000000010a0260027f7f017f600000030403000001040401700002070901057461626c65010008010209080100"
    "41000b0200010a14030700200020016a0b0700200020016c0b02000b"
)

# The same module, but its table is imported as env.table (two entries at least) instead of its own.
TABLE_IMPORT = bytes.fromhex(
    "0061736d01000000010e0260027f7f017f60037f7f7f017f020f0103656e76057461626c650170000203040300000107"
    "1403036164640000036d756c00010463616c6c00020a1d030700200020016a0b0700200020016c0b0b00200020012002"
    "1100000b"
)

# Two functions, caught() -> i32 and uncaught() -> i32: each throws a WebAssembly exception (tag 0 and tag 1) inside a
# handler for tag 0. caught() comes out of the handler with 42, uncaught() must not return. There is a module for each
# encoding of exception handling, since an engine may take one and not the other: the final one (`try_table`, exnref:
# wasmtime, wasm3, recent Node and Safari/iOS) and the older `try`/`catch` (what clang emits by default: Node, Safari,
# not wasmtime).
EXCEPTIONS_FINAL = bytes.fromhex(
    "0061736d01000000"  # magic, version
    "0108026000017f600000"  # types: () -> i32, () -> ()
    "03030200000d05020001000107150206636175676874000008756e6361756768740001"  # functions, tags, exports
    "0a2902130002401f400100000008000b41010f0b412a0b130002401f400100000008010b41010f0b412a0b"  # code
)
EXCEPTIONS_LEGACY = bytes.fromhex(
    "0061736d01000000"  # magic, version
    "0108026000017f600000"  # types: () -> i32, () -> ()
    "03030200000d05020001000107150206636175676874000008756e6361756768740001"  # functions, tags, exports
    "0a19020b00067f08000700412a0b0b0b00067f08010700412a0b0b"  # code
)

WASI_HELLO = bytes.fromhex(  # a WASI command: writes "hello, wasi\n" to stdout and to note.txt, exits with argc
    "0061736d0100000001280660027f7f017f60047f7f7f7f017f60097f7f7f7f7f7e7e7f7f017f60017f017f60017f00600000"
    "02b3010516776173695f736e617073686f745f70726576696577310e617267735f73697a65735f676574000016776173695f"
    "736e617073686f745f70726576696577310866645f7772697465000116776173695f736e617073686f745f70726576696577"
    "3109706174685f6f70656e000216776173695f736e617073686f745f70726576696577310866645f636c6f73650003167761"
    "73695f736e617073686f745f70726576696577310970726f635f657869740004030201050503010001071302066d656d6f72"
    "790200065f737461727400050a4c014a004100410410001a410141104101411810011a4103410141c00041084101427f427f"
    "410041e40010021a41e40028020041104101411810011a41e40028020010031a410028020010040b0b30020041100b1c2000"
    "00000c000000000000000000000068656c6c6f2c20776173690a0041c0000b086e6f74652e747874"
)

WASI_HELLO_UNSTABLE = bytes.fromhex(  # the same program, importing from wasi_unstable (the first snapshot)
    "0061736d0100000001280660027f7f017f60047f7f7f7f017f60097f7f7f7f7f7e7e7f7f017f60017f017f60017f00600000"
    "028601050d776173695f756e737461626c650e617267735f73697a65735f67657400000d776173695f756e737461626c6508"
    "66645f777269746500010d776173695f756e737461626c6509706174685f6f70656e00020d776173695f756e737461626c65"
    "0866645f636c6f736500030d776173695f756e737461626c650970726f635f65786974000403020105050301000107130206"
    "6d656d6f72790200065f737461727400050a4c014a004100410410001a410141104101411810011a4103410141c000410841"
    "01427f427f410041e40010021a41e40028020041104101411810011a41e40028020010031a410028020010040b0b30020041"
    "100b1c200000000c000000000000000000000068656c6c6f2c20776173690a0041c0000b086e6f74652e747874"
)


class _Report:
    def __init__(self, out: Callable[[str], object]) -> None:
        self.out = out
        self.passed = 0
        self.failed: list[str] = []

    def step(self, title: str, fn: Callable[[], object]) -> None:
        try:
            detail = fn()
        except Exception as exc:  # noqa: BLE001 -- the point is to report anything
            self.failed.append(title)
            self.out(f"  FAIL  {title}: {type(exc).__name__}: {exc}")
            for line in traceback.format_exc().rstrip().splitlines()[-6:]:
                self.out(f"        {line}")
        else:
            self.passed += 1
            self.out(f"  ok    {title}" + (f"  [{detail}]" if detail else ""))


def _expect(actual: object, expected: object) -> None:
    if actual != expected:
        raise AssertionError(f"expected {expected!r}, got {actual!r}")


def _environment(out: Callable[[str], object]) -> None:
    out(f"python      {sys.version.split()[0]} ({platform.python_implementation()}) on {sys.platform}")
    out(f"platform    {sysconfig.get_platform()}")
    for name in ("objc_util", "rubicon.objc"):
        try:
            found = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            found = False
        out(f"module      {name}: {'yes' if found else 'no'}")


def _selftest_backend(backend: Backend, out: Callable[[str], object]) -> _Report:
    report = _Report(out)
    step = report.step
    bridge = getattr(backend, "bridge", None)
    out(f"backend     {backend.name}" + (f" (bridge: {bridge})" if bridge else ""))
    if (why := getattr(backend, "c_api_error", None)) is not None:
        out(f"C API       not used: {why}")

    if isinstance(backend, JSBackend):
        step("evaluate returns a string", lambda: _expect(backend.evaluate("1 + 2"), "3"))
        step("evaluate raises on a JavaScript error, then still works", lambda: _js_error(backend))
        step("typeof WebAssembly", lambda: _expect(backend.evaluate("typeof WebAssembly"), "object"))
        step("BigInt (needed for i64)", lambda: _expect(backend.evaluate("String(2n ** 62n + 1n)"), str(2**62 + 1)))
        step("engine", lambda: backend.evaluate("typeof process === 'undefined' ? 'bare engine' : 'node'"))
        step("bytes in and out of the engine", lambda: _bytes(backend))

    box: dict[str, Instance] = {}

    def build() -> str:
        if not validate(MODULE, backend=backend):
            raise AssertionError("validate() said no")
        module = Module(MODULE, backend=backend)
        box["i"] = Instance(module)
        return f"{len(Module.exports(module))} exports"

    step("validate, compile, instantiate", build)
    if "i" not in box:
        out("  (nothing more can be checked without an instance)")
        return report
    ex = box["i"].exports
    step("i32 add, wraps like JavaScript", lambda: _expect((ex.add(2, 3), ex.add(2**31 - 1, 1)), (5, -(2**31))))
    step("i64 is exact", lambda: _expect(ex.add64(2**62 + 12345, 1), 2**62 + 12346))
    step("i64 wraps", lambda: _expect(ex.add64(-(2**63), -1), 2**63 - 1))
    step("f64, f32", lambda: _expect((ex.fadd(0.1, 0.2), ex.twice(1.5)), (0.1 + 0.2, 3.0)))
    step("NaN, infinity, -0.0", lambda: _floats(ex))
    step("multi-value result", lambda: _expect(ex.dup(41), (41, 41)))
    step("argument checks", lambda: _arguments(ex))
    step("memory write/read/slice", lambda: _memory(ex))
    step("memory out of bounds is an IndexError", lambda: _bounds(ex))
    step("module's own memory.grow", lambda: _expect((ex.grow(1), len(ex.memory)), (1, 2 * 65536)))
    step("Memory.grow from Python", lambda: _grow(backend, ex))
    step("globals", lambda: _globals(ex))
    step("a trap is a Trap, and the instance survives", lambda: _trap(ex))
    step("bad bytes are a CompileError", lambda: _compile_error(backend))
    step("batch: chained steps", lambda: _batch(box["i"]))
    step("batch: an error keeps the earlier results", lambda: _batch_error(box["i"]))
    step("host functions (Python called from the module)", lambda: _host_functions(backend))
    step("WASI: a program of each snapshot, with arguments, stdout, a file and an exit code", lambda: _wasi(backend))
    step("globals: made on their own, imported, shared", lambda: _globals_on_their_own(backend))
    step("memory: made on its own, imported, shared", lambda: _memory_on_its_own(backend))
    step("memory: the maximum of an imported memory stops the module's grow", lambda: _memory_ceiling(backend))
    step("memory: Instance(max_memory=) is a ceiling for a memory the module makes", lambda: _max_memory(backend))
    step("timeout: Instance(timeout=) stops an endless loop, where the engine can", lambda: _timeout(backend))
    step("fuel: Instance(fuel=) stops an endless loop, where the engine can count", lambda: _fuel(backend))
    step("table: made on its own, imported, shared", lambda: _table_on_its_own(backend))
    step("functions: one object per function, signature, a table entry", lambda: _functions(backend))
    step("async compile and instantiate, tasks at once", lambda: _async(backend))
    step("threads: one backend, several threads, one call at a time", lambda: _threads(backend))
    step("memory view: the engine's memory without a copy", lambda: _memory_view(backend, box["i"]))
    step("custom sections", lambda: _custom_sections(backend))
    step("type reflection: type() of a function, memory, table and global", lambda: _types(backend))
    step("an isolated instance", lambda: _isolated(backend))
    step("exception handling (which encodings the engine takes)", lambda: _exceptions(backend))
    step("call cost", lambda: _timing(backend, box["i"]))
    return report


def _bytes(backend: JSBackend) -> str:
    data = bytes(range(256)) * 64 + b"\x00\xff"
    how = backend.put_bytes("globalThis.__wasmhost_test", data)
    _expect(backend.evaluate("String(__wasmhost_test.length)"), str(len(data)))
    _expect(backend.get_bytes("__wasmhost_test"), data)
    _expect(backend.get_bytes("__wasmhost_test.subarray(3, 7)"), data[3:7])  # a view, not a whole buffer
    backend.put_bytes("globalThis.__wasmhost_test", b"")
    _expect(backend.get_bytes("__wasmhost_test"), b"")
    backend.evaluate("delete globalThis.__wasmhost_test")
    return f"{len(data)} bytes via {how}"


def _host_functions(backend: Backend) -> str:
    if not backend.supports("imports"):
        try:
            Instance(Module(CALLBACKS, backend=backend), _imports([]))
        except NotImplementedError:
            return "not available on this backend, as documented"
        raise AssertionError("should be NotImplementedError")
    calls: list[str] = []
    imports = _imports(calls)
    ex = Instance(Module(CALLBACKS, backend=backend), imports).exports
    _expect(ex.call_plus(2, 3), 5)
    _expect(ex.twice(10), 12)
    _expect(calls, ["plus(2, 3)", "plus(10, 1)", "plus(11, 1)"])
    _expect(ex.call_half(5.0), 2.5)
    _expect(ex.call_pair(7), (7, 8))
    ex.call_note(2**62 + 1)  # an i64 arrives exactly
    _expect(calls[-1], f"note({2**62 + 1})")
    box: dict[str, Any] = {}

    def again(a: int, b: int) -> int:  # a host function that calls the module again
        return int(box["ex"].call_plus(1, 2)) if a < 0 else a + b

    def failing(a: int, b: int) -> int:
        raise KeyError("from a host function")

    imports["env"]["plus"] = failing
    other = Instance(Module(CALLBACKS, backend=backend), imports).exports
    try:
        other.call_plus(1, 2)
    except KeyError:
        pass
    else:
        raise AssertionError("the exception of a host function did not come out")
    _expect(other.call_half(3.0), 1.5)  # and the instance is still good
    imports["env"]["plus"] = again
    box["ex"] = Instance(Module(CALLBACKS, backend=backend), imports).exports
    _expect(box["ex"].call_plus(-1, 0), 3)  # the nested call goes through the module and back
    return "a call, nested calls, i64, several results, an exception, re-entry"


def _wasi(backend: Backend) -> str:
    """A WASI command through `wasmhost.wasi.preview1`, once from each snapshot (`wasi_snapshot_preview1` and the first,
    `wasi_unstable`): its arguments, standard output, a file made in a folder of the host, and its exit code, which
    comes out of the program as an exception of the host function and must cross the engine. Then once more with the
    folder read-only: the file is refused and the folder stays empty."""
    if not backend.supports("imports"):
        try:
            Instance(Module(WASI_HELLO, backend=backend), Preview1().imports())
        except NotImplementedError:
            return "not available on this backend, as documented"
        raise AssertionError("should be NotImplementedError")
    for wasm in (WASI_HELLO, WASI_HELLO_UNSTABLE):
        out: list[bytes] = []
        with tempfile.TemporaryDirectory() as folder:
            wasi = Preview1(args=["selftest", "x"], preopens={"/": folder}, stdout=out.append)
            _expect(wasi.run(Module(wasm, backend=backend)), 2)  # proc_exit(argc)
            _expect(b"".join(out), b"hello, wasi\n")
            with open(os.path.join(folder, "note.txt"), "rb") as note:
                _expect(note.read(), b"hello, wasi\n")
    out = []
    with tempfile.TemporaryDirectory() as folder:  # the same program on a folder that nothing can be written to
        host = Preview1(args=["selftest", "x"], preopens={"/": folder}, stdout=out.append, readonly=True)
        _expect(host.run(Module(WASI_HELLO, backend=backend)), 2)
        _expect(b"".join(out), b"hello, wasi\n")
        _expect(os.listdir(folder), [])  # note.txt was refused
    return (
        "arguments, stdout, a file in a temporary folder, the exit code; both snapshots; a read-only folder stays empty"
    )


def _imports(calls: list[str]) -> dict[str, dict[str, Any]]:
    def plus(a: int, b: int) -> int:
        calls.append(f"plus({a}, {b})")
        return a + b

    def note(x: int) -> None:
        calls.append(f"note({x})")

    def half(x: float) -> float:
        return x / 2

    def pair(x: int) -> tuple[int, int]:
        return (x, x + 1)

    return {"env": {"plus": plus, "note": note, "half": half, "pair": pair}}


def _globals_on_their_own(backend: Backend) -> str:
    if not backend.supports("import.global"):
        try:
            Global("i32", 0, backend=backend)
        except NotImplementedError:
            return "not available on this backend, as documented"
        raise AssertionError("should be NotImplementedError")
    module = Module(GLOBAL_IMPORT, backend=backend)
    g = Global("i32", 10, mutable=True, backend=backend)
    a = Instance(module, {"env": {"g": g}}).exports
    b = Instance(module, {"env": {"g": g}}).exports
    a.inc()
    b.inc()
    _expect((a.get(), b.get(), g.value), (12, 12, 12))  # two instances and the host, one global
    g.value = 40
    _expect(a.get(), 40)
    donor = Instance(Module(MODULE, backend=backend)).exports  # an exported global goes into another instance
    taker = Instance(module, {"env": {"g": donor.counter}}).exports
    taker.inc()
    _expect(donor.counter.value, 8)
    try:
        Global("i32", 0, backend=backend).value = 1  # immutable
    except TypeError:
        pass
    else:
        raise AssertionError("an immutable global was written")
    try:
        Instance(module, {"env": {"g": 5}})
    except LinkError:
        return "shared by two instances, the host and an export; a wrong import is a LinkError"
    raise AssertionError("a number was taken for a mutable global")


def _memory_on_its_own(backend: Backend) -> str:
    if not backend.supports("import.memory"):
        try:
            Memory(1, backend=backend)
        except NotImplementedError:
            return "not available on this backend, as documented"
        raise AssertionError("should be NotImplementedError")
    mem = Memory(1, 3, backend=backend)
    _expect(len(mem), 65536)
    mem.write(10, b"abc")
    _expect(mem.read(10, 3), b"abc")
    module = Module(MEMORY_USER, backend=backend)
    a = Instance(module, {"env": {"memory": mem}}).exports
    b = Instance(module, {"env": {"memory": mem}}).exports
    a.store(5, 42)
    _expect((b.load(5), mem.read(5, 1)), (42, b"\x2a"))  # two instances and the host, one memory
    mem.write(6, b"\x07")
    _expect(a.load(6), 7)
    if backend.supports("memory.grow"):
        _expect((mem.grow(1), len(mem), mem.read(10, 3)), (1, 2 * 65536, b"abc"))
        try:
            mem.grow(5)  # over its maximum
        except IndexError:
            pass
        else:
            raise AssertionError("a memory grew past its maximum")
    for wrong in (Memory(0, backend=backend), 1):  # smaller than the module wants, and not a memory at all
        try:
            Instance(module, {"env": {"memory": wrong}})
        except LinkError:
            pass
        else:
            raise AssertionError(f"{wrong!r} was taken for the memory of the module")
    return "shared by two instances and the host; the limits and a wrong import are checked"


def _memory_ceiling(backend: Backend) -> str:
    if not backend.supports("import.memory"):
        return "not available on this backend, as documented"
    mem = Memory(1, 3, backend=backend)
    ex = Instance(Module(GROWS_MEMORY, backend=backend), {"env": {"memory": mem}}).exports
    _expect((ex.grow(2), ex.size()), (1, 3))  # up to the maximum
    _expect((ex.grow(1), ex.grow(65536), ex.size(), len(mem)), (-1, -1, 3, 3 * 65536))  # past it: -1, nothing changes
    mem.write(3 * 65536 - 1, b"\x07")
    _expect(mem.read(3 * 65536 - 1, 1), b"\x07")
    return "grow past the maximum answers -1; the memory and the instance are fine"


def _max_memory(backend: Backend) -> str:
    module = Module(OWNS_MEMORY, backend=backend)
    ex = Instance(module, max_memory=3).exports
    _expect((ex.grow(2), ex.size()), (1, 3))  # no maximum of its own: the ceiling is one, and it is reached
    _expect((ex.grow(1), ex.grow(65536), ex.size(), len(ex.memory)), (-1, -1, 3, 3 * 65536))  # past it: -1, as is
    _expect(ex.memory.type().maximum, 3)
    _expect(Instance(module).exports.grow(5), 1)  # the module itself has no ceiling, whatever an instance was given
    try:
        Instance(module, max_memory=0)  # it starts at 1 page
    except ValueError:
        pass
    else:
        raise AssertionError("a module that starts over the ceiling was taken")
    return "grow past max_memory answers -1; type() says the maximum that holds; the module is unchanged"


def _timeout(backend: Backend) -> str:
    module = Module(SPINNER, backend=backend)
    if not backend.supports("timeout"):  # never run the loop here: nothing would stop it
        try:
            Instance(module, timeout=1)
        except NotImplementedError:
            return "not available on this backend, as documented"
        raise AssertionError("an instance with a timeout was made where nothing can stop a call")
    ex = Instance(module, timeout=0.2).exports
    _expect(ex.quick(), 7)
    try:
        ex.spin()
    except Timeout:
        pass
    else:
        raise AssertionError("an endless loop came back")
    if backend.name == "wasm3":  # a call that paused can't be cancelled there: the instance is finished, and says so
        try:
            ex.quick()
        except Trap:
            return "an endless loop ended with a Timeout; wasm3 refuses the instance after it, as documented"
        raise AssertionError("a wasm3 instance that timed out ran again")
    _expect((ex.quick(), ex.busy(1000)), (7, 1000))  # the instance is fine, and callable again
    return "an endless loop ended with a Timeout; the instance is fine"


def _fuel(backend: Backend) -> str:
    module = Module(SPINNER, backend=backend)
    if not backend.supports("fuel"):  # never run the loop here: nothing would stop it
        try:
            Instance(module, fuel=1000)
        except NotImplementedError:
            return "not available on this backend, as documented"
        raise AssertionError("an instance with fuel was made where nothing can count a call")
    ex = Instance(module, fuel=20_000).exports
    _expect(ex.quick(), 7)
    try:
        ex.spin()
    except OutOfFuel:
        pass
    else:
        raise AssertionError("an endless loop came back")
    _expect(
        (ex.quick(), ex.busy(100)), (7, 100)
    )  # a trap of the engine's own: the instance is fine, and the fuel is back
    return "an endless loop ended with an OutOfFuel; the instance is fine"


def _table_on_its_own(backend: Backend) -> str:
    if not (backend.supports("import.table") and backend.supports("table.funcs")):
        try:
            Table("funcref", 1, backend=backend)
        except NotImplementedError:
            return "not available on this backend, as documented"
        raise AssertionError("should be NotImplementedError")
    table = Table("funcref", 2, 4, backend=backend)
    _expect((len(table), table.get(0)), (2, None))
    provider = Instance(Module(TABLE_OWN, backend=backend)).exports
    user = Instance(Module(TABLE_IMPORT, backend=backend), {"env": {"table": table}}).exports
    table.set(0, provider.add)
    table.set(1, provider.mul)
    _expect((user.call(6, 7, 0), user.call(6, 7, 1)), (13, 42))  # call_indirect into another instance's functions
    table.set(1, None)
    try:
        user.call(1, 2, 1)  # a null entry
    except Trap:
        pass
    else:
        raise AssertionError("a null entry was called")
    _expect((table.grow(1), len(table)), (2, 3))
    _expect((provider.table.grow(3), len(provider.table)), (2, 5))  # a table the module exports grows too
    filled = Table("funcref", 2, 4, provider.add, backend=backend)  # every entry starts as `add`, as in JavaScript
    _expect((filled.get(0) is provider.add, filled.get(1) is provider.add), (True, True))
    _expect(
        (filled.grow(1, provider.mul), filled.get(2) is provider.mul, filled.get(0) is provider.add), (2, True, True)
    )
    try:
        Table("externref", 1, backend=backend)
    except NotImplementedError:
        return "functions shared across instances; an externref table is not supported yet, as documented"
    raise AssertionError("an externref table should be NotImplementedError")


def _functions(backend: Backend) -> str:
    """A function is one object (`table.get(0) is exports.add`), its signature is known for an export, can be given
    by hand for a table entry the engine does not describe, and a wrong one is refused when it contradicts."""
    if not (backend.supports("import.table") and backend.supports("table.funcs")):
        return "no functions in tables on this backend"
    provider = Instance(Module(TABLE_OWN, backend=backend)).exports
    add = provider.add
    add_type = FuncType((i32, i32), (i32,))
    _expect((provider.add is add, add.signature, add.type()), (True, add_type, add_type))
    table = Table("funcref", 2, backend=backend)
    table.set(0, add)
    entry = table.get(0)
    _expect((entry is add, entry == add, hash(entry) == hash(add)), (True, True, True))
    _expect(provider.mul == add, False)
    try:
        add.signature = FuncType((i32,), (i32,))
    except ValueError:
        pass
    else:
        raise AssertionError("a signature that contradicts the known one was accepted")
    fresh = Table("funcref", 1, backend=backend)
    fresh.set(0, add)
    other = fresh.get(0)
    assert other is not None
    if other.signature is None:
        try:
            other.type()
        except ValueError:
            other.signature = add_type  # the engine does not say: given by hand
    _expect(other(2, 3), 5)
    entry = Instance(Module(TABLE_ELEM, backend=backend)).exports.table.get(1)  # put there by an `elem` segment
    assert entry is not None
    _expect((entry.signature, entry(6, 7)), (add_type, 42))  # known without being told, on every backend
    checked = "a wrong signature refused"
    if not backend.supports("table.signatures"):  # an engine that does not tell types: the call is checked by it
        unknown = Instance(Module(TABLE_ELEM_START, backend=backend)).exports.table.get(0)
        assert unknown is not None and unknown.signature is None
        unknown.signature = FuncType((i64,), (i64,))  # it is (i32, i32) -> i32
        try:
            unknown(1)
        except TypeError:
            pass
        else:
            raise AssertionError("a function was called as another type")
        unknown.signature = add_type
        _expect(unknown(1, 2), 3)
        checked = "a wrong signature refused by the engine"
    return f"identity, equality, signature rules, a function of an elem segment, {checked}; a table entry called"


def _memory_view(backend: Backend, instance: Instance) -> str:
    """`Memory.view` is the memory itself, and is unusable (a ValueError) once the module may have run."""
    if not backend.supports("memory.view"):
        try:
            instance.exports.memory.view()
        except NotImplementedError:
            return "not available on this backend (a JavaScript engine's memory is not in this process), as documented"
        raise AssertionError("a view of the memory was given where there is none")
    memory = instance.exports.memory
    view = memory.view(100, 4)
    view[:] = b"view"
    _expect(memory.read(100, 4), b"view")  # written through the view, read through the engine
    instance.exports.add(1, 2)
    try:
        view[0]
    except ValueError:
        return "writes through to the memory; released when the module runs, so never a stale address"
    raise AssertionError("a view of the memory survived a call into the module")


def _threads(backend: Backend) -> str:
    """Several threads calling into one backend each get right answers (its lock lets one in at a time)."""
    try:
        import threading
    except ImportError:
        return "skipped: this Python has no threads"
    ex = Instance(Module(MODULE, backend=backend)).exports
    wrong: list[str] = []

    def work(seed: int) -> None:
        for i in range(50):
            if ex.add(seed, i) != seed + i:
                wrong.append(f"{seed}+{i}")

    threads = [threading.Thread(target=work, args=(n * 1000,)) for n in range(4)]
    try:
        for t in threads:
            t.start()
    except RuntimeError:
        return "skipped: this Python can't start a thread"
    for t in threads:
        t.join()
    _expect(wrong, [])
    return "four threads, 50 calls each, all right"


def _async(backend: Backend) -> str:
    """`await compile(...)` and `await instantiate(...)`, many tasks at once: each gets its own right answer."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        return "skipped: an event loop is already running here (asyncio.run can't nest)"

    async def one(n: int, threaded: bool) -> int:
        module = await compile_async(MODULE, backend=backend, threaded=threaded)
        instance = await instantiate_async(module, backend=backend, threaded=threaded)
        assert isinstance(instance, Instance)
        return int(instance.exports.add(n, n))

    async def main(threaded: bool) -> list[int]:
        return list(await asyncio.gather(*(one(n, threaded) for n in range(6))))

    _expect(asyncio.run(main(False)), [0, 2, 4, 6, 8, 10])  # the default: no thread
    where = "in place, a turn of the loop around it"
    if backend.supports("threads"):
        _expect(asyncio.run(main(True)), [0, 2, 4, 6, 8, 10])
        where += "; and in worker threads when asked for"
    return f"six tasks at once; {where}"


def _custom(name: str, payload: bytes) -> bytes:
    """A custom section (id 0), for a name and payload short enough for a one-byte size."""
    body = bytes([len(name)]) + name.encode() + payload
    assert len(body) < 128
    return b"\x00" + bytes([len(body)]) + body


def _custom_sections(backend: Backend) -> str:
    module = Module(MODULE + _custom("mine", b"hello") + _custom("mine", b"again"), backend=backend)
    _expect(Module.customSections(module, "mine"), [b"hello", b"again"])
    _expect(Module.customSections(module, "absent"), [])
    _expect(Instance(module).exports.add(2, 3), 5)  # a custom section does not get in the way
    return "two of one name, none of another"


def _types(backend: Backend) -> str:
    """The type reflection of the JavaScript API: `type()` of a function, a memory and a global, and, where the
    backend makes them on its own, of a memory, a table and a global from a descriptor."""
    ex = Instance(Module(MODULE, backend=backend)).exports
    _expect(ex.add.type(), FuncType((i32, i32), (i32,)))  # the types are their names, so both spellings do
    _expect(repr(ex.add.type()), "FuncType(parameters=(i32, i32), results=(i32,))")
    _expect(ex.memory.type(), MemoryType(1, 4, False))
    _expect((ex.counter.type(), ex.ten.type()), (GlobalType("i32", True), GlobalType("i32", False)))
    _expect(ex.grow(1), 1)  # the module's own memory.grow
    _expect(ex.memory.type(), MemoryType(2, 4, False))  # the minimum is the size now
    made: list[str] = []
    if backend.supports("import.memory"):
        _expect(Memory({"initial": 1, "maximum": 3}, backend=backend).type(), MemoryType(1, 3, False))
        made.append("memory")
    if backend.supports("import.table"):
        _expect(Table({"element": "anyfunc", "initial": 2}, backend=backend).type(), TableType("funcref", 2, None))
        made.append("table")
    if backend.supports("import.global"):
        _expect(Global({"value": "f64", "mutable": True}, 1.5, backend=backend).type(), GlobalType("f64", True))
        made.append("global")
    return "from a descriptor: " + (", ".join(made) or "nothing on this backend")


def _exceptions(backend: Backend) -> str:
    """Which encodings of WebAssembly exceptions the engine takes: a module that uses them (C++ built with wasi-sdk's
    exceptions) needs the one it was built with. An engine without an encoding just refuses the module, which is an
    answer; one that takes it must catch what it should and let the rest out."""
    found: list[str] = []
    for label, wasm in (("final (try_table)", EXCEPTIONS_FINAL), ("older (try/catch)", EXCEPTIONS_LEGACY)):
        try:
            ex = Instance(Module(wasm, backend=backend)).exports
            caught = ex.caught()  # (wasm3 compiles a function when it is first called, and refuses it there)
        except Exception:  # noqa: BLE001 -- an engine without it refuses the module, in whatever way
            found.append(f"{label}: no")
            continue
        _expect(caught, 42)
        try:
            got = ex.uncaught()
        except Exception:  # noqa: BLE001 -- a Trap, or the engine's own exception: it came out, as it should
            found.append(f"{label}: yes")
        else:
            raise AssertionError(f"{label}: an exception of another tag was swallowed (uncaught() returned {got!r})")
    return ", ".join(found)


def _isolated(backend: Backend) -> str:
    if not backend.supports("isolated"):
        try:
            Instance(Module(MODULE, backend=backend), isolated=True)
        except NotImplementedError:
            return "not available on this backend, as documented"
        raise AssertionError("should be NotImplementedError")
    a = Instance(Module(MODULE, backend=backend), isolated=True).exports
    b = Instance(Module(MODULE, backend=backend), isolated=True).exports
    a.counter.value = 50
    _expect((a.add(1, 2), b.counter.value), (3, 7))  # nothing shared
    return "its own store: nothing shared with the others"


def _js_error(backend: JSBackend) -> None:
    try:
        backend.evaluate("throw new Error('boom')")
    except RuntimeError as exc:
        if "boom" not in str(exc):
            raise AssertionError(f"the error text was lost: {exc}") from None
    else:
        raise AssertionError("no exception")
    _expect(backend.evaluate("'after'"), "after")


def _floats(ex: Any) -> None:
    if not math.isnan(ex.fadd(math.nan, 1.0)):
        raise AssertionError("NaN lost")
    _expect(ex.fadd(math.inf, 1.0), math.inf)
    _expect(str(ex.fadd(-0.0, -0.0)), "-0.0")


def _arguments(ex: Any) -> None:
    for bad in ((1,), (1.5, 2), ("1", 2)):
        try:
            ex.add(*bad)
        except TypeError:
            continue
        raise AssertionError(f"add{bad} was accepted")


def _memory(ex: Any) -> None:
    mem = ex.memory
    mem.write(100, b"hello")
    _expect((mem.read(100, 5), mem[100:105], mem[100]), (b"hello", b"hello", ord("h")))
    ex.store8(200, 0x1FF)
    _expect(mem[200], 0xFF)


def _bounds(ex: Any) -> None:
    for op in (lambda: ex.memory.read(len(ex.memory) - 1, 2), lambda: ex.memory.write(len(ex.memory) - 1, b"xx")):
        try:
            op()
        except IndexError:
            continue
        raise AssertionError("no IndexError")


def _grow(backend: Backend, ex: Any) -> str:
    if not backend.supports("memory.grow"):
        try:
            ex.memory.grow(1)
        except NotImplementedError:
            return "not available on this backend, as documented"
        raise AssertionError("should be NotImplementedError")
    before = len(ex.memory)
    _expect(ex.memory.grow(1), before // 65536)
    return "grown"


def _globals(ex: Any) -> None:
    _expect((ex.counter.value, ex.ten.value), (7, 11))
    ex.counter.value = 99
    _expect(ex.counter.value, 99)
    try:
        ex.ten.value = 1
    except TypeError:
        return
    raise AssertionError("an immutable global was written")


def _trap(ex: Any) -> None:
    try:
        ex.trap()
    except Trap:
        _expect(ex.add(1, 1), 2)
        return
    raise AssertionError("no Trap")


def _compile_error(backend: Backend) -> None:
    for junk in (b"not wasm", MODULE[:-3]):
        try:
            Module(junk, backend=backend)
        except CompileError:
            continue
        raise AssertionError(f"{len(junk)} bytes of junk were accepted")


def _batch(instance: Instance) -> None:
    ex = instance.exports
    b = instance.batch()
    three = b.call(ex.add, 1, 2)
    b.call(ex.store8, three * 100, 0x41)
    b.write(ex.memory, three + 397, b"xyz")
    back = b.read(ex.memory, three * 100, 1)
    text = b.read(ex.memory, three + 397, three)
    first, second = b.call(ex.dup, three)  # a multi-value result: one Ref for each, usable by the next step
    again = b.call(ex.add, first, second)
    b.run()
    _expect((three.value, back.value, text.value), (3, b"A", b"xyz"))
    _expect((first.value, second.value, again.value), (3, 3, 6))


def _batch_error(instance: Instance) -> None:
    ex = instance.exports
    b = instance.batch()
    ok = b.call(ex.add, 1, 1)
    b.call(ex.trap)
    later = b.call(ex.add, 2, 2)
    try:
        b.run()
    except Trap:
        _expect((ok.value, later.done), (2, False))
        return
    raise AssertionError("no Trap")


def _timing(backend: Backend, instance: Instance) -> str:
    ex = instance.exports
    n = 200
    t0 = time.perf_counter()
    for _ in range(n):
        ex.add(1, 2)
    single = (time.perf_counter() - t0) / n
    t0 = time.perf_counter()
    for _ in range(n):
        b = instance.batch()
        b.call(ex.add, 1, 2)
        b.call(ex.add, 3, 4)
        b.call(ex.add, 5, 6)
        b.run()
    batch = (time.perf_counter() - t0) / n
    return f"a call {single * 1e6:.0f} us; a batch of 3 {batch * 1e6:.0f} us"


def selftest(backend: Backend | str | None = None, out: Callable[[str], object] = print) -> bool:
    """Run the checks on `backend` (a name, an instance, or None for the default). Returns True if all passed."""
    out("wasmhost self-test")
    _environment(out)
    try:
        started = get_backend(backend)
    except Exception as exc:  # noqa: BLE001
        out(f"  FAIL  no backend could start: {type(exc).__name__}: {exc}")
        return False
    report = _selftest_backend(started, out)
    total = report.passed + len(report.failed)
    out(f"{report.passed}/{total} passed" + (f"; failed: {', '.join(report.failed)}" if report.failed else ""))
    return not report.failed


DESCRIPTION = "Check that wasmhost works here."


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--backend",
        "--runtime",
        choices=sorted(BACKENDS),
        help="one backend (default: $WASMHOST_BACKEND, else the first that starts)",
    )
    parser.add_argument("--all", action="store_true", help="every backend that starts here")


def run(args: argparse.Namespace) -> int:
    """The `selftest` command, with the arguments `add_arguments` made: 0 if everything passed, else 1."""
    if args.all:
        ok = True
        for name in AUTO_ORDER:
            try:
                BACKENDS[name]().close()
            except Exception as exc:  # noqa: BLE001 -- not available here
                print(f"-- {name}: not available ({type(exc).__name__}: {exc})")
                continue
            print(f"-- {name}")
            ok = selftest(name) and ok
        return 0 if ok else 1
    return 0 if selftest(args.backend) else 1
