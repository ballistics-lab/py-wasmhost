"""Just enough of the WebAssembly binary format to know the types of a module's imports and exports.

The JavaScript API can't tell them (`WebAssembly.Module.exports()` has names and kinds, no signatures),
and they matter: an `i64` argument must reach JavaScript as a BigInt while an `i32` must not, and a
result is an integer or a float depending on its type. Only the type, import, function, table, memory,
global and export sections are read, and the custom sections are kept (`Module.customSections`); the rest is
skipped, and code is never looked at.

The types are the ones of the JavaScript API's type reflection (`memory.type()` and the `type` of an entry of
`WebAssembly.Module.imports()`), with the same names: a function's `parameters` and `results`, a memory's `minimum`,
`maximum` and `shared`, a table's `element`, `minimum` and `maximum`, a global's `value` and `mutable`.
"""

from __future__ import annotations

from typing import Final, NamedTuple

__all__ = (
    "ExportDescriptor",
    "FuncType",
    "GlobalType",
    "ImportDescriptor",
    "MemoryType",
    "ModuleInfo",
    "TableType",
    "ValueType",
    "f32",
    "f64",
    "i32",
    "i64",
    "parse",
)


class ValueType(str):
    """A WebAssembly value type: `i32`, `i64`, `f32` or `f64` (and `v128`, `funcref`, `externref`).

    It is a `str`, so it equals its name (`i32 == "i32"`) and a name does for it anywhere (`FuncType(("i32",), ())`);
    its `repr` is the bare name, so a type reads `FuncType(parameters=(i32, i32), results=(i32,))`. `i64` is what
    JavaScript takes as a `BigInt`, which is an `int` here, as is every integer type."""

    __slots__ = ()

    def __repr__(self) -> str:
        return str(self)


i32: Final = ValueType("i32")
i64: Final = ValueType("i64")
f32: Final = ValueType("f32")
f64: Final = ValueType("f64")
VALTYPES: Final = {
    0x7F: i32,
    0x7E: i64,
    0x7D: f32,
    0x7C: f64,
    0x7B: ValueType("v128"),
    0x70: ValueType("funcref"),
    0x6F: ValueType("externref"),
}
KINDS: Final = ("function", "table", "memory", "global", "tag")


class FuncType(NamedTuple):
    """A function's type: `parameters` and `results` are value types (`"i32"`, `"f64"`, ...)."""

    parameters: tuple[str, ...]
    results: tuple[str, ...]

    @property
    def params(self) -> tuple[str, ...]:
        """What `parameters` was called here before it was called what the JavaScript API calls it."""
        return self.parameters


class MemoryType(NamedTuple):
    """A memory's type, in 64 KiB pages; `maximum` is None when it has none."""

    minimum: int
    maximum: int | None = None
    shared: bool = False


class TableType(NamedTuple):
    """A table's type: `element` is `"funcref"` or `"externref"`; `maximum` is None when it has none."""

    element: str
    minimum: int
    maximum: int | None = None


class GlobalType(NamedTuple):
    """A global's type: its value type (`"i32"`, ...) and whether it can be written."""

    value: str
    mutable: bool


class ImportDescriptor(NamedTuple):
    module: str
    name: str
    kind: str  # "function" | "table" | "memory" | "global" | "tag"
    type: FuncType | TableType | MemoryType | GlobalType | None  # None for a tag


class ExportDescriptor(NamedTuple):
    name: str
    kind: str  # "function" | "table" | "memory" | "global" | "tag"
    type: FuncType | TableType | MemoryType | GlobalType | None  # None for a tag


class ModuleInfo(NamedTuple):
    imports: tuple[ImportDescriptor, ...]
    exports: tuple[ExportDescriptor, ...]
    custom: tuple[tuple[str, bytes], ...] = ()  # the custom sections (name, contents), in order


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def byte(self) -> int:
        b = self.data[self.pos]
        self.pos += 1
        return b

    def u32(self) -> int:
        result = shift = 0
        while True:
            b = self.byte()
            result |= (b & 0x7F) << shift
            if not b & 0x80:
                return result
            shift += 7

    def name(self) -> str:
        n = self.u32()
        s = self.data[self.pos : self.pos + n].decode("utf-8")
        self.pos += n
        return s

    def limits(self) -> tuple[int, int | None, bool]:
        """(minimum, maximum or None, shared). Bit 0 of the flags has a maximum, bit 1 is shared, bit 2 is a 64-bit
        index (the numbers are read the same), bit 3 a page size of its own (skipped)."""
        flags = self.u32()
        minimum = self.u32()
        maximum = self.u32() if flags & 1 else None
        if flags & 8:
            self.u32()
        return minimum, maximum, bool(flags & 2)

    def skip_leb(self) -> None:
        while self.byte() & 0x80:
            pass

    def skip_const_expr(self) -> None:
        """Past a constant expression: `i32.const 11` is 41 0B 0B, so the first 0x0B isn't always the end."""
        while (op := self.byte()) != 0x0B:
            if op in (0x41, 0x42, 0x23, 0xD2):  # i32.const, i64.const, global.get, ref.func
                self.skip_leb()
            elif op == 0x43:  # f32.const
                self.pos += 4
            elif op == 0x44:  # f64.const
                self.pos += 8
            elif op == 0xD0:  # ref.null
                self.pos += 1
            # anything else (the extended-constant arithmetic, e.g. i32.add) has no immediate

    def valtype(self) -> ValueType:
        return VALTYPES.get(b := self.byte()) or ValueType(f"0x{b:02x}")


