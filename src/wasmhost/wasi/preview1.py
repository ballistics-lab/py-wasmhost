"""`wasmhost.wasi.preview1`: a WASI host for `wasi_snapshot_preview1` (WASI 0.1), in plain Python.

The 46 functions of the specification (the `legacy/preview1` witx files of the WebAssembly/WASI repository, branch
`wasi-0.1`), over directories of the real file system, with standard streams, arguments, an environment, clocks and
random bytes. The calls themselves are those of the package `fusedwasi` (`fusedwasi.p1`, the Python twin of the C core
that `wasmext` builds into its modules); this module is the shape `wasmhost` gives them: the options of the host
(`args`, `env`, `preopens`, `stdin`, `stdout`, `stderr`, `readonly`), the import objects of the engines, and the tables
of the specification. Sockets are not supported (`ENOTSOCK`). The first snapshot, `wasi_unstable` (what older toolchains
still produce), is offered too, from the same object: see UNSTABLE below for the four things in which it differs.

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

import os
import struct
import sys
from collections.abc import Callable, Collection, Mapping, Sequence
from typing import Any, Protocol

from fusedwasi.p1 import wasi_p1, wasi_p1_unstable
from fusedwasi.p1.wasi_p1_core import E_PIPE, Exit, State, WasiError, errno_of

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
SIGNATURES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "args_get": ((i32, i32), (i32,)),
    "args_sizes_get": ((i32, i32), (i32,)),
    "environ_get": ((i32, i32), (i32,)),
    "environ_sizes_get": ((i32, i32), (i32,)),
    "clock_res_get": ((i32, i32), (i32,)),
    "clock_time_get": ((i32, i64, i32), (i32,)),
    "random_get": ((i32, i32), (i32,)),
    "sched_yield": ((), (i32,)),
    "proc_exit": ((i32,), ()),
    "proc_raise": ((i32,), (i32,)),
    "poll_oneoff": ((i32, i32, i32, i32), (i32,)),
    "fd_read": ((i32, i32, i32, i32), (i32,)),
    "fd_pread": ((i32, i32, i32, i64, i32), (i32,)),
    "fd_write": ((i32, i32, i32, i32), (i32,)),
    "fd_pwrite": ((i32, i32, i32, i64, i32), (i32,)),
    "fd_seek": ((i32, i64, i32, i32), (i32,)),
    "fd_tell": ((i32, i32), (i32,)),
    "fd_advise": ((i32, i64, i64, i32), (i32,)),
    "fd_allocate": ((i32, i64, i64), (i32,)),
    "fd_datasync": ((i32,), (i32,)),
    "fd_sync": ((i32,), (i32,)),
    "fd_close": ((i32,), (i32,)),
    "fd_renumber": ((i32, i32), (i32,)),
    "fd_fdstat_get": ((i32, i32), (i32,)),
    "fd_fdstat_set_flags": ((i32, i32), (i32,)),
    "fd_fdstat_set_rights": ((i32, i64, i64), (i32,)),
    "fd_filestat_get": ((i32, i32), (i32,)),
    "fd_filestat_set_size": ((i32, i64), (i32,)),
    "fd_filestat_set_times": ((i32, i64, i64, i32), (i32,)),
    "fd_prestat_get": ((i32, i32), (i32,)),
    "fd_prestat_dir_name": ((i32, i32, i32), (i32,)),
    "fd_readdir": ((i32, i32, i32, i64, i32), (i32,)),
    "path_create_directory": ((i32, i32, i32), (i32,)),
    "path_filestat_get": ((i32, i32, i32, i32, i32), (i32,)),
    "path_filestat_set_times": ((i32, i32, i32, i32, i64, i64, i32), (i32,)),
    "path_link": ((i32, i32, i32, i32, i32, i32, i32), (i32,)),
    "path_open": ((i32, i32, i32, i32, i32, i64, i64, i32, i32), (i32,)),
    "path_readlink": ((i32, i32, i32, i32, i32, i32), (i32,)),
    "path_remove_directory": ((i32, i32, i32), (i32,)),
    "path_rename": ((i32, i32, i32, i32, i32, i32), (i32,)),
    "path_symlink": ((i32, i32, i32, i32, i32), (i32,)),
    "path_unlink_file": ((i32, i32, i32), (i32,)),
    "sock_accept": ((i32, i32, i32), (i32,)),
    "sock_recv": ((i32, i32, i32, i32, i32, i32), (i32,)),
    "sock_send": ((i32, i32, i32, i32, i32), (i32,)),
    "sock_shutdown": ((i32, i32), (i32,)),
}
# The functions of `wasi_unstable`: those of the snapshot but sock_accept.
UNSTABLE_SIGNATURES = {name: sig for name, sig in SIGNATURES.items() if name not in _NOT_IN_UNSTABLE}


class WasiExit(Exception):  # noqa: N818 -- WASI's proc_exit
    """Raised inside the program by `proc_exit`; `start` turns it into the exit code."""

    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


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


def _sink(target: Callable[[bytes], object] | Any | None, fallback: Any) -> Callable[[bytes], object]:
    """Standard output or error: a callable that takes bytes, or an object with `write(bytes)`; None is the
    interpreter's own stream (text, decoded as UTF-8)."""
    if target is None:
        return lambda data: fallback().write(data.decode("utf-8", "replace"))
    if callable(target):
        return target
    return target.write


