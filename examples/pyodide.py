"""Pyodide (CPython -> WebAssembly) in a bare JavaScript engine, through wasmhost.

    python examples/pyodide.py [DIR] [--backend NAME] [-c CODE]

An interactive Python REPL that runs in Pyodide, on whichever JavaScript engine wasmhost finds: JSContext in
Pythonista or PythonIDE, WebKitGTK's JavaScriptCore or Node. Pyodide's own JavaScript (an Emscripten build) runs
in the engine; this file only feeds it: the package from npm (once, cached), a memory snapshot so that later starts
skip CPython's startup, `fetch` served by Python, and the packages installed in earlier sessions.

Adapted from https://gist.github.com/o-murphy/dd898e490094eaddab0875187e27f11a, which drives a Pythonista JSContext
directly. What was Pythonista-specific there now lives in wasmhost: the engine (`wasmhost.default_backend`) and the
fast way of moving bytes (`JSBackend.put_bytes` / `get_bytes`: JavaScriptCore's C API on JSContext, hex elsewhere).

DIR is a directory holding pyodide.js, pyodide.asm.mjs, pyodide.asm.wasm, python_stdlib.zip and
pyodide-lock.json (the npm package, the CDN or a GitHub release); without it the npm package is downloaded once
into the cache (`$WASMHOST_CACHE`, else `~/.cache/wasmhost`, or `./.cache` where there is no usable home:
PythonIDE). The first start compiles CPython's
WebAssembly and takes a while; with the snapshot the next ones do not.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import tarfile
import time
import urllib.error
import urllib.request

import wasmhost

# --- Package ---
PYODIDE_VERSION = "314.0.7"
PKG_URL = f"https://registry.npmjs.org/pyodide/-/pyodide-{PYODIDE_VERSION}.tgz"
WHEELS_URL = f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/"  # packages from the lock file
HTTP_TIMEOUT = 60
PKG_FILES = ("pyodide.js", "pyodide.asm.mjs", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json")
BINARY_FILES = ("pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json")
SNAPSHOT_FILE = "snapshot.bin"  # memory state right after CPython startup (its name inside JS; on disk one per backend)
SITE_FILE = "site-packages.zip"  # installed packages between runs
SITE_TMP = "/tmp/site-packages.zip"  # inside the Pyodide filesystem
INDEX_URL = "/pyodide/"  # virtual path inside JS, not the filesystem

# --- Patches for pyodide.asm.mjs ---
ASM_PATCHES = (
    # ES module -> classic script
    (r"export\s+default\s+\w+\s*;?", ""),
    # in the shell environment Emscripten takes random bytes from d8 `os.system`
    (
        r"var\s+initRandomFill\s*=\s*\(\)\s*=>\s*\{",
        "var initRandomFill=()=>{if(globalThis.crypto?.getRandomValues)return view=>crypto.getRandomValues(view);",
    ),
    # shell can't fetch URLs: hand them to our fetch (served by Python)
    (
        r',(\w+)\.includes\("://"\)\)throw new Error\("Shell cannot fetch urls"\)',
        r',\1.includes("://"))return{response:fetch(\1)}',
    ),
    # shell ignores packageBaseUrl when building a wheel path
    (
        r'(\w+)\.IN_SHELL\?(\w+)=(\w+)\((\w+)=>\4,"resolvePath"\)',
        r'\1.IN_SHELL?\2=\3((t,b)=>b&&!t.includes("://")&&!t.startsWith("/")?b+t:t,"resolvePath")',
    ),
)
ASM_IMPORT_META = ("import.meta.url", '"file:///pyodide/pyodide.asm.mjs"')

# --- JS: shims that make a bare JSContext look like an Emscripten "shell" ---
JS_PRELUDE = r"""
globalThis.__log = [];
globalThis.console = {};
for (const k of ['log', 'info', 'warn', 'error', 'debug', 'trace'])
    console[k] = (...a) => __log.push(a.map(x => x && x.stack || String(x)).join(' '));

