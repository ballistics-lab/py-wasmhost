"""Global(...): made on its own, imported, shared. The JavaScript API's WebAssembly.Global."""

from __future__ import annotations

import math

import pytest
import wasm_builder as wb

import wasmhost


def _needs_globals() -> wasmhost.Backend:
    backend = wasmhost.get_backend()
    if not backend.supports("import.global"):
        pytest.skip(f"the {backend.name} backend can't import a global")
    return backend


@pytest.fixture
def target(session: str) -> wasmhost.Backend:
    """The backend of this run, when it takes globals ("backend" itself is the parametrized name of it)."""
    return _needs_globals()


def test_a_global_on_its_own(target: wasmhost.Backend) -> None:
    g = wasmhost.Global("i32", 7, mutable=True)
    assert (g.type, g.mutable, g.value) == ("i32", True, 7)
    g.value = 99
    assert g.value == 99
    frozen = wasmhost.Global("f64", 1.5)
    assert frozen.value == 1.5
    with pytest.raises(TypeError):
        frozen.value = 2.0  # immutable, as in JavaScript


def test_values_are_as_webassembly_takes_them(target: wasmhost.Backend) -> None:
    assert wasmhost.Global("i32", 2**31, mutable=True).value == -(2**31)  # wraps
    assert wasmhost.Global("i64", 2**62 + 1).value == 2**62 + 1  # exact
    assert math.isnan(wasmhost.Global("f64", math.nan).value)
    with pytest.raises(TypeError):
        wasmhost.Global("i32", 1.5)
    with pytest.raises(ValueError, match="expected i32"):
        wasmhost.Global("v128", 0)


def test_a_module_imports_it_and_sees_what_the_host_does(target: wasmhost.Backend) -> None:
    g = wasmhost.Global("i32", 10, mutable=True)
    ex = wasmhost.Instance(wasmhost.Module(wb.imports_global()), {"env": {"g": g}}).exports
    assert ex.get() == 10
    g.value = 20
    assert ex.get() == 20  # written by the host, read by the module
    ex.inc()
    assert g.value == 21  # written by the module, read by the host


def test_two_instances_share_it(target: wasmhost.Backend) -> None:
    module = wasmhost.Module(wb.imports_global())
    g = wasmhost.Global("i32", 0, mutable=True)
    a = wasmhost.Instance(module, {"env": {"g": g}}).exports
    b = wasmhost.Instance(module, {"env": {"g": g}}).exports
    a.inc()
    a.inc()
    b.inc()
    assert (a.get(), b.get(), g.value) == (3, 3, 3)


def test_an_exported_global_can_be_imported_by_another_instance(target: wasmhost.Backend) -> None:
    donor = wasmhost.Instance(wasmhost.Module(wb.arith())).exports  # exports the mutable i32 `counter` (= 7)
    taker = wasmhost.Instance(wasmhost.Module(wb.imports_global()), {"env": {"g": donor.counter}}).exports
    assert taker.get() == 7
    taker.inc()
    assert donor.counter.value == 8


def test_a_number_is_an_immutable_global(target: wasmhost.Backend) -> None:
    ex = wasmhost.Instance(wasmhost.Module(wb.imports_global(wb.F64, mutable=False)), {"env": {"g": 2.5}}).exports
    assert ex.get() == 2.5
    ex64 = wasmhost.Instance(wasmhost.Module(wb.imports_global(wb.I64, mutable=False)), {"env": {"g": 2**40}}).exports
    assert ex64.get() == 2**40


def test_what_does_not_fit_is_a_linkerror(target: wasmhost.Backend) -> None:
    module = wasmhost.Module(wb.imports_global())  # env.g: a mutable i32
    with pytest.raises(wasmhost.LinkError, match="mutable"):
        wasmhost.Instance(module, {"env": {"g": 5}})  # a mutable one needs a Global, not a number
    with pytest.raises(wasmhost.LinkError, match="is an i64"):
        wasmhost.Instance(module, {"env": {"g": wasmhost.Global("i64", 0, mutable=True)}})
    with pytest.raises(wasmhost.LinkError, match="not a Global"):
        wasmhost.Instance(module, {"env": {"g": "no"}})
    with pytest.raises(wasmhost.LinkError):  # the type is right, the mutability is not
        wasmhost.Instance(module, {"env": {"g": wasmhost.Global("i32", 0, mutable=False)}})


def test_isolated_instances(target: wasmhost.Backend) -> None:
    module = wasmhost.Module(wb.arith())
    if not target.supports("isolated"):
        with pytest.raises(NotImplementedError, match="isolated"):
            wasmhost.Instance(module, isolated=True)
        return
    a = wasmhost.Instance(module, isolated=True).exports
    b = wasmhost.Instance(module, isolated=True).exports
    a.counter.value = 50
    assert b.counter.value == 7  # nothing shared
    assert a.add(1, 2) == 3
    g = wasmhost.Global("i32", 0, mutable=True)
    if target.supports("import.global"):
        with pytest.raises(ValueError, match="another store"):
            wasmhost.Instance(wasmhost.Module(wb.imports_global()), {"env": {"g": g}}, isolated=True)


def test_a_backend_that_cannot_says_so(session: str) -> None:
    backend = wasmhost.get_backend()
    if backend.supports("import.global"):
        pytest.skip("this backend can")
    with pytest.raises(NotImplementedError, match="global"):
        wasmhost.Global("i32", 0)
    with pytest.raises(NotImplementedError):
        wasmhost.Instance(wasmhost.Module(wb.imports_global(mutable=False)), {"env": {"g": 1}})


def test_a_global_of_another_backend(session: str) -> None:
    here = wasmhost.get_backend()
    others = [n for n in ("node", "wasmtime", "jsc") if n != here.name and wasmhost.BACKENDS[n] is not None]
    for name in others:
        try:
            other = wasmhost.get_backend(name)
        except Exception:  # noqa: BLE001 -- not available here
            continue
        if not other.supports("import.global") or not here.supports("import.global"):
            continue
        foreign = wasmhost.Global("i32", 0, mutable=True, backend=other)
        with pytest.raises(wasmhost.LinkError, match="another backend"):
            wasmhost.Instance(wasmhost.Module(wb.imports_global(), backend=here), {"env": {"g": foreign}})
        return
    pytest.skip("only one backend that takes globals is available")
