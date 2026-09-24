"""`--wasm-backend`: run the suite on one specific backend.

    uv run pytest                        # every backend that can start here
    uv run pytest --wasm-backend node
    uv run pytest --wasm-backend wasmtime     # or wasm3, gi-jsc (WebKitGTK JavaScriptCore, needs PyGObject), jscontext

The choice is also put in WASMHOST_BACKEND, so anything that picks a backend itself (subprocesses, the examples)
uses it too. With `--wasm-backend`, a backend that can't start stops the run with an error: it is never silently
skipped, so a CI step named after a backend really ran on it. Without it, the backends that can't start here
are left out.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import wasmhost


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--wasm-backend", action="store", default=None, choices=sorted(wasmhost.BACKENDS))


def _available(name: str) -> str | None:
    """None when the backend starts here, else why not."""
    try:
        wasmhost.BACKENDS[name]().close()
    except Exception as exc:  # noqa: BLE001 -- any failure means "not available here"
        return str(exc)
    return None


def pytest_configure(config: pytest.Config) -> None:
    chosen: str | None = config.getoption("--wasm-backend")
    if chosen:
        os.environ["WASMHOST_BACKEND"] = chosen
        if (why := _available(chosen)) is not None:
            pytest.exit(f"Cannot start tests: backend {chosen} failed: {why}", returncode=1)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "backend" not in metafunc.fixturenames:
        return
    chosen: str | None = metafunc.config.getoption("--wasm-backend")
    names = [chosen] if chosen else [n for n in wasmhost.AUTO_ORDER if _available(n) is None]
    if not names:
        pytest.exit("Cannot start tests: no WebAssembly backend is available", returncode=1)
    metafunc.parametrize("backend", names)


def pytest_report_header(config: pytest.Config) -> str:
    chosen: str | None = config.getoption("--wasm-backend")
    names = [chosen] if chosen else [n for n in wasmhost.AUTO_ORDER if _available(n) is None]
    return f"wasmhost backends: {', '.join(names) or 'none'}"


@pytest.fixture
def session(backend: str) -> Iterator[str]:
    """A fresh backend per test, so tests can't leak modules or memory into each other."""
    wasmhost.close()
    wasmhost.set_backend(backend)
    yield backend
    wasmhost.close()
