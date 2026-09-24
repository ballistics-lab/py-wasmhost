"""JavaScriptCore's C API through ctypes.

The same functions on Linux (libjavascriptcoregtk, without PyGObject), macOS (JavaScriptCore.framework) and iOS
(objc_util's `c` finds them in the process). What it gives that Python-level bindings don't: a typed array filled
or read in place, and a JavaScript function that calls a Python callable, which is how a WebAssembly module gets
host functions.

`CApi(lib, context)`: `lib` is a ctypes library, or anything whose attributes are its functions (objc_util's `c`);
`context` is the JSGlobalContextRef, or a callable that gives it (an Objective-C proxy has to be asked).
"""

from __future__ import annotations

import ctypes
import ctypes.util
import sys
from collections.abc import Callable
from typing import Any

__all__ = ("CApi", "HostCallFailed", "load_library")

_P = ctypes.c_void_p
_N = ctypes.c_size_t
_K_UINT8 = 3  # kJSTypedArrayTypeUint8Array
# JSObjectCallAsFunctionCallback: (ctx, function, thisObject, argumentCount, arguments[], exception*) -> value
_CALLBACK = ctypes.CFUNCTYPE(_P, _P, _P, _P, _N, ctypes.POINTER(_P), ctypes.POINTER(_P))

_SIGNATURES: dict[str, tuple[Any, list[Any]]] = {
    "JSEvaluateScript": (_P, [_P, _P, _P, _P, ctypes.c_int, _P]),
    "JSStringCreateWithUTF8CString": (_P, [ctypes.c_char_p]),
    "JSStringRelease": (None, [_P]),
    "JSStringGetMaximumUTF8CStringSize": (_N, [_P]),
    "JSStringGetUTF8CString": (_N, [_P, ctypes.c_char_p, _N]),
    "JSValueToStringCopy": (_P, [_P, _P, _P]),
    "JSValueToNumber": (ctypes.c_double, [_P, _P, _P]),
    "JSValueMakeString": (_P, [_P, _P]),
    "JSValueMakeUndefined": (_P, [_P]),
    "JSValueProtect": (None, [_P, _P]),
    "JSValueUnprotect": (None, [_P, _P]),
    "JSValueToObject": (_P, [_P, _P, _P]),
    "JSContextGetGlobalObject": (_P, [_P]),
    "JSObjectSetProperty": (None, [_P, _P, _P, _P, ctypes.c_uint, _P]),
    "JSObjectMakeFunctionWithCallback": (_P, [_P, _P, _P]),
    "JSObjectMakeTypedArray": (_P, [_P, ctypes.c_int, _N, _P]),
    "JSObjectGetTypedArrayBytesPtr": (_P, [_P, _P, _P]),
    "JSObjectGetTypedArrayByteLength": (_N, [_P, _P, _P]),
}


class HostCallFailed(Exception):
    """Raised by a callback's handler to make the JavaScript function throw (the caller keeps the real error)."""


def load_library() -> Any:
    """The JavaScriptCore library of this system as a ctypes library, or OSError."""
    candidates: list[str | None] = []
    if sys.platform == "darwin":
        candidates.append("/System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore")
    for name in ("javascriptcoregtk-4.1", "javascriptcoregtk-6.0", "javascriptcoregtk-4.0", "JavaScriptCore"):
        candidates.append(ctypes.util.find_library(name))
    candidates += ["libjavascriptcoregtk-4.1.so.0", "libjavascriptcoregtk-6.0.so.1", "libjavascriptcoregtk-4.0.so.18"]
    errors: list[str] = []
    for candidate in candidates:
        if candidate:
            try:
                return ctypes.CDLL(candidate)
            except OSError as exc:
                errors.append(str(exc))
    raise OSError("no JavaScriptCore library found" + (f" ({errors[0]})" if errors else ""))


