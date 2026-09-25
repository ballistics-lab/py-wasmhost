"""JavaScript engines as backends: one bare engine, one primitive (`evaluate`), the WebAssembly API on top.

Every engine evaluates a JavaScript source string in a *bare* context (no browser globals) and returns
the value of its last expression as a string. The engine's own `WebAssembly` object does the work, through a
small registry of modules and instances kept in the engine; a batch of steps is one evaluation.

    JSContextBackend           JavaScriptCore via Pythonista's objc_util, or rubicon-objc (iOS).
    GIJavaScriptCoreBackend    WebKitGTK's JavaScriptCore via PyGObject (Linux): the same engine, driven
                            the same way -- the desktop rehearsal of the Pythonista setup.
    NodeBackend                a long-lived `node` process running a `vm` context, bare like the other two.

Plain Python 3.10+, no third-party imports (objc_util / gi are Pythonista / Linux only and loaded
lazily), so it runs on PyPy and on Pythonista's interpreter.
"""

from __future__ import annotations

import atexit
import importlib
import json
import re
import shutil
import subprocess
from collections.abc import Sequence
from typing import Any

from ._backend import (
    Backend,
    BatchResult,
    CallStep,
    HostFunction,
    Operand,
    ReadStep,
    Step,
    WriteStep,
    check_results,
    normalize,
)
from ._binary import FuncType
from ._capi import CApi, load_library
from ._errors import CompileError, LinkError, Trap

__all__ = ("GIJavaScriptCoreBackend", "JSContextBackend", "JSBackend", "NodeBackend")

_JS_ERRORS: dict[str, type[BaseException]] = {
    "CompileError": CompileError,
    "LinkError": LinkError,
    "RuntimeError": Trap,
    "RangeError": IndexError,  # a memory access out of bounds
    "TypeError": TypeError,
}
# What a host function's failure throws in the engine. The real exception is kept in Python and raised there, from
# the call that was running, when this text comes back (an exception can't cross the engine's boundary itself).
HOST_ERROR = "__wasmhost_host_error__"


class _HostFailed(Exception):
    """A host function raised (or returned the wrong thing); the exception is in `JSBackend._raised`."""


_JS_MESSAGE = re.compile(r"^(?:\[JS\] )?(\w+): ?(.*)$", re.DOTALL)


def _translate(text: str) -> BaseException | None:
    """The Python exception for a JavaScript error's text (`Name: message`), if it has one."""
    m = _JS_MESSAGE.match(text)
    cls = _JS_ERRORS.get(m.group(1)) if m else None
    return cls(m.group(2)) if cls and m else None


