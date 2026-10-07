"""Compile C and C++ to WebAssembly with clang that is itself WebAssembly, in wasmhost, then run the result.

    python examples/wasmclang.py [--backend NAME]

No compiler is installed and no process is started, so it works where nothing else does: Pythonista, PythonIDE. The
first run downloads clang, lld, memfs and the sysroot (about 60 MB) from the wasm-clang project of Ben Smith
(https://github.com/binji/wasm-clang, Apache-2.0) into the cache (`$WASMHOST_CACHE`, else `~/.cache/wasmhost`, or
`./.cache` where there is no usable home directory). Later runs download nothing.

How it fits together: `clang` and `lld` are WASI programs (LLVM 8, built to WebAssembly) that want files. `memfs` is a
small module of that project, a file system in memory, which answers their `wasi_unstable` calls. Python is the host in
between: it hands clang's file calls to memfs (which copies bytes in and out of clang's memory through `copy_in` and
`copy_out`, here host functions), and answers the few calls that are not about files (arguments, clock, exit). A host
function that calls another instance, and the Python half of a WASI host, is all it takes; this file is that host.

It builds three programs, two from C and one from C++, and runs them:

1. a freestanding module (no libc): exports `fib`, `sum` and `buffer`, imports `env.host_log`, which Python answers;
2. a WASI program with `printf`, linked against the sysroot's libc, run by the same host: its `fd_write` goes to memfs
   and its stdout comes out here;
3. a C++ program (`std::vector`, `std::map`, `std::sort`, `std::cout` from the sysroot's libc++), the same way. This
   clang has no C++ exceptions (`throw` and `try` do not link), and the sysroot has no compiler-rt, so a stub stands
   in for `__lttf2`, a `long double` comparison libc++ refers to and this program never calls.

The compiling takes a while, most of it the engine compiling clang and lld (31 MB and 19 MB of WebAssembly): some
seconds on a computer, a minute or more on a phone. It prints where it is.
"""

import argparse
import os
import struct
import sys
import tarfile
import time
import urllib.request

import wasmhost

BASE = "https://binji.github.io/wasm-clang/"
SYSROOT_URL = "https://raw.githubusercontent.com/binji/wasm-clang/master/sysroot.tar"
FILES = {"clang": BASE + "clang", "lld": BASE + "lld", "memfs": BASE + "memfs", "sysroot.tar": SYSROOT_URL}

CLANG_ARGS = [
    "-disable-free",
    "-isysroot", "/",
    "-internal-isystem", "/include",
    "-internal-isystem", "/lib/clang/8.0.1/include",
    "-ferror-limit", "19",
    "-fmessage-length", "80",
]  # fmt: skip

FREESTANDING_C = """
extern void host_log(int value);

int fib(int n) {
    int a = 0, b = 1;
    while (n-- > 0) { int t = a + b; a = b; b = t; }
    return a;
}

/* sum of `count` int32 values the host wrote at `p` in the module's memory */
int sum(const int *p, int count) {
    int total = 0;
    for (int i = 0; i < count; i++) total += p[i];
    host_log(total);
    return total;
}

static int storage[16];
int *buffer(void) { return storage; }
"""

HELLO_C = """
#include <stdio.h>

int main(int argc, char **argv) {
    printf("hello from C, compiled by clang running in wasmhost (argc=%d)\\n", argc);
    return 0;
}
"""

HELLO_CPP = """
#include <algorithm>
#include <iostream>
#include <map>
#include <string>
#include <vector>

int main() {
    std::vector<int> v = {5, 3, 1, 4, 2};
    std::sort(v.begin(), v.end());
    std::map<std::string, int> m = {{"one", 1}, {"two", 2}};
    std::cout << "hello from C++, compiled by clang running in wasmhost: ";
    for (int x : v) std::cout << x << ' ';
    std::cout << "| two=" << m["two"] << std::endl;
    return 0;
}
"""

# libc++ refers to __lttf2 (a long double comparison, from compiler-rt, which the sysroot lacks); nothing here calls it.
LONG_DOUBLE_STUB = "int __lttf2(long double a, long double b) { return 0; }\n"

WASI_LINK = ["-z", "stack-size=1048576", "-Llib/wasm32-wasi", "lib/wasm32-wasi/crt1.o"]


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


def fetch(directory):
    os.makedirs(directory, exist_ok=True)
    for name, url in FILES.items():
        path = os.path.join(directory, name)
        if os.path.exists(path):
            continue
        print(f"downloading {url}", flush=True)
        with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310
            data = resp.read()
        with open(path + ".part", "wb") as f:  # so that an interrupted download is not taken for a whole file
            f.write(data)
        os.replace(path + ".part", path)


