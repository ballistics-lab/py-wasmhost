"""Host functions: a Python callable the module imports and calls."""

from __future__ import annotations

from typing import Any

import pytest
import wasm_builder as wb

import wasmhost


def _needs_imports() -> None:
    if not wasmhost.get_backend().supports("imports"):
        pytest.skip(f"the {wasmhost.get_backend().name} backend can't take imports")


class Host:
    """The import object of the callbacks() module, recording what the module asked."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def plus(self, a: int, b: int) -> int:
        self.calls.append(("plus", a, b))
        return a + b

    def note(self, x: int) -> None:
        self.calls.append(("note", x))

    def half(self, x: float) -> float:
        self.calls.append(("half", x))
        return x / 2

    def pair(self, x: int) -> tuple[int, int]:
        self.calls.append(("pair", x))
        return (x, x + 1)

    def imports(self) -> dict[str, dict[str, Any]]:
        return {"env": {"plus": self.plus, "note": self.note, "half": self.half, "pair": self.pair}}


@pytest.fixture
def host(session: str) -> Host:
    _needs_imports()
    return Host()


@pytest.fixture
def inst(host: Host) -> wasmhost.Instance:
    return wasmhost.Instance(wasmhost.Module(wb.callbacks()), host.imports())


def test_a_call_reaches_python_and_its_result_comes_back(inst: wasmhost.Instance, host: Host) -> None:
    assert inst.exports.call_plus(2, 3) == 5
    assert host.calls == [("plus", 2, 3)]


def test_types_cross_intact(inst: wasmhost.Instance, host: Host) -> None:
    big = 2**62 + 12345  # an i64 that a double could not hold
    assert inst.exports.call_note(big) is None
    assert inst.exports.call_half(5.0) == 2.5
    assert inst.exports.call_plus(-1, -2) == -3
    assert host.calls == [("note", big), ("half", 5.0), ("plus", -1, -2)]


def test_multiple_results(inst: wasmhost.Instance, host: Host) -> None:
    assert inst.exports.call_pair(7) == (7, 8)


def test_nested_calls_in_order(inst: wasmhost.Instance, host: Host) -> None:
    assert inst.exports.twice(10) == 12
    assert host.calls == [("plus", 10, 1), ("plus", 11, 1)]


def test_an_exception_comes_out_of_the_call_and_the_instance_lives(host: Host) -> None:
    def boom(a: int, b: int) -> int:
        raise ValueError("from python")

    imports = host.imports()
    imports["env"]["plus"] = boom
    inst = wasmhost.Instance(wasmhost.Module(wb.callbacks()), imports)
    with pytest.raises(ValueError, match="from python"):
        inst.exports.call_plus(1, 2)
    inst.exports.call_note(1)  # still usable
    assert host.calls == [("note", 1)]


@pytest.mark.parametrize(
    "bad",
    [lambda a, b: None, lambda a, b: "x", lambda a, b: 1.5, lambda a, b: (1, 2), lambda a, b: True],
    ids=["none", "str", "float", "tuple", "bool"],
)
def test_a_wrong_result_is_a_typeerror(host: Host, bad: Any) -> None:
    imports = host.imports()
    imports["env"]["plus"] = bad
    inst = wasmhost.Instance(wasmhost.Module(wb.callbacks()), imports)
    with pytest.raises(TypeError):
        inst.exports.call_plus(1, 2)


def test_a_result_wraps_to_its_width(host: Host) -> None:
    imports = host.imports()
    imports["env"]["plus"] = lambda a, b: 2**31  # not an i32: it wraps, as it would in JavaScript
    inst = wasmhost.Instance(wasmhost.Module(wb.callbacks()), imports)
    assert inst.exports.call_plus(0, 0) == -(2**31)


def test_in_a_batch(inst: wasmhost.Instance, host: Host) -> None:
    b = inst.batch()
    first = b.call(inst.exports.call_plus, 1, 2)
    second = b.call(inst.exports.call_plus, first * 10, 4)
    b.run()
    assert (first.value, second.value) == (3, 34)
    assert host.calls == [("plus", 1, 2), ("plus", 30, 4)]


def test_the_import_object_is_checked(host: Host) -> None:
    module = wasmhost.Module(wb.callbacks())
    with pytest.raises(TypeError, match="env.plus"):
        wasmhost.Instance(module)  # no import object
    with pytest.raises(TypeError, match="'env'"):
        wasmhost.Instance(module, {"other": {}})
    with pytest.raises(wasmhost.LinkError, match="env.half"):
        wasmhost.Instance(module, {"env": {"plus": host.plus, "note": host.note, "pair": host.pair}})
    with pytest.raises(wasmhost.LinkError, match="env.plus"):
        wasmhost.Instance(module, {"env": {**host.imports()["env"], "plus": 42}})


def test_a_backend_without_imports_says_so(session: str) -> None:
    if wasmhost.get_backend().supports("imports"):
        pytest.skip("this backend does take imports")
    module = wasmhost.Module(wb.callbacks())
    with pytest.raises(NotImplementedError, match="imports"):
        wasmhost.Instance(module, Host().imports())


def test_two_instances_have_their_own_host_functions(session: str) -> None:
    _needs_imports()
    module = wasmhost.Module(wb.callbacks())
    a, b = Host(), Host()
    ia, ib = wasmhost.Instance(module, a.imports()), wasmhost.Instance(module, b.imports())
    ia.exports.call_plus(1, 1)
    ib.exports.call_plus(2, 2)
    ib.exports.call_plus(3, 3)
    assert (len(a.calls), len(b.calls)) == (1, 2)


def test_a_host_function_can_call_the_module_again(session: str) -> None:
    _needs_imports()
    box: dict[str, wasmhost.Instance] = {}

    def plus(a: int, b: int) -> int:
        if a == 100:  # from inside the host function: another export of the same instance
            return int(box["inst"].exports.call_plus(1, 2)) + 1000
        return a + b

    imports = Host().imports()
    imports["env"]["plus"] = plus
    box["inst"] = wasmhost.Instance(wasmhost.Module(wb.callbacks()), imports)
    assert box["inst"].exports.call_plus(100, 0) == 1003
    assert box["inst"].exports.call_plus(2, 3) == 5


def test_an_exception_from_a_nested_call_reaches_the_outer_caller(session: str) -> None:
    _needs_imports()
    box: dict[str, wasmhost.Instance] = {}

    def plus(a: int, b: int) -> int:
        if a == 100:
            return int(box["inst"].exports.call_plus(-1, 0))  # the nested one raises below
        raise KeyError("inner")

    imports = Host().imports()
    imports["env"]["plus"] = plus
    box["inst"] = wasmhost.Instance(wasmhost.Module(wb.callbacks()), imports)
    with pytest.raises(KeyError, match="inner"):
        box["inst"].exports.call_plus(100, 0)
    with pytest.raises(KeyError, match="inner"):
        box["inst"].exports.call_plus(5, 0)  # and nothing is left over from the first