# The registry lives in the engine, under one global. ES5 plus BigInt literals (which only appear in
# the calls that use an i64), so any JavaScriptCore with WebAssembly runs it.
_JS = r"""
globalThis.__wh = (function () {
    var mods = [], insts = [], objs = [], HEX = [], UNHEX = new Uint8Array(128), q;
    for (q = 0; q < 256; q++) HEX.push((q + 256).toString(16).slice(1));
    for (q = 0; q < 10; q++) UNHEX[48 + q] = q;
    for (q = 0; q < 6; q++) { UNHEX[97 + q] = 10 + q; UNHEX[65 + q] = 10 + q; }
    function toHex(u8) {
        var parts = new Array(u8.length);
        for (var i = 0; i < u8.length; i++) parts[i] = HEX[u8[i]];
        return parts.join('');
    }
    function fromHex(h) {
        var u = new Uint8Array(h.length >> 1);
        for (var i = 0; i < u.length; i++) u[i] = (UNHEX[h.charCodeAt(2 * i)] << 4) | UNHEX[h.charCodeAt(2 * i + 1)];
        return u;
    }
    function fmt(v) {
        if (typeof v === 'number' && v === 0 && 1 / v < 0) return '-0';
        return String(v);
    }
    function ret(r) {
        if (r === undefined) return '';
        return Array.isArray(r) ? r.map(fmt).join(',') : fmt(r);
    }
    return {
        compile: function (hex) { mods.push(new WebAssembly.Module(fromHex(hex))); return mods.length - 1; },
        validate: function (hex) { return WebAssembly.validate(fromHex(hex)) ? '1' : '0'; },
        instantiate: function (m, imports) {
            insts.push(new WebAssembly.Instance(mods[m], imports || {}));
            return insts.length - 1;
        },
        s: fmt,
        call: function (i, name, args) { return ret(insts[i].exports[name].apply(undefined, args)); },
        // an exported memory, global or table gets a number here, and the operations take it
        obj: function (i, name) { objs.push(insts[i].exports[name]); return objs.length - 1; },
        get: function (o) { return fmt(objs[o].value); },
        set: function (o, v) { objs[o].value = v; return ''; },
        size: function (o) { return String(objs[o].buffer.byteLength); },
        grow: function (o, n) { return String(objs[o].grow(n)); },
        read: function (o, ptr, n) {
            return toHex(new Uint8Array(objs[o].buffer, ptr, n));
        },
        write: function (o, ptr, hex) {
            var b = fromHex(hex), u = new Uint8Array(objs[o].buffer);
            if (ptr < 0 || ptr + b.length > u.length) throw new RangeError('memory access out of bounds');
            u.set(b, ptr);
            return '';
        },
        fromHex: fromHex,
        toHex: toHex,
        tablelen: function (o) { return String(objs[o].length); },
        // A batch: `body` is a function of (exports, results, write, read) made of the calls, memory
        // accesses and early returns of the Python side, so it costs one trip to the engine. Whatever
        // ran before an error is reported with it.
        run: function (i, body) {
            var ex = insts[i].exports, r = [], err = '';
            function W(m, p, h) {
                var b = fromHex(h), u = new Uint8Array(m.buffer);
                if (p < 0 || p + b.length > u.length) throw new RangeError('memory access out of bounds');
                u.set(b, p);
            }
            function R(m, p, n) { return toHex(new Uint8Array(m.buffer, p, n)); }
            try { body(ex, r, W, R, objs); } catch (e) { err = String(e); }
            var out = [];
            for (var k = 0; k < r.length; k++) {
                var v = r[k];
                if (v === undefined) out.push(null);
                else if (typeof v === 'string') out.push(v);
                else out.push(Array.isArray(v) ? v.map(fmt).join(',') : fmt(v));
            }
            return JSON.stringify({ r: out, e: err });
        }
    };
})();
'ok'
"""


def _literal(value: int | float, kind: str) -> str:
    """A value as JavaScript source: an i64 is a BigInt, a float may be NaN or infinite."""
    if kind == "i64":
        return f"{int(value)}n"
    if kind == "i32":
        return str(int(value))
    x = float(value)
    if x != x:
        return "NaN"
    return {float("inf"): "Infinity", float("-inf"): "-Infinity"}.get(x) or repr(x)


def _parse(text: str, kind: str) -> int | float:
    return int(text) if kind in ("i32", "i64") else float(text)


def _text(value: int | float, kind: str) -> str:
    """A value as text for the engine (`Number("NaN")` and `BigInt("5")` take it back)."""
    if kind in ("i32", "i64"):
        return str(int(value))
    x = float(value)
    return "NaN" if x != x else {float("inf"): "Infinity", float("-inf"): "-Infinity"}.get(x) or repr(x)


def _js_value(kind: str, expr: str) -> str:
    return f"BigInt({expr})" if kind == "i64" else f"Number({expr})"


def _expr(operand: Operand) -> str:
    """An operand of a step as JavaScript source."""
    if not isinstance(operand, tuple):
        return str(operand)
    tag: str = operand[0]
    if tag == "ref":
        return f"r[{operand[1]}]"
    if tag == "const":
        return str(operand[1])
    return f"({_expr(operand[1])}{tag}{_expr(operand[2])})"


