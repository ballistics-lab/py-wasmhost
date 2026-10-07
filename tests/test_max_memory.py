"""`Instance(module, max_memory=pages)`: a ceiling for the memory a module makes itself (the JavaScript API has no way
to set one; for a memory the module imports, the maximum of the `Memory` the host gives is the ceiling, see
test_memory_table.py)."""

from __future__ import annotations

import asyncio

import pytest
import wasm_builder as wb

import wasmhost

PAGE = 65536


def _instance(initial: int = 1, maximum: int | None = None, limit: int | None = 3) -> wasmhost.Instance:
    return wasmhost.Instance(wasmhost.Module(wb.owns_memory(initial, maximum)), max_memory=limit)


def test_a_memory_with_no_maximum_gets_the_ceiling(session: str) -> None:
    ex = _instance().exports
    assert ex.grow(2) == 1  # 1 -> 3 pages, exactly the ceiling
    assert (ex.size(), len(ex.memory)) == (3, 3 * PAGE)
    assert ex.grow(1) == -1  # past it: refused, as the specification has it, and nothing changes
    assert (ex.grow(0), ex.size(), len(ex.memory)) == (3, 3, 3 * PAGE)
    ex.memory.write(3 * PAGE - 1, b"\x07")
    assert ex.memory.read(3 * PAGE - 1, 1) == b"\x07"


def test_a_bigger_maximum_is_lowered_and_a_smaller_one_stays(session: str) -> None:
    high = _instance(maximum=100).exports
    assert (high.grow(3), high.size()) == (-1, 1)  # 1 + 3 = 4 pages, over the ceiling of 3
    assert high.grow(2) == 1
    low = _instance(maximum=2).exports
    assert (low.grow(2), low.grow(1)) == (-1, 1)  # its own maximum of 2 is the tighter one
    assert low.size() == 2


def test_the_biggest_request_is_refused_without_an_allocation(session: str) -> None:
    ex = _instance().exports
    assert ex.grow(65536) == -1  # 4 GiB
    assert ex.grow(-1) == -1  # 0xFFFFFFFF pages
    assert ex.size() == 1 and ex.grow(1) == 1  # the instance is fine


def test_type_says_the_maximum_that_holds(session: str) -> None:
    assert _instance(maximum=None).exports.memory.type().maximum == 3
    assert _instance(maximum=2).exports.memory.type().maximum == 2
    module = wasmhost.Module(wb.owns_memory())
    assert wasmhost.Module.exports(module)[2].type == wasmhost.MemoryType(
        1, None, False
    )  # the module itself is as it was


def test_memory_grow_from_python_stops_at_the_ceiling(session: str) -> None:
    backend = wasmhost.get_backend()
    if not backend.supports("memory.grow"):
        pytest.skip(f"the {backend.name} backend has no Memory.grow from Python")
    memory = _instance().exports.memory
    assert memory.grow(2) == 1
    with pytest.raises(IndexError):
        memory.grow(1)
    assert len(memory) == 3 * PAGE


def test_without_max_memory_nothing_changes(session: str) -> None:
    ex = wasmhost.Instance(wasmhost.Module(wb.owns_memory())).exports  # no maximum at all
    assert ex.grow(50) == 1 and ex.size() == 51


def test_one_module_has_as_many_ceilings_as_instances_want(session: str) -> None:
    module = wasmhost.Module(wb.owns_memory())
    two = wasmhost.Instance(module, max_memory=2).exports
    five = wasmhost.Instance(module, max_memory=5).exports
    free = wasmhost.Instance(module).exports
    assert (two.grow(2), five.grow(2), free.grow(2)) == (-1, 1, 1)
    assert (two.grow(1), five.grow(2), five.grow(1), free.grow(40)) == (1, 3, -1, 3)


def test_a_ceiling_is_compiled_once(session: str, monkeypatch: pytest.MonkeyPatch) -> None:
    module = wasmhost.Module(wb.owns_memory())
    backend = wasmhost.get_backend()
    compiled: list[int] = []
    original = backend.compile

    def spy(data: bytes) -> object:
        compiled.append(len(data))
        return original(data)

    monkeypatch.setattr(backend, "compile", spy)
    for _ in range(3):
        wasmhost.Instance(module, max_memory=3)
    assert len(compiled) == 1  # the first instance with this ceiling paid for it, the next two did not
    wasmhost.Instance(module, max_memory=4)
    assert len(compiled) == 2  # another ceiling, another module
    wasmhost.Instance(module)
    assert len(compiled) == 2  # no ceiling: the module itself, nothing to compile


def test_a_module_that_needs_no_change_is_not_compiled_again(session: str, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = wasmhost.get_backend()
    for wasm in (wb.spinner(), wb.owns_memory(1, 2)):  # no memory of its own at all, one that is tight enough
        module = wasmhost.Module(wasm)
        monkeypatch.setattr(backend, "compile", lambda data: pytest.fail("compiled again"))
        wasmhost.Instance(module, max_memory=2)
        monkeypatch.undo()


def test_a_module_that_starts_over_the_ceiling_is_refused(session: str) -> None:
    module = wasmhost.Module(wb.owns_memory(initial=5))
    with pytest.raises(ValueError, match="starts at 5 pages"):
        wasmhost.Instance(module, max_memory=3)
    wasmhost.Instance(wasmhost.Module(wb.owns_memory(initial=3)), max_memory=3)  # exactly the ceiling is fine
    wasmhost.Instance(module)  # and the module is still good without one


def test_the_ceiling_is_checked(session: str) -> None:
    module = wasmhost.Module(wb.owns_memory())
    for wrong in (-1, 1.5, "3", True):
        with pytest.raises(TypeError):
            wasmhost.Instance(module, max_memory=wrong)  # type: ignore[arg-type]


def test_an_imported_memory_over_the_ceiling_is_a_link_error(session: str) -> None:
    backend = wasmhost.get_backend()
    if not backend.supports("import.memory"):
        pytest.skip(f"the {backend.name} backend has no import.memory")
    module = wasmhost.Module(wb.grows_memory())
    for memory in (wasmhost.Memory(1), wasmhost.Memory(1, 4)):  # no maximum, a bigger one
        with pytest.raises(wasmhost.LinkError, match="max_memory"):
            wasmhost.Instance(module, {"env": {"memory": memory}}, max_memory=3)
    ex = wasmhost.Instance(module, {"env": {"memory": wasmhost.Memory(1, 3)}}, max_memory=3).exports  # as tight: fine
    assert (ex.grow(2), ex.grow(1)) == (1, -1)
    wasmhost.Instance(module, {"env": {"memory": wasmhost.Memory(1)}})  # without a ceiling the host's memory is its own


def test_it_goes_through_instantiate(session: str) -> None:
    async def go() -> None:
        made = await wasmhost.instantiate(wb.owns_memory(), max_memory=2)
        assert made.instance.exports.grow(1) == 1 and made.instance.exports.grow(1) == -1
        module = await wasmhost.compile(wb.owns_memory())
        assert (await wasmhost.instantiate(module, max_memory=2)).exports.grow(2) == -1

    asyncio.run(go())
    assert wasmhost.instantiate_sync(wb.owns_memory(), max_memory=2).instance.exports.grow(2) == -1
    assert wasmhost.instantiate_sync(wasmhost.Module(wb.owns_memory()), max_memory=2).exports.grow(2) == -1