globalThis.TextDecoder = class {
    constructor(label = 'utf-8') { this.encoding = label; }
    decode(b) {
        if (!b) return '';
        b = b instanceof Uint8Array ? b
          : ArrayBuffer.isView(b) ? new Uint8Array(b.buffer, b.byteOffset, b.byteLength)
          : new Uint8Array(b);
        let s = '', chunk = [], i = 0;
        while (i < b.length) {
            let c = b[i++];
            if (c > 0x7f) {
                let n = c >= 0xf0 ? 3 : c >= 0xe0 ? 2 : c >= 0xc0 ? 1 : 0;
                c &= 0x3f >> n;
                while (n-- > 0 && i < b.length) c = (c << 6) | (b[i++] & 0x3f);
            }
            chunk.push(c);
            if (chunk.length >= 8192) { s += String.fromCodePoint(...chunk); chunk = []; }
        }
        return s + String.fromCodePoint(...chunk);
    }
};
globalThis.TextEncoder = class {
    get encoding() { return 'utf-8'; }
    encode(s = '') {
        const out = [];
        for (const ch of s) {
            const c = ch.codePointAt(0);
            if (c < 0x80) out.push(c);
            else if (c < 0x800) out.push(0xc0 | c >> 6, 0x80 | c & 63);
            else if (c < 0x10000) out.push(0xe0 | c >> 12, 0x80 | c >> 6 & 63, 0x80 | c & 63);
            else out.push(0xf0 | c >> 18, 0x80 | c >> 12 & 63, 0x80 | c >> 6 & 63, 0x80 | c & 63);
        }
        return new Uint8Array(out);
    }
    encodeInto(s, dst) {
        const b = this.encode(s), n = Math.min(b.length, dst.length);
        dst.set(b.subarray(0, n));
        return { read: s.length, written: n };
    }
};

// NOT cryptographically secure: JSContext has no entropy source
globalThis.crypto = {
    getRandomValues(a) {
        const u = new Uint8Array(a.buffer, a.byteOffset, a.byteLength);
        for (let i = 0; i < u.length; i++) u[i] = Math.random() * 256 | 0;
        return a;
    }
};
globalThis.performance = { now: () => Date.now() };
// (a block: a top-level `const` can't be declared twice in one context, and this may run again in it)
{
const __B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
globalThis.btoa ??= s => {
    let out = '';
    for (let i = 0; i < s.length; i += 3) {
        const a = s.charCodeAt(i), b = s.charCodeAt(i + 1), c = s.charCodeAt(i + 2);
        const n = a << 16 | (b || 0) << 8 | (c || 0);
        out += __B64[n >> 18 & 63] + __B64[n >> 12 & 63]
             + (i + 1 < s.length ? __B64[n >> 6 & 63] : '=')
             + (i + 2 < s.length ? __B64[n & 63] : '=');
    }
    return out;
};
globalThis.atob ??= s => {
    s = s.replace(/[^A-Za-z0-9+/]/g, '');
    let out = '';
    for (let i = 0; i < s.length; i += 4) {
        const n = __B64.indexOf(s[i]) << 18 | __B64.indexOf(s[i + 1]) << 12
                | (__B64.indexOf(s[i + 2]) & 63) << 6 | (__B64.indexOf(s[i + 3]) & 63);
        out += String.fromCharCode(n >> 16 & 255);
        if (i + 2 < s.length) out += String.fromCharCode(n >> 8 & 255);
        if (i + 3 < s.length) out += String.fromCharCode(n & 255);
    }
    return out;
};
}
globalThis.queueMicrotask ??= fn => Promise.resolve().then(fn);

globalThis.__timers = new Map();
globalThis.__timerId = 0;
globalThis.setTimeout = (fn, ms = 0, ...args) => {
    const id = ++__timerId;
    __timers.set(id, { at: Date.now() + (ms | 0), fn, args });
    return id;
};
globalThis.clearTimeout = id => __timers.delete(id);
globalThis.__runTimers = () => {
    const now = Date.now();
    for (const [id, t] of [...__timers])
        if (t.at <= now) { __timers.delete(id); t.fn(...t.args); }
    return __timers.size;
};

