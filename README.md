# wasmhost

WebAssembly from **CPython, PyPy and Pythonista**, with the JavaScript WebAssembly API. It runs on whichever
backend is available: JavaScriptCore's `JSContext` in Pythonista on iOS, WebKitGTK's JavaScriptCore on Linux, Node,
or, when installed, wasmtime or wasm3. Plain Python, no dependencies, no C extension of its own.

[![license]][MIT]
[![pypi]][PyPiUrl]
[![py-versions]][sources]
[![Made in Ukraine]][SWUBadge]

[![powered by webassembly]][WebAssembly]

[![Tests](https://github.com/ballistics-lab/py-wasmhost/actions/workflows/tests.yml/badge.svg)](https://github.com/ballistics-lab/py-wasmhost/actions/workflows/tests.yml)
[![Pre-commit](https://github.com/ballistics-lab/py-wasmhost/actions/workflows/pre-commit.yml/badge.svg)](https://github.com/ballistics-lab/py-wasmhost/actions/workflows/pre-commit.yml)

```python
import wasmhost

module = wasmhost.Module(open("lib.wasm", "rb").read())  # WebAssembly.Module
instance = wasmhost.Instance(module)  # WebAssembly.Instance
print(instance.exports.add(2, 3))  # i32/i64 -> int, f32/f64 -> float
instance.exports.memory.write(ptr, b"data")  # WebAssembly.Memory
print(instance.exports.counter.value)  # WebAssembly.Global
```

See `examples/basic.py`.

- **Types.** The JavaScript API can't tell a function's signature, and it matters (an `i64` argument must reach
  JavaScript as a BigInt), so the binary's type, import, function, global and export sections are read in
  Python. `Module.exports(module)` and `Module.imports(module)` describe a module with them.
- **Errors** are the API's: `CompileError`, `LinkError` and `Trap` (`WebAssembly.RuntimeError`, which is also a
  `RuntimeError`); an out-of-bounds memory access is an `IndexError`.
- **Memory** is copied, not shared: `memory.read(offset, n)`, `memory.write(offset, data)`, `memory[a:b]`,
  `memory.grow(pages)`.

## Installation

### uv

```shell
uv add wasmhost

# With wasmtime, the in-process JIT (otherwise Node or WebKitGTK JavaScriptCore is used)
uv add wasmhost[wasmtime]
```

### pip

```shell
pip install wasmhost

# With wasmtime, the in-process JIT (otherwise Node or WebKitGTK JavaScriptCore is used)
pip install wasmhost[wasmtime]
```

The `wasm3` backend has no extra: pywasm3's PyPI release is years behind the API used here, so install it from git
(CPython 3.11+, needs a C compiler): `pip install "pywasm3 @ git+https://github.com/wasm3/pywasm3"`.

### Pythonista and PythonIDE (iOS)

The ordinary wheel: it is pure Python (`py3-none-any`). In StaSh (Pythonista) or PythonIDE's pip,
`pip install wasmhost`, then run the self-test (see [Try it on a device](#try-it-on-a-device)).

## Batches

On a JavaScript engine every call into it has a fixed cost (a pipe to `node`, a bridged Objective-C call in
Pythonista). A batch does several steps in one trip (on `wasmtime` and `wasm3`, in Python), and a step can use the results of the earlier ones:

```python
batch = instance.batch()
ptr = batch.call(instance.exports.alloc, len(data))  # a Ref
batch.write(instance.exports.memory, ptr, data)
status = batch.call(instance.exports.run, ptr)
batch.stop_if_nonzero(status)  # leave the rest out on an error status
out = batch.read(instance.exports.memory, ptr, 16)
batch.run()
out.value  # bytes (`.done` says whether the step ran)
```

A failing step (a trap, an out-of-bounds access) raises from `run()`, after the earlier steps' results are set.
Only `i32` results can be used in arithmetic (`ptr * 8`, `ptr + 4`).

## Backends

| Backend | Where | How it is detected |
|---|---|---|
| `jscontext` | iOS (Pythonista, PythonIDE) | JavaScriptCore's `JSContext` through `objc_util` (both apps have it), or through [`rubicon-objc`](https://github.com/beeware/rubicon-objc) where that is missing (checked only against a fake bridge, not on a device) |
| `wasmtime` | anywhere with the `wasmtime` package | `import wasmtime` (`pip install wasmtime`) |
| `wasm3` | CPython 3.11+ with [pywasm3](https://github.com/wasm3/pywasm3) | `import wasm3`; install it from git: `uv add "pywasm3 @ git+https://github.com/wasm3/pywasm3"` (its PyPI release predates the API used here) |
| `gi-jsc` | Linux | WebKitGTK's JavaScriptCore through PyGObject (`apt install gir1.2-javascriptcoregtk-4.1 python3-gi`) |
| `node` | anywhere with Node.js | `node` on `PATH` |

With nothing configured, the first backend that starts wins, in the order shown. Each backend's constructor is its
own probe: it fails when its runtime is missing. Choose one with `WASMHOST_BACKEND=<name>`,
`wasmhost.set_backend("<name>")` or `Module(..., backend="<name>")`; `wasmhost.get_backend().name` says which is in
use. `wasmhost.close()` closes the backends it started. (In WebAssembly's words the *host* is the embedder, the
Python side that provides imports; what runs the module is the backend.)

Not every backend can do everything (`backend.supports("memory.grow")` and `supports("table.length")` say):
`wasm3` can't `Memory.grow` from Python (`NotImplementedError`; a module's own `memory.grow` works) and has no
tables API.

## Try it on a device

The package carries a self-test, since nothing else can be run in Pythonista to see whether this works there:

```python
import wasmhost

wasmhost.selftest()  # or, from a shell: python -m wasmhost [--backend NAME] [--all]
```

It prints one line per check (the Objective-C bridge in use, `WebAssembly` and `BigInt` in the engine, calls,
`i64`, memory, globals, traps, batches, the cost of a call), then `N/M passed`. If something fails, send the whole
output. On a computer, `python -m wasmhost --all` runs it on every backend that starts.

### Where it has been run

| Where | Backend | Result | A call / a batch of 3 |
|---|---|---|---|
| Pythonista 3 (StaSh 0.7.5), Python 3.10.4, iPhone17,3 | `jscontext` (`objc_util`) | 23/23 | 53 / 97 us |
| PythonIDE, Python 3.14.7, `ios-13.0-arm64-iphoneos` | `jscontext` (`objc_util`) | 23/23 | 37 / 76 us |
| Linux, CPython 3.14t | `gi-jsc` | 23/23 | 34 / 78 us |
| Linux, CPython 3.14t | `node` | 23/23 | 82 / 116 us |
| Linux, CPython 3.14t | `wasmtime` | 18/18 | 69 / 233 us |
| Linux, CPython 3.14t | `wasm3` | 18/18 | 4 / 46 us |
| Linux, CPython 3.10 and PyPy 3.10 | `node` | 23/23 (and the test suite on 3.10) | |

The times are one run of the self-test each, so read them as an order of magnitude. Not run on a device: the
`rubicon-objc` bridge (both iOS apps above have `objc_util`, so it wasn't needed), and imports (see below).

### A note on wasmtime and `faulthandler`

wasmtime installs process-wide signal handlers when its first engine is created, and uses them to catch a trap.
Python's `faulthandler` (on with `python -X faulthandler`, and in pytest) replaces the handlers when it is enabled
*after* that, and the first trap then ends the process (`Fatal Python error: Illegal instruction`). Enable it first
(or not at all), or start the backend later: this repo's `tests/conftest.py` does that.

## Not yet

- **Imports.** A module that imports host functions, memories, tables or globals can't be instantiated
  (`NotImplementedError`). Python callbacks need a synchronous bridge: native for `wasmtime` and `wasm3`, a JavaScript function
  made from Python for `gi-jsc`, an Objective-C block for `jscontext`, and for `node` a blocking read of the pipe.
- **Tables** beyond their length, `v128` and reference types, multi-value results in a batch.

## Test

```bash
uv run pytest                            # every backend that starts here
uv run pytest --wasm-backend node        # one backend: it must start, or the run stops with an error
uv run pytest --wasm-backend wasmtime    # or wasm3
uv run pytest --wasm-backend gi-jsc      # needs PyGObject: run it with a system-site-packages venv (see the CI job)
uv run pyright && uv run ruff check
```

[sources]: https://github.com/ballistics-lab/py-wasmhost
[license]: https://img.shields.io/github/license/ballistics-lab/py-wasmhost?style=flat-square
[MIT]: https://opensource.org/licenses/MIT
[pypi]: https://img.shields.io/pypi/v/wasmhost?style=flat-square&logo=pypi
[PyPiUrl]: https://pypi.org/project/wasmhost/
[py-versions]: https://img.shields.io/pypi/pyversions/wasmhost?style=flat-square
[Made in Ukraine]: https://img.shields.io/badge/made_in-Ukraine-ffd700.svg?labelColor=0057b7&style=flat-square
[SWUBadge]: https://stand-with-ukraine.pp.ua
[WebAssembly]: https://webassembly.org
[powered by webassembly]: https://img.shields.io/badge/webassembly-%23654FF0?style=flat-square&logo=webassembly&logoColor=white&label=powered%20by
