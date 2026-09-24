"""Experiment: run a JSLinux (TinyEMU, emscripten) VM in a bare JavaScript engine, no browser.

    python examples/jslinux.py [DIR] [--cpu x86|riscv64|riscv32] [--cfg root-x86.cfg] [--mem 128]

DIR is a JSLinux tarball directory (bellard.org/tinyemu: jslinux-2019-12-21.tar.gz, MIT) holding
`<cpu>emu-wasm.js/.wasm`, the .cfg, kernel and disk blocks; without it the tarball is downloaded once into
the cache (`$WASMHOST_CACHE`, else `~/.cache/wasmhost`, or `./.cache` where there is no usable
home: PythonIDE). Only the console is wired: the emulator's
`term` is a shim writing to stdout, stdin goes to `console_queue_char`, and XMLHttpRequest is a shim
that Python answers from DIR. Timers are pumped from here.

It needs a JavaScript engine (a `wasmhost` backend that has `evaluate`: JSContext, gi-jsc or Node), because an
Emscripten build brings its own JavaScript; the WebAssembly API of wasmhost can't take its imports yet.
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import queue
import select
import socket
import ssl
import struct
import sys
import sysconfig
import tarfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import wasmhost

# What Emscripten's shell branch and this file need from a bare engine: a console, timers (driven from Python,
# which calls `__runTimers()` while it waits) and a fast hex decoder for the wasm bytes handed over by Python.
PRELUDE = r"""
globalThis.__log = [];
globalThis.console = {};
for (const k of ['log', 'info', 'warn', 'error', 'debug'])
    console[k] = (...a) => __log.push(a.map(String).join(' '));

globalThis.__timers = new Map();
globalThis.__timerId = 0;
globalThis.setTimeout = (fn, ms = 0, ...args) => {
    const id = ++__timerId;
    __timers.set(id, { at: Date.now() + ms, fn, args });
    return id;
};
globalThis.clearTimeout = id => __timers.delete(id);
globalThis.__runTimers = () => {
    const now = Date.now();
    for (const [id, t] of [...__timers])
        if (t.at <= now) { __timers.delete(id); t.fn(...t.args); }
    return __timers.size;
};

