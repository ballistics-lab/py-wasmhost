"""`Instance(module, fuel=n)`: a call that uses more than `n` units of what the engine counts is stopped with an
`OutOfFuel`, where the engine can count (wasmtime's fuel, wasm3's gas); the others say so. Not in the JavaScript API.
The unit is the engine's own (wasmtime spends 8 for a turn of the test loop, wasm3 about 0.07), so a budget is not
the same number on two engines; these tests only ask that a small one stops an endless loop and a big one does not."""

from __future__ import annotations

import asyncio
import time

import pytest
import wasm_builder as wb

import wasmhost

SMALL = 20_000  # stops the endless loop at once on either engine, and is more than quick() needs
BIG = 10_000_000_000  # more than any call here uses


@pytest.fixture
def counting(session: str) -> wasmhost.Backend:
    backend = wasmhost.get_backend()
    if not backend.supports("fuel"):
        pytest.skip(f"the {backend.name} backend can't count what a call runs")
    return backend


def _spinner(fuel: int = SMALL, timeout: float | None = None) -> wasmhost.Instance:
    return wasmhost.Instance(wasmhost.Module(wb.spinner()), fuel=fuel, timeout=timeout)


def test_an_endless_loop_runs_out_of_fuel(counting: wasmhost.Backend) -> None:
    ex = _spinner().exports
    with pytest.raises(wasmhost.OutOfFuel, match="units of fuel"):
        ex.spin()


def test_out_of_fuel_is_a_trap(counting: wasmhost.Backend) -> None:
    with pytest.raises(wasmhost.Trap) as caught:  # whoever handles traps handles this
        _spinner().exports.spin()
    assert isinstance(caught.value, wasmhost.OutOfFuel) and isinstance(caught.value, RuntimeError)
    assert not isinstance(caught.value, wasmhost.Timeout)


def test_the_instance_is_fine_after_running_out(counting: wasmhost.Backend) -> None:
    """A trap of the engine's own, not a paused call: nothing is left behind, so the instance goes on."""
    ex = _spinner().exports
    assert ex.quick() == 7
    with pytest.raises(wasmhost.OutOfFuel):
        ex.spin()
    assert (ex.quick(), ex.busy(100)) == (7, 100)
    with pytest.raises(wasmhost.OutOfFuel):
        ex.spin()  # and stopped again


def test_the_fuel_is_for_each_call(counting: wasmhost.Backend) -> None:
    """Every call starts with the whole budget: many calls add up to more than one budget and none is stopped."""
    ex = _spinner(SMALL).exports
    assert all(ex.busy(100) == 100 for _ in range(500))
    assert all(ex.quick() == 7 for _ in range(500))


def test_a_big_budget_does_not_get_in_the_way(counting: wasmhost.Backend) -> None:
    ex = _spinner(BIG).exports
    assert ex.busy(3_000_000) == 3_000_000


def test_the_same_call_stops_at_the_same_budget(counting: wasmhost.Backend) -> None:
    """Deterministic: what one budget finishes it finishes every time, and what it can't it never does."""
    outcomes = []
    for _ in range(5):
        ex = _spinner(150_000).exports
        try:
            ex.busy(1_000_000)
            outcomes.append("done")
        except wasmhost.OutOfFuel:
            outcomes.append("out")
    assert len(set(outcomes)) == 1


def test_a_budget_is_a_limit_on_the_work(counting: wasmhost.Backend) -> None:
    """More work needs more fuel: a call the budget lets through, ten times the work it does not."""
    ex = _spinner(150_000).exports
    for n in (10, 100, 1000):  # find a loop count this budget passes, then one it doesn't
        try:
            ex.busy(n)
        except wasmhost.OutOfFuel:
            pytest.fail(f"busy({n}) should be well within the budget")
    with pytest.raises(wasmhost.OutOfFuel):
        ex.busy(100_000_000)


def test_a_batch_is_one_call(counting: wasmhost.Backend) -> None:
    inst = _spinner()
    batch = inst.batch()
    batch.call(inst.exports.quick)
    batch.call(inst.exports.spin)
    with pytest.raises(wasmhost.OutOfFuel):
        batch.run()
    assert inst.exports.quick() == 7


