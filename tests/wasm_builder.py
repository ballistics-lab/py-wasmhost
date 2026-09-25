"""A minimal WebAssembly assembler for the tests: no wat2wasm needed."""

from __future__ import annotations

from collections.abc import Sequence

I32, I64, F32, F64 = 0x7F, 0x7E, 0x7D, 0x7C

# a function: (type index, code) or (type index, code, [(count, valtype)] locals)
Func = tuple[int, bytes] | tuple[int, bytes, list[tuple[int, int]]]


def uleb(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def vec(items: list[bytes]) -> bytes:
    return uleb(len(items)) + b"".join(items)


def name(s: str) -> bytes:
    raw = s.encode()
    return uleb(len(raw)) + raw


def section(section_id: int, payload: bytes) -> bytes:
    return bytes([section_id]) + uleb(len(payload)) + payload


def functype(params: list[int], results: list[int]) -> bytes:
    return b"\x60" + vec([bytes([p]) for p in params]) + vec([bytes([r]) for r in results])


def body(code: bytes, locals_: list[tuple[int, int]] | None = None) -> bytes:
    """A function body; `locals_` is a list of (count, valtype)."""
    inner = vec([uleb(n) + bytes([t]) for n, t in locals_ or []]) + code + b"\x0b"
    return uleb(len(inner)) + inner


def module(
    types: list[bytes],
    funcs: Sequence[Func],
    exports: list[tuple[str, int, int]],
    memory: bool = False,
    globals_: list[bytes] | None = None,
    imports: list[bytes] | None = None,
) -> bytes:
    out = b"\0asm\x01\x00\x00\x00" + section(1, vec(types))
    if imports:
        out += section(2, vec(imports))
    out += section(3, vec([uleb(f[0]) for f in funcs]))
    if memory:
        out += section(5, vec([b"\x01\x01\x04"]))  # min 1 page, max 4
    if globals_:
        out += section(6, vec(globals_))
    out += section(7, vec([name(n) + bytes([kind]) + uleb(i) for n, kind, i in exports]))
    out += section(10, vec([body(f[1], f[2]) if len(f) == 3 else body(f[1]) for f in funcs]))
    return out


def arith() -> bytes:
    """add(i32, i32) -> i32, add64(i64, i64) -> i64, fadd(f64, f64) -> f64, half(f32) -> f32 (adds itself),
    trap(), dup(i32) -> (i32, i32), store8(ptr, val), grow(pages) -> i32, memory, counter (mutable i32 = 7),
    ten (immutable i32 = 11: its initialiser 41 0B 0B contains the end opcode as data)."""
    types = [
        functype([I32, I32], [I32]),  # 0
        functype([I64, I64], [I64]),  # 1
        functype([F64, F64], [F64]),  # 2
        functype([], []),  # 3
        functype([I32], [I32, I32]),  # 4
        functype([I32, I32], []),  # 5
        functype([I32], [I32]),  # 6
        functype([F32], [F32]),  # 7
    ]
    funcs = [
        (0, b"\x20\x00\x20\x01\x6a"),  # add
        (1, b"\x20\x00\x20\x01\x7c"),  # add64
        (2, b"\x20\x00\x20\x01\xa0"),  # fadd
        (3, b"\x00"),  # trap: unreachable
        (4, b"\x20\x00\x20\x00"),  # dup
        (5, b"\x20\x00\x20\x01\x3a\x00\x00"),  # store8: i32.store8 align=0 offset=0
        (6, b"\x20\x00\x40\x00"),  # grow: memory.grow
        (7, b"\x20\x00\x20\x00\x92"),  # halve... adds the f32 to itself
    ]
    globals_ = [
        b"\x7f\x01\x41\x07\x0b",  # mutable i32 = 7
        b"\x7f\x00\x41\x0b\x0b",  # immutable i32 = 11
    ]
    exports = [
        ("add", 0, 0),
        ("add64", 0, 1),
        ("fadd", 0, 2),
        ("trap", 0, 3),
        ("dup", 0, 4),
        ("store8", 0, 5),
        ("grow", 0, 6),
        ("twice", 0, 7),
        ("memory", 2, 0),
        ("counter", 3, 0),
        ("ten", 3, 1),
    ]
    return module(types, funcs, exports, memory=True, globals_=globals_)


def needs_import() -> bytes:
    """A module importing env.f: () -> (): it can't be instantiated without an import object."""
    types = [functype([], [])]
    imports = [name("env") + name("f") + b"\x00" + uleb(0)]
    return module(types, [(0, b"")], [("run", 0, 1)], imports=imports)


def callbacks() -> bytes:
    """A module that imports four host functions and calls them from its own exports:

    env.plus(i32, i32) -> i32        env.note(i64)            env.half(f64) -> f64      env.pair(i32) -> (i32, i32)
    call_plus(a, b) = plus(a, b)     call_note(x) = note(x)   call_half(x) = half(x)    call_pair(x) = pair(x)
    twice(a) = plus(plus(a, 1), 1)   two nested host calls
    """
    types = [
        functype([I32, I32], [I32]),  # 0 plus
        functype([I64], []),  # 1 note
        functype([F64], [F64]),  # 2 half
        functype([I32], [I32, I32]),  # 3 pair
        functype([I32], [I32]),  # 4 twice
    ]
    imports = [
        name("env") + name("plus") + b"\x00" + uleb(0),
        name("env") + name("note") + b"\x00" + uleb(1),
        name("env") + name("half") + b"\x00" + uleb(2),
        name("env") + name("pair") + b"\x00" + uleb(3),
    ]
    funcs = [  # function indices 4..8 (0..3 are the imports); `call` is 0x10
        (0, b"\x20\x00\x20\x01\x10\x00"),  # call_plus
        (1, b"\x20\x00\x10\x01"),  # call_note
        (2, b"\x20\x00\x10\x02"),  # call_half
        (3, b"\x20\x00\x10\x03"),  # call_pair
        (4, b"\x20\x00\x41\x01\x10\x00\x41\x01\x10\x00"),  # twice: plus(plus(a, 1), 1)
    ]
    exports = [
        ("call_plus", 0, 4),
        ("call_note", 0, 5),
        ("call_half", 0, 6),
        ("call_pair", 0, 7),
        ("twice", 0, 8),
    ]
    return module(types, funcs, exports, imports=imports)


def imports_memory() -> bytes:
    """A module that imports `env.memory` (what an Emscripten build does) and exports a function."""
    types = [functype([], [I32])]
    imports = [name("env") + name("memory") + b"\x02" + b"\x00\x01"]  # memory, limits: min 1 page
    return module(types, [(0, b"\x41\x00")], [("zero", 0, 0)], imports=imports)


def spinner() -> bytes:
    """spin() loops forever; quick() -> i32 returns 7; busy(n) -> i32 counts up to n in a loop and returns n."""
    types = [functype([], []), functype([], [I32]), functype([I32], [I32])]
    funcs: list[Func] = [
        (0, b"\x03\x40\x0c\x00\x0b"),  # spin: loop; br 0; end
        (1, b"\x41\x07"),  # quick: i32.const 7
        (
            2,
            # busy(n): local 1 = 0; loop { local1 += 1; br_if (local1 < n) } ; local1
            b"\x03\x40\x20\x01\x41\x01\x6a\x21\x01\x20\x01\x20\x00\x49\x0d\x00\x0b\x20\x01",
            [(1, I32)],
        ),
    ]
    return module(types, funcs, [("spin", 0, 0), ("quick", 0, 1), ("busy", 0, 2)])
