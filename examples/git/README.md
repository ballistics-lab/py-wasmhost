# wasmgit: git for Pythonista (and anywhere wasmhost runs)

A whole git client that runs in wasmhost, so also in Pythonista's `JSContext`, where there is no `git`, no processes,
and `dulwich` does not work (the owner, 2026-10). It is two WebAssembly modules and the Python package `wasmgit`, a WASI
host:

- `git-real.wasm` (3.4 MB): **git itself** (git 2.56), built to WASI (`real/build.sh`). Every command that needs
  neither the network nor a child process, with git's own options and output: `add`, `commit`, `log`, `diff`, `status`,
  `branch`, `checkout`, `merge`, `rebase`, `cherry-pick`, `reset`, `tag`, `rm`, `mv`, `show`, `config`, ...
- `git.wasm` (840 KB): [libgit2](https://libgit2.org) with a small command line of its own (`lg2.c`, `build.sh`), for the
  network only: `clone`, `fetch`, `push`. `pull` is a `fetch` by libgit2 and a `merge` (or `rebase`) by git.

They work on the same directory because they write the same repository format. WASI preview1 has no sockets and no
processes, which is why git alone cannot reach a server: libgit2 gets its connections from the host.

    wasmgit clone https://github.com/user/repo
    cd repo
    wasmgit status | add -A | commit -m "msg" | log --oneline | diff | merge feat | rebase main
    wasmgit pull | push [-u remote branch] | fetch

The command is `wasmgit` (also `python -m wasmgit`), not `git`: `git` is the real one, or StaSh's own, and an executable
named `git-NAME` on the PATH would become the subcommand `git NAME`.

## Install

This directory is a project (a `src` layout, like `uv init --package`): `pyproject.toml`, `setup.py` and `src/wasmgit/`.
Its wheel (`py3-none-any`, 1.7 MB, with both modules inside) is built by the workflow `wasmgit wheel` of this repository
and goes up as the artifact `wasmgit-wheel` of the run; it is not on PyPI. Install it with `pip install wasmgit-*.whl`
(it needs `wasmhost>=0.1.0`). `uv build --wheel` here builds it yourself, on Linux or macOS with `cmake`, `ninja` and `make`.

**From a ZIP (Pythonista), nothing installed:** download the repository as a ZIP from GitHub (*Code → Download ZIP*) and
unpack it. `examples/git/git.py` is a launcher: it finds `wasmgit` in `src/` and `wasmhost` in `../../src` of the
unpacked tree, and the two modules are beside it. Run it with arguments from a shell (StaSh, a terminal):
`python examples/git/git.py clone <url>`. Where there is no shell, only play, run it with the argument `-i` (or
`--interactive`): it gives a `git>` prompt (in `~/Documents/git`; `cd DIR`, `pwd`, `exit`, and any `git` command, with or
without the word `git`). Without arguments it only prints the usage.

Credentials for https remotes are read from the environment, as a token is the usual way in: `GIT_USERNAME` and
`GIT_TOKEN` (or `GIT_PASSWORD`). On GitHub, `GIT_USERNAME=<you> GIT_TOKEN=<personal access token>`. The committer is
`GIT_AUTHOR_NAME` and `GIT_AUTHOR_EMAIL`, else `git config --global user.name`/`user.email`. The global config is in
`$WASMGIT_HOME` (default `~/.wasmgit`), where the first run writes defaults (`main` as the first branch, no automatic gc).

## What is supported

Everything git does in one process, over http and https remotes. **Not supported:** ssh remotes, `stash` and everything
else that starts a child process (hooks, an editor, a pager, `gc`, `submodule`, `bisect run`), `clone --depth`,
`push --force`, a `git://` remote. See `real/README.md` for why, and for what a pure git would take.

## How it works

- **Which module.** `wasmgit` sends `clone`, `fetch`, `push` and the network half of `pull` to libgit2, and everything else to git.

- **Files.** WASI preview1 over a real directory (`wasmhost.wasi.preview1`). The root of the repository (found by going
  up from the current directory) is `/` to git (it starts in the right subdirectory with `-C`) and `/work` to libgit2,
  because libgit2 never looks for `.git` in the root of the path it walks up. Paths are relative to where the user is.
- **Network.** WASI preview1 has no sockets, so the module gets the connection from the host: four imports,
  `wasmhost_net.connect/read/write/close`. libgit2's own HTTP client runs on top of them, and the host does the TCP and,
  for https, the TLS with the system's certificates (`certifi` where the system has none) and the check of the
  certificate. There is no HTTP code of ours.
- **Processes.** There are none on WASI (and none in Pythonista), so libgit2's process and ssh-command code is replaced
  by stubs that fail (`stubs/process.c`).

## Licenses

The two modules are built from git (GPL-2.0) and libgit2 (GPL-2.0 with a linking exception), so they are not under this
repository's MIT license. See [`NOTICE`](NOTICE) for the licenses, the exact source versions and their checksums.

## Building `git.wasm`

`zig cc` is the C compiler for `wasm32-wasi` (it has wasi-libc inside, so no wasi-sdk), and the sources of libgit2 1.9.0
are the ones in the `libgit2-sys` crate on crates.io (no GitHub needed):

    uv pip install ziglang==0.17.0        # the version the committed modules were built with
    ZIG="python3 -m ziglang" examples/git/build.sh      # needs cmake, ninja, curl; writes examples/git/git.wasm

`build.sh` checks the SHA-256 of what it downloads (and gives a byte-identical module in any directory). It copies `stubs/process.c` and `stubs/socket.c` over the two files of libgit2 that need processes and sockets,
and links `lg2.c` and `compat.c` (the few POSIX calls, such as `getuid`, that wasi-libc lacks). The module imports only
`wasi_snapshot_preview1` and `wasmhost_net`, and uses no instructions beyond what `wasm3` runs.

## What has run

On the assistant's computer (`tests/test_git_example.py`, against a real `git http-backend`): clone, commit, push, a `pull` of
both sides that merges, `push -u`, Basic authentication, the TLS check, and merge, rebase, reset, tag, on `wasmtime`, `wasm3`,
`node`; on `bun` only the local test, because Bun 1.4.2 crashes with a segmentation fault in `git-real.wasm` on a
`git checkout` of another branch. **Not run: `jscontext` and `jsc`, on a device or on macOS**, so how fast a 3.4 MB module
compiles on an iPhone without JIT, and whether the TLS roots are found in Pythonista, are open. A large repository is slow
to clone in any case: libgit2 indexes the pack in WebAssembly.
