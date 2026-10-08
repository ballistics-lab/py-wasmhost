"""WASI hosts, one module for each version of the interface: `preview1` is WASI 0.1 (`wasi_snapshot_preview1`, and
the first snapshot, `wasi_unstable`).

    from wasmhost.wasi import Preview1                  # the host of WASI 0.1, as its name says
    from wasmhost.wasi import preview1                # the module: its tables, `SIGNATURES`, `WasiExit`...

A class is exported under a name that says its version (`Preview1`), so a later `Wasip2` cannot be taken for it.
"""

from . import preview1
from .preview1 import Preview1

__all__ = ("Preview1", "preview1")
