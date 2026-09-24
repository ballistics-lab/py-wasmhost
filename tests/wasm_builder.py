"""A minimal WebAssembly assembler for the tests: no wat2wasm needed."""

from __future__ import annotations

I32, I64, F32, F64 = 0x7F, 0x7E, 0x7D, 0x7C


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


def body(code: bytes) -> bytes:
    """A function body with no locals."""
    inner = uleb(0) + code + b"\x0b"
    return uleb(len(inner)) + inner


def module(
    types: list[bytes],
    funcs: list[tuple[int, bytes]],
    exports: list[tuple[str, int, int]],
    memory: bool = False,
    globals_: list[bytes] | None = None,
    imports: list[bytes] | None = None,
) -> bytes:
    out = b"\0asm\x01\x00\x00\x00" + section(1, vec(types))
    if imports:
        out += section(2, vec(imports))
    out += section(3, vec([uleb(t) for t, _ in funcs]))
    if memory:
        out += section(5, vec([b"\x01\x01\x04"]))  # min 1 page, max 4
    if globals_:
        out += section(6, vec(globals_))
    out += section(7, vec([name(n) + bytes([kind]) + uleb(i) for n, kind, i in exports]))
    out += section(10, vec([body(code) for _, code in funcs]))
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
