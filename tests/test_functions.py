"""`Function`: one object per function of the engine, its `signature`, `type()`, equality, and calls from a table."""

from __future__ import annotations

import pytest
import wasm_builder as wb

import wasmhost
from wasmhost import FuncType, i32, i64


@pytest.fixture
def provider(session: str) -> wasmhost.Instance:
    backend = wasmhost.get_backend()
    if not backend.supports("table.funcs"):
        pytest.skip(f"the {backend.name} backend has no table.funcs")
    return wasmhost.Instance(wasmhost.Module(wb.tables(imported=False)))


ADD = FuncType((i32, i32), (i32,))


def test_an_export_knows_its_type(provider: wasmhost.Instance) -> None:
    add = provider.exports.add
    assert isinstance(add, wasmhost.Function)
    assert add.name == "add"
    assert add.signature == ADD
    assert add.type() == ADD
    assert add(2, 3) == 5


def test_one_function_is_one_object(provider: wasmhost.Instance) -> None:
    add = provider.exports.add
    assert provider.exports.add is add
    table = provider.exports.table
    table.set(0, add)
    got = table.get(0)
    assert got is add  # as in JavaScript: table.get(0) === exports.add
    assert got == add and hash(got) == hash(add)
    assert provider.exports.mul != add


def test_a_function_of_a_table_is_called(provider: wasmhost.Instance) -> None:
    table = provider.exports.table
    table.set(1, provider.exports.mul)
    mul = table.get(1)
    assert mul is not None
    if mul.signature is None:  # an engine that does not tell the type: looked for on the first call, or set by hand
        try:
            mul.type()
        except ValueError:
            mul.signature = ADD
    assert mul(6, 7) == 42


def test_the_native_type_of_a_table_entry_is_found(provider: wasmhost.Instance) -> None:
    backend = wasmhost.get_backend()
    if backend.name != "wasmtime":
        pytest.skip("only wasmtime tells the type of a function it found in a table")
    table = provider.exports.table
    table.set(0, provider.exports.add)
    del table
    fresh = wasmhost.Table("funcref", 1)
    fresh.set(0, provider.exports.add)
    assert fresh.get(0).type() == ADD  # type: ignore[union-attr]


def test_the_signature_rules(provider: wasmhost.Instance) -> None:
    add = provider.exports.add
    add.signature = ADD  # the same as is known: fine
    with pytest.raises(ValueError, match="known"):
        add.signature = FuncType((i64,), (i64,))
    with pytest.raises(ValueError, match="can't be taken away"):
        add.signature = None
    with pytest.raises(TypeError):
        add.signature = "iii"  # type: ignore[assignment]
    unknown = wasmhost.Function.__new__(wasmhost.Function)
    unknown._known, unknown._signature = False, None
    with pytest.raises(ValueError, match="only i32, i64, f32 and f64"):
        unknown.signature = FuncType(("externref",), ())  # type: ignore[arg-type]
    unknown.signature = ADD
    unknown.signature = None  # not known for certain: may be taken back


def test_a_function_of_another_instance_is_not_batched(provider: wasmhost.Instance) -> None:
    other = wasmhost.Instance(wasmhost.Module(wb.tables(imported=False)))
    with pytest.raises(ValueError, match="another instance"):
        provider.batch().call(other.exports.add, 1, 2)
