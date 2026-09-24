# pyright: reportPrivateUsage=false
# The classes of this module (Module, Instance, Function, Memory, Global) share their handles on purpose.
"""The public API, shaped like the JavaScript WebAssembly API (Module, Instance, Memory, Global).

    mod = Module(wasm_bytes)                 # WebAssembly.Module
    inst = Instance(mod)                     # WebAssembly.Instance
    inst.exports.add(2, 3)                   # exported functions are callables; i64 is an int, f32/f64 a float
    inst.exports.memory.write(ptr, data)     # WebAssembly.Memory: read / write / slicing / grow
    inst.exports.counter.value               # WebAssembly.Global

It runs on a backend (see `_backend.py`): a JavaScript engine's own `WebAssembly` object, or a native runtime
(wasmtime, wasm3). The backend is whichever starts first, or the one you pick with `backend=`.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any, NamedTuple

from ._backend import Backend, CallStep, Expr, Operand, ReadStep, Step, StopStep, WriteStep, normalize
from ._binary import ExportDescriptor, FuncType, ImportDescriptor, ModuleInfo, parse
from ._errors import CompileError, LinkError, Trap, WasmError
from ._registry import BACKENDS, default_backend

__all__ = (
    "Batch",
    "CompileError",
    "Function",
    "Global",
    "Instance",
    "Instantiated",
    "LinkError",
    "Memory",
    "Module",
    "Ref",
    "Table",
    "Trap",
    "WasmError",
    "close",
    "get_backend",
    "instantiate",
    "set_backend",
    "validate",
)

PAGE_SIZE = 65536

_default: Backend | None = None
_started: dict[
    str, Backend
] = {}  # backends started by name, shared, so `backend="node"` doesn't start a process each time


def _backend(backend: Backend | str | None) -> Backend:
    global _default
    if backend is None:
        if _default is None:
            _default = default_backend()
        return _default
    if isinstance(backend, str):
        if _default is not None and _default.name == backend:
            return _default
        if backend not in BACKENDS:
            raise ValueError(f"unknown backend {backend!r}: expected one of {', '.join(BACKENDS)}")
        if backend not in _started:
            _started[backend] = BACKENDS[backend]()
        return _started[backend]
    return backend


def get_backend(backend: Backend | str | None = None) -> Backend:
    """The default backend (started on first use); or, given a name, the backend of that name (started once and
    shared, like `backend="node"` in `Module`)."""
    return _backend(backend)


def set_backend(backend: Backend | str | None) -> None:
    """Choose the default backend by name (`"node"`, `"gi-jsc"`, `"jscontext"`, `"wasmtime"`, `"wasm3"`) or instance;
    None forgets it, and the next use picks again."""
    global _default
    _default = None if backend is None else _backend(backend)


def close() -> None:
    """Close every backend this module started. Modules and instances made on them stop working."""
    global _default
    for b in {id(b): b for b in [*_started.values(), *([_default] if _default else [])]}.values():
        b.close()
    _started.clear()
    _default = None


def _check(value: object, kind: str) -> int | float:
    """The argument for a parameter of this type, as WebAssembly takes it (integers wrap to their width)."""
    if kind in ("i32", "i64"):
        if not isinstance(value, int):
            raise TypeError(f"expected an int for an {kind} argument, got {type(value).__name__}")
        return normalize(value, kind)
    if kind in ("f32", "f64"):
        if not isinstance(value, (int, float)):
            raise TypeError(f"expected a number for an {kind} argument, got {type(value).__name__}")
        return normalize(value, kind)
    raise NotImplementedError(f"{kind} values are not supported")


class Function:
    """An exported function. Call it; `params` and `results` are its value types (`"i32"`, `"f64"`, ...)."""

    def __init__(self, instance: Instance, name: str, functype: FuncType) -> None:
        self._instance = instance
        self.name = name
        self._type = functype
        self.params = functype.params
        self.results = functype.results

    def __call__(self, *args: object) -> Any:
        if len(args) != len(self.params):
            raise TypeError(f"{self.name}() takes {len(self.params)} arguments ({len(args)} given)")
        checked = [_check(a, k) for a, k in zip(args, self.params, strict=True)]
        values = self._instance._backend.call(self._instance._handle, self.name, checked, self._type)
        if not self.results:
            return None
        return values[0] if len(values) == 1 else tuple(values)

    def __repr__(self) -> str:
        return f"<wasmhost.Function {self.name}({', '.join(self.params)}) -> ({', '.join(self.results)})>"


class Memory:
    """An exported linear memory. Bytes are copied in and out (nothing is shared with the runtime)."""

    def __init__(self, instance: Instance, name: str) -> None:
        self._instance = instance
        self.name = name

    def __len__(self) -> int:
        return self._instance._backend.memory_size(self._instance._handle, self.name)

    @property
    def byte_length(self) -> int:
        return len(self)

    def grow(self, pages: int) -> int:
        """`WebAssembly.Memory.grow`: add `pages` 64 KiB pages and return the previous size in pages.

        Not on every backend (wasm3 has no such call from Python): `NotImplementedError` there."""
        return self._instance._backend.memory_grow(self._instance._handle, self.name, int(pages))

    def read(self, offset: int, length: int) -> bytes:
        if offset < 0 or length < 0 or offset + length > len(self):
            raise IndexError("memory access out of bounds")
        return (
            self._instance._backend.memory_read(self._instance._handle, self.name, int(offset), int(length))
            if length
            else b""
        )

    def write(self, offset: int, data: bytes | bytearray | memoryview) -> None:
        self._instance._backend.memory_write(self._instance._handle, self.name, int(offset), bytes(data))

    def __getitem__(self, key: int | slice) -> bytes | int:
        if isinstance(key, slice):
            start, stop, step = key.indices(len(self))
            if step != 1:
                raise ValueError("memory slices have no step")
            return self.read(start, max(0, stop - start))
        n = len(self)
        i = key + n if key < 0 else key
        if not 0 <= i < n:
            raise IndexError("memory index out of range")
        return self.read(i, 1)[0]

    def __setitem__(self, key: int | slice, value: bytes | bytearray | memoryview | int) -> None:
        if isinstance(key, slice):
            start, stop, step = key.indices(len(self))
            if step != 1:
                raise ValueError("memory slices have no step")
            if not isinstance(value, (bytes, bytearray, memoryview)):
                raise TypeError("a slice takes bytes")
            if len(value) != max(0, stop - start):
                raise ValueError("a memory slice can't change size")
            self.write(start, value)
        else:
            if not isinstance(value, int):
                raise TypeError("an index takes an int")
            n = len(self)
            i = key + n if key < 0 else key
            if not 0 <= i < n:
                raise IndexError("memory index out of range")
            self.write(i, bytes([value & 0xFF]))

    def __repr__(self) -> str:
        return f"<wasmhost.Memory {self.name} {len(self) // PAGE_SIZE} pages>"


class Global:
    """An exported global; `value` reads it and, for a mutable one, writes it."""

    def __init__(self, instance: Instance, name: str, kind: str) -> None:
        self._instance = instance
        self.name = name
        self.type = kind

    @property
    def value(self) -> int | float:
        return self._instance._backend.global_get(self._instance._handle, self.name, self.type)

    @value.setter
    def value(self, new: int | float) -> None:
        self._instance._backend.global_set(self._instance._handle, self.name, self.type, _check(new, self.type))

    def __repr__(self) -> str:
        return f"<wasmhost.Global {self.name}: {self.type}>"


class Ref:
    """A value produced inside a batch, usable as an argument or offset of a later step (`ref * 8`, `ref + 4`)
    and readable as `.value` once the batch has run (`.done` says whether its step ran)."""

    def __init__(self, batch: Batch, index: int, kind: str) -> None:
        self._batch = batch
        self._index = index
        self.kind = kind  # "i32" | "i64" | "f32" | "f64" | "bytes" | "void" (a call without a result)
        self._expr: Expr = ("ref", index)
        self._value: Any = None
        self.done = False

    @property
    def value(self) -> Any:
        if not self.done:
            raise RuntimeError("this step has not run (the batch hasn't run, stopped before it, or failed)")
        return self._value

    def _arith(self, op: str, other: object, reflected: bool = False) -> Ref:
        if self.kind != "i32" or isinstance(other, bool) or not isinstance(other, int):
            raise TypeError("only an i32 result can be used in arithmetic, and only with an int")
        derived = Ref(self._batch, self._index, "i32")
        derived._expr = (op, other, self._expr) if reflected else (op, self._expr, other)
        return derived

    def __add__(self, other: int) -> Ref:
        return self._arith("+", other)

    def __radd__(self, other: int) -> Ref:
        return self._arith("+", other, True)

    def __sub__(self, other: int) -> Ref:
        return self._arith("-", other)

    def __rsub__(self, other: int) -> Ref:
        return self._arith("-", other, True)

    def __mul__(self, other: int) -> Ref:
        return self._arith("*", other)

    def __rmul__(self, other: int) -> Ref:
        return self._arith("*", other, True)


class Batch:
    """Several steps done in one go (on a JavaScript engine, in one trip: each trip has a fixed cost).

        b = inst.batch()
        ptr = b.call(inst.exports.alloc, len(data))      # a Ref: later steps can use it
        b.write(inst.exports.memory, ptr, data)
        b.stop_if_nonzero(b.call(inst.exports.run, ptr))   # leave the rest out on a non-zero status
        out = b.read(inst.exports.memory, ptr, 16)
        b.run()
        out.value                                          # bytes

    A step that fails (a trap, an out-of-bounds access) raises from `run()`, after the earlier steps' `Ref`s
    have their values.
    """

    def __init__(self, instance: Instance) -> None:
        self._instance = instance
        self._steps: list[Step] = []
        self._refs: list[Ref] = []
        self._ran = False

    def _operand(self, value: object, kind: str) -> Operand:
        if isinstance(value, Ref):
            if value.kind != kind:
                raise TypeError(f"a {value.kind} result can't be used as an {kind}")
            if value._batch is not self:
                raise ValueError("a Ref from another batch")
            return value._expr
        return _check(value, kind)

    def _new(self, kind: str) -> Ref:
        ref = Ref(self, len(self._refs), kind)
        self._refs.append(ref)
        return ref

    def call(self, function: Function, *args: object) -> Ref:
        """Call an exported function. The result is a `Ref`; for a function without one its value is None."""
        if function._instance is not self._instance:
            raise ValueError("a function of another instance")
        if len(args) != len(function.params):
            raise TypeError(f"{function.name}() takes {len(function.params)} arguments ({len(args)} given)")
        if len(function.results) > 1:
            raise NotImplementedError("multi-value results in a batch")
        operands = tuple(self._operand(a, k) for a, k in zip(args, function.params, strict=True))
        ref = self._new(function.results[0] if function.results else "void")
        self._steps.append(CallStep(ref._index, function.name, operands, function._type))
        return ref

    def write(self, memory: Memory, offset: int | Ref, data: bytes | bytearray | memoryview) -> None:
        self._steps.append(WriteStep(memory.name, self._operand(offset, "i32"), bytes(data)))

    def read(self, memory: Memory, offset: int | Ref, length: int | Ref) -> Ref:
        """Bytes out of the memory; the `Ref` holds them (as `bytes`) after the batch has run."""
        ref = self._new("bytes")
        self._steps.append(
            ReadStep(ref._index, memory.name, self._operand(offset, "i32"), self._operand(length, "i32"))
        )
        return ref

    def stop_if_zero(self, ref: Ref) -> None:
        """Leave out the remaining steps when this result is 0 (an allocation that failed, say)."""
        self._steps.append(StopStep(self._flag(ref), True))

    def stop_if_nonzero(self, ref: Ref) -> None:
        """Leave out the remaining steps when this result isn't 0 (an error status, say)."""
        self._steps.append(StopStep(self._flag(ref), False))

    def _flag(self, ref: Ref) -> Operand:
        if ref.kind not in ("i32", "f32", "f64"):
            raise TypeError("expected the number result of a call")
        return ref._expr

    def run(self) -> None:
        """Do the steps. Raises the error of a failed step; `Ref.done` tells which steps ran."""
        if self._ran:
            raise RuntimeError("this batch has already run")
        self._ran = True
        result = self._instance._backend.run_batch(self._instance._handle, self._steps)
        for ref in self._refs:
            if ref._index in result.values:
                ref._value = result.values[ref._index]
                ref.done = True
        if result.error is not None:
            raise result.error

    def __enter__(self) -> Batch:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if exc_type is None:
            self.run()


