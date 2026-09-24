# wasmhost

WebAssembly from **CPython, PyPy and Pythonista**, with the JavaScript WebAssembly API: modules, instances, memory,
globals, and host functions (Python callables the module calls). It runs on whichever backend is available:
JavaScriptCore's `JSContext` in Pythonista on iOS, JavaScriptCore or Node on a computer, or, when installed,
wasmtime or wasm3. Plain Python, no dependencies, no C extension of its own.

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

A module that imports functions gets them from the import object, a dict of dicts of Python callables, as in the
JavaScript API:

```python
def log(x):  # env.log(i32), which the module calls
    print("the module says", x)


instance = wasmhost.Instance(module, {"env": {"log": log}})
instance.exports.run()  # runs the module, which calls log
```

See [Host functions](#host-functions) below, `examples/basic.py` and `examples/imports.py`, and for something bigger,
both in a bare JavaScript engine:

- `examples/pyodide.py`: a Python REPL that runs in [Pyodide](https://pyodide.org) (CPython built to WebAssembly),
  with a memory snapshot per backend, `fetch` served by Python and packages kept between sessions. It started as a
  [gist](https://gist.github.com/o-murphy/dd898e490094eaddab0875187e27f11a) for a Pythonista `JSContext`.
- `examples/jslinux.py`: a Linux virtual machine (JSLinux, riscv64 or x86_64 Alpine), with its console on your
  terminal.

Both keep their downloads in `$WASMHOST_CACHE`, else `~/.cache/wasmhost`, else `./.cache` where there is no usable
home directory (PythonIDE).

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

## Host functions

`Instance(module, imports)` takes the import object for the module's function imports (`Module.imports(module)`
lists them with their types). A callable gets its arguments as `int` (`i32`, `i64`) and `float` (`f32`, `f64`) and
returns what the signature says: `None`, one value, or a tuple for several results. What it returns is checked (an
`int` for an integer result, a number for a float one) and a wrong answer is a `TypeError`.

- **An exception** raised in a host function comes out of the call into the module that led to it, as the same
  exception, and the instance is still usable. That holds on every backend.
- **A host function can call the module again** (`instance.exports.f(...)` from inside it), and its own errors come
  out of the outer call.
- **Errors in the import object** are the API's: a missing module is a `TypeError`, a missing or non-callable
  function a `LinkError`. Importing a memory, a table or a global is not supported yet (`NotImplementedError`), and
  a backend that can't take host functions says so (`backend.supports("imports")`).

How a host function is called depends on the backend: `wasmtime` and `wasm3` call the Python function themselves; the
JavaScript engines that are JavaScriptCore (`jsc`, and `jscontext` on iOS) do it through its C API, which makes a
JavaScript function that calls Python; Node writes a request on its pipe and waits for the answer, reading its
stdin synchronously. Only `gi-jsc` can't: PyGObject has no way to make a JavaScript function that calls Python.

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

## Bytes in and out of a JavaScript engine

A program with JavaScript of its own (Pyodide, an emulator) needs to hand the engine files and read results back.
`JSBackend.put_bytes(target, data)` assigns a `Uint8Array` to a JavaScript expression (`__files["a"]`), and
`JSBackend.get_bytes(expr)` returns the bytes of one. On the JavaScriptCore backends (`jsc`, and `jscontext` under
`objc_util`) through its C API (`ctypes`), which fills the array in place; through hex on the others and as the
fallback if the C API ever fails. The self-test reports which was used (`N bytes via C API` or `via hex`).

## Backends

| Backend | Where | How it is detected |
|---|---|---|
| `jscontext` | iOS (Pythonista, PythonIDE) | JavaScriptCore's `JSContext` through `objc_util` (both apps have it), or through [`rubicon-objc`](https://github.com/beeware/rubicon-objc) where that is missing (checked only against a fake bridge, not on a device) |
| `wasmtime` | anywhere with the `wasmtime` package | `import wasmtime` (`pip install wasmtime`) |
| `wasm3` | CPython 3.11+ with [pywasm3](https://github.com/wasm3/pywasm3) | `import wasm3`; install it from git: `uv add "pywasm3 @ git+https://github.com/wasm3/pywasm3"` (its PyPI release predates the API used here) |
| `jsc` | Linux, macOS | JavaScriptCore's C API through `ctypes`, no PyGObject: `apt install libjavascriptcoregtk-4.1-0` (macOS uses the system framework) |
| `gi-jsc` | Linux | the same engine through PyGObject (`apt install gir1.2-javascriptcoregtk-4.1 python3-gi`) |
| `node` | anywhere with Node.js | `node` on `PATH` |

With nothing configured, the first backend that starts wins, in the order shown. Each backend's constructor is its
own probe: it fails when its runtime is missing. Choose one with `WASMHOST_BACKEND=<name>`,
`wasmhost.set_backend("<name>")` or `Module(..., backend="<name>")`; `wasmhost.get_backend().name` says which is in
use. `wasmhost.close()` closes the backends it started. (In WebAssembly's words the *host* is the embedder, the
Python side that provides imports; what runs the module is the backend.)

Not every backend can do everything; `backend.supports(...)` says:

| | `memory.grow` from Python | `table.length` | host functions (`imports`) |
|---|---|---|---|
| `jscontext` | yes | yes | yes, through the C API under `objc_util` (not under `rubicon-objc`) |
| `wasmtime` | yes | yes | yes |
| `wasm3` | no (`NotImplementedError`; a module's own `memory.grow` works) | no | yes |
| `jsc` | yes | yes | yes |
| `gi-jsc` | yes | yes | no |
| `node` | yes | yes | yes |

### Where it has been run

| Where | Backend | Result | A call / a batch of 3 |
|---|---|---|---|
| Pythonista 3 (StaSh 0.7.5), Python 3.10.4, iPhone17,3 | `jscontext` (`objc_util`) | 23/23 (before host functions and the C API bytes path were added) | 53 / 97 us |
| PythonIDE, Python 3.14.7, `ios-13.0-arm64-iphoneos` | `jscontext` (`objc_util`) | 23/23 (before host functions and the C API bytes path were added) | 37 / 76 us |
| Linux, CPython 3.14t | `jsc` | 25/25 | 32 / 102 us |
| Linux, CPython 3.14t | `gi-jsc` | 25/25 (host functions: not available, as documented) | 35 / 62 us |
| Linux, CPython 3.14t | `node` | 25/25 | 82 / 340 us |
| Linux, CPython 3.14t | `wasmtime` | 19/19 | 66 / 212 us |
| Linux, CPython 3.14t | `wasm3` | 19/19 | 3 / 63 us |
| Linux, CPython 3.10 and PyPy 3.10 | `node` | 25/25 (and the test suite on 3.10) | |

The times are one run of the self-test each, so read them as an order of magnitude. A host function costs about
what a call does, plus a round trip on `node` (measured once: about 4 us on `wasm3`, 50 us on `wasmtime` and `jsc`,
200 us on `node`, per host call including the export around it).

Not run on a device since they were added: the C API bytes path and host functions on `jscontext` (on Linux they
run against a fake `objc_util` whose `c` is the real JavaScriptCore library, so the calls are the ones the iOS path
makes, but it is not the device), and the `rubicon-objc` bridge (both iOS apps above have `objc_util`, so it wasn't
needed). Node's synchronous wait for a host function's answer has not been run on Windows either.

### A note on wasmtime and `faulthandler`

wasmtime installs process-wide signal handlers when its first engine is created, and uses them to catch a trap.
Python's `faulthandler` (on with `python -X faulthandler`, and in pytest) replaces the handlers when it is enabled
*after* that, and the first trap then ends the process (`Fatal Python error: Illegal instruction`). Enable it first
(or not at all), or start the backend later: this repo's `tests/conftest.py` does that.

## Not yet

- **Importing a memory, a table or a global** (an Emscripten build imports its memory): `NotImplementedError`.
  Host functions are there; see above.
- **Tables** beyond their length, `v128` and reference types, multi-value results in a batch.

## Test

```bash
uv run pytest                            # every backend that starts here
uv run pytest --wasm-backend node        # one backend: it must start, or the run stops with an error
uv run pytest --wasm-backend wasmtime    # or wasm3, or jsc (needs the JavaScriptCore library)
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
