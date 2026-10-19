"""A git client for Pythonista and anywhere wasmhost runs: git itself, plus libgit2 for the network, as WebAssembly.

    wasmgit clone https://github.com/user/repo        (or: python -m wasmgit clone ...)
    wasmgit status | add -A | commit -m msg | log | diff | merge | rebase | tag | reset | ...
    wasmgit pull | push | fetch
    wasmgit -i                                         # a `git>` prompt, for a place with no shell

Two modules share the repository on disk (they write the same format):

- `git-real.wasm`, git 2.56 itself (real/build.sh): every command that does not need the network or a child process,
  with git's own options and output: add, commit, log, diff, status, branch, checkout, merge, rebase, cherry-pick,
  reset, tag, rm, mv, show, config, and the rest.
- `git.wasm`, libgit2 with a small command line (build.sh): `clone`, `fetch` and `push`, and the `fetch` of `pull`.
  WASI preview1 has no sockets, so the module asks this host for the connection (`wasmhost_net`) and libgit2's own HTTP
  client talks over it, with TLS done here by `ssl`.

The root of the repository is found by going up from the current directory. Credentials for https: GIT_USERNAME and
GIT_PASSWORD, or GIT_TOKEN (a token, used as the password). `git config --global` is kept in $WASMGIT_HOME (default
~/.wasmgit), where a first run writes defaults (main as the first branch, no automatic gc). Not supported: ssh remotes,
`stash`, hooks, an editor, a pager (anything that starts a process), `clone --depth`, `push --force`.
"""

import os
import shlex
import socket
import ssl
import sys
import urllib.request

import wasmhost
from wasmhost.wasi import preview1

