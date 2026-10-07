"""`wasmhost.wasi1` behaviour: the calls made directly, over a plain bytearray standing in for the program's memory."""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path
from typing import Any

import pytest

from wasmhost import wasi1
from wasmhost.wasi1 import Errno, Fdflags, Filetype, Lookupflags, Oflags, Rights, Wasi, WasiExit

SIZE = 1 << 16
FOLLOW = Lookupflags.symlink_follow
ALL = 0xFFFFFFFFFFFFFFFF  # what a C library asks for when it wants every right


class Memory:
    def __init__(self) -> None:
        self.data = bytearray(SIZE)

    def read(self, offset: int, length: int) -> bytes:
        if offset < 0 or offset + length > SIZE:
            raise IndexError("out of bounds")
        return bytes(self.data[offset : offset + length])

    def write(self, offset: int, data: bytes) -> None:
        if offset < 0 or offset + len(data) > SIZE:
            raise IndexError("out of bounds")
        self.data[offset : offset + len(data)] = data

    def __len__(self) -> int:
        return SIZE


class Host:
    """A Wasi with a memory, and the helpers a test needs to put things in it and read results out."""

    def __init__(self, **options: Any) -> None:
        self.wasi = Wasi(**options)
        self.mem = Memory()
        self.wasi.memory = self.mem
        self.calls = self.wasi.imports()[wasi1.SNAPSHOT]
        self.top = 4096  # scratch space is handed out from here

    def __call__(self, name: str, *args: int) -> int:
        result = self.calls[name](*args)
        return 0 if result is None else result

    def ok(self, name: str, *args: int) -> None:
        code = self(name, *args)
        assert code == 0, f"{name} -> {wasi1.TABLES['errno'][code]}"

    def put(self, data: bytes) -> int:
        ptr, self.top = self.top, self.top + len(data) + 8
        self.mem.write(ptr, data)
        return ptr

    def text(self, text: str) -> tuple[int, int]:
        raw = text.encode()
        return self.put(raw), len(raw)

    def alloc(self, size: int) -> int:
        ptr, self.top = self.top, self.top + size + 8
        return ptr

    def u32(self, ptr: int) -> int:
        return struct.unpack("<I", self.mem.read(ptr, 4))[0]

    def u64(self, ptr: int) -> int:
        return struct.unpack("<Q", self.mem.read(ptr, 8))[0]

    def iov(self, *buffers: tuple[int, int]) -> tuple[int, int]:
        return self.put(b"".join(struct.pack("<II", p, n) for p, n in buffers)), len(buffers)

    def write_fd(self, fd: int, data: bytes) -> int:
        buf, out = self.put(data), self.alloc(4)
        iovs, count = self.iov((buf, len(data)))
        self.ok("fd_write", fd, iovs, count, out)
        return self.u32(out)

    def read_fd(self, fd: int, size: int) -> bytes:
        buf, out = self.alloc(size), self.alloc(4)
        iovs, count = self.iov((buf, size))
        self.ok("fd_read", fd, iovs, count, out)
        return self.mem.read(buf, self.u32(out))

    def open(
        self, name: str, oflags: int = 0, rights: int = ALL, fdflags: int = 0, dirfd: int = 3, flags: int = FOLLOW
    ) -> tuple[int, int]:
        ptr, size = self.text(name)
        out = self.alloc(4)
        code = self("path_open", dirfd, flags, ptr, size, oflags, rights, rights, fdflags, out)
        return code, self.u32(out)

    def path(self, call: str, name: str, *extra: int, dirfd: int = 3) -> int:
        ptr, size = self.text(name)
        return self(call, dirfd, ptr, size, *extra)

    def stat(self, fd: int) -> tuple[int, ...]:
        out = self.alloc(64)
        self.ok("fd_filestat_get", fd, out)
        return wasi1.STRUCTS["filestat"].unpack(self.mem.read(out, 64))


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "world").mkdir()
    return tmp_path / "world"


@pytest.fixture
def host(root: Path) -> Host:
    return Host(args=["prog", "-v", "héllo"], env={"A": "1", "B": "two"}, preopens={"/": root})


# --- arguments, environment


