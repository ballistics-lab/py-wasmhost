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

import asyncio
import functools
import weakref
from collections.abc import Callable, Iterator, Mapping
from typing import Any, NamedTuple, cast, overload

from ._backend import (
    Backend,
    CallStep,
    Expr,
    HostFunction,
    HostObject,
    Operand,
    ReadStep,
    Step,
    StopStep,
    WriteStep,
    normalize,
)
from ._binary import (
    ExportDescriptor,
    FuncType,
    GlobalType,
    ImportDescriptor,
    MemoryType,
    ModuleInfo,
    TableType,
    parse,
)
from ._errors import CompileError, LinkError, Trap, WasmError
from ._registry import BACKENDS, default_backend
from ._trampoline import call_checked

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
    """Choose the default backend by name (`"node"`, `"bun"`, `"gi-jsc"`, `"jscontext"`, `"wasmtime"`, `"wasm3"`) or
    instance; None forgets it, and the next use picks again."""
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
    """A function of the module that can be called: an export (`instance.exports.add`) or an entry of a table
    (`table.get(0)`), and one function is one object, as in JavaScript (`table.get(0) is instance.exports.add` when
    the table holds it).

    `signature` is its `FuncType`, or None when it is not known (the engine does not say what a function it found in
    a table is, and a JavaScript engine never does). Reading it asks nothing of the engine. `type()` gives the type
    and, if the signature is not known yet, looks for it (a `ValueError` if it can't be found); a call does the same,
    once. A signature can be set by hand: `f.signature = FuncType((i32, i32), (i32,))`, which works on every
    backend; a function whose type is known for certain (from the module, from the engine) can only be given the
    same one (`ValueError` otherwise). The signature does not count in `==` and `hash`: two `Function`s are equal
    when they are the same function of the engine.

    A wrong signature given by hand is refused, not obeyed: a call goes through a tiny module that the engine checks
    the type against (`call_indirect`), so a function of another type is a `TypeError` and does not run (wasmtime
    does the same by itself). A function whose signature is known is called directly."""

    def __init__(
        self,
        backend: Backend,
        handle: Any,
        *,
        instance: Instance | None = None,
        name: str | None = None,
        signature: FuncType | None = None,
    ) -> None:
        self._backend = backend
        self._h = handle
        self._key = backend.function_key(handle)
        self._instance = instance  # whose export it is, for a Batch
        self.name = name  # its name as an export
        self._signature: FuncType | None = signature
        self._known = signature is not None  # known for certain: from the module or the engine, not set by hand

    def _adopt(self, instance: Instance | None, name: str | None, signature: FuncType | None) -> None:
        """What another way to the same function knows (an export that was a table entry before)."""
        if self._instance is None:
            self._instance = instance
        if self.name is None:
            self.name = name
        if signature is not None and not self._known:
            self._signature, self._known = signature, True

    @property
    def signature(self) -> FuncType | None:
        return self._signature

    @signature.setter
    def signature(self, value: FuncType | None) -> None:
        if value is None:
            if self._known:
                raise ValueError(f"the type of this function is known, {self._signature}: it can't be taken away")
            self._signature = None
            return
        given: Any = value  # the annotation says FuncType; a caller may pass anything
        if not isinstance(given, FuncType):
            raise TypeError("a signature is a FuncType: FuncType((i32, i32), (i32,))")
        for kind in (*value.parameters, *value.results):
            if kind not in ("i32", "i64", "f32", "f64"):
                raise ValueError(f"a function of {kind!r}: only i32, i64, f32 and f64 can be called from here")
        if self._known and value != self._signature:
            raise ValueError(f"this function is known to be {self._signature}, not {value}")
        self._signature = value

    def type(self) -> FuncType:
        """The type of the function (`WebAssembly.Function.type` of the type reflection proposal). Looks for it when
        it is not known yet; a `ValueError` when it can't be found, and then `signature` can be set by hand."""
        if self._signature is None:
            found = self._backend.function_type(self._h)
            if found is None:
                raise ValueError(
                    f"no signature found for {self.name or 'this function'}: set `signature` "
                    "(a FuncType: the engine does not tell the type of a function of a table)"
                )
            self._signature, self._known = found, True
        return self._signature

    @property
    def parameters(self) -> tuple[str, ...] | None:
        return None if self._signature is None else self._signature.parameters

    @property
    def params(self) -> tuple[str, ...] | None:  # what `parameters` was called
        return self.parameters

    @property
    def results(self) -> tuple[str, ...] | None:
        return None if self._signature is None else self._signature.results

    @property
    def _type(self) -> FuncType:  # for a Batch, which only takes exports, whose type is known
        return self.type()

    def __call__(self, *args: object) -> Any:
        ftype = self.type()
        if len(args) != len(ftype.parameters):
            raise TypeError(
                f"{self.name or 'the function'}() takes {len(ftype.parameters)} arguments ({len(args)} given)"
            )
        checked = [_check(a, k) for a, k in zip(args, ftype.parameters, strict=True)]
        if not self._known and self._backend.supports("table.funcs") and not self._backend.supports("table.signatures"):
            # a signature given by hand, and an engine that does not check it by itself: let the engine check it
            values = call_checked(self._backend, self._h, checked, ftype, self.name or "this function")
        else:
            values = self._backend.call_ref(self._h, checked, ftype)
        if not ftype.results:
            return None
        return values[0] if len(values) == 1 else tuple(values)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Function) and other._backend is self._backend and other._key == self._key

    def __hash__(self) -> int:
        return hash((id(self._backend), self._key))

    def __repr__(self) -> str:
        label = self.name or "(a table entry)"
        if self._signature is None:
            return f"<wasmhost.Function {label}: signature not known>"
        parameters, results = self._signature.parameters, self._signature.results
        return f"<wasmhost.Function {label}({', '.join(parameters)}) -> ({', '.join(results)})>"