// Emscripten/Pyodide "shell": read/load/readbuffer over in-memory files
globalThis.__files = {};
globalThis.readbuffer = p => {
    const f = __files[p];
    if (!f) throw new Error('readbuffer: no such file ' + p);
    return f.byteOffset === 0 && f.buffer.byteLength === f.byteLength ? f.buffer : f.slice().buffer;
};
globalThis.read = (p, mode) => mode === 'binary'
    ? new Uint8Array(readbuffer(p)) : new TextDecoder().decode(new Uint8Array(readbuffer(p)));
globalThis.load = p => (0, eval)(read(p));

// --- fetch/Request/Response/Headers/AbortController: requests are queued,
// Python performs them in the pump loop and answers via __fetchDone
globalThis.Headers = class {
    constructor(init) {
        this._m = new Map();
        if (init) for (const [k, v] of (init instanceof Headers || Array.isArray(init)
            ? [...init] : Object.entries(init))) this.set(k, v);
    }
    get(k) { const v = this._m.get(String(k).toLowerCase()); return v === undefined ? null : v; }
    set(k, v) { this._m.set(String(k).toLowerCase(), String(v)); }
    append(k, v) { const o = this.get(k); this.set(k, o === null ? v : o + ', ' + v); }
    has(k) { return this._m.has(String(k).toLowerCase()); }
    delete(k) { this._m.delete(String(k).toLowerCase()); }
    entries() { return this._m.entries(); }
    keys() { return this._m.keys(); }
    values() { return this._m.values(); }
    forEach(fn) { this._m.forEach((v, k) => fn(v, k, this)); }
    [Symbol.iterator]() { return this._m.entries(); }
};
globalThis.AbortSignal = class {
    constructor() { this.aborted = false; this.reason = undefined; this.onabort = null; this._l = []; }
    addEventListener(t, fn) { if (t === 'abort') this._l.push(fn); }
    removeEventListener(t, fn) { this._l = this._l.filter(f => f !== fn); }
    throwIfAborted() { if (this.aborted) throw this.reason; }
    _abort(r) {
        if (this.aborted) return;
        this.aborted = true;
        this.reason = r ?? new Error('AbortError');
        for (const f of [...this._l, this.onabort]) if (f) f({ type: 'abort', target: this });
    }
    static any(list) {
        const s = new AbortSignal();
        for (const x of list) {
            if (x.aborted) { s._abort(x.reason); break; }
            x.addEventListener('abort', () => s._abort(x.reason));
        }
        return s;
    }
    static abort(r) { const s = new AbortSignal(); s._abort(r); return s; }
    static timeout(ms) {
        const s = new AbortSignal();
        setTimeout(() => s._abort(new Error('TimeoutError')), ms);
        return s;
    }
};
globalThis.AbortController = class {
    constructor() { this.signal = new AbortSignal(); }
    abort(r) { this.signal._abort(r); }
};
globalThis.Request = class {
    constructor(input, init = {}) {
        const base = input instanceof Request ? input : {};
        this.url = String(input instanceof Request ? input.url : input);
        this.method = String(init.method || base.method || 'GET').toUpperCase();
        this.headers = new Headers(init.headers || base.headers);
        this.body = init.body ?? base.body ?? null;
        this.signal = init.signal || base.signal || new AbortSignal();
    }
    clone() { return new Request(this); }
};
globalThis.Response = class {
    constructor(body = null, init = {}) {
        this._body = body == null ? new Uint8Array(0)
            : typeof body === 'string' ? new TextEncoder().encode(body)
            : ArrayBuffer.isView(body) ? new Uint8Array(body.buffer, body.byteOffset, body.byteLength)
            : new Uint8Array(body);
        this.status = init.status ?? 200;
        this.statusText = init.statusText ?? '';
        this.headers = new Headers(init.headers);
        this.url = init.url ?? '';
        this.redirected = !!init.redirected;
        this.ok = this.status >= 200 && this.status < 300;
        this.type = 'basic';
        this.bodyUsed = false;
    }
    _take() {
        if (this.bodyUsed) return Promise.reject(new TypeError('Body has already been used'));
        this.bodyUsed = true;
        return Promise.resolve(this._body);
    }
    arrayBuffer() { return this._take().then(b => b.slice().buffer); }
    bytes() { return this._take().then(b => b.slice()); }
    text() { return this._take().then(b => new TextDecoder().decode(b)); }
    json() { return this.text().then(JSON.parse); }
    clone() { return new Response(this._body.slice(), this); }
};
globalThis.__fetchQueue = [];
globalThis.__fetchWait = new Map();
globalThis.__fetchId = 0;
globalThis.fetch = (input, init = {}) => {
    const req = new Request(input, init);
    if (req.signal.aborted) return Promise.reject(req.signal.reason);
    return new Promise((resolve, reject) => {
        const id = ++__fetchId;
        __fetchWait.set(id, { resolve, reject });
        req.signal.addEventListener('abort', () => { if (__fetchWait.delete(id)) reject(req.signal.reason); });
        const body = req.body == null ? null
            : typeof req.body === 'string' ? req.body : new TextDecoder().decode(req.body);
        __fetchQueue.push({ id, url: req.url, method: req.method,
                            headers: Object.fromEntries(req.headers.entries()), body });
    });
};
globalThis.__fetchTake = () => JSON.stringify(__fetchQueue.splice(0));
globalThis.__fetchDone = (id, meta) => {
    const m = JSON.parse(meta), w = __fetchWait.get(id);
    const body = m.body ? __files[m.body] : null;
    if (m.body) delete __files[m.body];
    if (!w) return;
    __fetchWait.delete(id);
    if (m.error) w.reject(new TypeError(m.error));
    else w.resolve(new Response(body, m));
};

