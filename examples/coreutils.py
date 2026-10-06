"""A small shell over uutils coreutils (Rust, built to WASI), run by wasmhost with a WASI host written in Python.

    python examples/coreutils.py [--root DIR] [--backend NAME]       # a prompt
    python examples/coreutils.py -c "seq 5 | sort -r | head -3"      # one line, then exit

    wasm:/$ echo hello > a.txt
    wasm:/$ mkdir notes
    wasm:/$ cp a.txt notes/b.txt
    wasm:/$ cat *.txt notes/*.txt | wc -l
    wasm:/$ cd notes
    wasm:/notes$ ls -l

The utilities are `examples/wasm/coreutils.wasm`, one multi-call binary (`coreutils sort file`): `cat cp mv rm ls
mkdir touch head tail wc sort uniq tr cut seq echo printf ...`; `help` lists them. They are WASI programs, so what
they see of the world is whatever this file hands them: that is the `Wasi` class, the `wasi_snapshot_preview1` calls
(files, arguments, clock, exit) over a directory of the real file system, which is all they can reach. wasmhost runs
the module; Python answers its calls, the way `examples/wasmclang.py` does for the older `wasi_unstable`.

Besides coreutils there is `lua`, Lua 5.4.6 (`examples/wasm/lua.wasm`, built to the first snapshot of WASI):
`lua file.lua`, `lua -e "print(2^10)"`, `seq 3 | lua -e "for l in io.lines() do print(l * 2) end"`. That build has
no `longjmp`, so any error of a script (a syntax error, `error()`, a failed `pcall`) stops the interpreter with a
trap, with no message.

The directory (`--root`, by default `~/Documents/wasm-root`, which Pythonista shows in its file browser, else
`./wasm-root`) is the whole world: nothing above it can be named, and a symbolic link that leads out of it is refused.
The shell is Python's, a small one: `|`, `<`, `>`, `>>`, `;`, `&&`, `||`, `*` and `?`, and `cd`, `pwd`, `help`, `exit`.

Where it differs from a real shell:

- `cd` makes the directory the one the programs see as `/`, so they cannot name its parent (`cat ../a` fails; `cd ..`
  first, or name the file from where you are: `cp a.txt notes/b.txt`). WASI has no working directory of its own, and
  the C library inside the programs keeps `/` as theirs whatever `PWD` says.
- There is no interactive stdin: a program with nothing piped or redirected reads an empty file. `echo x | cat`,
  `cat < file`.
- A pipeline runs one stage after another, each to the end, and a stage writing more than 16 MB into a pipe gets a
  broken pipe, which is how `yes | head -3` ends.
- A `|`, `<` or `>` inside quotes is taken for the operator, and `*` is expanded wherever it has matches (when the
  command starts, so a file an earlier command of the line made is found).
- No variables, loops, `$(...)` or `&`.

The module is Rust's output (multi-value, reference types, bulk memory), so the engine has to take those: wasmtime and
JavaScriptCore or Node of a recent enough version do; wasm3 does not.

The first start compiles the module in the engine (some seconds, more on a phone); each command then runs its own
instance of it. `coreutils.wasm` is read from `examples/wasm/` next to this file, else from the cache
(`$WASMHOST_CACHE`, else `~/.cache/wasmhost`), downloaded once.
"""

import argparse
import codecs
import errno
import glob
import io
import os
import shlex
import struct
import sys
import tarfile
import time
import urllib.request

import wasmhost

WASM_URL = "https://raw.githubusercontent.com/ballistics-lab/py-wasmhost/examples/zigcc/examples/wasm/coreutils.wasm"
WASI = "wasi_snapshot_preview1"
WASI_OLD = "wasi_unstable"  # the first snapshot of WASI, which older toolchains (wasienv, wasm-clang) still produce
LUA_URL = "https://registry.npmjs.org/@antonz/lua-wasi/-/lua-wasi-5.4.6.tgz"  # Lua 5.4.6 built to WASI, MIT
# The directory is preopened as "/" and as ".": a C library of the first WASI snapshot takes relative paths against ".".
PREOPENS = ("/", ".")
PROGRAMS = {"lua": ("lua.wasm", LUA_URL, "package/dist/lua.wasm")}  # beside coreutils: name -> file, tarball, member
PIPE_LIMIT = 16 * 1024 * 1024

