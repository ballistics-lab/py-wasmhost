"""Node and Bun move a big buffer through a file of a private directory, not over the pipe (BACKLOG B-302)."""

from __future__ import annotations

import os
import stat
from typing import Any

import pytest
import wasm_builder as wb

import wasmhost
from wasmhost import _js

MiB = 1 << 20


@pytest.fixture
def node(session: str) -> wasmhost._js.NodeBackend:
    backend = wasmhost.get_backend()
    if not isinstance(backend, _js.NodeBackend):
        pytest.skip("only node and bun move memory through a side file")
    return backend


def _pattern(size: int) -> bytes:
    return bytes((i * 7 + i // 251) % 256 for i in range(size))


def _calls(backend: Any) -> list[str]:
    """Records the engine functions that move memory, to see which way each buffer went."""
    seen: list[str] = []
    original = backend._o

    def spy(fn: str, obj: int, *args: object) -> str:
        if fn in ("readB64", "writeB64", "readToFile", "writeFromFile"):
            seen.append(fn)
        return original(fn, obj, *args)

    backend._o = spy
    return seen


@pytest.mark.parametrize("size", [1, 16 * 1024 - 1, 16 * 1024, 16 * 1024 + 1, 300_000, MiB, 8 * MiB])
def test_round_trip_at_every_size(node: wasmhost._js.NodeBackend, size: int) -> None:
    mem = wasmhost.Memory(256)  # 16 MiB
    data = _pattern(size)
    mem.write(13, data)
    assert mem.read(13, size) == data
    assert mem.read(13 + size // 2, size - size // 2) == data[size // 2 :]  # a read from the middle
    assert mem.read(0, 13) == bytes(13)  # what was not written is still zero


def test_the_threshold_picks_the_way(node: wasmhost._js.NodeBackend) -> None:
    mem = wasmhost.Memory(256)
    seen = _calls(node)
    small, big = _pattern(node._side_min - 1), _pattern(node._side_min)
    mem.write(0, small)
    mem.write(0, big)
    mem.read(0, len(small))
    mem.read(0, len(big))
    assert seen == ["writeB64", "writeFromFile", "readB64", "readToFile"]


def test_a_buffer_is_taken_whole_when_it_overwrites_a_longer_old_one(node: wasmhost._js.NodeBackend) -> None:
    mem = wasmhost.Memory(256)
    mem.write(0, _pattern(2 * MiB))
    mem.write(0, b"\xff" * (node._side_min + 5))  # the file is shorter than before: no old tail may come along
    assert mem.read(0, node._side_min + 5) == b"\xff" * (node._side_min + 5)
    assert mem.read(node._side_min + 5, 100) == _pattern(2 * MiB)[node._side_min + 5 :][:100]


@pytest.mark.parametrize("op", ["read", "write"])
def test_out_of_bounds_is_still_an_index_error(node: wasmhost._js.NodeBackend, op: str) -> None:
    mem = wasmhost.Memory(1)  # 64 KiB, so a buffer over the threshold does not fit
    big = node._side_min + 1
    with pytest.raises(IndexError):
        mem.read(65536 - 10, big) if op == "read" else mem.write(65536 - 10, bytes(big))
    mem.write(0, b"ok")  # the backend is fine afterwards
    assert mem.read(0, 2) == b"ok"


def test_the_directory_is_private_and_goes_with_the_backend(node: wasmhost._js.NodeBackend) -> None:
    side = node._side
    assert side is not None and os.path.isdir(side)
    if os.name == "posix":
        assert stat.S_IMODE(os.stat(side).st_mode) == 0o700
    wasmhost.Memory(256).write(0, bytes(node._side_min))
    assert os.path.exists(node._side_file)
    node.close()
    assert not os.path.exists(side)


def test_without_a_directory_the_pipe_carries_everything(session: str, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = wasmhost.get_backend()
    if not isinstance(backend, _js.NodeBackend):
        pytest.skip("only node and bun move memory through a side file")
    monkeypatch.setattr(_js, "_side_dir", lambda: None)
    plain = type(backend)()
    try:
        assert plain._side is None
        mem = wasmhost.Memory(256, backend=plain)
        seen = _calls(plain)
        data = _pattern(MiB)
        mem.write(0, data)
        assert mem.read(0, MiB) == data
        assert seen == ["writeB64", "readB64"]
    finally:
        plain.close()


def test_a_host_function_may_move_a_big_buffer_while_a_call_is_running(node: wasmhost._js.NodeBackend) -> None:
    """The module calls Python, Python reads and writes a big buffer (a nested evaluation in Node), the call goes on."""
    mem = wasmhost.Memory(256)
    data = _pattern(MiB)
    mem.write(0, data)
    seen: list[bytes] = []

    def plus(a: int, b: int) -> int:
        seen.append(mem.read(0, MiB))
        mem.write(0, data[::-1])
        return a + b

    inst = wasmhost.Instance(
        wasmhost.Module(wb.callbacks()), {"env": {"plus": plus, "note": print, "half": float, "pair": tuple}}
    )
    assert inst.exports.call_plus(2, 3) == 5
    assert seen == [data]
    assert mem.read(0, MiB) == data[::-1]
