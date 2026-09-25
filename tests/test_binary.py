import pytest
import wasm_builder as wb

from wasmhost import FuncType
from wasmhost._binary import parse


def test_exports_have_types() -> None:
    info = parse(wb.arith())
    by_name = {e.name: e for e in info.exports}
    assert by_name["add"].kind == "function" and by_name["add"].type == FuncType(("i32", "i32"), ("i32",))
    assert by_name["add64"].type == FuncType(("i64", "i64"), ("i64",))
    assert by_name["dup"].type == FuncType(("i32",), ("i32", "i32"))
    assert by_name["memory"].kind == "memory"
    assert by_name["counter"].kind == "global" and by_name["counter"].type == "i32"


def test_const_expr_containing_the_end_opcode() -> None:
    """`i32.const 11` is 41 0B 0B; reading globals must not stop at the first 0x0B."""
    info = parse(wb.arith())
    assert [e.name for e in info.exports][-2:] == ["counter", "ten"]


def test_imports_are_described() -> None:
    (imp,) = parse(wb.needs_import()).imports
    assert (imp.module, imp.name, imp.kind, imp.type) == ("env", "f", "function", FuncType((), ()))


@pytest.mark.parametrize("junk", [b"", b"\0asm", b"nope", b"\0asm\x01\x00\x00\x00\x01\xff\xff"])
def test_junk_is_rejected(junk: bytes) -> None:
    with pytest.raises(ValueError):
        parse(junk)


def _with_custom(wasm: bytes, *sections: tuple[str, bytes]) -> bytes:
    return wasm + b"".join(wb.section(0, wb.name(n) + data) for n, data in sections)


def test_custom_sections_are_kept_in_order() -> None:
    wasm = _with_custom(wb.arith(), ("note", b"one"), ("other", b""), ("note", b"\x00\xff two"))
    assert parse(wasm).custom == (("note", b"one"), ("other", b""), ("note", b"\x00\xff two"))
    assert parse(wb.arith()).custom == ()


def test_a_custom_section_between_the_others() -> None:
    plain = wb.arith()
    at = 8  # right after the header, before the type section
    wasm = plain[:at] + wb.section(0, wb.name("first") + b"x") + plain[at:]
    info = parse(wasm)
    assert info.custom == (("first", b"x"),)
    assert [e.name for e in info.exports] == [e.name for e in parse(plain).exports]


def test_a_truncated_custom_section_is_rejected() -> None:
    with pytest.raises(ValueError):
        parse(wb.arith() + b"\x00\x7f\x01a")  # says 127 bytes, has 2