class Table:
    """An exported table: only its length is available so far."""

    def __init__(self, instance: Instance, name: str) -> None:
        self._instance = instance
        self.name = name

    def __len__(self) -> int:
        return self._instance._backend.table_length(self._instance._handle, self.name)


Export = Function | Memory | Global | Table


class _Exports:
    """`instance.exports`: attribute and item access, and iteration over the names (like the JS object)."""

    def __init__(self, items: dict[str, Export]) -> None:
        self._items = items

    def __getattr__(self, name: str) -> Any:  # like the JS object: any export, typed by what it is
        try:
            return self._items[name]
        except KeyError:
            raise AttributeError(name) from None

    def __getitem__(self, name: str) -> Export:
        return self._items[name]

    def __contains__(self, name: object) -> bool:
        return name in self._items

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:
        return f"<wasmhost exports {', '.join(self._items)}>"


class Module:
    """A compiled module. `Module.exports(m)` and `Module.imports(m)` describe it, with types."""

    def __init__(self, wasm: bytes | bytearray | memoryview, *, backend: Backend | str | None = None) -> None:
        data = bytes(wasm)
        try:
            self._info: ModuleInfo = parse(data)
        except ValueError as exc:
            raise CompileError(str(exc)) from None
        self._backend = _backend(backend)
        self._handle = self._backend.compile(data)

    @staticmethod
    def exports(module: Module) -> list[ExportDescriptor]:
        return list(module._info.exports)

    @staticmethod
    def imports(module: Module) -> list[ImportDescriptor]:
        return list(module._info.imports)


