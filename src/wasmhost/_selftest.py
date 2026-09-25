"""A self-test to run on the device: `python -m wasmhost`, or `wasmhost.selftest()` from a console.

Made for Pythonista (or any iOS Python app), where nothing else can be run to see whether wasmhost works: it
walks through the things that could go wrong there -- the Objective-C bridge, `WebAssembly` and `BigInt` in the
engine, calls, memory, globals, traps, batches -- prints one line for each, and ends with a summary and the
cost of a call. If something fails, send the whole output.
"""

from __future__ import annotations

import argparse
import importlib.util
import math
import platform
import sys
import sysconfig
import time
import traceback
from collections.abc import Callable, Sequence
from typing import Any

from ._api import Global, Instance, Module, get_backend, validate
from ._backend import Backend
from ._errors import CompileError, LinkError, Trap
from ._js import JSBackend
from ._registry import AUTO_ORDER, BACKENDS

__all__ = ("main", "selftest")

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
    step("globals: made on their own, imported, shared", lambda: _globals_on_their_own(backend))
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
    b.run()
    _expect((three.value, back.value, text.value), (3, b"A", b"xyz"))


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


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m wasmhost", description="Check that wasmhost works here.")
    ap.add_argument("--backend", choices=sorted(BACKENDS), help="one backend (default: the first that starts)")
    ap.add_argument("--all", action="store_true", help="every backend that starts here")
    args = ap.parse_args(argv)
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
