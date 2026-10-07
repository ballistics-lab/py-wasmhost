"""`wasmhost run [OPTIONS] module.wasm [ARGUMENTS]`: the running of a module, as `wasmtime run` does."""

from __future__ import annotations

from pathlib import Path

import pytest
import wasm_builder as wb

import wasmhost
from wasmhost import _cli, _run


@pytest.fixture
def arith(tmp_path: Path) -> str:
    path = tmp_path / "arith.wasm"
    path.write_bytes(wb.arith())
    return str(path)


def invoke(capsys: pytest.CaptureFixture[str], session: str, path: str, name: str, *words: str) -> list[str]:
    assert _cli.main(["run", "--backend", session, "--invoke", name, path, *words]) == 0
    return capsys.readouterr().out.splitlines()


def test_the_split_is_host_options_module_program_arguments() -> None:
    assert _run.split_command(["--backend", "node", "a.wasm", "--", "-x", "y"]) == (
        ["--backend", "node"],
        "a.wasm",
        ["-x", "y"],
    )
    assert _run.split_command(["a.wasm", "--verbose", "--dir", "x"]) == ([], "a.wasm", ["--verbose", "--dir", "x"])
    assert _run.split_command(["--invoke=add", "a.wasm", "1"]) == (["--invoke=add"], "a.wasm", ["1"])
    for line in ([], ["-h"], ["--backend", "node", "--help"], ["--dir", "x"]):
        assert _run.split_command(line) is None
    assert _run.split_command(["./self"]) == ([], "./self", [])  # nothing is reserved after `run`


def test_invoke_prints_each_result_on_a_line(capsys: pytest.CaptureFixture[str], session: str, arith: str) -> None:
    assert invoke(capsys, session, arith, "add", "2", "3") == ["5"]
    assert invoke(capsys, session, arith, "add", "-1", "0x10") == ["15"]
    assert invoke(capsys, session, arith, "dup", "7") == ["7", "7"]
    assert invoke(capsys, session, arith, "fadd", "0.5", "2") == ["2.5"]
    assert invoke(capsys, session, arith, "fadd", "1", "2") == ["3"]  # Rust's `{}`: no ".0"
    assert invoke(capsys, session, arith, "twice", "0.1") == ["0.2"]  # an f32, the shortest text of it


def test_a_function_without_results_prints_nothing(
    capsys: pytest.CaptureFixture[str], session: str, arith: str
) -> None:
    assert invoke(capsys, session, arith, "store8", "0", "1") == []


def test_errors_are_a_message_and_a_code(capsys: pytest.CaptureFixture[str], session: str, arith: str) -> None:
    assert _cli.main(["run", "--backend", session, "--invoke", "trap", arith]) == _run.TRAP_EXIT
    assert "Error:" in capsys.readouterr().err
    for line, text in (
        (["--invoke", "nope", arith], "no function named `nope`"),
        (["--invoke", "add", arith, "1"], "takes 2 argument(s)"),
        (["--invoke", "add", arith, "1", "x"], "invalid literal"),
        (["--invoke", "add", arith, "4294967295", "0"], "number too large"),
        (
            ["/no/such/file.wasm"],
            'failed to open wasm module "/no/such/file.wasm"\n\nCaused by:\n    No such file or directory (os error 2)',
        ),
    ):
        assert _cli.main(["run", "--backend", session, *line]) == 1
        assert text in capsys.readouterr().err


def test_a_command_runs_with_its_arguments_and_exit_code(
    capsys: pytest.CaptureFixture[str], session: str, tmp_path: Path
) -> None:
    if not wasmhost.get_backend(session).supports("imports"):
        pytest.skip(f"the {session} backend can't take imports")
    path = tmp_path / "hello.wasm"
    path.write_bytes(wb.wasi_hello())
    folder = tmp_path / "data"
    folder.mkdir()
    code = _cli.main(["run", "--backend", session, "--dir", f"{folder}::/", str(path), "--", "one", "--two"])
    assert code == 3  # proc_exit(argc): the name and two arguments
    assert capsys.readouterr().out == "hello, wasi\n"
    assert (folder / "note.txt").read_bytes() == b"hello, wasi\n"


def test_readonly_keeps_the_folder_as_it_was(session: str, tmp_path: Path) -> None:
    if not wasmhost.get_backend(session).supports("imports"):
        pytest.skip(f"the {session} backend can't take imports")
    path = tmp_path / "hello.wasm"
    path.write_bytes(wb.wasi_hello())
    folder = tmp_path / "data"
    folder.mkdir()
    assert _cli.main(["run", "--backend", session, "--dir", f"{folder}::/", "--readonly", str(path)]) == 1
    assert list(folder.iterdir()) == []


def test_values_are_written_as_rust_does() -> None:
    for kind, value, text in (
        ("f64", 1e21, "1000000000000000000000"),
        ("f64", float("nan"), "NaN"),
        ("f64", float("-inf"), "-inf"),
        ("f32", 0.30000001192092896, "0.3"),
        ("i32", -5, "-5"),
    ):
        assert _run.format_value(kind, value) == text


def test_argv0_names_the_program(session: str, tmp_path: Path) -> None:
    args = _run.build_parser().parse_args(["--argv0", "tool", "--dir", "a::b", "--env", "X=1"])
    assert (args.argv0, args.dir, args.env) == ("tool", ["a::b"], ["X=1"])
    assert _run.split_command(["--argv0", "tool", "a.wasm", "x"]) == (["--argv0", "tool"], "a.wasm", ["x"])


def test_a_module_without_a_start_function_exits_quietly(
    capsys: pytest.CaptureFixture[str], session: str, arith: str
) -> None:
    """As `wasmtime m.wasm` does (checked against wasmtime 48.0.5): no output, exit code 0."""
    assert _cli.main(["run", "--backend", session, arith]) == 0
    assert capsys.readouterr() == ("", "")
