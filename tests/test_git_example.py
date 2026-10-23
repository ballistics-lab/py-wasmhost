"""`examples/git/git.py`: git (git-real.wasm) and libgit2 (git.wasm, for the network) built to WASI, under a WASI host
written in Python, on every backend.

The network goes through the host (`wasmhost_net`), so the tests clone, push and pull against a real git
(`git http-backend`) served over HTTP by a thread of the test."""

from __future__ import annotations

import base64
import contextlib
import importlib.util
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import ModuleType

import pytest

import wasmhost

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "git"
GIT = shutil.which("git")
AUTH = "Basic " + base64.b64encode(b"bob:s3cret").decode()


def load_example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("git_example", EXAMPLE / "git.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def git(
    session: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Callable[..., int]]:
    if session == "pyodide":  # the host would run inside Pyodide's Python, which has no os.link and no sockets
        pytest.skip("the WASI and network host of the example needs a CPython with sockets and os.link")
    if not wasmhost.get_backend().supports("imports"):
        pytest.skip(f"the {wasmhost.get_backend().name} backend can't take imports")
    for name in ("GIT_USERNAME", "GIT_PASSWORD", "GIT_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WASMGIT_HOME", str(tmp_path_factory.mktemp("home")))
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.com")
    example = load_example()

    def run(*argv: str, cwd: Path = tmp_path) -> int:
        return example.run(
            list(argv), cwd=str(cwd), wasm=str(EXAMPLE / "git.wasm"), real_wasm=str(EXAMPLE / "git-real.wasm")
        )

    yield run


def skip_on_bun(backend: str) -> None:
    if backend == "bun":
        pytest.skip(
            "Bun 1.4.2 crashes (segmentation fault or illegal instruction) in git-real.wasm: on `git checkout` of "
            "another branch, and on `git commit` from a subdirectory with the stat of fusedwasi (node, JSC and wasmtime "
            "run it fine)"
        )


