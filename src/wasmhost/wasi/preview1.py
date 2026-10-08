"""`wasmhost.wasi.preview1`: a WASI host for `wasi_snapshot_preview1` (WASI 0.1), in plain Python.

The 46 functions of the specification (the `legacy/preview1` witx files of the WebAssembly/WASI repository, branch
`wasi-0.1`), over directories of the real file system, with standard streams, arguments, an environment, clocks and
random bytes. Sockets, threads and signals are not supported: those calls answer as the specification allows
(`ENOTSOCK`, `ENOSYS`). The first snapshot, `wasi_unstable` (what older toolchains still produce), is offered too,
from the same object: see UNSTABLE below for the four things in which it differs. Standard library only.

    wasi = Preview1(args=["prog", "-v"], preopens={"/": "some/dir"}, stdout=sys.stdout.buffer.write)
    code = wasi.run(module)           # the exit code of `_start`

or in steps, when the instance needs options of its own:

    instance = wasi.instantiate(module, timeout=5)
    code = wasi.start(instance)

`wasi.imports()` is the import object by itself, for an `Instance` you make yourself; `wasi.bind(instance)` then gives
the host the instance's memory.

A directory handed over is the whole world of the program: a name that leaves it (`..` past the directory, an
absolute name, a symbolic link that points out) is refused with `ENOTCAPABLE`. The check and the open are two
steps, so this is not proof against another process that changes the directory in between (B-503).
"""

from __future__ import annotations

import errno as _errno
import functools
import os
import stat
import struct
import sys
import time
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from typing import Any, Literal, Protocol, TypeVar

from .._api import Instance, Module
from .._binary import i32, i64

SNAPSHOT = "wasi_snapshot_preview1"

# The enumerations and sets of flags of typenames.witx, in the order of the file: an enumeration's value is the
# position of its name, a flag's bit is that position too (`Rights.fd_read == 1 << 1`).
TABLES: dict[str, tuple[str, ...]] = {
    k: tuple(v.split())
    for k, v in {
        "clockid": "realtime monotonic process_cputime_id thread_cputime_id",
        "errno": (
            "success 2big acces addrinuse addrnotavail afnosupport again already badf badmsg busy canceled child "
            "connaborted connrefused connreset deadlk destaddrreq dom dquot exist fault fbig hostunreach idrm "
            "ilseq inprogress intr inval io isconn isdir loop mfile mlink msgsize multihop nametoolong netdown "
            "netreset netunreach nfile nobufs nodev noent noexec nolck nolink nomem nomsg noprotoopt nospc nosys "
            "notconn notdir notempty notrecoverable notsock notsup notty nxio overflow ownerdead perm pipe proto "
            "protonosupport prototype range rofs spipe srch stale timedout txtbsy xdev notcapable"
        ),
        "rights": (
            "fd_datasync fd_read fd_seek fd_fdstat_set_flags fd_sync fd_tell fd_write fd_advise fd_allocate "
            "path_create_directory path_create_file path_link_source path_link_target path_open fd_readdir "
            "path_readlink path_rename_source path_rename_target path_filestat_get path_filestat_set_size "
            "path_filestat_set_times fd_filestat_get fd_filestat_set_size fd_filestat_set_times path_symlink "
            "path_remove_directory path_unlink_file poll_fd_readwrite sock_shutdown sock_accept"
        ),
        "whence": "set cur end",
        "filetype": (
            "unknown block_device character_device directory regular_file socket_dgram socket_stream symbolic_link"
        ),
        "advice": "normal sequential random willneed dontneed noreuse",
        "fdflags": "append dsync nonblock rsync sync",
        "fstflags": "atim atim_now mtim mtim_now",
        "lookupflags": "symlink_follow",
        "oflags": "creat directory excl trunc",
        "eventtype": "clock fd_read fd_write",
        "eventrwflags": "fd_readwrite_hangup",
        "subclockflags": "subscription_clock_abstime",
        "signal": (
            "none hup int quit ill trap abrt bus fpe kill usr1 segv usr2 pipe alrm term chld cont stop tstp ttin "
            "ttou urg xcpu xfsz vtalrm prof winch poll pwr sys"
        ),
        "riflags": "recv_peek recv_waitall",
        "roflags": "recv_data_truncated",
        "sdflags": "rd wr",
        "preopentype": "dir",
    }.items()
}


class Names:
    """The names of one enumeration or set of flags as numbers: `Errno.noent == 44`, `Rights.fd_read == 2`."""

    def __init__(self, names: tuple[str, ...], flags: bool) -> None:
        self.names = names
        self._values = {name: (1 << index if flags else index) for index, name in enumerate(names)}

    def __getattr__(self, name: str) -> int:
        try:
            return self._values[name]
        except KeyError:
            raise AttributeError(name) from None

    def __getitem__(self, name: str) -> int:
        return self._values[name]


Errno = Names(TABLES["errno"], False)
Rights = Names(TABLES["rights"], True)
Clockid = Names(TABLES["clockid"], False)
Whence = Names(TABLES["whence"], False)
Filetype = Names(TABLES["filetype"], False)
Advice = Names(TABLES["advice"], False)
Fdflags = Names(TABLES["fdflags"], True)
Fstflags = Names(TABLES["fstflags"], True)
Lookupflags = Names(TABLES["lookupflags"], True)
Oflags = Names(TABLES["oflags"], True)
Eventtype = Names(TABLES["eventtype"], False)
Subclockflags = Names(TABLES["subclockflags"], True)
Preopentype = Names(TABLES["preopentype"], False)

ALL_RIGHTS = (1 << len(TABLES["rights"])) - 1
MASK32, MASK64 = 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF

