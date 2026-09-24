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

from ._backend import Backend, BatchResult, CallStep, Operand, ReadStep, Step, WriteStep, normalize
from ._binary import FuncType
from ._errors import CompileError, LinkError, Trap

__all__ = ("GIJavaScriptCoreBackend", "JSContextBackend", "JSBackend", "NodeBackend")

_JS_ERRORS: dict[str, type[BaseException]] = {
    "CompileError": CompileError,
    "LinkError": LinkError,
    "RuntimeError": Trap,
    "RangeError": IndexError,  # a memory access out of bounds
    "TypeError": TypeError,
}
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
    var mods = [], insts = [], HEX = [], UNHEX = new Uint8Array(128), q;
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
        instantiate: function (m) { insts.push(new WebAssembly.Instance(mods[m], {})); return insts.length - 1; },
        call: function (i, name, args) { return ret(insts[i].exports[name].apply(undefined, args)); },
        get: function (i, name) { return fmt(insts[i].exports[name].value); },
        set: function (i, name, v) { insts[i].exports[name].value = v; return ''; },
        size: function (i, name) { return String(insts[i].exports[name].buffer.byteLength); },
        grow: function (i, name, n) { return String(insts[i].exports[name].grow(n)); },
        read: function (i, name, ptr, n) {
            return toHex(new Uint8Array(insts[i].exports[name].buffer, ptr, n));
        },
        write: function (i, name, ptr, hex) {
            var b = fromHex(hex), u = new Uint8Array(insts[i].exports[name].buffer);
            if (ptr < 0 || ptr + b.length > u.length) throw new RangeError('memory access out of bounds');
            u.set(b, ptr);
            return '';
        },
        tablelen: function (i, name) { return String(insts[i].exports[name].length); },
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
            try { body(ex, r, W, R); } catch (e) { err = String(e); }
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
            if err := _translate(str(exc)):
                raise err from None
            raise

    def validate(self, data: bytes) -> bool:
        return self._run(f'__wh.validate("{data.hex()}")') == "1"

    def compile(self, data: bytes) -> int:
        return int(self._run(f'__wh.compile("{data.hex()}")'))

    def instantiate(self, module: int) -> int:
        return int(self._run(f"__wh.instantiate({module})"))

    def call(self, instance: int, name: str, args: Sequence[int | float], ftype: FuncType) -> list[int | float]:
        literals = ",".join(_literal(a, k) for a, k in zip(args, ftype.params, strict=True))
        text = self._run(f"__wh.call({instance},{json.dumps(name)},[{literals}])")
        return [_parse(t, k) for t, k in zip(text.split(","), ftype.results, strict=True)] if ftype.results else []

    def _q(self, fn: str, instance: int, name: str, *args: object) -> str:
        extra = "".join(f",{a}" for a in args)
        return self._run(f"__wh.{fn}({instance},{json.dumps(name)}{extra})")

    def memory_size(self, instance: int, name: str) -> int:
        return int(self._q("size", instance, name))

    def memory_grow(self, instance: int, name: str, pages: int) -> int:
        return int(self._q("grow", instance, name, int(pages)))

    def memory_read(self, instance: int, name: str, offset: int, length: int) -> bytes:
        return bytes.fromhex(self._q("read", instance, name, int(offset), int(length))) if length else b""

    def memory_write(self, instance: int, name: str, offset: int, data: bytes) -> None:
        self._q("write", instance, name, int(offset), json.dumps(data.hex()))

    def global_get(self, instance: int, name: str, kind: str) -> int | float:
        return _parse(self._q("get", instance, name), kind)

    def global_set(self, instance: int, name: str, kind: str, value: int | float) -> None:
        self._q("set", instance, name, _literal(normalize(value, kind), kind))

    def table_length(self, instance: int, name: str) -> int:
        return int(self._q("tablelen", instance, name))

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
                lines.append(f"W(ex[{json.dumps(step.memory)}],{_expr(step.offset)},{json.dumps(step.data.hex())});")
            elif isinstance(step, ReadStep):
                kinds[step.out] = "bytes"
                lines.append(
                    f"r[{step.out}]=R(ex[{json.dumps(step.memory)}],{_expr(step.offset)},{_expr(step.length)});"
                )
            else:
                lines.append(f"if ({_expr(step.value)}{'===' if step.when_zero else '!=='}0) return;")
        body = "function(ex,r,W,R){" + "".join(lines) + "}"
        reply = json.loads(self._run(f"__wh.run({instance},{body})"))
        values: dict[int, Any] = {}
        for slot, text in enumerate(reply["r"]):
            if text is None or slot not in kinds:
                continue
            kind = kinds[slot]
            values[slot] = bytes.fromhex(text) if kind == "bytes" else None if kind == "void" else _parse(text, kind)
        error: BaseException | None = None
        if reply["e"]:
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
        try:
            objc_util: Any = importlib.import_module("objc_util")
            self._ctx = objc_util.ObjCClass("JSContext").alloc().init()
        except ImportError:
            try:
                rubicon: Any = importlib.import_module("rubicon.objc")
            except ImportError:
                raise ImportError(
                    "no Objective-C bridge: JSContext needs Pythonista's objc_util or rubicon-objc"
                ) from None
            self._ctx = rubicon.ObjCClass("JSContext").alloc().init()
            self._rubicon = True
            self.bridge = "rubicon-objc"
        _check_webassembly(self)

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
_NODE_LOOP = r"""
const vm = require('vm');
const ctx = vm.createContext({});
const rl = require('readline').createInterface({ input: process.stdin });
rl.on('line', (line) => {
    let reply;
    try { reply = { ok: true, value: String(vm.runInContext(JSON.parse(line), ctx)) }; }
    catch (e) { reply = { ok: false, value: String(e) }; }  // same text as JavaScriptCore's exception.toString()
    process.stdout.write(JSON.stringify(reply) + '\n');
});
rl.on('close', () => process.exit(0));
"""


class NodeBackend(JSBackend):
    """A long-lived `node` process evaluating one JSON-encoded script per line in a `vm` context."""

    name = "node"

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

    def evaluate(self, src: str) -> str:
        self._stdin.write(json.dumps(src) + "\n")
        self._stdin.flush()
        line = self._stdout.readline()
        if not line:
            raise RuntimeError(f"node exited (status {self._proc.poll()})")
        reply = json.loads(line)
        if not reply["ok"]:
            raise RuntimeError(f"[JS] {reply['value']}")
        return str(reply["value"])

    def close(self) -> None:
        if self._proc.poll() is None:
            self._stdin.close()
            self._proc.wait(timeout=5)
        if not self._stdout.closed:
            self._stdout.close()
