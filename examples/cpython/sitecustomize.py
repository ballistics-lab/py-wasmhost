"""What WASI lacks and code written for POSIX imports without asking (pip: `os.getuid` in platformdirs, `os.umask`
in the installer, `import mmap` in cachecontrol, `socket.socketpair` in asyncio). `mkstdlib.py` puts this into the
standard library zip, so it runs at the start of every program; a name WASI does have is left alone. `mmap` can be
imported and cannot be used."""

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


# asyncio's selector loop wakes itself through a socket pair, which WASI cannot make. There are no threads or signals
# to wake it from, so it needs none: the loop's calls that use the pair do nothing. Patched when asyncio is imported,
# not now.
def _no_self_pipe(module):
    loop = module.BaseSelectorEventLoop
    loop._make_self_pipe = lambda self: setattr(self, "_ssock", None) or setattr(self, "_csock", None)
    loop._close_self_pipe = loop._write_to_self = lambda self, *args: None
    loop._read_from_self = lambda self: None


class _AfterImport:
    """A meta path finder that lets the normal ones find `name`, and calls `then` with the module once it has run."""

    def __init__(self, name, then):
        self.name, self.then = name, then

    def find_spec(self, fullname, path=None, target=None):
        if fullname != self.name:
            return None
        sys.meta_path.remove(self)
        import importlib.util

        spec = importlib.util.find_spec(fullname)
        if spec is None or spec.loader is None:
            return None
        exec_module, then = spec.loader.exec_module, self.then

        def run(module):
            exec_module(module)
            then(module)

        spec.loader.exec_module = run  # type: ignore[method-assign]
        return spec


sys.meta_path.insert(0, _AfterImport("asyncio.selector_events", _no_self_pipe))
