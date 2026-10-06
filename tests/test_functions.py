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


def _unknown_entry() -> wasmhost.Function:
    """An entry of a table that no one told the type of: a module with a start function, so that what its `elem`
    segment put in the table is not assumed (on a JavaScript engine)."""
    from test_elem_signatures import module  # noqa: PLC0415

    entry = wasmhost.Instance(wasmhost.Module(module(start=True))).exports.table.get(0)
    assert entry is not None
    return entry


def test_a_wrong_signature_is_refused_not_obeyed(session: str) -> None:
    if not wasmhost.get_backend().supports("table.funcs"):
        pytest.skip("no functions in tables on this backend")
    if wasmhost.get_backend().supports("table.signatures"):
        pytest.skip("this engine tells the type of every function, so a wrong one can't be given")
    entry = _unknown_entry()
    assert entry.signature is None  # the engine does not tell it
    entry.signature = FuncType((i64, i64), (i64,))  # add is (i32, i32) -> i32
    with pytest.raises((TypeError, ValueError)):
        entry(1, 2)
    entry.signature = ADD  # the right one, by hand: it works
    assert entry(1, 2) == 3


def test_wasmtime_key_of_a_function_is_what_we_think_it_is(session: str) -> None:
    """The key that makes one function one object on wasmtime reads wasmtime-py's private fields (`store_id` and the
    mangled `__private`): if a new wasmtime-py renames them, this says so, instead of every table entry turning into
    a new object."""
    if wasmhost.get_backend().name != "wasmtime":
        pytest.skip("only wasmtime-py makes a new object for each table.get")
    provider = wasmhost.Instance(wasmhost.Module(wb.tables(imported=False)))
    raw = provider.exports.add._h.obj._func  # pyright: ignore[reportPrivateUsage]
    assert isinstance(int(raw.store_id), int) and isinstance(int(getattr(raw, "__private")), int)  # noqa: B009
    table = provider.exports.table
    table.set(0, provider.exports.add)
    first, second = table.get(0), table.get(0)
    assert first is second and first is provider.exports.add


def test_instantiate_sync_of_a_module_is_an_instance(session: str) -> None:
    module = wasmhost.Module(wb.arith())
    instance = wasmhost.instantiate_sync(module)
    assert isinstance(instance, wasmhost.Instance) and instance.exports.add(1, 2) == 3
    both = wasmhost.instantiate_sync(wb.arith())
    assert isinstance(both, wasmhost.Instantiated)


def test_the_features_the_new_code_relies_on(session: str) -> None:
    backend = wasmhost.get_backend()
    # "table.signatures": the engine refuses a wrong type by itself (wasmtime only);
    # "threads": a worker thread is allowed
    assert backend.supports("table.signatures") == (backend.name == "wasmtime")
    assert backend.supports("threads") == (backend.name in ("wasmtime", "node", "bun"))


def test_the_trampoline_module_is_valid_for_any_callable_type(session: str) -> None:
    from wasmhost._trampoline import module_for  # noqa: PLC0415

    for ftype in (
        FuncType((), ()),
        FuncType((i32,), ()),
        FuncType((i64, wasmhost.f32, wasmhost.f64), (i32,)),
        FuncType((i32, i32), (i32, i64)),  # several results
    ):
        assert wasmhost.validate(module_for(ftype)), ftype