// Synchronous wasm compilation: async compilation in JSContext depends on the run loop
WebAssembly.compile = async b => new WebAssembly.Module(b);
WebAssembly.instantiate = async (src, imports) => {
    if (src instanceof WebAssembly.Module) return new WebAssembly.Instance(src, imports);
    const module = new WebAssembly.Module(src);
    return { module, instance: new WebAssembly.Instance(module, imports) };
};
"""

JS_LOADER = """
globalThis.__out = [];
globalThis.__state = 'loading';
loadPyodide({{
    indexURL: {index_url},
    packageBaseUrl: {wheels_url},
    createPyodideModule: _createPyodideModule,
    {snapshot}
    stdout: line => __out.push(line),
    stderr: line => __out.push(line),
}}).then(
    p => {{ globalThis.pyodide = p; __state = 'ready'; }},
    e => {{ __state = 'error: ' + e + '\\n' + (e && e.stack || ''); }}
);
"""

JS_OPT_MAKE_SNAPSHOT = "_makeSnapshot: true,"
JS_OPT_LOAD_SNAPSHOT = "_loadSnapshot: __files[{path}],"
JS_MAKE_SNAPSHOT = "pyodide.makeMemorySnapshot()"
JS_FS_WRITE = "pyodide.FS.writeFile({dst}, __files[{src}]); delete __files[{src}]"
JS_FS_READ = "pyodide.FS.readFile({path})"
JS_FILE_LEN = "String((__files[{path}] || {{}}).length)"
JS_FREE_FILES = "globalThis.__files = {}"
JS_STATE = "__state"
JS_RUN = "pyodide.runPython({code})"
JS_AWAIT = (
    "globalThis.__async = 'pending'; ({expr}).then(() => __async = 'done', e => __async = 'error: ' + (e.message || e))"
)
JS_RUN_ASYNC = "pyodide.runPythonAsync({code})"
JS_LOAD_PACKAGES = "pyodide.loadPackage({names})"
JS_FETCH_TAKE = "__fetchTake()"
JS_FETCH_DONE = "__fetchDone({id}, {meta})"
FETCH_BODY_KEY = "__fetch/{id}"
JS_ASYNC_STATE = "__async"
JS_PUMP = "__runTimers()"
JS_DRAIN_OUT = '__out.splice(0).join("\\n")'
JS_DRAIN_LOG = '__log.splice(0).join("\\n")'

# --- Python ---
# REPL: each top-level statement runs separately in 'single' mode (echoes expression
# values); bare strings (e.g. a docstring at the top of a paste) are skipped
PY_REPL_INIT = """
import ast as _ast

