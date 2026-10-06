"""A POSIX shell in WebAssembly on an in-memory file system: BusyBox `ash` and about fifty utilities, in wasmhost.

    python examples/wasi_sh.py                              # a prompt; `exit` or the end of the input leaves
    python examples/wasi_sh.py -c 'echo hi | tr a-z A-Z'    # one command line, then exit (with its exit status)
    python examples/wasi_sh.py --backend node               # pick the engine (default: wasmhost.get_backend())
    python examples/wasi_sh.py --home                       # the real files under ~ (`/` to the shell)
    python examples/wasi_sh.py --root DIR [--readonly]      # the same for another real directory

    $ x=5; echo $((x * 2))
    10
    $ printf 'b\\na\\nc\\n' > list.txt
    $ sort list.txt | tr a-z A-Z
    A
    B
    C
    $ for f in a b; do echo $f > $f.txt; done; ls
    a.txt
    b.txt
    dev
    list.txt
    tmp
    $ cat <<EOF | wc -l
    > one
    > two
    > EOF
    2

It is made for Pythonista 3 on an iPhone (and PythonIDE): Python 3.10, the standard library and wasmhost, nothing else,
no process to start and nothing to install. wasmhost is the engine (there `jscontext`, Apple's JavaScriptCore); this
file is the rest of the computer a program expects: a WASI host in Python (`class Wasi`) over a file system that is a
dict (`class Vfs`). By default nothing here touches a real file: the tree (`/`, `/tmp`, `/dev/null`) and the pipes are
Python objects, and they are gone when the script ends.

With `--home` (or `--root DIR`, `class RealVfs`) the shell works on the real files instead: `~` (in Pythonista, the
data container of the app, with `Documents/` in it) is `/` to it. Inside it everything is allowed and is real: it can
read, create, change and delete any file or directory there, hidden ones too, and no question is asked. Outside it
nothing can be named, as with the directories a WASI program is given: `..` stops at the root, `/etc/passwd` means
`~/etc/passwd`, and a symbolic link is followed only if what it points to is inside as well (one that leads out is
refused with an error). `--readonly` lets the shell read and nothing else. Names are the real ones, in UTF-8 (bytes
that are not UTF-8 are kept as they are); on iOS APFS does not tell `A` from `a`, a file iCloud has not downloaded is a
small `.name.icloud` file, and a very large directory is listed whole when `ls` reads it.
`/dev/null` and `/tmp` are not made there: make a `tmp` folder if a script wants one.

Works: `cd`, `$(...)`, pipes, `>`, `>>`, `<`, here-documents, functions, `if`/`for`/`while`/`case`, `read`, `test`,
`printf`, arithmetic, `.` (source a file), and `cat ls cp mv rm mkdir find du touch stat grep sed awk sort uniq cut tr
head tail wc seq paste fold tac expr xxd hexdump md5sum sha256sum date env xargs`... (`type NAME` asks whether a name
exists). Does not: `( ... )`, `&`, jobs, a program of your own (there is no fork), and a nested `sh FILE` ends the whole
session (`. FILE` runs a script). There is no `sleep`, `tee`, `yes`, `ln` or `chmod`, and files have no permissions.

The program is `busybox.wasm` of wasi-sh (https://github.com/alganet/wasi-sh; its code is ISC, the BusyBox in the binary
GPL-2.0, with the sources and the build recipe in that project): ash made fork-free, 376 KB, a command (`_start`). The
first run downloads it from the npm registry, where the project releases it (an 820 KB tarball), checks it against a
SHA-256 and keeps it in the temporary directory (`tempfile.gettempdir()`); later runs download nothing.

What the host answers is the 27 WASI calls the module imports (arguments, environment, clock, files and directories,
`poll_oneoff`, `proc_exit`) and nine `env.__host_*` hooks of this build; the three that count, `__host_pipe`,
`__host_dup` and `__host_dup2`, are what let it have pipes and redirections without `fork`. All of it is in `Wasi`.

The build uses WebAssembly exception handling (for `setjmp`), so the engine must take it. Tried here: wasmtime, Node,
Bun, JavaScriptCore (`jsc`) and wasm3. `python -m wasmhost` is the self-test that says what an engine takes.

At the prompt the shell asks for each line when it wants one and the host answers with `input()`, which is all the
console of Pythonista (not a terminal, no `select`) allows. The shell prints its own prompts (`$ ` and `> `); it also
echoes the line, as it would to a terminal, and that echo is taken out because the console has shown the line already.
A tab in a line becomes spaces (the shell would take it for a request to complete a name).
"""

from __future__ import annotations

import argparse
import codecs
import errno
import hashlib
import io
import itertools
import os
import posixpath
import stat
import struct
import sys
import tarfile
import tempfile
import time
import urllib.request
from collections.abc import Callable
from typing import Any

import wasmhost