# WASI errno numbers (they are not the platform's).
ESUCCESS, EACCES, EBADF, EBUSY, EEXIST, EFBIG, EINVAL, EIO, EISDIR = 0, 2, 8, 10, 20, 22, 28, 29, 31
ELOOP, EMLINK, ENAMETOOLONG, ENOENT, ENOMEM, ENOSPC, ENOSYS, ENOTDIR = 32, 34, 37, 44, 48, 51, 52, 54
ENOTEMPTY, ENOTSUP, EPERM, EPIPE, ERANGE, EROFS, ESPIPE, EXDEV, ENOTCAPABLE = 55, 58, 63, 64, 68, 69, 70, 75, 76
ERRNO = {
    errno.EACCES: EACCES, errno.EBADF: EBADF, errno.EBUSY: EBUSY, errno.EEXIST: EEXIST, errno.EFBIG: EFBIG,
    errno.EINVAL: EINVAL, errno.EIO: EIO, errno.EISDIR: EISDIR, errno.ELOOP: ELOOP, errno.EMLINK: EMLINK,
    errno.ENAMETOOLONG: ENAMETOOLONG, errno.ENOENT: ENOENT, errno.ENOMEM: ENOMEM, errno.ENOSPC: ENOSPC,
    errno.ENOSYS: ENOSYS, errno.ENOTDIR: ENOTDIR, errno.ENOTEMPTY: ENOTEMPTY, errno.EPERM: EPERM,
    errno.EPIPE: EPIPE, errno.ERANGE: ERANGE, errno.EROFS: EROFS, errno.ESPIPE: ESPIPE, errno.EXDEV: EXDEV,
}  # fmt: skip
FILETYPE = {"unknown": 0, "char": 2, "dir": 3, "file": 4, "symlink": 7}
RIGHT_READ, RIGHT_WRITE, RIGHT_ALLOCATE, RIGHT_READDIR, RIGHT_SET_SIZE = 1 << 1, 1 << 6, 1 << 8, 1 << 14, 1 << 22
ALL_RIGHTS = 0xFFFFFFFFFFFFFFFF


class WasiExit(Exception):  # noqa: N818 -- WASI's proc_exit
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class WasiError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def s64(value):
    """An i64 argument as the signed number it is."""
    return value - (1 << 64) if value >= 1 << 63 else value


def filetype_of(mode):
    import stat  # noqa: PLC0415

    if stat.S_ISDIR(mode):
        return 3
    if stat.S_ISREG(mode):
        return 4
    if stat.S_ISLNK(mode):
        return 7
    if stat.S_ISCHR(mode):
        return 2
    if stat.S_ISBLK(mode):
        return 1
    return 0


class Fd:
    def __init__(self, kind, path="", osfd=-1):
        self.kind = kind  # stdin, stdout, stderr, dir, file
        self.path = path  # on the host, for a directory
        self.osfd = osfd  # the host's file descriptor, for a file


