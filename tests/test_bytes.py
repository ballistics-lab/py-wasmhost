"""The bytes bridge of the JavaScript backends: a Uint8Array in and out, by hex or (JSContext) the C API."""

from __future__ import annotations

import sys
import types
from typing import Any

import fake_objc
import pytest

import wasmhost


@pytest.fixture
def engine(session: str) -> wasmhost.JSBackend:
    backend = wasmhost.get_backend()
    if not isinstance(backend, wasmhost.JSBackend):
        pytest.skip("only a JavaScript engine has this bridge")
    return backend


@pytest.mark.parametrize("size", [0, 1, 255, 70_000])
def test_round_trip(engine: wasmhost.JSBackend, size: int) -> None:
    data = bytes(i % 256 for i in range(size))
    assert engine.put_bytes("globalThis.blob", data) == "hex"
    assert engine.evaluate("String(blob.length)") == str(size)
    assert engine.get_bytes("blob") == data


def test_into_a_nested_target_and_from_a_view(engine: wasmhost.JSBackend) -> None:
    engine.evaluate("globalThis.files = {}")
    engine.put_bytes("files['/a/b.bin']", b"\x00\x01\x02\x03\x04\x05")
    assert engine.get_bytes("files['/a/b.bin'].subarray(2, 5)") == b"\x02\x03\x04"


def test_not_bytes_is_an_error(engine: wasmhost.JSBackend) -> None:
    with pytest.raises(TypeError):  # JavaScript's TypeError is Python's (as everywhere in wasmhost)
        engine.get_bytes("undefined")


def _jscontext_with_broken_c_api(monkeypatch: pytest.MonkeyPatch, engine: wasmhost.JSBackend) -> Any:
    """A JSContext whose objc_util has a `c` that can't do what the C API path asks."""
    fake_objc.install(monkeypatch, engine, "objc_util")

    class Broken:
        def __getattr__(self, name: str) -> Any:
            def fail(*args: object) -> None:
                raise OSError(f"{name} is not available")

            return fail

    module: types.ModuleType = sys.modules["objc_util"]
    module.c = Broken()  # type: ignore[attr-defined]
    return wasmhost.JSContextBackend()


def test_jscontext_falls_back_to_hex_when_the_c_api_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = fake_objc.real_engine()
    backend = _jscontext_with_broken_c_api(monkeypatch, engine)
    assert (
        backend._c is not None
    )  # signatures could be set: it is the calls that fail  # pyright: ignore[reportPrivateUsage]
    assert backend.put_bytes("globalThis.blob", b"abc") == "hex"  # the C API was tried, failed, and is off now
    assert backend._c is None  # pyright: ignore[reportPrivateUsage]
    assert backend.get_bytes("blob") == b"abc"
    backend.close()
    engine.close()


def test_jscontext_without_a_c_api_uses_hex(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = fake_objc.real_engine()
    fake_objc.install(monkeypatch, engine, "objc_util")  # this fake has no `c` at all
    backend = wasmhost.JSContextBackend()
    assert backend._c is None  # pyright: ignore[reportPrivateUsage]
    assert backend.put_bytes("globalThis.blob", b"xyz") == "hex"
    assert backend.get_bytes("blob") == b"xyz"
    backend.close()
    engine.close()
