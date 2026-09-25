"""CoreMark on the backends wasmhost has, side by side.

    python examples/coremark.py [--backend NAME | --all]

Runs the CoreMark build of the wasm3 project (`examples/wasm/coremark-minimal.wasm`, from pywasm3's examples) with
`env.clock_ms`, its only import, answered by Python. `run()` calibrates by itself and then times a last pass, so it
takes 10 to 30 seconds per backend.

CoreMark only accepts a pass that took at least 10 seconds, otherwise `run()` returns 0.0. That is what happens on a
runtime much faster than the one this build was tuned on (wasmtime here: about 2.8 times faster than wasm3), so the
example also prints how long that last pass took: compare backends by it when the score is 0.0.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import wasmhost

WASM = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wasm", "coremark-minimal.wasm")


def coremark(backend: str | None) -> tuple[str, float, float]:
    """(the backend's name, CoreMark's score, seconds the last pass took)."""
    with open(WASM, "rb") as f:
        module = wasmhost.Module(f.read(), backend=backend)
    ticks: list[int] = []

    def clock_ms() -> int:
        ticks.append(int(time.monotonic() * 1000))
        return ticks[-1]

    instance = wasmhost.Instance(module, {"env": {"clock_ms": clock_ms}})
    score = instance.exports.run()
    return module._backend.name, score, (ticks[-1] - ticks[-2]) / 1000  # noqa: SLF001


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--backend", choices=sorted(wasmhost.BACKENDS), help="one backend (default: the first found)")
    parser.add_argument("--all", action="store_true", help="every backend that starts here and takes host functions")
    args = parser.parse_args()
    names: list[str | None] = [None]
    if args.all:
        names = []
        for name in wasmhost.AUTO_ORDER:
            try:
                backend = wasmhost.BACKENDS[name]()
            except Exception:  # noqa: BLE001 -- not available here
                continue
            if backend.supports("imports"):
                names.append(name)
            backend.close()
    elif args.backend:
        names = [args.backend]
    print(f"{'backend':10} {'score':>10} {'last pass':>10}")
    for name in names:
        try:
            used, score, seconds = coremark(name)
        except Exception as exc:  # noqa: BLE001
            print(f"{name or 'default':10} failed: {exc}", file=sys.stderr)
            continue
        note = "  (under 10 s: CoreMark does not score it)" if score == 0 else ""
        print(f"{used:10} {score:10.1f} {seconds:9.1f}s{note}", flush=True)


if __name__ == "__main__":
    main()