def _host_error(caught: OSError) -> WasiError:
    return WasiError(errno_of(caught.errno))


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
        names: set[str] = set()
        if isinstance(readonly, str):
            names = {readonly}
        elif not isinstance(readonly, bool):
            names = {str(name) for name in readonly}
        if unknown := names - set(preopens or {}):
            raise ValueError(f"readonly: no preopen of that name: {sorted(unknown)}")
        directories: list[tuple[str, str, bool]] = []
        for name, host in (preopens or {}).items():
            real = os.path.realpath(host)
            if not os.path.isdir(real):
                raise NotADirectoryError(f"preopen {name!r}: {os.fspath(host)!r} is not a directory")
            directories.append((real, str(name), not (readonly is True or name in names)))
        self.memory: _Memory | None = None
        self._current: Any = None  # the memory as the call in progress sees it: a view, where the engine can give one
        self._state = State(
            self._memory_of_the_call,
            args=self.args,
            env=self.env,
            preopens=directories,
            stdin_read=self._read_stdin,
            stdout_write=self._write_stdout,
        )

    # --- running

    def imports(self) -> dict[str, dict[str, Callable[..., int | None]]]:
        """The import object: every function of both snapshots, `wasi_snapshot_preview1` and `wasi_unstable`, to give to
        `Instance(module, wasi.imports())`; the engine links the ones the module imports."""
        return {
            SNAPSHOT: {name: self._bind_call(wasi_p1, name, params) for name, (params, _) in SIGNATURES.items()},
            UNSTABLE: {
                name: self._bind_call(wasi_p1_unstable, name, params)
                for name, (params, _) in UNSTABLE_SIGNATURES.items()
            },
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
        self._state.close_all()

    def __enter__(self) -> Preview1:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- what the host gives the calls of fusedwasi

    def _memory_of_the_call(self) -> Any:
        if self.memory is None:
            raise RuntimeError("the program has no memory yet: bind(instance) first")
        return self._current if self._current is not None else self.memory

    def _read_stdin(self, capacity: int) -> bytes:
        try:
            return self._stdin.read(capacity)
        except OSError as caught:
            raise _host_error(caught) from None

    def _write_stdout(self, fd: int, data: bytes) -> None:
        try:
            (self._stdout if fd == 1 else self._stderr)(data)
        except BrokenPipeError:  # a sink may raise it bare, without an errno
            raise WasiError(E_PIPE) from None
        except OSError as caught:
            raise _host_error(caught) from None

    def _bind_call(self, table: Any, name: str, params: tuple[str, ...]) -> Callable[..., int | None]:
        function = getattr(table, name)
        if (
            name == "proc_raise"
        ):  # there are no signals to raise here: the program goes on (fusedwasi ends it with the signal)

            def _raise(signal: int) -> int:
                return Errno.inval if (signal & MASK32) >= len(TABLES["signal"]) else Errno.nosys

            return _raise
        masks = tuple(MASK32 if p == i32 else MASK64 for p in params)
        state = self._state

        def call(*args: int) -> int | None:
            masked = [a & m for a, m in zip(args, masks, strict=False)]
            view: memoryview | None = None
            take_view: Callable[[], memoryview] | None = getattr(self.memory, "view", None)
            if take_view is not None:  # an engine that has its memory in this process: no copy
                try:
                    view = take_view()
                except NotImplementedError:
                    view = None
            self._current = view
            try:
                return function(state, *masked)  # type: ignore[no-any-return]
            except Exit as exit_:  # proc_exit does not come back: the exception goes through
                raise WasiExit(exit_.code) from None
            finally:
                self._current = None

        return call


class _Memory(Protocol):
    def read(self, offset: int, length: int) -> bytes: ...
    def write(self, offset: int, data: bytes) -> object: ...
    def __len__(self) -> int: ...
