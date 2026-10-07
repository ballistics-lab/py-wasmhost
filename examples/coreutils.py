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
they see of the world is whatever this file hands them: a `wasmhost.wasi1.Wasi`, the WASI calls (files, arguments,
clock, exit) over a directory of the real file system, which is all they can reach, with the few lines of `Wasi()` below
around it. wasmhost runs the module; Python answers its calls.

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

The module is Rust's output, and the engine has to take what that uses: wasmtime, wasm3, and JavaScriptCore or Node of a
recent enough version do.

The first start compiles the module in the engine (some seconds, more on a phone); each command then runs its own
instance of it. `coreutils.wasm` is read from `examples/wasm/` next to this file, else from the cache
(`$WASMHOST_CACHE`, else `~/.cache/wasmhost`), downloaded once.
"""

import argparse
import codecs
import glob
import io
import os
import shlex
import sys
import tarfile
import urllib.request

import wasmhost
from wasmhost import wasi1

WASM_URL = "https://raw.githubusercontent.com/ballistics-lab/py-wasmhost/examples/zigcc/examples/wasm/coreutils.wasm"
LUA_URL = "https://registry.npmjs.org/@antonz/lua-wasi/-/lua-wasi-5.4.6.tgz"  # Lua 5.4.6 built to WASI, MIT
# The directory is preopened as "/" and as ".": a C library of the first WASI snapshot takes relative paths against ".".
PREOPENS = ("/", ".")
PROGRAMS = {"lua": ("lua.wasm", LUA_URL, "package/dist/lua.wasm")}  # beside coreutils: name -> file, tarball, member
PIPE_LIMIT = 16 * 1024 * 1024


def Wasi(root, argv, stdin=b"", stdout=None, stderr=None, env=None, pipe_limit=None):  # noqa: N802 -- it was a class
    """The WASI host of one program run: `wasmhost.wasi1.Wasi` over the directory ROOT, which the program sees as
    `/` (and as `.`, for a C library of the first snapshot). STDIN is bytes; STDOUT and STDERR are callables that take
    bytes; PIPE_LIMIT is how many bytes the program may write to STDOUT before it gets a broken pipe (that is how
    `yes | head -3` ends)."""
    write_out = stdout or (lambda data: None)
    written = 0

    def out(data):
        nonlocal written
        if pipe_limit is not None and written + len(data) > pipe_limit:
            raise BrokenPipeError  # the host's answer to the program is EPIPE
        written += len(data)
        write_out(data)

    return wasi1.Wasi(
        argv, env, {name: root for name in PREOPENS}, stdin, out, stderr or (lambda data: None)
    )  # fmt: skip


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