BASE_URL = "https://raw.githubusercontent.com/ballistics-lab/py-wasmhost/main/examples/git/"
NETWORK = ("clone", "fetch", "push")  # the commands that libgit2 runs; the rest is git
PASSED_ENV = ("GIT_USERNAME", "GIT_PASSWORD", "GIT_TOKEN", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL")
TIMEOUT = 60
MOUNT = "/work"  # where libgit2 sees the root of the repository
HOME = "/home"  # where both see their home, for `git config --global`
DEFAULT_CONFIG = "[init]\n\tdefaultBranch = main\n[gc]\n\tauto = 0\n[maintenance]\n\tauto = false\n"


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


def find_wasm(name):
    """NAME ("git.wasm", "git-real.wasm"): in the package (a wheel), else in examples/git/ of a source tree, else in
    the cache, downloaded once."""
    here = os.path.dirname(os.path.abspath(__file__))
    for folder in (here, os.path.join(here, "..", "..")):
        found = os.path.join(folder, name)
        if os.path.exists(found):
            return found
    cached = os.path.join(cache_root(), "git", name)
    if not os.path.exists(cached):
        print(f"downloading {BASE_URL + name}", file=sys.stderr, flush=True)
        os.makedirs(os.path.dirname(cached), exist_ok=True)
        with urllib.request.urlopen(BASE_URL + name, timeout=300) as resp:  # noqa: S310
            data = resp.read()
        with open(cached + ".part", "wb") as f:  # so that an interrupted download is not taken for a whole file
            f.write(data)
        os.replace(cached + ".part", cached)
    return cached


def home_directory():
    """The directory for `git config --global`, with defaults in it on the first run."""
    home = os.environ.get("WASMGIT_HOME") or os.path.join(os.path.dirname(cache_root()) or ".", ".wasmgit")
    os.makedirs(home, exist_ok=True)
    config = os.path.join(home, ".gitconfig")
    if not os.path.exists(config):
        with open(config, "w") as f:
            f.write(DEFAULT_CONFIG)
    return home


def tls_context():
    """The default context; certifi's roots where the system has none (Pythonista)."""
    context = ssl.create_default_context()
    if not context.get_ca_certs():
        try:
            import certifi  # pyright: ignore[reportMissingImports] -- optional

            context.load_verify_locations(certifi.where())
        except ImportError:
            pass
    return context


def repository_root(start):
    """(root, path inside it): the nearest directory up from START that has `.git`, else START itself."""
    here = start
    while True:
        if os.path.exists(os.path.join(here, ".git")):
            return here, os.path.relpath(start, here)
        parent = os.path.dirname(here)
        if parent == here:
            return start, "."
        here = parent


class Network:
    """`wasmhost_net`: the connections of the module, by handle. A negative answer is an error."""

    def __init__(self, wasi):
        self.wasi = wasi
        self.sockets = {}
        self.next_handle = 1
        self.context = None

    def connect(self, host_ptr, host_len, port_ptr, port_len, tls):
        host = self.wasi.memory.read(host_ptr, host_len).decode()
        port = int(self.wasi.memory.read(port_ptr, port_len).decode())
        try:
            sock = socket.create_connection((host, port), timeout=TIMEOUT)
            if tls:
                self.context = self.context or tls_context()
                sock = self.context.wrap_socket(sock, server_hostname=host)
        except OSError as exc:
            print(f"git: cannot connect to {host}:{port}: {exc}", file=sys.stderr)
            return -1
        handle, self.next_handle = self.next_handle, self.next_handle + 1
        self.sockets[handle] = sock
        return handle

    def read(self, handle, ptr, length):
        try:
            data = self.sockets[handle].recv(length)
        except (OSError, KeyError):
            return -1
        self.wasi.memory.write(ptr, data)
        return len(data)

    def write(self, handle, ptr, length):
        try:
            sock = self.sockets[handle]
            sock.sendall(self.wasi.memory.read(ptr, length))
        except (OSError, KeyError):
            return -1
        return length

    def close(self, handle):
        sock = self.sockets.pop(handle, None)
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        return 0

    def imports(self):
        return {"wasmhost_net": {name: getattr(self, name) for name in ("connect", "read", "write", "close")}}


def identity(env):
    """The committer is the author unless the environment says otherwise."""
    for who in ("NAME", "EMAIL"):
        if f"GIT_AUTHOR_{who}" in env:
            env.setdefault(f"GIT_COMMITTER_{who}", env[f"GIT_AUTHOR_{who}"])


def run_libgit2(argv, root, inside, wasm=None, **options):
    """`git ARGV` by libgit2 (the network commands), with ROOT as /work and INSIDE the way in it from ROOT."""
    # Not "/": libgit2 never looks for .git in the root directory of the path it walks up.
    env = {"HOME": HOME, "LG2_CWD": MOUNT if inside == "." else f"{MOUNT}/{inside}"}
    env.update({name: os.environ[name] for name in PASSED_ENV if name in os.environ})
    identity(env)
    wasi = preview1.Preview1(["git", *argv], env, {MOUNT: root, HOME: home_directory()})
    network = Network(wasi)
    with open(wasm or find_wasm("git.wasm"), "rb") as f:
        module = wasmhost.Module(f.read(), **options)
    instance = wasmhost.Instance(module, {**wasi.imports(), **network.imports()})
    try:
        return wasi.start(instance)
    finally:
        for handle in list(network.sockets):
            network.close(handle)
        wasi.close()


def run_git(argv, root, inside, wasm=None, out=None, **options):
    """`git ARGV` by git itself, ROOT being / (WASI starts in /, so `-C` goes to where the user is)."""
    env = {
        "HOME": HOME,
        "GIT_EXEC_PATH": "/.exec",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TEMPLATE_DIR": "",
        "GIT_MERGE_AUTOEDIT": "no",
    }
    env.update({name: os.environ[name] for name in PASSED_ENV if name in os.environ})
    identity(env)
    here = "/" if inside == "." else f"/{inside}"
    args = ["git", "-C", here, "--no-pager", *argv]
    wasi = preview1.Preview1(args, env, {"/": root, HOME: home_directory()}, stdout=out)
    with open(wasm or find_wasm("git-real.wasm"), "rb") as f:
        module = wasmhost.Module(f.read(), **options)
    try:
        return wasi.run(module)
    finally:
        wasi.close()


def split_pull(args):
    """(options, remote, branch) of `git pull [options] [remote [branch]]`."""
    options = [a for a in args if a.startswith("-")]
    names = [a for a in args if not a.startswith("-")]
    return options, (names[0] if names else None), (names[1] if len(names) > 1 else None)


def run(argv, cwd=None, wasm=None, real_wasm=None, **options):
    """Run `git ARGV` in CWD (default: the current directory); the exit code."""
    argv = list(argv)
    start = os.path.abspath(cwd or os.getcwd())
    while argv[:1] == ["-C"] and len(argv) > 2:  # `git -C dir ...`
        start = os.path.abspath(os.path.join(start, argv[1]))
        argv = argv[2:]
    root, inside = repository_root(start)
    inside = "." if inside == "." else inside.replace(os.sep, "/")
    args = {"wasm": wasm, **options}
    git = {"wasm": real_wasm, **options}
    command = next((a for a in argv if not a.startswith("-")), None)
    if command in NETWORK:
        rest = argv[argv.index(command) + 1 :]
        pushing = command == "push"
        upstream = [a for a in rest if a in ("-u", "--set-upstream")] if pushing else []
        if pushing and any(a in ("-f", "--force", "--force-with-lease") for a in rest):
            print("git push: forcing is not supported", file=sys.stderr)
            return 1
        names = [a for a in rest if not a.startswith("-")] if pushing else rest
        code = run_libgit2([command, *names], root, inside, **args)
        if code == 0 and upstream:
            remote, branch = (names + [None, None])[:2]
            if branch is None:  # the current branch
                shown = []
                run_git(["symbolic-ref", "--short", "HEAD"], root, inside, out=shown.append, **git)
                branch = b"".join(shown).decode().strip()
            code = run_git(["branch", "--set-upstream-to", f"{remote or 'origin'}/{branch}"], root, inside, **git)
        return code
    if command == "pull":
        flags, remote, branch = split_pull(argv[argv.index("pull") + 1 :])
        code = run_libgit2(["fetch", *([remote] if remote else [])], root, inside, **args)
        if code:
            return code
        target = f"{remote or 'origin'}/{branch}" if branch else "@{u}"
        verb = "rebase" if "--rebase" in flags else "merge"
        keep = [f for f in flags if f != "--rebase" and (verb == "merge" or f in ("--autostash",))]
        return run_git([verb, *keep, target], root, inside, **git)
    return run_git(argv, root, inside, **git)


USAGE = """usage: wasmgit <git command and arguments>     e.g. wasmgit clone https://github.com/user/repo
       wasmgit -i                              a prompt that takes git commands (for Pythonista's play button)
Credentials: GIT_USERNAME and GIT_TOKEN. See the README of the package."""


def prompt():
    """`-i`: a prompt that takes `git` commands, `cd`, `pwd` and `exit`."""
    documents = os.path.expanduser("~/Documents")
    here = os.path.join(documents, "git") if os.path.isdir(documents) else os.getcwd()
    os.makedirs(here, exist_ok=True)
    print(f"git in {here}; `cd DIR`, `pwd`, `exit`; try: clone https://github.com/user/repo")
    while True:
        try:
            line = input(f"git {os.path.basename(here)}> ").strip()
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            continue
        words = shlex.split(line)
        if words[:1] in (["exit"], ["quit"]):
            return 0
        if words[:1] == ["pwd"]:
            print(here)
        elif words[:1] == ["cd"]:
            target = os.path.abspath(os.path.join(here, os.path.expanduser(words[1] if len(words) > 1 else "~")))
            if os.path.isdir(target):
                here = target
            else:
                print(f"cd: {target}: no such directory")
        elif words:
            if words[0] == "git":
                words = words[1:]
            try:
                run(words, cwd=here)
            except Exception as exc:  # noqa: BLE001 -- keep the prompt alive
                print(f"git: {exc}")


def main(argv):
    if argv[:1] in (["-i"], ["--interactive"]):
        return prompt()
    if not argv:
        print(USAGE, file=sys.stderr)
        return 2
    return run(argv)