def _function(
    backend: Backend,
    handle: Any,
    *,
    instance: Instance | None = None,
    name: str | None = None,
    signature: FuncType | None = None,
) -> Function:
    """The `Function` of a function of the engine, the same one every time: found by the key of the function, so
    what wasmtime-py makes anew each time (a `Func` for each `table.get`) is still the same object here. The cache
    holds them weakly: it keeps nothing alive."""
    cache = cast(
        "weakref.WeakValueDictionary[Any, Function]",
        backend.__dict__.setdefault("_functions", weakref.WeakValueDictionary()),
    )
    key = backend.function_key(handle)
    found = cache.get(key)
    if found is None:
        found = Function(backend, handle, instance=instance, name=name, signature=signature)
        if signature is None:  # an engine that knows the type (wasmtime) says so at once
            native = backend.function_type(handle) or backend.__dict__.get("_signatures", {}).get(key)
            if native is not None:
                found._signature, found._known = native, True
        cache[key] = found
    else:
        found._adopt(instance, name, signature)
    return found


def _limits(initial: object, maximum: object, what: str) -> None:
    if isinstance(initial, bool) or not isinstance(initial, int) or initial < 0:
        raise TypeError(f"the initial size of a {what} is a non-negative int")
    if maximum is not None and (isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < initial):
        raise ValueError(f"the maximum size of a {what} is an int, not less than the initial size")


def _sizes(
    spec: int | Mapping[str, Any] | MemoryType | TableType, maximum: int | None, what: str
) -> tuple[Any, int | None]:
    """The initial and the maximum size of a memory or a table, from `initial, maximum`, or from a descriptor: a
    mapping `{"initial": 1, "maximum": 3}` as in the JavaScript API, or a type (`MemoryType`, `TableType`) as `type()`
    gives it. A `shared` memory is refused: there are no threads here."""
    if isinstance(spec, Mapping):
        descriptor = spec
        if maximum is not None:
            raise TypeError(f"a descriptor of a {what} has its own maximum")
        initial = descriptor.get("initial", descriptor.get("minimum"))
        if initial is None:
            raise TypeError(f"the descriptor of a {what} needs an `initial` size")
        if descriptor.get("shared"):
            raise NotImplementedError("a shared memory: there are no threads")
        return initial, descriptor.get("maximum")
    if isinstance(spec, (MemoryType, TableType)):
        if maximum is not None:
            raise TypeError(f"a type of a {what} has its own maximum")
        if getattr(spec, "shared", False):
            raise NotImplementedError("a shared memory: there are no threads")
        return spec.minimum, spec.maximum
    return spec, maximum


