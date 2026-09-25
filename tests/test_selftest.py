import pytest

import wasmhost
from wasmhost import _selftest


def test_selftest_passes_on_every_backend(session: str) -> None:
    lines: list[str] = []
    assert wasmhost.selftest(session, out=lines.append), "\n".join(lines)
    assert lines[0] == "wasmhost self-test"
    assert any(line.startswith("backend     " + session) for line in lines)
    assert lines[-1].endswith(" passed") and "FAIL" not in "\n".join(lines)


def test_selftest_reports_a_failure_and_carries_on() -> None:
    class Broken(wasmhost.Backend):
        name = "broken"

        def validate(self, data: bytes) -> bool:
            return False

    lines: list[str] = []
    assert not wasmhost.selftest(Broken(), out=lines.append)
    text = "\n".join(lines)
    assert "FAIL  validate, compile, instantiate" in text
    assert "0/1 passed" in text  # nothing more can be checked without an instance


def test_module_constants_are_the_test_modules() -> None:
    import wasm_builder as wb

    assert _selftest.MODULE == wb.arith()
    assert _selftest.CALLBACKS == wb.callbacks()
    assert _selftest.GLOBAL_IMPORT == wb.imports_global()
    assert _selftest.EXCEPTIONS_FINAL == wb.exceptions(final=True)
    assert _selftest.EXCEPTIONS_LEGACY == wb.exceptions(final=False)


def test_main(capsys: pytest.CaptureFixture[str], session: str) -> None:
    assert _selftest.main(["--backend", session]) == 0
    assert "passed" in capsys.readouterr().out
