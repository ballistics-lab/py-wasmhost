"""Which backends exist and how one is picked."""

from __future__ import annotations

import os
from typing import Final

from ._backend import Backend
from ._js import BunBackend, GIJavaScriptCoreBackend, JSBackend, JSContextBackend, NodeBackend
from ._jsc import JSCBackend
from ._native import Wasm3Backend, WasmtimeBackend
from ._pyodide import PyodideBackend

__all__ = ("AUTO_ORDER", "BACKENDS", "JS_AUTO_ORDER", "JS_BACKENDS", "default_backend")

BACKENDS: Final[dict[str, type[Backend]]] = {
    "wasmtime": WasmtimeBackend,
    "node": NodeBackend,
    "bun": BunBackend,
    "jsc": JSCBackend,
    "gi-jsc": GIJavaScriptCoreBackend,
    "wasm3": Wasm3Backend,
    "jscontext": JSContextBackend,
    "pyodide": PyodideBackend,
}
# The ones that are JavaScript engines: a package that has JavaScript of its own to run needs these.
JS_BACKENDS: Final[dict[str, type[JSBackend]]] = {
    "node": NodeBackend,
    "bun": BunBackend,
    "jsc": JSCBackend,
    "gi-jsc": GIJavaScriptCoreBackend,
    "jscontext": JSContextBackend,
}

# Tried in this order when nothing is chosen: by speed first (a JIT before an interpreter), then by how much of
# WebAssembly the runtime runs, then by what it costs to run it. The JITs: wasmtime (in process), Node and Bun (a
# process of their own, so a call costs more), JavaScriptCore through its C API and through PyGObject (in process).
# Then the interpreters: wasm3 (several times faster than JSContext without a JIT) and JSContext (iOS, or a Mac with
# rubicon-objc; iOS gives it no JIT for wasm, and a C extension such as wasm3 can't be installed there, so on
# Pythonista it is the pick). Last Pyodide, the only one that can start inside it. Each constructor is its own
# availability probe: it raises when its runtime isn't there (ImportError for objc_util/wasmtime/gi/js, a missing
# `node` or `bun` binary, an engine without WebAssembly), so "available" means "could actually start".
AUTO_ORDER: Final[tuple[str, ...]] = ("wasmtime", "node", "bun", "jsc", "gi-jsc", "wasm3", "jscontext", "pyodide")
JS_AUTO_ORDER: Final[tuple[str, ...]] = tuple(n for n in AUTO_ORDER if n in JS_BACKENDS)


def default_backend(env_var: str = "WASMHOST_BACKEND", *, js_only: bool = False) -> Backend:
    """Start a backend: the one named by the environment variable `env_var` if set, else the first that starts.

    The variable is a parameter so a package built on wasmhost can have its own (mpwasm's MPWASM_HOST);
    `js_only` limits the choice to JavaScript engines, for a package that has JavaScript of its own to run.
    """
    backends: dict[str, type[Backend]] = dict(JS_BACKENDS) if js_only else dict(BACKENDS)
    choice = os.environ.get(env_var, "").lower()
    if choice:
        if choice not in backends:
            raise ValueError(f"{env_var}={choice!r}: expected one of {', '.join(backends)}")
        return backends[choice]()
    errors: list[str] = []
    for name in JS_AUTO_ORDER if js_only else AUTO_ORDER:
        try:
            return backends[name]()
        except Exception as exc:  # noqa: BLE001 -- not available here; try the next one
            errors.append(f"{name}: {exc}")
    raise RuntimeError(
        "No WebAssembly backend available (tried {}). Run in Pythonista, install wasmtime "
        "(`pip install wasmtime`), install PyGObject with JavaScriptCore, or put node (or bun) on PATH.".format(
            "; ".join(errors)
        )
    )