def real_git(*args: str, cwd: Path) -> str:
    assert GIT
    return subprocess.run([GIT, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def test_a_local_repository_from_a_subdirectory(
    git: Callable[..., int], tmp_path: Path, capfd: pytest.CaptureFixture[str], session: str
) -> None:
    skip_on_bun(session)
    run = git
    assert run("init") == 0
    deep = tmp_path / "sub" / "deep"
    deep.mkdir(parents=True)
    (deep / "f.txt").write_text("hi\n")
    (tmp_path / "root.txt").write_text("r\n")

    assert run("add", "f.txt", cwd=deep) == 0  # a path is relative to where the user is
    capfd.readouterr()
    assert run("status", "-s", cwd=deep) == 0
    assert sorted(capfd.readouterr().out.split("\n")) == [
        "",
        "?? ../../root.txt",
        "A  f.txt",
    ]  # relative to where we are

    assert run("commit", "-m", "first", cwd=deep) == 0
    (deep / "f.txt").write_text("hi\nmore\n")
    capfd.readouterr()
    assert run("diff", cwd=deep) == 0
    assert "+more" in capfd.readouterr().out

    assert run("branch", "dev") == 0
    assert run("checkout", "dev") == 0
    assert run("log", "-n", "1") == 0
    assert "first" in capfd.readouterr().out

    if GIT:  # the repository is a real one
        subprocess.run([GIT, "fsck", "--strict"], cwd=tmp_path, check=True, capture_output=True)
        assert "dev" in real_git("branch", cwd=tmp_path)
        assert real_git("log", "--format=%an <%ae> %s", cwd=tmp_path).strip() == "Test <test@example.com> first"


def test_merge_rebase_reset_and_tag_are_gits_own(git: Callable[..., int], tmp_path: Path, session: str) -> None:
    skip_on_bun(session)
    run = git
    assert run("init") == 0

    def commit(name: str, branch: str | None = None) -> None:
        if branch:
            assert run("checkout", "-b", branch) == 0
        (tmp_path / name).write_text(name)
        assert run("add", "-A") == 0
        assert run("commit", "-m", name) == 0

    commit("base.txt")
    commit("feat.txt", "feat")
    assert run("checkout", "main") == 0
    commit("main.txt")
    assert run("merge", "feat", "-m", "merged") == 0  # a real merge, with a merge commit and no editor
    assert run("tag", "-a", "v1", "-m", "release") == 0
    assert run("reset", "--hard", "HEAD~1") == 0
    assert not (tmp_path / "feat.txt").exists()
    assert run("rebase", "feat") == 0  # main.txt on top of feat
    assert (tmp_path / "feat.txt").exists()
    if GIT:
        assert real_git("tag", cwd=tmp_path).split() == ["v1"]
        assert real_git("log", "--format=%s", cwd=tmp_path).split() == ["main.txt", "feat.txt", "base.txt"]
        subprocess.run([GIT, "fsck", "--strict"], cwd=tmp_path, check=True, capture_output=True)


def test_a_command_outside_a_repository_fails(git: Callable[..., int], capfd: pytest.CaptureFixture[str]) -> None:
    run = git
    assert run("status") == 128
    assert "not a git repository" in capfd.readouterr().err


@contextlib.contextmanager
def serve(root: Path, *, auth: bool) -> Iterator[str]:
    """`git http-backend` over HTTP on a free port, with Basic auth (bob:s3cret) if AUTH."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def handle_any(self) -> None:
            if auth and self.headers.get("Authorization") != AUTH:
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="git"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            path, _, query = self.path.partition("?")
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if "chunked" in (self.headers.get("Transfer-Encoding") or ""):
                body = b""
                while size := int(self.rfile.readline().strip() or b"0", 16):
                    body += self.rfile.read(size)
                    self.rfile.readline()
                self.rfile.readline()
            env = {
                "GIT_PROJECT_ROOT": str(root), "GIT_HTTP_EXPORT_ALL": "1", "PATH_INFO": path, "QUERY_STRING": query,
                "REQUEST_METHOD": self.command, "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                "CONTENT_LENGTH": str(len(body)), "REMOTE_USER": "bob", "REMOTE_ADDR": "127.0.0.1",
                "GIT_COMMITTER_NAME": "S", "GIT_COMMITTER_EMAIL": "s@example.com", "PATH": os.environ["PATH"],
            }  # fmt: skip
            if encoding := self.headers.get("Content-Encoding"):
                env["HTTP_CONTENT_ENCODING"] = encoding
            assert GIT
            out = subprocess.run([GIT, "http-backend"], input=body, env=env, capture_output=True, check=False).stdout
            head, _, payload = out.partition(b"\r\n\r\n")
            status, headers = 200, []
            for line in head.decode().split("\r\n"):
                key, _, value = line.partition(": ")
                if key.lower() == "status":
                    status = int(value.split()[0])
                elif key:
                    headers.append((key, value))
            self.send_response(status)
            for key, value in headers:
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = handle_any

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.skipif(GIT is None, reason="needs git to serve a repository")
def test_clone_commit_push_and_pull_over_the_host_network(
    git: Callable[..., int], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session: str
) -> None:
    skip_on_bun(session)
    run = git
    served = tmp_path / "served"
    bare = served / "r.git"
    bare.mkdir(parents=True)
    real_git("init", "--bare", "-b", "main", cwd=bare)
    real_git("config", "http.receivepack", "true", cwd=bare)
    seed = tmp_path / "seed"
    seed.mkdir()
    real_git("init", "-b", "main", cwd=seed)
    (seed / "a.txt").write_text("one\n")
    real_git("add", ".", cwd=seed)
    real_git("-c", "user.name=S", "-c", "user.email=s@example.com", "commit", "-m", "seed", cwd=seed)
    real_git("push", str(bare), "main", cwd=seed)

    with serve(served, auth=True) as base:
        work = tmp_path / "work"
        work.mkdir()
        url = f"{base}/r.git"

        assert run("clone", url, cwd=work) == 1  # no credentials: it asks for them
        assert not (work / "r").exists()

        monkeypatch.setenv("GIT_USERNAME", "bob")
        monkeypatch.setenv("GIT_TOKEN", "s3cret")
        assert run("clone", url, cwd=work) == 0
        clone = work / "r"
        assert (clone / "a.txt").read_text() == "one\n"

        (clone / "b.txt").write_text("two\n")
        assert run("add", "b.txt", cwd=clone) == 0
        assert run("commit", "-m", "add b", cwd=clone) == 0
        assert run("push", cwd=clone) == 0
        assert real_git("log", "--format=%s", "main", cwd=bare).split() == ["add", "b", "seed"]

        other = tmp_path / "other"
        real_git("clone", str(bare), str(other), cwd=tmp_path)
        (other / "c.txt").write_text("three\n")
        real_git("add", ".", cwd=other)
        real_git("-c", "user.name=O", "-c", "user.email=o@example.com", "commit", "-m", "add c", cwd=other)
        real_git("push", "origin", "main", cwd=other)

        assert run("pull", cwd=clone) == 0
        assert (clone / "c.txt").read_text() == "three\n"
        assert real_git("log", "--format=%s", cwd=clone).split("\n")[0] == "add c"
        # both sides move: pull is a fetch by libgit2 and a merge (with a merge commit) by git
        (other / "d.txt").write_text("four\n")
        real_git("add", ".", cwd=other)
        real_git("-c", "user.name=O", "-c", "user.email=o@example.com", "commit", "-m", "add d", cwd=other)
        real_git("push", "origin", "main", cwd=other)
        (clone / "e.txt").write_text("five\n")
        assert run("add", "e.txt", cwd=clone) == 0
        assert run("commit", "-m", "add e", cwd=clone) == 0
        assert run("pull", cwd=clone) == 0
        assert (clone / "d.txt").exists()
        assert len(real_git("log", "--merges", "--format=%h", cwd=clone).split()) == 1
        assert run("push", cwd=clone) == 0
        assert real_git("log", "--format=%s", "-1", "main", cwd=bare).startswith("Merge")

        # a new branch, pushed with -u: the upstream is set
        assert run("checkout", "-b", "topic", cwd=clone) == 0
        (clone / "t.txt").write_text("t\n")
        assert run("add", "t.txt", cwd=clone) == 0
        assert run("commit", "-m", "topic", cwd=clone) == 0
        assert run("push", "-u", "origin", "topic", cwd=clone) == 0
        assert real_git("log", "--format=%s", "-1", "topic", cwd=bare).strip() == "topic"
        assert real_git("rev-parse", "--abbrev-ref", "topic@{u}", cwd=clone).strip() == "origin/topic"
        subprocess.run([GIT or "", "fsck", "--strict"], cwd=clone, check=True, capture_output=True)
