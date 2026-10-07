"""The `wasmhost` command: the entry point in pyproject.toml names a function that exists and runs."""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

import wasmhost  # noqa: F401 -- the package is on the path (conftest)


def test_the_entry_point_names_the_cli(capsys: pytest.CaptureFixture[str], session: str) -> None:
    text = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    found = re.search(r"^\[project\.scripts\]\s+wasmhost\s*=\s*\"([\w.]+):(\w+)\"", text, re.MULTILINE)
    assert found is not None, "no [project.scripts] wasmhost entry in pyproject.toml"
    main = getattr(importlib.import_module(found.group(1)), found.group(2))
    assert main(["self", "test", "--backend", session]) == 0  # canonical CLI form
    assert "passed" in capsys.readouterr().out
