"""A call of a function whose type was given by hand, checked by the engine (BACKLOG B-201, step 4).

A JavaScript engine does not say what a function it found in a table is, so the signature of such a `Function` is
whatever the caller set. To make a wrong one an error and not nonsense, the call goes through a tiny module made
here: it imports a one-entry table and has one function of exactly the given type, which does `call_indirect` on
that entry. The function is put into the entry and the module's function is called; the engine compares the type of
the entry with the one in the module and refuses a mismatch *before* the function runs (a trap "signature
mismatch"), which is turned into a `TypeError`.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ._binary import FuncType
from ._errors import Trap

if TYPE_CHECKING:
    from ._backend import Backend

_CODES = {"i32": 0x7F, "i64": 0x7E, "f32": 0x7D, "f64": 0x7C}
# What the engines call it: V8 "null function or function signature mismatch", JavaScriptCore "signature mismatch",
# wasmtime "indirect call type mismatch".
_MISMATCH = re.compile(r"signature mismatch|type mismatch|signature that does not match", re.IGNORECASE)


def _uleb(n: int) -> bytes:
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _section(section_id: int, payload: bytes) -> bytes:
    return bytes([section_id]) + _uleb(len(payload)) + payload


def _name(text: str) -> bytes:
    raw = text.encode()
    return _uleb(len(raw)) + raw


def module_for(ftype: FuncType) -> bytes:
    """The module: imports env.t (a funcref table of one entry), exports call(ftype) = call_indirect t[0] (ftype)."""
    params = bytes(_CODES[k] for k in ftype.parameters)
    results = bytes(_CODES[k] for k in ftype.results)
    functype = b"\x60" + _uleb(len(params)) + params + _uleb(len(results)) + results
    body = b"".join(b"\x20" + _uleb(i) for i in range(len(params))) + b"\x41\x00\x11\x00\x00\x0b"
    code = b"\x00" + body  # no locals
    return (
        b"\0asm\x01\x00\x00\x00"
        + _section(1, b"\x01" + functype)
        + _section(2, b"\x01" + _name("env") + _name("t") + b"\x01\x70\x00\x01")  # a funcref table, at least 1
        + _section(3, b"\x01\x00")
        + _section(7, b"\x01" + _name("call") + b"\x00\x00")
        + _section(10, b"\x01" + _uleb(len(code)) + code)
    )


def call_checked(backend: Backend, handle: Any, args: list[Any], ftype: FuncType, label: str) -> list[Any]:
    """Call `handle` as a function of `ftype`, the engine checking the type; a `TypeError` if it is another one."""
    from ._api import Instance, Module, Table  # here: _api imports this module

    cache: dict[FuncType, list[Any]] = backend.__dict__.setdefault("_trampolines", {})
    if ftype not in cache:
        table = Table("funcref", 1, backend=backend)
        instance = Instance(Module(module_for(ftype), backend=backend), {"env": {"t": table}})
        cache[ftype] = [table, instance.exports.call, None]  # the entry holds the last function put in it
    entry = cache[ftype]
    table, call = entry[0], entry[1]
    if entry[2] != handle:  # one trip less when the same function is called again
        backend.table_set(table._handle, 0, handle)
        entry[2] = handle
    try:
        return backend.call_ref(call._h, args, ftype)
    except Trap as exc:
        if _MISMATCH.search(str(exc)):
            raise TypeError(f"{label} is not a function of {ftype}: the engine refused the call ({exc})") from exc
        raise
