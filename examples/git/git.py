"""A git client: libgit2 built to WebAssembly (git.wasm, see build.sh) run by wasmhost, with Python as its WASI host.

    python examples/git/git.py clone https://github.com/user/repo
    python examples/git/git.py status | add -A | commit -m msg | log -n 5 | diff [--cached]
    python examples/git/git.py branch [name | -d name] | checkout <ref> | remote [add name url]
    python examples/git/git.py fetch | pull | push [remote [branch]]

In Pythonista, copy this file and git.wasm to StaSh's bin directory (as `git.py`) and `git status` is a command of
the shell. Files: the module sees the root of the repository as `/work` (found by going up from the current
directory). The network: WASI preview1 has no sockets, so the module asks this host for the connection
(`wasmhost_net`) and libgit2's own HTTP client talks over it, with TLS done here by `ssl`. Credentials for https:
GIT_USERNAME and GIT_PASSWORD, or GIT_TOKEN (a token, used as the password). The committer: GIT_AUTHOR_NAME and
GIT_AUTHOR_EMAIL, else user.name and user.email of the repository's config. Not supported: ssh remotes, merge (pull
only fast-forwards), rebase, stash.
"""

import os
import socket
import ssl
import sys
import urllib.request

import wasmhost
from wasmhost.wasi import preview1

WASM_URL = "https://raw.githubusercontent.com/ballistics-lab/py-wasmhost/main/examples/git/git.wasm"
PASSED_ENV = ("GIT_USERNAME", "GIT_PASSWORD", "GIT_TOKEN", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL")
TIMEOUT = 60
MOUNT = "/work"  # where the module sees the root of the repository


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


def find_wasm():
    beside = os.path.join(os.path.dirname(os.path.abspath(__file__)), "git.wasm")
    if os.path.exists(beside):
        return beside
    cached = os.path.join(cache_root(), "git", "git.wasm")
    if not os.path.exists(cached):
        print(f"downloading {WASM_URL}", file=sys.stderr, flush=True)
        os.makedirs(os.path.dirname(cached), exist_ok=True)
        with urllib.request.urlopen(WASM_URL, timeout=120) as resp:  # noqa: S310
            data = resp.read()
        with open(cached + ".part", "wb") as f:  # so that an interrupted download is not taken for a whole file
            f.write(data)
        os.replace(cached + ".part", cached)
    return cached


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


def run(argv, cwd=None, wasm=None, **options):
    """Run `git ARGV` in CWD (default: the current directory); the exit code."""
    root, inside = repository_root(os.path.abspath(cwd or os.getcwd()))
    # Not "/": libgit2 never looks for .git in the root directory of the path it walks up.
    env = {"HOME": MOUNT, "LG2_CWD": MOUNT if inside == "." else f"{MOUNT}/{inside.replace(os.sep, '/')}"}
    env.update({name: os.environ[name] for name in PASSED_ENV if name in os.environ})
    wasi = preview1.Preview1(["git", *argv], env, {MOUNT: root})
    network = Network(wasi)
    with open(wasm or find_wasm(), "rb") as f:
        module = wasmhost.Module(f.read(), **options)
    instance = wasmhost.Instance(module, {**wasi.imports(), **network.imports()})
    try:
        return wasi.start(instance)
    finally:
        for handle in list(network.sockets):
            network.close(handle)
        wasi.close()


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
