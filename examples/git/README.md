# git for Pythonista (and anywhere wasmhost runs)

A git client that runs in wasmhost, so also in Pythonista's `JSContext`, where there is no `git`, no processes, and `dulwich` does not
work (the owner, 2026-10). It is [libgit2](https://libgit2.org) built to
WebAssembly (`git.wasm`, 840 KB) with a small command line of its own (`lg2.c`), and `git.py`, a WASI host in Python.

    python examples/git/git.py clone https://github.com/user/repo
    cd repo
    python examples/git/git.py status | add -A | commit -m "msg" | log -n 5 | diff [--cached]
    python examples/git/git.py branch [name | -d name] | checkout <ref> | remote [add name url]
    python examples/git/git.py fetch | pull | push [remote [branch]]

**In StaSh:** copy `git.py` and `git.wasm` into StaSh's `bin` directory (or put them on `$PATH` of the shell). Then
`git clone ...`, `git status`, `git commit -m ...` are commands of the shell. If `git.wasm` is not beside `git.py`, it is
downloaded once into `$WASMHOST_CACHE` (else `~/.cache/wasmhost`). `wasmhost` itself must be importable (`pip install wasmhost`).

Credentials for https remotes are read from the environment, as a token is the usual way in: `GIT_USERNAME` and
`GIT_TOKEN` (or `GIT_PASSWORD`). On GitHub, `GIT_USERNAME=<you> GIT_TOKEN=<personal access token>`. The committer is
`GIT_AUTHOR_NAME` and `GIT_AUTHOR_EMAIL`, else `user.name` and `user.email` of the repository.

## What is supported

`init`, `clone`, `status`, `add`, `commit -m`, `log`, `diff`, `branch`, `checkout`, `remote`, `fetch`, `pull` (fast-forward
only), `push`. Over http and https. Not supported: ssh remotes, merge and rebase, stash, submodules, hooks, `commit -a`,
`reset`, `tag`. They are not missing from the module for a reason: they are not written yet in `lg2.c`, and libgit2 has
most of them (merge, tag, reset, stash) so each is a few dozen lines.

## How it works

- **Files.** WASI preview1 over a real directory (`wasmhost.wasi.preview1`). The root of the repository (found by going
  up from the current directory) is `/work` to the module, and the module starts in the right subdirectory, so `git add f.txt`
  in `src/` means `src/f.txt`. It is not `/`, because libgit2 never looks for `.git` in the root of the path it walks up.
- **Network.** WASI preview1 has no sockets, so the module gets the connection from the host: four imports,
  `wasmhost_net.connect/read/write/close`. libgit2's own HTTP client runs on top of them, and the host does the TCP and,
  for https, the TLS with the system's certificates (`certifi` where the system has none) and the check of the
  certificate. There is no HTTP code of ours.
- **Processes.** There are none on WASI (and none in Pythonista), so libgit2's process and ssh-command code is replaced
  by stubs that fail (`stubs/process.c`).

## Building `git.wasm`

`zig cc` is the C compiler for `wasm32-wasi` (it has wasi-libc inside, so no wasi-sdk), and the sources of libgit2 1.9.0
are the ones in the `libgit2-sys` crate on crates.io (no GitHub needed):

    uv pip install ziglang
    ZIG="python3 -m ziglang" examples/git/build.sh      # needs cmake, ninja, curl; writes examples/git/git.wasm

`build.sh` copies `stubs/process.c` and `stubs/socket.c` over the two files of libgit2 that need processes and sockets,
and links `lg2.c` and `compat.c` (the few POSIX calls, such as `getuid`, that wasi-libc lacks). The module imports only
`wasi_snapshot_preview1` and `wasmhost_net`, and uses no instructions beyond what `wasm3` runs.

## What has run

On the assistant's computer (`tests/test_git_example.py`, against a real `git http-backend`): clone, commit, push
and pull over http with Basic authentication, and the TLS check, on `wasmtime`, `wasm3`, `node` and `bun`. **Not run:
`jscontext` and `jsc` on a device or on macOS**, so how fast it is on an iPhone without JIT, and whether the TLS roots
are found in Pythonista, are open. A large repository is slow to clone in any case: libgit2 indexes the pack in WebAssembly.
