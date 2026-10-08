"""Pyodide as a backend: wasmhost running *inside* Pyodide (CPython in WebAssembly, in a browser or Node), driving the
`WebAssembly` object of the JavaScript engine around it through Pyodide's `js` module. No evaluation of source text,
no registry in the engine to speak of: a Python object stands for a JavaScript one and calls are direct, as with the
native runtimes.

Only where there is a `js` module (so only in Pyodide); anywhere else constructing it raises ImportError, which is how
`default_backend` knows it is not available.
"""

from __future__ import annotations

import importlib
from collections.abc import Hashable, Sequence
from typing import Any

from ._backend import Backend, HostFunction, HostObject, check_results
from ._binary import FuncType
from ._js import HOST_ERROR, parse_value, translate_error, value_text

__all__ = ("PyodideBackend",)

# The few things that need JavaScript's own types: a BigInt for an i64 (Pyodide hands one to Python as an int and
# takes an int back as a Number), a -0 that Python would see as the int 0, and a `null`, which Python can't make.
# Values cross as text where they could be lost; an i64 is a BigInt only on this side of the line.
_SHIM = r"""
(function () {
    var objs = [], ids = new Map();
    function fmt(v) { return typeof v === 'number' && v === 0 && 1 / v < 0 ? '-0' : String(v); }
    function pack(kinds, args) {
        return args.map(function (a, i) {
            return kinds[i] === 'i64' ? BigInt(a) : kinds[i] === 'i32' ? Number(a) : Number(a);
        });
    }
    function unpack(r) {
        if (r === undefined) return [];
        return (Array.isArray(r) ? r : [r]).map(fmt);
    }
    return {
        call: function (f, kinds, args) { return unpack(f.apply(undefined, pack(kinds, args))); },
        host: function (fn, ptypes, rtypes) {
            return function () {
                var r = fn(Array.prototype.map.call(arguments, fmt));
                var v = r.map(function (t, i) { return rtypes[i] === 'i64' ? BigInt(t) : Number(t); });
                return v.length === 0 ? undefined : v.length === 1 ? v[0] : v;
            };
        },
        newGlobal: function (kind, mutable, v) {
            return new WebAssembly.Global({ value: kind, mutable: mutable }, kind === 'i64' ? BigInt(v) : Number(v));
        },
        get: function (g) { return fmt(g.value); },
        set: function (g, kind, v) { g.value = kind === 'i64' ? BigInt(v) : Number(v); },
        newMemory: function (initial, maximum) {
            var d = { initial: initial };
            if (maximum >= 0) d.maximum = maximum;
            return new WebAssembly.Memory(d);
        },
        newTable: function (initial, maximum) {
            var d = { element: 'anyfunc', initial: initial };
            if (maximum >= 0) d.maximum = maximum;
            return new WebAssembly.Table(d);
        },
        tableSet: function (t, i, f) { t.set(i, f === undefined ? null : f); },
        key: function (o) {
            var n = ids.get(o);
            if (n === undefined) { objs.push(o); n = objs.length - 1; ids.set(o, n); }
            return n;
        },
        imports: function (spec) {
            var out = {};
            spec.forEach(function (e) { (out[e[0]] = out[e[0]] || {})[e[1]] = e[2]; });
            return out;
        },
    };
})()
"""


