"""Memory(...) and Table(...): made on their own, imported, shared. The JavaScript API's WebAssembly.Memory/Table."""

from __future__ import annotations

import pytest
import wasm_builder as wb

import wasmhost


def _need(feature: str) -> wasmhost.Backend:
    backend = wasmhost.get_backend()
    if not backend.supports(feature):
        pytest.skip(f"the {backend.name} backend has no {feature}")
    return backend


@pytest.fixture
def memories(session: str) -> wasmhost.Backend:
    return _need("import.memory")


@pytest.fixture
def tables(session: str) -> wasmhost.Backend:
    return _need("table.funcs")


def test_a_memory_on_its_own(memories: wasmhost.Backend) -> None:
    mem = wasmhost.Memory(1, 3)
    assert len(mem) == 65536
    mem.write(10, b"abc")
    assert mem.read(10, 3) == b"abc"
    if memories.supports("memory.grow"):
        assert mem.grow(1) == 1
        assert len(mem) == 2 * 65536
        assert mem.read(10, 3) == b"abc"
        with pytest.raises(IndexError):
            mem.grow(5)  # over its maximum
    with pytest.raises(IndexError):
        mem.read(len(mem) - 1, 2)


def test_memory_limits_are_checked(memories: wasmhost.Backend) -> None:
    with pytest.raises(ValueError):
        wasmhost.Memory(4, 2)
    with pytest.raises(TypeError):
        wasmhost.Memory(-1)


def test_a_memory_is_shared_by_instances(memories: wasmhost.Backend) -> None:
    mem = wasmhost.Memory(1)
    module = wasmhost.Module(wb.uses_memory())
    a = wasmhost.Instance(module, {"env": {"memory": mem}})
    b = wasmhost.Instance(module, {"env": {"memory": mem}})
    a.exports.store(5, 42)
    assert b.exports.load(5) == 42
    assert mem.read(5, 1) == b"\x2a"
    mem.write(6, b"\x07")
    assert a.exports.load(6) == 7


def test_the_memory_of_an_instance_can_be_imported(memories: wasmhost.Backend) -> None:
    module = wasmhost.Module(wb.uses_memory())
    first = wasmhost.Instance(module, {"env": {"memory": wasmhost.Memory(1)}})
    exported = wasmhost.Memory._wrap(first._backend, first._backend.new_memory(1, None))  # noqa: SLF001
    second = wasmhost.Instance(module, {"env": {"memory": exported}})
    second.exports.store(0, 9)
    assert exported.read(0, 1) == b"\x09"


def test_an_import_that_is_not_a_memory(memories: wasmhost.Backend) -> None:
    module = wasmhost.Module(wb.uses_memory())
    with pytest.raises(wasmhost.LinkError):
        wasmhost.Instance(module, {"env": {"memory": 1}})
    with pytest.raises(wasmhost.LinkError):
        wasmhost.Instance(module, {"env": {"memory": wasmhost.Global("i32", 0)}})


def test_a_memory_smaller_than_the_module_wants_is_a_link_error(memories: wasmhost.Backend) -> None:
    with pytest.raises(wasmhost.LinkError):
        wasmhost.Instance(wasmhost.Module(wb.uses_memory()), {"env": {"memory": wasmhost.Memory(0)}})


def test_a_table_on_its_own(tables: wasmhost.Backend) -> None:
    table = wasmhost.Table("funcref", 2, 4)
    assert len(table) == 2
    assert table.get(0) is None
    assert table.grow(1) == 2
    assert len(table) == 3
    with pytest.raises(IndexError):
        table.get(3)
    with pytest.raises(IndexError):
        table.set(3, None)
    with pytest.raises(TypeError):
        table.set(0, 5)  # type: ignore[arg-type]


def test_table_kinds_and_limits(tables: wasmhost.Backend) -> None:
    with pytest.raises(NotImplementedError):
        wasmhost.Table("externref", 1)
    with pytest.raises(ValueError):
        wasmhost.Table("funcref", 3, 2)


def test_functions_in_an_imported_table(tables: wasmhost.Backend) -> None:
    table = wasmhost.Table("funcref", 2)
    provider = wasmhost.Instance(wasmhost.Module(wb.tables(imported=False)))
    user = wasmhost.Instance(wasmhost.Module(wb.tables(imported=True)), {"env": {"table": table}})
    table.set(0, provider.exports.add)
    table.set(1, provider.exports.mul)
    assert user.exports.call(6, 7, 0) == 13  # call_indirect into the other instance's functions
    assert user.exports.call(6, 7, 1) == 42
    table.set(0, user.exports.mul)
    assert user.exports.call(3, 5, 0) == 15
    table.set(1, None)
    with pytest.raises(wasmhost.Trap):
        user.exports.call(1, 2, 1)  # null entry


def test_an_entry_read_from_a_table_goes_into_another(tables: wasmhost.Backend) -> None:
    provider = wasmhost.Instance(wasmhost.Module(wb.tables(imported=False)))
    source = provider.exports.table
    assert isinstance(source, wasmhost.Table)
    assert len(source) == 2
    source.set(0, provider.exports.add)
    ref = source.get(0)
    assert isinstance(ref, wasmhost.FuncRef)
    target = wasmhost.Table("funcref", 2)
    target.set(0, ref)
    user = wasmhost.Instance(wasmhost.Module(wb.tables(imported=True)), {"env": {"table": target}})
    assert user.exports.call(20, 22, 0) == 42


def test_an_exported_table_grows(tables: wasmhost.Backend) -> None:
    provider = wasmhost.Instance(wasmhost.Module(wb.tables(imported=False)))
    table = provider.exports.table
    assert table.grow(3) == 2
    assert len(table) == 5


def test_a_table_that_is_too_small_is_a_link_error(tables: wasmhost.Backend) -> None:
    with pytest.raises(wasmhost.LinkError):
        wasmhost.Instance(wasmhost.Module(wb.tables(imported=True)), {"env": {"table": wasmhost.Table("funcref", 1)}})
    with pytest.raises(wasmhost.LinkError):
        wasmhost.Instance(wasmhost.Module(wb.tables(imported=True)), {"env": {"table": wasmhost.Memory(1)}})


def test_a_backend_without_them_says_so(session: str) -> None:
    backend = wasmhost.get_backend()
    if backend.supports("import.memory"):
        pytest.skip("this backend has them")
    with pytest.raises(NotImplementedError):
        wasmhost.Memory(1)
    with pytest.raises(NotImplementedError):
        wasmhost.Table("funcref", 1)
