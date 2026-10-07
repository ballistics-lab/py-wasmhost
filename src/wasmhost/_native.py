"""Native runtimes as backends: the wasm3 interpreter (pywasm3) and wasmtime. No JavaScript, direct calls.

Both are optional: constructing a backend imports its package, so "available" means "installed".

    Wasm3Backend        pywasm3, CPython 3.11+; its PyPI release is years behind the API used here, so
                     install it from git: `uv add "pywasm3 @ git+https://github.com/wasm3/pywasm3"`
    WasmtimeBackend     the `wasmtime` package (`pip install wasmtime`), a JIT
"""

from __future__ import annotations

import contextlib
import importlib
import threading
import time
import weakref
from collections.abc import Callable, Generator, Sequence
from typing import Any, NamedTuple, cast

from ._backend import (
    Backend,
    BatchResult,
    HostFunction,
    HostObject,
    Step,
    check_results,
)
from ._binary import FuncType, f32, f64, i32, i64
from ._errors import CompileError, LinkError, OutOfFuel, Timeout, Trap

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
    def __init__(self, runtime: Any, module: Any, timeout: float | None = None, fuel: int | None = None) -> None:
        self.runtime = runtime
        self.module = module
        self.functions: dict[str, Any] = {}
        self.memories: dict[str, Any] = {}
        self.timeout = timeout  # seconds for a call, or None
        self.fuel = fuel  # gas for a call, or None
        self.depth = 0  # how many calls are in progress (a host function may call the module again)
        self.deadline: float | None = None  # of the outermost call in progress; a nested one shares it
        self.remaining: int | None = None  # of the fuel, in the slices of a timed call; a nested one shares it
        self.stopped = (
            False  # a slicing call was stopped: the paused call can't be cancelled, so the instance ends here
        )

    def function(self, name: str) -> Any:
        if (f := self.functions.get(name)) is None:
            f = self.functions[name] = self.runtime.find_function(name)
        return f

    def memory(self, name: str) -> Any:
        if (m := self.memories.get(name)) is None:
            m = self.memories[name] = self.module.get_memory(name)
        return m


class _Wasm3Function:
    """An exported function of a wasm3 instance: the instance and the name, which is all pywasm3 lets us hold."""

    def __init__(self, instance: _Wasm3Instance, name: str) -> None:
        self.instance = instance
        self.name = name


