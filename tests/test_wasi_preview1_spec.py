"""`wasmhost.wasi.preview1` against the specification: the witx files of WASI 0.1 (`tests/data/wasi`), unchanged."""

from __future__ import annotations

import re
import struct
from pathlib import Path

import pytest

from wasmhost.wasi import preview1

DATA = Path(__file__).parent / "data" / "wasi"


def parse(text: str) -> list[object]:
    """The s-expressions of a witx file: lists of atoms (strings) and lists; `;;` comments are dropped."""
    tokens = re.findall(r'"[^"]*"|[()]|[^\s()"]+', re.sub(r";;[^\n]*", "", text))
    stack: list[list[object]] = [[]]
    for token in tokens:
        if token == "(":
            stack.append([])
        elif token == ")":
            done = stack.pop()
            stack[-1].append(done)
        else:
            stack[-1].append(token)
    assert len(stack) == 1
    return stack[0]


def forms(tree: list[object], head: str) -> list[list[object]]:
    return [x for x in tree if isinstance(x, list) and x and x[0] == head]


TYPES = {str(f[1]).lstrip("$"): f[2] for f in forms(parse((DATA / "typenames.witx").read_text()), "typename")}
MODULE = forms(parse((DATA / "wasi_snapshot_preview1.witx").read_text()), "module")[0]
FUNCS = [f for f in MODULE if isinstance(f, list) and f[:2] == ["@interface", "func"]]  # pyright: ignore[reportUnnecessaryContains]


def resolve(node: object) -> object:
    """A type, with the names of other types looked through."""
    while isinstance(node, str) and node.startswith("$"):
        node = TYPES[node[1:]]
    return node


def lower(node: object) -> list[str]:
    """The core wasm types a witx type is passed as."""
    node = resolve(node)
    if node == "string":
        return ["i32", "i32"]  # a pointer and a length
    if isinstance(node, str):
        return {"u8": ["i32"], "u16": ["i32"], "u32": ["i32"], "s32": ["i32"], "u64": ["i64"], "s64": ["i64"]}[node]
    assert isinstance(node, list)
    match node[0]:
        case "handle":
            return ["i32"]
        case "enum":
            return lower(node[1][2])  # pyright: ignore[reportIndexIssue]
        case "flags":
            return lower(node[1][2])  # pyright: ignore[reportIndexIssue]
        case "@witx":
            return ["i32"]  # pointer, const_pointer
        case "list":
            return ["i32", "i32"]
    raise AssertionError(node)


def signature(func: list[object]) -> tuple[str, tuple[list[str], list[str]]]:
    name = str(next(f for f in func if isinstance(f, list) and f[0] == "export")[1]).strip('"')  # pyright: ignore[reportIndexIssue]
    params: list[str] = []
    for f in forms(func, "param"):
        params += lower(f[2])
    results: list[str] = []
    for f in forms(func, "result"):
        results = ["i32"]  # the errno
        expected = f[2]
        assert isinstance(expected, list) and expected[0] == "expected"
        ok = expected[1]
        if isinstance(ok, list) and ok[0] == "tuple":
            params += ["i32"] * (len(ok) - 1)  # each value comes back through a pointer
        elif not (isinstance(ok, list) and ok[0] == "error"):
            params.append("i32")
    return name, (params, results)


def tag_names(name: str) -> list[str]:
    node = TYPES[name]
    assert isinstance(node, list)
    return [str(x).lstrip("$") for x in node[2:]]