VERSION = "0.11.0"
WASM_URL = f"https://registry.npmjs.org/wasi-sh/-/wasi-sh-{VERSION}.tgz"  # the project's release on npm
WASM_MEMBER = "package/dist/busybox.wasm"
WASM_SHA256 = "195b770af8fad458de09144abd3cc3e688a9a83907ca90f7938a253253dfb9b3"
ENV = {"PATH": "/", "HOME": "/", "TERM": "dumb", "LANG": "C.UTF-8", "PS1": "$ ", "PS2": "> ", "USER": "root"}
# WASI errno numbers (they are not the platform's).
ESUCCESS, EBADF, EBUSY, EEXIST, EINVAL, EISDIR, ENOENT, ENOSYS = 0, 8, 10, 20, 28, 31, 44, 52
ENOTDIR, ENOTEMPTY, EPERM, EROFS, ESPIPE, ENOTCAPABLE = 54, 55, 63, 69, 70, 76
ERRNO = {  # the platform's errno of an OSError, as WASI numbers it
    errno.EACCES: 2, errno.EBADF: EBADF, errno.EBUSY: EBUSY, errno.EEXIST: EEXIST, errno.EFBIG: 22,
    errno.EINVAL: EINVAL, errno.EIO: 29, errno.EISDIR: EISDIR, errno.ELOOP: 32, errno.EMFILE: 33,
    errno.ENAMETOOLONG: 37, errno.ENOENT: ENOENT, errno.ENOMEM: 48, errno.ENOSPC: 51, errno.ENOSYS: ENOSYS,
    errno.ENOTDIR: ENOTDIR, errno.ENOTEMPTY: ENOTEMPTY, errno.EPERM: EPERM, errno.EROFS: EROFS,
    errno.ESPIPE: ESPIPE, errno.EXDEV: 75,
}  # fmt: skip
FILETYPE_CHAR, FILETYPE_DIR, FILETYPE_FILE = 2, 3, 4
RIGHT_READ, RIGHT_WRITE, RIGHT_ALLOCATE, RIGHT_SET_SIZE = 1 << 1, 1 << 6, 1 << 8, 1 << 22
ALL_RIGHTS = 0xFFFFFFFFFFFFFFFF
FIRST_FREE_FD = 5  # 0 to 2 are the standard streams, 3 and 4 the preopened directories


class WasiExit(Exception):  # noqa: N818 -- WASI's proc_exit
    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


class WasiError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


# --- the file systems: a dict, or a real directory. Both answer the same questions (`Wasi` asks only these).


def filetype_of(mode: int) -> int:
    if stat.S_ISDIR(mode):
        return FILETYPE_DIR
    if stat.S_ISREG(mode):
        return FILETYPE_FILE
    if stat.S_ISLNK(mode):
        return 7
    return FILETYPE_CHAR if stat.S_ISCHR(mode) else 0


def pack_stat(dev: int, ino: int, filetype: int, nlink: int, size: int, atime: int, mtime: int, ctime: int) -> bytes:
    return struct.pack("<QQBxxxxxxxQQQQQ", dev, ino, filetype, nlink, size, atime, mtime, ctime)


class Node:
    """A directory (`entries`), a regular file (`data`) or a character device that swallows what it is given."""

    def __init__(self, kind: str, ino: int) -> None:
        self.kind = kind  # "dir", "file" or "null"
        self.ino = ino
        self.data = bytearray()
        self.entries: dict[str, Node] = {}
        self.atime = self.mtime = self.ctime = time.time_ns()

    @property
    def filetype(self) -> int:
        return {"dir": FILETYPE_DIR, "file": FILETYPE_FILE}.get(self.kind, FILETYPE_CHAR)

    def stat(self) -> bytes:
        nlink = 2 if self.kind == "dir" else 1
        return pack_stat(1, self.ino, self.filetype, nlink, len(self.data), self.atime, self.mtime, self.ctime)


class Handle:
    """An open file, whatever it is kept in. A duplicated descriptor shares it; it is closed with the last one."""

    def __init__(self) -> None:
        self.refs = 1

    def read(self, offset: int, size: int) -> bytes:
        raise NotImplementedError

    def write(self, offset: int | None, data: bytes) -> int:
        """Write DATA at OFFSET (None: at the end); the offset after it."""
        raise NotImplementedError

    def size(self) -> int:
        raise NotImplementedError

    def stat(self) -> bytes:
        raise NotImplementedError

    def close(self) -> None:
        self.refs -= 1


class MemHandle(Handle):
    def __init__(self, node: Node) -> None:
        super().__init__()
        self.node = node

    def read(self, offset: int, size: int) -> bytes:
        return bytes(self.node.data[offset : offset + size])

    def write(self, offset: int | None, data: bytes) -> int:
        node = self.node
        if node.kind == "null":
            return 0 if offset is None else offset
        start = len(node.data) if offset is None else offset
        if start > len(node.data):
            node.data += bytes(start - len(node.data))
        node.data[start : start + len(data)] = data
        node.mtime = time.time_ns()
        return start + len(data)

    def size(self) -> int:
        return len(self.node.data)

    def stat(self) -> bytes:
        return self.node.stat()


