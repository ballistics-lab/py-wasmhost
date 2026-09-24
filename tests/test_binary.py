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