class JSBackend(Backend):
    """A JavaScript engine. Subclasses implement `evaluate`."""

    def __init__(self) -> None:
        self._ready = False
        self._hosts: list[HostFunction] = []  # the host functions handed out, by the number the engine calls them by
        self._raised: BaseException | None = None  # what a host function raised, until its call comes back
        self._hostcall_ready = False
        self._capi: CApi | None = (
            None  # JavaScriptCore's C API, for engines that are it: bytes in place, host functions
        )

    def evaluate(self, src: str) -> str:
        """Run `src` and return the value of its last expression as a string."""
        raise NotImplementedError

    def _run(self, src: str) -> str:
        if not self._ready:
            self.evaluate(_JS)
            self._ready = True
        try:
            return self.evaluate(src)
        except RuntimeError as exc:
            if self._raised is not None and HOST_ERROR in str(exc):
                raised, self._raised = self._raised, None
                raise raised from None
            if err := _translate(str(exc)):
                raise err from None
            raise

    # --- host functions: an engine that can call back into Python defines `__hostcall` and says so in `features` ---

    def _install_hostcall(self) -> None:
        """Define `__hostcall(number, argumentsAsJson) -> resultsAsJson` in the engine, answered by `_answer`; when
        `_answer` raises, the function throws `HOST_ERROR`. Through the C API here; an engine without it that can
        still call Python (Node, through its pipe) does it its own way."""
        if self._capi is None:
            raise NotImplementedError(f"the {self.name} backend can't take imports (host functions)")
        self._capi.set_function("__hostcall", lambda args: self._answer(int(float(args[0])), args[1]), HOST_ERROR)

    def _answer(self, ident: int, args_json: str) -> str:
        host = self._hosts[ident]
        try:
            args = [_parse(t, k) for t, k in zip(json.loads(args_json), host.ftype.params, strict=True)]
            values = check_results(host.fn(*args), host.ftype)
            return json.dumps([_text(v, k) for v, k in zip(values, host.ftype.results, strict=True)])
        except BaseException as exc:  # noqa: BLE001 -- whatever it is, it goes to the caller of the export
            self._raised = exc
            raise _HostFailed from None

    def _import_object(self, imports: Sequence[HostFunction]) -> str:
        """JavaScript source of the import object: each function turns its arguments into text, asks `__hostcall`
        and turns the answer back into what the signature says."""
        modules: dict[str, list[str]] = {}
        for host in imports:
            ident = len(self._hosts)
            self._hosts.append(host)
            n = len(host.ftype.params)
            call = f"__hostcall({ident},JSON.stringify([{','.join(f'__wh.s(a{i})' for i in range(n))}]))"
            values = [_js_value(k, f"r[{i}]") for i, k in enumerate(host.ftype.results)]
            if not values:
                body = f"{call};"
            elif len(values) == 1:
                body = f"var r=JSON.parse({call});return {values[0]};"
            else:
                body = f"var r=JSON.parse({call});return [{','.join(values)}];"
            params = ",".join(f"a{i}" for i in range(n))
            modules.setdefault(host.module, []).append(f"{json.dumps(host.name)}:function({params}){{{body}}}")
        return "{" + ",".join(f"{json.dumps(m)}:{{{','.join(fns)}}}" for m, fns in modules.items()) + "}"

    # --- bytes in and out of the engine, for a caller with JavaScript of its own (a Pyodide, an emulator) ---

    def put_bytes(self, target: str, data: bytes) -> str:
        """Assign a `Uint8Array` holding `data` to the JavaScript expression `target` (say `__files["a"]`).

        Through JavaScriptCore's C API where the engine has it (the array is filled in place), else through hex,
        which costs twice the size in a string. Returns which one was used (`"C API"` or `"hex"`)."""
        if self._capi is not None:
            try:
                self._capi.set_global_bytes("__wasmhost_in", data)
                self.evaluate(f"{target} = globalThis.__wasmhost_in; delete globalThis.__wasmhost_in;")
                if self.evaluate(f"String(({target} || {{}}).length)") != str(len(data)):  # it must have arrived whole
                    raise OSError("the typed array is not visible in JavaScript")
            except Exception:  # noqa: BLE001 -- whatever it was, hex works
                self._capi = None
            else:
                return "C API"
        self._run(f'{target} = __wh.fromHex("{data.hex()}");')
        return "hex"

    def get_bytes(self, expr: str) -> bytes:
        """The bytes of the `Uint8Array` that the JavaScript expression `expr` evaluates to."""
        if self._capi is not None:
            try:
                return self._capi.get_bytes(expr)
            except Exception:  # noqa: BLE001
                self._capi = None
        return bytes.fromhex(self._run(f"__wh.toHex({expr})"))

    def validate(self, data: bytes) -> bool:
        return self._run(f'__wh.validate("{data.hex()}")') == "1"

    def compile(self, data: bytes) -> int:
        return int(self._run(f'__wh.compile("{data.hex()}")'))

    def instantiate(self, module: int, imports: Sequence[HostFunction] = ()) -> int:
        if not imports:
            return int(self._run(f"__wh.instantiate({module})"))
        if not self.supports("imports"):
            raise NotImplementedError(f"the {self.name} backend can't take imports (host functions)")
        self._run("0")  # the registry (`__wh`) first: the import object uses it
        if not self._hostcall_ready:
            self._install_hostcall()
            self._hostcall_ready = True
        return int(self._run(f"__wh.instantiate({module},{self._import_object(imports)})"))

    def call(self, instance: int, name: str, args: Sequence[int | float], ftype: FuncType) -> list[int | float]:
        literals = ",".join(_literal(a, k) for a, k in zip(args, ftype.params, strict=True))
        text = self._run(f"__wh.call({instance},{json.dumps(name)},[{literals}])")
        return [_parse(t, k) for t, k in zip(text.split(","), ftype.results, strict=True)] if ftype.results else []

    def _o(self, fn: str, obj: int, *args: object) -> str:
        extra = "".join(f",{a}" for a in args)
        return self._run(f"__wh.{fn}({obj}{extra})")

    def _export(self, instance: int, name: str) -> int:
        return int(self._run(f"__wh.obj({instance},{json.dumps(name)})"))

    def export_memory(self, instance: int, name: str) -> int:
        return self._export(instance, name)

    def export_global(self, instance: int, name: str, kind: str) -> int:
        return self._export(instance, name)

    def export_table(self, instance: int, name: str) -> int:
        return self._export(instance, name)

    def memory_size(self, memory: int) -> int:
        return int(self._o("size", memory))

    def memory_grow(self, memory: int, pages: int) -> int:
        return int(self._o("grow", memory, int(pages)))

    def memory_read(self, memory: int, offset: int, length: int) -> bytes:
        return bytes.fromhex(self._o("read", memory, int(offset), int(length))) if length else b""

    def memory_write(self, memory: int, offset: int, data: bytes) -> None:
        self._o("write", memory, int(offset), json.dumps(data.hex()))

    def global_get(self, glob: int, kind: str) -> int | float:
        return _parse(self._o("get", glob), kind)

    def global_set(self, glob: int, kind: str, value: int | float) -> None:
        self._o("set", glob, _literal(normalize(value, kind), kind))

    def table_length(self, table: int) -> int:
        return int(self._o("tablelen", table))

    def run_batch(self, instance: int, steps: Sequence[Step]) -> BatchResult:
        """The steps as one JavaScript function, run in one trip; whatever ran before an error is reported with it."""
        lines: list[str] = []
        kinds: dict[int, str] = {}
        for step in steps:
            if isinstance(step, CallStep):
                operands = ",".join(
                    _expr(a) if isinstance(a, tuple) else _literal(a, k)
                    for a, k in zip(step.args, step.ftype.params, strict=True)
                )
                call = f"ex[{json.dumps(step.name)}]({operands})"
                if step.ftype.results:
                    kinds[step.out] = step.ftype.results[0]
                    lines.append(f"r[{step.out}]={call};")
                else:
                    kinds[step.out] = "void"
                    lines.append(f"r[{step.out}]=({call},0);")  # a 0 marks the step as done
            elif isinstance(step, WriteStep):
                lines.append(f"W(O[{step.memory}],{_expr(step.offset)},{json.dumps(step.data.hex())});")
            elif isinstance(step, ReadStep):
                kinds[step.out] = "bytes"
                lines.append(f"r[{step.out}]=R(O[{step.memory}],{_expr(step.offset)},{_expr(step.length)});")
            else:
                lines.append(f"if ({_expr(step.value)}{'===' if step.when_zero else '!=='}0) return;")
        body = "function(ex,r,W,R,O){" + "".join(lines) + "}"
        reply = json.loads(self._run(f"__wh.run({instance},{body})"))
        values: dict[int, Any] = {}
        for slot, text in enumerate(reply["r"]):
            if text is None or slot not in kinds:
                continue
            kind = kinds[slot]
            values[slot] = bytes.fromhex(text) if kind == "bytes" else None if kind == "void" else _parse(text, kind)
        error: BaseException | None = None
        if reply["e"]:
            if HOST_ERROR in reply["e"] and self._raised is not None:
                error, self._raised = self._raised, None
            else:
                error = _translate(reply["e"]) or RuntimeError(reply["e"])
        return BatchResult(values, error)


