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


def imports_global(valtype: int = I32, mutable: bool = True) -> bytes:
    """Imports the global env.g; exports get() -> valtype (and, for a mutable i32, inc(), which adds 1 to it)."""
    types = [functype([], [valtype]), functype([], [])]
    imports = [name("env") + name("g") + b"\x03" + bytes([valtype, 1 if mutable else 0])]
    funcs: list[Func] = [(0, b"\x23\x00")]  # get: global.get 0
    exports = [("get", 0, 0)]
    if mutable and valtype == I32:
        funcs.append((1, b"\x23\x00\x41\x01\x6a\x24\x00"))  # inc: global.set 0 (global.get 0 + 1)
        exports.append(("inc", 0, 1))
    return module(types, funcs, exports, imports=imports)


def uses_memory() -> bytes:
    """Imports the memory env.memory (min 1 page); exports load(addr) -> i32 (a byte) and store(addr, byte)."""
    types = [functype([I32], [I32]), functype([I32, I32], [])]
    imports = [name("env") + name("memory") + b"\x02" + b"\x00\x01"]
    funcs: list[Func] = [
        (0, b"\x20\x00\x2d\x00\x00"),  # load: i32.load8_u
        (1, b"\x20\x00\x20\x01\x3a\x00\x00"),  # store: i32.store8
    ]
    return module(types, funcs, [("load", 0, 0), ("store", 0, 1)], imports=imports)


def tables(imported: bool) -> bytes:
    """A table of two entries, imported as env.table or made by the module and exported as "table"; the module
    exports add(a, b) and mul(a, b), and call(a, b, index) = table[index](a, b) (call_indirect)."""
    types = [functype([I32, I32], [I32]), functype([I32, I32, I32], [I32])]
    funcs: list[Func] = [
        (0, b"\x20\x00\x20\x01\x6a"),  # add
        (0, b"\x20\x00\x20\x01\x6c"),  # mul
        (1, b"\x20\x00\x20\x01\x20\x02\x11\x00\x00"),  # call: call_indirect (type 0) table 0
    ]
    exports = [("add", 0, 0), ("mul", 0, 1), ("call", 0, 2)]
    out = b"\0asm\x01\x00\x00\x00" + section(1, vec(types))
    if imported:
        out += section(2, vec([name("env") + name("table") + b"\x01\x70\x00\x02"]))
    out += section(3, vec([uleb(f[0]) for f in funcs]))
    if not imported:
        out += section(4, vec([b"\x70\x00\x02"]))
        exports.append(("table", 1, 0))
    out += section(7, vec([name(n) + bytes([kind]) + uleb(i) for n, kind, i in exports]))
    out += section(10, vec([body(f[1]) for f in funcs]))
    return out


def swaps() -> bytes:
    """Multi-value: swap(i32, i32) -> (i32, i32), and env.swap(i32, i64) -> (i64, i32) imported and re-exported as
    swap_imported."""
    types = [functype([I32, I32], [I32, I32]), functype([I32, I64], [I64, I32])]
    imports = [name("env") + name("swap") + b"\x00" + uleb(1)]
    funcs: list[Func] = [
        (0, b"\x20\x01\x20\x00"),  # swap: local.get 1, local.get 0
        (1, b"\x20\x00\x20\x01\x10\x00"),  # swap_imported: local.get 0, local.get 1, call env.swap
    ]
    return module(types, funcs, [("swap", 0, 1), ("swap_imported", 0, 2)], imports=imports)


def dyn_callback() -> bytes:
    """The shape of what Emscripten's dynamic linking asks of a host (pywasm3's dyn_callback test): imports
    env.pass_fptr(i32) and the global env.__table_base (i32); its exported table holds f2 (add) and f3 (mul) from that
    base; run_test() calls pass_fptr(base) and pass_fptr(base + 1), call_pass_fptr(p) = pass_fptr(p),
    dynCall_iii(fptr, a, b) = table[fptr](a, b)."""
    types = [
        functype([I32, I32], [I32]),  # 0: the entries of the table
        functype([], []),  # 1
        functype([I32], []),  # 2: pass_fptr
        functype([I32, I32, I32], [I32]),  # 3: dynCall_iii
    ]
    imports = [
        name("env") + name("pass_fptr") + b"\x00" + uleb(2),
        name("env") + name("__table_base") + b"\x03\x7f\x00",  # global i32, immutable
    ]
    funcs: list[Func] = [  # function 0 is the imported pass_fptr
        (1, b"\x23\x00\x10\x00\x23\x00\x41\x01\x6a\x10\x00"),  # 1 run_test
        (0, b"\x20\x00\x20\x01\x6a"),  # 2 f2
        (0, b"\x20\x00\x20\x01\x6c"),  # 3 f3
        (2, b"\x20\x00\x10\x00"),  # 4 call_pass_fptr
        (3, b"\x20\x01\x20\x02\x20\x00\x11\x00\x00"),  # 5 dynCall_iii: call_indirect (type 0) table 0
    ]
    exports = [("run_test", 0, 1), ("call_pass_fptr", 0, 4), ("dynCall_iii", 0, 5), ("table", 1, 0)]
    out = b"\0asm\x01\x00\x00\x00" + section(1, vec(types)) + section(2, vec(imports))
    out += section(3, vec([uleb(f[0]) for f in funcs]))
    out += section(4, vec([b"\x70\x00\x02"]))  # one table, funcref, min 2
    out += section(7, vec([name(n) + bytes([kind]) + uleb(i) for n, kind, i in exports]))
    out += section(9, vec([b"\x00\x23\x00\x0b" + vec([uleb(2), uleb(3)])]))  # elem at global.get 0: f2, f3
    out += section(10, vec([body(f[1]) for f in funcs]))
    return out