class Instance:
    """`WebAssembly.Instance`. Modules that import things can't be instantiated yet."""

    def __init__(self, module: Module, imports: Mapping[str, Mapping[str, object]] | None = None) -> None:
        if module._info.imports:
            if imports:
                raise NotImplementedError("imports (host functions, memories, tables, globals) are not supported yet")
            wanted = ", ".join(f"{i.module}.{i.name}" for i in module._info.imports)
            raise TypeError(f"the module imports {wanted}: an import object is needed")
        self._backend = module._backend
        self._handle = self._backend.instantiate(module._handle)
        items: dict[str, Export] = {}
        for e in module._info.exports:
            if e.kind == "function" and isinstance(e.type, FuncType):
                items[e.name] = Function(self, e.name, e.type)
            elif e.kind == "memory":
                items[e.name] = Memory(self, e.name)
            elif e.kind == "global" and isinstance(e.type, str):
                items[e.name] = Global(self, e.name, e.type)
            elif e.kind == "table":
                items[e.name] = Table(self, e.name)
        self.exports = _Exports(items)

    def batch(self) -> Batch:
        """Steps done together (on a JavaScript engine, in one trip), the later ones using the earlier results."""
        return Batch(self)


class Instantiated(NamedTuple):
    module: Module
    instance: Instance


def instantiate(
    wasm: bytes | bytearray | memoryview,
    imports: Mapping[str, Mapping[str, object]] | None = None,
    *,
    backend: Backend | str | None = None,
) -> Instantiated:
    """`WebAssembly.instantiate(bytes, imports)`: compile and instantiate in one step."""
    module = Module(wasm, backend=backend)
    return Instantiated(module, Instance(module, imports))


def validate(wasm: bytes | bytearray | memoryview, *, backend: Backend | str | None = None) -> bool:
    """`WebAssembly.validate`: does the backend accept these bytes as a module?"""
    return _backend(backend).validate(bytes(wasm))