def _check_webassembly(backend: JSBackend) -> None:
    kind = backend.evaluate("typeof WebAssembly")
    if kind != "object":
        raise RuntimeError(f"WebAssembly is not available in this JavaScript engine (typeof WebAssembly = {kind})")


class JSContextBackend(JSBackend):
    """JavaScriptCore's `JSContext` through an Objective-C bridge: Pythonista's `objc_util`, or BeeWare's
    `rubicon-objc` in the iOS Python apps that don't have it (`pip install rubicon-objc`, >= 0.5.4)."""

    name = "jscontext"

    def __init__(self) -> None:
        super().__init__()
        # Untyped Objective-C proxies: import them as explicit Any.
        self._ctx: Any
        self._rubicon = False
        self.bridge = "objc_util"  # which Objective-C bridge is in use
        lib: Any = None  # the C functions of JavaScriptCore, for bytes and host functions
        try:
            objc_util: Any = importlib.import_module("objc_util")
            self._ctx = objc_util.ObjCClass("JSContext").alloc().init()
            lib = getattr(objc_util, "c", None)  # objc_util's `c` finds them in the process
        except ImportError:
            try:
                rubicon: Any = importlib.import_module("rubicon.objc")
            except ImportError:
                raise ImportError(
                    "no Objective-C bridge: JSContext needs Pythonista's objc_util or rubicon-objc"
                ) from None
            try:  # on a Mac the framework has to be loaded before its classes exist; already there on iOS
                lib = load_library()
            except OSError:
                pass
            self._ctx = rubicon.ObjCClass("JSContext").alloc().init()
            self._rubicon = True
            self.bridge = "rubicon-objc"
        self.c_api_error: str | None = None  # why there is no C API (bytes through hex, no host functions), if so
        if lib is None:
            self.c_api_error = "no JavaScriptCore library to take the C functions from"
        else:
            try:  # the context reference has to be asked of the proxy; asking now shows whether it can be
                capi = CApi(lib, self._context_ref)
                capi.ref  # noqa: B018
                self._capi = capi
            except Exception as exc:  # noqa: BLE001 -- no C API here: bytes go through hex, and no host functions
                self.c_api_error = f"{type(exc).__name__}: {exc}"
        if self._capi is not None:
            self.features = self.features | {"imports"}
        _check_webassembly(self)

    def _context_ref(self) -> Any:
        """The JSGlobalContextRef of the Objective-C context: a method under objc_util, a property under rubicon."""
        ref = self._ctx.JSGlobalContextRef
        if callable(ref):  # a method (objc_util) or a bound selector (rubicon-objc); a c_void_p or an int is not
            ref = ref()
        # rubicon-objc may hand the pointer back wrapped in an ObjCInstance, which keeps the raw one as `.ptr`
        ptr = getattr(ref, "ptr", None)
        return ref if ptr is None else ptr

    def evaluate(self, src: str) -> str:
        if self._rubicon:  # rubicon-objc: methods take their arguments positionally, properties are attributes
            res = self._ctx.evaluateScript(src)
            exc = self._ctx.exception
            if exc:
                self._ctx.exception = None
                raise RuntimeError(f"[JS] {exc.toString()}")
            return str(res.toString())
        res = self._ctx.evaluateScript_(src)
        exc = self._ctx.exception()
        if exc:
            self._ctx.setException_(None)
            raise RuntimeError(f"[JS] {exc.toString()}")
        return str(res.toString())