def _pyjsc_repl(src):
    for s in _ast.parse(src, '<repl>').body:
        if (isinstance(s, _ast.Expr) and isinstance(s.value, _ast.Constant)
                and isinstance(s.value.value, str)):
            continue
        exec(compile(_ast.Interactive([s]), '<repl>', 'single'), globals())
"""
PY_REPL = "_pyjsc_repl({src})"
# fingerprint of installed distributions: changes only on install/uninstall
PY_SITE_STATE = (
    "print(sorted(e.name for e in __import__('os').scandir("
    "__import__('sysconfig').get_path('purelib')) if e.name.endswith('.dist-info')))"
)
PY_HAS_MODULE = "print(__import__('importlib.util').util.find_spec({name!r}) is not None)"
PY_SITE_SAVE = f"""
import os, sysconfig, zipfile
_sp = sysconfig.get_path('purelib')
with zipfile.ZipFile({SITE_TMP!r}, 'w') as _z:
    for _root, _, _files in os.walk(_sp):
        for _f in _files:
            _p = os.path.join(_root, _f)
            _z.write(_p, os.path.relpath(_p, _sp))
del _sp, _z, _root, _files, _f, _p
"""
PY_SITE_RESTORE = f"""
import importlib, os, sysconfig, zipfile
with zipfile.ZipFile({SITE_TMP!r}) as _z:
    _z.extractall(sysconfig.get_path('purelib'))
