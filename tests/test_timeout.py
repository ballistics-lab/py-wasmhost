"""`Instance(module, timeout=seconds)`: a call that runs too long is stopped with a `Timeout`, where the engine can do
it (wasmtime by epochs, Node by `vm`'s timeout); the others say so. Not in the JavaScript API."""

from __future__ import annotations

import asyncio
import time

import pytest
import wasm_builder as wb

import wasmhost

LIMIT = 0.2  # seconds: short, so the suite stays quick, long enough that an ordinary call is never near it


@pytest.fixture
def timed(session: str) -> wasmhost.Backend:
    backend = wasmhost.get_backend()
    if not backend.supports("timeout"):
        pytest.skip(f"the {backend.name} backend can't stop a call that runs too long")
    return backend


def _spinner(limit: float | None = LIMIT) -> wasmhost.Instance:
    return wasmhost.Instance(wasmhost.Module(wb.spinner()), timeout=limit)


def test_an_endless_loop_is_stopped(timed: wasmhost.Backend) -> None:
    ex = _spinner().exports
    started = time.monotonic()
    with pytest.raises(wasmhost.Timeout):
        ex.spin()
    took = time.monotonic() - started
    assert LIMIT * 0.8 <= took < LIMIT + 3, took  # not at once, and not much after the limit


def test_a_timeout_is_a_trap(timed: wasmhost.Backend) -> None:
    ex = _spinner().exports
    with pytest.raises(wasmhost.Trap) as caught:  # whoever handles traps handles this
        ex.spin()
    assert isinstance(caught.value, wasmhost.Timeout) and isinstance(caught.value, RuntimeError)


def test_the_instance_is_fine_after_a_timeout(timed: wasmhost.Backend) -> None:
    ex = _spinner().exports
    assert ex.quick() == 7
    with pytest.raises(wasmhost.Timeout):
        ex.spin()
    assert (ex.quick(), ex.busy(1000)) == (7, 1000)  # callable again
    with pytest.raises(wasmhost.Timeout):
        ex.spin()  # and stopped again


def test_a_call_that_finishes_is_not_disturbed(timed: wasmhost.Backend) -> None:
    ex = _spinner(5).exports
    assert all(ex.quick() == 7 for _ in range(300))  # each call arms a deadline and drops it
    assert ex.busy(2_000_000) == 2_000_000  # a call that works for a while, well within its time


def test_a_late_deadline_does_not_hit_the_next_call(timed: wasmhost.Backend) -> None:
    """A call that ends just as its time does must not leave a tick behind that stops the one after it."""
    ex = _spinner(0.05).exports
    for _ in range(15):
        with pytest.raises(wasmhost.Timeout):
            ex.spin()
        assert ex.quick() == 7


def test_a_batch_is_one_call(timed: wasmhost.Backend) -> None:
    inst = _spinner()
    batch = inst.batch()
    batch.call(inst.exports.quick)
    batch.call(inst.exports.spin)
    with pytest.raises(wasmhost.Timeout):
        batch.run()
    assert inst.exports.quick() == 7


def test_the_start_function_is_under_the_timeout(timed: wasmhost.Backend) -> None:
    module = wasmhost.Module(wb.spins_at_start())
    started = time.monotonic()
    with pytest.raises(wasmhost.Timeout):
        wasmhost.Instance(module, timeout=LIMIT)
    assert time.monotonic() - started < LIMIT + 3


def test_a_trap_in_the_start_function_is_a_trap(session: str) -> None:
    """Whatever the timeout: the engine's own trap must not leak out of making an instance (it did, on wasmtime)."""
    if wasmhost.get_backend().name == "wasm3":
        pytest.skip("wasm3 runs the start function at the first call, not when the instance is made")
    module = wasmhost.Module(wb.traps_at_start())
    with pytest.raises(wasmhost.Trap):
        wasmhost.Instance(module)


def test_an_instance_without_a_timeout_is_not_timed(timed: wasmhost.Backend) -> None:
    module = wasmhost.Module(wb.spinner())
    plain = wasmhost.Instance(module).exports
    wasmhost.Instance(module, timeout=LIMIT)  # one with a timeout beside it changes nothing for this one
    assert (plain.quick(), plain.busy(3_000_000)) == (7, 3_000_000)


def test_the_module_for_stopping_is_made_once(timed: wasmhost.Backend, monkeypatch: pytest.MonkeyPatch) -> None:
    module = wasmhost.Module(wb.spinner())
    made: list[int] = []
    original = timed.compile_timed

    def spy(data: bytes) -> object:
        made.append(len(data))
        return original(data)

    monkeypatch.setattr(timed, "compile_timed", spy)
    for _ in range(3):
        wasmhost.Instance(module, timeout=LIMIT)
    assert len(made) == 1  # the first instance that asked paid for it
    wasmhost.Instance(module)
    assert len(made) == 1  # one that did not ask never needs it


def test_it_goes_with_a_memory_ceiling(timed: wasmhost.Backend) -> None:
    inst = wasmhost.Instance(wasmhost.Module(wb.owns_memory()), max_memory=2, timeout=LIMIT)
    assert (inst.exports.grow(1), inst.exports.grow(1)) == (1, -1)


def test_it_goes_through_instantiate(timed: wasmhost.Backend) -> None:
    async def go() -> None:
        made = await wasmhost.instantiate(wb.spinner(), timeout=LIMIT)
        with pytest.raises(wasmhost.Timeout):
            made.instance.exports.spin()

    asyncio.run(go())
    with pytest.raises(wasmhost.Timeout):
        wasmhost.instantiate_sync(wb.spinner(), timeout=LIMIT).instance.exports.spin()


def test_wasmtime_keeps_a_timed_instance_in_a_store_of_its_own(timed: wasmhost.Backend) -> None:
    if timed.name != "wasmtime":
        pytest.skip("only wasmtime has stores")
    module = wasmhost.Module(wb.uses_memory())
    with pytest.raises(ValueError, match="another store"):
        wasmhost.Instance(module, {"env": {"memory": wasmhost.Memory(1)}}, timeout=LIMIT)


def test_the_timeout_is_checked(session: str) -> None:
    module = wasmhost.Module(wb.spinner())
    backend = wasmhost.get_backend()
    for wrong in (0, -1, "1", True, float("inf"), float("nan"), None.__class__):
        with pytest.raises((TypeError, NotImplementedError)):
            wasmhost.Instance(module, timeout=wrong)  # type: ignore[arg-type]
    if backend.supports("timeout"):
        with pytest.raises(TypeError):
            wasmhost.Instance(module, timeout=0)


def test_a_backend_that_cannot_says_so(session: str) -> None:
    backend = wasmhost.get_backend()
    if backend.supports("timeout"):
        pytest.skip("this backend can")
    with pytest.raises(NotImplementedError, match="can't stop a call"):
        wasmhost.Instance(wasmhost.Module(wb.spinner()), timeout=1)
    assert wasmhost.Instance(wasmhost.Module(wb.spinner())).exports.quick() == 7  # and works as ever without
