"""What pywasm3's own tests (https://github.com/wasm3/pywasm3/tree/main/tests) ask of a WebAssembly host, done through
wasmhost on every backend: the same modules, the same expectations, the JavaScript API's names."""

from __future__ import annotations

import base64
import struct

import pytest
import wasm_builder as wb

import wasmhost

# pywasm3's test_smoke.py: (func $fib (export "fib") (param i64) (result i64) ...), and a page of memory with
# grow(n) -> i32, store(addr, byte) and load(addr) -> i32
FIB_WASM = base64.b64decode("AGFzbQEAAAABBgFgAX4BfgMCAQAHBwEDZmliAAAKHwEdACAAQgJUBEAgAA8LIABCAn0QACAAQgF9EAB8Dws=")
MEM_WASM = base64.b64decode(
    "AGFzbQEAAAABCwJgAX8Bf2ACf38AAwQDAAEABQMBAAEHIAQGbWVtb3J5AgAEZ3JvdwAABXN0b3JlAAEEbG9hZAACChoD"
    "BgAgAEAACwkAIAAgAToAAAsHACAALQAACw=="
)
PAGE = 65536


def test_call_a_precompiled_module(session: str) -> None:
    instance = wasmhost.Instance(wasmhost.Module(FIB_WASM))
    fib = instance.exports.fib
    assert fib(24) == 46368
    assert fib(0) == 0
    assert fib(1) == 1


def test_function_introspection(session: str) -> None:
    fib = wasmhost.Instance(wasmhost.Module(FIB_WASM)).exports.fib
    assert (fib.name, fib.params, fib.results) == ("fib", ("i64",), ("i64",))
    (export,) = wasmhost.Module.exports(wasmhost.Module(FIB_WASM))
    assert (export.name, export.kind) == ("fib", "function")


def _mem() -> wasmhost.Instance:
    return wasmhost.Instance(wasmhost.Module(MEM_WASM))


def test_memory_is_shared_with_wasm(session: str) -> None:
    instance = _mem()
    mem = instance.exports.memory
    assert len(mem) == PAGE
    mem[10] = 42
    assert instance.exports.load(10) == 42
    instance.exports.store(11, 7)
    assert mem[11] == 7
    mem[20:23] = b"abc"
    assert mem[20:23] == b"abc"
    assert mem[-1] == 0
    with pytest.raises(IndexError):
        mem[PAGE]


def test_memory_slices_are_copies(session: str) -> None:
    mem = _mem().exports.memory
    snapshot = mem[0:4]
    assert isinstance(snapshot, bytes)
    mem[0] = 1
    assert snapshot == b"\0\0\0\0"


def test_structs_through_memory(session: str) -> None:
    mem = _mem().exports.memory
    mem.write(100, struct.pack("<I", 0xDEADBEEF))
    assert struct.unpack("<I", mem.read(100, 4)) == (0xDEADBEEF,)


def test_memory_survives_the_modules_own_grow(session: str) -> None:
    instance = _mem()
    mem = instance.exports.memory
    mem[10] = 42
    assert instance.exports.grow(2) == 1
    assert len(mem) == 3 * PAGE
    assert mem[10] == 42
    mem[-1] = 9
    assert instance.exports.load(3 * PAGE - 1) == 9


def test_multivalue_results(session: str) -> None:
    instance = wasmhost.Instance(wasmhost.Module(wb.swaps(imported=False)))
    assert instance.exports.swap(1, 2) == (2, 1)
    assert instance.exports.swap(2, 1) == (1, 2)


def test_multivalue_through_an_import(session: str) -> None:
    if not wasmhost.get_backend().supports("imports"):
        pytest.skip("no host functions on this backend")
    instance = wasmhost.Instance(wasmhost.Module(wb.swaps()), {"env": {"swap": lambda a, b: (b, a)}})
    assert instance.exports.swap_imported(1, 2) == (2, 1)
    assert instance.exports.swap_imported(2, 1) == (1, 2)


def test_a_bound_method_is_a_host_function(session: str) -> None:
    if not wasmhost.get_backend().supports("imports"):
        pytest.skip("no host functions on this backend")

    class Runner:
        def __init__(self) -> None:
            imports = {"env": {"plus": self.plus, "note": print, "half": float, "pair": lambda x: (x, x)}}
            self.instance = wasmhost.Instance(wasmhost.Module(wb.callbacks()), imports)

        def plus(self, x: int, y: int) -> int:
            assert (x, y) == (987, 654)
            return x + y

    assert Runner().instance.exports.call_plus(987, 654) == 987 + 654


def test_an_immutable_f32_global_import(session: str) -> None:
    if not wasmhost.get_backend().supports("import.global"):
        pytest.skip("no globals on their own on this backend")
    module = wasmhost.Module(wb.imports_global(wb.F32, mutable=False))
    instance = wasmhost.Instance(module, {"env": {"g": 22050}})
    assert instance.exports.get() == pytest.approx(22050.0)


def test_dynamic_callback_through_the_table(session: str) -> None:
    """A host function that gets a table index and calls back into the module to run what is there."""
    backend = wasmhost.get_backend()
    if not backend.supports("imports") or not backend.supports("table.length"):
        pytest.skip("no host functions or tables on this backend")
    seen: list[int] = []
    box: dict[str, wasmhost.Instance] = {}

    def pass_fptr(fptr: int) -> None:
        seen.append(fptr)
        expected = {0: 12 + 34, 1: 12 * 34}[fptr]
        assert box["instance"].exports.dynCall_iii(fptr, 12, 34) == expected

    instance = wasmhost.Instance(
        wasmhost.Module(wb.dyn_callback()), {"env": {"pass_fptr": pass_fptr, "__table_base": 0}}
    )
    box["instance"] = instance
    assert len(instance.exports.table) == 2
    instance.exports.run_test()
    assert seen == [0, 1]
    instance.exports.call_pass_fptr(1)
    assert seen == [0, 1, 1]
