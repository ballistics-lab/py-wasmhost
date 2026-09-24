"""Fake Objective-C bridges for testing JSContextBackend off a device.

`install(monkeypatch, engine, "objc_util")` or `(..., "rubicon")` makes that bridge importable, backed by a real
JavaScript engine (`engine.evaluate`) in place of the device's JSContext, and returns nothing to clean up (the
monkeypatch does it). They copy each bridge's protocol -- objc_util: selectors as `name_` methods and properties
as methods; rubicon-objc: selectors as methods with positional arguments and properties as attributes -- so what
is checked is that the backend speaks it, not that the bridge behaves like the real one.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

import wasmhost


class _Value:
    def __init__(self, text: str) -> None:
        self._text = text

    def toString(self) -> str:  # noqa: N802 -- the Objective-C name
        return self._text


class ObjcUtilContext:
    def __init__(self, engine: wasmhost.JSBackend) -> None:
        self._engine = engine
        self._exception: _Value | None = None

    def evaluateScript_(self, src: str) -> _Value:  # noqa: N802
        try:
            return _Value(self._engine.evaluate(src))
        except RuntimeError as exc:
            self._exception = _Value(str(exc).removeprefix("[JS] "))
            return _Value("undefined")

    def exception(self) -> _Value | None:
        return self._exception

    def setException_(self, value: None) -> None:  # noqa: N802
        self._exception = value


class RubiconContext:
    def __init__(self, engine: wasmhost.JSBackend) -> None:
        self._engine = engine
        self.exception: _Value | None = None

    def evaluateScript(self, src: str) -> _Value:  # noqa: N802
        try:
            return _Value(self._engine.evaluate(src))
        except RuntimeError as exc:
            self.exception = _Value(str(exc).removeprefix("[JS] "))
            return _Value("undefined")


class _Class:
    def __init__(self, context: Any) -> None:
        self._context = context

    def alloc(self) -> _Class:
        return self

    def init(self) -> Any:
        return self._context


def _module(name: str, context: Any) -> types.ModuleType:
    module = types.ModuleType(name)
    module.ObjCClass = lambda cls: _Class(context)  # type: ignore[attr-defined]
    return module


def real_engine() -> wasmhost.JSBackend:
    """A real JavaScript engine to stand in for the device's JSContext, or a skip."""
    for name in wasmhost.JS_AUTO_ORDER:
        if name == "jscontext":
            continue
        try:
            return wasmhost.JS_BACKENDS[name]()
        except Exception:  # noqa: BLE001 -- not available here
            continue
    pytest.skip("no JavaScript engine to stand in for a JSContext")


def install(monkeypatch: pytest.MonkeyPatch, engine: wasmhost.JSBackend, bridge: str) -> None:
    if bridge == "objc_util":
        monkeypatch.setitem(sys.modules, "objc_util", _module("objc_util", ObjcUtilContext(engine)))
    elif bridge == "rubicon":
        monkeypatch.setitem(sys.modules, "objc_util", None)  # not importable: the fallback is taken
        monkeypatch.setitem(sys.modules, "rubicon", types.ModuleType("rubicon"))
        monkeypatch.setitem(sys.modules, "rubicon.objc", _module("rubicon.objc", RubiconContext(engine)))
    else:
        raise ValueError(bridge)