class Vfs:
    """Directories and files in a dict, with absolute `/`-separated paths; no links, no permissions.

    A file stays alive as long as something holds its `Node` (an open descriptor), whatever its name does.
    """

    def __init__(self) -> None:
        self._inos = itertools.count(1)
        self.root = Node("dir", next(self._inos))
        dev = self.root.entries["dev"] = Node("dir", next(self._inos))  # not through `create`: /dev is guarded
        dev.entries["null"] = Node("null", next(self._inos))
        self.create("/tmp", "dir")

    @staticmethod
    def normal(path: str) -> str:
        return posixpath.normpath("/" + path.lstrip("/"))

    def find(self, path: str) -> Node:
        node = self.root
        for part in [p for p in path.split("/") if p]:
            if node.kind != "dir":
                raise WasiError(ENOTDIR)
            child = node.entries.get(part)
            if child is None:
                raise WasiError(ENOENT)
            node = child
        return node

    def parent(self, path: str) -> tuple[Node, str]:
        """The directory that holds PATH, and PATH's name in it."""
        head, name = posixpath.split(path)
        if not name:
            raise WasiError(EBUSY)  # the root
        directory = self.find(head or "/")
        if directory.kind != "dir":
            raise WasiError(ENOTDIR)
        return directory, name

    def guard(self, path: str) -> None:
        """/dev is the system's: nothing is made, removed or moved in it."""
        if path == "/dev" or path.startswith("/dev/"):
            raise WasiError(EPERM)

    def create(self, path: str, kind: str) -> Node:
        self.guard(path)
        directory, name = self.parent(path)
        if name in directory.entries:
            raise WasiError(EEXIST)
        node = directory.entries[name] = Node(kind, next(self._inos))
        directory.mtime = time.time_ns()
        return node

    def remove(self, path: str, directory: bool) -> None:
        self.guard(path)
        parent, name = self.parent(path)
        node = parent.entries.get(name)
        if node is None:
            raise WasiError(ENOENT)
        if directory:
            if node.kind != "dir":
                raise WasiError(ENOTDIR)
            if node.entries:
                raise WasiError(ENOTEMPTY)
        elif node.kind == "dir":
            raise WasiError(EISDIR)
        del parent.entries[name]
        parent.mtime = time.time_ns()

    def rename(self, source: str, target: str) -> None:
        self.guard(source)
        self.guard(target)
        old_parent, old_name = self.parent(source)
        node = old_parent.entries.get(old_name)
        if node is None:
            raise WasiError(ENOENT)
        new_parent, new_name = self.parent(target)
        if node.kind == "dir" and (target + "/").startswith(source + "/"):
            if target == source:
                return
            raise WasiError(EINVAL)  # a directory into itself
        existing = new_parent.entries.get(new_name)
        if existing is node:
            return
        if existing is not None:
            if existing.kind == "dir":
                if node.kind != "dir":
                    raise WasiError(EISDIR)
                if existing.entries:
                    raise WasiError(ENOTEMPTY)
            elif node.kind == "dir":
                raise WasiError(ENOTDIR)
        del old_parent.entries[old_name]
        new_parent.entries[new_name] = node

    # What `Wasi` asks of a file system.

    def stat(self, path: str, follow: bool = True) -> bytes:
        return self.find(path).stat()  # (no links here)

    def open(self, path: str, oflags: int, read: bool, write: bool) -> Handle | None:
        """The file at PATH, made (oflags 1), emptied (8) as the flags say; None for a directory."""
        try:
            node = self.find(path)
        except WasiError as exc:
            if exc.code != ENOENT or not oflags & 1:
                raise
            node = self.create(path, "file")
        else:
            if oflags & 1 and oflags & 4:
                raise WasiError(EEXIST)
        if node.kind == "dir":
            if write:
                raise WasiError(EISDIR)
            return None
        if oflags & 2:
            raise WasiError(ENOTDIR)
        if oflags & 8 and node.kind == "file":
            node.data = bytearray()
            node.mtime = time.time_ns()
        return MemHandle(node)

    def create_dir(self, path: str) -> None:
        self.create(path, "dir")

    def listing(self, path: str) -> list[tuple[str, int, int]]:
        """(name, inode, filetype) of what is in the directory, with `.` and `..`, in the order `ls` of this build
        wants: it does not sort, and lists what it is given from the last name to the first."""
        directory = self.find(path)
        parent = self.find(posixpath.dirname(path))
        names = sorted(directory.entries.items(), key=lambda item: item[0], reverse=True)
        return [(name, node.ino, node.filetype) for name, node in names] + [
            ("..", parent.ino, FILETYPE_DIR),
            (".", directory.ino, FILETYPE_DIR),
        ]

    def set_times(self, path: str, atime: int | None, mtime: int | None) -> None:
        node = self.find(path)
        if atime is not None:
            node.atime = atime
        if mtime is not None:
            node.mtime = mtime

    # What the program of a script of yours may want: put a file in, take one out.

    def write_file(self, path: str, data: bytes) -> None:
        path = self.normal(path)
        try:
            node = self.find(path)
        except WasiError:
            node = self.create(path, "file")
        node.data = bytearray(data)

    def read_file(self, path: str) -> bytes:
        node = self.find(self.normal(path))
        if node.kind != "file":
            raise WasiError(EISDIR if node.kind == "dir" else EINVAL)
        return bytes(node.data)


class RealHandle(Handle):
    def __init__(self, osfd: int) -> None:
        super().__init__()
        self.osfd = osfd

    def read(self, offset: int, size: int) -> bytes:
        return os.pread(self.osfd, size, offset)

    def write(self, offset: int | None, data: bytes) -> int:
        start = os.fstat(self.osfd).st_size if offset is None else offset
        done = 0
        while done < len(data):
            done += os.pwrite(self.osfd, data[done:], start + done)
        return start + done

    def size(self) -> int:
        return os.fstat(self.osfd).st_size

    def stat(self) -> bytes:
        return RealVfs.pack(os.fstat(self.osfd))

    def close(self) -> None:
        self.refs -= 1
        if self.refs == 0:
            os.close(self.osfd)


