# CPython on `wasmhost.wasi.preview1`

The official CPython 3.15 (`python/cpython`, branch `3.15`, commit `1c6794e`), built for `wasm32-wasip1` with the build that CPython
ships (`Platforms/WASI`) and wasi-sdk 33, run by `wasmhost.wasi.preview1`. Not Pyodide: no Emscripten, no JavaScript glue, the WASI
interface only, so it runs on every backend that runs a WASI command.

```sh
python examples/cpython/run.py -c "print(1 + 1)"
python examples/cpython/run.py script.py                              # a file in the current folder
python examples/cpython/run.py --dir ~/data:/data -c "print(open('/data/a.txt').read())"
python examples/cpython/run.py --site ~/packages -c "import six"      # packages of pure Python, put on sys.path
```

| File                | What it is                                                                                         |
| ------------------- | -------------------------------------------------------------------------------------------------- |
| `python.wasm`       | CPython with zlib linked in, 7.9 MB (the debug information is stripped)                            |
| `lib/python315.zip` | the standard library as byte code, 5 MB, found by CPython on its own                               |
| `sitecustomize.py`  | what WASI lacks (see below); `mkstdlib.py` puts it into the zip                                    |
| `run.py`            | the runner: `Preview1` with the current folder as `/` and this folder as `/py`                     |
| `build.sh`          | makes `python.wasm` and `lib/python315.zip` from python/cpython, wasi-sdk and zlib                  |
| `mkstdlib.py`       | packs the standard library of a checkout into the zip                                              |

`run.py` options: `--dir HOST[:GUEST]` gives the program one more folder; `--site HOST` is a folder of packages, which becomes
`/site` (then `/site1`...) on `sys.path`. Everything else is passed to `python.wasm` (`-c`, `-m`, a script).

## Building

`./build.sh` needs git, curl, tar, make, a C compiler and python3. It downloads wasi-sdk 33 (checked against a SHA-256), fetches
CPython and zlib by commit, builds a native Python (the cross build needs one), cross-compiles, strips the module and packs the
standard library. Sources go to `./.build` (or `$WORK`), which is not committed. Nothing has to run WebAssembly while it builds, so
there is no wasmtime step. The committed `python.wasm` and `lib/python315.zip` are the output of it; another machine gives the same
contents but not necessarily the same bytes (paths are in the module, and the zip differed by a few bytes between two builds).

wasi-sdk has no zlib, so `build.sh` compiles the dozen C files of zlib 1.3.1 by hand and links them in. The arguments that say where
it is go to the WASI build only: the native Python is built without them.

## Checked

Run by hand on the Node and Bun backends of this repository (Linux x64). `.github/workflows/tests.yml` runs a small program (`sys.platform`,
`json`, `re`, `hashlib`, a loop) on **wasmtime** (Linux x64) and on **`jscontext`** (a real `JSContext` through rubicon-objc, macOS);
both passed on the commit that added them (`de02bc6`). The checks below were made on Node; only that program has run on wasmtime and
`jscontext`, so `pip` and `asyncio` there are not checked.

| Works                                                                                                                              | How it was checked                                           |
| ---------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| `python -c`, a script, `-m`; `sys.version` 3.15.0+dev, `sys.platform == "wasi"`                                                    | run                                                          |
| Reading and writing files in the folders given with `--dir`; the current folder is `/`                                              | wrote and read a file, listed a folder                       |
| `--site`: packages of pure Python (`six`, `py-ballisticcalc` with `deprecated`, `wrapt`, `typing_extensions`)                      | imported them; `py-ballisticcalc` ran a calculation          |
| `zlib`, `gzip`, `zipfile` with compression                                                                                         | round trips                                                  |
| `get-pip.py` offline (`--no-index --find-links`), then `python -m pip install/list` of local wheels                                | installed `pip 26.1` and `six 1.17.0`                        |
| `venv.create(..., with_pip=False)`                                                                                                 | ran                                                          |
| `asyncio` (`run`, `gather`, `Queue`, `call_later`, `wait_for` with a timeout)                                                      | ran                                                          |
| `signal.signal`, `select.select`, `selectors` (`PollSelector`), `time.sleep`                                                       | ran                                                          |
| `decimal`, `uuid`, `csv`, `json`, `tomllib`, `xml`, `email`, `http.client`, `hashlib`, `re`, `unicodedata`                         | imported / ran                                               |

`sitecustomize.py` makes up three things that WASI does not have and that `pip` imports: `os.getuid` and its relatives (always 0),
`os.umask`, and a `mmap` module that can be imported and cannot be used. It also takes the self-pipe out of `asyncio`'s selector loop
(there is no `socketpair`, and no thread or signal to wake the loop from), when `asyncio` is imported. Names that WASI does have are
left alone.

## Does not work

| What                                                     | Why                                                                                                                                                       |
| -------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Network: `socket`, `urllib`, `requests`, `pip install` from an index | WASI preview1 has no sockets. `import socket` passes; `socket.socket()` and `getaddrinfo` fail. Downloads have to be done by the host (`pip download`).   |
| `ssl`, HTTPS                                             | no OpenSSL in the build (`_ssl` is missing)                                                                                                               |
| `subprocess`, `multiprocessing`, `ensurepip`, `venv.create(with_pip=True)` | `OSError: wasi does not support processes`. `ensurepip` runs `pip` in a child process; `get-pip.py` and `python -m pip` run it in the same one.          |
| `threading.Thread`, `ThreadPoolExecutor`                 | no threads: the build is `--disable-wasm-pthreads` (`RuntimeError: can't start new thread`)                                                                |
| Extension modules, wheels with compiled code             | no `dlopen`: a module must be linked into `python.wasm`. The `wasm32` wheels on PyPI (for example `py-ballisticcalc.exts`) are `pyemscripten_*`, built for Pyodide, not for WASI; no wheel tagged for WASI was found there. |
| `sqlite3`, `bz2`, `lzma`                                 | wasi-sdk has no such libraries (zlib was built by hand; these were not)                                                                                    |
| `zoneinfo` without `tzdata`                              | no time zone database; the pure Python `tzdata` wheel, given with `--site`, should do (not tried)                                                           |
| `ctypes`, `readline`, `tkinter`                          | not built for WASI                                                                                                                                         |

## Not done

- **An interactive prompt.** `input()` at a prompt works differently on iOS (keys do not work as in a terminal), as in the other
  examples of this repository; left for later.
- **The network through the host.** A host function is a plain Python callable and works the same on every backend (the way
  `examples/git/` gets the network for libgit2), so a bridge needs no JavaScript. It has not been written: `python.wasm` would need a
  module that calls it in place of `_socket`.
- **Compiled extensions** linked into `python.wasm` (for example the C part of `py-ballisticcalc`).
- **`sqlite3`, `bz2`, `lzma`.**
- **Start time** was measured only on Node: about 1 s for `python -c pass`, 0.7 s of it compiling the module. On `jscontext` (no JIT on
  iOS) it is not measured.
- Not run on a device (Pythonista); on `jscontext` through rubicon only the small CI program has run.
