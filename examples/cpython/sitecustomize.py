"""What WASI lacks and code written for POSIX imports without asking (pip: `os.getuid` in platformdirs, `os.umask`
in the installer, `import mmap` in cachecontrol). `mkstdlib.py` puts this into the standard library zip, so it runs at
the start of every program; a name WASI does have is left alone. `mmap` can be imported and cannot be used."""

import errno
import os
import sys
import types

for _name in ("getuid", "geteuid", "getgid", "getegid"):
    if not hasattr(os, _name):
        setattr(os, _name, lambda: 0)  # there is one user, and it owns what the program can see
if not hasattr(os, "umask"):
    os.umask = lambda mask: 0o022

try:
    import mmap  # noqa: F401
except ImportError:

    class _Mmap:
        def __init__(self, *args, **kwargs):
            raise OSError(errno.ENOSYS, "mmap is not available on WASI")

    _module = types.ModuleType("mmap")
    _module.mmap = _Mmap  # type: ignore[attr-defined]
    sys.modules["mmap"] = _module
