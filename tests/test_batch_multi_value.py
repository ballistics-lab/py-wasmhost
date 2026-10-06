"""A batch takes a function with several results: a tuple of `Ref`s, each usable by the later steps."""

from __future__ import annotations

import pytest
import wasm_builder as wb

import wasmhost


@pytest.fixture
def swapper(session: str) -> wasmhost.Instance:
    return wasmhost.Instance(wasmhost.Module(wb.swaps(imported=False)))


def test_each_result_is_a_ref(swapper: wasmhost.Instance) -> None:
    b = swapper.batch()
    first, second = b.call(swapper.exports.swap, 1, 2)
    b.run()
    assert (first.value, second.value) == (2, 1)


def test_results_feed_later_steps(swapper: wasmhost.Instance) -> None:
    b = swapper.batch()
    first, second = b.call(swapper.exports.swap, 10, 20)
    again = b.call(swapper.exports.swap, first, second + 1)  # 20, 11 -> 11, 20
    b.run()
    assert (again[0].value, again[1].value) == (11, 20)


def test_a_failing_step_keeps_the_results_before_it(swapper: wasmhost.Instance) -> None:
    if not wasmhost.get_backend().supports("import.memory"):
        pytest.skip("a memory made on its own is needed for the failing step")
    b = swapper.batch()
    first, second = b.call(swapper.exports.swap, 3, 4)
    memory_less = b.read(wasmhost.Memory(1), 0, 70000)  # out of bounds
    after = b.call(swapper.exports.swap, 1, 1)
    with pytest.raises((IndexError, wasmhost.WasmError, NotImplementedError)):
        b.run()
    assert memory_less is not None
    assert (first.value, second.value) == (4, 3)
    assert not after[0].done
