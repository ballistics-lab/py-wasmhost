"""Native runtimes as backends: the wasm3 interpreter (pywasm3) and wasmtime. No JavaScript, direct calls.

Both are optional: constructing a backend imports its package, so "available" means "installed".

    Wasm3Backend        pywasm3, CPython 3.11+; its PyPI release is years behind the API used here, so
                     install it from git: `uv add "pywasm3 @ git+https://github.com/wasm3/pywasm3"`
    WasmtimeBackend     the `wasmtime` package (`pip install wasmtime`), a JIT
"""

from __future__ import annotations

import importlib
from collections.abc import Sequence
from typing import Any, cast

from ._backend import Backend
from ._binary import FuncType
from ._errors import CompileError, Trap

__all__ = ("Wasm3Backend", "WasmtimeBackend")


def _check_range(offset: int, length: int, size: int) -> None:
    if offset < 0 or length < 0 or offset + length > size:
        raise IndexError("memory access out of bounds")


class _Wasm3Instance:
    def __init__(self, runtime: Any, module: Any) -> None:
        self.runtime = runtime
        self.module = module
        self.functions: dict[str, Any] = {}
        self.memories: dict[str, Any] = {}

    def function(self, name: str) -> Any:
        if (f := self.functions.get(name)) is None:
            f = self.functions[name] = self.runtime.find_function(name)
        return f

    def memory(self, name: str) -> Any:
        if (m := self.memories.get(name)) is None:
            m = self.memories[name] = self.module.get_memory(name)
        return m


class Wasm3Backend(Backend):
    """pywasm3: the wasm3 interpreter as a CPython extension."""

    name = "wasm3"
    # It has no Memory.grow from Python (a module's own memory.grow works, and len(memory) follows) and no tables.
    features = frozenset[str]()
    # wasm3's own value stack, separate from the module's shadow stack in linear memory.
    STACK_BYTES = 256 * 1024

    def __init__(self, stack_size: int = STACK_BYTES) -> None:
        self._wasm3: Any = importlib.import_module("wasm3")  # ImportError here means "backend not available"
        self._env: Any = self._wasm3.Environment()
        self._stack = stack_size

    def validate(self, data: bytes) -> bool:
        try:
            self._env.parse_module(data)
        except Exception:  # noqa: BLE001 -- any parse failure means "not valid"
            return False
        return True

    def compile(self, data: bytes) -> bytes:
        try:
            self._env.parse_module(data)  # to reject what wasm3 can't take, here rather than at instantiation
        except Exception as exc:  # noqa: BLE001
            raise CompileError(str(exc)) from None
        return data  # a wasm3 module belongs to one runtime, so each instance parses it again

    def instantiate(self, module: bytes) -> _Wasm3Instance:
        runtime = self._env.new_runtime(self._stack)
        parsed = self._env.parse_module(module)
        runtime.load(parsed)
        return _Wasm3Instance(runtime, parsed)

    def call(
        self, instance: _Wasm3Instance, name: str, args: Sequence[int | float], ftype: FuncType
    ) -> list[int | float]:
        try:
            result = instance.function(name)(*args)
        except RuntimeError as exc:
            if "[trap]" in str(exc):
                raise Trap(str(exc)) from None
            raise
        if result is None:
            return []
        return list(cast("Sequence[int | float]", result)) if isinstance(result, tuple) else [result]

    def memory_size(self, instance: _Wasm3Instance, name: str) -> int:
        return len(instance.memory(name))

    def memory_grow(self, instance: _Wasm3Instance, name: str, pages: int) -> int:
        raise NotImplementedError("pywasm3 can't grow a memory from Python (a module's own memory.grow works)")

    def memory_read(self, instance: _Wasm3Instance, name: str, offset: int, length: int) -> bytes:
        memory = instance.memory(name)
        _check_range(offset, length, len(memory))
        return bytes(memory[offset : offset + length])

    def memory_write(self, instance: _Wasm3Instance, name: str, offset: int, data: bytes) -> None:
        memory = instance.memory(name)
        _check_range(offset, len(data), len(memory))
        memory[offset : offset + len(data)] = data

    def global_get(self, instance: _Wasm3Instance, name: str, kind: str) -> int | float:
        return instance.module.get_global(name)  # type: ignore[no-any-return]

    def global_set(self, instance: _Wasm3Instance, name: str, kind: str, value: int | float) -> None:
        try:
            instance.module.set_global(name, value)
        except RuntimeError as exc:  # "global is not mutable"
            raise TypeError(str(exc)) from None

    def table_length(self, instance: _Wasm3Instance, name: str) -> int:
        raise NotImplementedError("pywasm3 has no tables API")


class _WasmtimeInstance:
    def __init__(self, store: Any, exports: Any) -> None:
        self.store = store
        self.exports = exports


class WasmtimeBackend(Backend):
    """The `wasmtime` package."""

    name = "wasmtime"

    def __init__(self) -> None:
        self._wt: Any = importlib.import_module("wasmtime")  # ImportError here means "backend not available"
        self._engine: Any = self._wt.Engine()

    def validate(self, data: bytes) -> bool:
        try:
            self._wt.Module.validate(self._engine, data)
        except self._wt.WasmtimeError:
            return False
        return True

    def compile(self, data: bytes) -> Any:
        try:
            return self._wt.Module(self._engine, data)
        except self._wt.WasmtimeError as exc:
            raise CompileError(str(exc)) from None

    def instantiate(self, module: Any) -> _WasmtimeInstance:
        store = self._wt.Store(self._engine)
        instance = self._wt.Instance(store, module, [])
        return _WasmtimeInstance(store, instance.exports(store))

    def call(
        self, instance: _WasmtimeInstance, name: str, args: Sequence[int | float], ftype: FuncType
    ) -> list[int | float]:
        try:
            result = instance.exports[name](instance.store, *args)
        except self._wt.Trap as exc:
            raise Trap(str(exc)) from None
        if result is None:
            return []
        return list(cast("Sequence[int | float]", result)) if isinstance(result, list) else [result]

    def memory_size(self, instance: _WasmtimeInstance, name: str) -> int:
        return int(instance.exports[name].data_len(instance.store))

    def memory_grow(self, instance: _WasmtimeInstance, name: str, pages: int) -> int:
        try:
            return int(instance.exports[name].grow(instance.store, pages))
        except self._wt.WasmtimeError as exc:
            raise IndexError(str(exc)) from None

    def memory_read(self, instance: _WasmtimeInstance, name: str, offset: int, length: int) -> bytes:
        memory = instance.exports[name]
        _check_range(offset, length, int(memory.data_len(instance.store)))
        return bytes(memory.read(instance.store, offset, offset + length))

    def memory_write(self, instance: _WasmtimeInstance, name: str, offset: int, data: bytes) -> None:
        memory = instance.exports[name]
        _check_range(offset, len(data), int(memory.data_len(instance.store)))
        memory.write(instance.store, data, offset)

    def global_get(self, instance: _WasmtimeInstance, name: str, kind: str) -> int | float:
        return instance.exports[name].value(instance.store)  # type: ignore[no-any-return]

    def global_set(self, instance: _WasmtimeInstance, name: str, kind: str, value: int | float) -> None:
        try:
            instance.exports[name].set_value(instance.store, value)
        except self._wt.WasmtimeError as exc:  # an immutable global
            raise TypeError(str(exc)) from None

    def table_length(self, instance: _WasmtimeInstance, name: str) -> int:
        return int(instance.exports[name].size(instance.store))
