"""JSContextBackend against fake Objective-C bridges (objc_util, rubicon-objc) that drive a real engine.

The real thing only exists on an iOS device; this checks that the backend speaks each bridge's protocol
(method names, how exceptions are read and cleared) and that everything above it works through it.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator

import fake_objc
import pytest
import wasm_builder as wb

import wasmhost


@pytest.fixture(params=["objc_util", "rubicon", "objc_util+c"])
def jscontext(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[wasmhost.JSContextBackend]:
    engine = None if request.param == "objc_util+c" else fake_objc.real_engine()
    fake_objc.install(monkeypatch, engine, request.param)
    backend = wasmhost.JSContextBackend()
    yield backend
    backend.close()
    if engine is not None:
        engine.close()


def test_which_bridge(jscontext: wasmhost.JSContextBackend, request: pytest.FixtureRequest) -> None:
    param = request.node.callspec.params["jscontext"]
    assert jscontext.bridge == ("rubicon-objc" if param == "rubicon" else "objc_util")


def test_evaluate_and_errors(jscontext: wasmhost.JSContextBackend) -> None:
    assert jscontext.evaluate("1 + 2") == "3"
    with pytest.raises(RuntimeError, match=r"^\[JS\] .*boom"):
        jscontext.evaluate("throw new Error('boom')")
    assert jscontext.evaluate("'still works'") == "still works"  # the exception was cleared


def test_the_api_through_it(jscontext: wasmhost.JSContextBackend) -> None:
    module, instance = wasmhost.instantiate(wb.arith(), backend=jscontext)
    assert wasmhost.Module.exports(module)
    assert instance.exports.add(20, 22) == 42
    assert instance.exports.add64(2**62, 1) == 2**62 + 1
    with pytest.raises(wasmhost.Trap):
        instance.exports.trap()
    batch = instance.batch()
    total = batch.call(instance.exports.add, 40, 2)
    batch.write(instance.exports.memory, total, b"!")
    echo = batch.read(instance.exports.memory, total, 1)
    batch.run()
    assert (total.value, echo.value) == (42, b"!")


def test_the_selftest_through_it(jscontext: wasmhost.JSContextBackend) -> None:
    lines: list[str] = []
    assert wasmhost.selftest(jscontext, out=lines.append), "\n".join(lines)
    assert any("bridge: " in line for line in lines)


def test_no_bridge_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "objc_util", None)
    monkeypatch.setitem(sys.modules, "rubicon.objc", None)
    with pytest.raises(ImportError, match="Objective-C bridge"):
        wasmhost.JSContextBackend()


def test_the_c_api_path_is_taken_only_where_there_is_one(jscontext: wasmhost.JSContextBackend) -> None:
    has_c_api = jscontext.bridge == "objc_util" and jscontext.supports("imports")
    assert has_c_api == (jscontext.put_bytes("globalThis.b", b"abc") == "C API")
    assert jscontext.get_bytes("b") == b"abc"


def test_host_functions_where_there_is_a_c_api(jscontext: wasmhost.JSContextBackend) -> None:
    imports = {"env": {"plus": lambda a, b: a + b, "note": print, "half": float, "pair": lambda x: (x, x)}}
    if not jscontext.supports("imports"):
        with pytest.raises(NotImplementedError, match="imports"):
            wasmhost.Instance(wasmhost.Module(wb.callbacks(), backend=jscontext), imports)
        return
    calls: list[tuple[int, int]] = []

    def plus(a: int, b: int) -> int:
        calls.append((a, b))
        return a + b

    imports["env"]["plus"] = plus
    instance = wasmhost.Instance(wasmhost.Module(wb.callbacks(), backend=jscontext), imports)
    assert instance.exports.twice(10) == 12
    assert calls == [(10, 1), (11, 1)]