os.remove({SITE_TMP!r})
importlib.invalidate_caches()
del _z
"""
PY_PIP_INSTALL = """
import pyodide_js
await pyodide_js.loadPackage('micropip')
import micropip
await micropip.install({names})
"""

# --- Messages ---
ERR_JS = "[JS] {exc}"
PY_ERROR_PREFIX = "PythonError: "
ERR_LOAD = "Pyodide failed to load: {state}"
ERR_TIMEOUT = "Timed out waiting ({what})"
ERR_NO_FILE = "File not found: {path}"
ERR_NO_PACKAGE = "Package {name!r} is not in pyodide-lock.json"
ERR_PUT = "Failed to transfer {path} to JS"
ERR_PATCH = "Patch {pattern!r}: {n} matches instead of 1"
ERR_WITH_LOG = "{err}\n--- JS log ---\n{log}"
MSG_DOWNLOAD = "Downloading {url}"
MSG_STAGE = "  {stage}... {dt:.1f} s"
MSG_SNAPSHOT_BAD = "Snapshot rejected ({err}); deleting it and starting without one"
MSG_SAVED = "Saved {path} ({size} bytes)"
MSG_HTTP = "  HTTP {method} {url} -> {status}"
MSG_PUT = "    {path}: {size} bytes via {how}"


def cache_root():
    """Where downloads are kept: $WASMHOST_CACHE; else ~/.cache/wasmhost; else ./.cache where there is no usable home
    directory (PythonIDE), so relative to where the script is run."""
    if env := os.environ.get("WASMHOST_CACHE"):
        return env
    try:
        home = os.path.expanduser("~")
        if home != "~" and os.path.isdir(home) and os.access(home, os.W_OK):
            return os.path.join(home, ".cache", "wasmhost")
    except Exception:  # noqa: BLE001 -- a sandbox that won't even say
        pass
    return os.path.join(".", ".cache")


def fetch_package(cache_dir=None):
    cache_dir = cache_dir or os.path.join(cache_root(), "pyodide", PYODIDE_VERSION)
    paths = {name: os.path.join(cache_dir, name) for name in PKG_FILES}
    if all(os.path.exists(p) for p in paths.values()):
        return cache_dir
    print(MSG_DOWNLOAD.format(url=PKG_URL))
    os.makedirs(cache_dir, exist_ok=True)
    data = urllib.request.urlopen(PKG_URL).read()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for name, path in paths.items():
            with open(path, "wb") as f:
                member = tar.extractfile("package/" + name)
                if member is None:
                    raise RuntimeError(f"{name} is not in the package")
                f.write(member.read())
    return cache_dir


def patch_asm(src):
    for pattern, new in ASM_PATCHES:
        src, n = re.subn(pattern, new, src)
        if n != 1:
            raise RuntimeError(ERR_PATCH.format(pattern=pattern, n=n))
    return src.replace(*ASM_IMPORT_META)


def clean_traceback(text):
    """Keep only user code in a traceback: from the first <repl> frame, otherwise drop Pyodide frames."""
    lines = text.rstrip("\n").splitlines()
    first_repl = next((i for i, line in enumerate(lines) if line.startswith('  File "<repl>"')), None)
    out, skip = [], False
    for i, line in enumerate(lines):
        if line.startswith("  File "):
            skip = i < first_repl if first_repl is not None else "/_pyodide/" in line
        elif not (skip and line.startswith("    ")):
            skip = False
        if not skip:
            out.append(line)
    return "\n".join(out) + "\n"


class Pyodide:
    """Pyodide in a JavaScript engine of wasmhost. `run`, `run_async`, `repl`, `load_packages`, `pip_install`."""

    def __init__(self, index_dir=None, timeout=300.0, verbose=True, snapshot=True, backend=None):
        self.verbose = verbose
        self._index_dir = index_dir = index_dir or fetch_package()
        paths = {name: os.path.join(index_dir, name) for name in PKG_FILES}
        for path in paths.values():
            if not os.path.isfile(path):
                raise FileNotFoundError(ERR_NO_FILE.format(path=path))
        if backend is None:
            backend = wasmhost.default_backend(js_only=True)
        elif isinstance(backend, str):
            backend = wasmhost.get_backend(backend)
        if not isinstance(backend, wasmhost.JSBackend):
            raise TypeError(f"{backend.name} is not a JavaScript engine: Pyodide brings its own JavaScript")
        self.js = backend
        # A snapshot is only good for the engine that made it (another one rejects it), so one per backend.
        snap_path = os.path.join(index_dir, f"snapshot-{backend.name}.bin")
        use_snap = snapshot and os.path.isfile(snap_path)
        self._eval(JS_PRELUDE)
        try:
            with self._stage("files to JS"):
                for name in BINARY_FILES:
                    with open(paths[name], "rb") as f:
                        self._put_file(INDEX_URL + name, f.read())
                if use_snap:
                    with open(snap_path, "rb") as f:
                        self._put_file(INDEX_URL + SNAPSHOT_FILE, f.read())
            with self._stage("pyodide.asm.mjs"):
                with open(paths["pyodide.asm.mjs"], encoding="utf-8") as f:
                    self._eval(patch_asm(f.read()))
            with self._stage("pyodide.js"):
                with open(paths["pyodide.js"], encoding="utf-8") as f:
                    self._eval(f.read())
            opt = (
                JS_OPT_LOAD_SNAPSHOT.format(path=json.dumps(INDEX_URL + SNAPSHOT_FILE))
                if use_snap
                else JS_OPT_MAKE_SNAPSHOT
                if snapshot
                else ""
            )
            with self._stage("CPython startup" + (" from snapshot" if use_snap else "")):
                self._eval(
                    JS_LOADER.format(index_url=json.dumps(INDEX_URL), wheels_url=json.dumps(WHEELS_URL), snapshot=opt)
                )
                state = self._wait(JS_STATE, "loading", timeout, "load")
            if state != "ready":
                raise RuntimeError(ERR_LOAD.format(state=state))
        except RuntimeError as e:
            if use_snap:  # snapshot from another build or corrupted: restart without it
                print(MSG_SNAPSHOT_BAD.format(err=str(e).splitlines()[0]))
                os.remove(snap_path)
                return self.__init__(index_dir, timeout, verbose, snapshot, backend)
            raise RuntimeError(ERR_WITH_LOG.format(err=e, log=self.js_log())) from None

        if snapshot and not use_snap:
            with self._stage("memory snapshot"):
                self._save(snap_path, self._get_bytes(JS_MAKE_SNAPSHOT))
        self._eval(JS_FREE_FILES)

        site_path = os.path.join(index_dir, SITE_FILE)
        if os.path.isfile(site_path):
            with self._stage("restoring site-packages"):
                with open(site_path, "rb") as f:
                    self._put_file(INDEX_URL + SITE_FILE, f.read(), log=False)
                self._eval(JS_FS_WRITE.format(dst=json.dumps(SITE_TMP), src=json.dumps(INDEX_URL + SITE_FILE)))
                self.run(PY_SITE_RESTORE)
        self.run(PY_REPL_INIT)
        self._site_state = self.run(PY_SITE_STATE)

    # --- internals ---

    def _stage(self, name):
        runner = self

        class _Stage:
            def __enter__(self):
                self.t0 = time.time()

            def __exit__(self, *exc):
                if runner.verbose and exc[0] is None:
                    print(MSG_STAGE.format(stage=name, dt=time.time() - self.t0))

        return _Stage()

    def _eval(self, src):
        """Run JavaScript; its value as a string. A Python exception raised in Pyodide arrives cleaned up."""
        try:
            return self.js.evaluate(src)
        except RuntimeError as e:
            msg = str(e).removeprefix("[JS] ")
            if msg.startswith(PY_ERROR_PREFIX):
                raise RuntimeError(clean_traceback(msg[len(PY_ERROR_PREFIX) :])) from None
            raise RuntimeError(ERR_JS.format(exc=msg)) from None

    _str = _eval

    def _wait(self, expr, pending, timeout, what):
        deadline = time.time() + timeout
        while (state := self._str(expr)) == pending:
            if time.time() > deadline:
                raise TimeoutError(ERR_TIMEOUT.format(what=what))
            self._eval(JS_PUMP)
            self._service_fetch()
            time.sleep(0.005)
        return state

    def _save(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        if self.verbose:
            print(MSG_SAVED.format(path=path, size=len(data)))

    def _file_len(self, path):
        return self._str(JS_FILE_LEN.format(path=json.dumps(path)))

    def _put_file(self, path, data, log=True):
        """Put bytes into __files[path] as a Uint8Array."""
        how = self.js.put_bytes(f"__files[{json.dumps(path)}]", data)
        if self._file_len(path) != str(len(data)):
            raise RuntimeError(ERR_PUT.format(path=path))
        if self.verbose and log:
            print(MSG_PUT.format(path=path, size=len(data), how=how))

    def _get_bytes(self, expr):
        """Value of a JS expression (Uint8Array) as bytes."""
        return self.js.get_bytes(expr)

    def _http(self, r):
        """Perform one request from the JS queue; CDN wheels are cached in the cache dir."""
        url = r["url"]
        cache = (
            os.path.join(self._index_dir, url[len(WHEELS_URL) :])
            if url.startswith(WHEELS_URL) and r["method"] == "GET"
            else None
        )
        if cache and os.path.isfile(cache):
            with open(cache, "rb") as f:
                return {"status": 200, "url": url, "headers": {}}, f.read()
        body = r["body"].encode() if r["body"] is not None else None
        req = urllib.request.Request(url, data=body, headers=r["headers"], method=r["method"])
        try:
            resp = urllib.request.urlopen(req, timeout=HTTP_TIMEOUT)
        except urllib.error.HTTPError as e:
            resp = e
        with resp:
            data = resp.read()
            meta = {
                "status": resp.getcode(),
                "statusText": str(resp.reason or ""),
                "url": resp.geturl(),
                "redirected": resp.geturl() != url,
                "headers": dict(resp.headers.items()),
            }
        if self.verbose:
            print(MSG_HTTP.format(method=r["method"], url=url, status=meta["status"]))
        if cache and meta["status"] == 200:
            with open(cache, "wb") as f:
                f.write(data)
        return meta, data

    def _service_fetch(self):
        for r in json.loads(self._str(JS_FETCH_TAKE)):
            key = FETCH_BODY_KEY.format(id=r["id"])
            try:
                meta, data = self._http(r)
                self._put_file(key, data, log=False)
                meta["body"] = key
            except Exception as e:
                meta = {"error": f"{type(e).__name__}: {e}"}
            self._eval(JS_FETCH_DONE.format(id=r["id"], meta=json.dumps(json.dumps(meta))))

    # --- public API ---

    def output(self):
        return self._str(JS_DRAIN_OUT)

    def js_log(self):
        return self._str(JS_DRAIN_LOG)

    def run(self, code):
        """Run code synchronously and return stdout."""
        try:
            self._eval(JS_RUN.format(code=json.dumps(code)))
        finally:
            out = self.output()
        return out

    def _await(self, expr, timeout, what):
        self._eval(JS_AWAIT.format(expr=expr))
        state = self._wait(JS_ASYNC_STATE, "pending", timeout, what)
        out = self.output()
        if state != "done":
            raise RuntimeError((out + "\n" if out else "") + clean_traceback(state.removeprefix("error: ")))
        return out

    def run_async(self, code, timeout=60.0):
        """Run code with top-level await (asyncio) and return stdout."""
        return self._await(JS_RUN_ASYNC.format(code=json.dumps(code)), timeout, "run_async")

    def load_packages(self, *names, timeout=600.0):
        """pyodide.loadPackage: packages from pyodide-lock.json (CDN, cached locally)."""
        return self._await(JS_LOAD_PACKAGES.format(names=json.dumps(list(names))), timeout, "load_packages")

    def save_site(self, force=False):
        """Save site-packages to disk if the set of packages changed since the last save."""
        state = self.run(PY_SITE_STATE)
        if state == self._site_state and not force:
            return
        self._site_state = state
        self.run(PY_SITE_SAVE)
        data = self._get_bytes(JS_FS_READ.format(path=json.dumps(SITE_TMP)))
        self._save(os.path.join(self._index_dir, SITE_FILE), data)

    def ensure_packages(self, *names, timeout=600.0):
        """Load lock-file packages only if they are not already in site-packages."""
        missing = [n for n in names if self.run(PY_HAS_MODULE.format(name=n)) != "True"]
        return self.load_packages(*missing, timeout=timeout) if missing else ""

    def pip_install(self, *names, timeout=600.0):
        """micropip.install from PyPI (lock-file dependencies come from the CDN)."""
        return self.run_async(PY_PIP_INSTALL.format(names=repr(list(names))), timeout)

    def repl(self, source):
        """Run source as in an interactive REPL (echoes expression values)."""
        return self.run(PY_REPL.format(src=repr(source)))


DEMO = """
import sys, platform
print('version:', sys.version)
print('platform:', sys.platform, platform.machine())
# print('Hello,', sum(range(10)))
"""

DEMO_ASYNC = """
import asyncio
async def ticker():
    for i in range(3):
        print('tick', i)
        await asyncio.sleep(0.2)
