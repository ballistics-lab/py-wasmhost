"""`python -m wasmhost bench`: the numbers are not tested (they are the machine's), that it runs and is right is."""

from __future__ import annotations

import pytest

import wasmhost
from wasmhost import _bench, _cli


def test_the_module_computes(session: str) -> None:
    ex = wasmhost.Instance(wasmhost.Module(_bench.MODULE)).exports
    assert (ex.add(2, 3), ex.fib(10), ex.loop(5)) == (5, 55, 30)  # 0 + 1 + 4 + 9 + 16


def test_bench_prints_a_row(capsys: pytest.CaptureFixture[str], session: str) -> None:
    args = ["bench", "--backend", session, "--fib", "10", "--loop", "1000", "--calls", "5", "--repeat", "1"]
    assert _cli.main(args) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("backend") and session in out.splitlines()[1]


def test_memory_transfer_is_measured(session: str) -> None:
    ex = wasmhost.Instance(wasmhost.Module(_bench.MEMORY_MODULE)).exports
    assert len(ex.memory) == 256 * 65536  # the module of the measurement is a memory of 16 MiB
    written, read = _bench.measure_memory(wasmhost.get_backend(), 64, 1)
    assert written > 0 and read > 0