class Wasm3Backend(Backend):
    """pywasm3: the wasm3 interpreter as a CPython extension."""

    name = "wasm3"
    # It has no Memory.grow from Python (a module's own memory.grow works, and len(memory) follows) and no tables.
    # "timeout": a call is cut into slices of gas, and the clock is looked at between them (a thread can't stop a call,
    # which holds the GIL). The paused call can't be cancelled, so an instance that timed out can't run again.
    # "fuel": pywasm3's gas, which ends a call with a trap that leaves the instance fine (not with a timeout too).
    features = frozenset({"imports", "isolated", "memory.view", "timeout", "fuel"})
    # wasm3's own value stack, separate from the module's shadow stack in linear memory.
    STACK_BYTES = 256 * 1024
    # Gas between two looks at the clock: about 5 ms of a tight loop, which cost about 8% in a measurement (a slice of
    # 5000 is 1.4 ms and finer, one of 100000 is 27 ms and coarser).
    SLICE_GAS = 20_000

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

    def instantiate(
        self,
        module: bytes,
        imports: Sequence[HostFunction | HostObject] = (),
        *,
        isolated: bool = False,
        timeout: float | None = None,
        fuel: int | None = None,
    ) -> _Wasm3Instance:
        # `isolated` is what a wasm3 instance is anyway: it has its own runtime and shares nothing.
        runtime = self._env.new_runtime(self._stack)
        if timeout is not None:
            # Both before the first `find_function`: the pause points and the gas count are compiled into the code
            # as it is found, and a function found earlier would run on, uncounted, for ever.
            runtime.suspendable = True
            runtime.gas_limit = self.SLICE_GAS
        elif fuel is not None:
            runtime.gas_limit = fuel  # counted, and a trap when spent (not paused: it isn't suspendable)
        parsed = self._env.parse_module(module)
        runtime.load(parsed)
        for host in imports:  # linked after loading, before the first call
            if isinstance(host, HostObject):
                raise NotImplementedError(f"pywasm3 can't import a {host.kind}: only functions")
            parsed.link_function(host.module, host.name, _wasm3_signature(host.ftype), _wasm3_callable(host))
        return _Wasm3Instance(runtime, parsed, timeout, fuel)

    def new_global(self, kind: str, value: int | float, mutable: bool) -> Any:
        raise NotImplementedError("pywasm3 can't make a global outside a module")

    @contextlib.contextmanager
    def _scope(self, instance: _Wasm3Instance) -> Generator[None]:
        """Around anything that runs wasm in a limited instance: the outermost call starts the clock and the fuel, and a
        call made from a host function while it runs is under the same ones."""
        if instance.timeout is None and instance.fuel is None:
            yield
            return
        outermost = instance.depth == 0
        instance.depth += 1
        if outermost:
            if instance.timeout is not None:
                instance.deadline = time.monotonic() + instance.timeout
            instance.remaining = instance.fuel
        try:
            yield
        finally:
            instance.depth -= 1
            if outermost:
                instance.deadline = instance.remaining = None

    def _slice(self, instance: _Wasm3Instance) -> int:
        """The gas of the next slice: the usual, or what is left of the fuel if that is less."""
        return self.SLICE_GAS if instance.remaining is None else min(self.SLICE_GAS, instance.remaining)

    def _run(self, instance: _Wasm3Instance, function: Any, args: Sequence[int | float]) -> Any:
        """The call. With `fuel` alone the gas is armed for it and a trap ends it. With a `timeout` it is cut into
        slices of gas: it pauses when a slice is spent (the clock, and the fuel, are looked at there), and goes on from
        where it stopped until it ends, the time is up or the fuel is."""
        if instance.timeout is None and instance.fuel is None:
            return function(*args)
        if instance.stopped:
            raise Trap("this wasm3 instance was stopped by its timeout and can't run again: make a new one")
        runtime = instance.runtime
        if instance.timeout is None:
            if instance.depth == 1:  # the outermost call arms it; one from a host function shares what is left
                runtime.gas_limit = instance.fuel
            return function(*args)
        size = self._slice(instance)
        runtime.gas_limit = size
        result = function(*args)
        while runtime.suspended:  # a call without results gives None whether it ended or paused: `suspended` says
            if instance.remaining is not None:
                instance.remaining -= size
                if instance.remaining <= 0:
                    instance.stopped = True  # the paused call stays paused: pywasm3 has no way to cancel it
                    raise OutOfFuel(f"the call used more than {instance.fuel} units of fuel")
            if instance.deadline is not None and time.monotonic() >= instance.deadline:
                instance.stopped = True
                raise Timeout(f"the call ran longer than {instance.timeout:g} s")
            size = self._slice(instance)
            runtime.gas_limit = size
            result = runtime.resume()
        return result

    def call(
        self, instance: _Wasm3Instance, name: str, args: Sequence[int | float], ftype: FuncType
    ) -> list[int | float]:
        try:
            with self._scope(instance):
                result = self._run(instance, instance.function(name), args)
        except RuntimeError as exc:
            if isinstance(exc, (Trap, Timeout, OutOfFuel)):
                raise
            if "out of gas" in str(exc) and instance.fuel is not None:  # the count of a fuel-only instance ran out
                raise OutOfFuel(f"the call used more than {instance.fuel} units of fuel") from None
            if "[trap]" in str(exc):
                raise Trap(str(exc)) from None
            raise
        if result is None:
            return []
        return list(cast("Sequence[int | float]", result)) if isinstance(result, tuple) else [result]

    def run_batch(self, instance: _Wasm3Instance, steps: Sequence[Step]) -> BatchResult:
        with self._scope(instance):  # the whole batch is one call to the limits
            return super().run_batch(instance, steps)

    def export_function(self, instance: _Wasm3Instance, name: str) -> _Wasm3Function:
        return _Wasm3Function(instance, name)  # pywasm3 has no function objects of its own to hold: by name

    def call_ref(self, func: _Wasm3Function, args: Sequence[int | float], ftype: FuncType) -> list[int | float]:
        return self.call(func.instance, func.name, args, ftype)

    def function_key(self, func: _Wasm3Function) -> tuple[int, str]:
        return (id(func.instance), func.name)  # an instance of its own for each, and the Function keeps it alive

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

    def memory_view(self, memory: Any, offset: int, length: int) -> memoryview:
        _check_range(offset, length, len(memory))
        return memoryview(memory)[offset : offset + length]

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