class GIJavaScriptCoreBackend(JSBackend):
    """WebKitGTK's JavaScriptCore via PyGObject: `apt install gir1.2-javascriptcoregtk-4.1 python3-gi`."""

    name = "gi-jsc"

    def __init__(self) -> None:
        super().__init__()
        # PyGObject (Linux), untyped GObject-introspection proxies: import it as an explicit Any.
        gi: Any = importlib.import_module("gi")
        gi.require_version("JavaScriptCore", "4.1")
        javascriptcore: Any = importlib.import_module("gi.repository.JavaScriptCore")
        self._ctx: Any = javascriptcore.Context()
        _check_webassembly(self)

    def evaluate(self, src: str) -> str:
        res = self._ctx.evaluate(src, -1)
        exc = self._ctx.get_exception()
        if exc:
            self._ctx.clear_exception()
            raise RuntimeError(f"[JS] {exc.to_string()}")
        return str(res.to_string())


# A `vm` context is a fresh global object with only the JS builtins (Promise, WebAssembly, Date, ...).
# The process leaves when its stdin closes, so a killed parent doesn't leave it running.
#
# The protocol is JSON lines. Python sends {"eval": source}; Node answers {"ok": value} or {"err": text}. A host
# function is `__hostcall(number, argumentsAsJson)`: Node writes {"cb": [number, arguments]} and then *reads its
# stdin synchronously* for {"ret": text} (or {"fail": true}) -- a WebAssembly call is on the stack and can't be
# left, so the wait can't be an event -- while Python may send further {"eval": ...} lines meanwhile (a host
# function that calls the module again), which are answered from inside the wait, nested. Everything is written
# with fs.writeSync, so a reply can't be queued behind a wait.
_NODE_LOOP = r"""
const fs = require('fs'), vm = require('vm'), { StringDecoder } = require('string_decoder');
const ctx = vm.createContext({});
const pause = new Int32Array(new SharedArrayBuffer(4));
const sleep = ms => Atomics.wait(pause, 0, 0, ms);

function writeAll(text) {
    const bytes = Buffer.from(text);
    for (let off = 0; off < bytes.length;) {
        try { off += fs.writeSync(1, bytes, off, bytes.length - off); }
        catch (e) { if (e.code === 'EAGAIN') sleep(0.05); else throw e; }
    }
}
function evaluate(src) {
    try { return { ok: String(vm.runInContext(src, ctx)) }; }
    catch (e) { return { err: String(e) }; }  // same text as JavaScriptCore's exception.toString()
}

const decoder = new StringDecoder('utf8');
let pending = '';
function readLineSync() {
    for (let spins = 0; ;) {
        const i = pending.indexOf('\n');
        if (i >= 0) { const line = pending.slice(0, i); pending = pending.slice(i + 1); return line; }
        const chunk = Buffer.allocUnsafe(1 << 16);
        let n;
        try { n = fs.readSync(0, chunk, 0, chunk.length, null); }
        catch (e) {
            if (e.code === 'EAGAIN') { sleep(spins++ < 200 ? 0.02 : 1); continue; }
            if (e.code === 'EOF') return null;
            throw e;
        }
        if (n === 0) return null;
        spins = 0;
        pending += decoder.write(chunk.subarray(0, n));
    }
}
ctx.__hostcall = (ident, argsJson) => {
    writeAll(JSON.stringify({ cb: [ident, argsJson] }) + '\n');
    for (;;) {
        const line = readLineSync();
        if (line === null) throw new Error('wasmhost: the parent closed the pipe');
        const msg = JSON.parse(line);
        if (msg.eval !== undefined) { writeAll(JSON.stringify(evaluate(msg.eval)) + '\n'); continue; }
        if (msg.fail) throw '@HOST_ERROR@';
        return msg.ret;
    }
};

const rl = require('readline').createInterface({ input: process.stdin });
rl.on('line', (line) => writeAll(JSON.stringify(evaluate(JSON.parse(line).eval)) + '\n'));
rl.on('close', () => process.exit(0));
""".replace("@HOST_ERROR@", HOST_ERROR)


