"""`examples/pyodide.py`: Pyodide (CPython in WebAssembly) in a JavaScript engine, through wasmhost. A start takes
a few seconds and the package is downloaded (once per run, cached), so the test is skipped without a network and on
backends that are not JavaScript engines."""

from __future__ import annotations

import importlib.util
import sys
import urllib.error
from pathlib import Path
from types import ModuleType

import pytest

import wasmhost

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def load_example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("pyodide_example", EXAMPLES / "pyodide.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def cache(tmp_path_factory: pytest.TempPathFactory) -> str:
    return str(tmp_path_factory.mktemp("pyodide-cache"))


def test_python_runs_in_pyodide(session: str, cache: str, monkeypatch: pytest.MonkeyPatch) -> None:
    if session not in wasmhost.JS_BACKENDS:
        pytest.skip(f"the {session} backend is not a JavaScript engine")
    monkeypatch.setenv("WASMHOST_CACHE", cache)
    example = load_example()
    try:
        py = example.Pyodide(None, backend=session, snapshot=False)
    except (urllib.error.URLError, OSError) as exc:
        pytest.skip(f"the Pyodide package can't be downloaded here: {exc}")
    assert py.repl("sum(range(10))") == "45"
    assert "emscripten" in py.repl("import sys; sys.platform")  # the platform CPython reports: it is CPython in wasm
