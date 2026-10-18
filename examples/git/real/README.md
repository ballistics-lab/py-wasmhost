# PoC: the real git (git.git) built to WASI

`examples/git/` has a git client on libgit2 with a command line of its own (`lg2.c`): every command is written by hand.
This directory is the other way: **git itself**, from the `git-2.56.0` tarball, built with `zig cc` for `wasm32-wasi`,
so the command line, the options and the output are git's own, and every command of git that does not need a
process or the network is there.

    ZIG="python3 -m ziglang" examples/git/real/build.sh     # writes ../git-real.wasm (3.4 MB; 30 s with 8 cores)

It imports only `wasi_snapshot_preview1` (34 functions). The module needs `-C /` in front (WASI starts in `/`, which is
the preopened root of the repository) and `--no-pager`; the host should also pass `-c gc.auto=0 -c maintenance.auto=false`
(both would start a process).

## What was checked (2026-10-18, `wasmtime`, `node`, `bun`, `wasm3`; not `jscontext`)

`init`, `add`, `commit`, `commit -am`, `log` (also `--graph`), `status`, `diff`, `show`, `branch`, `checkout`, `tag` (also `-a`),
`describe`, `reset --hard`, `cherry-pick`, `rebase`, `rm`, `merge` (a real merge, strategy `ort`, a merge commit). The repository
passes `git fsck --strict` and is read by the host's own `git`, and the other way round.

## What does not work, and why

- **The network.** `git clone/fetch/pull/push` over http(s) run `git-remote-http`, a separate program on libcurl, as a child
  process; there is no libcurl and no process. `git://` needs sockets. The only way in WASI preview1 is a host import.
- **Commands that start a child `git`** (they fail with `cannot create standard input pipe`): `stash`, and the parts of other
  commands that run `git` again (`gc`, `maintenance`, hooks, an editor, a pager, `git bisect run`, `submodule`).
  `merge` is made to work by a patch (`save_state` runs `git stash create` only to restore the state on failure).
- `chmod` is a no-op (WASI has no modes), so `core.filemode` is false; `getuid` is 0 to agree with the owner WASI reports.

## The way to a whole client: both modules together

`git.py` of the parent directory (libgit2, which has the network through `wasmhost_net`) and this module work on **the same
directory**, because they write the same repository format. Checked: `clone` by libgit2, `add` and `commit` by git (it sees
`origin/main: ahead 1`), `push` by libgit2, the server has the commit, `git fsck --strict` is clean. So a `git` command can send
`clone`, `fetch`, `push` (and `ls-remote`) to libgit2 and everything else to this module; `pull` is a `fetch` by libgit2
and a `merge FETCH_HEAD` by git. That gives merge, rebase, stash-free workflows and every option of git, with the
network that already works. The dispatcher is `git.py` of the parent directory.

## What a pure git would take

A process for the commands that start a child `git` (about 150 lines: run the builtin in the same process, with the
child's standard streams in temporary files; the host needs a call to redirect a file descriptor), and for the network
a libcurl that is a shim over `wasmhost_net` (`curl_easy_*` and `curl_multi_*` as used by `http.c` and `remote-curl.c`) and
a way to run `git-remote-http` as a second instance with a pipe in between, which needs two instances of the engine
to run at the same time, and wasmhost has one lock per backend. Not tried.