class NodeBackend(JSBackend):
    """A long-lived `node` process evaluating scripts in a `vm` context (see the protocol above)."""

    name = "node"
    features = JSBackend.features | {"imports"}

    def __init__(self, node: str | None = None) -> None:
        super().__init__()
        node = node or shutil.which("node")
        if not node:
            raise FileNotFoundError("node not found on PATH")
        self._proc = subprocess.Popen(
            [node, "-e", _NODE_LOOP],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            universal_newlines=True,
            encoding="utf-8",
        )
        if self._proc.stdin is None or self._proc.stdout is None:  # can't happen with PIPE; narrows the types
            raise RuntimeError("node started without stdin/stdout pipes")
        self._stdin = self._proc.stdin
        self._stdout = self._proc.stdout
        atexit.register(self.close)
        try:
            _check_webassembly(self)
        except Exception:
            self.close()
            raise

    def _send(self, message: dict[str, Any]) -> None:
        self._stdin.write(json.dumps(message) + "\n")
        self._stdin.flush()

    def _install_hostcall(self) -> None:
        """Nothing to do: `__hostcall` is in the context from the start."""

    def evaluate(self, src: str) -> str:
        self._send({"eval": src})
        while True:
            line = self._stdout.readline()
            if not line:
                raise RuntimeError(f"node exited (status {self._proc.poll()})")
            reply = json.loads(line)
            if "cb" in reply:  # a host function is being called: answer it (it may evaluate again, nested)
                ident, args_json = reply["cb"]
                try:
                    self._send({"ret": self._answer(ident, args_json)})
                except _HostFailed:
                    self._send({"fail": True})
            elif "err" in reply:
                raise RuntimeError(f"[JS] {reply['err']}")
            else:
                return str(reply["ok"])

    def close(self) -> None:
        if self._proc.poll() is None:
            self._stdin.close()
            self._proc.wait(timeout=5)
        if not self._stdout.closed:
            self._stdout.close()
