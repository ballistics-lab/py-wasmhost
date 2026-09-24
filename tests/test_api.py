import math

import pytest
import wasm_builder as wb

import wasmhost


@pytest.fixture
def inst(session: str) -> wasmhost.Instance:
    return wasmhost.Instance(wasmhost.Module(wb.arith()))


def test_backend_is_the_chosen_one(session: str) -> None:
    assert wasmhost.get_backend().name == session


def test_integer_and_float_calls(inst: wasmhost.Instance) -> None:
    assert inst.exports.add(2, 3) == 5
    assert inst.exports.add(2**31 - 1, 1) == -(2**31)  # i32 wraps, as in JavaScript
    assert inst.exports.fadd(0.1, 0.2) == 0.1 + 0.2
    assert inst.exports.twice(1.5) == 3.0
    assert math.isnan(inst.exports.fadd(math.nan, 1.0))
    assert inst.exports.fadd(math.inf, 1.0) == math.inf
    assert str(inst.exports.fadd(-0.0, -0.0)) == "-0.0"


def test_i64_is_exact(inst: wasmhost.Instance) -> None:
    big = 2**62 + 12345  # not representable as a double
    assert inst.exports.add64(big, 1) == big + 1
    assert inst.exports.add64(-(2**63), -1) == 2**63 - 1  # wraps


def test_no_result_and_multi_value(inst: wasmhost.Instance) -> None:
    assert inst.exports.store8(0, 1) is None
    assert inst.exports.dup(41) == (41, 41)


def test_argument_checks(inst: wasmhost.Instance) -> None:
    with pytest.raises(TypeError, match="takes 2 arguments"):
        inst.exports.add(1)
    with pytest.raises(TypeError, match="i32"):
        inst.exports.add(1.5, 2)
    with pytest.raises(TypeError, match="i64"):
        inst.exports.add64("1", 2)


def test_trap(inst: wasmhost.Instance) -> None:
    with pytest.raises(wasmhost.Trap):
        inst.exports.trap()
    assert inst.exports.add(1, 1) == 2  # the instance survives a trap


def test_memory(inst: wasmhost.Instance) -> None:
    mem = inst.exports.memory
    assert isinstance(mem, wasmhost.Memory)
    assert len(mem) == 65536
    mem.write(100, b"hello")
    assert mem.read(100, 5) == b"hello"
    assert mem[100:105] == b"hello"
    mem[100:105] = b"HELLO"
    assert mem[100] == ord("H")
    inst.exports.store8(200, 0x1FF)  # only the low byte is stored
    assert mem[200] == 0xFF
    assert mem[-1] == 0
    with pytest.raises(IndexError):
        mem.read(65535, 2)
    with pytest.raises(IndexError):
        mem.write(65535, b"xx")
    with pytest.raises(ValueError):
        mem[0:2] = b"abc"


def test_memory_follows_the_modules_own_grow(inst: wasmhost.Instance) -> None:
    mem = inst.exports.memory
    assert inst.exports.grow(1) == 1
    assert len(mem) == 2 * 65536  # the same object sees the new size
    assert inst.exports.grow(10) == -1  # past the max of 4 pages
    mem.write(65536 + 10, b"far")
    assert mem[65536 + 10 : 65536 + 13] == b"far"


def test_memory_grow_from_python(inst: wasmhost.Instance) -> None:
    if not wasmhost.get_backend().supports("memory.grow"):
        with pytest.raises(NotImplementedError):
            inst.exports.memory.grow(1)
        return
    mem = inst.exports.memory
    assert mem.grow(1) == 1
    assert len(mem) == 2 * 65536
    assert inst.exports.grow(1) == 2  # the module's own memory.grow, seen through the same object
    assert len(mem) == 3 * 65536
    with pytest.raises(IndexError):
        mem.grow(10)  # past the max


def test_globals(inst: wasmhost.Instance) -> None:
    counter, ten = inst.exports.counter, inst.exports.ten
    assert isinstance(counter, wasmhost.Global)
    assert counter.value == 7 and ten.value == 11
    counter.value = 99
    assert counter.value == 99
    with pytest.raises(TypeError):
        ten.value = 1  # immutable


def test_exports_object(inst: wasmhost.Instance) -> None:
    assert "add" in inst.exports and "nope" not in inst.exports
    assert inst.exports["add"] is inst.exports.add
    assert {"add", "memory", "counter"} <= set(inst.exports)
    with pytest.raises(AttributeError):
        inst.exports.nope  # noqa: B018


def test_module_descriptors(session: str) -> None:
    mod = wasmhost.Module(wb.arith())
    names = {e.name: e.kind for e in wasmhost.Module.exports(mod)}
    assert names["add"] == "function" and names["memory"] == "memory" and names["counter"] == "global"
    assert wasmhost.Module.imports(mod) == []


