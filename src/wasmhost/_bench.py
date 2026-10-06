"""`python -m wasmhost bench`: how fast each backend is, to choose one (BACKLOG B-302, B-302a).

Two kinds of cost, kept apart: what a call from Python costs (a single call; a batch of three), and what the engine
does with a module (a recursive `fib` and a loop, integers only). Which one matters depends on the program: a few big
calls are about the engine, many small ones about the bridge. Each is the best of a few runs.
"""

from __future__ import annotations

import argparse
import os
import time
from collections.abc import Callable
from typing import Any

from ._api import Instance, Module, get_backend
from ._registry import AUTO_ORDER, BACKENDS

DESCRIPTION = "Time calls from Python and the engine's own speed on each backend."

# add(a, b), fib(n) (recursive) and loop(n) = the sum of i*i for i < n, all on i32; written out by hand.
MODULE = bytes.fromhex(
    "0061736d01000000010c0260017f017f60027f7f017f030403000001071403036669620000046c6f6f7000010361646400020a4d031c00"
    "2000410248047f200005200041016b1000200041026b10006a0b0b2601027f02400340200120004e0d012002200120016c6a2102200141"
    "016a21010c000b0b20020b0700200020016a0b"
)
# Engines that take their JIT off by an environment variable, read when the engine starts.
JSC_BACKENDS = ("jscontext", "jsc", "gi-jsc")


def _best(fn: Callable[[], object], repeat: int) -> float:
    times: list[float] = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return min(times)


def measure(backend: Any, *, fib: int, loop: int, calls: int, repeat: int) -> dict[str, float]:
    """The costs on `backend`: microseconds for `call` and `batch3`, milliseconds for `fib` and `loop`."""
    instance = Instance(Module(MODULE, backend=backend))
    ex = instance.exports
    add = ex.add
    add(1, 2)  # the first call may start things up

    def batch() -> None:
        b = instance.batch()
        b.call(add, 1, 2)
        b.call(add, 3, 4)
        b.call(add, 5, 6)
        b.run()

    def many(fn: Callable[[], object]) -> Callable[[], None]:
        def go() -> None:
            for _ in range(calls):
                fn()

        return go

    return {
        "call": _best(many(lambda: add(1, 2)), repeat) / calls * 1e6,
        "batch3": _best(many(batch), repeat) / calls * 1e6,
        "fib": _best(lambda: ex.fib(fib), repeat) * 1e3,
        "loop": _best(lambda: ex.loop(loop), repeat) * 1e3,
    }


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--backend", choices=sorted(BACKENDS), help="one backend (default: every one that starts)")
    parser.add_argument("--fib", type=int, default=27, metavar="N", help="n of the recursive fib (default 27)")
    parser.add_argument(
        "--loop", type=int, default=30_000_000, metavar="N", help="iterations of the loop (default 3e7)"
    )
    parser.add_argument("--calls", type=int, default=200, metavar="N", help="calls per timing of a call (default 200)")
    parser.add_argument("--repeat", type=int, default=3, metavar="N", help="runs of each, the best counts (default 3)")
    parser.add_argument(
        "--no-jit",
        action="store_true",
        help="JavaScriptCore without its JIT (JSC_useJIT=false: jscontext, jsc, gi-jsc; the others are not changed)",
    )


def run(args: argparse.Namespace) -> int:
    """The `bench` command: a table, one row per backend; 0 if one at least ran."""
    if args.no_jit:
        os.environ["JSC_useJIT"] = "false"  # read when the engine starts, so before any backend is made
    names = [args.backend] if args.backend else list(AUTO_ORDER)
    print(f"{'backend':10s} {'call us':>9s} {'batch3 us':>10s} {f'fib({args.fib}) ms':>12s} {'loop ms':>10s}")
    ran = 0
    for name in names:
        try:
            backend = get_backend(name)
        except Exception as exc:  # noqa: BLE001 -- not available here
            if args.backend:
                print(f"{name}: not available ({type(exc).__name__}: {exc})")
            continue
        label = f"{name}*" if args.no_jit and name in JSC_BACKENDS else name
        try:
            r = measure(backend, fib=args.fib, loop=args.loop, calls=args.calls, repeat=args.repeat)
        except Exception as exc:  # noqa: BLE001 -- e.g. a module the engine can't take
            print(f"{label}: failed ({type(exc).__name__}: {exc})")
            continue
        finally:
            backend.close()
        print(f"{label:10s} {r['call']:9.1f} {r['batch3']:10.1f} {r['fib']:12.1f} {r['loop']:10.1f}")
        ran += 1
    if args.no_jit:
        print("* without its JIT")
    return 0 if ran else 1