class Wasi:
    """The `wasi_snapshot_preview1` calls of one program run, over the directory ROOT (which it sees as `/`).

    STDIN is bytes; STDOUT and STDERR are callables that take bytes.
    """

    def __init__(self, root, argv, stdin=b"", stdout=None, stderr=None, env=None, pipe_limit=None):
        self.root = os.path.realpath(root)
        self.argv = [str(a) for a in argv]
        self.env = [f"{k}={v}" for k, v in (env or {}).items()]
        self.stdin = stdin
        self.stdin_pos = 0
        self.write_out = stdout or (lambda data: None)
        self.write_err = stderr or (lambda data: None)
        self.pipe_limit = pipe_limit  # bytes the program may write to stdout, then EPIPE
        self.written = 0
        self.fds = {0: Fd("stdin"), 1: Fd("stdout"), 2: Fd("stderr")}
        for fd, _name in enumerate(PREOPENS, 3):  # the same directory under each name
            self.fds[fd] = Fd("dir", self.root)
        self.next_fd = 3 + len(PREOPENS)
        self.old = False  # the module speaks wasi_unstable
        self._mem = None

    @property
    def mem(self):
        if self._mem is None:
            raise RuntimeError("the program is not running")
        return self._mem

    # --- running

    def run(self, module):
        """Run the module's `_start`; its exit code."""
        calls = {}
        for imp in wasmhost.Module.imports(module):
            if imp.module in (WASI, WASI_OLD):
                calls.setdefault(imp.module, {})[imp.name] = self._bind(imp.name)
                self.old = self.old or imp.module == WASI_OLD
        instance = wasmhost.Instance(module, calls)
        self._mem = instance.exports.memory
        try:
            instance.exports._start()
        except WasiExit as exit_:
            return exit_.code
        finally:
            for fd in self.fds.values():
                if fd.osfd >= 0:
                    os.close(fd.osfd)
        return 0

    def _bind(self, name):
        method = getattr(self, name, None)
        if method is None:
            return lambda *args: ENOSYS

        def call(*args):
            try:
                method(*args)
            except WasiError as exc:
                return exc.code
            except OSError as exc:
                return ERRNO.get(exc.errno or 0, EIO)
            except UnicodeDecodeError:
                return EINVAL
            return ESUCCESS

        return call

    # --- memory

    def put32(self, ptr, value):
        self.mem.write(ptr, struct.pack("<I", value & 0xFFFFFFFF))

    def put64(self, ptr, value):
        self.mem.write(ptr, struct.pack("<Q", value & 0xFFFFFFFFFFFFFFFF))

    def iovs(self, ptr, count):
        return list(struct.iter_unpack("<II", self.mem.read(ptr, 8 * count)))

    def string(self, ptr, size):
        return self.mem.read(ptr, size).decode("utf-8")

    # --- paths: nothing leaves ROOT

    def dir_entry(self, fd):
        entry = self.fds.get(fd)
        if entry is None:
            raise WasiError(EBADF)
        if entry.kind != "dir":
            raise WasiError(ENOTDIR)
        return entry

    def resolve(self, fd, ptr, size, follow=True):
        base = self.dir_entry(fd).path
        rel = self.string(ptr, size).lstrip("/")
        path = os.path.normpath(os.path.join(base, rel))
        # With the last name not followed, only the directory it is in has to stay inside.
        real = (
            os.path.realpath(path)
            if follow
            else os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))
        )
        if real != self.root and not real.startswith(self.root + os.sep):
            raise WasiError(ENOTCAPABLE)
        return path

    def file_entry(self, fd):
        entry = self.fds.get(fd)
        if entry is None:
            raise WasiError(EBADF)
        return entry

    def add_fd(self, entry):
        fd = self.next_fd
        self.next_fd += 1
        self.fds[fd] = entry
        return fd

    # --- arguments, environment, clocks, randomness

    def args_sizes_get(self, argc_ptr, size_ptr):
        self.put32(argc_ptr, len(self.argv))
        self.put32(size_ptr, sum(len(a.encode()) + 1 for a in self.argv))

    def args_get(self, argv_ptr, buf_ptr):
        for arg in self.argv:
            raw = arg.encode() + b"\0"
            self.put32(argv_ptr, buf_ptr)
            self.mem.write(buf_ptr, raw)
            argv_ptr += 4
            buf_ptr += len(raw)

    def environ_sizes_get(self, count_ptr, size_ptr):
        self.put32(count_ptr, len(self.env))
        self.put32(size_ptr, sum(len(e.encode()) + 1 for e in self.env))

    def environ_get(self, environ_ptr, buf_ptr):
        for item in self.env:
            raw = item.encode() + b"\0"
            self.put32(environ_ptr, buf_ptr)
            self.mem.write(buf_ptr, raw)
            environ_ptr += 4
            buf_ptr += len(raw)

    def clock_res_get(self, clock_id, out):
        self.put64(out, 1000)

    def clock_time_get(self, clock_id, precision, out):
        now = (time.time_ns, time.monotonic_ns, time.process_time_ns, time.thread_time_ns)
        if clock_id > 3:
            raise WasiError(EINVAL)
        self.put64(out, now[clock_id]())

    def random_get(self, buf, size):
        self.mem.write(buf, os.urandom(size))

    def sched_yield(self):
        pass

    def proc_exit(self, code):
        raise WasiExit(code)

    def poll_oneoff(self, in_ptr, out_ptr, count, nevents_ptr):
        events = []
        for i in range(count):
            raw = self.mem.read(in_ptr + 48 * i, 48)
            userdata, tag = struct.unpack_from("<QB", raw, 0)
            kind = 0 if tag == 0 else tag
            if tag == 0:  # a clock: sleep until the timeout
                clock_id, timeout, _precision, flags = struct.unpack_from("<IxxxxQQH", raw, 16)
                if flags & 1:  # absolute
                    now = time.time_ns() if clock_id == 0 else time.monotonic_ns()
                    timeout = max(0, timeout - now)
                time.sleep(timeout / 1e9)
            events.append(struct.pack("<QHBxxxxxQHxxxxxx", userdata, ESUCCESS, kind, 0, 0))
        self.mem.write(out_ptr, b"".join(events))
        self.put32(nevents_ptr, len(events))

    # --- the preopened directory

    def fd_prestat_get(self, fd, buf):
        if not 3 <= fd < 3 + len(PREOPENS):
            raise WasiError(EBADF)
        self.mem.write(buf, struct.pack("<BxxxI", 0, len(PREOPENS[fd - 3])))  # a directory, and the size of its name

    def fd_prestat_dir_name(self, fd, path, size):
        if not 3 <= fd < 3 + len(PREOPENS):
            raise WasiError(EBADF)
        self.mem.write(path, PREOPENS[fd - 3].encode()[:size])

    # --- file descriptors

    def fd_close(self, fd):
        entry = self.file_entry(fd)
        if fd < 3 + len(PREOPENS):
            return
        if entry.osfd >= 0:
            os.close(entry.osfd)
        del self.fds[fd]

    def fd_read(self, fd, iovs, count, out):
        entry = self.file_entry(fd)
        total = 0
        for buf, size in self.iovs(iovs, count):
            if entry.kind == "stdin":
                data = self.stdin[self.stdin_pos : self.stdin_pos + size]
                self.stdin_pos += len(data)
            elif entry.kind == "file":
                data = os.read(entry.osfd, size)
            else:
                raise WasiError(EISDIR if entry.kind == "dir" else EBADF)
            self.mem.write(buf, data)
            total += len(data)
            if len(data) < size:
                break
        self.put32(out, total)

    def fd_write(self, fd, iovs, count, out):
        entry = self.file_entry(fd)
        total = 0
        for buf, size in self.iovs(iovs, count):
            data = self.mem.read(buf, size)
            if entry.kind == "stdout":
                if self.pipe_limit is not None and self.written + len(data) > self.pipe_limit:
                    raise WasiError(EPIPE)
                self.written += len(data)
                self.write_out(data)
            elif entry.kind == "stderr":
                self.write_err(data)
            elif entry.kind == "file":
                data = data[: os.write(entry.osfd, data)]
            else:
                raise WasiError(EBADF)
            total += len(data)
        self.put32(out, total)

    def fd_seek(self, fd, offset, whence, out):
        entry = self.file_entry(fd)
        if entry.kind != "file":
            raise WasiError(ESPIPE)
        # The first snapshot numbers the origins differently: CUR, END, SET.
        origins = (os.SEEK_CUR, os.SEEK_END, os.SEEK_SET) if self.old else (os.SEEK_SET, os.SEEK_CUR, os.SEEK_END)
        self.put64(out, os.lseek(entry.osfd, s64(offset), origins[whence]))

    def fd_renumber(self, fd, to):
        entry = self.file_entry(fd)
        other = self.fds.get(to)
        if other is not None and other is not entry and other.osfd >= 0 and to >= 3 + len(PREOPENS):
            os.close(other.osfd)
        self.fds[to] = entry
        if fd != to:
            del self.fds[fd]

    def fd_tell(self, fd, out):
        self.fd_seek(fd, 0, 1, out)

    def fd_sync(self, fd):
        entry = self.file_entry(fd)
        if entry.kind == "file":
            os.fsync(entry.osfd)

    def fd_datasync(self, fd):
        self.fd_sync(fd)

    def fd_advise(self, fd, offset, length, advice):
        self.file_entry(fd)

    def fd_fdstat_get(self, fd, buf):
        entry = self.file_entry(fd)
        if entry.kind == "dir":
            kind = 3
        elif entry.kind == "file":
            kind = filetype_of(os.fstat(entry.osfd).st_mode)
        else:
            kind = 2
        self.mem.write(buf, struct.pack("<BxHxxxxQQ", kind, 0, ALL_RIGHTS, ALL_RIGHTS))

    def fd_fdstat_set_flags(self, fd, flags):
        self.file_entry(fd)

    def stat_bytes(self, st):
        return struct.pack(
            "<QQBxxxxxxxQQQQQ", st.st_dev, st.st_ino, filetype_of(st.st_mode), st.st_nlink, st.st_size,
            st.st_atime_ns, st.st_mtime_ns, st.st_ctime_ns,
        )  # fmt: skip

    def fd_filestat_get(self, fd, buf):
        entry = self.file_entry(fd)
        if entry.kind == "file":
            self.mem.write(buf, self.stat_bytes(os.fstat(entry.osfd)))
        elif entry.kind == "dir":
            self.mem.write(buf, self.stat_bytes(os.stat(entry.path)))
        else:
            self.mem.write(buf, struct.pack("<QQBxxxxxxxQQQQQ", 0, 0, 2, 1, 0, 0, 0, 0))

    def fd_filestat_set_size(self, fd, size):
        entry = self.file_entry(fd)
        if entry.kind != "file":
            raise WasiError(EBADF)
        os.ftruncate(entry.osfd, size)

    def times(self, current, atim, mtim, flags):
        """The (atime, mtime) in ns that FST_FLAGS ask for; CURRENT is the file's stat."""
        now = time.time_ns()
        a = now if flags & 2 else atim if flags & 1 else current.st_atime_ns
        m = now if flags & 8 else mtim if flags & 4 else current.st_mtime_ns
        return a, m

    def fd_filestat_set_times(self, fd, atim, mtim, flags):
        entry = self.file_entry(fd)
        if entry.kind == "file":
            os.utime(entry.osfd, ns=self.times(os.fstat(entry.osfd), atim, mtim, flags))
        elif entry.kind == "dir":
            os.utime(entry.path, ns=self.times(os.stat(entry.path), atim, mtim, flags))
        else:
            raise WasiError(EBADF)

    def fd_readdir(self, fd, buf, size, cookie, used_ptr):
        entry = self.dir_entry(fd)
        names = [(".", os.stat(entry.path)), ("..", os.stat(os.path.join(entry.path, "..")))]
        for name in sorted(os.listdir(entry.path)):
            try:
                names.append((name, os.lstat(os.path.join(entry.path, name))))
            except OSError:
                continue
        out = b""
        for index in range(cookie, len(names)):
            name, st = names[index]
            raw = name.encode()
            out += struct.pack("<QQIBxxx", index + 1, st.st_ino, len(raw), filetype_of(st.st_mode)) + raw
            if len(out) >= size:
                break
        out = out[:size]
        self.mem.write(buf, out)
        self.put32(used_ptr, len(out))

    # --- paths

    def path_open(self, dirfd, dirflags, ptr, size, oflags, rights, inheriting, fdflags, out):
        follow = bool(dirflags & 1)
        path = self.resolve(dirfd, ptr, size, follow)
        write = bool(rights & (RIGHT_WRITE | RIGHT_SET_SIZE | RIGHT_ALLOCATE))
        read = bool(rights & (RIGHT_READ | RIGHT_READDIR)) or not write
        exists_dir = os.path.isdir(path) if follow else os.path.isdir(path) and not os.path.islink(path)
        if oflags & 2 or exists_dir:  # a directory
            if oflags & 1 and not os.path.exists(path):
                raise WasiError(EISDIR if not oflags & 2 else ENOENT)
            if not os.path.isdir(path):
                raise WasiError(ENOTDIR if os.path.exists(path) else ENOENT)
            if write and not oflags & 2:
                raise WasiError(EISDIR)
            self.put32(out, self.add_fd(Fd("dir", path)))
            return
        flags = os.O_RDWR if read and write else os.O_WRONLY if write else os.O_RDONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        if oflags & 1:
            flags |= os.O_CREAT
        if oflags & 4:
            flags |= os.O_EXCL
        if oflags & 8:
            flags |= os.O_TRUNC
        if fdflags & 1:
            flags |= os.O_APPEND
        if not follow:
            flags |= getattr(os, "O_NOFOLLOW", 0)
        self.put32(out, self.add_fd(Fd("file", path, os.open(path, flags, 0o666))))

    def path_create_directory(self, fd, ptr, size):
        os.mkdir(self.resolve(fd, ptr, size, follow=False))

    def path_remove_directory(self, fd, ptr, size):
        os.rmdir(self.resolve(fd, ptr, size, follow=False))

    def path_unlink_file(self, fd, ptr, size):
        os.unlink(self.resolve(fd, ptr, size, follow=False))

    def path_rename(self, fd, ptr, size, new_fd, new_ptr, new_size):
        os.rename(self.resolve(fd, ptr, size, follow=False), self.resolve(new_fd, new_ptr, new_size, follow=False))

    def path_link(self, fd, flags, ptr, size, new_fd, new_ptr, new_size):
        source = self.resolve(fd, ptr, size, follow=bool(flags & 1))
        os.link(source, self.resolve(new_fd, new_ptr, new_size, follow=False), follow_symlinks=bool(flags & 1))

    def path_symlink(self, ptr, size, fd, new_ptr, new_size):
        os.symlink(self.string(ptr, size), self.resolve(fd, new_ptr, new_size, follow=False))

    def path_readlink(self, fd, ptr, size, buf, buf_size, used_ptr):
        target = os.readlink(self.resolve(fd, ptr, size, follow=False)).encode()[:buf_size]
        self.mem.write(buf, target)
        self.put32(used_ptr, len(target))

    def path_filestat_get(self, fd, flags, ptr, size, buf):
        path = self.resolve(fd, ptr, size, follow=bool(flags & 1))
        self.mem.write(buf, self.stat_bytes(os.stat(path) if flags & 1 else os.lstat(path)))

    def path_filestat_set_times(self, fd, flags, ptr, size, atim, mtim, fst_flags):
        path = self.resolve(fd, ptr, size, follow=bool(flags & 1))
        st = os.stat(path) if flags & 1 else os.lstat(path)
        os.utime(path, ns=self.times(st, atim, mtim, fst_flags), follow_symlinks=bool(flags & 1))


