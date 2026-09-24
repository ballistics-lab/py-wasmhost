"""Which backends exist and how one is picked."""

from __future__ import annotations

import os
from typing import Final

from ._backend import Backend
from ._js import GIJavaScriptCoreBackend, JSBackend, JSContextBackend, NodeBackend
from ._native import Wasm3Backend, WasmtimeBackend

__all__ = ("AUTO_ORDER", "BACKENDS", "JS_AUTO_ORDER", "JS_BACKENDS", "default_backend")

BACKENDS: Final[dict[str, type[Backend]]] = {
    "jscontext": JSContextBackend,
    "wasmtime": WasmtimeBackend,
    "wasm3": Wasm3Backend,
    "gi-jsc": GIJavaScriptCoreBackend,
    "node": NodeBackend,
}
# The ones that are JavaScript engines: a package that has JavaScript of its own to run needs these.
JS_BACKENDS: Final[dict[str, type[JSBackend]]] = {
    "jscontext": JSContextBackend,
    "gi-jsc": GIJavaScriptCoreBackend,
    "node": NodeBackend,
}

# Tried in this order when nothing is chosen: Pythonista's JSContext first (it only exists there), then
# the in-process runtimes when installed -- wasmtime (a JIT), wasm3 (an interpreter) -- then WebKitGTK's
# JavaScriptCore (Linux with PyGObject), then Node. Each constructor is its own availability probe: it raises
# when its runtime isn't there (ImportError for objc_util/wasmtime/gi, a missing `node` binary, an engine
# without WebAssembly), so "available" means "could actually start".
AUTO_ORDER: Final[tuple[str, ...]] = ("jscontext", "wasmtime", "wasm3", "gi-jsc", "node")
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
        "(`pip install wasmtime`), install PyGObject with JavaScriptCore, or put node on PATH.".format(
            "; ".join(errors)
        )
    )
