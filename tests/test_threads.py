"""One backend takes one call at a time (a lock of its own); two backends do not wait for each other."""

from __future__ import annotations

import threading

import wasm_builder as wb

import wasmhost


def test_threads_in_one_backend_get_right_answers(session: str) -> None:
    ex = wasmhost.Instance(wasmhost.Module(wb.arith())).exports
    errors: list[BaseException] = []

    def work(seed: int) -> None:
        try:
            for i in range(200):
                assert ex.add(seed, i) == seed + i
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(n * 1000,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors[0]


def test_two_backends_do_not_share_a_lock(session: str) -> None:
    first = wasmhost.get_backend()
    second = type(first)()
    try:
        assert first._lock is not second._lock  # pyright: ignore[reportPrivateUsage]
        with first._lock:  # pyright: ignore[reportPrivateUsage]
            done: list[bool] = []
            t = threading.Thread(target=lambda: done.append(second.validate(wb.arith())))
            t.start()
            t.join(timeout=10)
            assert done == [True]  # the other backend answered while this one was held
    finally:
        second.close()
