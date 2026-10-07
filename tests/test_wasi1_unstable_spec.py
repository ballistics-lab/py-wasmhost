"""`wasmhost.wasi1` as `wasi_unstable` against the specification: the witx files of the first snapshot
(`tests/data/wasi/preview0`)."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest
import test_wasi1_spec as spec

from wasmhost import wasi1

DATA = Path(__file__).parent / "data" / "wasi" / "preview0"
TYPES0 = {
    str(f[1]).lstrip("$"): f[2] for f in spec.forms(spec.parse((DATA / "typenames.witx").read_text()), "typename")
}
MODULE0 = spec.forms(spec.parse((DATA / "wasi_unstable.witx").read_text()), "module")[0]
FUNCS0 = [f for f in MODULE0 if isinstance(f, list) and f[:2] == ["@interface", "func"]]  # pyright: ignore[reportUnnecessaryContains]
NAMES0 = [str(next(f for f in func if isinstance(f, list) and f[0] == "export")[1]).strip('"') for func in FUNCS0]  # pyright: ignore[reportIndexIssue]


@pytest.fixture
def first_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """The helpers of test_wasi1_spec look types up in one table: give them the first snapshot's."""
    monkeypatch.setattr(spec, "TYPES", TYPES0)


def test_the_witx_files_are_what_the_tests_think() -> None:
    assert len(FUNCS0) == 45
    assert str(MODULE0[1]) == "$" + wasi1.UNSTABLE


def test_the_functions_are_those_of_the_snapshot_but_sock_accept() -> None:
    assert set(wasi1.UNSTABLE_SIGNATURES) == set(NAMES0)
    assert set(wasi1.SIGNATURES) - set(wasi1.UNSTABLE_SIGNATURES) == {"sock_accept"}
    assert set(wasi1.Wasi().imports()[wasi1.UNSTABLE]) == set(NAMES0)


@pytest.mark.parametrize("name", NAMES0)
def test_signature(name: str, first_snapshot: None) -> None:
    func = FUNCS0[NAMES0.index(name)]
    _, (params, results) = spec.signature(func)
    assert wasi1.UNSTABLE_SIGNATURES[name] == (tuple(params), tuple(results)), name


def test_enumerations_and_flags(first_snapshot: None) -> None:
    named = {n for n, node in TYPES0.items() if isinstance(node, list) and node[0] in ("enum", "flags")}
    assert named == set(wasi1.TABLES)
    for name in sorted(named):
        expected = wasi1.UNSTABLE_TABLES.get(name, wasi1.TABLES[name])
        assert expected == tuple(spec.tag_names(name)), name


def test_the_differences_are_exactly_the_four_that_are_kept_apart() -> None:
    """Whatever differs from the later snapshot is in UNSTABLE_TABLES and UNSTABLE_STRUCTS, and nothing else."""
    assert set(wasi1.UNSTABLE_TABLES) == {"whence", "rights"}
    assert {k for k in TYPES0 if TYPES0[k] != spec.TYPES.get(k)} == {
        "whence",
        "rights",
        "linkcount",
        "subscription_clock",
    }
    assert wasi1.UNSTABLE_TABLES["whence"] == ("cur", "end", "set")
    assert wasi1.UNSTABLE_ALL_RIGHTS == (1 << 29) - 1
    assert set(wasi1.UNSTABLE_STRUCTS) == {"filestat", "subscription"}


@pytest.mark.parametrize("name", ["iovec", "ciovec", "dirent", "fdstat", "event", "prestat"])
def test_records_that_do_not_differ_keep_their_size(name: str, first_snapshot: None) -> None:
    assert wasi1.STRUCTS[name].size == spec.size_and_align(f"${name}")[0]


@pytest.mark.parametrize("name", ["filestat", "subscription"])
def test_records_that_differ_have_the_first_snapshot_s_size(name: str, first_snapshot: None) -> None:
    assert wasi1.UNSTABLE_STRUCTS[name].size == spec.size_and_align(f"${name}")[0]


def test_field_offsets(first_snapshot: None) -> None:
    def offsets(record: str, base: int = 0) -> list[int]:
        node = spec.resolve(f"${record}")
        assert isinstance(node, list)
        offset, found = 0, []
        for field in node[1:]:
            size, align = spec.size_and_align(field[2])  # pyright: ignore[reportIndexIssue]
            offset = -(-offset // align) * align
            found.append(base + offset)
            offset += size
        return found

    assert offsets("filestat") == [0, 8, 16, 20, 24, 32, 40, 48]  # nlink is 32 bits and sits at 20
    packed = wasi1.UNSTABLE_STRUCTS["filestat"].pack(1, 2, 3, 4, 5, 6, 7, 8)
    assert packed[16] == 3
    assert struct.unpack_from("<I", packed, 20)[0] == 4
    assert [struct.unpack_from("<Q", packed, o)[0] for o in (0, 8, 24, 32, 40, 48)] == [1, 2, 5, 6, 7, 8]
    # a subscription: userdata, the tag, then the union at 16; a clock starts with its identifier
    assert offsets("subscription_clock") == [0, 8, 16, 24, 32]
    clock = wasi1._SUB_CLOCK_UNSTABLE  # pyright: ignore[reportPrivateUsage]
    assert clock.size + 16 == wasi1.UNSTABLE_STRUCTS["subscription"].size
    raw = clock.pack(11, 1, 22, 33, 1)
    assert struct.unpack_from("<Q", raw, 0)[0] == 11
    assert struct.unpack_from("<I", raw, 8)[0] == 1
    assert [struct.unpack_from("<Q", raw, o)[0] for o in (16, 24)] == [22, 33]
    assert struct.unpack_from("<H", raw, 32)[0] == 1
