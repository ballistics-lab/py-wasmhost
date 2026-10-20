"""Runs the official CPython (3.15, built for `wasm32-wasip1` from python/cpython with wasi-sdk 33) on
`wasmhost.wasi.preview1`: not Pyodide, no Emscripten, the WASI interface only.

Build it once (a checkout of python/cpython, branch 3.15, and wasi-sdk from github.com/WebAssembly/wasi-sdk):

    export WASI_SDK_PATH=/opt/wasi-sdk-33.0-x86_64-linux
    python3 Platforms/WASI build --host-runner true     # `true`: nothing needs to run wasm during the build

then `python examples/cpython_wasi.py /path/to/cpython [python arguments]`, e.g. `-c "print(1 + 1)"`.

The checkout is preopened as `/`: CPython finds `Lib/` and the build's extension modules by its program name
(`/cross-build/wasm32-wasip1/python.wasm`) relative to it, so a program sees the whole checkout. What it can't do is
what WASI preview1 can't: no processes (`subprocess`), no sockets (`_socket` has no `getaddrinfo`, so `urllib` fails),
no `dlopen` (an extension module has to be linked into `python.wasm`), and wasi-sdk has no zlib or sqlite3.
"""

import sys

from wasmhost import Module
from wasmhost.wasi import preview1

PYTHON = "/cross-build/wasm32-wasip1/python.wasm"


def main(checkout, *args):
    with open(checkout + PYTHON, "rb") as file:
        module = Module(file.read())
    wasi = preview1.Preview1(
        [PYTHON, *args], {}, {"/": checkout}, sys.stdin.buffer, sys.stdout.buffer.write, sys.stderr.buffer.write
    )  # fmt: skip
    return wasi.run(module)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.exit(main(*sys.argv[1:]))