class _Watchdog:
    """One daemon thread that, when a deadline passes, ends the epoch of an engine, which makes the running wasm trap
    (`TrapCode.INTERRUPT`) at its next check. A thread is only made when an instance asks for a `timeout`."""

    def __init__(self, engine: Any) -> None:
        self._engine = engine
        self._cond = threading.Condition()
        self._deadline: float | None = None
        self._token = 0
        self._stopped = False
        self._thread: threading.Thread | None = None

    def _run(self) -> None:
        with self._cond:
            while not self._stopped:
                if self._deadline is None:
                    self._cond.wait()
                    continue
                left = self._deadline - time.monotonic()
                if left > 0:
                    self._cond.wait(left)
                    continue
                self._deadline = None
                self._engine.increment_epoch()  # under the lock: a `disarm` after this knows it has happened

    def arm(self, seconds: float) -> int:
        with self._cond:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="wasmhost-watchdog", daemon=True)
                self._thread.start()
            self._token += 1
            self._deadline = time.monotonic() + seconds
            self._cond.notify()
            return self._token

    def disarm(self, token: int) -> None:
        with self._cond:
            if self._token == token:
                self._deadline = None

    def stop(self) -> None:
        with self._cond:
            self._stopped = True
            self._cond.notify()


class WasmtimeBackend(Backend):
    """The `wasmtime` package."""

    name = "wasmtime"
    features = frozenset(
        {
            "memory.grow",
            "memory.view",
            "table.length",
            "table.funcs",
            "threads",  # not tied to the thread that made it: calls may come from others, one at a time
            "table.signatures",  # a call of a function of another type than the one given is refused
            "imports",
            "import.global",
            "import.memory",
            "import.table",
            "isolated",
            "timeout",  # an engine that counts epochs, for the instances that ask (it costs speed)
            "fuel",  # and one that counts fuel
        }
    )

    def __init__(self) -> None:
        self._wt: Any = importlib.import_module("wasmtime")  # ImportError here means "backend not available"
        self._engine: Any = self._wt.Engine()
        self._store: Any = None
        # Engines for the instances with a limit, by (counts epochs, counts fuel), each with the watchdog that ends its
        # epoch: what is counted is counted in the code the engine makes (a tight loop runs about 3 times slower with
        # epochs, 2.4 with fuel, 5 with both, measured), so an instance gets an engine that counts only what it asked.
        self._limited: dict[tuple[bool, bool], tuple[Any, _Watchdog | None]] = {}
        self._timeouts: weakref.WeakKeyDictionary[Any, float] = weakref.WeakKeyDictionary()  # store -> seconds
        self._fuels: weakref.WeakKeyDictionary[Any, int] = weakref.WeakKeyDictionary()  # store -> fuel for a call
        self._armed = 0  # how deep in calls that a limit is set for (a host function may call the module again)

    def close(self) -> None:
        for _, watchdog in self._limited.values():
            if watchdog is not None:
                watchdog.stop()

    def _engine_for(self, epochs: bool, fuel: bool) -> tuple[Any, _Watchdog | None]:
        """The engine for instances with a `timeout` (`epochs`) and/or a `fuel` limit, and the watchdog of its epochs;
        made when first needed."""
        key = (epochs, fuel)
        if key not in self._limited:
            config = self._wt.Config()
            config.epoch_interruption = epochs
            config.consume_fuel = fuel
            engine = self._wt.Engine(config)
            self._limited[key] = (engine, _Watchdog(engine) if epochs else None)
        return self._limited[key]

    @contextlib.contextmanager
    def _guard(self, store: Any) -> Generator[None]:
        """Around anything that runs wasm in `store`: if its instance has a `timeout`, a deadline for it, and if it has
        `fuel`, the fuel for the call (the outermost call sets them; a call from a host function inside is under the
        same ones)."""
        seconds, fuel = self._timeouts.get(store), self._fuels.get(store)
        if (seconds is None and fuel is None) or self._armed:
            yield
            return
        self._armed += 1
        watchdog, token = None, 0
        if fuel is not None:
            store.set_fuel(fuel)
        if seconds is not None:
            watchdog = self._engine_for(True, fuel is not None)[1]
            store.set_epoch_deadline(1)  # one tick from now: the watchdog's `increment_epoch` is that tick
            token = cast("_Watchdog", watchdog).arm(seconds)
        try:
            yield
        finally:
            if watchdog is not None:
                watchdog.disarm(token)
            self._armed -= 1

    def _trap(self, exc: Any, store: Any) -> Trap:
        code = getattr(exc, "trap_code", None)
        if code == self._wt.TrapCode.INTERRUPT and store in self._timeouts:
            return Timeout(f"the call ran longer than {self._timeouts[store]:g} s")
        if code == self._wt.TrapCode.OUT_OF_FUEL and store in self._fuels:
            return OutOfFuel(f"the call used more than {self._fuels[store]} units of fuel")
        return Trap(str(exc))

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

    def compile_limited(self, data: bytes, *, epochs: bool, fuel: bool) -> Any:
        try:
            return self._wt.Module(self._engine_for(epochs, fuel)[0], data)
        except self._wt.WasmtimeError as exc:
            raise CompileError(str(exc)) from None

    @property
    def store(self) -> Any:
        """The one store everything shares (as in the JavaScript API), made when first needed."""
        if self._store is None:
            self._store = self._wt.Store(self._engine)
        return self._store

    def instantiate(
        self,
        module: Any,
        imports: Sequence[HostFunction | HostObject] = (),
        *,
        isolated: bool = False,
        timeout: float | None = None,
        fuel: int | None = None,
    ) -> _WasmtimeInstance:
        if timeout is not None or fuel is not None:  # a store of its own, on the engine that counts what it asked
            store = self._wt.Store(self._engine_for(timeout is not None, fuel is not None)[0])
            if timeout is not None:
                self._timeouts[store] = timeout
            if fuel is not None:
                self._fuels[store] = fuel
        else:
            store = self._wt.Store(self._engine) if isolated else self.store
        externs: list[Any] = []
        for host in imports:
            if isinstance(host, HostObject):
                if host.handle.store is not store:
                    raise ValueError(
                        f"{host.module}.{host.name} belongs to another store: an isolated or timed instance can't "
                        "share objects, and nothing can be shared with one"
                    )
                externs.append(host.handle.obj)
            else:
                externs.append(self._host_func(store, host))
        try:
            with self._guard(store):  # a start function runs now
                instance = self._wt.Instance(store, module, externs)
        except self._wt.Trap as exc:  # the start function trapped (or ran out of time)
            raise self._trap(exc, store) from None
        except self._wt.WasmtimeError as exc:  # the imports don't fit the module
            raise LinkError(str(exc)) from None
        return _WasmtimeInstance(store, instance.exports(store))

    def new_global(self, kind: str, value: int | float, mutable: bool) -> _WasmtimeObject:
        wt = self._wt
        store = self.store
        gtype = wt.GlobalType(getattr(wt.ValType, kind)(), mutable)
        return _WasmtimeObject(store, wt.Global(store, gtype, value))

    def new_memory(self, initial: int, maximum: int | None) -> _WasmtimeObject:
        wt = self._wt
        try:
            return _WasmtimeObject(self.store, wt.Memory(self.store, wt.MemoryType(wt.Limits(initial, maximum))))
        except (wt.WasmtimeError, OverflowError) as exc:
            raise ValueError(str(exc)) from None

    def new_table(self, initial: int, maximum: int | None) -> _WasmtimeObject:
        wt = self._wt
        try:
            ttype = wt.TableType(wt.ValType.funcref(), wt.Limits(initial, maximum))
            return _WasmtimeObject(self.store, wt.Table(self.store, ttype, None))
        except (wt.WasmtimeError, OverflowError) as exc:
            raise ValueError(str(exc)) from None

    def export_function(self, instance: _WasmtimeInstance, name: str) -> _WasmtimeObject:
        return _WasmtimeObject(instance.store, instance.exports[name])

    def call_ref(self, func: _WasmtimeObject, args: Sequence[int | float], ftype: FuncType) -> list[int | float]:
        try:
            with self._guard(func.store):
                result = func.obj(func.store, *args)
        except self._wt.Trap as exc:
            raise self._trap(exc, func.store) from None
        if result is None:
            return []
        return list(cast("Sequence[int | float]", result)) if isinstance(result, list) else [result]

    def function_key(self, func: _WasmtimeObject) -> tuple[int, int]:
        # wasmtime-py makes a new Func on every `table.get`, but what it stands for is the pair of the store and the
        # index in it (`getattr`: a name that begins with two underscores is mangled inside a class)
        raw = func.obj._func  # pyright: ignore[reportPrivateUsage]
        return (int(raw.store_id), int(getattr(raw, "__private")))  # noqa: B009

    def function_type(self, func: _WasmtimeObject) -> FuncType | None:
        ftype = func.obj.type(func.store)
        names = {str(i32): i32, str(i64): i64, str(f32): f32, str(f64): f64}
        try:
            return FuncType(tuple(names[str(t)] for t in ftype.params), tuple(names[str(t)] for t in ftype.results))
        except KeyError:  # a type that can't be called from here (v128, a reference)
            return None

    def table_grow(self, table: _WasmtimeObject, delta: int) -> int:
        try:
            return int(table.obj.grow(table.store, delta, None))
        except self._wt.WasmtimeError as exc:
            raise IndexError(str(exc)) from None

    def table_get(self, table: _WasmtimeObject, index: int) -> _WasmtimeObject | None:
        if not 0 <= index < int(table.obj.size(table.store)):
            raise IndexError("table index out of bounds")
        func = table.obj.get(table.store, index)
        if func is None or isinstance(func, self._wt.Val):  # a null funcref comes back as a Val, not as None
            return None
        return _WasmtimeObject(table.store, func)

    def table_set(self, table: _WasmtimeObject, index: int, func: _WasmtimeObject | None) -> None:
        if not 0 <= index < int(table.obj.size(table.store)):
            raise IndexError("table index out of bounds")
        if func is not None and func.store is not table.store:
            raise ValueError("the function belongs to another store")
        table.obj.set(table.store, index, None if func is None else func.obj)

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
            with self._guard(instance.store):
                result = instance.exports[name](instance.store, *args)
        except self._wt.Trap as exc:
            raise self._trap(exc, instance.store) from None
        if result is None:
            return []
        return list(cast("Sequence[int | float]", result)) if isinstance(result, list) else [result]

    def run_batch(self, instance: _WasmtimeInstance, steps: Sequence[Step]) -> BatchResult:
        with self._guard(instance.store):  # the whole batch is one call to the timeout
            return super().run_batch(instance, steps)

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

    def memory_view(self, memory: _WasmtimeObject, offset: int, length: int) -> memoryview:
        _check_range(offset, length, int(memory.obj.data_len(memory.store)))
        return memoryview(memory.obj.get_buffer_ptr(memory.store, length, offset)).cast("B")

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
