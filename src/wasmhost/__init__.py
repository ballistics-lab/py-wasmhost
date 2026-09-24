"""wasmhost: run WebAssembly from CPython, PyPy and Pythonista, with the JavaScript WebAssembly API.

`Module`, `Instance`, `Memory` and `Global` run on a backend: JavaScriptCore's `JSContext` (Pythonista on iOS),
WebKitGTK's JavaScriptCore (Linux), Node, or a native runtime (wasmtime, wasm3). Plain Python, no dependencies,
no C extension of its own.
"""

from ._api import (
    Batch,
    CompileError,
    Function,
    Global,
    Instance,
    Instantiated,
    LinkError,
    Memory,
    Module,
    Ref,
    Table,
    Trap,
    WasmError,
    close,
    get_backend,
    instantiate,
    set_backend,
    validate,
)
from ._backend import Backend
from ._binary import ExportDescriptor, FuncType, ImportDescriptor
from ._js import GIJavaScriptCoreBackend, JSBackend, JSContextBackend, NodeBackend
from ._jsc import JSCBackend
from ._native import Wasm3Backend, WasmtimeBackend
from ._registry import AUTO_ORDER, BACKENDS, JS_AUTO_ORDER, JS_BACKENDS, default_backend
from ._selftest import selftest

__all__ = (
    "AUTO_ORDER",
    "BACKENDS",
    "JS_AUTO_ORDER",
    "JS_BACKENDS",
    "Backend",
    "Batch",
    "CompileError",
    "ExportDescriptor",
    "FuncType",
    "Function",
    "GIJavaScriptCoreBackend",
    "Global",
    "ImportDescriptor",
    "Instance",
    "Instantiated",
    "JSBackend",
    "JSCBackend",
    "JSContextBackend",
    "LinkError",
    "Memory",
    "Module",
    "NodeBackend",
    "Ref",
    "Table",
    "Trap",
    "Wasm3Backend",
    "WasmError",
    "WasmtimeBackend",
    "close",
    "default_backend",
    "get_backend",
    "instantiate",
    "selftest",
    "set_backend",
    "validate",
)