class Memory:
    """`WebAssembly.Memory`: an exported linear memory, or one made on its own with `Memory(initial, maximum)` (in
    pages of 64 KiB) or, as in JavaScript, `Memory({"initial": 1, "maximum": 3})` (a `MemoryType` will do too), which
    can be given to instances as an import (and shared by them). Bytes are copied in and out (nothing is shared with
    the runtime)."""

    def __init__(
        self,
        initial: int | Mapping[str, Any] | MemoryType,
        maximum: int | None = None,
        *,
        backend: Backend | str | None = None,
    ) -> None:
        size, limit = _sizes(initial, maximum, "memory")
        _limits(size, limit, "memory")
        target = _backend(backend)
        if not target.supports("import.memory"):
            raise NotImplementedError(f"the {target.name} backend can't make a memory on its own")
        self._backend = target
        self._handle = target.new_memory(size, limit)
        self._maximum: int | None = limit
        self.name: str | None = None

    @classmethod
    def _wrap(cls, backend: Backend, handle: Any, name: str | None = None, type_: MemoryType | None = None) -> Memory:
        """A memory that already exists: an export."""
        self = object.__new__(cls)
        self._backend, self._handle, self.name = backend, handle, name
        self._maximum = None if type_ is None else type_.maximum
        return self

    def type(self) -> MemoryType:
        """`WebAssembly.Memory.type` of the type reflection proposal: `minimum` (the size now, in pages, as the
        specification has it; JavaScriptCore keeps the initial size here), `maximum` (None if there is none) and
        `shared` (never)."""
        return MemoryType(len(self) // PAGE_SIZE, self._maximum, False)

    def __len__(self) -> int:
        return self._backend.memory_size(self._handle)

    @property
    def byte_length(self) -> int:
        return len(self)

    def grow(self, pages: int) -> int:
        """`WebAssembly.Memory.grow`: add `pages` 64 KiB pages and return the previous size in pages.

        Not on every backend (wasm3 has no such call from Python): `NotImplementedError` there."""
        return self._backend.memory_grow(self._handle, int(pages))

    def read(self, offset: int, length: int) -> bytes:
        if offset < 0 or length < 0 or offset + length > len(self):
            raise IndexError("memory access out of bounds")
        return self._backend.memory_read(self._handle, int(offset), int(length)) if length else b""

    def write(self, offset: int, data: bytes | bytearray | memoryview) -> None:
        self._backend.memory_write(self._handle, int(offset), bytes(data))

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
    """`WebAssembly.Global`: `Global("i32", 7, mutable=True)` makes one on its own, or, as in JavaScript,
    `Global({"value": "i32", "mutable": True}, 7)` (a `GlobalType` will do too); it can be given to an instance as an
    import (and shared by several). An exported one comes from `instance.exports`. `value` reads it and, for a
    mutable one, writes it (an immutable one raises TypeError, as in JavaScript); `type()` is its `GlobalType`."""

    def __init__(
        self,
        kind: str | Mapping[str, Any] | GlobalType,
        value: int | float = 0,
        *,
        mutable: bool = False,
        backend: Backend | str | None = None,
    ) -> None:
        valtype: Any = kind
        if isinstance(kind, Mapping):
            valtype, mutable = kind.get("value"), bool(kind.get("mutable", mutable))
        elif isinstance(kind, GlobalType):
            valtype, mutable = kind.value, kind.mutable
        if valtype not in ("i32", "i64", "f32", "f64"):
            raise ValueError(f"a global of type {valtype!r}: expected i32, i64, f32 or f64")
        target = _backend(backend)
        if not target.supports("import.global"):
            raise NotImplementedError(f"the {target.name} backend can't make a global on its own")
        self._backend = target
        self._handle = target.new_global(valtype, _check(value, valtype), mutable)
        self.name: str | None = None
        self._kind: str = valtype
        self.mutable = mutable

    @classmethod
    def _wrap(cls, backend: Backend, handle: Any, type_: GlobalType, name: str | None = None) -> Global:
        """A global that already exists: an export."""
        self = object.__new__(cls)
        self._backend, self._handle, self.name, self._kind, self.mutable = (
            backend,
            handle,
            name,
            type_.value,
            type_.mutable,
        )
        return self

    def type(self) -> GlobalType:
        """`WebAssembly.Global.type` of the type reflection proposal: the value type and whether it can be written."""
        return GlobalType(self._kind, bool(self.mutable))

    @property
    def value(self) -> int | float:
        return self._backend.global_get(self._handle, self._kind)

    @value.setter
    def value(self, new: int | float) -> None:
        self._backend.global_set(self._handle, self._kind, _check(new, self._kind))

    def __repr__(self) -> str:
        return f"<wasmhost.Global {self.name or ''}: {self._kind}{' mutable' if self.mutable else ''}>"


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

    def _check_memory(self, memory: Memory) -> None:
        if memory._backend is not self._instance._backend:
            raise ValueError("a memory of another backend")

    def _new(self, kind: str) -> Ref:
        ref = Ref(self, len(self._refs), kind)
        self._refs.append(ref)
        return ref

    def call(self, function: Function, *args: object) -> Any:
        """Call an exported function. The result is a `Ref`; for a function without one its value is None; for one
        with several results (multi-value) a tuple of `Ref`s, one for each: `low, high = batch.call(split, x)`."""
        if function._instance is not self._instance:
            raise ValueError("a function of another instance, or one that this instance does not export")
        ftype = function.type()  # an export's type is known
        name = function.name or ""
        if len(args) != len(ftype.parameters):
            raise TypeError(f"{name}() takes {len(ftype.parameters)} arguments ({len(args)} given)")
        operands = tuple(self._operand(a, k) for a, k in zip(args, ftype.parameters, strict=True))
        refs = [self._new(k) for k in ftype.results] or [self._new("void")]
        self._steps.append(CallStep(tuple(r._index for r in refs), function._h, operands, ftype))
        return tuple(refs) if len(refs) > 1 else refs[0]

    def write(self, memory: Memory, offset: int | Ref, data: bytes | bytearray | memoryview) -> None:
        self._check_memory(memory)
        self._steps.append(WriteStep(memory._handle, self._operand(offset, "i32"), bytes(data)))

    def read(self, memory: Memory, offset: int | Ref, length: int | Ref) -> Ref:
        """Bytes out of the memory; the `Ref` holds them (as `bytes`) after the batch has run."""
        self._check_memory(memory)
        ref = self._new("bytes")
        self._steps.append(
            ReadStep(ref._index, memory._handle, self._operand(offset, "i32"), self._operand(length, "i32"))
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
    """`WebAssembly.Table` of functions: an exported one, or one made on its own, `Table("funcref", initial, maximum)`
    or, as in JavaScript, `Table({"element": "anyfunc", "initial": 2, "maximum": 4})` (a `TableType` will do too),
    which can be given to instances as an import (and shared by them). `get(i)` is a `Function` (or None for an empty
    entry), the same one every time, as the function it is: the very object the module exports, if it does; `set(i, f)`
    takes a `Function` or None. Only `"funcref"` tables are supported."""

    def __init__(
        self,
        element: str | Mapping[str, Any] | TableType = "funcref",
        initial: int = 0,
        maximum: int | None = None,
        *,
        backend: Backend | str | None = None,
    ) -> None:
        if isinstance(element, Mapping):
            kind = element.get("element", "funcref")
        elif isinstance(element, TableType):
            kind = element.element
        else:
            kind = element
        size, limit = _sizes(element, maximum, "table") if not isinstance(element, str) else (initial, maximum)
        if kind == "anyfunc":  # what the JavaScript API called it first
            kind = "funcref"
        if kind != "funcref":
            raise NotImplementedError(f"a table of {kind!r}: only funcref tables are supported")
        _limits(size, limit, "table")
        target = _backend(backend)
        if not target.supports("import.table"):
            raise NotImplementedError(f"the {target.name} backend can't make a table on its own")
        self._backend = target
        self._handle = target.new_table(size, limit)
        self._maximum: int | None = limit
        self.name: str | None = None

    @classmethod
    def _wrap(cls, backend: Backend, handle: Any, name: str | None = None, type_: TableType | None = None) -> Table:
        """A table that already exists: an export."""
        self = object.__new__(cls)
        self._backend, self._handle, self.name = backend, handle, name
        self._maximum = None if type_ is None else type_.maximum
        return self

    def type(self) -> TableType:
        """`WebAssembly.Table.type` of the type reflection proposal: the `element` type, `minimum` (the length now)
        and `maximum` (None if there is none)."""
        return TableType("funcref", len(self), self._maximum)

    @property
    def length(self) -> int:
        """`WebAssembly.Table.length`: the same as `len(table)`."""
        return len(self)

    def _need(self) -> None:
        if not self._backend.supports("table.funcs"):
            raise NotImplementedError(f"the {self._backend.name} backend has no table access, only the length")

    def __len__(self) -> int:
        return self._backend.table_length(self._handle)

    def grow(self, delta: int) -> int:
        """Add `delta` empty entries; the length before."""
        self._need()
        return self._backend.table_grow(self._handle, int(delta))

    def get(self, index: int) -> Function | None:
        self._need()
        handle = self._backend.table_get(self._handle, int(index))
        return None if handle is None else _function(self._backend, handle)

    def set(self, index: int, value: Function | None) -> None:
        self._need()
        given: Any = value
        if given is not None and not isinstance(given, Function):
            raise TypeError("a table entry is a Function or None")
        if value is not None and value._backend is not self._backend:
            raise ValueError("a function of another backend")
        self._backend.table_set(self._handle, int(index), None if value is None else value._h)

    def __repr__(self) -> str:
        return f"<wasmhost.Table {self.name or ''} length {len(self)}>"


Export = Function | Memory | Global | Table


class _LazyFunction:
    """An exported function that is not made yet: asking the engine for it costs a trip, and not every export is
    ever used."""

    def __init__(self, make: Callable[[], Function]) -> None:
        self.make = make


class _Exports:
    """`instance.exports`: attribute and item access, and iteration over the names (like the JS object)."""

    def __init__(self, items: dict[str, Export | _LazyFunction]) -> None:
        self._items = items

    def _get(self, name: str) -> Export:
        item = self._items[name]
        if isinstance(item, _LazyFunction):
            item = self._items[name] = item.make()
        return item

    def __getattr__(self, name: str) -> Any:  # like the JS object: any export, typed by what it is
        try:
            return self._get(name)
        except KeyError:
            raise AttributeError(name) from None

    def __getitem__(self, name: str) -> Export:
        return self._get(name)

    def __contains__(self, name: object) -> bool:
        return name in self._items

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:
        return f"<wasmhost exports {', '.join(self._items)}>"


class Module:
    """A compiled module. `Module.exports(m)` and `Module.imports(m)` describe it, with types, and
    `Module.customSections(m, name)` gives its custom sections (the `name` section, producers, your own data)."""

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

    @staticmethod
    def customSections(module: Module, name: str) -> list[bytes]:  # noqa: N802 -- the JavaScript API's name
        """The contents of every custom section called `name` (a list of `bytes`, empty when there is none)."""
        return [data for section, data in module._info.custom if section == name]


def _resolve_imports(
    module: Module, imports: Mapping[str, Mapping[str, object]] | None
) -> list[HostFunction | HostObject]:
    """The module's imports, each with what the import object has for it, as the JS API checks: a callable for a
    function, a Global (or, for an immutable one, a plain number) for a global, a Memory, a Table."""
    found: list[HostFunction | HostObject] = []
    for d in module._info.imports:
        if imports is None:
            wanted = ", ".join(f"{i.module}.{i.name}" for i in module._info.imports)
            raise TypeError(f"the module imports {wanted}: an import object is needed")
        namespace = imports.get(d.module)
        if namespace is None:
            raise TypeError(f"the import object has no {d.module!r}")
        value = namespace.get(d.name)
        where = f"{d.module}.{d.name}"
        if d.kind == "function" and isinstance(d.type, FuncType):
            if not callable(value):
                raise LinkError(f"import {where} is not a function")
            found.append(HostFunction(d.module, d.name, d.type, value))
        elif d.kind == "global" and isinstance(d.type, GlobalType):
            if isinstance(value, (int, float)) and not isinstance(value, bool) and not d.type.mutable:
                value = Global(d.type.value, value, backend=module._backend)  # a number is an immutable global
            if not isinstance(value, Global):
                raise LinkError(f"import {where} is not a Global" + (" (it is mutable)" if d.type.mutable else ""))
            if value._backend is not module._backend:
                raise LinkError(f"import {where} was made on another backend ({value._backend.name})")
            mine = value.type()
            if mine.value != d.type.value:
                raise LinkError(f"import {where} is an {mine.value}, the module wants an {d.type.value}")
            if mine.mutable != d.type.mutable:
                raise LinkError(
                    f"import {where} is {'mutable' if mine.mutable else 'immutable'}, "
                    f"the module wants {'a mutable' if d.type.mutable else 'an immutable'} one"
                )
            found.append(HostObject(d.module, d.name, "global", value._handle))
        elif d.kind in ("memory", "table"):
            cls = Memory if d.kind == "memory" else Table
            if not isinstance(value, cls):
                raise LinkError(f"import {where} is not a {cls.__name__}")
            if value._backend is not module._backend:
                raise LinkError(f"import {where} was made on another backend ({value._backend.name})")
            found.append(HostObject(d.module, d.name, d.kind, value._handle))
        else:
            raise NotImplementedError(f"importing a {d.kind} ({where}) is not supported")
    return found


class Instance:
    """`WebAssembly.Instance`. `imports` is the import object, `{"env": {"name": value}}`: a callable for a function
    (it gets the arguments as ints and floats and returns what the signature says, None, a value or a tuple; an
    exception it raises comes out of the call into the module) and a `Global` for a global (a plain number will do
    for an immutable one), a `Memory` for a memory and a `Table` for a table.

    `isolated=True` (not in the JavaScript API) gives the instance a store of its own, which goes away with it: its
    memory is freed when the instance is dropped, but it can't share a Global with any other instance. Where it is
    not wanted or not available (`backend.supports("isolated")`) it is a NotImplementedError. Without it, all the
    instances of a backend live in one store, as in JavaScript, and their memory is freed with the backend."""

    def __init__(
        self,
        module: Module,
        imports: Mapping[str, Mapping[str, object]] | None = None,
        *,
        isolated: bool = False,
    ) -> None:
        hosts = _resolve_imports(module, imports)
        backend = module._backend
        if any(isinstance(h, HostFunction) for h in hosts) and not backend.supports("imports"):
            raise NotImplementedError(f"the {backend.name} backend can't take imports (host functions)")
        for h in hosts:
            if isinstance(h, HostObject) and not backend.supports(f"import.{h.kind}"):
                raise NotImplementedError(f"the {backend.name} backend can't import a {h.kind}")
        if isolated and not backend.supports("isolated"):
            raise NotImplementedError(f"the {backend.name} backend has one store for everything: no isolated instances")
        self._backend = backend
        self._handle = backend.instantiate(module._handle, hosts, isolated=isolated)
        items: dict[str, Export | _LazyFunction] = {}
        for e in module._info.exports:
            if e.kind == "function" and isinstance(e.type, FuncType):
                items[e.name] = _LazyFunction(functools.partial(self._export_function, e.name, e.type))
            elif e.kind == "memory" and isinstance(e.type, MemoryType):
                handle = self._backend.export_memory(self._handle, e.name)
                items[e.name] = Memory._wrap(self._backend, handle, e.name, e.type)
            elif e.kind == "global" and isinstance(e.type, GlobalType):
                handle = self._backend.export_global(self._handle, e.name, e.type.value)
                items[e.name] = Global._wrap(self._backend, handle, e.type, e.name)
            elif e.kind == "table" and isinstance(e.type, TableType):
                handle = self._backend.export_table(self._handle, e.name)
                items[e.name] = Table._wrap(self._backend, handle, e.name, e.type)
        self.exports = _Exports(items)
        self._learn_table_entries(module, imports, items)

    def _learn_table_entries(
        self, module: Module, imports: Mapping[str, Mapping[str, object]] | None, items: dict[str, Any]
    ) -> None:
        """Remember which function the `elem` segments put in each slot, by the identity of the function, so that a
        `Table.get` of one on an engine that does not say what a function is still knows its signature. Right after
        the instance is made, and not for a module with a start function (it may have changed the tables before we
        looked); a slot rewritten later holds another function, which is known by itself or not at all."""
        info = module._info
        backend = self._backend
        if not info.elems or info.has_start or not backend.supports("table.funcs"):
            return
        tables: dict[int, Table] = {}
        own = [d for d in info.imports if d.kind == "table"]
        for i, d in enumerate(own):
            found = (imports or {}).get(d.module, {}).get(d.name)
            if isinstance(found, Table):
                tables[i] = found
        for name, index in info.table_exports:
            if isinstance(items.get(name), Table):
                tables[index] = items[name]
        known: dict[Any, FuncType] = backend.__dict__.setdefault("_signatures", {})
        globals_ = [d for d in info.imports if d.kind == "global"]
        for table_index, slot, ftype, glob in info.elems[:1024]:
            table = tables.get(table_index)
            if glob is not None:  # the offset is an imported global: its value now, a number or a Global
                if glob >= len(globals_):
                    continue
                given = (imports or {}).get(globals_[glob].module, {}).get(globals_[glob].name)
                base = given.value if isinstance(given, Global) else given
                if isinstance(base, bool) or not isinstance(base, int):
                    continue
                slot += base
            handle = None if table is None else backend.table_get(table._handle, slot)
            if handle is not None:
                known.setdefault(backend.function_key(handle), ftype)

    def _export_function(self, name: str, ftype: FuncType) -> Function:
        handle = self._backend.export_function(self._handle, name)
        return _function(self._backend, handle, instance=self, name=name, signature=ftype)

    def batch(self) -> Batch:
        """Steps done together (on a JavaScript engine, in one trip), the later ones using the earlier results."""
        return Batch(self)


class Instantiated(NamedTuple):
    module: Module
    instance: Instance


@overload
def instantiate_sync(
    wasm: Module, imports: Mapping[str, Mapping[str, object]] | None = None, *, backend: Backend | str | None = None
) -> Instance: ...
@overload
def instantiate_sync(
    wasm: bytes | bytearray | memoryview,
    imports: Mapping[str, Mapping[str, object]] | None = None,
    *,
    backend: Backend | str | None = None,
) -> Instantiated: ...
def instantiate_sync(
    wasm: bytes | bytearray | memoryview | Module,
    imports: Mapping[str, Mapping[str, object]] | None = None,
    *,
    backend: Backend | str | None = None,
) -> Instantiated | Instance:
    """`WebAssembly.instantiate` without the promise: from bytes, a `Module` and an `Instance` (an `Instantiated`);
    from a `Module`, just the `Instance`."""
    if isinstance(wasm, Module):
        return Instance(wasm, imports)
    module = Module(wasm, backend=backend)
    return Instantiated(module, Instance(module, imports))


async def _offload(backend: Backend | str | None, work: Callable[[Backend], Any], threaded: bool) -> Any:
    """Run `work(backend)`. By default in place, with a turn of the event loop before and after so that other tasks
    get to run, which needs no threads at all (some Pythons have none that work). With `threaded=True`, on a backend
    that says "threads" (wasmtime, Node, Bun: not tied to the thread that made them), in a worker thread, so that a
    long compile does not hold the loop up; the backend's own lock lets one call in at a time, so tasks never race. A
    backend that is tied to its thread (the JavaScriptCore ones, wasm3) always runs in place."""
    chosen = _backend(backend)
    if threaded and chosen.supports("threads"):
        return await asyncio.to_thread(work, chosen)
    await asyncio.sleep(0)
    try:
        return work(chosen)
    finally:
        await asyncio.sleep(0)


async def compile(  # noqa: A001
    wasm: bytes | bytearray | memoryview, *, backend: Backend | str | None = None, threaded: bool = False
) -> Module:
    """`WebAssembly.compile(bytes)`: a `Module`, awaited. `threaded=True` does the work in a worker thread where the
    backend allows it (see `instantiate`)."""
    return cast("Module", await _offload(backend, lambda chosen: Module(wasm, backend=chosen), threaded))


@overload
async def instantiate(
    wasm: Module,
    imports: Mapping[str, Mapping[str, object]] | None = None,
    *,
    backend: Backend | str | None = None,
    threaded: bool = False,
) -> Instance: ...
@overload
async def instantiate(
    wasm: bytes | bytearray | memoryview,
    imports: Mapping[str, Mapping[str, object]] | None = None,
    *,
    backend: Backend | str | None = None,
    threaded: bool = False,
) -> Instantiated: ...
async def instantiate(
    wasm: bytes | bytearray | memoryview | Module,
    imports: Mapping[str, Mapping[str, object]] | None = None,
    *,
    backend: Backend | str | None = None,
    threaded: bool = False,
) -> Instantiated | Instance:
    """`WebAssembly.instantiate(bytes | module, imports)`, awaited: from bytes an `Instantiated` (`module`,
    `instance`), from a `Module` just the `Instance`.

    By default the work is done in place, between two turns of the event loop: no threads are used (so it works where
    Python's threads do not). `threaded=True` does it in a worker thread on a backend that allows it (wasmtime, Node,
    Bun), which keeps the loop free during a long compile; a host function in `imports` is then called in that thread,
    so it should not touch what the event loop owns."""
    return cast(
        "Instantiated | Instance",
        await _offload(backend, lambda chosen: instantiate_sync(wasm, imports, backend=chosen), threaded),
    )


def validate(wasm: bytes | bytearray | memoryview, *, backend: Backend | str | None = None) -> bool:
    """`WebAssembly.validate`: does the backend accept these bytes as a module?"""
    return _backend(backend).validate(bytes(wasm))