def test_args(host: Host) -> None:
    count, size = host.alloc(4), host.alloc(4)
    host.ok("args_sizes_get", count, size)
    assert (host.u32(count), host.u32(size)) == (3, len(b"prog\0-v\0h\xc3\xa9llo\0"))
    table, buf = host.alloc(12), host.alloc(host.u32(size))
    host.ok("args_get", table, buf)
    pointers = struct.unpack("<3I", host.mem.read(table, 12))
    assert pointers[0] == buf and pointers[1] == buf + 5
    assert host.mem.read(buf, host.u32(size)) == b"prog\0-v\0h\xc3\xa9llo\0"


def test_environ(host: Host) -> None:
    count, size = host.alloc(4), host.alloc(4)
    host.ok("environ_sizes_get", count, size)
    assert (host.u32(count), host.u32(size)) == (2, len(b"A=1\0B=two\0"))
    table, buf = host.alloc(8), host.alloc(host.u32(size))
    host.ok("environ_get", table, buf)
    assert host.mem.read(buf, host.u32(size)) == b"A=1\0B=two\0"


def test_no_arguments_at_all() -> None:
    host = Host()
    count, size = host.alloc(4), host.alloc(4)
    host.ok("args_sizes_get", count, size)
    assert (host.u32(count), host.u32(size)) == (0, 0)


# --- standard streams


def test_stdout_and_stderr_take_bytes() -> None:
    out: list[bytes] = []
    err: list[bytes] = []
    host = Host(stdout=out.append, stderr=err.append)
    assert host.write_fd(1, b"hello ") == 6
    assert host.write_fd(2, b"oops") == 4
    assert (b"".join(out), b"".join(err)) == (b"hello ", b"oops")


def test_gathering_write_joins_the_buffers() -> None:
    out: list[bytes] = []
    host = Host(stdout=out.append)
    a, b = host.put(b"ab"), host.put(b"cde")
    iovs, count = host.iov((a, 2), (b, 3))
    n = host.alloc(4)
    host.ok("fd_write", 1, iovs, count, n)
    assert (host.u32(n), b"".join(out)) == (5, b"abcde")


def test_stdout_can_be_an_object_with_write() -> None:
    class Sink:
        def __init__(self) -> None:
            self.got = b""

        def write(self, data: bytes) -> None:
            self.got += data

    sink = Sink()
    host = Host(stdout=sink)
    host.write_fd(1, b"x")
    assert sink.got == b"x"


def test_default_streams_are_the_interpreters(capsys: pytest.CaptureFixture[str]) -> None:
    host = Host()
    host.write_fd(1, "ü\n".encode())
    host.write_fd(2, b"e\n")
    seen = capsys.readouterr()
    assert (seen.out, seen.err) == ("ü\n", "e\n")


def test_stdin_bytes_and_end_of_file() -> None:
    host = Host(stdin=b"abcdef")
    assert host.read_fd(0, 4) == b"abcd"
    assert host.read_fd(0, 4) == b"ef"
    assert host.read_fd(0, 4) == b""


def test_stdin_from_a_file_object() -> None:
    import io

    host = Host(stdin=io.BytesIO(b"xyz"))
    assert host.read_fd(0, 2) == b"xy"
    assert host.read_fd(0, 9) == b"z"


def test_no_stdin_is_empty() -> None:
    assert Host().read_fd(0, 8) == b""


def test_stdout_is_not_readable_and_stdin_not_writable() -> None:
    host = Host()
    buf = host.put(b"x")
    iovs, count = host.iov((buf, 1))
    n = host.alloc(4)
    assert host("fd_read", 1, iovs, count, n) == Errno.notcapable
    assert host("fd_write", 0, iovs, count, n) == Errno.notcapable


def test_a_failing_sink_is_an_errno() -> None:
    def broken(data: bytes) -> None:
        raise BrokenPipeError

    host = Host(stdout=broken)
    buf = host.put(b"x")
    iovs, count = host.iov((buf, 1))
    assert host("fd_write", 1, iovs, count, host.alloc(4)) == Errno.pipe


# --- preopens