def test_validate_and_compile_errors(session: str) -> None:
    assert wasmhost.validate(wb.arith())
    assert not wasmhost.validate(b"\0asm\x01\x00\x00\x00\x01\x02")
    with pytest.raises(wasmhost.CompileError):
        wasmhost.Module(b"not wasm")
    truncated = wb.arith()[:-3]
    with pytest.raises(wasmhost.CompileError):
        wasmhost.Module(truncated)


def test_a_module_that_imports_needs_an_import_object(session: str) -> None:
    mod = wasmhost.Module(wb.needs_import())
    with pytest.raises(TypeError, match="env.f"):
        wasmhost.Instance(mod)  # what it does with one is in test_imports.py


def test_instantiate_shortcut(session: str) -> None:
    module, instance = wasmhost.instantiate(wb.arith())
    assert instance.exports.add(20, 22) == 42
    assert wasmhost.Module.exports(module)


def test_instances_are_independent(session: str) -> None:
    mod = wasmhost.Module(wb.arith())
    a, b = wasmhost.Instance(mod), wasmhost.Instance(mod)
    a.exports.counter.value = 1
    assert b.exports.counter.value == 7
    a.exports.memory.write(0, b"a")
    assert b.exports.memory.read(0, 1) == b"\0"


def test_batch_runs_in_one_trip_and_chains_results(inst: wasmhost.Instance) -> None:
    mem = inst.exports.memory
    b = inst.batch()
    three = b.call(inst.exports.add, 1, 2)
    assert isinstance(three, wasmhost.Ref)
    stored = b.call(inst.exports.store8, three * 100, 0x41)  # 300: a Ref used in arithmetic, as an argument
    b.write(mem, three + 397, b"xyz")  # ... and as an offset (400)
    back = b.read(mem, three * 100, 1)
    text = b.read(mem, three + 397, three)
    b.run()
    assert three.value == 3 and back.value == b"A" and text.value == b"xyz"
    assert stored.done and stored.value is None  # a call without a result: done, and None
    assert mem.read(400, 3) == b"xyz"


def test_batch_does_a_single_evaluate(inst: wasmhost.Instance, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = wasmhost.get_backend()
    if not isinstance(engine, wasmhost.JSBackend):
        pytest.skip("only a JavaScript engine has trips to save")
    calls: list[str] = []
    real = engine.evaluate
    monkeypatch.setattr(engine, "evaluate", lambda src: calls.append(src) or real(src))
    b = inst.batch()
    x = b.call(inst.exports.add, 20, 22)
    y = b.call(inst.exports.fadd, 0.5, 0.25)
    z = b.call(inst.exports.add64, 2**40, 1)
    b.run()
    assert (x.value, y.value, z.value) == (42, 0.75, 2**40 + 1)
    assert len(calls) == 1  # one trip for the three calls


def test_batch_stop_if(inst: wasmhost.Instance) -> None:
    b = inst.batch()
    zero = b.call(inst.exports.add, 0, 0)
    b.stop_if_zero(zero)
    after = b.call(inst.exports.add, 1, 1)
    b.run()
    assert zero.done and not after.done
    with pytest.raises(RuntimeError, match="has not run"):
        after.value  # noqa: B018
    b = inst.batch()
    nonzero = b.call(inst.exports.add, 1, 0)
    b.stop_if_nonzero(nonzero)
    after = b.call(inst.exports.add, 1, 1)
    b.run()
    assert nonzero.value == 1 and not after.done


def test_batch_error_keeps_earlier_results(inst: wasmhost.Instance) -> None:
    b = inst.batch()
    ok = b.call(inst.exports.add, 1, 1)
    b.call(inst.exports.trap)
    later = b.call(inst.exports.add, 2, 2)
    with pytest.raises(wasmhost.Trap):
        b.run()
    assert ok.value == 2 and not later.done
    b = inst.batch()
    b.read(inst.exports.memory, 65535, 2)
    with pytest.raises(IndexError):
        b.run()


def test_batch_context_manager_and_checks(inst: wasmhost.Instance) -> None:
    with inst.batch() as b:
        r = b.call(inst.exports.add, 4, 5)
    assert r.value == 9
    other = wasmhost.Instance(wasmhost.Module(wb.arith()))
    with pytest.raises(ValueError):
        inst.batch().call(other.exports.add, 1, 2)
    with pytest.raises(TypeError, match="i32"):
        inst.batch().call(inst.exports.add64, b.call(inst.exports.add, 1, 1), 1)
    with pytest.raises(RuntimeError, match="already run"):
        b.run()