def parse(wasm: bytes) -> ModuleInfo:
    """Read the imports and exports of `wasm`. Raises ValueError when it isn't a WebAssembly binary."""
    if wasm[:4] != b"\0asm":
        raise ValueError("not a WebAssembly binary (bad magic)")
    if wasm[4:8] != b"\x01\x00\x00\x00":
        raise ValueError("unsupported WebAssembly version" if len(wasm) >= 8 else "truncated WebAssembly binary")
    r = _Reader(wasm)
    r.pos = 8
    types: list[FuncType] = []
    func_types: list[int] = []  # type index of every function: imported ones first
    table_types: list[TableType] = []  # of every table, memory and global: imported ones first
    memory_types: list[MemoryType] = []
    global_types: list[GlobalType] = []
    imports: list[ImportDescriptor] = []
    raw_exports: list[tuple[str, int, int]] = []
    custom: list[tuple[str, bytes]] = []

    def table_type() -> TableType:
        element = r.valtype()
        minimum, maximum, _ = r.limits()
        return TableType(element, minimum, maximum)

    def memory_type() -> MemoryType:
        minimum, maximum, shared = r.limits()
        return MemoryType(minimum, maximum, shared)

    try:
        while r.pos < len(wasm):
            section_id = r.byte()
            size = r.u32()
            end = r.pos + size
            if section_id == 0:
                name = r.name()
                if end > len(wasm) or r.pos > end:
                    raise ValueError("truncated WebAssembly binary")
                custom.append((name, wasm[r.pos : end]))
            elif section_id == 1:
                for _ in range(r.u32()):
                    if r.byte() != 0x60:
                        raise ValueError("unsupported type form")
                    params = tuple(r.valtype() for _ in range(r.u32()))
                    results = tuple(r.valtype() for _ in range(r.u32()))
                    types.append(FuncType(params, results))
            elif section_id == 2:
                for _ in range(r.u32()):
                    module, name, kind = r.name(), r.name(), r.byte()
                    desc: FuncType | TableType | MemoryType | GlobalType | None = None
                    if kind == 0:
                        func_types.append(index := r.u32())
                        desc = types[index]
                    elif kind == 1:
                        table_types.append(desc := table_type())
                    elif kind == 2:
                        memory_types.append(desc := memory_type())
                    elif kind == 3:
                        value = r.valtype()
                        global_types.append(desc := GlobalType(value, r.byte() == 1))
                    elif kind == 4:
                        r.byte()
                        r.u32()
                    else:
                        raise ValueError(f"unknown import kind {kind}")
                    imports.append(ImportDescriptor(module, name, KINDS[kind], desc))
            elif section_id == 3:
                func_types.extend(r.u32() for _ in range(r.u32()))
            elif section_id == 4:
                for _ in range(r.u32()):
                    if r.data[r.pos] == 0x40:  # a table with its own initial value: 0x40 0x00 type limits expression
                        r.pos += 2
                        table_types.append(table_type())
                        r.skip_const_expr()
                    else:
                        table_types.append(table_type())
            elif section_id == 5:
                memory_types.extend(memory_type() for _ in range(r.u32()))
            elif section_id == 6:
                for _ in range(r.u32()):
                    value = r.valtype()
                    global_types.append(GlobalType(value, r.byte() == 1))
                    r.skip_const_expr()
            elif section_id == 7:
                for _ in range(r.u32()):
                    raw_exports.append((r.name(), r.byte(), r.u32()))
            r.pos = end
    except IndexError as exc:
        raise ValueError("truncated WebAssembly binary") from exc
    exports: list[ExportDescriptor] = []
    for name, kind, index in raw_exports:
        found: FuncType | TableType | MemoryType | GlobalType | None = None
        if kind == 0:
            found = types[func_types[index]]
        elif kind == 1:
            found = table_types[index]
        elif kind == 2:
            found = memory_types[index]
        elif kind == 3:
            found = global_types[index]
        exports.append(ExportDescriptor(name, KINDS[kind], found))
    return ModuleInfo(tuple(imports), tuple(exports), tuple(custom))
