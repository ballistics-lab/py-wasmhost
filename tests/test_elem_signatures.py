"""A function put in a table by the module's own `elem` segment has its signature found on every backend."""

from __future__ import annotations

import pytest
import wasm_builder as wb

import wasmhost
from wasmhost import FuncType, i32
from wasmhost._binary import parse

ADD = FuncType((i32, i32), (i32,))


def module(start: bool = False) -> bytes:
    """Two functions (add, mul), neither exported, in the slots 0 and 1 of an exported table by an `elem` segment;
    with `start` the module also has a start function (an empty one)."""
    types = [wb.functype([wb.I32, wb.I32], [wb.I32]), wb.functype([], [])]
    out = b"\0asm\x01\x00\x00\x00" + wb.section(1, wb.vec(types))
    out += wb.section(3, wb.vec([wb.uleb(0), wb.uleb(0), wb.uleb(1)]))
    out += wb.section(4, wb.vec([b"\x70\x00\x02"]))
    out += wb.section(7, wb.vec([wb.name("table") + b"\x01\x00"]))
    if start:
        out += wb.section(8, wb.uleb(2))
    out += wb.section(9, wb.vec([b"\x00\x41\x00\x0b" + wb.vec([wb.uleb(0), wb.uleb(1)])]))
    out += wb.section(10, wb.vec([wb.body(b"\x20\x00\x20\x01\x6a"), wb.body(b"\x20\x00\x20\x01\x6c"), wb.body(b"")]))
    return out


def test_the_elem_segment_is_read() -> None:
    info = parse(module())
    assert info.elems == ((0, 0, ADD), (0, 1, ADD)) and info.table_exports == (("table", 0),) and not info.has_start
    assert parse(module(start=True)).has_start


@pytest.fixture
def engine(session: str) -> wasmhost.Backend:
    b = wasmhost.get_backend()
    if not b.supports("table.funcs"):
        pytest.skip(f"the {b.name} backend has no table.funcs")
    return b


def test_a_function_from_elem_knows_its_type(engine: wasmhost.Backend) -> None:
    table = wasmhost.Instance(wasmhost.Module(module())).exports.table
    add, mul = table.get(0), table.get(1)
    assert add is not None and mul is not None
    assert add.signature == ADD and mul.signature == ADD
    assert (add(2, 3), mul(2, 3)) == (5, 6)


def test_a_module_with_a_start_function_is_not_assumed(engine: wasmhost.Backend) -> None:
    if engine.name == "wasmtime":
        pytest.skip("wasmtime knows the types itself")
    entry = wasmhost.Instance(wasmhost.Module(module(start=True))).exports.table.get(0)
    assert entry is not None and entry.signature is None
