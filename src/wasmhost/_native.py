"""Native runtimes as backends: the wasm3 interpreter (pywasm3) and wasmtime. No JavaScript, direct calls.

Both are optional: constructing a backend imports its package, so "available" means "installed".

    Wasm3Backend        pywasm3, CPython 3.11+; its PyPI release is years behind the API used here, so
                     install it from git: `uv add "pywasm3 @ git+https://github.com/wasm3/pywasm3"`
    WasmtimeBackend     the `wasmtime` package (`pip install wasmtime`), a JIT
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Sequence
from typing import Any, NamedTuple, cast

from ._backend import Backend, HostFunction, check_results
from ._binary import FuncType
from ._errors import CompileError, Trap

__all__ = ("Wasm3Backend", "WasmtimeBackend")


def _check_range(offset: int, length: int, size: int) -> None:
    if offset < 0 or length < 0 or offset + length > size:
        raise IndexError("memory access out of bounds")


_WASM3_CODES = {"i32": "i", "i64": "I", "f32": "f", "f64": "F"}


def _wasm3_signature(ftype: FuncType) -> str:
    """wasm3's spelling of a signature: the result types, then the parameters in parentheses (`v` for none)."""
    results = "".join(_WASM3_CODES[k] for k in ftype.results) or "v"
    return f"{results}({''.join(_WASM3_CODES[k] for k in ftype.params)})"


def _wasm3_callable(host: HostFunction) -> Callable[..., Any]:
    def call(*args: Any) -> Any:
        values = check_results(host.fn(*args), host.ftype)
        return values[0] if len(values) == 1 else tuple(values) if values else None

    return call


class _Wasm3Global(NamedTuple):
    module: Any
    name: str


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
    features = frozenset({"imports"})
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

    def instantiate(self, module: bytes, imports: Sequence[HostFunction] = ()) -> _Wasm3Instance:
        runtime = self._env.new_runtime(self._stack)
        parsed = self._env.parse_module(module)
        runtime.load(parsed)
        for host in imports:  # linked after loading, before the first call
            parsed.link_function(host.module, host.name, _wasm3_signature(host.ftype), _wasm3_callable(host))
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

    def export_memory(self, instance: _Wasm3Instance, name: str) -> Any:
        return instance.memory(name)  # pywasm3's Memory: looked up afresh on every access, so it survives a grow

    def export_global(self, instance: _Wasm3Instance, name: str, kind: str) -> _Wasm3Global:
        return _Wasm3Global(instance.module, name)

    def export_table(self, instance: _Wasm3Instance, name: str) -> str:
        return name  # pywasm3 has no tables API: a placeholder, so an instance that exports one can still be made

    def memory_size(self, memory: Any) -> int:
        return len(memory)

    def memory_grow(self, memory: Any, pages: int) -> int:
        raise NotImplementedError("pywasm3 can't grow a memory from Python (a module's own memory.grow works)")

    def memory_read(self, memory: Any, offset: int, length: int) -> bytes:
        _check_range(offset, length, len(memory))
        return bytes(memory[offset : offset + length])

    def memory_write(self, memory: Any, offset: int, data: bytes) -> None:
        _check_range(offset, len(data), len(memory))
        memory[offset : offset + len(data)] = data

    def global_get(self, glob: _Wasm3Global, kind: str) -> int | float:
        return glob.module.get_global(glob.name)  # type: ignore[no-any-return]

    def global_set(self, glob: _Wasm3Global, kind: str, value: int | float) -> None:
        try:
            glob.module.set_global(glob.name, value)
        except RuntimeError as exc:  # "global is not mutable"
            raise TypeError(str(exc)) from None

    def table_length(self, table: Any) -> int:
        raise NotImplementedError("pywasm3 has no tables API")


class _WasmtimeObject(NamedTuple):
    """An exported memory, global or table, with the store it belongs to (every call needs it)."""

    store: Any
    obj: Any


class _WasmtimeInstance:
    def __init__(self, store: Any, exports: Any) -> None:
        self.store = store
        self.exports = exports


class WasmtimeBackend(Backend):
    """The `wasmtime` package."""

    name = "wasmtime"
    features = frozenset({"memory.grow", "table.length", "imports"})

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

    def instantiate(self, module: Any, imports: Sequence[HostFunction] = ()) -> _WasmtimeInstance:
        store = self._wt.Store(self._engine)
        funcs = [self._host_func(store, host) for host in imports]
        instance = self._wt.Instance(store, module, funcs)
        return _WasmtimeInstance(store, instance.exports(store))

    def _host_func(self, store: Any, host: HostFunction) -> Any:
        wt = self._wt
        valtypes = {kind: getattr(wt.ValType, kind)() for kind in ("i32", "i64", "f32", "f64")}
        ftype = wt.FuncType([valtypes[k] for k in host.ftype.params], [valtypes[k] for k in host.ftype.results])

        def call(*args: Any) -> Any:
            values = check_results(host.fn(*args), host.ftype)
            return values[0] if len(values) == 1 else values if values else None

        return wt.Func(store, ftype, call)

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

    def export_memory(self, instance: _WasmtimeInstance, name: str) -> _WasmtimeObject:
        return _WasmtimeObject(instance.store, instance.exports[name])

    def export_global(self, instance: _WasmtimeInstance, name: str, kind: str) -> _WasmtimeObject:
        return _WasmtimeObject(instance.store, instance.exports[name])

    def export_table(self, instance: _WasmtimeInstance, name: str) -> _WasmtimeObject:
        return _WasmtimeObject(instance.store, instance.exports[name])

    def memory_size(self, memory: _WasmtimeObject) -> int:
        return int(memory.obj.data_len(memory.store))

    def memory_grow(self, memory: _WasmtimeObject, pages: int) -> int:
        try:
            return int(memory.obj.grow(memory.store, pages))
        except self._wt.WasmtimeError as exc:
            raise IndexError(str(exc)) from None

    def memory_read(self, memory: _WasmtimeObject, offset: int, length: int) -> bytes:
        _check_range(offset, length, int(memory.obj.data_len(memory.store)))
        return bytes(memory.obj.read(memory.store, offset, offset + length))

    def memory_write(self, memory: _WasmtimeObject, offset: int, data: bytes) -> None:
        _check_range(offset, len(data), int(memory.obj.data_len(memory.store)))
        memory.obj.write(memory.store, data, offset)

    def global_get(self, glob: _WasmtimeObject, kind: str) -> int | float:
        return glob.obj.value(glob.store)  # type: ignore[no-any-return]

    def global_set(self, glob: _WasmtimeObject, kind: str, value: int | float) -> None:
        try:
            glob.obj.set_value(glob.store, value)
        except self._wt.WasmtimeError as exc:  # an immutable global
            raise TypeError(str(exc)) from None

    def table_length(self, table: _WasmtimeObject) -> int:
        return int(table.obj.size(table.store))
