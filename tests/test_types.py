"""`type()` of a function, memory, table and global, and the descriptors that make the last three: the type
reflection of the JavaScript API, with the same names."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
import wasm_builder as wb

import wasmhost
from wasmhost import FuncType, GlobalType, MemoryType, TableType


def _need(feature: str) -> None:
    backend = wasmhost.get_backend()
    if not backend.supports(feature):
        pytest.skip(f"the {backend.name} backend has no {feature}")


def test_the_type_of_an_exported_function(session: str) -> None:
    ex = wasmhost.Instance(wasmhost.Module(wb.arith())).exports
    assert ex.add.type() == FuncType(("i32", "i32"), ("i32",))
    assert ex.dup.type() == FuncType(("i32",), ("i32", "i32"))
    assert ex.add.type().parameters == ex.add.params


def test_exported_memory_and_globals_say_what_they_are(session: str) -> None:
    ex = wasmhost.Instance(wasmhost.Module(wb.arith())).exports
    assert ex.memory.type() == MemoryType(1, 4, False)
    assert ex.grow(1) == 1  # the module's own memory.grow
    assert ex.memory.type() == MemoryType(2, 4, False)  # the minimum is the size now, as the specification has it
    assert ex.counter.type() == GlobalType("i32", True)
    assert ex.ten.type() == GlobalType("i32", False)
    assert (ex.counter.mutable, ex.ten.mutable) == (True, False)


def test_the_type_of_an_exported_table(session: str) -> None:
    _need("table.funcs")
    table = wasmhost.Instance(wasmhost.Module(wb.tables(imported=False))).exports.table
    assert table.type() == TableType("funcref", 2, None)
    assert table.grow(1) == 2
    assert (table.type(), table.length, len(table)) == (TableType("funcref", 3, None), 3, 3)


def test_a_memory_made_from_a_descriptor(session: str) -> None:
    _need("import.memory")
    assert wasmhost.Memory(1, 3).type() == MemoryType(1, 3, False)
    assert wasmhost.Memory({"initial": 2, "maximum": 5}).type() == MemoryType(2, 5, False)  # as in JavaScript
    assert wasmhost.Memory({"initial": 1}).type() == MemoryType(1, None, False)
    assert len(wasmhost.Memory(MemoryType(2, None, False))) == 2 * 65536  # what type() gave goes back in
    with pytest.raises(TypeError):
        wasmhost.Memory({"maximum": 3})  # no initial size
    with pytest.raises(TypeError):
        wasmhost.Memory({"initial": 1}, 3)  # two maximums
    with pytest.raises(NotImplementedError):
        wasmhost.Memory({"initial": 1, "maximum": 2, "shared": True})  # there are no threads


def test_a_table_made_from_a_descriptor(session: str) -> None:
    _need("import.table")
    assert wasmhost.Table("funcref", 2, 4).type() == TableType("funcref", 2, 4)
    assert wasmhost.Table({"element": "anyfunc", "initial": 2, "maximum": 4}).type() == TableType("funcref", 2, 4)
    assert wasmhost.Table(TableType("funcref", 3, None)).type() == TableType("funcref", 3, None)
    with pytest.raises(NotImplementedError):
        wasmhost.Table({"element": "externref", "initial": 1})
    with pytest.raises(TypeError):
        wasmhost.Table({"element": "anyfunc"})  # no initial size


def test_a_global_made_from_a_descriptor(session: str) -> None:
    _need("import.global")
    assert wasmhost.Global("i32", 7, mutable=True).type() == GlobalType("i32", True)
    g = wasmhost.Global({"value": "f64", "mutable": True}, 1.5)
    assert (g.type(), g.value) == (GlobalType("f64", True), 1.5)
    assert wasmhost.Global(GlobalType("i64", False), 5).value == 5
    with pytest.raises(ValueError):
        wasmhost.Global({"value": "v128"})


def test_a_global_has_to_be_as_mutable_as_the_module_says(session: str) -> None:
    _need("import.global")
    wants_mutable = wasmhost.Module(wb.imports_global())  # env.g: a mutable i32
    wants_frozen = wasmhost.Module(wb.imports_global(mutable=False))
    with pytest.raises(wasmhost.LinkError, match="immutable"):
        wasmhost.Instance(wants_mutable, {"env": {"g": wasmhost.Global("i32", 1)}})
    with pytest.raises(wasmhost.LinkError, match="mutable"):
        wasmhost.Instance(wants_frozen, {"env": {"g": wasmhost.Global("i32", 1, mutable=True)}})
    wasmhost.Instance(wants_frozen, {"env": {"g": wasmhost.Global("i32", 1)}})  # as wanted


@pytest.mark.parametrize(
    ("feature", "make"),
    [
        ("import.memory", lambda: wasmhost.Memory(1, 3)),
        ("import.table", lambda: wasmhost.Table("funcref", 2, 4)),
        ("import.global", lambda: wasmhost.Global("i32", 7, mutable=True)),
    ],
    ids=["memory", "table", "global"],
)
def test_what_type_gives_makes_the_same_again(session: str, feature: str, make: Callable[[], Any]) -> None:
    _need(feature)
    thing = make()
    again = type(thing)(thing.type())  # the type of a Memory, a Table or a Global goes in as its descriptor
    assert again.type() == thing.type()