# --- the shell

OPERATORS = {"|", "<", ">", ">>", ";", "&&", "||"}


def cache_root():
    """Where downloads are kept: $WASMHOST_CACHE; else ~/.cache/wasmhost; else ./.cache (no usable home)."""
    if env := os.environ.get("WASMHOST_CACHE"):
        return env
    try:
        home = os.path.expanduser("~")
        if home != "~" and os.path.isdir(home) and os.access(home, os.W_OK):
            return os.path.join(home, ".cache", "wasmhost")
    except Exception:  # noqa: BLE001 -- a sandbox that won't even say
        pass
    return os.path.join(".", ".cache")


def find_wasm(explicit=None):
    if explicit:
        return explicit
    beside = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wasm", "coreutils.wasm")
    if os.path.exists(beside):
        return beside
    cached = os.path.join(cache_root(), "coreutils", "coreutils.wasm")
    if not os.path.exists(cached):
        print(f"downloading {WASM_URL}", flush=True)
        os.makedirs(os.path.dirname(cached), exist_ok=True)
        with urllib.request.urlopen(WASM_URL, timeout=120) as resp:  # noqa: S310
            data = resp.read()
        with open(cached + ".part", "wb") as f:  # so that an interrupted download is not taken for a whole file
            f.write(data)
        os.replace(cached + ".part", cached)
    return cached


