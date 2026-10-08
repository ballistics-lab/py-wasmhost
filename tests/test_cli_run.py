"""`wasmhost run [OPTIONS] module.wasm [ARGUMENTS]`: the running of a module, as `wasmtime run` does."""

from __future__ import annotations

import errno
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
        (["--invoke", "add", arith, "1", "x"], "invalid digit"),
        (["--invoke", "add", arith, "4294967295", "0"], "number too large"),
        (
            ["/no/such/file.wasm"],
            'failed to open wasm module "/no/such/file.wasm"\n\nCaused by:\n'
            f"    No such file or directory (os error {errno.ENOENT})",
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


def test_arguments_are_read_as_wasmtime_reads_them() -> None:
    assert _run.parse_value("i32", "+5") == 5
    assert _run.parse_value("i32", "0X7fffffff") == 2147483647
    assert _run.parse_value("i64", "-9223372036854775808") == -(1 << 63)
    assert _run.parse_value("f64", ".5") == 0.5
    assert _run.parse_value("f32", "-inf") == float("-inf")
    for kind, text in (("i32", "1_0"), ("i32", "0b1"), ("i32", "0o7"), ("i32", "0x"), ("i32", " 1"), ("i32", "-0x10")):
        with pytest.raises(ValueError, match="invalid digit"):
            _run.parse_value(kind, text)
    for kind, text in (("f64", "1_0"), ("f64", "0x10"), ("f64", " 1"), ("f32", ""), ("f64", "e5")):
        with pytest.raises(ValueError, match="invalid float"):
            _run.parse_value(kind, text)
    for kind, text in (("i32", "2147483648"), ("i32", "0xffffffff"), ("i64", "9223372036854775808")):
        with pytest.raises(ValueError, match="too large"):
            _run.parse_value(kind, text)


def test_runtime_is_the_backend_option_and_the_environment_variable(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, session: str, arith: str
) -> None:
    """`--runtime NAME` = `--backend NAME` = `WASMHOST_BACKEND=NAME` in run, bench and self test; the option wins."""
    assert _cli.main(["run", "--runtime", session, "--invoke", "add", arith, "2", "3"]) == 0
    assert capsys.readouterr().out == "5\n"
    monkeypatch.setenv("WASMHOST_BACKEND", session)
    assert _cli.main(["run", "--invoke", "add", arith, "2", "3"]) == 0  # no option: the variable
    assert capsys.readouterr().out == "5\n"
    monkeypatch.setenv("WASMHOST_BACKEND", "no-such-backend")
    assert _cli.main(["run", "--runtime", session, "--invoke", "add", arith, "2", "3"]) == 0  # the option wins
    assert capsys.readouterr().out == "5\n"
    for line in (["bench", "--runtime", session], ["self", "test", "--runtime", session]):
        assert _cli.build_parser().parse_args(line).backend == session


EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "wasm"


def need_imports() -> None:
    if not wasmhost.get_backend().supports("imports"):
        pytest.skip(f"the {wasmhost.get_backend().name} backend can't take imports")


def test_a_limit_the_backend_cannot_keep_is_an_error_not_a_traceback(
    capsys: pytest.CaptureFixture[str], session: str, arith: str
) -> None:
    backend = wasmhost.get_backend(session)
    for option, value, feature in (("--fuel", "1000000", "fuel"), ("--timeout", "5", "timeout")):
        code = _cli.main(["run", "--backend", session, option, value, "--invoke", "add", arith, "2", "3"])
        captured = capsys.readouterr()
        if backend.supports(feature):
            assert (code, captured.out) == (0, "5\n")
        else:
            assert code == 1
            assert captured.err.startswith("Error:")


def test_a_real_program_with_a_folder(capsys: pytest.CaptureFixture[str], session: str, tmp_path: Path) -> None:
    """coreutils (uutils, in Rust) through the command: arguments, a folder, a file read, a read-only folder."""
    need_imports()
    (tmp_path / "a.txt").write_bytes(b"hello from host\n")  # bytes: write_text would make \r\n on Windows
    base = ["run", "--backend", session, "--argv0", "coreutils", "--dir", f"{tmp_path}::/data"]
    program = str(EXAMPLES / "coreutils.wasm")
    assert _cli.main([*base, program, "cat", "/data/a.txt"]) == 0
    assert capsys.readouterr().out == "hello from host\n"
    assert _cli.main([*base, "--readonly", program, "touch", "/data/new.txt"]) == 1
    assert "Capabilities insufficient" in capsys.readouterr().err
    assert not (tmp_path / "new.txt").exists()
    assert _cli.main([*base, program, "touch", "/data/new.txt"]) == 0
    assert (tmp_path / "new.txt").exists()
    assert _cli.main(["run", "--backend", session, "--argv0", "coreutils", program, "false"]) == 1


def test_a_real_program_with_variables_and_an_exit_code(capsys: pytest.CaptureFixture[str], session: str) -> None:
    """Lua: what follows the module is the program's (`-e` is not a host option), `--env`, the exit code."""
    need_imports()
    lua = str(EXAMPLES / "lua.wasm")
    assert (
        _cli.main(["run", "--backend", session, "--env", "FOO=bar", lua, "-e", "print(os.getenv('FOO'), arg[0])"]) == 0
    )
    assert capsys.readouterr().out == "bar\tlua.wasm\n"
    assert _cli.main(["run", "--backend", session, lua, "-e", "os.exit(7)"]) == 7