def size_and_align(node: object) -> tuple[int, int]:
    node = resolve(node)
    if isinstance(node, str):
        size = {"u8": 1, "u16": 2, "u32": 4, "s32": 4, "u64": 8, "s64": 8}[node]
        return size, size
    assert isinstance(node, list)
    match node[0]:
        case "handle":
            return 4, 4
        case "enum" | "flags":
            return size_and_align(node[1][2])  # pyright: ignore[reportIndexIssue]
        case "@witx":
            return 4, 4
        case "record":
            offset, align = 0, 1
            for field in node[1:]:
                size, a = size_and_align(field[2])  # pyright: ignore[reportIndexIssue]
                offset = -(-offset // a) * a + size
                align = max(align, a)
            return -(-offset // align) * align, align
        case "union":
            tag = size_and_align(node[1][2])  # pyright: ignore[reportIndexIssue]
            variants = [size_and_align(v) for v in node[2:]]
            align = max([tag[1], *[a for _, a in variants]])
            payload = max(s for s, _ in variants)
            offset = -(-tag[0] // align) * align
            return -(-(offset + payload) // align) * align, align
    raise AssertionError(node)


def test_the_witx_files_are_what_the_tests_think() -> None:
    assert len(FUNCS) == 46
    assert len(TYPES) == 46


def test_every_function_of_the_specification_and_no_other() -> None:
    expected = {signature(f)[0] for f in FUNCS}
    assert set(preview1.SIGNATURES) == expected
    assert set(preview1.Preview1().imports()[preview1.SNAPSHOT]) == expected
    assert preview1.SNAPSHOT == str(MODULE[1]).lstrip("$")


@pytest.mark.parametrize("func", FUNCS, ids=lambda f: signature(f)[0])
def test_signature(func: list[object]) -> None:
    name, (params, results) = signature(func)
    assert preview1.SIGNATURES[name] == (tuple(params), tuple(results)), name


@pytest.mark.parametrize("table", sorted(preview1.TABLES))
def test_enumerations_and_flags_are_the_specification_s(table: str) -> None:
    assert preview1.TABLES[table] == tuple(tag_names(table))


def test_every_enumeration_and_flags_type_of_the_specification_is_in_the_tables() -> None:
    named = {n for n, node in TYPES.items() if isinstance(node, list) and node[0] in ("enum", "flags")}
    assert named == set(preview1.TABLES)


def test_values_are_positions_and_bits() -> None:
    assert preview1.Errno.success == 0
    assert preview1.Errno.noent == 44
    assert preview1.Errno.notcapable == 76
    assert preview1.Rights.fd_read == 1 << 1
    assert preview1.Filetype.regular_file == 4
    assert preview1.Filetype.directory == 3
    assert preview1.Whence.cur == 1
    assert preview1.ALL_RIGHTS == (1 << 30) - 1
    with pytest.raises(AttributeError):
        preview1.Errno.nonsense  # noqa: B018


@pytest.mark.parametrize(
    ("name", "record"),
    [
        ("iovec", "iovec"),
        ("ciovec", "ciovec"),
        ("dirent", "dirent"),
        ("fdstat", "fdstat"),
        ("filestat", "filestat"),
        ("event", "event"),
        ("subscription", "subscription"),
        ("prestat", "prestat"),
    ],
)
def test_records_have_the_specification_s_size(name: str, record: str) -> None:
    size, _ = size_and_align(f"${record}")
    assert preview1.STRUCTS[name].size == size


def test_field_offsets() -> None:
    """Where the fields sit, from the specification's own layout rules, against what the packers put there."""

    def offsets(record: str) -> list[int]:
        node = resolve(f"${record}")
        assert isinstance(node, list)
        offset, found = 0, []
        for field in node[1:]:
            size, align = size_and_align(field[2])  # pyright: ignore[reportIndexIssue]
            offset = -(-offset // align) * align
            found.append(offset)
            offset += size
        return found

    assert offsets("filestat") == [0, 8, 16, 24, 32, 40, 48, 56]
    assert offsets("fdstat") == [0, 2, 8, 16]
    assert offsets("dirent") == [0, 8, 16, 20]
    assert offsets("event") == [0, 8, 10, 16]
    packed = preview1.STRUCTS["filestat"].pack(1, 2, 3, 4, 5, 6, 7, 8)
    assert [struct.unpack_from("<Q", packed, o)[0] for o in (0, 8, 24, 32, 40, 48, 56)] == [1, 2, 4, 5, 6, 7, 8]
    assert packed[16] == 3
    fdstat = preview1.STRUCTS["fdstat"].pack(4, 0x1F, 1 << 2, 1 << 3)
    assert (fdstat[0], struct.unpack_from("<H", fdstat, 2)[0]) == (4, 0x1F)
    assert struct.unpack_from("<QQ", fdstat, 8) == (4, 8)
