"""Compile C to WebAssembly with Zig (`zig cc`), then run it with wasmhost.

    pip install ziglang        # Zig's compiler as a PyPI package; or any `zig` on PATH
    python examples/zigcc.py

Where there is no Zig and no way to start one (Pythonista on iOS), it runs PREBUILT, the same C built beforehand by
this script and kept in the file as base64, so the file works on its own.

`zig cc` is a drop-in clang that ships its own libc and wasm-ld, so there is no wasi-sdk to install. The C below is
built for `wasm32-freestanding` (no libc, no WASI): it exports `fib` and `sum`, works on the module's memory, and
calls the host function `env.log`. With Zig, the `.wasm` is built in a temporary directory; nothing is written.
"""

import base64
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


PREBUILT = base64.b64decode(
    "AGFzbQEAAAABFARgAX8AYAF/AX9gAn9/AX9gAAF/AgsBA2VudgNsb2cAAAMEAwECAwQFAXABAQEFAwEAEQYJAX8BQYCAwAALBx8EBm1lbW9y"
    "eQIAA2ZpYgABA3N1bQACBmJ1ZmZlcgADCu8CA58BAQN/QQEhAQJAIABBAU4NAEEADwsgAEEHcSECAkACQCAAQQhPDQBBACEADAELIABB+P//"
    "/wdxIQNBACEAQQEhAQNAIAEgAGoiACABaiIBIABqIgAgAWoiASAAaiIAIAFqIgEgAGoiACABaiEBIANBeGoiAw0ACwsCQCACRQ0AIAAhAwNA"
    "IAEiACADaiEBIAAhAyACQX9qIgINAAsLIAALwgEBBH8CQCABQQFODQBBABCAgICAAEEADwsgAUEDcSECAkACQCABQQRPDQBBACEDQQAhBAwB"
    "CyABQfz///8HcSEFQQAhAyAAIQFBACEEA0AgAUEMaigCACABQQhqKAIAIAFBBGooAgAgASgCACAEampqaiEEIAFBEGohASAFIANBBGoiA0cN"
    "AAsLAkAgAkUNACAAIANBAnRqIQEDQCABKAIAIARqIQQgAUEEaiEBIAJBf2oiAg0ACwsgBBCAgICAACAECwgAQYCAwIAACwDIAwouZGVidWdf"
    "bG9j/////wMAAAAAAAAAQAAAAAYA7QAAMRyfAAAAAAAAAAD/////AwAAAAcAAABAAAAAAwARAJ+GAAAAiAAAAAQA7QIAn4gAAACaAAAABADt"
    "AACfAAAAAAAAAAD/////AwAAAAcAAABAAAAAAwARAZ9lAAAAZwAAAAQA7QIAn2cAAABsAAAABADtAACfbAAAAHUAAAAEAO0AAZ+GAAAAiAAA"
    "AAQA7QIAn4gAAACNAAAABADtAACfjQAAAJoAAAAEAO0AAZ8AAAAAAAAAAP////8DAAAAZQAAAGcAAAAEAO0CAJ9nAAAAbAAAAAQA7QAAn2wA"
    "AAB1AAAABADtAAGfjQAAAJoAAAAEAO0AAZ8AAAAAAAAAAP////+kAAAAAAAAAEwAAAAEAO0AAZ8AAAAAAAAAAP////+kAAAAAAAAAEwAAAAD"
    "ABEAn3AAAABxAAAABADtAgGfcwAAAIYAAAAEAO0ABJ+lAAAAtQAAAAQA7QAEnwAAAAAAAAAA/////6QAAAAAAAAADAAAAAMAEQCfFwAAAEwA"
    "AAADABEAn4EAAACDAAAABADtAgGfgwAAAIYAAAAEAO0AA58AAAAAAAAAAADwAQ0uZGVidWdfYWJicmV2AREBJQ4TBQMOEBcbDhEBVRcAAAI0"
    "AAMOSRM6CzsLAhgAAAMBAUkTAAAEIQBJEzcLAAAFJAADDj4LCwsAAAYkAAMOCws+CwAABy4BEQESBkAYl0IZAw46CzsLJxlJEz8ZAAAIBQAC"
    "FwMOOgs7C0kTAAAJNAACFwMOOgs7C0kTAAAKCwFVFwAACwUAAhgDDjoLOwtJEwAADImCAQAxExEBAAANLgEDDjoLOwsnGTwZPxkAAA4FAEkT"
    "AAAPLgARARIGQBiXQhkDDjoLOwsnGUkTPxkAABAPAEkTAAARJgBJEwAAAADkAgsuZGVidWdfaW5mb1QBAAAEAAAAAAAEAXgAAAAdAEcAAAAA"
    "AAAAAAAAAAAAAAAwAAAAAikAAAA3AAAAARYFAwAAEAADQwAAAARKAAAAEAAFHQAAAAUEBmQAAAAIBwcDAAAAnwAAAAftAwAAAACfXgAAAAEH"
    "QwAAAAgAAAAAMAAAAAEHQwAAAAkgAAAAYgAAAAEIQwAAAAlZAAAAYAAAAAEIQwAAAAoAAAAACcoAAAAfAAAAAQlDAAAAAAAHpAAAAMIAAAAH"
    "7QMAAAAAnzIAAAABDkMAAAALBO0AAJ8hAAAAAQ5NAQAACBIBAAAXAAAAAQ5DAAAACTABAAA2AAAAAQ9DAAAAChgAAAAJdwEAADwAAAABEEMA"
    "AAAADCABAAC4AAAADCABAABjAQAAAA0+AAAAAQUOQwAAAAAPZwEAAAgAAAAH7QMAAAAAnyUAAAABF0gBAAAQQwAAABBSAQAAEUMAAAAAAF4N"
    "LmRlYnVnX3Jhbmdlc0MAAABzAAAAiQAAAJgAAAAAAAAAAAAAAK0AAACwAAAAwAAAAFsBAAAAAAAAAAAAAAMAAACiAAAApAAAAGYBAABnAQAA"
    "bwEAAAAAAAAAAAAAAJgBCi5kZWJ1Z19zdHIvaG9tZS91c2VyL3B5LXdhc21ob3N0AGNvdW50AGludABwdHIAZ2V0X2J1ZmZlcgBuAHN1bQB0"
    "b3RhbABpAGhvc3RfbG9nAC90bXAvdG1wdzNsdWZ5b2ovbGliLmMAZmliAGEAX19BUlJBWV9TSVpFX1RZUEVfXwBjbGFuZyB2ZXJzaW9uIDIx"
    "LjEuMAAAlwILLmRlYnVnX2xpbmUHAQAABAAuAAAAAQEB+w4NAAEBAQEAAAABAAABL3RtcC90bXB3M2x1ZnlvagAAbGliLmMAAQAAAAAFAgMA"
    "AAAYBRAKygUFBiADdy4GAwouBgN2IAYDCVgGA3fWAwlKA3cuAwmQBSGsA3cCLAEFBQMJSgN31gUhBgMJggYDd3QFBQMJggZ1AgMAAQEABQKk"
    "AAAAAw0BBRcKkgUFBiADcC4GAxEuBgNvZgYDEi4GA24gBgMQWAYDcNYDEAguA3DkBS4DEGYFKwiCA3CCBQUDEEoFIZAFBSADcFgGAxAuBgNw"
    "dAMQZgUuSgUrdANwWAUFAxBKBteDAgMAAQEFKgoABQJoAQAAAxYBAgcAAQEARwRuYW1lAAkIbGliLndhc20BIQQACGhvc3RfbG9nAQNmaWIC"
    "A3N1bQMKZ2V0X2J1ZmZlcgcSAQAPX19zdGFja19wb2ludGVyADUJcHJvZHVjZXJzAghsYW5ndWFnZQEDQzExAAxwcm9jZXNzZWQtYnkBBWNs"
    "YW5nBjIxLjEuMACGAQ90YXJnZXRfZmVhdHVyZXMHKw9idWxrLW1lbW9yeS1vcHQrFmNhbGwtaW5kaXJlY3Qtb3ZlcmxvbmcrDmV4dGVuZGVk"
    "LWNvbnN0KwptdWx0aXZhbHVlKw9tdXRhYmxlLWdsb2JhbHMrE25vbnRyYXBwaW5nLWZwdG9pbnQrCHNpZ24tZXh0"
)


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
    wasm = PREBUILT
    print(f"no Zig here (`pip install ziglang`): using the prebuilt wasm, {len(wasm)} bytes")

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