class CApi:
    def __init__(self, lib: Any, context: int | Callable[[], int]) -> None:
        for name, (result, args) in _SIGNATURES.items():
            function = getattr(lib, name)  # AttributeError: not this library
            function.restype, function.argtypes = result, args
        self._c = lib
        self._context = context
        self._callbacks: list[Any] = []  # the ctypes thunks must outlive the functions made from them

    @property
    def ref(self) -> int:
        ref: Any = self._context() if callable(self._context) else self._context
        ref = getattr(ref, "value", ref)  # a c_void_p, as objc_util returns it
        if not isinstance(ref, int) or not ref:
            raise OSError(f"no JSGlobalContextRef ({ref!r})")
        return ref

    # --- strings ---

    def _string(self, text: str) -> Any:
        return self._c.JSStringCreateWithUTF8CString(text.encode())

    def _text(self, ref: int, value: Any) -> str:
        js = self._c.JSValueToStringCopy(ref, value, None)
        if not js:
            return ""
        try:
            size = self._c.JSStringGetMaximumUTF8CStringSize(js)
            buffer = ctypes.create_string_buffer(size)
            self._c.JSStringGetUTF8CString(js, buffer, size)
        finally:
            self._c.JSStringRelease(js)
        return buffer.value.decode()

    def _make_string(self, ref: int, text: str) -> Any:
        js = self._string(text)
        try:
            return self._c.JSValueMakeString(ref, js)
        finally:
            self._c.JSStringRelease(js)

    # --- evaluation ---

    def evaluate(self, src: str) -> str:
        """The value of the last expression as a string; a JavaScript exception is a RuntimeError("[JS] ...")."""
        ref = self.ref
        script = self._string(src)
        exception = ctypes.c_void_p()
        try:
            value = self._c.JSEvaluateScript(ref, script, None, None, 1, ctypes.byref(exception))
        finally:
            self._c.JSStringRelease(script)
        if exception.value:
            raise RuntimeError(f"[JS] {self._text(ref, exception.value)}")
        return self._text(ref, value)

    # --- bytes ---

    def set_global_bytes(self, name: str, data: bytes) -> None:
        """`globalThis[name]` becomes a Uint8Array with these bytes, filled in place."""
        ref = self.ref
        array = self._c.JSObjectMakeTypedArray(ref, _K_UINT8, len(data), None)
        ptr = array and self._c.JSObjectGetTypedArrayBytesPtr(ref, array, None)
        if not ptr:
            raise OSError("typed array allocation failed")
        ctypes.memmove(ptr, data, len(data))
        js_name = self._string(name)
        try:
            self._c.JSObjectSetProperty(ref, self._c.JSContextGetGlobalObject(ref), js_name, array, 0, None)
        finally:
            self._c.JSStringRelease(js_name)

    def get_bytes(self, expr: str) -> bytes:
        """The bytes of the Uint8Array that `expr` evaluates to."""
        ref = self.ref
        script = self._string(f"(b => b.byteOffset ? b.slice() : b)({expr})")
        exception = ctypes.c_void_p()
        try:
            value = self._c.JSEvaluateScript(ref, script, None, None, 1, ctypes.byref(exception))
        finally:
            self._c.JSStringRelease(script)
        if exception.value or not value:
            raise OSError("JSEvaluateScript failed")
        self._c.JSValueProtect(ref, value)
        try:
            array = self._c.JSValueToObject(ref, value, None)
            ptr = array and self._c.JSObjectGetTypedArrayBytesPtr(ref, array, None)
            if not ptr:
                raise OSError("not a typed array")
            return ctypes.string_at(ptr, self._c.JSObjectGetTypedArrayByteLength(ref, array, None))
        finally:
            self._c.JSValueUnprotect(ref, value)

    # --- a JavaScript function that calls Python ---

    def set_function(self, name: str, handler: Callable[[list[Any]], str], error: str) -> None:
        """`globalThis[name](...)` calls `handler` with its arguments (numbers as floats, the rest as strings) and
        returns the string it returns. If the handler raises anything, the JavaScript function throws the string
        `error` (the caller keeps the real exception): an exception must not cross the C boundary."""
        ref = self.ref
        c = self._c

        def thunk(context: Any, function: Any, this: Any, argc: int, argv: Any, exception: Any) -> Any:
            try:
                args: list[Any] = []
                for i in range(argc):
                    args.append(self._text(context, argv[i]))
                return self._make_string(context, handler(args))
            except BaseException:  # noqa: BLE001 -- see above
                exception[0] = self._make_string(context, error)
                return c.JSValueMakeUndefined(context)

        callback = _CALLBACK(thunk)
        self._callbacks.append(callback)
        js_name = self._string(name)
        try:
            function = c.JSObjectMakeFunctionWithCallback(ref, js_name, ctypes.cast(callback, _P))
            c.JSObjectSetProperty(ref, c.JSContextGetGlobalObject(ref), js_name, function, 0, None)
        finally:
            c.JSStringRelease(js_name)
