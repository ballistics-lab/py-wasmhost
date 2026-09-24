"""What a backend is: something that compiles, instantiates and runs WebAssembly modules.

The public API (`_api.py`) is the same on every backend; a backend supplies these primitives. They are
JavaScript engines (`_js.py`: one trip to the engine per primitive, or per batch) and native runtimes
(`_native.py`: wasm3, wasmtime; direct calls). ("Host" is the WebAssembly word for the embedder, the Python
side, which provides imports; the thing that runs the module is the backend.)

Handles are whatever the backend likes (an id in the engine, a runtime object). Values crossing this
boundary are Python `int` (i32, i64) and `float` (f32, f64); a backend raises the exceptions of
`_errors.py`, `IndexError` for a memory access out of bounds and `TypeError` for writing an immutable
global, whatever its runtime raises itself.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, NamedTuple, cast

from ._binary import FuncType
from ._errors import WasmError

__all__ = (
    "BatchResult",
    "CallStep",
    "Expr",
    "Backend",
    "HostFunction",
    "Operand",
    "ReadStep",
    "Step",
    "StopStep",
    "WriteStep",
    "check_results",
    "evaluate",
    "normalize",
)

# A step's number: a literal, or an expression over earlier results: ("ref", n), ("const", x), (op, a, b).
Expr = tuple[Any, ...]
Operand = int | float | Expr


class CallStep(NamedTuple):
    out: int  # the batch's result slot
    name: str
    args: tuple[Operand, ...]
    ftype: FuncType


class WriteStep(NamedTuple):
    memory: str
    offset: Operand
    data: bytes


class ReadStep(NamedTuple):
    out: int
    memory: str
    offset: Operand
    length: Operand


class StopStep(NamedTuple):
    value: Operand
    when_zero: bool  # stop if the value is 0 (else: if it isn't)


Step = CallStep | WriteStep | ReadStep | StopStep


class HostFunction(NamedTuple):
    """A function the module imports, and the Python callable that answers it."""

    module: str
    name: str
    ftype: FuncType
    fn: Callable[..., Any]


def check_results(values: Any, ftype: FuncType) -> list[int | float]:
    """What a host function returned, as the list of values its signature promises (else a TypeError).

    None for no result, a value for one, a tuple or list for several."""
    if not ftype.results:
        return []
    if values is None:
        raise TypeError(f"the host function should return {len(ftype.results)} value(s), not None")
    found: list[Any] = (
        list(cast("Sequence[Any]", values))
        if isinstance(values, (tuple, list)) and len(ftype.results) != 1
        else [values]
    )
    if len(found) != len(ftype.results):
        raise TypeError(f"the host function returned {len(found)} value(s), expected {len(ftype.results)}")
    out: list[int | float] = []
    for value, kind in zip(found, ftype.results, strict=True):
        if kind in ("i32", "i64"):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"the host function should return an int for an {kind}, not {type(value).__name__}")
        elif isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"the host function should return a number for an {kind}, not {type(value).__name__}")
        out.append(normalize(value, kind))
    return out


class BatchResult(NamedTuple):
    values: dict[int, Any]  # slot -> value (int, float, bytes; None for a call without a result), for steps that ran
    error: BaseException | None  # the first step that failed


def normalize(value: int | float, kind: str) -> int | float:
    """The value as WebAssembly (and JavaScript) take it: integers wrap to their width, floats are floats."""
    if kind == "i32":
        return ((int(value) + 0x80000000) & 0xFFFFFFFF) - 0x80000000
    if kind == "i64":
        return ((int(value) + 0x8000000000000000) & 0xFFFFFFFFFFFFFFFF) - 0x8000000000000000
    return float(value)


def evaluate(operand: Operand, values: dict[int, Any]) -> int | float:
    """An operand's value, given the results so far."""
    if not isinstance(operand, tuple):
        return operand
    tag = operand[0]
    if tag == "ref":
        return values[operand[1]]
    if tag == "const":
        return operand[1]
    left, right = evaluate(operand[1], values), evaluate(operand[2], values)
    return left + right if tag == "+" else left - right if tag == "-" else left * right


class Backend:
    """A runtime. Subclasses implement the primitives; `run_batch` has a sequential default."""

    name: str = "?"
    # What the runtime can't do is left out: "memory.grow" (Memory.grow from Python), "table.length", "imports"
    # (host functions: a Python callable the module calls).
    features: frozenset[str] = frozenset({"memory.grow", "table.length"})

    def supports(self, feature: str) -> bool:
        return feature in self.features

    def close(self) -> None:
        """Release the runtime. Safe to call more than once."""

    def validate(self, data: bytes) -> bool:
        raise NotImplementedError

    def compile(self, data: bytes) -> Any:
        raise NotImplementedError

    def instantiate(self, module: Any, imports: Sequence[HostFunction] = ()) -> Any:
        """A new instance. `imports` answers the module's function imports, in the order it declares them; a
        backend without the "imports" feature is only asked with none."""
        raise NotImplementedError

    def call(self, instance: Any, name: str, args: Sequence[int | float], ftype: FuncType) -> list[int | float]:
        """Call an export with already-checked arguments; the results, in order."""
        raise NotImplementedError

    def memory_size(self, instance: Any, name: str) -> int:
        raise NotImplementedError

    def memory_grow(self, instance: Any, name: str, pages: int) -> int:
        raise NotImplementedError

    def memory_read(self, instance: Any, name: str, offset: int, length: int) -> bytes:
        raise NotImplementedError

    def memory_write(self, instance: Any, name: str, offset: int, data: bytes) -> None:
        raise NotImplementedError

    def global_get(self, instance: Any, name: str, kind: str) -> int | float:
        raise NotImplementedError

    def global_set(self, instance: Any, name: str, kind: str, value: int | float) -> None:
        raise NotImplementedError

    def table_length(self, instance: Any, name: str) -> int:
        raise NotImplementedError

    def run_batch(self, instance: Any, steps: Sequence[Step]) -> BatchResult:
        """The steps in order, in Python. (A JavaScript engine does them in one trip instead.)"""
        values: dict[int, Any] = {}
        try:
            for step in steps:
                if isinstance(step, CallStep):
                    args = [
                        normalize(evaluate(a, values), k) for a, k in zip(step.args, step.ftype.params, strict=True)
                    ]
                    results = self.call(instance, step.name, args, step.ftype)
                    values[step.out] = results[0] if step.ftype.results else None
                elif isinstance(step, WriteStep):
                    self.memory_write(instance, step.memory, int(evaluate(step.offset, values)), step.data)
                elif isinstance(step, ReadStep):
                    offset, length = int(evaluate(step.offset, values)), int(evaluate(step.length, values))
                    values[step.out] = self.memory_read(instance, step.memory, offset, length)
                elif (evaluate(step.value, values) == 0) == step.when_zero:
                    break
        except (WasmError, IndexError, TypeError) as exc:
            return BatchResult(values, exc)
        return BatchResult(values, None)
