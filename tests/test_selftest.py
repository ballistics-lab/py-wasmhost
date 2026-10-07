import pytest
import test_elem_signatures

import wasmhost
from wasmhost import _cli, _selftest


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
    assert _selftest.MEMORY_USER == wb.uses_memory()
    assert _selftest.TABLE_OWN == wb.tables(imported=False)
    assert _selftest.TABLE_IMPORT == wb.tables(imported=True)
    assert _selftest.TABLE_ELEM == test_elem_signatures.module()
    assert _selftest.TABLE_ELEM_START == test_elem_signatures.module(start=True)
    assert _selftest.EXCEPTIONS_FINAL == wb.exceptions(final=True)
    assert _selftest.EXCEPTIONS_LEGACY == wb.exceptions(final=False)


def test_main(capsys: pytest.CaptureFixture[str], session: str) -> None:
    assert _cli.main(["self", "test", "--backend", session]) == 0
    assert "passed" in capsys.readouterr().out


def test_the_command_is_a_subcommand(capsys: pytest.CaptureFixture[str], session: str) -> None:
    assert _cli.main(["self", "test", "--backend", session]) == 0
    assert "passed" in capsys.readouterr().out


def test_version_and_help_print_and_succeed(capsys: pytest.CaptureFixture[str]) -> None:
    assert _cli.main(["version"]) == 0
    assert capsys.readouterr().out.strip() == _cli._VERSION  # pyright: ignore[reportPrivateUsage]
    assert _cli.main(["help"]) == 0
    assert "usage: python -m wasmhost" in capsys.readouterr().out
    assert _cli.main(["self"]) == 0  # no subcommand: its usage
    assert "usage: python -m wasmhost self" in capsys.readouterr().out


def test_without_a_command_there_is_only_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert _cli.main([]) == 2
    err = capsys.readouterr().err
    assert "COMMAND" in err
    assert "usage: python -m wasmhost" in err


def test_a_flag_of_the_selftest_is_not_a_flag_of_the_program(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stop:
        _cli.main(["--backend", "node"])  # before the command: it is not the program's flag, so a usage error
    assert stop.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