class RealVfs:
    """A real directory as `/`: the shell can read, change and delete what is in it, and can name nothing outside.

    Every name is looked up inside ROOT: `..` stops at it, an absolute path starts from it, and a symbolic link that
    leads out of it is refused (a link may be followed only to something that is inside). READONLY refuses every change.
    """

    def __init__(self, root: str, readonly: bool = False) -> None:
        self.root = os.path.realpath(os.path.expanduser(root))
        os.makedirs(self.root, exist_ok=True)
        self.readonly = readonly

    normal = staticmethod(Vfs.normal)

    def host(self, path: str, follow: bool = True) -> str:
        """The real path of PATH. With FOLLOW false the last name is not looked through (to remove or rename a link)."""
        rel = path.lstrip("/")
        full = os.path.join(self.root, rel) if rel else self.root
        real = os.path.realpath(full)
        if not follow and rel:
            real = os.path.join(os.path.realpath(os.path.dirname(full)), os.path.basename(full))
        if real != self.root and not real.startswith(self.root + os.sep):
            raise WasiError(ENOTCAPABLE)
        return full

    def changing(self) -> None:
        if self.readonly:
            raise WasiError(EROFS)

    @staticmethod
    def pack(st: os.stat_result) -> bytes:
        return pack_stat(
            st.st_dev, st.st_ino, filetype_of(st.st_mode), st.st_nlink, st.st_size, st.st_atime_ns, st.st_mtime_ns,
            st.st_ctime_ns,
        )  # fmt: skip

    def stat(self, path: str, follow: bool = True) -> bytes:
        full = self.host(path, follow)
        return self.pack(os.stat(full) if follow else os.lstat(full))

    def open(self, path: str, oflags: int, read: bool, write: bool) -> Handle | None:
        full = self.host(path)
        try:
            exists = os.stat(full)
        except FileNotFoundError:
            exists = None
        if exists is None:
            if not oflags & 1:
                raise WasiError(ENOENT)
            self.changing()
        elif oflags & 1 and oflags & 4:
            raise WasiError(EEXIST)
        if exists is not None and stat.S_ISDIR(exists.st_mode):
            if write:
                raise WasiError(EISDIR)
            return None
        if oflags & 2:
            raise WasiError(ENOTDIR)
        if write or oflags & 8:
            self.changing()
        flags = os.O_RDWR if read and write else os.O_WRONLY if write else os.O_RDONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= (
            (os.O_CREAT if oflags & 1 else 0) | (os.O_EXCL if oflags & 4 else 0) | (os.O_TRUNC if oflags & 8 else 0)
        )
        return RealHandle(os.open(full, flags, 0o666))

    def create_dir(self, path: str) -> None:
        self.changing()
        os.mkdir(self.host(path, follow=False))

    def remove(self, path: str, directory: bool) -> None:
        self.changing()
        full = self.host(path, follow=False)
        if directory:
            os.rmdir(full)
        else:
            if os.path.isdir(full) and not os.path.islink(full):
                raise WasiError(EISDIR)
            os.unlink(full)

    def rename(self, source: str, target: str) -> None:
        self.changing()
        os.rename(self.host(source, follow=False), self.host(target, follow=False))

    def listing(self, path: str) -> list[tuple[str, int, int]]:
        full = self.host(path)
        out = []
        for name in sorted(os.listdir(full), reverse=True):
            st = os.lstat(os.path.join(full, name))  # a link is told as one, and not looked through
            out.append((name, st.st_ino, filetype_of(st.st_mode)))
        here = os.stat(full)
        up = os.stat(self.host(posixpath.dirname(path)))
        return [*out, ("..", up.st_ino, FILETYPE_DIR), (".", here.st_ino, FILETYPE_DIR)]

    def set_times(self, path: str, atime: int | None, mtime: int | None) -> None:
        self.changing()
        full = self.host(path)
        st = os.stat(full)
        os.utime(full, ns=(st.st_atime_ns if atime is None else atime, st.st_mtime_ns if mtime is None else mtime))


# --- descriptors


class Pipe:
    def __init__(self) -> None:
        self.buffer = bytearray()


class Fd:
    """An open descriptor: a standard stream, a directory (by path), a file (by handle) or an end of a pipe."""

    def __init__(
        self,
        kind: str,
        path: str = "",
        handle: Handle | None = None,
        pipe: Pipe | None = None,
        readable: bool = True,
        writable: bool = True,
        append: bool = False,
    ) -> None:
        self.kind = kind  # "stdin", "stdout", "stderr", "dir", "file", "pipe"
        self.path = path
        self.handle = handle
        self.pipe = pipe
        self.readable = readable
        self.writable = writable
        self.append = append
        self.offset = [0]  # a cell: a duplicate shares it, as POSIX makes descriptors of one open file do
        self.listing: list[tuple[str, int, int]] | None = None  # a directory being read

    def duplicate(self) -> Fd:
        other = Fd(self.kind, self.path, self.handle, self.pipe, self.readable, self.writable, self.append)
        other.offset = self.offset
        if self.handle is not None:
            self.handle.refs += 1
        return other

    def release(self) -> None:
        if self.handle is not None:
            self.handle.close()


