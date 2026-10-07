"""`examples/coreutils.py`: uutils coreutils (WASI) under a WASI host written in Python, on every backend."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

import wasmhost

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def load_example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("coreutils_example", EXAMPLES / "coreutils.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def shell_for(root: Path) -> tuple[Any, list[str]]:
    if not wasmhost.get_backend().supports("imports"):
        pytest.skip(f"the {wasmhost.get_backend().name} backend can't take imports")
    example = load_example()
    module = wasmhost.Module((EXAMPLES / "wasm" / "coreutils.wasm").read_bytes())
    out: list[str] = []
    return example.Shell(str(root), module, out.append), out


def test_files_pipes_and_globs(session: str, tmp_path: Path) -> None:
    shell, out = shell_for(tmp_path)
    assert shell.run_line("echo hello > a.txt; mkdir notes; cp a.txt notes/b.txt") == 0
    assert (tmp_path / "notes" / "b.txt").read_text() == "hello\n"

    out.clear()
    shell.run_line("printf 'x\\ny\\n' >> a.txt; cat *.txt notes/*.txt | wc -l")
    assert "".join(out).strip() == "4"  # the glob sees a.txt, made earlier on the same line

    out.clear()
    shell.run_line("seq 5 | sort -r | head -n 3")
    assert "".join(out).split() == ["5", "4", "3"]

    out.clear()
    shell.run_line("cd notes; pwd; ls; cd /; pwd")
    assert "".join(out).split() == ["/notes", "b.txt", "/"]

    assert shell.run_line("false || echo recovered") == 0
    assert "recovered" in "".join(out)


def test_a_program_cannot_leave_the_directory(session: str, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n")
    root = tmp_path / "root"
    root.mkdir()
    os.symlink(outside, root / "link")
    shell, out = shell_for(root)

    for line in ("cat link", "cat ../outside.txt", f"cat {outside}"):
        out.clear()
        assert shell.run_line(line) != 0
        assert "secret" not in "".join(out)
    assert outside.read_text() == "secret\n"


def test_lua_runs_as_a_program_of_the_shell(session: str, tmp_path: Path) -> None:
    shell, out = shell_for(tmp_path)
    assert (
        shell.run_line('echo \'print(10 // 3, #arg, arg[1]) io.open("made.txt", "w"):write("from lua\\n")\' > t.lua')
        == 0
    )
    out.clear()
    assert shell.run_line("lua t.lua x") == 0
    assert "".join(out).split() == ["3", "1", "x"]
    assert (tmp_path / "made.txt").read_text() == "from lua\n"

    out.clear()
    shell.run_line("seq 3 | lua -e 'for l in io.lines() do io.write(l * 2, \" \") end'")
    assert "".join(out).split() == ["2", "4", "6"]

    out.clear()  # an error of the script stops this build of Lua with a trap, which the shell reports
    assert shell.run_line("lua -e 'x ='") == 134
    assert "stopped with a trap" in "".join(out)
