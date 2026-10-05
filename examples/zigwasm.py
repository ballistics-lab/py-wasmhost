"""Zig's own compiler, built to WebAssembly (zig.wasm), compiling a program to WebAssembly that wasmhost then runs.

    pip install wasmtime
    python examples/zigwasm.py

The first run downloads `@zigc/wasm32-wasi` (zig.wasm, 13 MB) and `@zigc/lib` (Zig's standard library, 28 MB, of
which the part a freestanding build needs is unpacked) from npm into the cache (`$WASMHOST_CACHE`, else
`~/.cache/wasmhost`, or `./.cache` where there is no usable home directory). Later runs download nothing.

Two limits, both of this build of Zig and of this example:

- It compiles **Zig, not C**. zig.wasm has no clang inside: `zig cc` stops with "aro does not support compiling C
  objects yet", and `zig translate-c` needs a child process, which WASI does not have. For C use a native Zig, as in
  `examples/zigcc.py`.
- wasmhost has no WASI, which zig.wasm needs (files, arguments, environment). So zig.wasm runs here on wasmtime's WASI
  (hence `pip install wasmtime`, so not on iOS); the module it produces is plain WebAssembly and runs on wasmhost's
  own backend, any of them.
"""

import io
import os
import sys
import tarfile
import time
import urllib.request

import wasmhost

ZIG_VERSION = "0.17.0"
NPM = "https://registry.npmjs.org/@zigc/{pkg}/-/{pkg}-" + ZIG_VERSION + ".tgz"
SKIP = {
    "libc",
    "include",
    "libcxx",
    "libcxxabi",
    "libunwind",
    "libtsan",
    "lldb",
    "docs",
    "build-web",
}  # not for wasm32-freestanding

ZIG_SOURCE = """
export fn fib(n: i32) i32 {
    var a: i32 = 0;
    var b: i32 = 1;
    var i: i32 = 0;
    while (i < n) : (i += 1) {
        const t = a + b;
        a = b;
        b = t;
    }
    return a;
}

/// Sum of `count` i32 values at `ptr` in the module's memory.
export fn sum(ptr: [*]const i32, count: i32) i32 {
    var total: i32 = 0;
    for (ptr[0..@intCast(count)]) |v| total += v;
    return total;
}

var buffer: [16]i32 = undefined;
export fn get_buffer() [*]i32 {
    return &buffer;
}
"""


def cache_root():
    """Where downloads are kept: $WASMHOST_CACHE; else ~/.cache/wasmhost; else ./.cache (no usable home)."""
    if env := os.environ.get("WASMHOST_CACHE"):
        return env
    home = os.path.expanduser("~")
    if home != "~" and os.path.isdir(home) and os.access(home, os.W_OK):
        return os.path.join(home, ".cache", "wasmhost")
    return os.path.join(".", ".cache")


def download(pkg):
    url = NPM.format(pkg=pkg)
    print(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310
        data = resp.read()
    return tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")


def fetch_zig(root):
    """Lay out `root` as zig.wasm sees it: /zig.wasm, /lib (the standard library), /cache, /work."""
    if os.path.exists(os.path.join(root, "lib", "std", "std.zig")) and os.path.exists(os.path.join(root, "zig.wasm")):
        return
    os.makedirs(os.path.join(root, "cache"), exist_ok=True)
    os.makedirs(os.path.join(root, "work"), exist_ok=True)
    with download("wasm32-wasi") as tar:
        member = tar.extractfile("package/bin/zig.wasm")
        with open(os.path.join(root, "zig.wasm"), "wb") as f:
            f.write(member.read())
    with download("lib") as tar:
        for info in tar:
            if not info.isfile():
                continue
            parts = info.name.split("/")[1:]  # drop "package/"
            if not parts or parts[0] in SKIP:
                continue
            path = os.path.join(root, "lib", *parts)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(tar.extractfile(info).read())


class Zig:
    """zig.wasm on wasmtime's WASI. Paths are relative to `root`, which is the only directory it sees (as `/`)."""

    def __init__(self, root):
        try:
            import wasmtime  # noqa: PLC0415
        except ImportError:
            sys.exit("zig.wasm needs a WASI runtime: `pip install wasmtime`")
        self.wasmtime = wasmtime
        self.root = root
        self.engine = wasmtime.Engine()
        self.module = wasmtime.Module.from_file(self.engine, os.path.join(root, "zig.wasm"))

    def run(self, *args):
        """Run `zig ARGS`; its output goes to ours. True if it exited with 0."""
        wt = self.wasmtime
        store = wt.Store(self.engine)
        config = wt.WasiConfig()
        config.argv = ["zig", *args]
        config.env = [("ZIG_LIB_DIR", "lib"), ("ZIG_GLOBAL_CACHE_DIR", "cache")]
        config.preopen_dir(self.root, "/")
        config.inherit_stdout()
        config.inherit_stderr()
        store.set_wasi(config)
        linker = wt.Linker(self.engine)
        linker.define_wasi()
        instance = linker.instantiate(store, self.module)
        try:
            instance.exports(store)["_start"](store)
        except wt.ExitTrap as exc:
            return exc.code == 0
        return True


root = os.path.join(cache_root(), "zig", ZIG_VERSION)
fetch_zig(root)

with open(os.path.join(root, "work", "lib.zig"), "w") as f:
    f.write(ZIG_SOURCE)

zig = Zig(root)
start = time.monotonic()
ok = zig.run(
    "build-exe", "-target", "wasm32-freestanding", "-O", "ReleaseSmall", "-fno-entry", "-rdynamic",
    "-fno-compiler-rt", "work/lib.zig", "-femit-bin=work/lib.wasm",
)  # fmt: skip
if not ok:
    sys.exit("zig.wasm failed (its messages are above)")
with open(os.path.join(root, "work", "lib.wasm"), "rb") as f:
    wasm = f.read()
print(f"zig.wasm compiled {len(wasm)} bytes of wasm in {time.monotonic() - start:.1f} s")

module = wasmhost.Module(wasm)
print("exports:", [(e.name, e.kind) for e in wasmhost.Module.exports(module)])
instance = wasmhost.Instance(module)
print("backend:", wasmhost.get_backend().name)
print("fib(10) =", instance.exports.fib(10))

values = [1, 2, 3, 4, 5]
ptr = instance.exports.get_buffer()
instance.exports.memory.write(ptr, b"".join(v.to_bytes(4, "little") for v in values))
print(f"sum({values}) =", instance.exports.sum(ptr, len(values)))
