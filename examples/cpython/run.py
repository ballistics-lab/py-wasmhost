"""Runs the official CPython (3.15, `wasm32-wasip1`, built from python/cpython with wasi-sdk) on
`wasmhost.wasi.preview1`: not Pyodide, not Emscripten, the WASI interface only. `python.wasm` and its standard library
(`lib/python315.zip`) are in this folder, which the program sees as `/py`; `build.sh` makes both. The current folder is
`/` to the program.

    python examples/cpython/run.py -c "print(1 + 1)"
    python examples/cpython/run.py script.py        # a file in the current folder
    python examples/cpython/run.py --dir ~/data:/data -c "print(open('/data/a.txt').read())"
    python examples/cpython/run.py --site ~/packages -c "import six"   # packages of pure Python, put on sys.path

What it can't do is what WASI preview1 can't: no processes (`subprocess`), no sockets (`urllib` fails), no `dlopen` (an
extension module has to be linked into `python.wasm`, so a wheel with compiled code, even a `wasm32` one, can't be
loaded: those on PyPI are for Emscripten/Pyodide), and wasi-sdk has no sqlite3. zlib is linked in (`build.sh`).
"""

import os
import sys

from wasmhost import Module
from wasmhost.wasi import preview1

HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv):
    preopens = {"/": os.getcwd(), "/py": HERE}  # the program starts in `/`, so that is the current folder
    path = []
    while argv[:1] in (["--dir"], ["--site"]) and len(argv) > 1:
        if argv[0] == "--dir":  # --dir HOST[:GUEST], the guest name defaults to the host path
            host, _, guest = argv[1].partition(":")
            preopens[guest or host] = os.path.expanduser(host)
        else:  # --site HOST: a folder of pure Python packages, put on sys.path as /site, /site1...
            guest = "/site" + (str(len(path)) if path else "")
            preopens[guest] = os.path.expanduser(argv[1])
            path.append(guest)
        argv = argv[2:]
    env = {"PYTHONHOME": "/py", "PYTHONPATH": ":".join(path)} if path else {"PYTHONHOME": "/py"}
    with open(os.path.join(HERE, "python.wasm"), "rb") as file:
        module = Module(file.read())
    stdout, stderr = sys.stdout.buffer.write, sys.stderr.buffer.write
    wasi = preview1.Preview1(["/py/python.wasm", *argv], env, preopens, sys.stdin.buffer, stdout, stderr)
    return wasi.run(module)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