def find_program(name):
    """The module of an extra program (`lua`): examples/wasm/ next to this file, else the cache, downloaded once."""
    wasm, url, member = PROGRAMS[name]
    beside = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wasm", wasm)
    if os.path.exists(beside):
        return beside
    cached = os.path.join(cache_root(), "coreutils", wasm)
    if not os.path.exists(cached):
        print(f"downloading {url}", flush=True)
        os.makedirs(os.path.dirname(cached), exist_ok=True)
        with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310
            data = resp.read()
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            extracted = tar.extractfile(member)
            if extracted is None:
                raise RuntimeError(f"{member} is not in {url}")
            with open(cached + ".part", "wb") as f:  # so that an interrupted download is not taken for a whole file
                f.write(extracted.read())
        os.replace(cached + ".part", cached)
    return cached


def default_root():
    documents = os.path.expanduser("~/Documents")
    return os.path.join(documents if os.path.isdir(documents) else ".", "wasm-root")


class Shell:
    def __init__(self, root, module, out=None):
        self.root = os.path.realpath(root)
        os.makedirs(self.root, exist_ok=True)
        self.cwd = self.root
        self.module = module
        self.extra = {}  # extra programs, compiled when first run
        self.out = out or self._console

    @staticmethod
    def _console(text):
        sys.stdout.write(text)
        sys.stdout.flush()

    def console_writer(self):
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        return lambda data: self.out(decoder.decode(data))

    @property
    def where(self):
        rel = os.path.relpath(self.cwd, self.root)
        return "/" if rel == "." else "/" + rel.replace(os.sep, "/")

    # --- parsing

    def tokens(self, line):
        lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)

    def expand(self, word):
        if not any(c in word for c in "*?["):
            return [word]
        found = sorted(glob.glob(os.path.join(glob.escape(self.cwd), word)))
        return [os.path.relpath(p, self.cwd) for p in found] or [word]

    def parse(self, line):
        """A list of (pipeline, connector before it); a pipeline is a list of commands (words, in, out, append)."""
        sequence, pipeline, command = [], [], {"words": [], "in": None, "out": None, "append": False}
        connector = ";"
        redirect = None

        def end_command():
            nonlocal command
            if command["words"]:
                pipeline.append(command)
            elif redirect or pipeline:
                raise ValueError("empty command")
            command = {"words": [], "in": None, "out": None, "append": False}

        for token in self.tokens(line):
            if redirect:
                if token in OPERATORS:
                    raise ValueError(f"nothing after {redirect}")
                if redirect == "<":
                    command["in"] = token
                else:
                    command["out"], command["append"] = token, redirect == ">>"
                redirect = None
            elif token in ("<", ">", ">>"):
                redirect = token
            elif token == "|":
                end_command()
            elif token in (";", "&&", "||"):
                end_command()
                if pipeline:
                    sequence.append((pipeline, connector))
                pipeline, connector = [], token
            else:
                command["words"].append(token)
        if redirect:
            raise ValueError(f"nothing after {redirect}")
        end_command()
        if pipeline:
            sequence.append((pipeline, connector))
        return sequence

    # --- running

    def host_path(self, name):
        """The host path of NAME, which may be absolute: `/` is the root of the directory, as the prompt shows it."""
        base = self.root if name.startswith("/") else self.cwd
        path = os.path.normpath(os.path.join(base, name.lstrip("/")))
        real = os.path.realpath(path)
        if real != self.root and not real.startswith(self.root + os.sep):
            raise ValueError(f"{name}: outside the root")
        return path

    def builtin(self, words):
        """The exit code of a builtin, or None if WORDS is not one."""
        name, args = words[0], words[1:]
        if name in ("exit", "quit"):
            raise SystemExit(int(args[0]) if args else 0)
        if name == "pwd":
            self.out(self.where + "\n")
            return 0
        if name == "cd":
            target = self.host_path(args[0]) if args else self.root
            if not os.path.isdir(target):
                self.out(f"cd: {args[0] if args else '/'}: no such directory\n")
                return 1
            self.cwd = os.path.realpath(target)
            return 0
        if name == "help":
            self.out(f"builtins: cd pwd help exit; also {' '.join(PROGRAMS)} (a program of its own); the utilities:\n")
            return self.run_program(["--help"], b"", self.console_writer())
        return None

    def run_program(self, argv, stdin, stdout, limit=None):
        name = argv[0]
        if name in PROGRAMS:
            if name not in self.extra:
                with open(find_program(name), "rb") as f:
                    self.extra[name] = wasmhost.Module(f.read())
            module, args = self.extra[name], argv
        else:
            module, args = self.module, ["coreutils", *argv]
        wasi = Wasi(
            self.cwd, args, stdin, stdout, self.console_writer(),
            env={"PWD": "/", "HOME": "/"}, pipe_limit=limit,
        )  # fmt: skip
        try:
            return wasi.run(module)
        except wasmhost.Trap:
            # What a program does when it cannot go on: Lua built without longjmp traps on any error of the script.
            self.out(f"\n{name}: the program stopped with a trap\n")
            return 134

    def run_pipeline(self, pipeline):
        data = b""
        code = 0
        for index, command in enumerate(pipeline):
            last = index == len(pipeline) - 1
            if command["in"] is not None:
                with open(self.host_path(command["in"]), "rb") as f:
                    data = f.read()
            words = command["words"][:1] + [w for word in command["words"][1:] for w in self.expand(word)]
            if len(pipeline) == 1 and command["in"] is None and command["out"] is None:
                builtin = self.builtin(words)
                if builtin is not None:
                    return builtin
            chunks = []
            if last and command["out"] is None:
                sink, limit = self.console_writer(), None
            else:
                sink, limit = chunks.append, None if last else PIPE_LIMIT
            code = self.run_program(words, data, sink, limit)
            data = b"".join(chunks)
            if command["out"] is not None:
                with open(self.host_path(command["out"]), "ab" if command["append"] else "wb") as f:
                    f.write(data)
                data = b""
        return code

    def run_line(self, line):
        """Run a line; the exit code of the last command."""
        try:
            sequence = self.parse(line)
        except ValueError as exc:
            self.out(f"syntax error: {exc}\n")
            return 2
        code = 0
        for pipeline, connector in sequence:
            if (connector == "&&" and code != 0) or (connector == "||" and code == 0):
                continue
            try:
                code = self.run_pipeline(pipeline)
            except (OSError, ValueError) as exc:
                self.out(f"{exc}\n")
                code = 1
        return code


def main():
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("-c", dest="command", metavar="LINE", help="run this line and exit")
    parser.add_argument(
        "--root", default=default_root(), help="the directory the programs see as / (default: %(default)s)"
    )
    parser.add_argument("--backend", choices=sorted(wasmhost.BACKENDS), help="the engine (default: the first found)")
    parser.add_argument("--wasm", help="the coreutils.wasm to use (default: examples/wasm/, else downloaded)")
    args = parser.parse_args()

    wasm = find_wasm(args.wasm)
    if args.command is None:
        print("compiling coreutils.wasm in the engine (once)...", flush=True)
    with open(wasm, "rb") as f:
        module = wasmhost.Module(f.read(), backend=args.backend)
    shell = Shell(args.root, module)
    if args.command is not None:
        return shell.run_line(args.command)
    print(f"root {shell.root} is /; backend {wasmhost.get_backend().name}; `help` lists the utilities, `exit` leaves")
    while True:
        try:
            line = input(f"wasm:{shell.where}$ ")
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            continue
        try:
            shell.run_line(line)
        except SystemExit as exc:
            return exc.code


if __name__ == "__main__":
    sys.exit(main())
