"""Runs the official CPython (3.15, `wasm32-wasip1`, built from python/cpython with wasi-sdk) on
`wasmhost.wasi.preview1`: not Pyodide, not Emscripten, the WASI interface only. `python.wasm` and its standard library
(`lib/python315.zip`) are in this folder, which the program sees as `/py`; `build.sh` makes both. The current folder is
`/` to the program.

    python examples/cpython/run.py -c "print(1 + 1)"
    python examples/cpython/run.py script.py        # a file in the current folder
    python examples/cpython/run.py --dir ~/data:/data -c "print(open('/data/a.txt').read())"

What it can't do is what WASI preview1 can't: no processes (`subprocess`), no sockets (`urllib` fails), no `dlopen` (an
extension module has to be linked into `python.wasm`), and wasi-sdk has no zlib or sqlite3, so no `ensurepip`.
"""

import os
import sys

from wasmhost import Module
from wasmhost.wasi import preview1

HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv):
    preopens = {"/": os.getcwd(), "/py": HERE}  # the program starts in `/`, so that is the current folder
    while argv[:1] == ["--dir"] and len(argv) > 1:  # --dir HOST[:GUEST], the guest name defaults to the host path
        host, _, guest = argv[1].partition(":")
        preopens[guest or host] = os.path.expanduser(host)
        argv = argv[2:]
    with open(os.path.join(HERE, "python.wasm"), "rb") as file:
        module = Module(file.read())
    stdout, stderr = sys.stdout.buffer.write, sys.stderr.buffer.write
    wasi = preview1.Preview1(
        ["/py/python.wasm", *argv], {"PYTHONHOME": "/py"}, preopens, sys.stdin.buffer, stdout, stderr
    )
    return wasi.run(module)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
