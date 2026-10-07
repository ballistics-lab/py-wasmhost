"""The exceptions, named after the JavaScript WebAssembly API's."""

from __future__ import annotations

__all__ = ("CompileError", "LinkError", "Timeout", "Trap", "WasmError")


class WasmError(Exception):
    """Base of the errors below."""


class CompileError(WasmError):
    """`WebAssembly.CompileError`: the bytes are not a valid module."""


class LinkError(WasmError):
    """`WebAssembly.LinkError`: the imports don't match the module."""


class Trap(WasmError, RuntimeError):
    """`WebAssembly.RuntimeError`: the code trapped (unreachable, out of bounds, division by zero, ...)."""


class Timeout(Trap):
    """The code was stopped because its call ran longer than the instance's `timeout` (not in the JavaScript API). A
    `Trap`, so a handler of traps takes it; the instance can be called again."""