class ProcExit(Exception):  # noqa: N818 -- WASI's proc_exit
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class Memory:
    """Little-endian words in a wasmhost Memory."""

    def __init__(self, memory):
        self.memory = memory

    def u32(self, offset):
        return struct.unpack("<I", self.memory.read(offset, 4))[0]

    def set_u32(self, offset, value):
        self.memory.write(offset, struct.pack("<I", value & 0xFFFFFFFF))

    def set_u64(self, offset, value):
        self.memory.write(offset, struct.pack("<Q", value & 0xFFFFFFFFFFFFFFFF))


class MemFS:
    """The project's file system in memory (memfs.wasm), as a WASI file system for the programs that run next to it."""

    def __init__(self, module, write):
        self.write = write  # takes a str: what a program writes to stdout or stderr
        self._host_memory = None  # the memory of the program that is running now
        imports = {
            "env": {
                "abort": self._abort,
                "host_write": self._host_write,
                "host_read": self._host_read,
                "memfs_log": self._log,
                "copy_in": self._copy_in,
                "copy_out": self._copy_out,
            }
        }
        self.instance = wasmhost.Instance(module, imports)
        self.exports = self.instance.exports
        self.memory = self.exports.memory
        self.exports.init()

    @property
    def host_memory(self):
        if self._host_memory is None:
            raise RuntimeError("no program is running")
        return self._host_memory

    @host_memory.setter
    def host_memory(self, memory):
        self._host_memory = memory

    def _abort(self):
        raise RuntimeError("memfs aborted")

    def _host_write(self, fd, iovs, iovs_len, nwritten_out):
        host = Memory(self.host_memory)
        chunks = []
        for _ in range(iovs_len):
            buf, size = host.u32(iovs), host.u32(iovs + 4)
            iovs += 8
            chunks.append(self.host_memory.read(buf, size))
        data = b"".join(chunks)
        host.set_u32(nwritten_out, len(data))
        self.write(data.decode("utf-8", "replace"))
        return 0

    def _host_read(self, fd, iovs, iovs_len, nread_out):
        Memory(self.host_memory).set_u32(nread_out, 0)  # stdin is empty
        return 0

    def _log(self, buf, size):
        self.write("[memfs] " + self.memory.read(buf, size).decode("latin-1") + "\n")

    def _copy_out(self, host_dst, memfs_src, size):
        self.host_memory.write(host_dst, self.memory.read(memfs_src, size))

    def _copy_in(self, memfs_dst, host_src, size):
        self.memory.write(memfs_dst, self.host_memory.read(host_src, size))

    def add_directory(self, path):
        raw = path.encode()
        self.memory.write(self.exports.GetPathBuf(), raw)
        self.exports.AddDirectoryNode(len(raw))

    def add_file(self, path, data):
        raw = path.encode()
        self.memory.write(self.exports.GetPathBuf(), raw)
        inode = self.exports.AddFileNode(len(raw), len(data))
        self.memory.write(self.exports.GetFileNodeAddress(inode), data)

    def read_file(self, path):
        raw = path.encode()
        self.memory.write(self.exports.GetPathBuf(), raw)
        inode = self.exports.FindNode(len(raw))
        return self.memory.read(self.exports.GetFileNodeAddress(inode), self.exports.GetFileNodeSize(inode))

    def add_tar(self, path):
        with tarfile.open(path) as tar:
            for member in tar:
                if member.isdir():
                    self.add_directory(member.name)
                elif (data := tar.extractfile(member)) is not None:  # a regular file
                    self.add_file(member.name, data.read())

    def run(self, module, *argv):
        """Run a WASI program (a module that imports `wasi_unstable`) with ARGV; its exit code."""
        box = {}

        def memory():
            return Memory(box["instance"].exports.memory)

        def proc_exit(code):
            raise ProcExit(code)

        def environ_sizes_get(count_out, size_out):
            memory().set_u64(count_out, 0)
            memory().set_u64(size_out, 0)
            return 0

        def environ_get(ptrs, buf):
            return 0

        def args_sizes_get(argc_out, size_out):
            memory().set_u64(argc_out, len(argv))
            memory().set_u64(size_out, sum(len(a.encode()) + 1 for a in argv))
            return 0

        def args_get(ptrs, buf):
            mem = memory()
            for arg in argv:
                raw = arg.encode() + b"\0"
                mem.set_u32(ptrs, buf)
                mem.memory.write(buf, raw)
                ptrs += 4
                buf += len(raw)
            mem.set_u32(ptrs, 0)
            return 0

        def random_get(buf, size):
            memory().memory.write(buf, os.urandom(size))
            return 0

        def clock_time_get(clock_id, precision, time_out):
            memory().set_u64(time_out, int(time.time() * 1e9))
            return 0

        def poll_oneoff(*_):
            return 52  # ENOSYS

        wasi = {
            "proc_exit": proc_exit,
            "environ_sizes_get": environ_sizes_get,
            "environ_get": environ_get,
            "args_sizes_get": args_sizes_get,
            "args_get": args_get,
            "random_get": random_get,
            "clock_time_get": clock_time_get,
            "poll_oneoff": poll_oneoff,
        }
        for imp in wasmhost.Module.imports(module):  # everything else is a file call: memfs has it
            if imp.module == "wasi_unstable" and imp.name not in wasi:
                wasi[imp.name] = getattr(self.exports, imp.name)
        box["instance"] = wasmhost.Instance(module, {"wasi_unstable": wasi, "env": {}})
        self.host_memory = box["instance"].exports.memory
        try:
            box["instance"].exports._start()
        except ProcExit as exit_:
            return exit_.code
        return 0


