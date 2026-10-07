"""Rough JIT check: time a tight wasm loop on the active wasmhost backend."""
import time
import wasmhost

BODY = bytes([
    1, 1, 0x7F,                    # one i32 local
    0x02, 0x40, 0x03, 0x40,        # block, loop
    0x20, 0, 0x45, 0x0D, 1,        # if n == 0: break
    0x20, 1, 0x20, 0, 0x6A, 0x21, 1,  # acc += n
    0x20, 0, 0x41, 1, 0x6B, 0x21, 0,  # n -= 1
    0x0C, 0, 0x0B, 0x0B,           # continue; end loop; end block
    0x20, 1, 0x0B,                 # return acc
])
WASM = (b"\0asm\1\0\0\0" + bytes([1, 6, 1, 0x60, 1, 0x7F, 1, 0x7F])
        + bytes([3, 2, 1, 0]) + bytes([7, 5, 1, 1, 0x66, 0, 0])
        + bytes([10, len(BODY) + 2, 1, len(BODY)]) + BODY)

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