class Wasi:
    """The `wasi_snapshot_preview1` calls of busybox.wasm, and its `env.__host_*` hooks, over a `Vfs`.

    STDIN is called with the number of bytes wanted and gives bytes (empty: the end of the input); STDOUT and STDERR are
    called with bytes. A method has the name of the call it answers and returns nothing, or raises `WasiError`.
    """

    def __init__(
        self,
        vfs: Vfs | RealVfs,
        argv: list[str],
        stdin: Callable[[int], bytes],
        stdout: Callable[[bytes], None],
        stderr: Callable[[bytes], None],
        env: dict[str, str] | None = None,
    ) -> None:
        self.vfs = vfs
        self.argv = argv
        self.env = [f"{k}={v}" for k, v in (env or {}).items()]
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self.fds: dict[int, Fd] = {0: Fd("stdin"), 1: Fd("stdout"), 2: Fd("stderr")}
        self.fds[3] = Fd("dir", "/")  # the root, preopened as "/" and as ".": the C library tries both
        self.fds[4] = Fd("dir", "/")
        self.preopens = {3: b"/", 4: b"."}
        self._mem: Any = None

    # --- running

    def run(self, module: wasmhost.Module) -> int:
        """Run the module's `_start`; its exit status."""
        wasi: dict[str, Callable[..., int]] = {}
        for imp in wasmhost.Module.imports(module):
            if imp.module == "wasi_snapshot_preview1":
                wasi[imp.name] = self._bind(imp.name)
        env = {
            "__host_pipe": self.host_pipe,
            "__host_dup": self.host_dup,
            "__host_dup2": self.host_dup2,
            "__host_trace": lambda _value: None,
            "__host_winsize": self.host_winsize,
            "__host_winch": lambda: 0,
            "__host_interrupt": lambda: 0,
            "__host_builtin_lookup": lambda _name, _length: 0,  # no commands of the host's own
            "__host_builtin_run": lambda _cwd, _argc, _argv, _envp: -1,
        }
        instance = wasmhost.Instance(module, {"wasi_snapshot_preview1": wasi, "env": env})
        self._mem = instance.exports.memory
        try:
            instance.exports._start()
        except WasiExit as exit_:
            return exit_.code
        return 0

    def _bind(self, name: str) -> Callable[..., int]:
        method = getattr(self, name, None)
        if method is None:
            return lambda *args: ENOSYS

        def call(*args: int) -> int:
            try:
                method(*args)
            except WasiError as exc:
                return exc.code
            except OSError as exc:
                return ERRNO.get(exc.errno or 0, 29)
            return ESUCCESS

        return call

    # --- memory

    @property
    def mem(self) -> Any:
        if self._mem is None:
            raise RuntimeError("the program is not running")
        return self._mem

    def put32(self, ptr: int, value: int) -> None:
        self.mem.write(ptr, struct.pack("<I", value & 0xFFFFFFFF))

    def put64(self, ptr: int, value: int) -> None:
        self.mem.write(ptr, struct.pack("<Q", value & 0xFFFFFFFFFFFFFFFF))

    def iovs(self, ptr: int, count: int) -> list[tuple[int, int]]:
        return list(struct.iter_unpack("<II", self.mem.read(ptr, 8 * count)))

    def string(self, ptr: int, size: int) -> str:
        return self.mem.read(ptr, size).decode("utf-8", "surrogateescape")

    # --- descriptors and paths

    def entry(self, fd: int) -> Fd:
        entry = self.fds.get(fd)
        if entry is None:
            raise WasiError(EBADF)
        return entry

    def resolve(self, fd: int, ptr: int, size: int) -> str:
        """The absolute path in the file system that the name at PTR means, from the directory FD."""
        base = self.entry(fd)
        if base.kind != "dir":
            raise WasiError(ENOTDIR)
        name = self.string(ptr, size)
        return self.vfs.normal(name if name.startswith("/") else posixpath.join(base.path, name))

    def new_fd(self, entry: Fd, at_least: int = FIRST_FREE_FD) -> int:
        fd = max(at_least, FIRST_FREE_FD)
        while fd in self.fds:
            fd += 1
        self.fds[fd] = entry
        return fd

    # --- the hooks of this build of busybox: pipes and descriptor copies, which `fork` and `pipe` would have done

    def host_pipe(self, ptr: int) -> int:
        pipe = Pipe()
        read_end = self.new_fd(Fd("pipe", pipe=pipe, writable=False))
        write_end = self.new_fd(Fd("pipe", pipe=pipe, readable=False))
        self.mem.write(ptr, struct.pack("<II", read_end, write_end))
        return 0

    def host_dup(self, fd: int, at_least: int) -> int:
        entry = self.fds.get(fd)
        return -1 if entry is None else self.new_fd(entry.duplicate(), at_least)

    def host_dup2(self, fd: int, new: int) -> int:
        entry = self.fds.get(fd)
        if entry is None:
            return -1
        if fd != new:
            old = self.fds.get(new)
            self.fds[new] = entry.duplicate()
            if old is not None:
                old.release()
        return new

    def host_winsize(self, rows_ptr: int, cols_ptr: int) -> None:
        self.put32(rows_ptr, 0)  # unknown: the shell falls back to $LINES and $COLUMNS
        self.put32(cols_ptr, 0)

    # --- arguments, environment, clock, exit

    def args_sizes_get(self, count_ptr: int, size_ptr: int) -> None:
        self.put32(count_ptr, len(self.argv))
        self.put32(size_ptr, sum(len(a.encode()) + 1 for a in self.argv))

    def args_get(self, argv_ptr: int, buf_ptr: int) -> None:
        self.strings(self.argv, argv_ptr, buf_ptr)

    def environ_sizes_get(self, count_ptr: int, size_ptr: int) -> None:
        self.put32(count_ptr, len(self.env))
        self.put32(size_ptr, sum(len(e.encode()) + 1 for e in self.env))

    def environ_get(self, environ_ptr: int, buf_ptr: int) -> None:
        self.strings(self.env, environ_ptr, buf_ptr)

    def strings(self, items: list[str], table_ptr: int, buf_ptr: int) -> None:
        for item in items:
            raw = item.encode() + b"\0"
            self.put32(table_ptr, buf_ptr)
            self.mem.write(buf_ptr, raw)
            table_ptr += 4
            buf_ptr += len(raw)

    def clock_time_get(self, clock_id: int, precision: int, out: int) -> None:
        if clock_id == 0:
            self.put64(out, time.time_ns())
        else:
            self.put64(out, time.monotonic_ns())  # monotonic, and the two CPU clocks (near enough)

    def proc_exit(self, code: int) -> None:
        raise WasiExit(code)

    def poll_oneoff(self, in_ptr: int, out_ptr: int, count: int, nevents_ptr: int) -> None:
        subscriptions = [self.mem.read(in_ptr + 48 * i, 48) for i in range(count)]
        readable = [raw for raw in subscriptions if raw[8] == 1]  # a descriptor to read: always ready (a read may wait)
        events = []
        if readable:
            for raw in readable:
                userdata = struct.unpack_from("<Q", raw, 0)[0]
                events.append(struct.pack("<QHBxxxxxQHxxxxxx", userdata, ESUCCESS, 1, 0, 0))
        else:  # clocks only: sleep until the first one
            waits = []
            for raw in subscriptions:
                userdata = struct.unpack_from("<Q", raw, 0)[0]
                clock_id, timeout, _precision, flags = struct.unpack_from("<IxxxxQQH", raw, 16)
                if flags & 1:  # an absolute time
                    timeout = max(0, timeout - (time.time_ns() if clock_id == 0 else time.monotonic_ns()))
                waits.append((timeout, userdata))
            timeout, userdata = min(waits)
            time.sleep(timeout / 1e9)
            events.append(struct.pack("<QHBxxxxxQHxxxxxx", userdata, ESUCCESS, 0, 0, 0))
        self.mem.write(out_ptr, b"".join(events))
        self.put32(nevents_ptr, len(events))

    # --- the preopened directories

    def fd_prestat_get(self, fd: int, buf: int) -> None:
        if fd not in self.preopens:
            raise WasiError(EBADF)
        self.mem.write(buf, struct.pack("<BxxxI", 0, len(self.preopens[fd])))  # a directory, and its name's size

    def fd_prestat_dir_name(self, fd: int, path: int, size: int) -> None:
        if fd not in self.preopens:
            raise WasiError(EBADF)
        self.mem.write(path, self.preopens[fd][:size])

    # --- reading and writing

    def fd_close(self, fd: int) -> None:
        entry = self.entry(fd)
        if fd >= FIRST_FREE_FD:
            del self.fds[fd]
            entry.release()

    def fd_fdstat_get(self, fd: int, buf: int) -> None:
        entry = self.entry(fd)
        kind = {"dir": FILETYPE_DIR, "file": FILETYPE_FILE}.get(entry.kind, FILETYPE_CHAR)
        self.mem.write(buf, struct.pack("<BxHxxxxQQ", kind, 0, ALL_RIGHTS, ALL_RIGHTS))

    def fd_fdstat_set_flags(self, fd: int, flags: int) -> None:
        self.entry(fd)  # non-blocking has no meaning here: nothing waits but the keyboard

    def fd_filestat_get(self, fd: int, buf: int) -> None:
        entry = self.entry(fd)
        if entry.handle is not None:
            self.mem.write(buf, entry.handle.stat())
        elif entry.kind == "dir":
            self.mem.write(buf, self.vfs.stat(entry.path))
        else:
            self.mem.write(buf, struct.pack("<QQBxxxxxxxQQQQQ", 0, 0, FILETYPE_CHAR, 1, 0, 0, 0, 0))

    def fd_read(self, fd: int, iovs: int, count: int, out: int) -> None:
        entry = self.entry(fd)
        total = 0
        for buf, size in self.iovs(iovs, count):
            data = self.take(entry, size)
            self.mem.write(buf, data)
            total += len(data)
            if len(data) < size:
                break
        self.put32(out, total)

    def take(self, entry: Fd, size: int) -> bytes:
        if not entry.readable:
            raise WasiError(EBADF)
        if entry.kind == "stdin":
            return self.stdin(size)
        if entry.kind == "pipe" and entry.pipe is not None:  # what is not there is the end: the writer has run already
            data = bytes(entry.pipe.buffer[:size])
            del entry.pipe.buffer[:size]
            return data
        if entry.kind == "file" and entry.handle is not None:
            data = entry.handle.read(entry.offset[0], size)
            entry.offset[0] += len(data)
            return data
        raise WasiError(EISDIR if entry.kind == "dir" else EBADF)

    def fd_write(self, fd: int, iovs: int, count: int, out: int) -> None:
        entry = self.entry(fd)
        total = 0
        for buf, size in self.iovs(iovs, count):
            self.give(entry, self.mem.read(buf, size))
            total += size
        self.put32(out, total)

    def give(self, entry: Fd, data: bytes) -> None:
        if not entry.writable:
            raise WasiError(EBADF)
        if entry.kind == "stdout":
            self.stdout(data)
        elif entry.kind == "stderr":
            self.stderr(data)
        elif entry.kind == "pipe" and entry.pipe is not None:
            entry.pipe.buffer += data
        elif entry.kind == "file" and entry.handle is not None:
            entry.offset[0] = entry.handle.write(None if entry.append else entry.offset[0], data)
        else:
            raise WasiError(EISDIR if entry.kind == "dir" else EBADF)

    def fd_seek(self, fd: int, offset: int, whence: int, out: int) -> None:
        entry = self.entry(fd)
        if entry.kind != "file" or entry.handle is None:
            raise WasiError(ESPIPE)
        offset = offset - (1 << 64) if offset >= 1 << 63 else offset
        base = (0, entry.offset[0], entry.handle.size())[whence] if whence in (0, 1, 2) else None
        if base is None or base + offset < 0:
            raise WasiError(EINVAL)
        entry.offset[0] = base + offset
        self.put64(out, entry.offset[0])

    def fd_readdir(self, fd: int, buf: int, size: int, cookie: int, used_ptr: int) -> None:
        entry = self.entry(fd)
        if entry.kind != "dir":
            raise WasiError(ENOTDIR)
        if cookie == 0 or entry.listing is None:  # the names as they are when the reading starts, whatever is removed
            entry.listing = self.vfs.listing(entry.path)
        names = entry.listing
        out = b""
        for index in range(cookie, len(names)):
            name, ino, filetype = names[index]
            raw = name.encode("utf-8", "surrogateescape")  # a name of the real file system may not be UTF-8
            out += struct.pack("<QQIBxxx", index + 1, ino, len(raw), filetype) + raw
            if len(out) >= size:
                break
        out = out[:size]  # a cut entry tells the caller there is more
        self.mem.write(buf, out)
        self.put32(used_ptr, len(out))

    # --- paths

    def path_open(
        self,
        dirfd: int,
        dirflags: int,
        ptr: int,
        size: int,
        oflags: int,
        rights: int,
        inheriting: int,
        fdflags: int,
        out: int,
    ) -> None:
        path = self.resolve(dirfd, ptr, size)
        write = bool(rights & (RIGHT_WRITE | RIGHT_SET_SIZE | RIGHT_ALLOCATE))
        read = bool(rights & RIGHT_READ) or not write
        handle = self.vfs.open(path, oflags, read, write)
        if handle is None:  # a directory
            self.put32(out, self.new_fd(Fd("dir", path)))
            return
        entry = Fd("file", path, handle, readable=read, writable=write, append=bool(fdflags & 1))
        self.put32(out, self.new_fd(entry))

    def path_filestat_get(self, fd: int, flags: int, ptr: int, size: int, buf: int) -> None:
        self.mem.write(buf, self.vfs.stat(self.resolve(fd, ptr, size), bool(flags & 1)))

    def path_filestat_set_times(
        self, fd: int, flags: int, ptr: int, size: int, atim: int, mtim: int, fst_flags: int
    ) -> None:
        now = time.time_ns()
        atime = now if fst_flags & 2 else atim if fst_flags & 1 else None
        mtime = now if fst_flags & 8 else mtim if fst_flags & 4 else None
        self.vfs.set_times(self.resolve(fd, ptr, size), atime, mtime)

    def path_create_directory(self, fd: int, ptr: int, size: int) -> None:
        self.vfs.create_dir(self.resolve(fd, ptr, size))

    def path_remove_directory(self, fd: int, ptr: int, size: int) -> None:
        self.vfs.remove(self.resolve(fd, ptr, size), directory=True)

    def path_unlink_file(self, fd: int, ptr: int, size: int) -> None:
        self.vfs.remove(self.resolve(fd, ptr, size), directory=False)

    def path_rename(self, fd: int, ptr: int, size: int, new_fd: int, new_ptr: int, new_size: int) -> None:
        self.vfs.rename(self.resolve(fd, ptr, size), self.resolve(new_fd, new_ptr, new_size))

    def path_readlink(self, fd: int, ptr: int, size: int, buf: int, buf_size: int, used_ptr: int) -> None:
        self.vfs.stat(self.resolve(fd, ptr, size))
        raise WasiError(EINVAL)  # there are no links: whatever exists is not one

    def path_link(self, fd: int, flags: int, ptr: int, size: int, new_fd: int, new_ptr: int, new_size: int) -> None:
        raise WasiError(ENOSYS)

    def path_symlink(self, ptr: int, size: int, fd: int, new_ptr: int, new_size: int) -> None:
        raise WasiError(ENOSYS)


