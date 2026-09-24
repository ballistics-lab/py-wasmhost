"""Just enough of the WebAssembly binary format to know the types of a module's imports and exports.

The JavaScript API can't tell them (`WebAssembly.Module.exports()` has names and kinds, no signatures),
and they matter: an `i64` argument must reach JavaScript as a BigInt while an `i32` must not, and a
result is an integer or a float depending on its type. Only the type, import, function, table, memory,
global and export sections are read; the rest is skipped, and code is never looked at.
"""

from __future__ import annotations

from typing import Final, NamedTuple

__all__ = ("ExportDescriptor", "FuncType", "ImportDescriptor", "ModuleInfo", "parse")

VALTYPES: Final = {
    0x7F: "i32",
    0x7E: "i64",
    0x7D: "f32",
    0x7C: "f64",
    0x7B: "v128",
    0x70: "funcref",
    0x6F: "externref",
}
KINDS: Final = ("function", "table", "memory", "global", "tag")


class FuncType(NamedTuple):
    params: tuple[str, ...]
    results: tuple[str, ...]


class ImportDescriptor(NamedTuple):
    module: str
    name: str
    kind: str
    type: FuncType | str | None  # a function's signature, a global's value type, else None


class ExportDescriptor(NamedTuple):
    name: str
    kind: str  # "function" | "table" | "memory" | "global" | "tag"
    type: FuncType | str | None


class ModuleInfo(NamedTuple):
    imports: tuple[ImportDescriptor, ...]
    exports: tuple[ExportDescriptor, ...]


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

    def limits(self) -> None:
        flags = self.u32()
        self.u32()
        if flags & 1:
            self.u32()

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

    def valtype(self) -> str:
        return VALTYPES.get(b := self.byte(), f"0x{b:02x}")


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
    global_types: list[str] = []  # value type of every global: imported ones first
    imports: list[ImportDescriptor] = []
    raw_exports: list[tuple[str, int, int]] = []
    try:
        while r.pos < len(wasm):
            section_id = r.byte()
            size = r.u32()
            end = r.pos + size
            if section_id == 1:
                for _ in range(r.u32()):
                    if r.byte() != 0x60:
                        raise ValueError("unsupported type form")
                    params = tuple(r.valtype() for _ in range(r.u32()))
                    results = tuple(r.valtype() for _ in range(r.u32()))
                    types.append(FuncType(params, results))
            elif section_id == 2:
                for _ in range(r.u32()):
                    module, name, kind = r.name(), r.name(), r.byte()
                    desc: FuncType | str | None = None
                    if kind == 0:
                        func_types.append(index := r.u32())
                        desc = types[index]
                    elif kind == 1:
                        r.byte()
                        r.limits()
                    elif kind == 2:
                        r.limits()
                    elif kind == 3:
                        global_types.append(desc := r.valtype())
                        r.byte()
                    elif kind == 4:
                        r.byte()
                        r.u32()
                    else:
                        raise ValueError(f"unknown import kind {kind}")
                    imports.append(ImportDescriptor(module, name, KINDS[kind], desc))
            elif section_id == 3:
                func_types.extend(r.u32() for _ in range(r.u32()))
            elif section_id == 6:
                for _ in range(r.u32()):
                    global_types.append(r.valtype())
                    r.byte()
                    r.skip_const_expr()
            elif section_id == 7:
                for _ in range(r.u32()):
                    raw_exports.append((r.name(), r.byte(), r.u32()))
            r.pos = end
    except IndexError as exc:
        raise ValueError("truncated WebAssembly binary") from exc
    exports: list[ExportDescriptor] = []
    for name, kind, index in raw_exports:
        desc = None
        if kind == 0:
            desc = types[func_types[index]]
        elif kind == 3:
            desc = global_types[index]
        exports.append(ExportDescriptor(name, KINDS[kind], desc))
    return ModuleInfo(tuple(imports), tuple(exports))
