"""Packs the standard library of a CPython checkout into one zip of byte code, `lib/python315.zip`, which is where
CPython looks for it when it finds no `lib/python3.15/`. Run it with a CPython of the same minor version as the one the
checkout was built as (the build's own Python is that): the byte code is made here, and a module is imported from it
as it is, with no source beside it. The zip is not compressed: `python.wasm` has no zlib to read it with.

    python mkstdlib.py CHECKOUT/Lib CHECKOUT/cross-build/wasm32-wasip1/build/lib.wasi-wasm32-3.15 lib/python315.zip
"""

import os
import py_compile
import sys
import tempfile
import zipfile

SKIP = {"test", "tests", "idlelib", "turtledemo", "tkinter", "ensurepip", "lib2to3", "__pycache__", "site-packages"}


def main(lib, builddir, out):
    names = []
    for root, dirs, files in os.walk(lib):
        dirs[:] = sorted(d for d in dirs if d not in SKIP)
        for name in sorted(files):
            if name.endswith(".py") and not name.startswith("test_") and not name.startswith("_test"):
                names.append(os.path.join(root, name))
    names += [os.path.join(builddir, f) for f in os.listdir(builddir) if f.startswith("_sysconfigdata")]
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as archive:
        for path in names:
            base = lib if path.startswith(lib) else builddir
            name = os.path.relpath(path, base)[:-3] + ".pyc"
            cfile = os.path.join(tmp, "x.pyc")
            compiled = py_compile.compile(path, cfile=cfile, dfile=name[:-1], doraise=True, optimize=0)
            # a fixed time, so that the same sources give the same zip
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            with open(compiled, "rb") as file:
                archive.writestr(info, file.read())
    print(f"{out}: {len(names)} modules, {os.path.getsize(out)} bytes")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*(os.path.abspath(a) for a in sys.argv[1:]))