# --- the program and the shell


def download() -> bytes:
    """busybox.wasm out of the project's npm tarball."""
    with urllib.request.urlopen(WASM_URL, timeout=120) as resp:  # noqa: S310
        archive = resp.read()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        member = tar.extractfile(WASM_MEMBER)
        if member is None:
            raise RuntimeError(f"{WASM_MEMBER} is not in {WASM_URL}")
        return member.read()


def find_wasm(explicit: str | None = None) -> str:
    """The path of busybox.wasm: EXPLICIT, else the copy in the temporary directory, downloaded once."""
    if explicit:
        return explicit
    cached = os.path.join(tempfile.gettempdir(), f"wasi-sh-{VERSION}-busybox.wasm")
    if os.path.exists(cached):
        with open(cached, "rb") as f:
            if hashlib.sha256(f.read()).hexdigest() == WASM_SHA256:
                return cached
    print(f"downloading {WASM_URL} (820 KB)", flush=True)
    try:
        data = download()
    except Exception as exc:  # noqa: BLE001 -- any failure of the network, the archive or the disk
        raise SystemExit(
            f"could not download busybox.wasm: {exc}\n"
            f"Check the connection, or fetch {WASM_URL}, take {WASM_MEMBER} out of it and run with --wasm FILE."
        ) from exc
    if hashlib.sha256(data).hexdigest() != WASM_SHA256:
        raise SystemExit(f"busybox.wasm from {WASM_URL} is not the file this script knows (SHA-256 differs); stopped")
    with open(cached + ".part", "wb") as f:  # so that an interrupted download is not taken for a whole file
        f.write(data)
    os.replace(cached + ".part", cached)
    return cached