# The records of typenames.witx, laid out as the specification lays them out (little endian, natural alignment).
STRUCTS: dict[str, struct.Struct] = {
    "iovec": struct.Struct("<II"),
    "ciovec": struct.Struct("<II"),
    "dirent": struct.Struct("<QQIB3x"),  # d_next, d_ino, d_namlen, d_type
    "fdstat": struct.Struct("<BxH4xQQ"),  # fs_filetype, fs_flags, fs_rights_base, fs_rights_inheriting
    "filestat": struct.Struct("<QQB7xQQQQQ"),  # dev, ino, filetype, nlink, size, atim, mtim, ctim
    "event": struct.Struct("<QHB5xQH6x"),  # userdata, error, type, fd_readwrite.nbytes, fd_readwrite.flags
    "subscription": struct.Struct("<QB7x32x"),  # userdata, the tag, then the union (see _SUB_CLOCK, _SUB_FD)
    "prestat": struct.Struct("<B3xI"),  # the tag, pr_name_len
}
_SUB_CLOCK = struct.Struct("<I4xQQH6x")  # id, timeout, precision, flags
_SUB_FD = struct.Struct("<I")

# `wasi_unstable`, the first snapshot (`preview0/witx` of the same branch of the specification): the same functions
# but `sock_accept`, and four differences, all kept here: `whence` is in the order cur, end, set; there is one right
# less (the last, sock_accept); `filestat` has a 32-bit link count, which moves its fields; and a clock subscription
# has an extra `identifier` in front, which makes a subscription 56 bytes. The tests compare these with the preview0
# witx files.
UNSTABLE = "wasi_unstable"
UNSTABLE_TABLES: dict[str, tuple[str, ...]] = {"whence": ("cur", "end", "set"), "rights": TABLES["rights"][:-1]}
UNSTABLE_ALL_RIGHTS = (1 << len(UNSTABLE_TABLES["rights"])) - 1
UNSTABLE_STRUCTS: dict[str, struct.Struct] = {
    "filestat": struct.Struct("<QQB3xIQQQQ"),  # dev, ino, filetype, nlink (32 bits), size, atim, mtim, ctim
    "subscription": struct.Struct("<QB7x40x"),  # userdata, the tag, then the union (see _SUB_CLOCK_UNSTABLE, _SUB_FD)
}
_SUB_CLOCK_UNSTABLE = struct.Struct("<QI4xQQH6x")  # identifier, id, timeout, precision, flags
_NOT_IN_UNSTABLE = frozenset({"sock_accept"})
# The calls that read or write something that differs, and so take `legacy=True` when offered as `wasi_unstable`.
_SNAPSHOT_AWARE = frozenset({"fd_seek", "fd_fdstat_get", "fd_filestat_get", "path_filestat_get", "poll_oneoff"})

# The wasm signature of each function: the parameters (the specification's results other than the errno are
# pointers, added at the end) and the results. The tests compare this table with the witx file.
SIGNATURES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
_F = TypeVar("_F", bound=Callable[..., object])


def _call(*params: str, results: tuple[str, ...] = (i32,)) -> Callable[[_F], _F]:
    def register(fn: _F) -> _F:
        SIGNATURES[fn.__name__] = (params, results)
        return fn

    return register


# Python's errno numbers -> WASI's (the position in the table).
_HOST_ERRNO: dict[int, int] = {}
for _value, _name in enumerate(TABLES["errno"]):
    _host = getattr(_errno, "E" + _name.upper(), None)
    if _host is not None and _value:
        _HOST_ERRNO.setdefault(_host, _value)

