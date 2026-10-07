"""`wasmhost.wasi.preview1` on every backend: a real WASI program (`wasm_builder.wasi_hello`) run through the engine."""

from __future__ import annotations

from pathlib import Path

import pytest
import wasm_builder as wb

import wasmhost
from wasmhost import Instance, Module
from wasmhost.wasi import preview1

TEXT = b"hello, wasi\n"


def need_imports() -> None:
    """A program imports its WASI functions, so a backend that can't take imports (gi-jsc) can't run one."""
    if not wasmhost.get_backend().supports("imports"):
        pytest.skip(f"the {wasmhost.get_backend().name} backend can't take imports")


def test_a_command_runs_to_its_exit_code(session: str, tmp_path: Path) -> None:
    need_imports()
    out: list[bytes] = []
    wasi = preview1.Wasip1(args=["prog", "one", "two"], preopens={"/": tmp_path}, stdout=out.append)
    assert wasi.run(Module(wb.wasi_hello(), backend=session)) == 3  # proc_exit(argc)
    assert b"".join(out) == TEXT
    assert (tmp_path / "note.txt").read_bytes() == TEXT  # path_open, fd_write, fd_close on a file


def test_the_program_without_a_directory_still_runs(session: str) -> None:
    need_imports()
    out: list[bytes] = []
    assert preview1.Wasip1(args=["prog"], stdout=out.append).run(Module(wb.wasi_hello(), backend=session)) == 1
    assert b"".join(out) == TEXT  # the failed path_open was only an errno the program ignored


def test_steps_and_a_second_run_over_the_same_host(session: str, tmp_path: Path) -> None:
    need_imports()
    out: list[bytes] = []
    wasi = preview1.Wasip1(args=["a", "b"], preopens={"/": tmp_path}, stdout=out.append)
    instance = wasi.instantiate(Module(wb.wasi_hello(), backend=session))
    assert isinstance(instance, Instance)
    assert wasi.start(instance) == 2
    wasi.close()
    assert b"".join(out) == TEXT


def test_options_of_the_instance_go_through(session: str, tmp_path: Path) -> None:
    need_imports()
    wasi = preview1.Wasip1(args=["p"], preopens={"/": tmp_path}, stdout=lambda data: None)
    assert wasi.run(Module(wb.wasi_hello(), backend=session), max_memory=4) == 1


def test_a_program_of_the_first_snapshot(session: str, tmp_path: Path) -> None:
    """The same program, importing from `wasi_unstable` (what older toolchains produce), through the same host."""
    need_imports()
    out: list[bytes] = []
    wasi = preview1.Wasip1(args=["prog", "one"], preopens={"/": tmp_path}, stdout=out.append)
    assert wasi.run(Module(wb.wasi_hello(preview1.UNSTABLE), backend=session)) == 2
    assert b"".join(out) == TEXT
    assert (tmp_path / "note.txt").read_bytes() == TEXT


def test_a_module_that_is_not_a_program(session: str) -> None:
    wasi = preview1.Wasip1()
    with pytest.raises(TypeError, match="_start"):
        wasi.start(Instance(Module(wb.arith(), backend=session)))


def test_the_imports_link_against_the_module(session: str) -> None:
    """Every function of the snapshot can be offered at once: the engine links the ones the module imports."""
    need_imports()
    wasi = preview1.Wasip1()
    Instance(Module(wb.wasi_hello(), backend=session), wasi.imports())
