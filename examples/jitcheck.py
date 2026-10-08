"""Rough JIT check: time a tight wasm loop on the active wasmhost backend."""

import time

import wasmhost

BODY = bytes.fromhex(
    "01017f"  # one i32 local
    "02400340"  # block, loop
    "2000450d01"  # if n == 0: break
    "200120006a2101"  # acc += n
    "200041016b2100"  # n -= 1
    "0c000b0b"  # continue; end loop; end block
    "20010b"  # return acc
)
WASM = (
    b"\0asm\1\0\0\0"
    + bytes([1, 6, 1, 0x60, 1, 0x7F, 1, 0x7F])
    + bytes([3, 2, 1, 0])
    + bytes([7, 5, 1, 1, 0x66, 0, 0])
    + bytes([10, len(BODY) + 2, 1, len(BODY)])
    + BODY
)

f = wasmhost.Instance(wasmhost.Module(WASM)).exports.f
N = 50_000_000
f(1000)  # warm up
t = time.perf_counter()
f(N)
dt = time.perf_counter() - t
rate = N / dt / 1e6
print(f"backend: {wasmhost.get_backend().name}")
print(f"{N:,} iterations in {dt:.2f}s = {rate:.0f} M iter/s")
print("JIT likely" if rate > 300 else "interpreter likely (no JIT)")