def test_prestat(host: Host, root: Path) -> None:
    out = host.alloc(8)
    host.ok("fd_prestat_get", 3, out)
    assert host.mem.read(out, 8) == struct.pack("<BxxxI", 0, 1)
    name = host.alloc(1)
    host.ok("fd_prestat_dir_name", 3, name, 1)
    assert host.mem.read(name, 1) == b"/"
    assert host("fd_prestat_get", 4, out) == Errno.badf  # the end of the preopens: how a C library knows to stop
    assert host("fd_prestat_get", 1, out) == Errno.badf  # not a preopen
    assert host("fd_prestat_dir_name", 3, name, 0) == Errno.nametoolong


def test_several_preopens_in_order(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    host = Host(preopens={"/": tmp_path / "a", "data": tmp_path / "b"})
    out = host.alloc(8)
    host.ok("fd_prestat_get", 3, out)
    host.ok("fd_prestat_get", 4, out)
    assert host.u32(out + 4) == 4
    name = host.alloc(4)
    host.ok("fd_prestat_dir_name", 4, name, 4)
    assert host.mem.read(name, 4) == b"data"
    assert host.open("f", Oflags.creat, dirfd=4)[0] == 0
    assert (tmp_path / "b" / "f").exists() and not (tmp_path / "a" / "f").exists()


def test_a_preopen_must_be_a_directory(tmp_path: Path) -> None:
    with pytest.raises(NotADirectoryError):
        Wasi(preopens={"/": tmp_path / "missing"})


# --- files


def test_create_write_read_back(host: Host, root: Path) -> None:
    code, fd = host.open("a.txt", Oflags.creat | Oflags.excl)
    assert code == 0 and fd >= 4
    assert host.write_fd(fd, b"hello world") == 11
    assert (root / "a.txt").read_bytes() == b"hello world"
    pos = host.alloc(8)
    host.ok("fd_seek", fd, 0, 0, pos)  # set
    assert host.read_fd(fd, 5) == b"hello"
    host.ok("fd_tell", fd, pos)
    assert host.u64(pos) == 5
    host.ok(
        "fd_seek", fd, (-5) & ALL, 2, pos
    )  # end, backwards: a negative offset arrives as an unsigned i64 or a signed one
    assert host.u64(pos) == 6
    assert host.read_fd(fd, 99) == b"world"
    assert host.read_fd(fd, 99) == b""
    host.ok("fd_close", fd)
    assert host("fd_close", fd) == Errno.badf


def test_exclusive_create_fails_when_it_exists(host: Host, root: Path) -> None:
    (root / "a").write_text("x")
    assert host.open("a", Oflags.creat | Oflags.excl)[0] == Errno.exist


def test_missing_file(host: Host) -> None:
    assert host.open("nope")[0] == Errno.noent


def test_truncate_on_open(host: Host, root: Path) -> None:
    (root / "a").write_text("long contents")
    code, fd = host.open("a", Oflags.trunc)
    assert code == 0
    assert (root / "a").read_text() == ""
    host.ok("fd_close", fd)


def test_seek_whence_is_checked(host: Host, root: Path) -> None:
    (root / "a").write_text("x")
    _, fd = host.open("a")
    assert host("fd_seek", fd, 0, 3, host.alloc(8)) == Errno.inval
    assert host("fd_seek", 0, 0, 0, host.alloc(8)) == Errno.spipe  # stdin is not seekable


def test_pread_and_pwrite_leave_the_position(host: Host, root: Path) -> None:
    (root / "a").write_bytes(b"0123456789")
    _, fd = host.open("a")
    buf, n = host.alloc(4), host.alloc(4)
    iovs, count = host.iov((buf, 4))
    host.ok("fd_pread", fd, iovs, count, 3, n)
    assert host.mem.read(buf, host.u32(n)) == b"3456"
    data = host.put(b"AB")
    iovs, count = host.iov((data, 2))
    host.ok("fd_pwrite", fd, iovs, count, 8, n)
    assert (root / "a").read_bytes() == b"01234567AB"
    assert host.read_fd(fd, 3) == b"012"  # the position never moved


def test_a_read_through_two_buffers_stops_at_the_short_one(host: Host, root: Path) -> None:
    (root / "a").write_bytes(b"abc")
    _, fd = host.open("a")
    one, two, n = host.alloc(2), host.alloc(4), host.alloc(4)
    iovs, count = host.iov((one, 2), (two, 4))
    host.ok("fd_read", fd, iovs, count, n)
    assert host.u32(n) == 3
    assert host.mem.read(one, 2) + host.mem.read(two, 1) == b"abc"


def test_append_flag(host: Host, root: Path) -> None:
    (root / "log").write_bytes(b"one\n")
    _, fd = host.open("log", fdflags=Fdflags.append)
    pos = host.alloc(8)
    host.ok("fd_seek", fd, 0, 0, pos)
    host.write_fd(fd, b"two\n")
    assert (root / "log").read_bytes() == b"one\ntwo\n"


def test_fdstat(host: Host, root: Path) -> None:
    (root / "a").write_text("x")
    _, fd = host.open("a", rights=Rights.fd_read | Rights.fd_fdstat_set_flags, fdflags=Fdflags.append)
    out = host.alloc(24)
    host.ok("fd_fdstat_get", fd, out)
    filetype, flags, base, inheriting = wasi1.STRUCTS["fdstat"].unpack(host.mem.read(out, 24))
    assert filetype == Filetype.regular_file and flags == Fdflags.append
    assert base & Rights.fd_read and not base & Rights.fd_write  # opened for reading only: no right to write
    host.ok("fd_fdstat_set_flags", fd, Fdflags.sync)
    host.ok("fd_fdstat_get", fd, out)
    assert wasi1.STRUCTS["fdstat"].unpack(host.mem.read(out, 24))[1] == Fdflags.sync
    assert host("fd_fdstat_set_flags", fd, 1 << 5) == Errno.inval
    host.ok("fd_fdstat_get", 1, out)
    assert host.mem.read(out, 1)[0] == Filetype.character_device


def test_rights_can_be_given_up_but_not_regained(host: Host, root: Path) -> None:
    (root / "a").write_text("x")
    _, fd = host.open("a", Oflags.creat)
    assert host("fd_fdstat_set_rights", fd, Rights.fd_read, 0) == 0
    buf = host.put(b"x")
    iovs, count = host.iov((buf, 1))
    assert host("fd_write", fd, iovs, count, host.alloc(4)) == Errno.notcapable
    assert host("fd_fdstat_set_rights", fd, Rights.fd_read | Rights.fd_write, 0) == Errno.notcapable


def test_rights_a_directory_hands_down(host: Host, root: Path) -> None:
    (root / "d").mkdir()
    (root / "d" / "f").write_text("x")
    _, narrow = host.open("d", Oflags.directory, Rights.path_open | Rights.fd_read, 0)
    code, fd = host.open("f", rights=Rights.fd_read | Rights.fd_write, dirfd=narrow)
    assert code == 0
    out = host.alloc(24)
    host.ok("fd_fdstat_get", fd, out)
    assert (
        wasi1.STRUCTS["fdstat"].unpack(host.mem.read(out, 24))[2] & Rights.fd_write == 0
    )  # the directory could not hand it down


def test_filestat_of_file_and_directory(host: Host, root: Path) -> None:
    (root / "a").write_bytes(b"12345")
    _, fd = host.open("a")
    st = host.stat(fd)
    assert st[2] == Filetype.regular_file and st[4] == 5 and st[3] == 1
    assert st[6] == (root / "a").stat().st_mtime_ns
    assert host.stat(3)[2] == Filetype.directory
    assert host.stat(1)[2] == Filetype.character_device


def test_set_size_and_allocate(host: Host, root: Path) -> None:
    _, fd = host.open("a", Oflags.creat)
    host.ok("fd_filestat_set_size", fd, 10)
    assert (root / "a").stat().st_size == 10
    host.ok("fd_allocate", fd, 8, 20)
    assert (root / "a").stat().st_size >= 28
    host.ok("fd_sync", fd)
    host.ok("fd_datasync", fd)
    host.ok("fd_advise", fd, 0, 10, 3)
    assert host("fd_advise", fd, 0, 10, 6) == Errno.inval


def test_set_times_on_a_descriptor(host: Host, root: Path) -> None:
    (root / "a").write_text("x")
    _, fd = host.open("a")
    host.ok("fd_filestat_set_times", fd, 5_000_000_000, 7_000_000_000, 1 | 4)  # atim, mtim
    st = (root / "a").stat()
    assert (st.st_atime_ns, st.st_mtime_ns) == (5_000_000_000, 7_000_000_000)
    assert host("fd_filestat_set_times", fd, 0, 0, 1 | 2) == Errno.inval  # a time and "now" at once


def test_set_times_by_path_and_the_omitted_one_stays(host: Host, root: Path) -> None:
    (root / "a").write_text("x")
    before = (root / "a").stat().st_atime_ns
    ptr, size = host.text("a")
    host.ok("path_filestat_set_times", 3, FOLLOW, ptr, size, 0, 9_000_000_000, 4)
    st = (root / "a").stat()
    assert st.st_mtime_ns == 9_000_000_000
    assert abs(st.st_atime_ns - before) < 5_000_000_000  # untouched (reading the file can move it a little)


def test_renumber(host: Host, root: Path) -> None:
    (root / "a").write_text("A")
    (root / "b").write_text("B")
    _, a = host.open("a")
    _, b = host.open("b")
    host.ok("fd_renumber", a, b)
    assert host("fd_close", a) == Errno.badf
    assert host.read_fd(b, 1) == b"A"
    assert host("fd_renumber", 99, b) == Errno.badf


def test_descriptors_are_reused_lowest_first(host: Host, root: Path) -> None:
    (root / "a").write_text("A")
    _, first = host.open("a")
    _, second = host.open("a")
    host.ok("fd_close", first)
    assert host.open("a")[1] == first
    assert second == first + 1


def test_bad_descriptor_everywhere(host: Host) -> None:
    buf = host.alloc(64)
    for name, args in {
        "fd_close": (99,),
        "fd_sync": (99,),
        "fd_tell": (99, buf),
        "fd_fdstat_get": (99, buf),
        "fd_filestat_get": (99, buf),
        "fd_seek": (99, 0, 0, buf),
        "fd_readdir": (99, buf, 8, 0, buf),
    }.items():
        assert host(name, *args) == Errno.badf, name


# --- directories


def listing(host: Host, fd: int, size: int = 4096, cookie: int = 0) -> tuple[list[tuple[str, int, int]], int]:
    buf, used = host.alloc(size), host.alloc(4)
    host.ok("fd_readdir", fd, buf, size, cookie, used)
    data, entries, pos = host.mem.read(buf, host.u32(used)), [], 0
    while pos + 24 <= len(data):
        nxt, ino, namlen, kind = wasi1.STRUCTS["dirent"].unpack_from(data, pos)
        if pos + 24 + namlen > len(data):
            break
        entries.append((data[pos + 24 : pos + 24 + namlen].decode(), kind, nxt))
        pos += 24 + namlen
    return entries, host.u32(used)


def test_readdir(host: Host, root: Path) -> None:
    (root / "b.txt").write_text("x")
    (root / "a").mkdir()
    entries, used = listing(host, 3)
    assert [name for name, _, _ in entries] == [".", "..", "a", "b.txt"]
    assert [kind for _, kind, _ in entries] == [Filetype.directory] * 3 + [Filetype.regular_file]
    assert [nxt for _, _, nxt in entries] == [1, 2, 3, 4]
    assert used < 4096  # the end: less than the buffer


def test_readdir_resumes_from_a_cookie_and_a_small_buffer_is_full(host: Host, root: Path) -> None:
    for name in ("a", "b", "c"):
        (root / name).write_text("x")
    seen, cookie = [], 0
    while True:
        entries, used = listing(host, 3, size=50, cookie=cookie)  # room for one entry and a piece of the next
        seen += [name for name, _, _ in entries]
        if used < 50:
            break
        cookie = entries[-1][2]
    assert seen == [".", "..", "a", "b", "c"]


def test_readdir_of_a_file_is_notdir(host: Host, root: Path) -> None:
    (root / "a").write_text("x")
    _, fd = host.open("a")
    assert host("fd_readdir", fd, host.alloc(64), 64, 0, host.alloc(4)) == Errno.notdir


def test_reading_a_directory_is_isdir(host: Host) -> None:
    buf = host.alloc(8)
    iovs, count = host.iov((buf, 8))
    assert host("fd_read", 3, iovs, count, host.alloc(4)) == Errno.isdir


def test_open_a_directory(host: Host, root: Path) -> None:
    (root / "d").mkdir()
    (root / "d" / "f").write_text("x")
    code, fd = host.open("d", Oflags.directory)
    assert code == 0
    entries, _ = listing(host, fd)
    assert [n for n, _, _ in entries] == [".", "..", "f"]
    assert host.open("d/f", Oflags.directory)[0] == Errno.notdir
    assert host.open("nothing", Oflags.directory)[0] == Errno.noent


def test_mkdir_rmdir_unlink_rename(host: Host, root: Path) -> None:
    assert host.path("path_create_directory", "d") == 0
    assert (root / "d").is_dir()
    assert host.path("path_create_directory", "d") == Errno.exist
    (root / "d" / "f").write_text("x")
    assert host.path("path_remove_directory", "d") == Errno.notempty
    assert host.path("path_unlink_file", "d") == Errno.isdir
    assert host.path("path_unlink_file", "d/f") == 0
    assert host.path("path_remove_directory", "d") == 0
    assert host.path("path_unlink_file", "d") == Errno.noent
    (root / "x").write_text("X")
    old, olen = host.text("x")
    new, nlen = host.text("y")
    host.ok("path_rename", 3, old, olen, 3, new, nlen)
    assert (root / "y").read_text() == "X" and not (root / "x").exists()


def test_filestat_of_a_path(host: Host, root: Path) -> None:
    (root / "a").write_bytes(b"123")
    out = host.alloc(64)
    ptr, size = host.text("a")
    host.ok("path_filestat_get", 3, FOLLOW, ptr, size, out)
    assert wasi1.STRUCTS["filestat"].unpack(host.mem.read(out, 64))[4] == 3
    ptr, size = host.text("zzz")
    assert host("path_filestat_get", 3, FOLLOW, ptr, size, out) == Errno.noent


@pytest.mark.skipif(sys.platform == "win32", reason="symbolic links need a privilege on Windows")
def test_symlink_readlink_and_link(host: Host, root: Path) -> None:
    (root / "target").write_text("T")
    t, tl = host.text("target")
    n, nl = host.text("sym")
    host.ok("path_symlink", t, tl, 3, n, nl)
    assert os.readlink(root / "sym") == "target"
    buf, used = host.alloc(32), host.alloc(4)
    host.ok("path_readlink", 3, n, nl, buf, 32, used)
    assert host.mem.read(buf, host.u32(used)) == b"target"
    host.ok("path_readlink", 3, n, nl, buf, 3, used)  # a short buffer cuts the name
    assert host.mem.read(buf, host.u32(used)) == b"tar"
    out = host.alloc(64)
    host.ok("path_filestat_get", 3, 0, n, nl, out)  # without follow: the link itself
    assert wasi1.STRUCTS["filestat"].unpack(host.mem.read(out, 64))[2] == Filetype.symbolic_link
    host.ok("path_filestat_get", 3, FOLLOW, n, nl, out)
    assert wasi1.STRUCTS["filestat"].unpack(host.mem.read(out, 64))[2] == Filetype.regular_file
    h, hl = host.text("hard")
    host.ok("path_link", 3, FOLLOW, t, tl, 3, h, hl)
    assert (root / "hard").read_text() == "T" and (root / "target").stat().st_nlink == 2


# --- the sandbox


def test_names_that_leave_the_directory_are_refused(host: Host, root: Path) -> None:
    (root.parent / "secret").write_text("S")
    for name in ("../secret", "d/../../secret", "/etc/passwd", "a/../.."):
        assert host.open(name)[0] == Errno.notcapable, name
    assert host.path("path_unlink_file", "../secret") == Errno.notcapable
    assert (root.parent / "secret").exists()
    assert host.path("path_create_directory", "../new") == Errno.notcapable
    assert not (root.parent / "new").exists()


def test_dot_dot_that_stays_inside_is_fine(host: Host, root: Path) -> None:
    (root / "d").mkdir()
    (root / "f").write_text("x")
    assert host.open("d/../f")[0] == 0


@pytest.mark.skipif(sys.platform == "win32", reason="symbolic links need a privilege on Windows")
def test_a_link_that_leads_out_is_refused(host: Host, root: Path) -> None:
    (root.parent / "secret").write_text("S")
    (root / "out").symlink_to(root.parent / "secret")
    (root / "outdir").symlink_to(root.parent)
    assert host.open("out")[0] == Errno.notcapable
    assert host.open("outdir/secret")[0] == Errno.notcapable
    code, _ = host.open("out", flags=0)  # not followed: opening the link itself is refused by the system
    assert code != 0
    ptr, size = host.text("out")
    host.ok("path_filestat_get", 3, 0, ptr, size, host.alloc(64))  # looking at the link itself is allowed


def test_a_directory_below_is_its_own_world(host: Host, root: Path) -> None:
    (root / "d").mkdir()
    (root / "top").write_text("x")
    _, sub = host.open("d", Oflags.directory)
    assert host.open("../top", dirfd=sub)[0] == Errno.notcapable


def test_bad_names(host: Host) -> None:
    assert host.open("")[0] == Errno.noent
    buf = host.put(b"\xff\xfe")
    assert host("path_open", 3, FOLLOW, buf, 2, 0, ALL, ALL, 0, host.alloc(4)) == Errno.ilseq
    buf = host.put(b"a\0b")
    assert host("path_open", 3, FOLLOW, buf, 3, 0, ALL, ALL, 0, host.alloc(4)) == Errno.inval
    assert host.open("x", dirfd=1)[0] == Errno.notdir


# --- clocks, random, scheduling, exit


def test_clocks(host: Host) -> None:
    out = host.alloc(8)
    for clock in range(4):
        host.ok("clock_res_get", clock, out)
        assert host.u64(out) >= 1
        host.ok("clock_time_get", clock, 1, out)
    host.ok("clock_time_get", 0, 1, out)
    import time

    assert abs(host.u64(out) - time.time_ns()) < 10_000_000_000
    host.ok("clock_time_get", 1, 1, out)
    first = host.u64(out)
    host.ok("clock_time_get", 1, 1, out)
    assert host.u64(out) >= first
    assert host("clock_time_get", 4, 1, out) == Errno.inval
    assert host("clock_res_get", 9, out) == Errno.inval


def test_random(host: Host) -> None:
    a, b = host.alloc(32), host.alloc(32)
    host.ok("random_get", a, 32)
    host.ok("random_get", b, 32)
    assert host.mem.read(a, 32) != host.mem.read(b, 32)
    assert host("random_get", SIZE - 4, 64) == Errno.fault


def test_proc_exit_raises_and_the_code_survives_the_mask(host: Host) -> None:
    with pytest.raises(WasiExit) as caught:
        host.calls["proc_exit"](7)
    assert caught.value.code == 7
    with pytest.raises(WasiExit) as caught:
        host.calls["proc_exit"](-1)  # an i32 can arrive signed
    assert caught.value.code == 0xFFFFFFFF


def test_sched_yield_and_proc_raise(host: Host) -> None:
    host.ok("sched_yield")
    assert host("proc_raise", 15) == Errno.nosys
    assert host("proc_raise", 99) == Errno.inval


def sub_clock(userdata: int, timeout: int, abstime: bool = False, clock: int = 1) -> bytes:
    return struct.pack("<QB7x", userdata, 0) + struct.pack("<I4xQQH6x", clock, timeout, 0, int(abstime))


def sub_fd(userdata: int, kind: int, fd: int) -> bytes:
    return struct.pack("<QB7xI", userdata, kind, fd).ljust(48, b"\0")


def poll(host: Host, *subs: bytes) -> list[tuple[int, ...]]:
    src, out, n = host.put(b"".join(subs)), host.alloc(32 * len(subs)), host.alloc(4)
    host.ok("poll_oneoff", src, out, len(subs), n)
    return [wasi1.STRUCTS["event"].unpack(host.mem.read(out + 32 * i, 32)) for i in range(host.u32(n))]


def test_poll_a_clock_sleeps(host: Host) -> None:
    import time

    started = time.monotonic()
    events = poll(host, sub_clock(42, 30_000_000))
    assert time.monotonic() - started >= 0.025
    assert events == [(42, 0, 0, 0, 0)]


def test_poll_picks_the_nearest_clock(host: Host) -> None:
    events = poll(host, sub_clock(1, 5_000_000_000), sub_clock(2, 1_000_000))
    assert [e[0] for e in events] == [2]


def test_poll_an_absolute_deadline_in_the_past_is_ready_at_once(host: Host) -> None:
    assert [e[0] for e in poll(host, sub_clock(5, 0, abstime=True))] == [5]


def test_poll_a_ready_descriptor_does_not_wait(host: Host, root: Path) -> None:
    (root / "a").write_bytes(b"12345")
    _, fd = host.open("a")
    events = poll(host, sub_fd(8, 1, fd), sub_clock(9, 10_000_000_000))
    assert events == [(8, 0, 1, 5, 0)]  # fd_read, with the bytes left
    assert poll(host, sub_fd(3, 2, 1)) == [(3, 0, 2, 0, 0)]  # stdout is writable


def test_poll_errors_per_event(host: Host) -> None:
    assert poll(host, sub_fd(1, 1, 99))[0][1] == Errno.badf
    assert poll(host, sub_clock(1, 0, clock=9))[0][1] == Errno.inval
    assert host("poll_oneoff", host.put(b"x" * 48), host.alloc(32), 0, host.alloc(4)) == Errno.inval


# --- sockets, memory faults, the binding


def test_sockets_are_not_supported(host: Host) -> None:
    buf = host.alloc(16)
    assert host("sock_accept", 3, 0, buf) == Errno.notsock
    assert host("sock_shutdown", 1, 3) == Errno.notsock
    iovs, count = host.iov((buf, 4))
    assert host("sock_recv", 0, iovs, count, 0, buf, buf + 4) == Errno.notsock
    assert host("sock_send", 1, iovs, count, 0, buf) == Errno.notsock
    assert host("sock_recv", 99, iovs, count, 0, buf, buf + 4) == Errno.badf


def test_memory_out_of_bounds_is_a_fault(host: Host) -> None:
    assert host("args_sizes_get", SIZE - 2, 0) == Errno.fault
    assert host("fd_write", 1, SIZE - 4, 1, 0) == Errno.fault
    iovs, count = host.iov((SIZE - 2, 100))
    assert host("fd_write", 1, iovs, count, host.alloc(4)) == Errno.fault
    assert host("fd_read", 0, iovs, count, host.alloc(4)) == Errno.fault
    assert host("fd_write", 1, iovs, 1 << 31, 0) == Errno.fault  # a count that cannot fit


def test_arguments_arrive_unsigned(host: Host) -> None:
    """An i32 or i64 that the engine hands over as a negative number is the same bits."""
    out = host.alloc(8)
    host.ok("clock_time_get", 1 - (1 << 32) + (1 << 32), 1 - (1 << 64), out)  # precision: i64 -1 is 2^64 - 1
    assert host("fd_write", (1 << 32) - 4096 - 1, 0, 0, 0) == Errno.badf  # a huge, invalid fd, not a crash


def test_calls_before_the_memory_is_bound_say_so() -> None:
    wasi = Wasi()
    with pytest.raises(RuntimeError, match="memory"):
        wasi.imports()[wasi1.SNAPSHOT]["args_sizes_get"](0, 4)


def test_close_closes_the_files(host: Host, root: Path) -> None:
    (root / "a").write_text("x")
    _, fd = host.open("a")
    osfd = host.wasi._fds[fd].osfd  # pyright: ignore[reportPrivateUsage]
    host.wasi.close()
    with pytest.raises(OSError):
        os.fstat(osfd)
    assert host("fd_close", fd) == Errno.badf


def test_context_manager_closes(root: Path) -> None:
    with Wasi(preopens={"/": root}) as wasi:
        assert wasi.imports()
    assert not wasi._fds  # pyright: ignore[reportPrivateUsage]
