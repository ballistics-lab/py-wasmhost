"""JavaScriptCore through its C API, no PyGObject: the `jsc` backend.

The same engine as `gi-jsc` (WebKitGTK's) and JSContext (iOS), driven the way the iOS backend drives it for bytes
and host functions, so this one can stand in for it on a desktop. It needs the library only:
`apt install libjavascriptcoregtk-4.1-0` on Linux; on macOS the system framework is used.
"""

from __future__ import annotations

import ctypes

from ._capi import CApi, load_library
from ._js import JSBackend, _check_webassembly  # pyright: ignore[reportPrivateUsage]

__all__ = ("JSCBackend",)


class JSCBackend(JSBackend):
    name = "jsc"
    features = JSBackend.features | {"imports"}

    def __init__(self) -> None:
        super().__init__()
        lib = load_library()  # OSError: not on this system
        create, release = lib.JSGlobalContextCreate, lib.JSGlobalContextRelease
        create.restype, create.argtypes = ctypes.c_void_p, [ctypes.c_void_p]
        release.restype, release.argtypes = None, [ctypes.c_void_p]
        self._lib = lib
        self._context: int | None = create(None)
        if not self._context:
            raise OSError("JSGlobalContextCreate failed")
        self._api = CApi(lib, self._context)
        self._capi = self._api  # for bytes and host functions; turned off if a fast path ever fails
        _check_webassembly(self)

    def evaluate(self, src: str) -> str:
        if self._context is None:
            raise RuntimeError("the JavaScriptCore context is closed")
        return self._api.evaluate(src)

    def close(self) -> None:
        if self._context is not None:
            self._lib.JSGlobalContextRelease(self._context)
            self._context = None