class PyodideBackend(Backend):
    """The `WebAssembly` of the JavaScript engine that Pyodide runs in, called directly."""

    name = "pyodide"
    # One store, as in the JavaScript API: what is made on its own can be imported by any instance. Nothing is counted
    # or timed, and the call runs on the one thread that Python has, so there is nothing to stop it from.
    features = Backend.features | {"imports", "import.global", "import.memory", "import.table", "table.funcs"}

    def __init__(self) -> None:
        js: Any = importlib.import_module("js")  # ImportError here means "not in Pyodide"
        ffi: Any = importlib.import_module("pyodide.ffi")
        self._js: Any = js
        self._wasm: Any = js.WebAssembly
        self._u8: Any = js.Uint8Array
        self._to_js: Any = ffi.to_js
        self._null: Any = getattr(ffi, "jsnull", None)
        self._proxy: Any = ffi.create_proxy
        self._js_exception: Any = ffi.JsException
        self._shim: Any = importlib.import_module("pyodide.code").run_js(_SHIM)
        self._proxies: list[Any] = []  # the host functions handed out: JavaScript holds them, so Python has to
        self._raised: BaseException | None = None  # what a host function raised, until its call comes back

    def close(self) -> None:
        proxies, self._proxies = self._proxies, []
        for proxy in proxies:
            proxy.destroy()

    # --- errors: a JavaScript error is a `JsException`; its text is `Name: message` ---

    def _guard(self, fn: Any, *args: Any) -> Any:
        try:
            return fn(*args)
        except Exception as exc:
            if self._raised is not None:  # a host function failed; Pyodide gives it (or its stand-in) back as it is
                raised, self._raised = self._raised, None
                raise raised from None
            if isinstance(exc, self._js_exception) and (translated := translate_error(str(exc))) is not None:
                raise translated from None
            raise

    # --- bytes: a JavaScript typed array holding (or filled from) Python's ---

    def _bytes_in(self, data: bytes) -> Any:
        array = self._u8.new(len(data))
        array.assign(data)
        return array

    def validate(self, data: bytes) -> bool:
        return bool(self._wasm.validate(self._bytes_in(data)))

    def compile(self, data: bytes) -> Any:
        return self._guard(self._wasm.Module.new, self._bytes_in(data))

    def instantiate(
        self,
        module: Any,
        imports: Sequence[HostFunction | HostObject] = (),
        *,
        isolated: bool = False,
        timeout: float | None = None,
        fuel: int | None = None,
    ) -> Any:
        if fuel is not None:
            raise NotImplementedError("the pyodide backend can't count what a call runs")
        if isolated:
            raise NotImplementedError("the pyodide backend has one store for everything: no isolated instances")
        if timeout is not None:
            raise NotImplementedError("the pyodide backend can't stop a call that runs too long")
        spec = [[h.module, h.name, h.handle if isinstance(h, HostObject) else self._host(h)] for h in imports]
        import_object = self._shim.imports(self._to_js(spec, depth=1)) if spec else self._js.Object.new()
        return self._guard(self._wasm.Instance.new, module, import_object)

    def _host(self, host: HostFunction) -> Any:
        """The JavaScript function the module calls for a host function: it takes its arguments as text, calls the
        Python callable and gives back text. What the callable raises is kept to be raised by the call that was running,
        as an error that goes through the engine is not the exception any more."""

        def answer(args: Any) -> Any:
            try:
                values = [parse_value(t, k) for t, k in zip(args, host.ftype.params, strict=True)]
                results = check_results(host.fn(*values), host.ftype)
                return self._to_js([value_text(v, k) for v, k in zip(results, host.ftype.results, strict=True)])
            except BaseException as exc:  # noqa: BLE001 -- whatever it is, it goes to the caller of the export
                self._raised = exc
                raise RuntimeError(HOST_ERROR) from None

        proxy = self._proxy(answer)
        self._proxies.append(proxy)
        return self._shim.host(proxy, self._to_js(list(host.ftype.params)), self._to_js(list(host.ftype.results)))

    def new_global(self, kind: str, value: int | float, mutable: bool) -> Any:
        return self._guard(self._shim.newGlobal, kind, mutable, value_text(value, kind))

    # --- calls ---

    def _call(self, func: Any, args: Sequence[int | float], ftype: FuncType) -> list[int | float]:
        texts = self._guard(
            self._shim.call,
            func,
            self._to_js(list(ftype.params)),
            self._to_js([value_text(a, k) for a, k in zip(args, ftype.params, strict=True)]),
        )
        return [parse_value(t, k) for t, k in zip(texts, ftype.results, strict=True)]

    def call(self, instance: Any, name: str, args: Sequence[int | float], ftype: FuncType) -> list[int | float]:
        return self._call(getattr(instance.exports, name), args, ftype)

    def call_ref(self, func: Any, args: Sequence[int | float], ftype: FuncType) -> list[int | float]:
        return self._call(func, args, ftype)

    def export_function(self, instance: Any, name: str) -> Any:
        return getattr(instance.exports, name)

    def function_key(self, func: Any) -> Hashable:
        return int(self._shim.key(func))  # the same JavaScript function is the same number, whichever way it came

    def export_memory(self, instance: Any, name: str) -> Any:
        return getattr(instance.exports, name)

    def export_global(self, instance: Any, name: str, kind: str) -> Any:
        return getattr(instance.exports, name)

    def export_table(self, instance: Any, name: str) -> Any:
        return getattr(instance.exports, name)

    # --- memory ---

    def _span(self, memory: Any, offset: int, length: int) -> Any:
        buffer = memory.buffer
        if offset < 0 or length < 0 or offset + length > buffer.byteLength:
            raise IndexError("memory access out of bounds")
        return self._u8.new(buffer, offset, length)

    def memory_size(self, memory: Any) -> int:
        return int(memory.buffer.byteLength)

    def memory_grow(self, memory: Any, pages: int) -> int:
        return int(self._guard(memory.grow, int(pages)))

    def memory_read(self, memory: Any, offset: int, length: int) -> bytes:
        return bytes(self._span(memory, offset, length).to_bytes())

    def memory_write(self, memory: Any, offset: int, data: bytes) -> None:
        self._span(memory, offset, len(data)).assign(data)

    # --- globals and tables ---

    def global_get(self, glob: Any, kind: str) -> int | float:
        return parse_value(self._shim.get(glob), kind)

    def global_set(self, glob: Any, kind: str, value: int | float) -> None:
        self._guard(self._shim.set, glob, kind, value_text(value, kind))

    def table_length(self, table: Any) -> int:
        return int(table.length)

    def new_memory(self, initial: int, maximum: int | None) -> Any:
        return self._guard(self._shim.newMemory, int(initial), -1 if maximum is None else int(maximum))

    def new_table(self, initial: int, maximum: int | None) -> Any:
        return self._guard(self._shim.newTable, int(initial), -1 if maximum is None else int(maximum))

    def table_grow(self, table: Any, delta: int) -> int:
        return int(self._guard(table.grow, int(delta)))

    def table_get(self, table: Any, index: int) -> Any | None:
        func = self._guard(table.get, int(index))
        return None if func is None or func is self._null else func  # a JavaScript `null` is not None in Pyodide

    def table_set(self, table: Any, index: int, func: Any | None) -> None:
        self._guard(self._shim.tableSet, table, int(index), func)