class Shell:
    """One ash, on a file system of its own. `run` is one session; its input is either LINES or a prompt (`input()`)."""

    def __init__(self, module: wasmhost.Module, vfs: Vfs | RealVfs | None = None) -> None:
        self.module = module
        self.vfs = vfs if vfs is not None else Vfs()

    @staticmethod
    def console(stream: Any) -> Callable[[bytes], None]:
        decoder = codecs.getincrementaldecoder("utf-8")("replace")

        def write(data: bytes) -> None:
            stream.write(decoder.decode(data))
            stream.flush()

        return write

    def run(
        self,
        command: str | None = None,
        stdout: Callable[[bytes], None] | None = None,
        stderr: Callable[[bytes], None] | None = None,
        lines: str | None = None,
    ) -> int:
        """Run `sh -c COMMAND` (with LINES as its standard input, if given), or, without COMMAND, a shell that reads
        its commands from LINES, or from the keyboard (`input()`) when there are none. The exit status."""
        interactive = command is None
        buffer = bytearray()
        pending = [b""]  # what the shell is going to echo of the line it was just given
        fixed = None if lines is None else lines.encode()
        queued = fixed.splitlines(keepends=True) if fixed is not None and interactive else []
        # At a prompt the two streams are one, as on a terminal (the console of Pythonista paints standard error red).
        err = stderr or self.console(sys.stdout if interactive else sys.stderr)

        def next_line() -> bytes:
            """The next line for the prompt: from LINES if there are some, else typed; empty at the end of the input."""
            if fixed is not None:
                return queued.pop(0) if queued else b""
            try:
                return input().encode() + b"\n"
            except EOFError:
                return b""  # the end of the input, which leaves the shell
            except KeyboardInterrupt:
                return b"\n"

        def stdin(size: int) -> bytes:
            nonlocal fixed
            if not buffer:
                if interactive:  # one line at a time, as a terminal gives it
                    line = next_line().expandtabs(8)  # a tab would ask the shell for a completion
                    buffer.extend(line)
                    pending[0] = line
                elif fixed is not None:  # fixed input for a command: all of it, then the end
                    buffer.extend(fixed)
                    fixed = None
            data = bytes(buffer[:size])
            del buffer[:size]
            return data

        greeting: bytearray | None = bytearray()  # what the shell says before its first prompt

        def stderr_without_echo(data: bytes) -> None:
            # An interactive ash edits its line itself, and so writes back each character it is given (to standard
            # error, in the same order): the console has shown them already, so they are taken out.
            nonlocal greeting
            if greeting is not None:
                greeting.extend(data)
                if b"$ " not in greeting:
                    return
                data = bytes(greeting).replace(b"sh: can't access tty; job control turned off\n", b"")
                greeting = None
            echo = pending[0]
            if echo and data:
                same = 0
                while same < min(len(data), len(echo)) and data[same] == echo[same]:
                    same += 1
                pending[0] = echo[same:] if same else b""
                data = data[same:]
            if data:
                err(data)

        argv = ["busybox", "sh", "-i"] if interactive else ["busybox", "sh", "-c", command or ""]
        out = stdout or self.console(sys.stdout)
        wasi = Wasi(self.vfs, argv, stdin, out, stderr_without_echo if interactive else err, env=ENV)
        return wasi.run(self.module)


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("-c", dest="command", metavar="LINE", help="run this command line and exit with its status")
    parser.add_argument("--backend", choices=sorted(wasmhost.BACKENDS), help="the engine (default: the first found)")
    parser.add_argument(
        "--root", metavar="DIR", help="a real directory to be `/` (made if missing); default: an in-memory file system"
    )
    parser.add_argument("--home", action="store_true", help="the real files under ~ are `/` (as --root ~)")
    parser.add_argument("--readonly", action="store_true", help="with --root: the shell can read the files, not change")
    parser.add_argument(
        "--wasm", help="the busybox.wasm to use (default: downloaded once into the temporary directory)"
    )
    args = parser.parse_args()

    if args.home:
        args.root = args.root or "~"
    if args.readonly and not args.root:
        parser.error("--readonly is for --home or --root DIR")
    path = find_wasm(args.wasm)
    backend = wasmhost.get_backend(args.backend)
    banner = f"BusyBox ash (wasi-sh {VERSION}) in WebAssembly, on the {backend.name} backend of wasmhost"
    if args.command is None:
        print(f"{banner}; compiling (once)...", flush=True)
    else:
        print(banner, file=sys.stderr, flush=True)
    try:
        with open(path, "rb") as f:
            module = wasmhost.Module(f.read(), backend=backend)
    except wasmhost.CompileError as exc:
        raise SystemExit(
            f"the {backend.name} backend can't take busybox.wasm (it uses WebAssembly exception handling): {exc}"
        ) from exc
    vfs = RealVfs(args.root, args.readonly) if args.root else Vfs()
    shell = Shell(module, vfs)
    if args.command is not None:
        return shell.run(args.command)
    if isinstance(vfs, RealVfs):
        print(f"REAL FILES: {vfs.root} is `/`{' (read only)' if vfs.readonly else ''}; changes and deletions are real.")
    else:
        print("An empty in-memory file system, gone when you leave. `exit` leaves. Try: ls /dev; echo hi | wc")
    return shell.run()


if __name__ == "__main__":
    sys.exit(main())
