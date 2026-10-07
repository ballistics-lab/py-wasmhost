"""`wasmhost.wasi1` on every backend: a real WASI program (`wasm_builder.wasi_hello`) run through the engine."""

from __future__ import annotations

from pathlib import Path

import pytest
import wasm_builder as wb

import wasmhost
from wasmhost import Instance, Module, wasi1

TEXT = b"hello, wasi\n"


def need_imports() -> None:
    """A program imports its WASI functions, so a backend that can't take imports (gi-jsc) can't run one."""
    if not wasmhost.get_backend().supports("imports"):
        pytest.skip(f"the {wasmhost.get_backend().name} backend can't take imports")


def test_a_command_runs_to_its_exit_code(session: str, tmp_path: Path) -> None:
    need_imports()
    out: list[bytes] = []
    wasi = wasi1.Wasi(args=["prog", "one", "two"], preopens={"/": tmp_path}, stdout=out.append)
    assert wasi.run(Module(wb.wasi_hello(), backend=session)) == 3  # proc_exit(argc)
    assert b"".join(out) == TEXT
    assert (tmp_path / "note.txt").read_bytes() == TEXT  # path_open, fd_write, fd_close on a file


def test_the_program_without_a_directory_still_runs(session: str) -> None:
    need_imports()
    out: list[bytes] = []
    assert wasi1.Wasi(args=["prog"], stdout=out.append).run(Module(wb.wasi_hello(), backend=session)) == 1
    assert b"".join(out) == TEXT  # the failed path_open was only an errno the program ignored


def test_steps_and_a_second_run_over_the_same_host(session: str, tmp_path: Path) -> None:
    need_imports()
    out: list[bytes] = []
    wasi = wasi1.Wasi(args=["a", "b"], preopens={"/": tmp_path}, stdout=out.append)
    instance = wasi.instantiate(Module(wb.wasi_hello(), backend=session))
    assert isinstance(instance, Instance)
    assert wasi.start(instance) == 2
    wasi.close()
    assert b"".join(out) == TEXT


def test_options_of_the_instance_go_through(session: str, tmp_path: Path) -> None:
    need_imports()
    wasi = wasi1.Wasi(args=["p"], preopens={"/": tmp_path}, stdout=lambda data: None)
    assert wasi.run(Module(wb.wasi_hello(), backend=session), max_memory=4) == 1


def test_a_module_that_is_not_a_program(session: str) -> None:
    wasi = wasi1.Wasi()
    with pytest.raises(TypeError, match="_start"):
        wasi.start(Instance(Module(wb.arith(), backend=session)))


def test_the_imports_link_against_the_module(session: str) -> None:
    """Every function of the snapshot can be offered at once: the engine links the ones the module imports."""
    need_imports()
    wasi = wasi1.Wasi()
    Instance(Module(wb.wasi_hello(), backend=session), wasi.imports())
