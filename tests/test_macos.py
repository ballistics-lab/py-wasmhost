"""A Mac's own JSContext, through rubicon-objc: the real Objective-C class, not a fake.

`objc_util` is Pythonista's and exists only there; but rubicon-objc reaches the same `JSContext`
(JavaScriptCore.framework) from CPython on a Mac, so CI can run this backend against the real class, and the C API
(bytes, host functions) in the same process, the way it runs on an iPhone. Skipped anywhere else.
"""

from __future__ import annotations

import sys

import pytest
import wasm_builder as wb

import wasmhost

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="a Mac's JavaScriptCore.framework")


@pytest.fixture
def jscontext() -> wasmhost.JSContextBackend:
    try:
        return wasmhost.JSContextBackend()
    except ImportError:
        pytest.skip("rubicon-objc is not installed")


def test_it_is_the_real_thing(jscontext: wasmhost.JSContextBackend) -> None:
    assert jscontext.bridge == "rubicon-objc"
    assert jscontext.evaluate("1 + 2") == "3"
    with pytest.raises(RuntimeError, match=r"^\[JS\] .*boom"):
        jscontext.evaluate("throw new Error('boom')")
    assert jscontext.evaluate("'still works'") == "still works"  # the exception was cleared


def test_the_c_api_is_there_too(jscontext: wasmhost.JSContextBackend) -> None:
    assert jscontext.supports("imports")
    assert jscontext.put_bytes("globalThis.blob", bytes(range(256)) * 10) == "C API"
    assert jscontext.get_bytes("blob") == bytes(range(256)) * 10


def test_host_functions_on_the_real_jscontext(jscontext: wasmhost.JSContextBackend) -> None:
    calls: list[tuple[int, int]] = []

    def plus(a: int, b: int) -> int:
        calls.append((a, b))
        return a + b

    imports = {"env": {"plus": plus, "note": lambda x: None, "half": lambda x: x / 2, "pair": lambda x: (x, x + 1)}}
    instance = wasmhost.Instance(wasmhost.Module(wb.callbacks(), backend=jscontext), imports)
    assert instance.exports.twice(10) == 12
    assert calls == [(10, 1), (11, 1)]
    assert instance.exports.call_pair(3) == (3, 4)


def test_the_selftest(jscontext: wasmhost.JSContextBackend) -> None:
    lines: list[str] = []
    assert wasmhost.selftest(jscontext, out=lines.append), "\n".join(lines)
    assert any("bridge: rubicon-objc" in line for line in lines)
