"""`examples/wasi_sh.py`: BusyBox ash (WASI) on an in-memory file system, under a WASI host written in Python."""

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
    spec = importlib.util.spec_from_file_location("wasi_sh_example", EXAMPLES / "wasi_sh.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def shell_for() -> tuple[Any, ModuleType]:
    if not wasmhost.get_backend().supports("imports"):
        pytest.skip(f"the {wasmhost.get_backend().name} backend can't take imports")
    example = load_example()
    try:
        path = example.find_wasm()  # downloaded once into the temporary directory
    except SystemExit as exc:
        pytest.skip(f"busybox.wasm is not available: {exc}")
    with open(path, "rb") as f:
        module = wasmhost.Module(f.read())
    return example.Shell(module), example


class Output:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def __call__(self, data: bytes) -> None:
        self.chunks.append(data)

    @property
    def text(self) -> str:
        return b"".join(self.chunks).decode()


def run(shell: Any, command: str, lines: str | None = None) -> tuple[int, str, str]:
    out, err = Output(), Output()
    code = shell.run(command, stdout=out, stderr=err, lines=lines)
    return code, out.text, err.text


def test_variables_redirection_here_document_and_status(session: str) -> None:
    shell, _ = shell_for()
    code, out, _ = run(
        shell,
        "x=5; echo $((x * 2)); printf 'b\\na\\nc\\n' > list.txt; sort list.txt | tr a-z A-Z; "
        "cat <<EOF >> list.txt\nfrom here\nEOF\n"
        "wc -l < list.txt; false; echo st=$?; exit 7",
    )
    assert code == 7
    assert out.split() == ["10", "A", "B", "C", "4", "st=1"]
    assert shell.vfs.read_file("/list.txt") == b"b\na\nc\nfrom here\n"


def test_control_flow_functions_and_substitution(session: str) -> None:
    shell, _ = shell_for()
    code, out, err = run(
        shell,
        'f() { echo "in f: $1"; return 3; }; f arg; echo st=$?; '
        "for i in 1 2 3; do if [ $i -eq 2 ]; then continue; fi; echo i=$i; done; "
        "case abc in a*) echo matched;; esac; s=$(echo sub | sed s/s/S/); echo $s; "
        "cat /nonexistent; echo cat=$?",
    )
    assert code == 0
    assert out.split() == ["in", "f:", "arg", "st=3", "i=1", "i=3", "matched", "Sub", "cat=1"]
    assert "/nonexistent" in err


def test_directories_listing_and_removal(session: str) -> None:
    shell, _ = shell_for()
    code, out, _ = run(
        shell,
        "mkdir -p proj/src; cd proj; pwd; echo x > src/b.c; echo y > src/a.c; ls src; cd ..; mv proj/src proj/lib; "
        "ls proj; find proj | sort; rm -r proj; ls; ls /dev",
    )
    assert code == 0
    assert out.split() == [
        "/proj",
        "a.c",
        "b.c",
        "lib",
        "proj",
        "proj/lib",
        "proj/lib/a.c",
        "proj/lib/b.c",
        "dev",
        "tmp",
        "null",
    ]


def test_nothing_reaches_the_real_files(session: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    shell, _ = shell_for()
    code, out, _ = run(
        shell, "echo secret > /tmp/s; cat /tmp/s; echo z > made; cat /etc/passwd; echo st=$?; cat ../../etc/passwd"
    )
    assert out.split() == ["secret", "st=1"]
    assert code == 1
    assert list(tmp_path.iterdir()) == []  # the working directory of the process has nothing new
    assert shell.vfs.read_file("/made") == b"z\n"
    assert not os.path.exists("/made")


def test_standard_input_and_pipes_between_stages(session: str) -> None:
    shell, _ = shell_for()
    code, out, _ = run(shell, 'while read -r l; do echo "got $l"; done | sort -r | head -n 2', lines="a\nb\nc\n")
    assert code == 0
    assert out.split() == ["got", "c", "got", "b"]


def test_a_prompt_session_reads_line_by_line(session: str) -> None:
    shell, _ = shell_for()
    out, err = Output(), Output()
    code = shell.run(
        None, stdout=out, stderr=err, lines="x=3\nfor i in 1 2; do\n\techo $x$i\ndone\ncd /tmp\npwd\nexit 4\n"
    )
    assert code == 4
    assert out.text.split()[-3:] == ["31", "32", "/tmp"]  # (before them, the banner of BusyBox)
    assert "$ " in err.text  # the shell's own prompt
    assert "> " in err.text  # and the one of a command that goes on
    assert "echo $x$i" not in err.text  # its echo of the line is not repeated to the console


def test_the_file_system_on_its_own() -> None:
    example = load_example()
    vfs = example.Vfs()
    vfs.write_file("/t.txt", b"hi")
    assert vfs.read_file("t.txt") == b"hi"
    node_dir = vfs.create("/d", "dir")
    vfs.create("/d/f", "file")
    with pytest.raises(example.WasiError) as removing:
        vfs.remove("/d", directory=True)
    assert removing.value.code == example.ENOTEMPTY
    with pytest.raises(example.WasiError) as creating:
        vfs.create("/dev/x", "file")
    assert creating.value.code == example.EPERM
    held = vfs.find("/d/f")
    vfs.rename("/d/f", "/g")
    vfs.remove("/g", directory=False)
    assert held.kind == "file"  # what is open outlives its name
    assert node_dir.entries == {}
    with pytest.raises(example.WasiError) as missing:
        vfs.find("/d/f")
    assert missing.value.code == example.ENOENT