class Toolchain:
    def __init__(self, directory, backend=None, log=print):
        self.backend = backend
        self.log = log
        self.directory = directory
        self.modules = {}
        self.memfs = MemFS(self._module("memfs"), lambda text: print(text, end="", flush=True))
        self.memfs.add_tar(os.path.join(directory, "sysroot.tar"))

    def _module(self, name):
        if name not in self.modules:
            self.log(f"compiling {name} in the engine (the slow part)...")
            with open(os.path.join(self.directory, name), "rb") as f:
                self.modules[name] = wasmhost.Module(f.read(), backend=self.backend)
        return self.modules[name]

    def _run(self, name, *argv):
        code = self.memfs.run(self._module(name), *argv)
        if code != 0:
            raise RuntimeError(f"{argv[0]} exited with {code} (its messages are above)")

    def _clang(self, source, name, cpp):
        """Compile SOURCE (C, or C++ with libc++'s headers) to the object file NAME.o."""
        flags = list(CLANG_ARGS)
        if cpp:
            flags[flags.index("/include") : flags.index("/include")] = ["/include/c++/v1", "-internal-isystem"]
            flags += ["-std=c++14"]
        language = "c++" if cpp else "c"
        self.memfs.add_file(f"{name}.src", source.encode())
        self._run(
            "clang", "clang", "-cc1", "-emit-obj", *flags, "-O2", "-o", f"{name}.o", "-x", language, f"{name}.src"
        )

    def compile(self, source, link_args, cpp=False):
        """C (or C++) source -> the bytes of a wasm module. LINK_ARGS are wasm-ld's, besides the objects and output."""
        self.log("clang: C++ -> object" if cpp else "clang: C -> object")
        self._clang(source, "input", cpp)
        objects = ["input.o"]
        if cpp:
            self._clang(LONG_DOUBLE_STUB, "stub", cpp=False)
            objects.append("stub.o")
        self.log("wasm-ld: object -> wasm")
        self._run("lld", "wasm-ld", "--no-threads", *link_args, *objects, "-o", "output.wasm")
        return self.memfs.read_file("output.wasm")


def main():
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--backend", choices=sorted(wasmhost.BACKENDS), help="the engine (default: the first found)")
    args = parser.parse_args()

    directory = os.path.join(cache_root(), "wasm-clang")
    fetch(directory)
    start = time.monotonic()
    toolchain = Toolchain(
        directory, args.backend, log=lambda m: print(f"[{time.monotonic() - start:5.1f} s] {m}", flush=True)
    )

    # 1. Freestanding: no libc, so no WASI either; the module imports only what C declared `extern`.
    wasm = toolchain.compile(
        FREESTANDING_C, ["--no-entry", "--allow-undefined", "--export=fib", "--export=sum", "--export=buffer"]
    )
    print(f"built {len(wasm)} bytes of wasm from C")
    module = wasmhost.Module(wasm, backend=args.backend)
    print("exports:", [(e.name, e.kind) for e in wasmhost.Module.exports(module)])
    logged = []
    instance = wasmhost.Instance(module, {"env": {"host_log": logged.append}})
    print("fib(10) =", instance.exports.fib(10))
    values = [1, 2, 3, 4, 5]
    ptr = instance.exports.buffer()
    instance.exports.memory.write(ptr, b"".join(v.to_bytes(4, "little") for v in values))
    print(f"sum({values}) =", instance.exports.sum(ptr, len(values)), "| env.host_log got", logged)

    # 2. A WASI program: libc from the sysroot, `main`, printf. The same host runs it.
    wasm = toolchain.compile(HELLO_C, [*WASI_LINK, "-lc"])
    print(f"built {len(wasm)} bytes of wasm, a WASI program; running it:")
    code = toolchain.memfs.run(wasmhost.Module(wasm, backend=args.backend), "hello.wasm", "one", "two")
    print(f"exit code {code}")

    # 3. The same in C++: libc++ and its ABI library on top of libc.
    wasm = toolchain.compile(HELLO_CPP, [*WASI_LINK, "-lc", "-lc++", "-lc++abi"], cpp=True)
    print(f"built {len(wasm)} bytes of wasm from C++; running it:")
    code = toolchain.memfs.run(wasmhost.Module(wasm, backend=args.backend), "hello_cpp.wasm")
    print(f"exit code {code}")


if __name__ == "__main__":
    sys.exit(main())
