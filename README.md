# wasmhost

WebAssembly from **CPython, PyPy and Pythonista**, with the JavaScript WebAssembly API: modules, instances, memory,
globals, and host functions (Python callables the module calls). It runs on whichever backend is available:
JavaScriptCore's `JSContext` in Pythonista on iOS, JavaScriptCore, Node or Bun on a computer, or, when installed,
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

`examples/wasmclang.py` compiles C and C++ to WebAssembly with clang and lld that are themselves WebAssembly (the wasm-clang
project), with Python as their WASI host, and runs the result, with no compiler installed and no process started, so
it works in Pythonista.

`examples/coreutils.py` is a small shell over uutils coreutils (Rust, built to WASI, in `examples/wasm/`): `ls`, `cat`,
`sort`, `cp`, `seq`, `wc` and the rest, and `lua`, with pipes and redirects, over a directory of the real file system.
The WASI host for the modules is `wasmhost.wasi1` (see [WASI](#wasi)); the example only gives it a directory and a limit on what a
pipe may carry. `wasmclang.py` and `wasi_sh.py` keep hosts of their own: the first answers the WASI calls with a file system that is
itself a WebAssembly module, the second has a file system in a Python dict, pipes and hooks of its own, and `wasi1` works over real folders.

`examples/wasi_sh.py` is a real POSIX shell, BusyBox `ash` with about fifty utilities (the wasi-sh project's
`busybox.wasm`, downloaded once from npm), with pipes, `$(...)`, here-documents and functions, over a file system that is
a Python dict: nothing real is touched. It is made for Pythonista (`input()` at the prompt, no terminal needed).

`examples/coremark.py` runs CoreMark (the wasm3 project's build, in `examples/wasm/`) on every backend that starts here,
to compare their speed. A runtime that finishes the last pass in under 10 s is not scored by CoreMark itself, so it
prints the pass time too.

- **Types.** The JavaScript API can't tell a function's signature, and it matters (an `i64` argument must reach
  JavaScript as a BigInt), so the binary's type, import, function, global and export sections are read in
  Python. `Module.exports(module)` and `Module.imports(module)` describe a module with them, and
  `Module.customSections(module, name)` gives the contents of its custom sections (a list of `bytes`).
- **Errors** are the API's: `CompileError`, `LinkError` and `Trap` (`WebAssembly.RuntimeError`, which is also a
  `RuntimeError`); an out-of-bounds memory access is an `IndexError`.
- **Memory** is copied, not shared: `memory.read(offset, n)`, `memory.write(offset, data)`, `memory[a:b]`,
  `memory.grow(pages)`.

## Installation

### uv

```shell
uv add wasmhost

# With wasmtime, the in-process JIT (otherwise Node, Bun or WebKitGTK JavaScriptCore is used)
uv add wasmhost[wasmtime]
```

### pip

```shell
pip install wasmhost

# With wasmtime, the in-process JIT (otherwise Node, Bun or WebKitGTK JavaScriptCore is used)
pip install wasmhost[wasmtime]
```

The `wasm3` backend has no extra: pywasm3's PyPI release is years behind the API used here, so install it from git
(CPython 3.11+, needs a C compiler): `pip install "pywasm3 @ git+https://github.com/wasm3/pywasm3"`.

### Pythonista and PythonIDE (iOS)

The ordinary wheel: it is pure Python (`py3-none-any`). In StaSh (Pythonista) or PythonIDE's pip,
`pip install wasmhost`, then run the self-test (see [Try it on a device](#try-it-on-a-device)).

### iSH and iSH-AOK (iOS)

iSH is a Linux shell for iOS (an emulated Linux with ordinary CPython); iSH-AOK is a fork of it. Neither has `objc_util`,
JavaScriptCore or Node, so without an engine the self-test ends with `no backend could start` and says what to install.

- **iSH-AOK** (`Linux 5.10.0-ish_aok`, `aarch64`): `wasmtime` installs there and works, and so does `wasm3` (`WASMHOST_BACKEND=wasm3`),
  both 34/34.

  ```shell
  uv tool install wasmhost --prerelease=allow --with wasmtime
  wasmhost self test
  ```

  (`--prerelease=allow`: the current version, 0.1.0b2, is a beta.)
- **The original iSH** (`Linux 4.20.69-ish`, `i686`): the owner ran it with `wasm3` only. It passes 31 of 34
  steps; the three that fail all come from one thing, see "On the original iSH, `wasm3` starts a memory at its maximum" in
  [Backends](#backends).

Both were run by the owner, not by the assistant: see [Where it has been run](#where-it-has-been-run).

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
  function a `LinkError`. A global, a memory and a table go into the same import object (see below). A backend that
  can't take host functions says so (`backend.supports("imports")`), and one that can't take the others says so
  with `import.global`, `import.memory` and `import.table`.

How a host function is called depends on the backend: `wasmtime` and `wasm3` call the Python function themselves; the
JavaScript engines that are JavaScriptCore (`jsc`, and `jscontext` on iOS) do it through its C API, which makes a
JavaScript function that calls Python; Node and Bun write a request on their pipe and waits for the answer, reading its
stdin synchronously. Only `gi-jsc` can't: PyGObject has no way to make a JavaScript function that calls Python.

## Globals

`Global(type, value, mutable=False)` makes a `WebAssembly.Global` on its own (or, as in JavaScript,
`Global({"value": "i32", "mutable": True}, 7)`), and an instance can import it, one instance or several:

```python
counter = wasmhost.Global("i32", 0, mutable=True)
a = wasmhost.Instance(module, {"env": {"counter": counter}})
b = wasmhost.Instance(module, {"env": {"counter": counter}})  # the same global: what one writes, the other reads
counter.value = 10  # and so does the host
```

An exported global (`instance.exports.g`) can be passed to another instance the same way, and for an immutable one a
plain number is enough (`{"env": {"limit": 100}}`), as in the JavaScript API. A wrong import (a number for a mutable
global, another type, another backend) is a `LinkError`; an immutable global's `value` can't be written (`TypeError`).
`backend.supports("import.global")` says whether a backend can (`wasm3` can't: pywasm3 makes no global outside a
module). A global has to be as mutable as the module says (`LinkError` otherwise), as in JavaScript.

## Memories and tables

`Memory(initial, maximum=None)` (in 64 KiB pages) and `Table("funcref", initial, maximum=None)` make a
`WebAssembly.Memory` and `WebAssembly.Table` on their own, to import into one instance or several (an Emscripten build
imports its memory). As in JavaScript they also take a descriptor, `Memory({"initial": 1, "maximum": 16})` and
`Table({"element": "anyfunc", "initial": 2})`:

```python
mem = wasmhost.Memory(1, 16)
a = wasmhost.Instance(module, {"env": {"memory": mem}})
mem.write(0, b"shared with every instance that imports it")

table = wasmhost.Table("funcref", 2)
user = wasmhost.Instance(module, {"env": {"table": table}})
table.set(0, provider.exports.add)  # a Function (or None to empty the entry)
table.get(0) is provider.exports.add  # True: one function is one object, as in JavaScript
table.get(0).type()  # FuncType((i32, i32), (i32,)); wasmtime finds it, a JavaScript engine does not tell:
# there a function an `elem` segment put in the table is known, any other: a `ValueError` and `table.get(0).signature = FuncType((i32, i32), (i32,))` (a call looks for it, too);
# a wrong signature given by hand is a `TypeError` when called (the engine checks the type), never a wrong result
table.grow(2)  # the length before
wasmhost.Table(
    "funcref", 2, 4, provider.exports.add
)  # every entry starts as `add`; table.grow(1, f) adds entries of `f`
```

An import that is not the right object, is too small for the module or comes from another backend is a `LinkError`.
Only `funcref` tables are supported (no `externref`). `wasm3` has neither (pywasm3 imports functions only:
`NotImplementedError`).

Like the JavaScript API, all the instances of a backend live in one store, so nothing stops two of them from sharing
what the host made. **`isolated=True` is not in the JavaScript API**: `Instance(module, isolated=True)` gives an
instance a store of its own, which goes away with it. It matters on `wasmtime`, which never frees an instance's memory
inside a store (only when the store is dropped): the default is one store per backend, freed by `wasmhost.close()`,
so many instances add up (300 instances of 1 MiB were 315 MB, not given back by `store.gc()`), while an isolated one
is freed when the instance is. Its price: it shares nothing (a Global made outside it is a `ValueError`).
`backend.supports("isolated")` is true for `wasmtime` and `wasm3` (a `wasm3` instance is isolated anyway); on the
JavaScript engines, which have one store, it is a `NotImplementedError`.

## Types

`type()` is the type reflection of the JavaScript API, with its names, on a function, a memory, a table and a global
(on every backend: the types are read from the module's binary, not asked of the engine, which may not have them):

```python
instance.exports.add.type()  # FuncType(parameters=('i32', 'i32'), results=('i32',))
instance.exports.memory.type()  # MemoryType(minimum=1, maximum=4, shared=False)
instance.exports.table.type()  # TableType(element='funcref', minimum=2, maximum=None)
instance.exports.counter.type()  # GlobalType(value='i32', mutable=True)
```

`Module.imports()` and `Module.exports()` give the same types in the `type` of each entry, so what a module asks
for (the limits of an imported memory or table, whether a global is mutable) can be read before it is instantiated.
What `type()` gives goes back into the constructor: `Memory(memory.type())`.

Value types are `wasmhost.i32`, `i64`, `f32` and `f64`. They are `str`s (`i32 == "i32"`), so a name does wherever one
of them goes: `FuncType((i32, i32), (i32,))` and `FuncType(("i32", "i32"), ("i32",))` are the same. An `i64` is an
`int` here, as every integer type is; it is what JavaScript takes as a `BigInt`.

`minimum` is the size now, as the specification has it (a table's `minimum` in JavaScriptCore is too; its memory's
stays the initial size). A shared memory is refused (`NotImplementedError`): there are no threads here.

## Async and threads

`await wasmhost.compile(bytes)` and `await wasmhost.instantiate(bytes | module, imports)` are the JavaScript API's promises
(`instantiate` of bytes gives `Instantiated(module, instance)`, of a `Module` just the `Instance`). The synchronous
`Module(...)`, `Instance(...)` and `instantiate_sync(...)` stay. By default the work is done in place with a turn of the
event loop before and after it, and uses **no threads**, so it works where Python's threads do not. `threaded=True` does it in a
worker thread on `wasmtime`, `node` and `bun`, which keeps the loop free during a long compile (the JavaScriptCore backends
and `wasm3` are tied to their thread and always run in place).

A backend takes one call at a time: every call into it holds a lock of the backend's own (reentrant, so a host function
may call back into the same backend), and two backends never wait for each other. Several tasks or threads may use one
backend, one after the other; to run two engines side by side make two backends (`wasmhost.NodeBackend()` twice), the
default one is shared. A host function must not wait for another thread that wants the same backend.

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

A function with several results gives a tuple of `Ref`s (`low, high = batch.call(split, x)`), each usable by later steps.
A failing step (a trap, an out-of-bounds access) raises from `run()`, after the earlier steps' results are set.
Only `i32` results can be used in arithmetic (`ptr * 8`, `ptr + 4`).

## Bytes in and out of a JavaScript engine

A program with JavaScript of its own (Pyodide, an emulator) needs to hand the engine files and read results back.
`JSBackend.put_bytes(target, data)` assigns a `Uint8Array` to a JavaScript expression (`__files["a"]`), and
`JSBackend.get_bytes(expr)` returns the bytes of one. On the JavaScriptCore backends (`jsc`, and `jscontext` under
`objc_util`) through its C API (`ctypes`), which fills the array in place; through hex on the others and as the
fallback if the C API ever fails. The self-test reports which was used (`N bytes via C API` or `via hex`).

## Backends

| Backend     | Where                                                          | How it is detected                                                                                                                                                                                                                                                  |
| ----------- | -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `wasmtime`  | anywhere with the `wasmtime` package                           | `import wasmtime` (`pip install wasmtime`) |
| `wasm3`     | CPython 3.11+ with [pywasm3](https://github.com/wasm3/pywasm3) | `import wasm3`; install it from git: `uv add "pywasm3 @ git+https://github.com/wasm3/pywasm3"` (its PyPI release predates the API used here) |
| `jscontext` | iOS (Pythonista, PythonIDE), and a Mac with rubicon-objc       | Apple's `JSContext` through an Objective-C bridge: Pythonista's `objc_util` (both iOS apps have it), or [`rubicon-objc`](https://github.com/beeware/rubicon-objc) (`pip install rubicon-objc`; tested in CI on macOS, not on a device). `backend.bridge` says which |
| `jsc`       | Linux, macOS                                                   | JavaScriptCore's C API through `ctypes`, no PyGObject: `apt install libjavascriptcoregtk-4.1-0` (macOS uses the system framework) |
| `gi-jsc`    | Linux                                                          | the same engine through PyGObject (`apt install gir1.2-javascriptcoregtk-4.1 python3-gi`, or `pip install wasmhost[gi-jsc]`: see below) |
| `node`      | anywhere with Node.js                                          | `node` on `PATH` |
| `bun`       | anywhere with [Bun](https://bun.sh)                            | `bun` on `PATH`. It is JavaScriptCore (as in Safari and on iOS) in a runtime of its own, and runs the very script `node` does |

With nothing configured, the first backend that starts wins, in the order shown: the native runtimes when they are installed, then the JavaScript engines. (On Pythonista nothing above `jscontext` can be installed, so it is the pick there; on a Mac that has rubicon-objc, `wasmtime` still comes first.) Each backend's constructor is its
own probe: it fails when its runtime is missing. Choose one with `WASMHOST_BACKEND=<name>`,
`wasmhost.set_backend("<name>")` or `Module(..., backend="<name>")`; `wasmhost.get_backend().name` says which is in
use. `wasmhost.close()` closes the backends it started. (In WebAssembly's words the *host* is the embedder, the
Python side that provides imports; what runs the module is the backend.)

Not every backend can do everything; `backend.supports(...)` says:

|             | `memory.grow` from Python                                      | `table.length` | host functions (`imports`)                                | Global, Memory, Table on their own (`import.*`) | table get/set/grow (`table.funcs`) | `isolated`   | `timeout`                          | `fuel` |
| ----------- | -------------------------------------------------------------- | -------------- | --------------------------------------------------------- | ----------------------------------------------- | ---------------------------------- | ------------ | ---------------------------------- | ----- |
| `wasmtime`  | yes                                                            | yes            | yes                                                       | yes                                             | yes                                | yes          | yes                                | yes |
| `wasm3`     | no (`NotImplementedError`; a module's own `memory.grow` works) | no             | yes                                                       | no                                              | no                                 | yes (always) | yes (an instance that timed out is finished) | yes (a trap, the instance goes on) |
| `jscontext` | yes                                                            | yes            | yes, through JavaScriptCore's C API (under either bridge) | yes                                             | yes                                | no           | no                                 | no |
| `jsc`       | yes                                                            | yes            | yes                                                       | yes                                             | yes                                | no           | no                                 | no |
| `gi-jsc`    | yes                                                            | yes            | no                                                        | yes                                             | yes                                | no           | no                                 | no |
| `node`      | yes                                                            | yes            | yes                                                       | yes                                             | yes                                | no           | yes                                | no |
| `bun`       | yes                                                            | yes            | yes                                                       | yes                                             | yes                                | no           | no (its wasm loop is not stopped)  | no |

**`wasm3` has room for 128 live instances in a process.** Its guarded memory hands out slots of one arena, a module loaded takes
one and a runtime freed gives it back, so the 129th instance alive at once is `RuntimeError: memory allocation failed` (measured,
and the same whether the module has a memory or not). An `Instance` is in a reference cycle (it holds its exports, which hold it), so
one is freed only when Python's cyclic collector gets to it: in a loop that makes many of them, on a build whose collector runs rarely
(the free-threaded `3.14t` did, in CI), call `gc.collect()` now and then, or keep them few. The other backends have no such limit.

**On the original iSH, `wasm3` starts a memory at its maximum.** On the original iSH (`i686`, CPython 3.11.12) a module whose memory
is declared as 1 page, at most 4, is 4 pages long from the start: `len(memory)` is 262144, `type()` says `minimum=4`, and the
module's own `memory.grow(1)` answers -1 (it is already at its maximum). The same module has 1 page on 64-bit (Linux x86-64, and
`wasm3` on iSH-AOK, `aarch64`: 34/34 there). Three steps of the self-test fail for this reason (`module's own memory.grow`,
`Instance(max_memory=)` and `type()` of a memory); the other 31 pass. That is what was observed. The cause is not known and has not been looked into: the owner suspects iSH's emulation of i386 rather than 32-bit as such. It is the only platform so far where not every test passes.

**PyGObject for `gi-jsc`.** The simplest way is the system's own `python3-gi` (with `gir1.2-javascriptcoregtk-4.1`) and the system's Python, as
CI does. To have it in a venv instead, `pip install "wasmhost[gi-jsc]"` (or `uv sync --extra gi-jsc`) builds PyGObject from source, so the system needs
its development files first, and which ones depends on the PyGObject that gets built: **3.52 and later need `girepository-2.0`** (GLib 2.80+: Ubuntu 24.04
and later), **3.50 and earlier need `girepository-1.0`**:

```bash
# Ubuntu 24.04 and later (PyGObject 3.52+, the default)
sudo apt install libgirepository-2.0-dev gir1.2-javascriptcoregtk-4.1 libcairo2-dev pkg-config python3-dev build-essential

# an older Ubuntu (22.04): the older development package, and keep PyGObject below 3.52
sudo apt install libgirepository1.0-dev gir1.2-javascriptcoregtk-4.1 libcairo2-dev pkg-config python3-dev build-essential
pip install "wasmhost[gi-jsc]" "pygobject<3.52"
```

The build stops with `Dependency 'girepository-2.0' is required but not found` when only the older package is there (seen on Ubuntu 26.04, where
`libgirepository1.0-dev` alone was not enough for PyGObject 3.58). The backend asks for the `4.1` typelib of JavaScriptCore, so on a system that only
has `4.0` it does not start: use `jsc`, which needs no PyGObject. The Ubuntu 22.04 line was not tried here.

**No JIT on iOS.** In Pythonista (and any app that is not Safari) JavaScriptCore runs WebAssembly without its JIT, so `jscontext` there is
an interpreter: `python -m wasmhost bench` shows a loop several times slower than `jsc` on a desktop and close to `jsc --no-jit`.
For plain computation an interpreter such as `wasm3` would be faster there, but `wasm3` is a C extension that can not be installed
on iOS; the cost of a call from Python is the same either way (the bridge dominates).

**Deno is not supported.** Run on its pipe protocol like Node, it passes all of the self-test but the exception-handling step: Deno
(2.9.6 and 2.9.7 tried) panics ("Deno has panicked", `capacity overflow`) when a `WebAssembly.Exception` leaves
`vm.runInContext`, which is what a module that throws an exception of a tag the caller does not catch does there. Node
and Bun are the same V8 and JavaScriptCore without this. It is an upstream bug; see `BACKLOG.md` (B-701b).

## Limits for untrusted code

Three limits, each an extension outside the JavaScript API except the first road of the memory ceiling.
What an engine can't do is a `NotImplementedError` (`backend.supports("timeout")`, `supports("fuel")`), never a silent no-op.

| Backend                       | Memory ceiling | `timeout` | `fuel` |
| ----------------------------- | -------------- | --------- | ------ |
| `wasmtime`                    | yes            | yes       | yes    |
| `wasm3`                       | yes            | yes       | yes    |
| `node`                        | yes            | yes       | no     |
| `bun`                         | yes            | no        | no     |
| `jsc`, `gi-jsc`, `jscontext`  | yes            | no        | no     |

So **on iOS (`jscontext`) only the memory ceiling exists**: do not run untrusted code there expecting a time limit.

**Memory.** `memory.grow` past the ceiling answers `-1` and nothing else changes. A module that *imports* its memory
(Emscripten `IMPORTED_MEMORY`, `wasm-ld --import-memory`) is held as in JavaScript: give it `Memory(initial, maximum)` (not on
`wasm3`, which has no imported memory). A memory the module makes itself, with no maximum, is held by
`Instance(module, max_memory=pages)` (also in `instantiate` and `instantiate_sync`): the maximum is written into a copy of the
module, so it works the same on every engine, and the copy for a given ceiling is compiled once and kept.

**Time.** `Instance(module, timeout=seconds)` ends a call that runs longer with a `Timeout` (a `Trap`). It is wall-clock time,
per call (a batch is one call; the start function and host functions are under it too).

- `wasmtime`: by epochs. The instance gets an engine that counts them, a tight loop runs about 3 times slower, and it lives in a
  store of its own, so it can't share a `Memory`, `Table` or `Global` made outside it. The instance can be called again.
- `node`: `vm`'s timeout. A `Timeout` in a batch drops the results of the steps before it. The instance can be called again.
- `wasm3`: no thread can stop a call (it holds the GIL), so the call is cut into slices of gas (about 5 ms of a tight loop, about
  8% cost) and the clock is looked at between them, using pywasm3's suspendable runs. A paused call can't be cancelled, so
  **an instance that timed out is finished: every later call is a `Trap` that says so** (other instances, and a new one from the
  same module, are fine).
- `bun` (its `vm` timeout leaves a wasm loop running) and the JavaScriptCore ones (a script's time limit does not reach a wasm
  loop, checked with `JSContextGroupSetExecutionTimeLimit`): not possible.

**Fuel.** `Instance(module, fuel=n)` ends a call that uses more than `n` units with an `OutOfFuel` (a `Trap`). It is
deterministic, the same on every machine, and needs no thread. The unit is the engine's own (a turn of a tight loop is 8 on
`wasmtime`, under 0.1 on `wasm3`), so a number does not carry from one engine to the other. Each call starts with the whole
budget (a batch is one call; a call from a host function shares the outer one).

- `wasmtime`: `consume_fuel`. The instance gets an engine that counts fuel (a tight loop about 2.4 times slower) and a store of
  its own, like a timed one.
- `wasm3`: pywasm3's gas. A clean trap, and the instance goes on; with a `timeout` too it can't (the call is in slices), as after a
  `Timeout`.
- Node, Bun and the JavaScriptCore engines have nothing to count with.

`timeout` and `fuel` can be given together; whichever runs out first ends the call (on `wasmtime` that instance pays for both
engines, about 5 times slower on a tight loop).

## WASI

`wasmhost.wasi1` is a host for `wasi_snapshot_preview1` (WASI 0.1), the interface that Rust (`wasm32-wasip1`), wasi-sdk, Zig and
most "command line" modules are built against: all 46 functions of the specification, in plain Python, standard library only. It also
offers `wasi_unstable`, the first snapshot, which older toolchains (wasienv, wasm-clang) still produce and which the Lua build of
`examples/coreutils.py` imports from. It is checked against the specification's own `witx` files of both snapshots (names, signatures,
the numbers of errors and flags, the layout of the records), and its self-test step runs a small WASI program of each on every backend.

```python
import sys
from wasmhost import Module
from wasmhost.wasi1 import Wasi

wasi = Wasi(
    args=["prog", "-v"],
    env={"HOME": "/"},
    preopens={"/": "some/folder"},
    stdin=b"input",
    stdout=sys.stdout.buffer.write,
)
code = wasi.run(Module(open("prog.wasm", "rb").read()))  # the exit code: what `proc_exit` was given, else 0
```

- **Folders.** `preopens` maps the name the program sees to a folder of the host; several can be given, and a name that leaves its
  folder (`..`, an absolute name, a link that points out) is refused with `ENOTCAPABLE`. The check and the open are two steps, so
  another process changing the folder in between can still get past it.
- **Streams.** `stdin` is bytes or an object with `read(n)` (default: empty, there is no interactive input); `stdout` and `stderr`
  are callables that take bytes, or objects with `write` (default: the interpreter's own streams).
- **Running.** `wasi.run(module)` instantiates, starts and closes. To give options to the instance, or to make it yourself, use
  `wasi.instantiate(module, timeout=5)` and `wasi.start(instance)`, or `wasi.imports()` with `wasi.bind(instance)`: the import
  object has both snapshots, and the engine links the one a module asks for.
- **`wasi_unstable`** is the same calls with four differences (the order of `whence`, one right less, a 32-bit link count in
  `filestat`, and a clock subscription with an extra field) and no `sock_accept`.
- **Not supported:** sockets (`ENOTSOCK`), signals (`ENOSYS`). A host function that sleeps (`poll_oneoff`) is outside what
  `timeout` and `fuel` can stop.

## Try it on a device

The package carries a self-test, since nothing else can be run in Pythonista/Python IDE to see whether this works there:

```python
import wasmhost

wasmhost.selftest()  # or, from a shell: python -m wasmhost self test [--backend NAME] [--all]
```

It prints one line per check, then `N/M passed`: the Objective-C bridge in use (and, if the C API is not used, why:
`C API  not used: ...`), `WebAssembly` and `BigInt` in the engine, bytes in and out (`via C API` or `via hex`), calls,
`i64`, memory, globals, traps, batches, host functions, a WASI program, globals made on their own and shared between instances, an
isolated instance, which encodings of WebAssembly exceptions the engine takes (`final (try_table): yes, older
(try/catch): no`: a module built with C++ exceptions, such as bclibc's `bclibc_wasm.wasm` from wasi-sdk, needs the final one),
and the cost of a call. A check the backend can't do says so (`not available on this backend, as
documented`) and counts as passed. If something fails, send the whole output. On a computer,
`python -m wasmhost self test --all` runs it on every backend that starts. Installed with pip, the same commands are
there as `wasmhost self test` and `wasmhost bench`.

`python -m wasmhost bench [--backend NAME] [--no-jit] [--buffer KIB]` times a call, a batch of three, moving a buffer in and
out of memory (MB/s) and the engine itself (a recursive
`fib`, a loop) on each backend that starts, to choose one. `--no-jit` takes the JIT off JavaScriptCore
(`JSC_useJIT=false`): where there is none, an interpreter such as `wasm3` can be several times faster.

### Where it has been run

| Where                                                                     | Backend                   | Result                                                                                                                                    | A call / a batch of 3 |
| ------------------------------------------------------------------------- | ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- | --------------------- |
| Pythonista 3 (StaSh 0.7.5), Python 3.10.4, iPhone 16 (iPhone17,3)         | `jscontext` (`objc_util`) | **25/25**, bytes `via C API`, host functions (wasmhost 0.0.2b1)                                                                           | 55 / 102 us           |
| Pythonista 3, Python 3.10.4, iPhone 16 (iPhone17,3), iOS 26 (Darwin 25.6) | `jscontext` (`objc_util`) | **28/28**, bytes `via C API`, host functions, both encodings of WebAssembly exceptions (`try_table` and `try`/`catch`) (wasmhost 0.0.3b2) | 40 / 85 us            |
| Pythonista 3, Python 3.10.4, iPhone 16 (iPhone17,3), iOS 26 (Darwin 25.6) | `jscontext` (`objc_util`) | **35/35**, bytes `via C API`, both encodings of exceptions, functions and signatures, async, threads (wasmhost 0.0.4.dev64)               | 68 / 156 us           |
| Pythonista 3, Python 3.10.4, iPhone 16 (iPhone17,3), iOS 26 (Darwin 25.6) | `jscontext` (`objc_util`) | **36/36**, a 1 MiB buffer moves at 5400 MB/s in and 12700 MB/s out (a typed array through the C API; through hex it was about 40) (wasmhost 0.0.4.dev84) | 36 / 84 us |
| Pythonista 3, Python 3.10.4, iPhone 16 (iPhone17,3), iOS 26 (Darwin 25.6) | `jscontext` (`objc_util`) | **38/38**, a memory held by the maximum of an imported `Memory` and by `Instance(max_memory=)` (wasmhost 0.1.0b2.dev3)                       | 36 / 83 us            |
| Pythonista 3, Python 3.10.4, iPhone 16 (iPhone17,3), iOS 26 (Darwin 25.6) | `jscontext` (`objc_util`) | **41/41**, `wasmhost.wasi1`: a WASI program of each snapshot (`wasi_snapshot_preview1` and `wasi_unstable`) with arguments, stdout, a file and an exit code; timeout and fuel "not available", as documented (wasmhost 0.1.0b3.dev14) | 47 / 111 us |
| PythonIDE, Python 3.14.7, `ios-13.0-arm64-iphoneos`                       | `jscontext` (`objc_util`) | **25/25**, bytes `via C API`, host functions (wasmhost 0.0.2b1)                                                                           | 39 / 77 us            |
| PythonIDE, Python 3.14.7, `ios-13.0-arm64-iphoneos` | `jscontext` (`objc_util`) | **41/41**, the same, WASI step included (wasmhost 0.1.0b3.dev14) | 44 / 102 us |
| iSH-AOK 1.3 (557), a fork of iSH, Linux 5.10.0-ish_aok aarch64, CPython 3.14.8 | `wasmtime` | **34/34** (wasmhost 0.1.0b2, `uv tool install wasmhost --prerelease=allow --with wasmtime`; without `wasmtime` no backend starts there and the self-test says so) | 1615 / 5500 us |
| iSH-AOK 1.3 (557), Linux 5.10.0-ish_aok aarch64, CPython 3.14.8 | `wasm3` (`WASMHOST_BACKEND=wasm3`) | **34/34**, wasmhost 0.1.0b2 (the memory steps pass: the 64-bit `wasm3` starts a memory at its initial size) | 213 / 1191 us |
| iSH (the original), Linux 4.20.69-ish i686, CPython 3.11.12 | `wasm3` | 31/34, failed: the module's own `memory.grow`, `Instance(max_memory=)`, `type()` of a memory (`wasm3` starts a memory at its maximum there, see Backends); wasmhost 0.1.0b2 | 571 / 3157 us |
| Linux, CPython 3.14t                                                      | `jsc`                     | 35/35                                                                                                                                     | 32 / 102 us           |
| Linux, CPython 3.14t                                                      | `gi-jsc`                  | 27/27 (host functions: not available, as documented)                                                                                      | 35 / 62 us            |
| Ubuntu 26.04, CPython 3.10.20, PyGObject 3.58.0 (`pip`, built from source) | `gi-jsc`                  | **38/38** (host functions: not available, as documented)                                                                                  | 32 / 74 us            |
| Linux, CPython 3.14t                                                      | `node`                    | 35/35                                                                                                                                     | 82 / 340 us           |
| Linux, CPython 3.11, Bun 1.4.2                                            | `bun`                     | 35/35                                                                                                                                     | 133 / 287 us          |
| Linux, CPython 3.14t                                                      | `wasmtime`                | 29/29                                                                                                                                     | 66 / 212 us           |
| Linux, CPython 3.14t                                                      | `wasm3`                   | 29/29                                                                                                                                     | 3 / 63 us             |
| Linux, CPython 3.10 and PyPy 3.10                                         | `node`                    | 25/25 (an earlier version; and the test suite on 3.10)                                                                                    |                       |

The counts of the Linux rows are for the current version (the first two phone rows are for `0.0.2b1`: the self-test has
grown since); the times are one run of the self-test each, so read them as an order of magnitude. A host function costs about
what a call does, plus a round trip on `node` or `bun` (measured once: about 4 us on `wasm3`, 50 us on `wasmtime` and `jsc`,
200 us on `node`, per host call including the export around it).

Host functions and the C API bytes path on `jscontext` have run on both iOS apps above; on Linux they also run
against a fake `objc_util` whose `c` is the real JavaScriptCore library, and on macOS in CI against a real
Objective-C `JSContext` through rubicon-objc. Not run on a device: the `rubicon-objc` bridge (both iOS apps have
`objc_util`, so it isn't needed there). Node's synchronous wait for a host function's answer has run in CI on Linux,
macOS and Windows; Bun's, in CI on Linux and macOS (not on Windows yet).

### A note on wasmtime and `faulthandler`

wasmtime installs process-wide signal handlers when its first engine is created, and uses them to catch a trap.
Python's `faulthandler` (on with `python -X faulthandler`, and in pytest) replaces the handlers when it is enabled
*after* that, and the first trap then ends the process (`Fatal Python error: Illegal instruction`). Enable it first
(or not at all), or start the backend later: this repo's `tests/conftest.py` does that.

## Not yet

What the JavaScript API has, or a module can need, and wasmhost does not have yet. `BACKLOG.md` has the order in
which it is meant to be done.

- **Tables and references.** The signature of a table entry is not told by a JavaScript engine (set it by hand,
  or let the module's `elem` segment tell it; wasmtime finds it); no `externref` (tables, globals or values); no `v128`.
- **Memory is copied** on `read` and `write`. `Memory.view(offset, length)` gives the engine's own memory as a `memoryview`, with no copy, on `wasmtime` and `wasm3`
  (not on a JavaScript engine, whose memory lives in another place); it is released when the module may have run (a call, a batch, a `grow`), so it is
  for use at once. On a JavaScript engine the cost of moving a buffer is in the encoding, not the copy: see `wasmhost bench`.
- **Limits on untrusted code** are only partly possible: a memory ceiling everywhere, a timeout and a fuel count only where the
  engine can do it, and **none of the last two on iOS**. See "Limits for untrusted code" above.
- **Threads.** A module built with `-pthread` (the WebAssembly threads proposal: a `shared` memory that the module
  imports, atomic instructions, threads made by the host as several instances of the module on one memory) does not
  run: `Memory(..., shared=True)` raises `NotImplementedError`, so it can not be given as an import. Build without
  threads (`-pthread` off, for wasm-ld `--no-threads`). A wasm instance runs in one thread, and a backend takes one
  call at a time (a lock of its own, see "Async and threads").
- **Newer proposals**: no API for `WebAssembly.Tag` and `WebAssembly.Exception` (the self-test only reports which
  encodings of exceptions an engine takes), SIMD, `memory64`, multi-memory, GC types.
  Whether a module that uses them runs is up to the engine.
- **WASI** is provided for `wasi_snapshot_preview1` by `wasmhost.wasi1` (see [WASI](#wasi)); not yet: sockets, a read-only mode for a folder, and a safe open through
  `openat` (the check of a name and the open are two steps now). `examples/coreutils.py` uses it; `wasmclang.py` and `wasi_sh.py` keep hosts of their own (file systems that are not a folder).

## Test

```bash
uv run pytest                            # every backend that starts here
uv run pytest --wasm-backend node        # one backend: it must start, or the run stops with an error
uv run pytest --wasm-backend bun         # needs `bun` on PATH
uv run pytest --wasm-backend wasmtime    # or wasm3, or jsc (needs the JavaScriptCore library)
uv run pytest --wasm-backend gi-jsc      # needs PyGObject: `uv sync --extra gi-jsc` after the packages above, or a system-site-packages venv (the CI job)
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