const UNHEX = new Uint8Array(128);
for (let i = 0; i < 10; i++) UNHEX[48 + i] = i;
for (let i = 0; i < 6; i++) { UNHEX[97 + i] = 10 + i; UNHEX[65 + i] = 10 + i; }
globalThis.__hexToBytes = hex => {
    const out = new Uint8Array(hex.length >> 1);
    for (let i = 0; i < out.length; i++)
        out[i] = (UNHEX[hex.charCodeAt(2 * i)] << 4) | UNHEX[hex.charCodeAt(2 * i + 1)];
    return out;
};
"""


def cache_dir() -> str:
    """Where downloads are kept: $WASMHOST_CACHE; else ~/.cache/wasmhost; else ./.cache where there is no usable home
    directory (PythonIDE), so relative to where the script is run."""
    if env := os.environ.get("WASMHOST_CACHE"):
        return env
    try:
        home = os.path.expanduser("~")
        if home != "~" and os.path.isdir(home) and os.access(home, os.W_OK):
            return os.path.join(home, ".cache", "wasmhost")
    except Exception:  # noqa: BLE001 -- a sandbox that won't even say
        pass
    return os.path.join(".", ".cache")


def on_ios() -> bool:
    """iOS Python apps (Pythonista, PythonIDE): consoles that look like a pipe but are interactive."""
    return (
        sys.platform == "ios"
        or "ios" in sysconfig.get_platform()
        or importlib.util.find_spec("objc_util") is not None  # Pythonista's own module
    )


TARBALL = "https://bellard.org/tinyemu/jslinux-2019-12-21.tar.gz"


NET_URL = "wss://relay.widgetry.org/"


class WebSocket:
    """A minimal RFC 6455 client (binary and text messages, ping/pong), stdlib only.

    The emulator's `eth0` is a VPN: every Ethernet frame is one binary message to a relay that does the NAT.
    """

    def __init__(self, url: str) -> None:
        u = urllib.parse.urlsplit(url)
        secure = u.scheme == "wss"
        host, port = u.hostname or "", u.port or (443 if secure else 80)
        sock = socket.create_connection((host, port), timeout=15)
        self.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host) if secure else sock
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            (
                f"GET {u.path or '/'} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nOrigin: https://bellard.org\r\n\r\n"
            ).encode()
        )
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("relay closed the connection during the handshake")
            buf += chunk
        head, _, self.buf = buf.partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(head.split(b"\r\n", 1)[0].decode(errors="replace"))
        self.sock.setblocking(False)

    def fileno(self) -> int:
        return self.sock.fileno()

    def send(self, payload: bytes, opcode: int = 2) -> None:
        n = len(payload)
        head = bytes([0x80 | opcode])
        head += (
            bytes([0x80 | n])
            if n < 126
            else b"\xfe" + struct.pack(">H", n)
            if n < 65536
            else b"\xff" + struct.pack(">Q", n)
        )
        mask = os.urandom(4)
        body = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
        self.sock.setblocking(True)
        try:
            self.sock.sendall(head + mask + body)
        finally:
            self.sock.setblocking(False)

    def recv(self) -> list[tuple[int, bytes]] | None:
        """Whatever whole messages have arrived as (opcode, payload); None once the connection is closed."""
        try:
            chunk = self.sock.recv(65536)
        except (BlockingIOError, ssl.SSLWantReadError):
            chunk = None
        except OSError:
            return None
        if chunk == b"":
            return None
        self.buf += chunk or b""
        out: list[tuple[int, bytes]] = []
        while len(self.buf) >= 2:
            opcode, n = self.buf[0] & 0x0F, self.buf[1] & 0x7F
            pos = 2
            if n == 126 and len(self.buf) >= 4:
                n, pos = struct.unpack(">H", self.buf[2:4])[0], 4
            elif n == 127 and len(self.buf) >= 10:
                n, pos = struct.unpack(">Q", self.buf[2:10])[0], 10
            elif n >= 126:
                break
            if len(self.buf) < pos + n:
                break
            payload, self.buf = self.buf[pos : pos + n], self.buf[pos + n :]
            if opcode == 8:
                return None
            if opcode == 9:
                self.send(payload, 10)
            elif opcode in (1, 2):
                out.append((opcode, payload))
        return out


ONLINE = "https://bellard.org/jslinux/"


def online_dir() -> Path:
    """Where files of the current (closed-source) x86_64 build are cached as they are requested."""
    dest = Path(cache_dir()) / "jslinux-online"
    dest.mkdir(parents=True, exist_ok=True)
    return dest


VERBOSE = False


def isatty(stream: object) -> bool:
    """`stream.isatty()`, for the console wrappers (StaSh's) that don't have it."""
    try:
        return bool(stream.isatty())  # type: ignore[attr-defined]
    except (AttributeError, ValueError, OSError):
        return False


def terminal_size() -> tuple[int, int]:
    try:
        size = os.get_terminal_size()
    except (AttributeError, ValueError, OSError):
        return 80, 25
    return size.columns, size.lines


def log(msg: str) -> None:
    if VERBOSE:  # the terminal is raw while the VM runs, hence \r\n
        sys.stderr.write(msg + "\r\n")


def download(url: str, path: Path) -> bool:
    if not path.is_file():
        log(f"[get {url}]")
        try:
            with urllib.request.urlopen(url) as resp:
                data = resp.read()
        except OSError as exc:
            log(f"[{exc}: {url}]")
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return True


def fetch() -> Path:
    dest = Path(cache_dir()) / "jslinux-2019-12-21"
    if not dest.is_dir():
        print(f"[downloading {TARBALL}]", file=sys.stderr)
        with urllib.request.urlopen(TARBALL) as resp, tarfile.open(fileobj=resp, mode="r|gz") as tar:
            if hasattr(tarfile, "data_filter"):
                tar.extractall(dest.parent, filter="data")
            else:  # older Python (Pythonista): no filter argument, so refuse paths that leave the directory
                for member in tar:
                    target = (dest.parent / member.name).resolve()
                    if not target.is_relative_to(dest.parent.resolve()) or not (member.isfile() or member.isdir()):
                        raise RuntimeError(f"unexpected tar member {member.name!r}")
                    tar.extract(member, dest.parent)
    return dest


# The emulator only fetches an absolute URL through XMLHttpRequest (a relative one goes to a load_file()
# that aborts), so files live under a made-up origin and Python maps it back onto DIR.
BASE = "http://vm.invalid/"

SHIMS = r"""
globalThis.__out = [];
globalThis.dateNow = () => Date.now();
globalThis.performance = { now: () => Date.now() };   // Emscripten's shell branch looks for it
globalThis.term = {
    write(s) { __out.push(s); },
    getSize() { return [__cols, __rows]; },
};
globalThis.print = s => __out.push('[print] ' + s + '\n');
globalThis.update_downloading = () => {};   // jslinux.js shows a progress bar here
globalThis.__netOut = [];
globalThis.net_state = {
    recv_packet(buf) { __netOut.push(Array.from(buf, b => (b + 256).toString(16).slice(1)).join('')); }
};
globalThis.__netTake = () => JSON.stringify(__netOut.splice(0));
globalThis.__netIn = hex => {
    const b = __hexToBytes(hex), addr = _malloc(b.length);
    HEAPU8.set(b, addr);
    Module.ccall('net_write_packet', null, ['number', 'number'], [addr, b.length]);
    _free(addr);
};
globalThis.__carrier = up => Module.ccall('net_set_carrier', null, ['number'], [up]);
globalThis.__reqs = [];
globalThis.__pending = new Map();
globalThis.__reqId = 0;
globalThis.XMLHttpRequest = class {
    open(method, url) { this.method = method; this.url = url; this.status = 0; }
    setRequestHeader() {}
    send() {
        this.id = ++__reqId;
        __pending.set(this.id, this);
        __reqs.push({ id: this.id, url: this.url });
    }
};
globalThis.__takeReqs = () => JSON.stringify(__reqs.splice(0));
globalThis.__respond = (id, hex, status) => {
    const x = __pending.get(id);
    __pending.delete(id);
    x.status = status;
    x.response = __hexToBytes(hex).buffer;
    if (status == 200) x.onload && x.onload({}); else x.onerror && x.onerror({});
};
globalThis.__drainOut = () => __out.splice(0).join('');
"""

START = r"""
var Module = {
    instantiateWasm(imports, done) {
        const bytes = __hexToBytes(__wasmHex);
        delete globalThis.__wasmHex;
        const instance = new WebAssembly.Instance(new WebAssembly.Module(bytes), imports);
        done(instance);
        return instance.exports;   // these old loaders take the exports from the return value
    },
    print: s => __out.push(s + '\n'),
    printErr: s => __out.push('[err] ' + s + '\n'),
    noExitRuntime: true,
    onAbort: () => __out.push('[abort] ' + new Error().stack + '\n'),
    preRun: [() => {
        globalThis.console_write1 = Module.cwrap('console_queue_char', null, ['number']);
        Module.ccall('vm_start', null,
            ['string', 'number', 'string', 'string', 'number', 'number', 'number', 'string'],
            [__cfg, __mem, '', '', 0, 0, __net, '']);
    }],
};
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir", nargs="?", type=Path)
    ap.add_argument("--cpu", default="x86", choices=["x86", "x86_64", "riscv64", "riscv32"])
    ap.add_argument("--cfg")
    ap.add_argument("--mem", type=int, default=128)
    ap.add_argument("--net-url", default=NET_URL, help="WebSocket relay for eth0 (default: %(default)s)")
    ap.add_argument("--no-net", action="store_true", help="leave the network card unplugged")
    ap.add_argument("-v", "--verbose", action="store_true", help="log every file fetched")
    args = ap.parse_args()
    global VERBOSE
    VERBOSE = args.verbose

    root: Path = args.dir or fetch()
    cfg = args.cfg or {"riscv64": "root-riscv64.cfg", "x86_64": "alpine-x86_64.cfg"}.get(args.cpu, "root-x86.cfg")
    emu = {"x86": "x86emu", "x86_64": "x86_64emu", "riscv64": "riscvemu64", "riscv32": "riscvemu32"}[args.cpu]
    if args.cpu == "x86_64":
        root = online_dir()

    net = not args.no_net
    host = wasmhost.default_backend(js_only=True)
    if not isinstance(host, wasmhost.JSBackend):  # can't happen with js_only; narrows the type
        raise TypeError(f"{host.name} is not a JavaScript engine")
    print(f"[backend: {host.name}]", file=sys.stderr)
    host.evaluate(PRELUDE)  # bare (no `window`), so Emscripten picks its shell branch
    cols, rows = terminal_size() if isatty(sys.stdout) else (80, 25)
    host.evaluate(f"globalThis.__cols = {cols}; globalThis.__rows = {rows};")
    host.evaluate(SHIMS)
    if args.cpu == "x86_64":
        for name in (f"{emu}-wasm.wasm", f"{emu}-wasm.js"):
            download(ONLINE + name, root / name)
    wasm = (root / f"{emu}-wasm.wasm").read_bytes().hex()
    host.evaluate(
        f'globalThis.__wasmHex = "{wasm}"; globalThis.__cfg = {json.dumps(BASE + cfg)}; '
        f"globalThis.__mem = {args.mem}; globalThis.__net = {int(net)};"
    )
    host.evaluate(START)
    host.evaluate((root / f"{emu}-wasm.js").read_text())

    ws: WebSocket | None = None
    if net:
        try:
            ws = WebSocket(args.net_url)
            print(f"[network: connected to {args.net_url}]", file=sys.stderr)
        except OSError as exc:
            print(f"[no network: {exc}]", file=sys.stderr)
    carrier = False  # plugged in once the machine exists, i.e. once the guest first prints something

    # iOS consoles are interactive but not ttys, and select() can't wait on them: a thread reads a line at a
    # time (Enter sends it to the guest) and `~.` on a line of its own leaves.
    lines: queue.Queue[str] | None = None
    if on_ios():
        lines = queue.Queue()

        def read_lines() -> None:
            while True:
                try:
                    lines.put(input() + "\n")  # type: ignore[union-attr]
                except EOFError:
                    lines.put("~.\n")
                    return

        threading.Thread(target=read_lines, daemon=True).start()
        print("[type ~. on a line of its own to leave]", file=sys.stderr)

    tty = None
    if lines is None and isatty(sys.stdin):
        import termios
        import tty as ttymod

        tty = termios.tcgetattr(sys.stdin)
        ttymod.setraw(sys.stdin.fileno())
    try:
        while True:
            host.evaluate("__runTimers()")
            for req in json.loads(host.evaluate("__takeReqs()")):
                url: str = req["url"]
                if "://" not in url or url.startswith(BASE):  # a file of the image (blocks are relative to the cfg)
                    rel = url.removeprefix(BASE)
                    path = root / rel
                    ok = download(ONLINE + rel, path) if args.cpu == "x86_64" else path.is_file()
                else:  # a real URL, e.g. the root filesystem of Alpine: fetched once, then cached
                    path = root / "ext" / url.split("?")[0].split("://", 1)[-1]
                    ok = download(url, path)  # the query is only a cache buster: a snapshot of the image is kept
                if ok:
                    host.evaluate(f"__respond({req['id']}, '{path.read_bytes().hex()}', 200)")
                else:
                    log(f"[404 {req['url']}]")
                    host.evaluate(f"__respond({req['id']}, '', 404)")
            out = host.evaluate("__drainOut()")
            if out:
                sys.stdout.write(out)
                getattr(sys.stdout, "flush", lambda: None)()
                if ws and not carrier:
                    host.evaluate("__carrier(1)")
                    carrier = True
            if ws:
                for hexframe in json.loads(host.evaluate("__netTake()")):
                    frame = bytes.fromhex(hexframe)
                    log(f"[net -> relay {len(frame)} bytes]")
                    ws.send(frame)
            watched = [*([sys.stdin] if lines is None else []), *([ws] if ws else [])]
            ready = select.select(watched, [], [], 0.005)[0] if watched else []
            if not watched:
                time.sleep(0.005)
            if ws and ws in ready:
                msgs = ws.recv()
                if msgs is None:
                    print("[network: the relay closed the connection]", file=sys.stderr)
                    host.evaluate("__carrier(0)")
                    ws = None
                else:
                    for opcode, payload in msgs:
                        log(f"[net <- relay {len(payload)} bytes, opcode {opcode}]")
                        if opcode == 2:
                            host.evaluate(f"__netIn('{payload.hex()}')")
                        elif payload.startswith(b"ping:"):
                            ws.send(b"pong:" + payload[5:], 1)
            while lines is not None and not lines.empty():
                line = lines.get()
                if line == "~.\n":
                    return
                host.evaluate("".join(f"console_write1({b});" for b in line.encode()))
            if sys.stdin in ready:
                data = os.read(sys.stdin.fileno(), 1024)
                if not data:
                    break
                if data == b"\x1d":  # Ctrl-]
                    break
                host.evaluate("".join(f"console_write1({b});" for b in data))
            time.sleep(0.001)
    finally:
        if tty is not None:
            import termios

            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, tty)
        host.close()


if __name__ == "__main__":
    main()
