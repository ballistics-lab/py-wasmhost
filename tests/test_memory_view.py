"""`Memory.view`: the engine's own memory as a `memoryview`, with no copy, and never a stale address."""

from __future__ import annotations

import pytest
import wasm_builder as wb

import wasmhost


@pytest.fixture
def memory(session: str) -> wasmhost.Memory:
    inst = wasmhost.Instance(wasmhost.Module(wb.arith()))
    return inst.exports.memory


def need() -> wasmhost.Backend:
    backend = wasmhost.get_backend()
    if not backend.supports("memory.view"):
        pytest.skip(f"the {backend.name} backend has no view of its memory")
    return backend


def test_a_view_is_the_memory_itself(memory: wasmhost.Memory) -> None:
    need()
    view = memory.view(10, 5)
    assert len(view) == 5 and not view.readonly
    view[:] = b"hello"
    assert memory.read(10, 5) == b"hello"  # written through the view, read through the engine
    memory.write(10, b"HELLO")
    assert bytes(view) == b"HELLO"  # and the other way round: nothing was copied
    assert len(memory.view()) == len(memory)


def test_a_view_is_checked(memory: wasmhost.Memory) -> None:
    need()
    with pytest.raises(IndexError):
        memory.view(len(memory) - 1, 2)
    with pytest.raises(IndexError):
        memory.view(-1, 1)


def test_a_view_does_not_outlive_a_call_or_a_batch_or_a_grow(session: str) -> None:
    need()
    inst = wasmhost.Instance(wasmhost.Module(wb.arith()))
    memory = inst.exports.memory
    view = memory.view(0, 4)
    inst.exports.add(1, 2)  # the module ran: it may have moved the memory
    with pytest.raises(ValueError):
        view[0]  # noqa: B018
    view = memory.view(0, 4)
    batch = inst.batch()
    batch.call(inst.exports.add, 1, 2)
    batch.run()
    with pytest.raises(ValueError):
        view[0]  # noqa: B018
    if wasmhost.get_backend().supports("memory.grow"):
        view = memory.view(0, 4)
        memory.grow(1)
        with pytest.raises(ValueError):
            view[0]  # noqa: B018


def test_a_copy_made_from_a_view_is_for_good(memory: wasmhost.Memory) -> None:
    need()
    memory.write(0, b"keep")
    kept = bytes(memory.view(0, 4))
    memory.read(0, 1)
    assert kept == b"keep"


def test_without_a_view_the_backend_says_so(session: str) -> None:
    if wasmhost.get_backend().supports("memory.view"):
        pytest.skip("this backend has views")
    inst = wasmhost.Instance(wasmhost.Module(wb.arith()))
    with pytest.raises(NotImplementedError):
        inst.exports.memory.view()


def test_large_buffers_go_in_and_out_whole(session: str) -> None:
    """Whatever way the engine moves bytes (hex, a typed array through the C API, ...), they arrive as they were."""
    memory = wasmhost.Memory(20) if wasmhost.get_backend().supports("import.memory") else None
    if memory is None:
        inst = wasmhost.Instance(wasmhost.Module(wb.arith()))
        memory = inst.exports.memory
    data = bytes(range(256)) * min(1000, (len(memory) - 400) // 256)  # every value, more than the C API threshold
    memory.write(300, data)
    assert memory.read(300, len(data)) == data
    assert memory.read(299, 3) == b"\x00\x00\x01"  # the neighbours were not touched
    for size in (255, 256, 257, 4096):  # round the threshold where the way of moving them changes
        chunk = bytes((i * 7) % 256 for i in range(size))
        memory.write(10, chunk)
        assert memory.read(10, size) == chunk
    with pytest.raises(IndexError):
        memory.read(len(memory) - 10, 300)
