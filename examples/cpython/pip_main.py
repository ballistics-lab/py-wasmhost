"""pip, inside the WASI Python: run it as `/py/pip_main.py ARGS` (see run.py). The pip wheel is looked for in the
folders given with `--site`; WASI has no `os.getuid`, `os.umask` or `mmap`, which pip's own modules want, so they are
made up here.
What pip can do is what needs no processes and no sockets: install wheels of pure Python from a folder
(`--no-index --find-links DIR`, `--target DIR`), list, show, uninstall.
"""

import glob
import os
import sys
import types

for folder in sorted(glob.glob("/site*")):
    for wheel in glob.glob(folder + "/pip-*.whl"):
        sys.path.insert(0, wheel)
os.getuid = os.geteuid = lambda: 0
os.umask = lambda mask: 0o022
sys.modules.setdefault("mmap", types.ModuleType("mmap")).mmap = type("mmap", (), {})  # type: ignore[attr-defined]

from pip._internal.cli.main import main  # noqa: E402  # pyright: ignore[reportMissingImports]

sys.exit(main(sys.argv[1:]))