_STDIN_RIGHTS = Rights.fd_read | Rights.fd_filestat_get | Rights.poll_fd_readwrite | Rights.fd_fdstat_set_flags
_STDOUT_RIGHTS = (
    Rights.fd_write | Rights.fd_filestat_get | Rights.poll_fd_readwrite | Rights.fd_fdstat_set_flags | Rights.fd_sync
)
_WRITE_RIGHTS = Rights.fd_write | Rights.fd_datasync | Rights.fd_allocate | Rights.fd_filestat_set_size
# Every right that changes something in a folder or in a file: a read-only preopen is one without them, and so is
# whatever is opened from it (a directory hands down no more than it has).
_MUTATING_RIGHTS = (
    _WRITE_RIGHTS
    | Rights.fd_filestat_set_times
    | Rights.path_create_directory
    | Rights.path_create_file
    | Rights.path_link_source
    | Rights.path_link_target
    | Rights.path_rename_source
    | Rights.path_rename_target
    | Rights.path_filestat_set_size
    | Rights.path_filestat_set_times
    | Rights.path_symlink
    | Rights.path_remove_directory
    | Rights.path_unlink_file
)
_O_BINARY = getattr(os, "O_BINARY", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class WasiExit(Exception):  # noqa: N818 -- WASI's proc_exit
    """Raised inside the program by `proc_exit`; `start` turns it into the exit code."""

    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


class _Fail(Exception):
    """An error code to hand back to the program."""

    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


class _Memory(Protocol):
    def read(self, offset: int, length: int) -> bytes: ...
    def write(self, offset: int, data: bytes) -> object: ...
    def __len__(self) -> int: ...


class _Entry:
    """One open file descriptor of the program."""

    def __init__(self, kind: str, rights: int, inheriting: int = 0, *, host_path: str = "", osfd: int = -1) -> None:
        self.kind = kind  # stdin, stdout, stderr, dir, file
        self.rights = rights
        self.inheriting = inheriting
        self.host_path = host_path  # a directory's real path (the world of its names), or a file's
        self.osfd = osfd
        self.flags = 0  # fdflags
        self.filetype = Filetype.directory if kind == "dir" else Filetype.character_device
        self.preopen: bytes | None = None  # the name the program was given, for a preopened directory
        self.listing: list[tuple[bytes, int, int]] | None = None  # fd_readdir: (name, inode, filetype)


def _s64(value: int) -> int:
    return value - (1 << 64) if value >= 1 << 63 else value


def _filetype_of(mode: int) -> int:
    if stat.S_ISDIR(mode):
        return Filetype.directory
    if stat.S_ISREG(mode):
        return Filetype.regular_file
    if stat.S_ISLNK(mode):
        return Filetype.symbolic_link
    if stat.S_ISCHR(mode):
        return Filetype.character_device
    if stat.S_ISBLK(mode):
        return Filetype.block_device
    if stat.S_ISSOCK(mode):
        return Filetype.socket_stream
    return Filetype.unknown


def _inside(path: str, base: str) -> bool:
    path, base = os.path.normcase(path), os.path.normcase(base)
    return path == base or path.startswith(base if base.endswith(os.sep) else base + os.sep)


class _Source:
    """Standard input: bytes, or an object with `read(n)`; nothing at all if None (there is no interactive stdin)."""

    def __init__(self, source: bytes | bytearray | Any | None) -> None:
        self.data = bytes(source) if isinstance(source, (bytes, bytearray)) else b""
        self.pos = 0
        self.reader = None if isinstance(source, (bytes, bytearray)) or source is None else source

    def read(self, count: int) -> bytes:
        if self.reader is not None:
            return bytes(self.reader.read(count) or b"")
        chunk = self.data[self.pos : self.pos + count]
        self.pos += len(chunk)
        return chunk

    def remaining(self) -> int:
        return len(self.data) - self.pos if self.reader is None else 0


def _sink(target: Callable[[bytes], object] | Any | None, fallback: Any) -> Callable[[bytes], object]:
    """Standard output or error: a callable that takes bytes, or an object with `write(bytes)`; None is the
    interpreter's own stream (text, decoded as UTF-8)."""
    if target is None:
        return lambda data: fallback().write(data.decode("utf-8", "replace"))
    if callable(target):
        return target
    return target.write


class Preview1:
    """The `wasi_snapshot_preview1` calls of one program run.

    `args` are the program's arguments, the first one its name; `env` the environment; `preopens` maps the name the
    program sees to a directory of the host (`{"/": "some/dir", "data": "/mnt/data"}`), each one preopened as the
    next file descriptor from 3; `readonly` is True for all of them, or the names of the ones that nothing can be
    written to or changed in (the rights that change something are taken away: a call that does answers
    `ENOTCAPABLE`). `stdin` is bytes or an object with `read(n)` (default: empty); `stdout` and `stderr` are callables
    that take bytes, or objects with `write(bytes)` (default: the interpreter's text streams).
    """

    def __init__(
        self,
        args: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
        preopens: Mapping[str, str | os.PathLike[str]] | None = None,
        stdin: bytes | bytearray | Any | None = None,
        stdout: Callable[[bytes], object] | Any | None = None,
        stderr: Callable[[bytes], object] | Any | None = None,
        *,
        readonly: bool | Collection[str] = False,
    ) -> None:
        self.args = [str(a) for a in args]
        self.env = [f"{k}={v}" for k, v in (env or {}).items()]
        self._stdin = _Source(stdin)
        self._stdout = _sink(stdout, lambda: sys.stdout)
        self._stderr = _sink(stderr, lambda: sys.stderr)
        self._fds: dict[int, _Entry] = {
            0: _Entry("stdin", _STDIN_RIGHTS),
            1: _Entry("stdout", _STDOUT_RIGHTS),
            2: _Entry("stderr", _STDOUT_RIGHTS),
        }
        names: set[str] = set()
        if isinstance(readonly, str):
            names = {readonly}
        elif not isinstance(readonly, bool):
            names = {str(name) for name in readonly}
        if unknown := names - set(preopens or {}):
            raise ValueError(f"readonly: no preopen of that name: {sorted(unknown)}")
        for name, host in (preopens or {}).items():
            real = os.path.realpath(host)
            if not os.path.isdir(real):
                raise NotADirectoryError(f"preopen {name!r}: {os.fspath(host)!r} is not a directory")
            allowed = ALL_RIGHTS & ~_MUTATING_RIGHTS if readonly is True or name in names else ALL_RIGHTS
            entry = _Entry("dir", allowed, allowed, host_path=real)
            entry.preopen = str(name).encode("utf-8")
            self._fds[self._free_fd()] = entry
        self.memory: _Memory | None = None

    # --- running

    def imports(self) -> dict[str, dict[str, Callable[..., int | None]]]:
        """The import object: every function of both snapshots, `wasi_snapshot_preview1` and `wasi_unstable`, to give to
        `Instance(module, wasi.imports())`; the engine links the ones the module imports."""
        return {
            SNAPSHOT: {name: self._bind_call(name, params, False) for name, (params, _) in SIGNATURES.items()},
            UNSTABLE: {name: self._bind_call(name, params, True) for name, (params, _) in UNSTABLE_SIGNATURES.items()},
        }

    def bind(self, instance: Instance) -> None:
        """Give the host the memory of the instance (it is needed by every call that reads or writes the program's
        memory, so before `_start`)."""
        self.memory = instance.exports.memory

    def instantiate(self, module: Module | bytes, **options: Any) -> Instance:
        """`Instance(module, wasi.imports(), **options)`, bound to this host."""
        if not isinstance(module, Module):
            module = Module(module)
        instance = Instance(module, self.imports(), **options)
        self.bind(instance)
        return instance

    def start(self, instance: Instance) -> int:
        """Run the program: its `_start` (a command) or `_initialize` (a reactor, which then returns 0). The exit
        code: what `proc_exit` was given, else 0. Anything else the program raises (a `Trap`) goes on out."""
        self.bind(instance)
        exports = instance.exports
        entry = getattr(exports, "_start", None) or getattr(exports, "_initialize", None)
        if entry is None:
            raise TypeError("the module exports neither _start nor _initialize")
        try:
            entry()
        except WasiExit as exit_:
            return exit_.code
        return 0

    def run(self, module: Module | bytes, **options: Any) -> int:
        """Instantiate, start, and close the files: the exit code of the program."""
        try:
            return self.start(self.instantiate(module, **options))
        finally:
            self.close()

    def close(self) -> None:
        """Close the files the program opened (and forget all its descriptors)."""
        for entry in self._fds.values():
            if entry.osfd >= 0:
                try:
                    os.close(entry.osfd)
                except OSError:
                    pass
        self._fds.clear()

    def __enter__(self) -> Preview1:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _bind_call(self, name: str, params: tuple[str, ...], legacy: bool) -> Callable[..., int | None]:
        method = getattr(self, name)
        if legacy and name in _SNAPSHOT_AWARE:
            method = functools.partial(method, legacy=True)
        masks = tuple(MASK32 if p == i32 else MASK64 for p in params)

        def call(*args: int) -> int | None:
            try:
                method(*[a & m for a, m in zip(args, masks, strict=False)])
            except _Fail as fail:
                return fail.code
            except BrokenPipeError:  # a sink may raise it bare, without an errno
                return Errno.pipe
            except OSError as exc:
                return _HOST_ERRNO.get(exc.errno or 0, Errno.io)
            except IndexError:  # the memory's own bounds check
                return Errno.fault
            except NotImplementedError:
                return Errno.nosys
            return Errno.success

        if name == "proc_exit":  # does not come back: the exception goes through
            return lambda *args: method(*[a & m for a, m in zip(args, masks, strict=False)])
        return call

    # --- the program's memory

    def _mem(self) -> _Memory:
        if self.memory is None:
            raise RuntimeError("the program has no memory yet: bind(instance) first")
        return self.memory

    def _read(self, ptr: int, size: int) -> bytes:
        mem = self._mem()
        if ptr + size > len(mem):
            raise _Fail(Errno.fault)
        return bytes(mem.read(ptr, size)) if size else b""

    def _write(self, ptr: int, data: bytes) -> None:
        mem = self._mem()
        if ptr + len(data) > len(mem):
            raise _Fail(Errno.fault)
        if data:
            mem.write(ptr, data)

    def _put(self, ptr: int, fmt: str, *values: int) -> None:
        self._write(ptr, struct.pack(fmt, *values))

    def _iovs(self, ptr: int, count: int) -> list[tuple[int, int]]:
        return list(STRUCTS["iovec"].iter_unpack(self._read(ptr, 8 * count)))

    def _string(self, ptr: int, size: int) -> str:
        try:
            text = self._read(ptr, size).decode("utf-8")
        except UnicodeDecodeError:
            raise _Fail(Errno.ilseq) from None
        if "\0" in text:
            raise _Fail(Errno.inval)
        return text

    # --- descriptors

    def _free_fd(self) -> int:
        fd = 0
        while fd in self._fds:
            fd += 1
        return fd

    def _entry(self, fd: int, right: int = 0) -> _Entry:
        entry = self._fds.get(fd)
        if entry is None:
            raise _Fail(Errno.badf)
        if right and entry.rights & right != right:  # all the rights asked for, not any of them
            raise _Fail(Errno.notcapable)
        return entry

    def _file(self, fd: int, right: int = 0) -> _Entry:
        entry = self._entry(fd)
        if entry.kind == "dir":
            raise _Fail(Errno.isdir)
        return self._entry(fd, right)

    def _dir(self, fd: int, right: int = 0) -> _Entry:
        entry = self._entry(fd)
        if entry.kind != "dir":  # what it is comes before what it may do
            raise _Fail(Errno.notdir)
        return self._entry(fd, right)

    def _resolve(self, fd: int, right: int, ptr: int, size: int, follow: bool) -> tuple[_Entry, str]:
        """The host path of a name under the directory `fd`; the name may not leave that directory. With `follow`
        False the last component is not looked through (it may be a link: unlink, rename, symlink, lstat)."""
        base = self._dir(fd, right)
        name = self._string(ptr, size)
        if name.startswith("/") or os.path.isabs(name):
            raise _Fail(Errno.notcapable)
        if not name:
            raise _Fail(Errno.noent)
        path = os.path.normpath(os.path.join(base.host_path, name))
        real = (
            os.path.realpath(path)
            if follow
            else os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))
        )
        if not _inside(path, base.host_path) or not _inside(real, base.host_path):
            raise _Fail(Errno.notcapable)
        return base, path

    # --- arguments, environment

    @_call(i32, i32)
    def args_get(self, argv: int, argv_buf: int) -> None:
        self._strings(self.args, argv, argv_buf)

    @_call(i32, i32)
    def args_sizes_get(self, argc: int, size: int) -> None:
        self._sizes(self.args, argc, size)

    @_call(i32, i32)
    def environ_get(self, environ: int, environ_buf: int) -> None:
        self._strings(self.env, environ, environ_buf)

    @_call(i32, i32)
    def environ_sizes_get(self, count: int, size: int) -> None:
        self._sizes(self.env, count, size)

    def _strings(self, items: Iterable[str], table: int, buf: int) -> None:
        pointers: list[int] = []
        data = b""
        for item in items:
            pointers.append(buf + len(data))
            data += item.encode("utf-8") + b"\0"
        self._write(table, struct.pack(f"<{len(pointers)}I", *pointers))
        self._write(buf, data)

    def _sizes(self, items: Sequence[str], count: int, size: int) -> None:
        total = sum(len(item.encode("utf-8")) + 1 for item in items)
        self._put(count, "<I", len(items))
        self._put(size, "<I", total)

    # --- clocks, random, scheduling

    _CLOCKS: dict[int, tuple[Literal["time", "monotonic", "process_time", "thread_time"], Callable[[], int]]] = {
        # clockid -> (the time module's name for it, the reading in nanoseconds)
        Clockid.realtime: ("time", time.time_ns),
        Clockid.monotonic: ("monotonic", time.monotonic_ns),
        Clockid.process_cputime_id: ("process_time", time.process_time_ns),
        Clockid.thread_cputime_id: ("thread_time", time.thread_time_ns),
    }

    @_call(i32, i32)
    def clock_res_get(self, clock_id: int, ptr: int) -> None:
        if clock_id not in self._CLOCKS:
            raise _Fail(Errno.inval)
        resolution = time.get_clock_info(self._CLOCKS[clock_id][0]).resolution
        self._put(ptr, "<Q", max(1, round(resolution * 1e9)))

    @_call(i32, i64, i32)
    def clock_time_get(self, clock_id: int, precision: int, ptr: int) -> None:
        if clock_id not in self._CLOCKS:
            raise _Fail(Errno.inval)
        self._put(ptr, "<Q", self._CLOCKS[clock_id][1]() & MASK64)

    @_call(i32, i32)
    def random_get(self, buf: int, size: int) -> None:
        if buf + size > len(self._mem()):  # before asking the system for that many bytes
            raise _Fail(Errno.fault)
        self._write(buf, os.urandom(size))

    @_call()
    def sched_yield(self) -> None:
        time.sleep(0)

    @_call(i32, results=())
    def proc_exit(self, code: int) -> None:
        raise WasiExit(code)

    @_call(i32)
    def proc_raise(self, signal: int) -> None:
        if signal >= len(TABLES["signal"]):
            raise _Fail(Errno.inval)
        raise _Fail(Errno.nosys)  # there are no signals to raise here

    @_call(i32, i32, i32, i32)
    def poll_oneoff(self, in_ptr: int, out_ptr: int, count: int, nevents: int, legacy: bool = False) -> None:
        if count == 0:
            raise _Fail(Errno.inval)
        stride = (UNSTABLE_STRUCTS if legacy else STRUCTS)["subscription"].size
        raw = self._read(in_ptr, stride * count)
        events: list[bytes] = []
        deadlines: list[tuple[int, int, int]] = []  # (wake-up in ns from now, userdata, error)
        pack = STRUCTS["event"].pack
        for i in range(count):
            userdata, tag = struct.unpack_from("<QB", raw, stride * i)
            if tag == Eventtype.clock:
                if legacy:
                    _, clock_id, timeout, _, flags = _SUB_CLOCK_UNSTABLE.unpack_from(raw, stride * i + 16)
                else:
                    clock_id, timeout, _, flags = _SUB_CLOCK.unpack_from(raw, stride * i + 16)
                if clock_id not in self._CLOCKS:
                    events.append(pack(userdata, Errno.inval, Eventtype.clock, 0, 0))
                    continue
                wait = (
                    timeout - self._CLOCKS[clock_id][1]()
                    if flags & Subclockflags.subscription_clock_abstime
                    else timeout
                )
                deadlines.append((max(0, wait), userdata, 0))
            elif tag in (Eventtype.fd_read, Eventtype.fd_write):
                (fd,) = _SUB_FD.unpack_from(raw, stride * i + 16)
                entry = self._fds.get(fd)
                if entry is None:
                    events.append(pack(userdata, Errno.badf, tag, 0, 0))
                else:  # files, directories and the standard streams are always ready
                    events.append(pack(userdata, Errno.success, tag, self._ready_bytes(entry, tag), 0))
            else:
                raise _Fail(Errno.inval)
        if not events and deadlines:  # nothing is ready: sleep until the first clock
            first = min(d[0] for d in deadlines)
            time.sleep(first / 1e9)
            deadlines = [d for d in deadlines if d[0] <= first]
            events = [pack(userdata, error, Eventtype.clock, 0, 0) for _, userdata, error in deadlines]
        else:  # something is ready: clocks that have already run out go with it
            events += [pack(u, e, Eventtype.clock, 0, 0) for wait, u, e in deadlines if wait == 0]
        self._write(out_ptr, b"".join(events))
        self._put(nevents, "<I", len(events))

    def _ready_bytes(self, entry: _Entry, tag: int) -> int:
        if tag == Eventtype.fd_read and entry.kind == "stdin":
            return self._stdin.remaining()
        if tag == Eventtype.fd_read and entry.kind == "file":
            try:
                return max(0, os.fstat(entry.osfd).st_size - os.lseek(entry.osfd, 0, os.SEEK_CUR))
            except OSError:
                return 0
        return 0

    # --- files: reading, writing, position

    @_call(i32, i32, i32, i32)
    def fd_read(self, fd: int, iovs: int, count: int, nread: int) -> None:
        entry = self._file(fd, Rights.fd_read)
        self._put(nread, "<I", self._gather(entry, self._iovs(iovs, count), None))

    @_call(i32, i32, i32, i64, i32)
    def fd_pread(self, fd: int, iovs: int, count: int, offset: int, nread: int) -> None:
        entry = self._file(fd)
        if entry.kind != "file":
            raise _Fail(Errno.spipe)  # what it is comes before what it may do
        self._entry(fd, Rights.fd_read | Rights.fd_seek)
        self._put(nread, "<I", self._gather(entry, self._iovs(iovs, count), offset))

    def _gather(self, entry: _Entry, iovs: list[tuple[int, int]], offset: int | None) -> int:
        """Read into the buffers one after another, until one comes back short; the total."""
        total = 0
        for ptr, size in iovs:
            if ptr + size > len(self._mem()):
                raise _Fail(Errno.fault)
            if not size:
                continue
            if entry.kind == "stdin":
                data = self._stdin.read(size)
            elif entry.kind == "file":
                data = (
                    self._pread(entry.osfd, size, total + offset) if offset is not None else os.read(entry.osfd, size)
                )
            else:
                raise _Fail(Errno.badf)
            self._write(ptr, data)
            total += len(data)
            if len(data) < size:
                break
        return total

    @staticmethod
    def _pread(osfd: int, size: int, offset: int) -> bytes:
        if hasattr(os, "pread"):
            return os.pread(osfd, size, offset)
        here = os.lseek(osfd, 0, os.SEEK_CUR)  # Windows: no pread, so seek there and back
        try:
            os.lseek(osfd, offset, os.SEEK_SET)
            return os.read(osfd, size)
        finally:
            os.lseek(osfd, here, os.SEEK_SET)

    @staticmethod
    def _pwrite(osfd: int, data: bytes, offset: int) -> int:
        if hasattr(os, "pwrite"):
            return os.pwrite(osfd, data, offset)
        here = os.lseek(osfd, 0, os.SEEK_CUR)
        try:
            os.lseek(osfd, offset, os.SEEK_SET)
            return os.write(osfd, data)
        finally:
            os.lseek(osfd, here, os.SEEK_SET)

    @_call(i32, i32, i32, i32)
    def fd_write(self, fd: int, iovs: int, count: int, nwritten: int) -> None:
        entry = self._file(fd, Rights.fd_write)
        self._put(nwritten, "<I", self._scatter(entry, self._iovs(iovs, count), None))

    @_call(i32, i32, i32, i64, i32)
    def fd_pwrite(self, fd: int, iovs: int, count: int, offset: int, nwritten: int) -> None:
        entry = self._file(fd)
        if entry.kind != "file":
            raise _Fail(Errno.spipe)
        self._entry(fd, Rights.fd_write | Rights.fd_seek)
        self._put(nwritten, "<I", self._scatter(entry, self._iovs(iovs, count), offset))

    def _scatter(self, entry: _Entry, iovs: list[tuple[int, int]], offset: int | None) -> int:
        data = b"".join(self._read(ptr, size) for ptr, size in iovs)
        if entry.kind == "stdout":
            self._stdout(data)
        elif entry.kind == "stderr":
            self._stderr(data)
        elif entry.kind == "file":
            if offset is not None:
                return self._pwrite(entry.osfd, data, offset)
            if entry.flags & Fdflags.append:
                os.lseek(entry.osfd, 0, os.SEEK_END)
            return os.write(entry.osfd, data)
        else:
            raise _Fail(Errno.badf)
        return len(data)

    @_call(i32, i64, i32, i32)
    def fd_seek(self, fd: int, offset: int, whence: int, ptr: int, legacy: bool = False) -> None:
        entry = self._file(fd)
        names = UNSTABLE_TABLES["whence"] if legacy else TABLES["whence"]
        if whence >= len(names):
            raise _Fail(Errno.inval)
        if entry.kind != "file":
            raise _Fail(Errno.spipe)
        offset = _s64(offset)
        if not entry.rights & Rights.fd_seek and not (
            offset == 0 and names[whence] == "cur" and entry.rights & Rights.fd_tell
        ):
            raise _Fail(Errno.notcapable)
        how = {"set": os.SEEK_SET, "cur": os.SEEK_CUR, "end": os.SEEK_END}[names[whence]]
        self._put(ptr, "<Q", os.lseek(entry.osfd, offset, how))

    @_call(i32, i32)
    def fd_tell(self, fd: int, ptr: int) -> None:
        entry = self._file(fd, Rights.fd_tell)
        if entry.kind != "file":
            raise _Fail(Errno.spipe)
        self._put(ptr, "<Q", os.lseek(entry.osfd, 0, os.SEEK_CUR))

    @_call(i32, i64, i64, i32)
    def fd_advise(self, fd: int, offset: int, length: int, advice: int) -> None:
        entry = self._entry(fd, Rights.fd_advise)
        if advice >= len(TABLES["advice"]):
            raise _Fail(Errno.inval)
        if entry.kind == "dir":
            raise _Fail(Errno.badf)
        if entry.kind != "file":
            raise _Fail(Errno.spipe)
        if hasattr(os, "posix_fadvise"):
            hint = getattr(os, "POSIX_FADV_" + TABLES["advice"][advice].upper(), None)
            if hint is not None:
                os.posix_fadvise(entry.osfd, offset, length, hint)

    @_call(i32, i64, i64)
    def fd_allocate(self, fd: int, offset: int, length: int) -> None:
        entry = self._file(fd, Rights.fd_allocate)
        if entry.kind != "file":
            raise _Fail(Errno.badf)
        if hasattr(os, "posix_fallocate"):
            os.posix_fallocate(entry.osfd, offset, length)
        elif os.fstat(entry.osfd).st_size < offset + length:
            os.ftruncate(entry.osfd, offset + length)

    @_call(i32)
    def fd_datasync(self, fd: int) -> None:
        entry = self._entry(fd, Rights.fd_datasync)
        if entry.kind == "file":
            getattr(os, "fdatasync", os.fsync)(entry.osfd)

    @_call(i32)
    def fd_sync(self, fd: int) -> None:
        entry = self._entry(fd, Rights.fd_sync)
        if entry.kind == "file":
            os.fsync(entry.osfd)

    @_call(i32)
    def fd_close(self, fd: int) -> None:
        entry = self._entry(fd)
        del self._fds[fd]
        if entry.osfd >= 0:
            os.close(entry.osfd)

    @_call(i32, i32)
    def fd_renumber(self, source: int, target: int) -> None:
        entry = self._entry(source)
        old = self._entry(target)
        if source != target:
            if old.osfd >= 0:
                os.close(old.osfd)
            self._fds[target] = entry
            del self._fds[source]

    # --- descriptors' own state

    @_call(i32, i32)
    def fd_fdstat_get(self, fd: int, ptr: int, legacy: bool = False) -> None:
        entry = self._entry(fd)
        keep = UNSTABLE_ALL_RIGHTS if legacy else ALL_RIGHTS  # the first snapshot has no right for sock_accept
        self._put(
            ptr, STRUCTS["fdstat"].format, entry.filetype, entry.flags, entry.rights & keep, entry.inheriting & keep
        )

    @_call(i32, i32)
    def fd_fdstat_set_flags(self, fd: int, flags: int) -> None:
        entry = self._entry(fd, Rights.fd_fdstat_set_flags)
        if flags >> len(TABLES["fdflags"]):
            raise _Fail(Errno.inval)
        entry.flags = flags  # append is honoured by fd_write; the others are only recorded

    @_call(i32, i64, i64)
    def fd_fdstat_set_rights(self, fd: int, base: int, inheriting: int) -> None:
        entry = self._entry(fd)
        if base & ~entry.rights or inheriting & ~entry.inheriting:  # rights can be given up, never gained
            raise _Fail(Errno.notcapable)
        entry.rights, entry.inheriting = base, inheriting

    @_call(i32, i32)
    def fd_filestat_get(self, fd: int, ptr: int, legacy: bool = False) -> None:
        entry = self._entry(fd, Rights.fd_filestat_get)
        if entry.kind == "file":
            self._put_stat(ptr, os.fstat(entry.osfd), legacy)
        elif entry.kind == "dir":
            self._put_stat(ptr, os.stat(entry.host_path), legacy)
        else:
            self._put_filestat(ptr, legacy, 0, 0, Filetype.character_device, 1, 0, 0, 0, 0)

    def _put_stat(self, ptr: int, st: os.stat_result, legacy: bool) -> None:
        self._put_filestat(
            ptr,
            legacy,
            st.st_dev & MASK64,
            st.st_ino & MASK64,
            _filetype_of(st.st_mode),
            st.st_nlink,
            st.st_size,
            st.st_atime_ns,
            st.st_mtime_ns,
            st.st_ctime_ns,
        )

    def _put_filestat(self, ptr: int, legacy: bool, *fields: int) -> None:
        # the first snapshot counts links in 32 bits, and its record is laid out differently
        self._put(ptr, (UNSTABLE_STRUCTS if legacy else STRUCTS)["filestat"].format, *fields)

    @_call(i32, i64)
    def fd_filestat_set_size(self, fd: int, size: int) -> None:
        entry = self._file(fd, Rights.fd_filestat_set_size)
        if entry.kind != "file":
            raise _Fail(Errno.inval)
        os.ftruncate(entry.osfd, size)

    @_call(i32, i64, i64, i32)
    def fd_filestat_set_times(self, fd: int, atim: int, mtim: int, flags: int) -> None:
        entry = self._entry(fd, Rights.fd_filestat_set_times)
        if entry.kind in ("stdin", "stdout", "stderr"):
            raise _Fail(Errno.badf)
        target: int | str = entry.osfd if entry.kind == "file" and os.utime in os.supports_fd else entry.host_path
        self._set_times(target, atim, mtim, flags, True)

    @staticmethod
    def _set_times(target: int | str, atim: int, mtim: int, flags: int, follow: bool) -> None:
        both = Fstflags.atim | Fstflags.atim_now, Fstflags.mtim | Fstflags.mtim_now
        if flags >> len(TABLES["fstflags"]) or flags & both[0] == both[0] or flags & both[1] == both[1]:
            raise _Fail(Errno.inval)
        st = os.stat(target, follow_symlinks=follow) if isinstance(target, str) else os.fstat(target)
        now = time.time_ns()
        a = atim if flags & Fstflags.atim else now if flags & Fstflags.atim_now else st.st_atime_ns
        m = mtim if flags & Fstflags.mtim else now if flags & Fstflags.mtim_now else st.st_mtime_ns
        if isinstance(target, str) and os.utime not in os.supports_follow_symlinks:
            os.utime(target, ns=(a, m))
        elif isinstance(target, str):
            os.utime(target, ns=(a, m), follow_symlinks=follow)
        else:
            os.utime(target, ns=(a, m))

    # --- preopened directories, directory listing

    @_call(i32, i32)
    def fd_prestat_get(self, fd: int, ptr: int) -> None:
        entry = self._entry(fd)
        if entry.preopen is None:
            raise _Fail(Errno.badf)
        self._put(ptr, STRUCTS["prestat"].format, Preopentype.dir, len(entry.preopen))

    @_call(i32, i32, i32)
    def fd_prestat_dir_name(self, fd: int, ptr: int, size: int) -> None:
        entry = self._entry(fd)
        if entry.preopen is None:
            raise _Fail(Errno.badf)
        if size < len(entry.preopen):
            raise _Fail(Errno.nametoolong)
        self._write(ptr, entry.preopen)

    @_call(i32, i32, i32, i64, i32)
    def fd_readdir(self, fd: int, buf: int, size: int, cookie: int, used: int) -> None:
        entry = self._dir(fd, Rights.fd_readdir)
        if cookie == 0 or entry.listing is None:
            entry.listing = self._list(entry.host_path)
        out = b""
        for index in range(cookie, len(entry.listing)):
            name, inode, kind = entry.listing[index]
            out += STRUCTS["dirent"].pack(index + 1, inode, len(name), kind) + name
            if len(out) >= size:
                break
        out = out[:size]
        self._write(buf, out)
        self._put(used, "<I", len(out))

    @staticmethod
    def _list(path: str) -> list[tuple[bytes, int, int]]:
        rows: list[tuple[bytes, int, int]] = []
        for name in [".", "..", *sorted(os.listdir(path))]:
            try:
                st = os.lstat(os.path.join(path, name))
            except OSError:  # gone since the listing was made
                continue
            rows.append((name.encode("utf-8"), st.st_ino & MASK64, _filetype_of(st.st_mode)))
        return rows

    # --- paths

    @_call(i32, i32, i32)
    def path_create_directory(self, fd: int, ptr: int, size: int) -> None:
        _, path = self._resolve(fd, Rights.path_create_directory, ptr, size, False)
        os.mkdir(path)

    @_call(i32, i32, i32, i32, i32)
    def path_filestat_get(self, fd: int, flags: int, ptr: int, size: int, out: int, legacy: bool = False) -> None:
        follow = bool(flags & Lookupflags.symlink_follow)
        _, path = self._resolve(fd, Rights.path_filestat_get, ptr, size, follow)
        self._put_stat(out, os.stat(path) if follow else os.lstat(path), legacy)

    @_call(i32, i32, i32, i32, i64, i64, i32)
    def path_filestat_set_times(self, fd: int, flags: int, ptr: int, size: int, atim: int, mtim: int, fst: int) -> None:
        follow = bool(flags & Lookupflags.symlink_follow)
        _, path = self._resolve(fd, Rights.path_filestat_set_times, ptr, size, follow)
        self._set_times(path, atim, mtim, fst, follow)

    @_call(i32, i32, i32, i32, i32, i32, i32)
    def path_link(
        self, old_fd: int, flags: int, old_ptr: int, old_size: int, new_fd: int, new_ptr: int, new_size: int
    ) -> None:
        follow = bool(flags & Lookupflags.symlink_follow)
        _, old = self._resolve(old_fd, Rights.path_link_source, old_ptr, old_size, follow)
        _, new = self._resolve(new_fd, Rights.path_link_target, new_ptr, new_size, False)
        os.link(old, new)

    @_call(i32, i32, i32, i32, i32, i64, i64, i32, i32)
    def path_open(
        self,
        fd: int,
        dirflags: int,
        ptr: int,
        size: int,
        oflags: int,
        base: int,
        inheriting: int,
        fdflags: int,
        out: int,
    ) -> None:
        if oflags >> len(TABLES["oflags"]) or fdflags >> len(TABLES["fdflags"]):
            raise _Fail(Errno.inval)
        follow = bool(dirflags & Lookupflags.symlink_follow)
        parent, path = self._resolve(fd, Rights.path_open, ptr, size, follow)
        if oflags & Oflags.creat and not parent.rights & Rights.path_create_file:
            raise _Fail(Errno.notcapable)
        if oflags & Oflags.trunc and not parent.rights & Rights.path_filestat_set_size:
            raise _Fail(Errno.notcapable)
        base &= parent.inheriting & ALL_RIGHTS
        inheriting &= parent.inheriting & ALL_RIGHTS
        try:
            st: os.stat_result | None = os.stat(path) if follow else os.lstat(path)
        except FileNotFoundError:
            st = None
        if st is not None and stat.S_ISDIR(st.st_mode):
            if oflags & Oflags.creat and oflags & Oflags.excl:
                raise _Fail(Errno.exist)
            if oflags & Oflags.trunc:
                raise _Fail(Errno.isdir)
            entry = _Entry("dir", base, inheriting, host_path=os.path.realpath(path))
        else:
            if oflags & Oflags.directory:
                raise _Fail(Errno.noent if st is None else Errno.notdir)
            writable = bool(base & _WRITE_RIGHTS) or bool(oflags & Oflags.trunc)
            readable = bool(base & (Rights.fd_read | Rights.fd_readdir)) or not writable
            mode = os.O_RDWR if readable and writable else os.O_WRONLY if writable else os.O_RDONLY
            if oflags & Oflags.creat:
                mode |= os.O_CREAT
            if oflags & Oflags.excl:
                mode |= os.O_EXCL
            if oflags & Oflags.trunc:
                mode |= os.O_TRUNC
            if not follow:
                mode |= _O_NOFOLLOW
            osfd = os.open(path, mode | _O_BINARY, 0o666)
            kind = _filetype_of(os.fstat(osfd).st_mode)
            if not writable:
                base &= ~_WRITE_RIGHTS
            entry = _Entry("file", base, inheriting, host_path=os.path.realpath(path), osfd=osfd)
            entry.filetype = kind
        entry.flags = fdflags
        if entry.kind == "dir":
            entry.filetype = Filetype.directory
        new = self._free_fd()
        self._fds[new] = entry
        self._put(out, "<I", new)

    @_call(i32, i32, i32, i32, i32, i32)
    def path_readlink(self, fd: int, ptr: int, size: int, buf: int, buf_len: int, used: int) -> None:
        _, path = self._resolve(fd, Rights.path_readlink, ptr, size, False)
        target = os.fsencode(os.readlink(path))[:buf_len]
        self._write(buf, target)
        self._put(used, "<I", len(target))

    @_call(i32, i32, i32)
    def path_remove_directory(self, fd: int, ptr: int, size: int) -> None:
        _, path = self._resolve(fd, Rights.path_remove_directory, ptr, size, False)
        os.rmdir(path)

    @_call(i32, i32, i32, i32, i32, i32)
    def path_rename(self, old_fd: int, old_ptr: int, old_size: int, new_fd: int, new_ptr: int, new_size: int) -> None:
        _, old = self._resolve(old_fd, Rights.path_rename_source, old_ptr, old_size, False)
        _, new = self._resolve(new_fd, Rights.path_rename_target, new_ptr, new_size, False)
        os.replace(old, new)

    @_call(i32, i32, i32, i32, i32)
    def path_symlink(self, old_ptr: int, old_size: int, fd: int, new_ptr: int, new_size: int) -> None:
        target = self._string(old_ptr, old_size)  # stored as given; a link that leads out is refused when followed
        _, path = self._resolve(fd, Rights.path_symlink, new_ptr, new_size, False)
        os.symlink(target, path)

    @_call(i32, i32, i32)
    def path_unlink_file(self, fd: int, ptr: int, size: int) -> None:
        _, path = self._resolve(fd, Rights.path_unlink_file, ptr, size, False)
        if stat.S_ISDIR(os.lstat(path).st_mode):
            raise _Fail(Errno.isdir)
        os.unlink(path)

    # --- sockets: none (a descriptor is never a socket)

    @_call(i32, i32, i32)
    def sock_accept(self, fd: int, flags: int, out: int) -> None:
        self._socket(fd)

    @_call(i32, i32, i32, i32, i32, i32)
    def sock_recv(self, fd: int, iovs: int, count: int, flags: int, nread: int, roflags: int) -> None:
        self._socket(fd)

    @_call(i32, i32, i32, i32, i32)
    def sock_send(self, fd: int, iovs: int, count: int, flags: int, nwritten: int) -> None:
        self._socket(fd)

    @_call(i32, i32)
    def sock_shutdown(self, fd: int, how: int) -> None:
        self._socket(fd)

    def _socket(self, fd: int) -> None:
        self._entry(fd)
        raise _Fail(Errno.notsock)


# The functions of `wasi_unstable`: those of the snapshot but sock_accept (complete only here, once all are registered).
UNSTABLE_SIGNATURES = {name: sig for name, sig in SIGNATURES.items() if name not in _NOT_IN_UNSTABLE}