await ticker()
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description="Pyodide in a JavaScript engine of wasmhost.")
    ap.add_argument("dir", nargs="?", help="a Pyodide package directory (default: the npm package, cached)")
    ap.add_argument(
        "--backend", choices=sorted(wasmhost.JS_BACKENDS), help="the JavaScript engine (default: the first)"
    )
    ap.add_argument("-c", dest="command", metavar="CODE", help="run this and exit (no REPL)")
    ap.add_argument("--no-snapshot", action="store_true", help="start CPython from scratch every time")
    args = ap.parse_args(argv)

    t0 = time.time()
    py = Pyodide(args.dir, backend=args.backend, snapshot=not args.no_snapshot)
    print(f"Pyodide ready in {time.time() - t0:.1f} s on {py.js.name}")

    print(py.run(DEMO))
    if args.command is not None:
        print(py.run_async(args.command) if "await " in args.command else py.repl(args.command))
        return 0
    print(py.ensure_packages("micropip"))
    py.run("import micropip")

    # REPL: an empty line at the first prompt exits
    while line := input("pyo>>> "):
        if line.rstrip().endswith(":"):
            lines = [line]
            while more := input("pyo... "):
                lines.append(more)
            line = "\n".join(lines) + "\n"
        try:
            out = py.run_async(line) if "await " in line else py.repl(line)
            if out:
                print(out)
        except RuntimeError as e:
            print(e)
        py.save_site()  # writes to disk only if the set of packages changed
    return 0


if __name__ == "__main__":
    sys.exit(main())
