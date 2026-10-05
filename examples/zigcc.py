"""Compile C to WebAssembly with Zig (`zig cc`), then run it with wasmhost.

    pip install ziglang        # Zig's compiler as a PyPI package; or any `zig` on PATH
    python examples/zigcc.py

Where there is no Zig and no way to start one (Pythonista on iOS), it runs `examples/wasm/zigcc.wasm`, the same C
built beforehand by this script, so the rest of the example works there too.

`zig cc` is a drop-in clang that ships its own libc and wasm-ld, so there is no wasi-sdk to install. The C below is
built for `wasm32-freestanding` (no libc, no WASI): it exports `fib` and `sum`, works on the module's memory, and
calls the host function `env.log`. With Zig, the `.wasm` is built in a temporary directory; nothing is written.
"""

import os
import shutil
import subprocess
import sys
import tempfile

import wasmhost

C_SOURCE = r"""
#define EXPORT(name) __attribute__((export_name(name)))
#define IMPORT(name) __attribute__((import_module("env"), import_name(name)))

IMPORT("log") void host_log(int value);

EXPORT("fib") int fib(int n) {
    int a = 0, b = 1;
    while (n-- > 0) { int t = a + b; a = b; b = t; }
    return a;
}

/* sum of `count` int32 values the host wrote at `ptr` in the module's memory */
EXPORT("sum") int sum(const int *ptr, int count) {
    int total = 0;
    for (int i = 0; i < count; i++) total += ptr[i];
    host_log(total);
    return total;
}

/* a place for the host to write to: the address is what matters, the module owns the bytes */
static int buffer[16];
EXPORT("buffer") int *get_buffer(void) { return buffer; }
"""


PREBUILT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wasm", "zigcc.wasm")


def zig_command() -> list[str] | None:
    if shutil.which("zig"):
        return ["zig"]
    try:
        import ziglang  # noqa: F401, PLC0415
    except ImportError:
        return None
    return [sys.executable, "-m", "ziglang"]


def compile_c(source: str, zig: list[str]) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        src, out = os.path.join(tmp, "lib.c"), os.path.join(tmp, "lib.wasm")
        with open(src, "w") as f:
            f.write(source)
        subprocess.run(  # noqa: S603
            [
                *zig,
                "cc",
                "-target",
                "wasm32-freestanding",
                "-O2",
                "-nostdlib",
                "-Wl,--no-entry",
                "-Wl,--export-memory",
                src,
                "-o",
                out,
            ],  # fmt: skip
            check=True,
        )
        with open(out, "rb") as f:
            return f.read()


zig = zig_command()
if zig:
    wasm = compile_c(C_SOURCE, zig)
    print(f"compiled {len(wasm)} bytes of wasm with Zig")
else:
    with open(PREBUILT, "rb") as f:
        wasm = f.read()
    print(f"no Zig here (`pip install ziglang`): using the prebuilt {os.path.basename(PREBUILT)}, {len(wasm)} bytes")

module = wasmhost.Module(wasm)
print("exports:", [(e.name, e.kind) for e in wasmhost.Module.exports(module)])

logged = []
instance = wasmhost.Instance(module, {"env": {"log": logged.append}})
print("backend:", wasmhost.get_backend().name)
print("fib(10) =", instance.exports.fib(10))

values = [1, 2, 3, 4, 5]
ptr = instance.exports.buffer()
instance.exports.memory.write(ptr, b"".join(v.to_bytes(4, "little") for v in values))
print(f"sum({values}) =", instance.exports.sum(ptr, len(values)), "| env.log got", logged)