def test_the_start_function_has_fuel_too(counting: wasmhost.Backend) -> None:
    module = wasmhost.Module(wb.spins_at_start())
    with pytest.raises(wasmhost.OutOfFuel):
        instance = wasmhost.Instance(module, fuel=SMALL)
        assert counting.name == "wasm3", "the start function ran and returned"  # wasm3 runs it at the first call
        instance.exports.quick()


def test_a_host_function_can_call_the_module_again_with_one_budget(counting: wasmhost.Backend) -> None:
    from test_imports import Host

    if not counting.supports("imports"):
        pytest.skip(f"the {counting.name} backend can't take imports")
    box: dict[str, wasmhost.Instance] = {}

    def plus(a: int, b: int) -> int:
        if a == 100:  # from inside the host function: another export of the same instance
            return int(box["inst"].exports.call_plus(1, 2)) + 1000
        return a + b

    imports = Host().imports()
    imports["env"]["plus"] = plus
    box["inst"] = wasmhost.Instance(wasmhost.Module(wb.callbacks()), imports, fuel=BIG)
    assert box["inst"].exports.call_plus(100, 0) == 1003
    assert box["inst"].exports.call_plus(2, 3) == 5


def test_fuel_and_timeout_together(counting: wasmhost.Backend) -> None:
    """Whichever runs out first ends the call, with its own error."""
    if not counting.supports("timeout"):
        pytest.skip(f"the {counting.name} backend has no timeout")
    with pytest.raises(wasmhost.OutOfFuel):
        _spinner(SMALL, timeout=30).exports.spin()  # a little fuel, a lot of time
    started = time.monotonic()
    with pytest.raises(wasmhost.Timeout):
        _spinner(BIG, timeout=0.2).exports.spin()  # a lot of fuel, a little time
    assert 0.15 <= time.monotonic() - started < 4


def test_with_both_the_instance_goes_on_where_the_engine_lets_it(counting: wasmhost.Backend) -> None:
    if not counting.supports("timeout"):
        pytest.skip(f"the {counting.name} backend has no timeout")
    ex = _spinner(SMALL, timeout=30).exports
    with pytest.raises(wasmhost.OutOfFuel):
        ex.spin()
    if counting.name == "wasm3":  # a paused call can't be cancelled there: the instance is finished
        with pytest.raises(wasmhost.Trap, match="can't run again"):
            ex.quick()
    else:
        assert ex.quick() == 7


def test_the_module_for_counting_is_made_once(counting: wasmhost.Backend, monkeypatch: pytest.MonkeyPatch) -> None:
    module = wasmhost.Module(wb.spinner())
    made: list[tuple[bool, bool]] = []
    original = counting.compile_limited

    def spy(data: bytes, *, epochs: bool, fuel: bool) -> object:
        made.append((epochs, fuel))
        return original(data, epochs=epochs, fuel=fuel)

    monkeypatch.setattr(counting, "compile_limited", spy)
    for _ in range(3):
        wasmhost.Instance(module, fuel=SMALL)
    assert made == [(False, True)]  # the first instance that asked paid for it; counts only fuel
    wasmhost.Instance(module)
    assert made == [(False, True)]  # one that did not ask never needs it


def test_it_goes_through_instantiate(counting: wasmhost.Backend) -> None:
    async def go() -> None:
        made = await wasmhost.instantiate(wb.spinner(), fuel=SMALL)
        with pytest.raises(wasmhost.OutOfFuel):
            made.instance.exports.spin()

    asyncio.run(go())
    with pytest.raises(wasmhost.OutOfFuel):
        wasmhost.instantiate_sync(wb.spinner(), fuel=SMALL).instance.exports.spin()


def test_the_fuel_is_checked(session: str) -> None:
    module = wasmhost.Module(wb.spinner())
    for wrong in (0, -1, 1.5, "100", True):
        with pytest.raises(TypeError):
            wasmhost.Instance(module, fuel=wrong)  # type: ignore[arg-type]


def test_a_backend_that_cannot_says_so(session: str) -> None:
    backend = wasmhost.get_backend()
    if backend.supports("fuel"):
        pytest.skip("this backend can")
    with pytest.raises(NotImplementedError, match="can't count"):
        wasmhost.Instance(wasmhost.Module(wb.spinner()), fuel=1000)
    assert wasmhost.Instance(wasmhost.Module(wb.spinner())).exports.quick() == 7  # and works as ever without
