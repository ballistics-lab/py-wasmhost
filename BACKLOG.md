# wasmhost backlog

A phased plan. The phases are ordered from what is cheap and needed by other items to what is expensive and depends on the engines.
Marks: `[x]` done, `[ ]` not done, `[~]` partly done. Size: **S** (up to a day), **M** (a few days),
**L** (a week or more). "Backends" says where this can actually be done; the rest honestly report `supports(...) == False`.

The state of the branch `claude/kind-goldberg-3dsi27` (2026-10-07; the earlier branch `claude/loving-hawking-u966j0` was merged as PR #10) was checked against the code and `git log`. Whatever lies outside this repository was not checked here
and is marked "not verified"; the only exception: `tiny-bclibc-wasm` was verified and it works (the owner confirmed this).

## Rule: the self-test ships together with the feature

The runtimes are already checked by CI: the whole `pytest` suite is run separately on each backend (`wasmtime`, `node`, `wasm3`,
`jsc` and `jscontext` on macOS, WebKitGTK `jsc` and `gi-jsc` with and without JIT, as on iOS), plus the "Examples" step.
The self-test (`python -m wasmhost self test`, `wasmhost.selftest()`) does not replace this: it runs **on the device** (iPhone,
Pythonista), where CI does not exist. It must reflect what the library can do now, so it is updated **in the same PR** as
the feature itself:

- A new or changed capability gets a step in `_selftest.py` (following the pattern of `_globals_on_their_own`, `_exceptions`).
  A step that depends on the engine first asks `backend.supports(...)` and, if the answer is no, honestly reports "not supported" instead of
  failing.
- The module for the step is built in `tests/wasm_builder.py`, and `tests/test_selftest.py` checks that the bytes in
  `_selftest.py` match the builder (this is already done for `MODULE`, `CALLBACKS`, `EXCEPTIONS_*`).
- A changed behavior (for example, `Table.get` becoming a callable function) changes the corresponding step, and does not add
  a new one next to it.
- `test_selftest_passes_on_every_backend` runs the self-test on every backend in CI; a green run on a device
  is recorded in the README the same way it was done for iPhone 16 (PR #8).
- The tests in CI check the library, the self-test checks it on the device: one does not replace the other.
- A removed feature means a removed step.

## Working rules (the owner's conventions; read before starting)

- **The owner opens the PR and names/tidies the branch.** The assistant does not create a PR, rename the branch or squash history. Work goes
  into the branch the owner names; after a merge, a new change starts from a fresh `main` in a new branch.
- **Statuses in this file change only on the owner's word.** `[x]`, "DONE" and similar marks are not set on the assistant's own impression
  (B-302a was once closed wrongly and reopened). A decision of the owner is written with its date.
- **The API stays as close to the JavaScript WebAssembly API as possible** (JavaScriptCore is the reference engine). The library is an alpha, so
  breaking old code for a cleaner API is allowed; when a new form is added, the old plain `int`/`float` form keeps working. Over-engineering is
  rejected (e.g. wrapper value types).
- **No races, and no threads by default.** One backend takes one call at a time (a reentrant lock per backend). Worker threads only on request
  (`threaded=True`), because the library must work also where Python threads do not.
- **Pythonista is the main target:** Python 3.10, the standard library only, `jscontext` (no JIT for wasm). The owner checks on the device; give him a
  wheel (`uv build --wheel`) and a script, not a request to "try it". Everything platform-specific is checked in CI (Windows, macOS, PyPy too).
- **A feature comes with tests, a self-test step and the README/BACKLOG** (see the rule at the top). Run `PATH=/usr/local/bin:$PATH uv tool run
  pre-commit run --all-files` (one run at a time: two in parallel corrupt each other) and look at its result **before** committing; do not chain
  `&& git commit` after a `| tail`.
- **Every commit:** `git fetch`, `git pull --ff-only` first (the owner pushes to the same branch), then commit, push, and **look at CI** (Tests and
  Pre-commit, all platforms) instead of trusting the local run. A red CI is fixed at once, not left.
- **Tell the owner in Ukrainian in the chat, keep this file in English.** When he is confused, explain "as to a child" before continuing. Say honestly what
  was not verified (a device, Windows) and what was a guess.
- **Sub-agents:** a worktree starts from `main`, which has no `BACKLOG.md` and none of this branch; give an agent a copy of the files it needs (or a
  commit to fast-forward to), and ask it to verify its own work with a script. Check an agent's result yourself before bringing it into the branch.

## Current state (2026-10-07, written as a handoff to the next session)

**Where things are.** Branch `claude/kind-goldberg-3dsi27`, the one to work in (it started from `main` at `599e2e8`: PR #10 "Limits for untrusted code" and the tag `v0.1.0b2`). Never create a PR or rename the branch: the owner does.
Look at CI of the newest commit first (`git log`). `Tests` at `000d75e` (run 134, after the rename to `wasmhost.wasi.preview1`) was green on every job, and so was `Pre-commit` (run 185); at `c06ac9e` (run 132) and `6f782dc` (run 131) `Tests` was red only on `macos-latest / pypy3.11`, the known flake below. `Pre-commit` has been green in CI since `d823812` (it was red on every push because `ruff format` kept changing `examples/jitcheck.py`; fixed). Locally `pre-commit` runs pyright, ruff and the whole `pytest` (wasmtime, wasm3, node, bun
here; `jsc`, `gi-jsc` and `jscontext` only in CI); on Python 3.15.0rc3 the whole suite passes too (906 passed, 166 skipped). The self-test has 35 steps on wasmtime and wasm3 and 41 on the JS engines.

**Done in this branch (phases 1 to 5, in part).**
- Phases 1 to 4: B-201 (`Function`, `signature`, `type()`, identity, `elem`, `call_indirect` net), B-202, B-204, B-301 (`Memory.view`), B-302 (`bench`), B-304 (a lock per backend, `await compile/instantiate`, threads only with
  `threaded=True`), B-507, B-509, fast buffers, Bun in CI, phase 4 (B-401 memory ceiling, B-402/B-403 timeout, B-404 fuel), all closed by the owner. The limits are in one README section, "Limits for untrusted code".
- **Phase 5 (started by the owner's command): B-501, B-502, B-504 and B-505 are closed.** The package `wasmhost/wasi/` has `preview1.py`: all 46 functions of `wasi_snapshot_preview1` and the 45 of `wasi_unstable` (B-501.1), the class `Wasip1`; the package
  exports the module and the class (`from wasmhost.wasi import Wasip1`). Decisions of the owner: `wasi_unstable` stays in `preview1` (`imports()` offers both); `wasi` matches the official specification and nothing else, what is specific
  to an example stays in the example; WASI 0.2 and 0.3 (Component Model) would be modules of their own and are out of scope. Examples: `coreutils.py` runs on it, `wasmclang.py` takes the calls that are not about files from it (the file
  calls are `memfs.wasm`'s), `wasi_sh.py` keeps its own host; a pluggable file system (`wasmhost.vfs`) is deferred and has no entry. B-504: `Wasip1(readonly=True or {names})`, made of the rights of the specification
  (`ENOTCAPABLE`). B-505: README "What has run where" (programs against backends; `wasmclang.py` aborts on Bun 1.4.2, a bug in Bun). Tests: `tests/test_wasi_preview1*.py` against copies of the witx files in `tests/data/wasi`.
- Python 3.15: the classifier is in `pyproject.toml` and the CI matrix has `3.15` where it had `3.14` (first run green; `3.14t` stays, `3.15t` is not there: `pywasm3` builds only `cp311-*` and `cp314t-*`). `3.15` is unpinned: CI takes
  the release candidate until the stable release (the owner says 2026-10-08).
- `wasmhost.i32`, `i64`, `f32`, `f64` are `ValueType`, a `str` that equals its name (`wasi` uses them); the values stay `int` and `float`. The idea of `ctypes` types for the values is still only in "Ideas".

**Run on devices by the owner.** Pythonista (iPhone 16, iOS 26, Python 3.10.4) and PythonIDE (Python 3.14.7), `jscontext`: **41/41** with the WASI step (wheel `0.1.0b3.dev14+gc14888b9c`, before the rename), earlier 38/38, buffers intact from 1 byte to
8 MiB, `await compile/instantiate`, `wasi_sh --home`, `bench`. iSH-AOK (aarch64, wasmhost 0.1.0b2): `wasmtime` and `wasm3`, both 34/34. The original iSH (i686, `wasm3` only): 31/34, a memory declared 1..4 pages is 4 pages from the start so three memory
steps fail; cause unknown, the owner suspects iSH's i386 emulator and will look when an issue is filed (B-505, README "Backends"). The rows are in README "Where it has been run".
**After the rename the self-test on the device is 41/41 again (Pythonista, the wheel `0.1.0b3.dev20+gb9498e8f6`; the output does not print the version).**
**Examples on the device (Pythonista, `jscontext`, the repo at `62f5f39`, wasmhost `0.1.0b3.dev23+g62f5f397d`, 2026-10-07):** `coreutils.py` works (it runs on `wasmhost.wasi.preview1` now), `coremark.py` works (score 1951.5, last pass
15.4 s), `imports.py` works (host functions: nested calls, `i64`, several results, an exception out of a host function), `basic.py` works, `jitcheck.py` says 167 M iter/s, "interpreter likely (no JIT)" (as documented for iOS),
`jslinux.py` connects (to `wss://relay.widgetry.org/`) and shows the shell prompt `/root #`, `pyodide.py` is ready in 1.9 s (Python 3.14.2, `emscripten`, `wasm32`; the snapshot of 31 MB and the other files came in "via C API"). `wasi_sh.py` works (BusyBox ash 1.38.0 on wasi-sh 0.11.0, the prompt `$`). `wasmclang.py` first failed in `add_tar` **under StaSh** (the owner: it ran without the fix in plain Pythonista; StaSh's `tarfile` object needs the size in `read()`); fixed in `03ca1f6`, and then
it works on the device with its hybrid host: clang and lld compiled C and C++ in `jscontext` (the C++ link took 16 s of 22 s), the same output as on wasmtime and node (`fib(10) = 55`, a WASI program with `argc=3`, C++ with `std::vector` and `std::map`).

**To do first, if the owner has not said otherwise:**
1. ~~On the device: the self-test after the rename~~ Done 2026-10-07 by the owner: Pythonista 41/41 with the wheel after the rename (call 37 us, batch of 3 94 us); all the examples run on the device (see above).
2. The CI of the newest commit, and the macOS/PyPy flake (below): it has now come back three times in the eleven runs 124 to 134 on this branch, so "seen once" is out of date; the owner chose to treat it as a flake, but it may be worth the guesses in its entry.
3. The first CI run after Python 3.15 is released (the matrix is unpinned).
4. B-405 when upstream merges pywasm3's two PRs (#13, #14); B-406 is closed (option b: leave it, documented).
5. Then the owner picks: B-203 (`externref`), a binary channel for node/bun (about 50 MB/s through the pipe and JSON now), B-006 (CI for examples), or the rest of phase 5 (B-503 `openat` sandbox).

**Deferred by owner decision:** B-303 (until it is really needed), B-304a (examples on asyncio), the rest of phase 5 (it waits for a command), `wasmhost.vfs`, Deno (B-701b: a bug in Deno itself). **Waiting for the owner:** the branch name and
tidying (B-003), `coreutils.wasm` stays in git (B-004), `tiny-bclibc-wasm` (B-803). **Not covered by tests:** `examples/wasmclang.py` (run by hand on wasmtime and node on 2026-10-07: the same output before and after the change), `jslinux.py`
(`bellard.org` is blocked in the assistant's environment; the owner ran it on the device), `coremark.py` (the owner ran it on the device); the "Examples" step in CI runs only `basic.py` and `imports.py` (B-006).

**Things that bit this session (read before touching the same code):**
- wasm3: 128 live runtimes per process at most, and an `Instance` is in a reference cycle, so only the cyclic collector frees it (B-406); the `session` fixture in `tests/conftest.py` calls `gc.collect()` for that reason. `suspendable` and
  `gas_limit` must be set **before** the first `find_function`. An instance that timed out is finished.
- Run one pre-commit at a time and do not edit files during a run (a pytest "Failed" can be only the hook seeing a tracked file change). Use the `uv` from pip: `PATH=/usr/local/bin:$PATH uv tool run pre-commit run --all-files` (the one in
  `~/.local/bin` is older and sees only Python 3.15.0b4). `git fetch` and `git pull --ff-only` before each commit; look at CI after each push.
- The WASI specification is **not in `main`** of WebAssembly/WASI: it is the branch `wasi-0.1`, directories `preview1` and `preview0` (`witx/*.witx`, `docs.md`; commit `fae981b` when copied into `tests/data/wasi`). `wasi.dev` is blocked in the
  assistant's environment; reading another repository takes `add_repo` (git only, no Actions logs).
- `gi-jsc` takes no host functions (`supports("imports")` is false): a test that gives a module imports must skip there, as `tests/test_wasi_preview1_run.py` does (CI went red on it once).
- On Windows `time.monotonic` ticks every 15.6 ms: a timing test uses `time.perf_counter` (a 30 ms sleep read as 16 ms on `windows-latest / 3.10`).
- Under StaSh (not in plain Pythonista, the owner says) `tarfile.extractfile()` gives a `MyFileObject` whose `read()` needs the `size` (the standard one does not): pass `member.size` (`examples/wasmclang.py` failed there with `read() missing 1 required positional argument: 'size'`). When something fails on the device, ask whether it was run in StaSh or in Pythonista itself.
- A mutation check that edits a file and puts it back within the same second, with the same size, can leave a stale `.pyc`: delete `__pycache__` before believing a result.
- Facts: `jscontext` on iOS has **no JIT**, no wasmtime or wasm3 there, and neither timeout nor fuel; the owner works in Ukrainian, this file stays in English; a status mark (`[x]`) is set only on the owner's word.

## Order of work

Owner decision: everything is done in the current branch (the owner opens the PR and tidies the branch). Each step comes with tests, a step in the
self-test and a green pre-commit, and is a separate commit. Done so far, in order: B-201 (steps 1-4), B-304, B-204, B-507, B-509, B-202, B-301, B-302,
then phase 4 (B-401 to B-404), then B-501, B-502, B-504 and B-505 (done 2026-10-07). The detail of each is in its own entry below. Open: B-203, B-303, B-304a, B-006, phase 5 (B-503) and phase 6.

## Accepted API decisions

The owner's principle: the facade is close to the JS WebAssembly API (JavaScriptCore); this is an alpha, so the old can be broken for the sake of
a clean API.

- `type()` on `Function`, `Memory`, `Table`, `Global` as a method, with the names from JS type reflection; `Global.type`
  is no longer a string (done).
- A runtime error stays `Trap` (a subclass of `RuntimeError`), and not `RuntimeError`: the name is built into Python and
  would be shadowed, and "trap" is a term of the specification itself. The README already says that this is `WebAssembly.RuntimeError`.
- Numbers stay `int`/`float` (`i64` ↔ Python `int` corresponds to `BigInt`); `ctypes` types are to be considered later,
  only if we want to change the behavior.
- Memory without copying (B-301): done as `Memory.view` on wasmtime and wasm3; on the JS engines `read`/`write` go through a typed array (C API) or base64.
- Async: `async def compile/instantiate` plus `instantiate_sync`, no races (B-304).
- only `Function` and `FuncType` (`FuncRef` is removed): `Function.signature` is optional, `type()` resolves
  it lazily, and so does a call without a signature (B-201); our own value types `i32`/`i64`/`f32`/`f64` as a subclass of `str` (B-205).

## Done (for context)

- [x] Host functions (callbacks from the module into Python) on all backends except `gi-jsc` — PR #2.
- [x] JSContext on macOS (rubicon) — PR #3.
- [x] `Module.customSections`, API completeness — PR #4.
- [x] `Memory(initial, maximum)`, `Table("funcref", …)` with `get/set/grow`, `Global(type, value, mutable=…)`, all of these
      both as separate objects and as values in an import object — PR #5.
- [x] The example `examples/coremark.py` on all backends — PR #6.
- [x] The self-test checks which WebAssembly exception encodings the engine accepts — PR #7; a run on iPhone 16 — PR #8.
- [x] The command line: `python -m wasmhost self test [--backend NAME] [--all]` as a subcommand,
      the first positional argument is left free for a module; without a command the help is printed (code 2). The module `_cli.py`.
- [x] The self-test on the device was reconciled with `main` (steps for `Memory`, `Table`, `customSections`) — see B-005.
- [~] Examples from the branch `examples/zigcc` (merged into `main` by the owner, the branch deleted): `wasmclang.py` (clang/lld in wasm, C and C++),
      `coreutils.py` (a shell over uutils coreutils and Lua with a WASI host in Python), `examples/wasm/coreutils.wasm`,
      `examples/wasm/lua.wasm`, the test `tests/test_coreutils_example.py`. The decision about a PR into `main` — see phase 0.

## Phase 0. Cleanup (S, done first)

It changes nothing in the API, it only makes the documentation truthful.

- [x] **B-001** README, the section on host functions (the line about "Importing a memory, a table or a global is not supported
      yet"): outdated after PR #5. Fix it.
- [x] **B-002** README "Not yet": add what really does not exist (limits in descriptors, `type()`, `externref`,
      zero-copy, timeouts), and remove the items that already exist.
- [x] **B-003** The branch and the PR: **not the assistant's business.** Owner decision: everything will be one PR, which the owner will open personally, and
      before that will tidy the branch personally (name, contents). The assistant works in `examples/zigcc` and does not
      rename, split or open anything.
- [x] **B-005** The self-test on the device was reconciled with `main` and extended with the steps: "memory: made on its own, imported,
      shared" (creation, sharing between two instances and the host, `grow` and the maximum, a wrong
      import), "table: made on its own, imported, shared" (functions through `call_indirect` between instances, null,
      `grow`, `externref` is rejected) and "custom sections". Backends without `import.memory`/`import.table` (wasm3)
      honestly say «не підтримується» (that is, "not supported"). The modules in `_selftest.py` are checked by `tests/test_selftest.py`.
- [ ] **B-006** CI: the "Examples" step runs only `basic.py` and `imports.py`. `coreutils.py` is covered through
      `pytest` (`tests/test_coreutils_example.py`), and `wasmclang.py` is not run in CI at all (it needs
      a download of ~60 MB). Decide whether it is worth it: a light smoke test with an artifact cache, or leave it as manual.
      As of 2026-10-06 the ones left without tests are `examples/wasmclang.py`, `examples/jslinux.py` and `examples/coremark.py`
      (they need large downloads or a terminal; `jslinux.py` cannot be checked here at all: `bellard.org` is blocked by the
      environment's network proxy, 403). `examples/pyodide.py` now has `tests/test_pyodide_example.py` (JS backends, ~5 s,
      skipped without a network); `coreutils.py` and `wasi_sh.py` have tests, but are not
      part of the "Examples" step in CI.
- [x] **B-004** (OWNER DECISION 2026-10-07: `coreutils.wasm` **stays in git for now**; moving it to a Release or a cache at startup is deferred)
      Where to keep it (the repo, a GitHub Release, a cache at startup). Right now the branch history holds two copies (~21 MB):
      before the PR the owner can squash the history so that only one reaches the repository.

Done when: the README matches the behavior, and CI does not go red because of documentation.

## Phase 1. Module introspection (S–M, pure Python in `_binary.py`)

Cheap and needed later: the host must know which memory and table to create for an import.

- [x] **B-101** Limits in `ImportDescriptor` and `ExportDescriptor`: for memory `min`, `max`, the flags `shared` and
      `memory64`; for a table the element type, `min`, `max`. Right now `limits()` is parsed and discarded.
- [x] **B-102** `mutable` in `ExportDescriptor` for globals (imports already have it).
- [x] **B-103** `type()` for `Memory`, `Table`, `Global`, `Function`: a descriptor with limits (following the pattern of
      `WebAssembly.*.type()`).
- [x] **B-104** A helper constructor that creates `Memory`/`Table`/`Global` from an import descriptor, so that the host does not
      compute the limits by hand.

Done as in JS type reflection (the data shapes and names come from the JS API): `FuncType(parameters, results)`, `MemoryType`,
`TableType`, `GlobalType`; `type()` as a method; the constructors accept both the JS descriptor dictionary and `*Type`. `Global.type`
is no longer a string and became a method (a deliberate decision: the facade is close to JS). Tests: `tests/test_types.py`,
`tests/test_binary.py`; self-test: the step "type reflection". Verified on JSC, node, wasmtime, wasm3.

Done when: for an arbitrary module an import object can be assembled automatically from the memory and tables it asks for.

## Phase 2. Tables and references (M–L)

- [x] **B-201** (DONE, steps 1–4, see «Order of work») `Table.get` returns a callable `Function`; `FuncRef` goes away. In JS this question does not exist: `table.get(i)`
      gives an ordinary callable function, the same one as the export (`table.get(0) === exports.add`), because the engine knows the type
      from the inside (verified on JSC 2.52.6 and V8). The JS API does not give us the signature (`WebAssembly.Function` exists only in
      V8 behind a flag), so Python recovers it.
      **Agreed (owner):**
      - **only two types: `Function` and `FuncType`.** `Function` is everything that is callable: an export and a table entry;
        `FuncType` is the single description of a signature (`parameters`, `results`) in descriptors, `type()` and `signature`;
        `FuncRef` is removed from the public API (this is an alpha, breaking is allowed);
      - the field **`Function.signature`**: `FuncType` or `None` ("not set"); reading it resolves nothing and does not
        call the engine; assignment is atomic with the whole value: `f.signature = FuncType((i32, i32), (i32,))`; there is no
        half-set state; an export has `signature` from the moment of creation (from the descriptor);
      - **`Function.type()` resolves lazily and returns `FuncType`**: if `signature` is present, it returns it; if not,
        it searches, puts the result in `signature` and returns it; and if that fails, it raises `ValueError` («signature not found,
        set `signature`»); where the backend has no access to tables, `NotImplementedError`. There is **no** separate
        `resolve_*`, `cast`, `.annotated` and no `resolve=` parameter. A failure is not cached;
      - a call `f(...)` without `signature` resolves it lazily through `type()`, once; the exception propagates unchanged;
      - what was assigned manually is never overwritten by resolution;
      - `f.parameters`/`f.results` (and the old `Function.params`) remain only as reads from `signature`;
      - errors are neither swallowed nor wrapped;
      - `table.get` caches `Function` by (table, function): `table.get(0) is table.get(0)`, as in JS.
      **Where the signature resolves by itself:** wasmtime (the engine provides the types), exports, entries put there by the host through
      `table.set`, functions from active `elem` segments (by function identity, see below); wasm3 has no access to
      tables. **`f.length` is not used at all** (owner decision): the number of parameters does not distinguish `i32`
      from `i64` or `f64`, so it is an unreliable check.
      **A wrong `signature` is safe on all backends (agreed, see «Safety net»):** an error, not a silently wrong
      result.
      **Equality and hash (agreed):** two `Function`s are equal if they are **the same function in the engine**; `signature` is
      **not part** of `__eq__` and `__hash__` (it may appear later, the hash must be stable). One object
      per function (the cache, below), so `table.get(0) is table.get(0)` and `table.get(0) is exports.add`; and if the cache
      ever drops the object, a new wrapper of the same function gives an equal value and the same hash. Signatures are compared
      explicitly: `a.signature == b.signature` (`None` against `FuncType` gives `False`, not an error) or `a.type() ==
      b.type()`, which resolves if needed.
      **Do not contradict what is known (agreed, provided that it does not break the call):** if the signature is known for certain (from the
      module descriptor, from the engine, from an `elem` segment, from `table.set`), assigning a **different** one manually is not allowed:
      `ValueError` immediately at assignment, not at call time ("this function has signature X"). Assigning the **same** one
      is always allowed. Filling in an empty one is allowed, contradicting a known one is not (there is one object, a wrong annotation would break all
      references to it).
      **One code on different backends (agreed):** the user writes the same thing, only the source of the
      signature differs. Exports: the signature comes from the binary on **all** backends. Table entries: on wasmtime the engine gives the type
      (`Func.type(store)`); the JS engines take it from a snapshot by identity after the instance is created (see below), from `table.set`
      entries and from exports; there remains a rare case (the module itself wrote into the table a function unknown to us while
      running), where on a JS engine there will be a clear `ValueError`. **A portable recipe:** `f.signature = FuncType(...)` works the same everywhere (on wasmtime, if it
      matches the known one). `supports("table.signatures")` says whether the backend itself resolves the signatures of table entries.
      wasm3 has no tables: there it is `NotImplementedError`.
      **Automatic resolution by function identity, not by slot (agreed, way 2):** if we remembered
      "slot 3 → Anna", the slot could be overwritten by the module unnoticed by us, so we remember "this specific
      function → its `FuncType`". Right after the instance is created, for every slot of the active `elem` segments
      (for the tables it fills) we read `table.get(i)`, take the function's identity key (the same one as for
      the cache) and store the correspondence with the type given by the binary parse (function index → type). After that
      `table.get(j)` looks up the signature by function identity: if the module overwrote the slot, there will be either another function known
      to us (the correct signature) or an unknown one (`ValueError`), **but never a wrong signature**. Limitations:
      (1) a module with a `start` section: it runs while the instance is created and could have changed the table before
      we read it, and the `start` section is visible from the binary, so for such modules we turn off the auto-resolution by `elem`;
      (2) passive and declarative segments and `table.init` in the module's code are not covered (there it is
      `ValueError`, and the signature can be set manually).
      **Safety net: the engine itself checks the type (agreed, way 1):** on the JS engines a call of a table function, when its
      `signature` is set (manually or resolved), goes through a tiny generated wasm module with a `call_indirect`
      of exactly this type (a service table, the function is put into slot 0: `scratch.set(0, f)`). The engine checks the exact type
      and on a mismatch refuses **before** the function runs. Verified: on wasmtime, Node (V8) and JSC a function of type
      `i64` in a slot `(i32, i32) -> i32` and a function `f32 -> f32` both give `Trap` ("signature mismatch") and are not
      executed. We turn this into a clear error («the function does not have the signature you specified»), so even
      a wrong manual annotation does not silently give a wrong result, and the caveat "a wrong annotation on a JS engine gives
      garbage" disappears from the README. This is plain MVP wasm with no new features, it works on all engines. It needs a
      small wasm generator in `src/` (there is a prototype in `tests/wasm_builder.py`), one module per signature, cached;
      exports (the signature from the module) stay direct calls; a call of a table entry on the JS engines has one extra
      link. On wasmtime the call itself checks the type. Test: a function with a different type in a slot with an annotation, a clear error is expected,
      and the function's side effects did not happen.
      **The cache (agreed):** `Function` is cached by the function's key in the engine; the cache is on weak references so that nothing
      is held longer than needed. wasmtime-py gives a **new** Python wrapper `Func` on every `table.get`
      (`exports[...]` caches by itself); the key of both is the same: `(store_id, внутрішній індекс)` from `Func._func`. We look
      it up in our cache, return the existing `Function`, and throw the new wrapper away. These are **private** fields of
      wasmtime-py: a test that catches a change is needed. For the JS engines the key is `===` in the shim's registry (one object,
      one key).
      **One call path (agreed, path 1):** right now `Function.__call__` and `Batch` (`CallStep(…,
      function.name, …)`) address the backend by the pair (instance, export name), while a table entry has only
      a `handle`. Everything moves to a call by `handle` (`call_ref`): on wasmtime a direct call of `Func`, on the JS engines by
      the key in the shim's registry (with `BigInt` for `i64`); `Batch` too. On wasm3 the handle is the pywasm3 function for
      the export (calling exports stays, there are no tables). For the user the syntax `instance.exports.add(2, 3)`
      **does not change in any case**; for JS it is even closer: `exports.add` there is already the function itself, and not a lookup by
      name on every call.
      Self-test: a step about `Function` with a known and an unknown signature, `type()` and resolution; ordinary tests.
- [x] **B-202** (DONE: `Table(..., init)` and `grow(delta, init)`, filling through `table_set` at the API level, so on the JS engines N calls for a table of N entries; tests in `test_memory_table.py`, a step in the self-test) `Table(kind, initial, maximum, init)`: the initial value.
- [ ] **B-203** `externref` and `funcref` as values of globals and tables (right now only numbers) and `Table("externref", …)`.
      A design decision: how to hold Python objects on the engine side (a registry with handles, release).
- [x] **B-204** (DONE: the `Ref` tuple, `CallStep.outs`, `tests/test_batch_multi_value.py`, a step in the self-test; it was: this is code, not just documentation: `Batch.call` currently raises `NotImplementedError("multi-value results in a batch")`, the README describes this; waiting for a decision about the shape of the result, `Ref` or a tuple of `Ref`) Multi-value in `Batch`: a tuple of results in a batch (right now the README says "not supported").

Backends: wasmtime (fully), wasm3 (partly), the JS engines (through wrappers); what does not exist is marked
`supports("table.externref")` and the like. Risk: `externref` in JSC/Node needs a separate path.
Self-test: change the step for tables (`Table.get` callable, `externref`, the initial value) and the step for `Batch`
(multi-value).

Done when: `instance.exports.table.get(0)(1, 2)` works where the engine allows it, and where it does not, there is a clear error.

## Phase 3. Speed and memory (M–L)

- [x] **B-301** (DONE 2026-10-07: `Memory.view` on wasmtime and wasm3, `supports("memory.view")`, the view is reset on a module call, batch, grow and instance creation, so there is no stale address; `tests/test_memory_view.py`, a step in the self-test. The gain is small (a 6 MB frame is ~6 ms of copying), the main bottleneck on the JS engines remains: encoding, see B-302) Memory without copying on the native backends (wasmtime, wasm3): a `memoryview` over the engine's buffer,
      with an honest rule: the view becomes invalid after `grow`. On the JS backends a copy remains.
- [x] **B-302** (DONE 2026-10-07: `examples/coremark.py` already existed; the transfer of buffers is measured by `wasmhost bench --buffer KIB`, `_bench.measure_memory`, a test in `test_bench.py`; results below) A benchmark: `examples/coremark.py` plus a measurement of the transfer of large buffers (a frame, megabytes) before and after.
- [x] **B-302a** (DONE 2026-10-07, on the owner's word: the `bench` command, the measurements below, the buffer measurement of B-302 and the `AUTO_ORDER` comment about the missing JIT on `jscontext`; the owner has no other iPhone, so no more device rows will come, and what is listed as
      not measured at the end stays unmeasured) The results of comparing the backends (measured 2026-10-06, Linux, CPython 3.11; the best of three series) and what to
      do about them. Packaged as the command `python -m wasmhost bench [--backend] [--no-jit]` (`_bench.py`, `tests/test_bench.py`).
      **Transfer of a 1 MiB buffer into and out of memory (MB/s, Linux, CPython 3.11, `wasmhost bench --buffer 1024`, the best of three):**
      write / read: `wasmtime` ~5800 / ~1000; `wasm3` ~10000 / ~1350; `jsc` 43 / 29; `node` 15 / 11; `bun` 18 / 26.
      Conclusion: on the JS engines 1 MiB takes 25-90 ms, on the native ones 0.1-1 ms, that is, a difference of 100-500 times; for a frame
      of megabytes this is noticeable. The native backends are fast anyway (reading is slower than writing because it creates `bytes`), so
      B-301 (`memoryview` without a copy) would give a gain on them only for very large buffers, and the main gain lies in
      the JS engines: there the bottleneck is encoding (hex through a pipe for `node`/`bun`, hex through a string for `jsc`; on
      `jscontext` in Pythonista the path through the C API, `bytes via C API`, is faster, it has to be measured on the device with the same
      `bench`). Ideas: base64 instead of hex (half the data), a binary channel for `node`/`bun`, large buffers in chunks.
      **Done (2026-10-07):** `memory_read`/`memory_write` on the engines with a C API (jsc, jscontext) now go through a typed
      array via the C API, and on node/bun through base64 with `Buffer` (`__Buffer` is passed into the `vm` context). After: `jsc` write
      43 -> ~1400, read 29 -> ~7200 MB/s (30-250 times); `node` 15 -> 48 / 11 -> 48; `bun` 20 -> 39 / 25 -> 94.
      **On iPhone 16 (Pythonista, `jscontext`, measured by the owner 2026-10-07, dev84): 1 MiB write 5419 / read 12742 MB/s, 8 MiB 9743 / 24201;
      64 KiB 1052 / 1169; the buffers are intact at all sizes (1 byte to 8 MiB); a call 36-42 µs, a batch of 3 81-84 µs; selftest 36/36.**
      Next, the bottleneck of node/bun is the pipe and JSON (~50 MB/s): a binary channel or a larger read chunk. `jscontext`
      on Pythonista has the same path through the C API: measure `wasmhost bench` on the device.
      JSC without JIT is turned off with the variable `JSC_useJIT=false` before the process starts (`jsc` has no `--jitless` of its own; `typeof
      WebAssembly` remains `object`, wasm runs on the LLInt interpreter).
      Computation inside wasm itself (`fib(30)` recursive / a loop of 1e8 iterations), integers only, no memory:
      `jsc` with JIT 5.9 ms / 132 ms; `wasmtime` 10.6 ms / 180 ms; **`wasm3` 60 ms / 513 ms**; **`jsc` without JIT 226-259 ms /
      2780-2860 ms** (4-5 times slower than `wasm3`).
      A call from Python (`add(1, 2)` / a batch of 3): `wasm3` 2-4 / 18-31 µs; `jsc` without JIT 25 / 75 µs; `jsc` with JIT 19 /
      54-66 µs; `wasmtime` 40-58 / 150-180 µs; `node` 123 / 160-200 µs; `bun` 530-560 / 570-620 µs (JIT has almost no effect on a call:
      the bridge through the C API or the pipe dominates).
      **Pythonista, iPhone 16, iOS 26, `jscontext` (2026-10-06, `--fib 20 --loop 1000000 --calls 50 --repeat 1`):** a call
      65 µs, a batch of 3 160 µs, `fib(20)` 1.4 ms, a loop of 1e6 13.5 ms. The same parameters on a Linux machine (a different processor,
      `--repeat 3`): `jsc` with JIT 0.2 / 1.9 ms; `jsc` without JIT 1.7 / 24.5 ms; `wasm3` 0.4 / 4.8 ms; `wasmtime` 0.1 / 1.3 ms.
      Conclusion: in speed `jscontext` on iOS is closer to JavaScriptCore **without JIT** (on `fib(20)` 1.4 versus 1.7 ms, and
      not 0.2), so this matches the known fact: in Pythonista (a third-party app on iOS) there is no JIT. A comparison across different
      processors is rough; if desired, run the same `wasmhost bench` on several iPhones and add a line to the README; pywasm3 cannot be installed on iOS (a C extension), so
      the advantage of `wasm3` there is only an estimate. Conclusion: where JIT is unavailable, `wasm3` wins several times over (it runs Rust builds and `coreutils.wasm` too, checked 2026-10-07, see B-505;
      Pyodide it does not take: see B-505). An idea: mention this difference (there is no JIT on `jscontext`) in `AUTO_ORDER` and the README;
      Not measured: memory, f32/f64, SIMD, `wasmtime` with other
      Cranelift settings, `node --jitless`.
- [ ] **B-304a** (after B-304) Examples on `asyncio` instead of polling in a loop with `sleep`/`select`. Owner decision:
      convert `examples/jslinux.py` (the loop of `__runTimers`/`__takeReqs`/WebSocket/console with `select` and `time.sleep(0.005)`,
      and on iOS a thread for `input()`), `examples/pyodide.py` (`time.sleep(0.005)`, line ~565) and everything else that has such
      a loop (`examples/coreutils.py`: `time.sleep` in `poll_oneoff`, this stays blocking by the definition of WASI, but the
      interactive loop of the shell can be converted). Conditions: engine calls are synchronous, so in the event loop they should be passed through
      the backend lock from B-304 (`asyncio.to_thread` where the backend is not tied to a thread; on jscontext/jsc executed
      in place), without races; reading the console: `loop.add_reader`, and where `select()` does not work (the iOS console), `to_thread`
      on `input()`. Each example must keep working on Pythonista; the example tests are updated together with it.
      Analysis before the work (2026-10-06): there are no technical blockers (B-304 is done), but there are two questions. (1) `pyodide.py`:
      `_wait` is a synchronous pump `while ...: _pump; _service_fetch; sleep(0.005)` inside a class with a synchronous
      API (`run`, `eval`), so converting to asyncio changes the example's public API: it has to be decided whether to make `async def`
      methods (with `run_sync` for the old code), or only the main loop. (2) Both examples have no tests and to
      check them need large downloads (Pyodide, the JSLinux image) and a terminal; breaking working examples
      without a check on the device is risky. A proposal: first `tests` with a minimal run of each (skipped without a
      network), then convert one by one, with the owner checking on Pythonista.
- [ ] **B-303** (assessed 2026-10-07; **deferred by the owner until it is really needed**; status unchanged) Batch `read/write` for many regions in one call (fewer engine↔Python transitions).
      **Assessment (nothing built):** size **S**, about 3-5 hours. The item as first written is **mostly done already**: `Batch.write(memory, offset, data)` and `Batch.read(memory, offset, length)`
      (`_api.py`) take an offset and a length that may be `Ref`s, and on a JavaScript engine a whole batch is one trip (`run_batch`), so many regions in one trip works today with `b.write(...)` /
      `b.read(...)` in a loop. What is missing is only a convenience outside `Batch`: `Memory.read` / `Memory.write` take one region, one trip each. A possible shape: `memory.readv([(offset, length), ...])`
      -> `list[bytes]` and `memory.writev([(offset, data), ...])`, built from the same `ReadStep` / `WriteStep`, with no new step type and no change in `_native.py` / `_js.py`; about 25 lines in `_api.py`,
      tests on every backend, and a self-test step (the rule at the top).
      **Risks:** (1) `Memory` does not know its `Instance` (it can be made on its own), and a `Batch` needs one, so `readv` / `writev` on `Memory` would call `backend.run_batch` directly, or live on `Instance` /
      `Batch` only: the one real design question. (2) A batch stops at the first failing step: for `writev` that leaves the earlier writes done; either accept and document it, or check every bound first.
      (3) The gain is small: native backends pay microseconds per trip; it is real on node/bun and a little on jsc; on `jscontext` a call is 36 us, so Pythonista gains little. (4) It adds surface that the JavaScript API
      does not have, against the rule that the API stays close to it, and a second road to what `Batch` already does. **Suggestion made to the owner:** perhaps close it with the note "`Batch` covers it"; the owner chose to
      keep it open and wait for a real need.
- [x] **B-304** (DONE 2026-10-06; owner decision: **threads only on demand, none by default**, because it has to work also where Python threads do not work: `await compile(...)`/`await instantiate(...)` by default run in place with a tick of the loop before and after, and `threaded=True` hands the work to a worker on wasmtime/node/bun (`supports("threads")`); jsc/gi-jsc/jscontext/wasm3 always in place. `Backend._lock`: a reentrant lock on every public method of the backend, separate for each backend instance, so two backends do not wait for each other, and a host function can call the same backend again; `instantiate_sync` is the former synchronous `instantiate`; tests `tests/test_async.py`, `tests/test_threads.py`, a step in the self-test. Not done: examples on asyncio (B-304a); that a host function with `threaded=True` runs in the worker is only described) Asynchronous `compile`/`instantiate`, as in JS, where `WebAssembly.compile/instantiate` return a promise.
      The original proposal (kept for the record): `async def compile(...)` and `async def instantiate(...)` on `asyncio`, while the synchronous
      `Module(...)`/`Instance(...)` stay (these are JS constructors) and also `instantiate_sync` for the current
      synchronous `instantiate` (it can be renamed now: this is a breaking change). Work in a thread
      (`asyncio.to_thread`) only on the backends where this is safe (wasmtime: compilation does not touch the store; Node:
      the request goes to a separate process anyway), and on `jscontext`/`jsc`, which are tied to a thread, it runs in place with
      `await asyncio.sleep(0)` before and after. Mark it via `supports("threads")`. **Owner's requirement: no races.**
      No simultaneous calls into one backend: one lock (or queue) per backend, the thread performs only one
      task at a time, and the result returns to the event loop through `loop.call_soon_threadsafe`. A test on concurrent
      `gather(instantiate(...), instantiate(...))`. Tests through `asyncio.run`, a step in the
      self-test.

Self-test: the step «zero-copy memory» (the view is invalid after `grow`) and a step for the asynchronous `compile`;
extend the "call cost" line with a measurement of buffer transfer.

Done when: transferring a buffer of megabytes is not copied on wasmtime and wasm3, and the benchmark shows it.

## Phase 4. Limits for untrusted code (L)

- [x] **B-401** (DONE 2026-10-07, on the owner's word; two roads, the self-test has the steps "memory: the maximum of an imported memory stops the module's grow" and "memory: Instance(max_memory=) is a ceiling for a memory the module makes")
      A memory limit: `grow` beyond the limit returns -1 and does not bring the process down.
      **A module that imports its memory** (Emscripten `IMPORTED_MEMORY`, `--import-memory`): as in JavaScript, the maximum of the `Memory`
      the host gives is the ceiling; nothing new in `src/`, pinned by tests on every backend with `import.memory`
      (`test_memory_table.py`), a self-test step, the README.
      **A memory the module makes itself, with no maximum** (the JavaScript API has no way to cap it, so this is an extension): `Instance(module,
      imports, max_memory=pages)`, also in `instantiate` and `instantiate_sync` (the owner chose `Instance`, not `Module`, and asked for a
      cache). `_binary.limit_memory` writes the ceiling into the memory section (no maximum gets it, a larger one is lowered, a smaller one stays;
      a module that starts above it is a `ValueError`; a custom page size is refused), so it is the same on every engine, `wasm3` included.
      `Module` keeps its bytes and `Module._variant(pages, epochs, fuel)` (it was `_held_to`) compiles the changed copy once per ceiling (a module that needs no change is the module
      itself, nothing is compiled); `type()` of an exported memory says the maximum that holds. A memory the module imports is checked against the
      ceiling: no maximum, or a larger one, is a `LinkError`. Tests: `tests/test_max_memory.py`.
      Limits of this: only memories; no time or fuel (B-402); a bigger-than-needed ceiling costs nothing, but a module compiled with a custom page
      size is not supported. **Verified by the owner on the device (Pythonista, iPhone 16, iOS 26, `jscontext`, 2026-10-07): self-test 38/38**, both steps
      run (not "not available"), a call 36 us, a batch of 3 83 us. CI was green on all platforms (run 37616628414).
- [x] **B-402** (DONE 2026-10-07, on the owner's word; CI green on every platform, run 37626340443; not run on a device: there `supports("timeout")` is false and the step says so; self-test step "timeout: Instance(timeout=) stops an endless loop, where the engine can", `tests/test_timeout.py`)
      A timeout: `Instance(module, imports, timeout=seconds)`, also `instantiate` and `instantiate_sync`; a call that runs longer ends with `wasmhost.Timeout`
      (a `Trap`), and the instance can be called again. Wall-clock time, per call (a batch is one call), the start function included.
      Measured before building (2026-10-07, Linux): **wasmtime** epochs stop a loop (0.30 s for 0.3 s asked) but a tight loop runs about 3 times slower (0.032 -> 0.105 s for
      1e8 iterations), so the epoch engine is a second `Engine`, made only for instances that ask (`Backend.compile_limited`, `Module._variant`: the module is
      compiled for it once and kept), and such an instance lives in a store of its own (it can't share a `Memory`, `Table` or `Global` made outside it); a daemon watchdog thread
      (`_Watchdog`) per backend, made when first needed, calls `increment_epoch`. **node**: `vm.runInContext(src, ctx, {timeout})` stops a wasm loop (0.33 s),
      the context is fine afterwards; the timeout goes with the request, and is kept for an instance, its exported functions and tables (and what is taken from them),
      so a function that is not one of those is not timed; a `Timeout` in a batch drops the results of the steps before it (the termination can't be caught in the script).
      **Not possible, and `supports("timeout")` is false:** **bun** (its `vm` timeout does not stop a wasm loop: the process hangs, had to be killed); **JavaScriptCore**
      (`jscontext`, `jsc`, `gi-jsc`: `JSContextGroupSetExecutionTimeLimit` is exported and stops a JavaScript loop in 0.30 s, but a wasm loop runs on: probed with
      `libjavascriptcoregtk` 2.52.6 on Linux; not probed on a device, but the owner's view that JSC has no interruption holds), so **on iOS there is no time limit**. A backend without it raises `NotImplementedError` from
      `Instance(timeout=)`, and the self-test step does not run the loop there.
      **wasm3 (added 2026-10-07, after the owner re-pinned pywasm3 and asked to look at suspend/resume):** a thread can't stop a call (it holds the GIL: `request_suspend()` from a
      `threading.Timer` never ran and `spin()` hung), but pywasm3's suspendable runs can: `suspendable = True` and `gas_limit` make a call **pause** (return None, `suspended`) when its gas is
      spent, and `resume()` goes on, so `Wasm3Backend._run` cuts a call into slices of `SLICE_GAS` = 20000 (about 5 ms of a tight loop; measured: 5000 gas is 1.4 ms and notices the deadline 0.4 ms
      late, 100000 is 27 ms and 13 ms late; a busy loop in slices of 20000 cost x1.08, and `suspendable` alone costs nothing) and looks at the clock between them.
      **Both settings must come before the first `find_function()`** (the code is instrumented as it is found; set after, an endless loop ran on for ever), so `instantiate` sets them.
      **Limit: a paused call can't be cancelled** (no such call in pywasm3; turning `suspendable` off and resuming with no gas left paused it again; a new call that has to pause then traps
      `out of gas`), so **an instance that timed out is finished: every later call is a `Trap`** ("can't run again"), other instances are fine. Calls from a host function share the outer deadline.
      **Correction (2026-10-07): an earlier note here and in the README said "no gas or interruption in pywasm3"; that was wrong and unchecked.** What pywasm3 has: `Runtime.gas_limit` / `gas_used`,
      and an endless loop under a limit ends with `[trap] out of gas` (1.3 s for 5e6 units; units are not instructions), plus `suspendable`, `request_suspend`, `resume`, snapshots, and `memory_limit` /
      `table_limit` / `continuation_limit`, `new_tag` (exceptions, B-601).
      Not done: fuel (a count of instructions, deterministic, no thread): it was only in this item's title, and is now B-404. wasmtime has `consume_fuel` (same cost question as epochs: a
      second engine), wasm3 has `gas_limit` (cheap, no second engine); neither JavaScriptCore nor Node/Bun can count, and iOS has neither wasmtime nor wasm3, so it would not help there.
      Not verified on macOS/Windows or a device by this item itself (CI was green on every platform).
      Found on the way: a trap in the start function leaked out of wasmtime as its own `Trap` (fixed: `Trap` is not a `WasmtimeError`); `wasm3` does not run the start function when the
      instance is made, but at the first call (so there the start function is under the timeout at the first call).
- [x] **B-403** (DONE 2026-10-07, with B-402: `tests/test_timeout.py`, 36 tests) Tests "a module with an infinite loop" on every backend that can do this:
      `wasmtime` and `node` stop it; the others are skipped with the reason, and `test_a_backend_that_cannot_says_so` checks that they refuse honestly.
- [x] **B-404** (DONE 2026-10-07, on the owner's word ("if implemented, close it"); CI of 47c409e was still running when it was closed, a check was set for after; started on the owner's word once the wasm3 timeout showed gas works; self-test step "fuel: Instance(fuel=) stops an endless loop, where the engine can count", `tests/test_fuel.py`)
      Fuel / gas: `Instance(module, imports, fuel=n)`, also `instantiate` and `instantiate_sync`: a call that uses more than `n` units of what the engine counts ends with `wasmhost.OutOfFuel` (a `Trap`).
      Deterministic (the same module and input stop at the same place on any machine, whatever the load) and needs no thread. **The owner left the decisions to the assistant; made as follows:**
      per call (every call starts with the whole budget, a batch is one call, a call from a host function shares the outer one, the start function is under it: as `timeout`); `supports("fuel")`;
      `OutOfFuel` apart from `Timeout`; the unit is the engine's own ("units"), so a number does not carry between engines (wasmtime: 8 for a turn of the test loop; wasm3: under 0.1).
      **wasmtime:** `Config.consume_fuel`, `Store.set_fuel` before the call, `TrapCode.OUT_OF_FUEL`. Counted in the code the engine makes; measured on a tight loop, busy(1e8): plain 0.033 s, epochs 0.096 (x2.9), fuel
      0.080 (x2.4), both 0.175 (x5.3), so there is an engine for each of (epochs, fuel) that an instance asks for (`_engine_for`, `Backend.compile_limited(data, epochs=, fuel=)`, `Module._variant(pages, epochs, fuel)`),
      each instance with a limit in a store of its own. **wasm3:** `Runtime.gas_limit` set before the first `find_function` (the code is instrumented as it is found); out of gas is `RuntimeError: [trap] out of gas`,
      turned into `OutOfFuel`, and the instance goes on (a trap leaves nothing paused). With a `timeout` too, the call is already in slices of gas, so the fuel is counted there (`remaining`); running out
      then leaves a paused call that can't be cancelled, so the instance is finished, as after a `Timeout`. **Engines that can't:** Node, Bun and the JavaScriptCore ones have nothing to count with:
      `NotImplementedError`; and **iOS has neither wasmtime nor wasm3, so this does not help in Pythonista.**
      Not verified on macOS/Windows or a device by this item itself (CI will say); the wasmtime and wasm3 numbers are from one Linux machine.
      Checked on the way, 2026-10-07: pywasm3's `request_suspend()` from another thread can not work, whatever a note said: a call holds the GIL, a `threading.Timer` never ran in 10 s, with `suspendable` and gas armed
      before `find_function` (pywasm3 at 4c1334e); the documented way is from a host function. Snapshots (`save_snapshot` / `load_snapshot`) and `memory_limit` / `table_limit` / `continuation_limit` are there, unused.
      **pywasm3 PR #14 (2026-10-07, the owner's, `fix/gil-env-lock`, 7562b12: a recursive lock of an `Environment` on GIL builds too), checked: not needed for this item.**
      Built from that commit in a scratch venv: `wasmhost` on wasm3 gives **174 passed, 76 skipped, the same as on the pinned `main` (4c1334e)**, `test_timeout.py` and `test_fuel.py`
      included. We are not exposed to what it fixes (two threads calling one runtime): every public method of a backend takes `Backend._lock`, a `Wasm3Backend` owns its
      `Environment`, and the slices of a timed call are inside one locked call. It does not change the timeout: a call still holds the GIL between imports (the `threading.Timer`
      probe was run on the pinned `main` only). **Owner's decision (2026-10-07): B-404 stays closed; the re-pin is B-405.**
- [ ] **B-405** (added 2026-10-07 on the owner's word; waiting for upstream, nothing to do here until then) Re-pin `pywasm3` (`[tool.uv.sources]` in `pyproject.toml`, and `uv.lock`) when upstream merges its two open PRs.
      Now pinned to `4c1334e` (main of 2026-09-28: the exception handling API, wasm3 0.9.2, on top of suspendable runs, snapshots, gas and the resource caps). The two PRs, both the owner's, both of 2026-10-07
      (named from the PR heads on `wasm3/pywasm3`, read through git; whether they are still open was not checked, the GitHub API does not reach that repository from here, the owner says two are open):
      **#14** `fix/gil-env-lock` (7562b12): a lock of an `Environment` on GIL builds too (see B-404: hardening, not needed by us because every backend call holds `Backend._lock`);
      **#13** `python315` (da5d132): "build: add Python 3.15 and 3.15t support" (not read; a build matter: CI here runs 3.10, 3.14 and 3.14t, no 3.15).
      When they are merged: take the SHA of `main`, change `rev` and its comment in `pyproject.toml`, `uv lock` (only pywasm3 should change in the lock), `uv sync`, the wasm3 tests
      (`uv run pytest --wasm-backend wasm3`: 174 passed, 76 skipped now) and the self-test, the whole pre-commit, then CI on every platform (it builds pywasm3 from git, with a C compiler, on each).
      Not needed before: with the fix branch the same 174 tests pass (see B-404), so nothing waits on it.
- [x] **B-406** (added 2026-10-07, found when CI went red; CLOSED 2026-10-07 on the owner's word, as option (b): left as it is, documented) wasm3: at most 128 live runtimes, and an `Instance` is freed only by the cyclic collector.
      **CI went red twice on `3.14t` (the free-threaded build):** run 37633049533 (2f40e8e, `windows-latest / 3.14t`, `test_the_module_for_stopping_is_made_once[wasm3]`) and run 37634050575 (47c409e,
      `ubuntu-latest / 3.14t`, `test_an_instance_without_a_timeout_is_not_timed[wasm3]`): `RuntimeError: memory allocation failed` in `runtime.load()`, in different tests, a plain instance too.
      **Cause (measured here, 2026-10-07):** pywasm3's guarded memory takes a slot of one process-wide arena at every `Runtime.load` and gives it back when the runtime is freed; there are **128**: with runtimes
      kept in a list the 129th fails, with or without a memory in the module, and after they are freed a new one loads (`_wasm3.c` says so: "Guarded memory hands out slots of a single arena for the whole process").
      And `wasmhost`'s `Instance` is in a reference cycle (`Instance.exports` -> `_LazyFunction.make`, a partial of the bound `_export_function` -> `Instance`; once an export is used, `Function.instance` and the exports
      cache close it again), so it is not freed by counting references: with the collector off, `Instance(module)` made and dropped at once fails at #129, with a timeout or with fuel too. CPython collects often enough
      that this never showed; a free-threaded build does not, and the timeout and fuel tests make many wasm3 instances.
      **Fixed for the tests only:** the `session` fixture calls `gc.collect()` when a test ends (checked with the collector switched off: before, many failures; after, 174 passed on wasm3; and with it on, 174 passed).
      **Not fixed in the library, the owner decides:** (a) break the cycle: the lazy export holding the instance through a weak reference, and `_Exports` not keeping the `Function` it made (the backend's weak cache
      keeps `exports.add is exports.add`; cost: a cache lookup on each `exports.name`, to be measured on the hot path of a call); (b) leave it and say so, as the README now does ("room for 128 live instances");
      (c) both. A user who makes hundreds of wasm3 instances in a loop on a free-threaded build is the one this touches.
      **Owner's answer (2026-10-07), read as option (b): leave the library as it is and say so** (README: "room for 128 live instances"); the cycle is not broken. Closed by the owner the same day.

Risks: JSC has no interruption, so on iOS this is not guaranteed; a separate note is needed about what the sandbox does not
promise.
Self-test: the steps "memory limit" and "interruption of an infinite loop" with `supports("timeout")`; on JSC they say
"not supported" instead of failing.

Done when: an infinite loop is interrupted with a `Trap`-like error where the engine supports it.

## Phase 5. WASI1 (started by the owner's command on 2026-10-07: B-501, tests, self-test; the rest deferred, L)

Decision: "WASI1 for later", then on 2026-10-07 the owner commanded B-501 with its tests and self-test, and B-502 after the docs refresh. Anything else in this phase waits for a command. The specification to follow is the
official `wasi_snapshot_preview1` (branch `wasi-0.1` of WebAssembly/WASI), not the example. Preparatory material, now in `main`: the class `Wasi` in `examples/coreutils.py` (over 40 calls, both snapshots
`wasi_snapshot_preview1` and `wasi_unstable`, files in a real folder, stdio, arguments, a clock, `sleep`), tests on wasmtime and node.

- [x] **B-501** (DONE 2026-10-07, closed on the owner's word; with B-501.1, `wasi_unstable`) Provide a reusable Python module `wasmhost.wasi1` with a full WASI1-compatible host API, not just the subset used in one example. The public surface should cover the ~50 WASI1 functions and be usable across backends that support it.
      **Implemented 2026-10-07 in branch `claude/kind-goldberg-3dsi27`, closed by the owner the same day.**
      - **Renamed 2026-10-07 on the owner's word:** the module was `wasmhost.wasi1`; it is now the package `wasmhost/wasi/` with `preview1.py` (WASI 0.1, `wasi_snapshot_preview1`) and the class `Wasip1`; `wasmhost.wasi` exports
        the module and the class (`from wasmhost.wasi import Wasip1`): a class has its version in its name, so another version's cannot be taken for it (WASI 0.2 and 0.3 are Component Model, would be modules of their own,
        and are out of scope). **`wasi_unstable` stays in `preview1`** (owner's decision):
        `imports()` has both. `pyproject.toml` lists the package (`wasmhost.wasi`) by hand: a new subpackage has to be added there.
      - `src/wasmhost/wasi/preview1.py`: all 46 functions of `wasi_snapshot_preview1`, written from the specification (branch `wasi-0.1` of WebAssembly/WASI, `preview1/witx`) and not from `examples/coreutils.py`.
        Real folders as preopens, standard streams, arguments, environment, clocks, random. Sockets answer `ENOTSOCK`, signals `ENOSYS`.
      - **B-501.1, `wasi_unstable` (the first snapshot), added on the owner's word 2026-10-07**: the same `Wasip1` offers both modules (`imports()` has both). The four differences from `preview0/witx` (the order of `whence`,
        one right less, a 32-bit `nlink` in `filestat`, an `identifier` in a clock subscription) live in `UNSTABLE_TABLES`, `UNSTABLE_STRUCTS` and a `legacy` flag of five functions; no `sock_accept`, 45 functions.
      - Tests: `tests/test_wasi_preview1_spec.py` and `tests/test_wasi_preview1_unstable_spec.py` compare names, signatures, tables, record sizes and offsets with copies of the witx files (`tests/data/wasi`, with the specification's
        `LICENSE.md`); `tests/test_wasi_preview1.py` tests each call over a plain bytearray; `tests/test_wasi_preview1_run.py` runs a program of each snapshot on every backend. The self-test has a WASI step (a program of each snapshot).
      - Not done here: `openat` (B-503; the read-only mode is B-504, done); not run on a device. CI: `Tests` green at `c5aaf86` (all of `wasi_snapshot_preview1`); the run for `9ce13e0`, which added `wasi_unstable`, was still going when this was closed: look at it.
- [x] **B-502** (DONE 2026-10-07, closed on the owner's word, in the scope below) Update all WASI examples and tests to use `wasmhost.wasi1` instead of carrying a copy of `Wasi` in each example. The example-side API should stay thin: `Wasi(args=…, preopens={"/": dir}, stdin=…, stdout=…)` is a convenience wrapper around the common module, not the implementation itself.
      **Done in the scope below 2026-10-07, closed by the owner.** `examples/coreutils.py` uses `wasmhost.wasi.preview1` (its own 430-line `Wasi` class is gone; `wasi_host()` is now 15 lines that give the library a folder as `/` and `.` and turn the
      pipe limit into a `BrokenPipeError` of the stdout sink); `tests/test_coreutils_example.py` passes on wasmtime, wasm3, node and bun, and a run by hand of Lua (`wasi_unstable`), `yes | head` and `cat ../x` gives the same as before.
      **`examples/wasmclang.py` uses `wasi.preview1` for the seven calls that are not about files** (`proc_exit`, `args_*`, `environ_*`, `random_get`, `clock_time_get`; of the first snapshot) and leaves the file calls to `memfs.wasm`, whose
      exports *are* the WASI functions (they work on the memory of the other module); `poll_oneoff` stays ENOSYS. Run for real on wasmtime before and after: the same output (clang C -> wasm, a WASI program with `argc=3`, C++ with
      `std::vector`/`std::map`); the file is 46 lines shorter. **Owner decision 2026-10-07: `wasi.preview1` matches the official specification and nothing else; what is specific to an example stays in the example.** So `examples/wasi_sh.py`
      keeps its host (a file system in a Python dict, pipes and `dup` through `env.__host_*`, an in-memory mode), and a pluggable file system (`wasmhost.vfs`, a `MemVfs`) is **deferred**: not planned, no entry yet.
- [ ] **B-503** A sandbox through `openat` with `dir_fd`, to remove the gap between checking the path and opening
      (right now `_resolve()` in `wasmhost/wasi/preview1.py` checks, then `os.open`; the README says so).
- [x] **B-504** (DONE 2026-10-07, closed on the owner's word to close what is done) Several preopen folders and a "read-only" mode.
      Several preopens were there from B-501 (`Wasip1(preopens={name: folder, ...})`). **The read-only mode:** `Wasip1(..., readonly=True)` for all of them or `readonly={"name", ...}` for some (a keyword at the end, so no
      positional call moves; an unknown name is a `ValueError`). It is done with the rights of the specification and not with a check of its own: the rights that change something (`_MUTATING_RIGHTS`: `fd_write`, `fd_datasync`,
      `fd_allocate`, `fd_filestat_set_*`, `path_create_*`, `path_link_*`, `path_rename_*`, `path_symlink`, `path_remove_directory`, `path_unlink_file`, `path_filestat_set_*`) are taken away from the folder and so from everything opened
      in it; a call that needs one answers `ENOTCAPABLE`, and a file opened with every right asked for is opened for reading only. Tests in `tests/test_wasi_preview1.py` (the rights, every refused call, a file opened from it,
      named preopens, both snapshots) and `tests/test_wasi_preview1_run.py` (a program on every backend); the self-test's WASI step runs the program on a read-only folder too. **A bug found on the way and fixed:** a combined right
      (`fd_pread`, `fd_pwrite`: `fd_read|fd_seek`, `fd_write|fd_seek`) passed the check if *any* of the bits was there; now all are needed, and the type of the descriptor is looked at before the right.
- [x] **B-505** (DONE 2026-10-07, closed on the owner's word to close what is done, in the scope below) Tests on all backends. **Correction (2026-10-07): `wasm3` is confirmed to run Rust/WASI1 programs; the older note that it does not take Rust builds was wrong and unchecked.**
      Checked: a `std` program built with `rustc 1.97.0` for `wasm32-wasip1` (HashMap, `format!`, `u128`, arguments; 2.1 MB, uses `memory.copy` / `memory.fill`) and a `no_std` `wasm32-unknown-unknown` library
      run on `wasm3` with the same output as on `wasmtime`; and `tests/test_coreutils_example.py` (3 tests) passes on `wasm3` with its skip taken off (the skip is now removed). What `wasm3` does not take is
      still unmeasured as a list: Pyodide's `pyodide.asm.wasm` (not WASI-only: 280 imported globals, which our `wasm3` backend can't give (`supports("import.global")` is false), and `externref` in 149 function types) does not link.
      So `supports` needs no "Rust" entry; the remaining work was the table of what each backend can run (see the WASI notes above).
      **Done in that scope (2026-10-07):** the suite runs on every backend in CI (that part was there), the capability table was there, and README "Backends" now has "What has run where": the programs (the self-test's WASI programs,
      `coreutils`, `wasi_sh`, `wasmclang`, `pyodide`, `coremark`, `jslinux`) against the seven backends, filled from runs here, CI logs and the owner's device runs, with `?` where nothing was run. Found by it: `wasmclang.py` works on
      `wasm3` (14 s, faster than on `wasmtime`, 21 s) and on `node`, and **does not on Bun 1.4.2**, which aborts (`panic: abort() called`, "a bug in Bun"), the same with the file as it was before this session. The `?` cells (`jsc`
      for `wasmclang`, `pyodide`, `coremark`; `jslinux` everywhere but the device) are not tests we lack but runs nobody made; filling them needs a machine with `jsc`.
      Seen 2026-10-07 on the original iSH (i686, CPython 3.11.12, `wasm3` only; reported by the owner, not reproduced here): a memory declared 1..4 pages is 4 pages from the start, so 3 self-test
      steps fail (31/34): the module's own `memory.grow`, `Instance(max_memory=)`, `type()` of a memory. Cause not known and not investigated: the owner suspects iSH's i386 emulator, not 32-bit as such; he will look when an issue is filed. (pywasm3's publish.yml builds i686 under QEMU and runs its own tests on wasm3, but they only use memories without a declared maximum, so they do not settle it); open
      for the owner: make those steps tolerant of it, fix it in the backend, or document it (README "Backends" says what was seen).
- [x] **B-506** (DONE 2026-10-08 as `wasmhost run`, the owner's word: the running is behind a subcommand, so no word is reserved and nothing like `./self` is needed; `src/wasmhost/_run.py`, `tests/test_cli_run.py`; the options are the ones below plus `--readonly`, `--max-memory`, `--timeout`, `--fuel`) A command to run a module along the lines of `wasmtime myapp.wasm -- arg1 arg2 --verbose`. The grammar:
      `wasmhost [ОПЦІЇ ХОСТА] myapp.wasm [-- АРГУМЕНТИ ПРОГРАМИ]`: the options before the module belong to the host (`--backend`,
      `--dir ХОСТ::ГІСТЬ`, `--env K=V`), everything after the module (and after `--`) goes unparsed to the program through
      `args_get`. The command names (`self`/`test`) are checked first, everything else is treated as a module file; a file with
      the name of a command is run as `./self`. The subcommand `self test` is the canonical form. Depends on B-502 (done: `wasmhost.wasi.preview1.Wasip1`).
- [x] **B-507** (DONE: `[project.scripts]`, `tests/test_cli_entry.py`; we will extend it when running a module appears, B-506) The entry point `wasmhost`: `[project.scripts] wasmhost = "wasmhost._cli:main"` in `pyproject.toml`, so that one can
      write `wasmhost myapp.wasm` and `wasmhost self test`, and not `python -m wasmhost`. Right now there is no such command.
      Check `uv lock --check` and the wheels.
- [x] **B-508** (DONE 2026-10-08: form (b), `wasmhost run --invoke add m.wasm 2 3`; results one per line as `wasmtime run --invoke` prints them (from memory: the wasmtime docs were not reachable, check against a real `wasmtime`); a module without `_start` and without `--invoke` is an error, as in wasmtime) (rewritten 2026-10-07 on the owner's word; **part of the B-506 design**, deferred with phase 5; the form is **not decided**) Calling an exported function of a module without WASI
      (like our `fib` and `sum`: no `_start`, no arguments) from the command line. It does not need WASI itself, but it shares the command line with B-506, so the two are designed together.
      **Two forms, neither chosen:** (a) a subcommand, `wasmhost call myapp.wasm add 2 3`: one more reserved word next to `self`/`test`, and it looks like the WASI form, where everything after the module
      goes to the program; a module file named `call` would have to be run as `./call`; (b) an option of the host, as `wasmtime --invoke` has: `wasmhost --invoke add myapp.wasm 2 3`: no new reserved word,
      and it fits the B-506 rule that an option before the module belongs to the host. Either way the argument types come from `Module.exports`. Open: the form, what a module with `_start` does without
      the option (run as WASI, B-506), what one without prints (its exports?), the output format of results (multi-value), how an `i64`/`f32` argument is written.

Self-test: a WASI step (run a tiny WASI program: `fd_write` into stdout, `args`, a file in a temporary folder).

Out of scope: sockets, threads, interactive stdin, WASI preview2.

## Phase 6. New language features (L, depends on the engines)

- [ ] **B-601** Exceptions: `WebAssembly.Tag` and `WebAssembly.Exception` in the API (the self-test already knows the encoding).
- [ ] **B-602** SIMD (`v128`) and shared memory/threads: the JS engines have it, wasm3 does not.
- [ ] **B-603** `memory64` and multi-memory (Safari does not support multi-memory).
- [ ] **B-604** GC types.
- [ ] **B-605** Custom page sizes (the `custom-page-sizes` proposal: bit 3 of a memory's limits flags, a page of 2^k bytes instead of 64 KiB).
      No engine takes such a module here (checked 2026-10-07 with a module whose memory has the flag): `wasmtime` and `wasm3` refuse it,
      `node` ("invalid memory limits flags 0x8") and `bun` ("resizable limits flag are not valid") do not parse it, so it fails as a `CompileError`
      before any of our code matters. Today `_binary.parse` reads the flag and skips the page size (so a `MemoryType` would be counted in the wrong
      unit), and `limit_memory` (B-401) refuses such a memory with a `ValueError`. To support it: read the log2 of the page size into `MemoryType`,
      and let `limit_memory` turn the ceiling from bytes into the module's pages (`max_memory * 65536 >> k`); about ten lines, but it can't
      be tested until an engine runs such a module. Start only when one does (wasmtime-py turns the proposal on, JSC or V8 accepts it) or a real module needs it.

Each item goes through `supports(...)`; start only when a real module appears that needs it.
Self-test: the step "exception handling" already knows the encoding; add a check of `Tag`/`Exception` and a step each for SIMD,
shared memory, memory64 (and GC), each with `supports(...)`.

## Ideas, not planned

- **Numbers as `ctypes` types.** Currently `i32` and `i64` are `int`, and `f32` and `f64` are `float` (`i64` ↔ Python `int` corresponds to
  JS `BigInt`). The alternative: types in the spirit of `ctypes` (`c_int32`, `c_int64`, `c_float`) for arguments and results.
  To be considered later, only if we want to change the current behavior.
- **Memory through `ctypes`.** A window onto the engine's memory (a `memoryview`/`ctypes` array) on the native backends: related to
  B-301.

## New backends

- [x] **B-701a** Bun (`bun`): `BunBackend` on the same script and protocol as Node; self-test 32/32 and the whole test
      suite (140 passed, 7 skipped, as in Node) on Bun 1.4.2. CI: Linux and macOS (Windows not tried). README, the tables
      of backends and capabilities. Bun is JavaScriptCore, so it is useful as JSC on all OSes without GTK, closer to iOS.
- [ ] **B-701b** Deno (`deno`): through `deno eval` and the same shim (`deno eval -A`, the same `_NODE_LOOP`) passes
      32/33 (as of 2026-10-06, Deno 2.9.6 and 2.9.7, V8 15.0; the npm package `deno` lags behind, 2.9.7 exists only as a binary from GitHub Releases). **The cause was found, it is a bug
      in Deno itself, not in our code:** the step "call cost" merely finishes off an already dead process (`BrokenPipeError`); Deno
      crashes with a Rust panic (`capacity overflow`, "Deno has panicked") on the step "exception handling". A minimal
      reproduction without wasmhost: a module that throws `WebAssembly.Exception`, and `vm.runInContext(src, ctx)` (`import vm
      from 'node:vm'`), where `src` does not catch the exception, that is, it escapes outward from `runInContext`; the same call with
      `try/catch` inside `src`, or outside `vm`, works. Our `uncaught()` in the self-test escapes outward exactly like this.
      Options: (a) report it to denoland/deno and wait; (b) work around it: run `src` in `vm` so that the exception is
      caught inside the context (a `try{...}catch(e){...}` wrapper around the source code, converting to the same
      `{err: ...}`), which needs a check on Node and Bun, because it changes the way of running for all. As a separate
      observation: the old exception encoding (`try/catch`) is accepted by an ordinary Deno script (`caught 42`), while the self-test
      through our `vm` context says "older: no", so the answer differs between contexts in Deno; find out why.
      **Owner decision: we do not support Deno for now** (recorded in the README, section "Backends"). We do not add it as a backend until (b) exists: Deno is V8, already covered by Node, so it is not a priority.
- [ ] **B-701c** Ideas without verification: a pure-Python interpreter (`pywasm` on PyPI, version 2.2.3, updated 2026-05; **not to be
      confused with `pywasm3`**: that is a binding to the C engine wasm3, which is already our backend `wasm3`) as a fallback backend where
      nothing else exists (slow, but without C extensions); WAMR (WebAssembly Micro Runtime) through `ctypes`; WKWebView on iOS (an engine with JIT, unlike
      `JSContext` in a third-party app; host functions are more complex because the JS→Python bridge is asynchronous).

## Phase 7. Platforms (deferred)

- [ ] **B-701** A custom JS engine for Android and wheels for Android. Not verified, there are no mentions in the repository.

## Phase 8. Release and ecosystem

- [x] **B-801** (DONE 2026-10-07, after merge and publication) The example PR is merged and the next version is released; the repository is on tag `v0.1.0b2`.
- [x] **B-802** `tiny-bclibc-wasm` was verified and works (per the owner, outside this repository).
- [ ] **B-803** The rest around `tiny-bclibc-wasm`: the jsc runner (branch `jsc-runner`), coverage in `tiny`, documentation.
      The state is not verified.

## Examples: a shell for Pythonista (researched 2026-10-06)

- [x] **B-509** (DONE 2026-10-07) A shell for Pythonista on wasmhost: `examples/wasi_sh.py` and `tests/test_wasi_sh_example.py`
      (12 tests) in the branch `examples/zigcc`. The owner confirmed that it works in Pythonista, including the `--home` mode.
      This is BusyBox `ash` from the project `alganet/wasi-sh` v0.11.0 (`busybox.wasm` 376 KB from an npm tarball, SHA-256 is verified;
      GPL-2.0 in the binary, ISC in the shim): a `class Wasi` with 27 preview1 calls and 9 `env.__host_*`, a file system
      `Vfs` on a dictionary (the default) or `RealVfs` over a real folder; the flags `--home`, `--root DIR`,
      `--readonly`. On Windows there is no `os.pread`/`pwrite`: there reading and writing go through `lseek`.
      Limitations of the shell itself: no `( ... )`, `&`, `sleep`, `tee`, `yes`, `ln`, `chmod`; a nested `sh FILE` ends the
      session (`. FILE`); `ls` without sorting (the script reverses the order of `fd_readdir`).
      Rejected after checking: the **dash reactor** (`aperturerobotics/go-dash-wasi-reactor`, the exports `dash_init`,
      `dash_eval` are real) imports `env.__setjmp`/`__longjmp`, designed for wazero; on the JS engines and wasmtime
      only `echo`/variables/loops work, there are no pipes, `$(...)`, here-doc, redirections, `exit N` loses the status.
      **wasi-shell/wasibox** (oligamiq's crates) is not confirmed (the docs are unavailable). `wasmsh` was cancelled by the owner.

## Known problems of the examples (not the API)

- `examples/wasm/lua.wasm` was built without `longjmp`: any script error (syntax, `error()`, `pcall`) kills the
  interpreter. The cure is a different build of Lua.
- `coreutils.wasm` runs on `wasm3` too (checked 2026-10-07: `tests/test_coreutils_example.py` passes there; an older note and test skip said it didn't).
- `coreutils.py` works on a device (the owner, Pythonista, `jscontext`, 2026-10-07); the remaining features of `coreutils.wasm` are accepted there.
- `examples/wasmclang.py` does not run on Bun 1.4.2: Bun aborts ("this indicates a bug in Bun") while it compiles the WASI program, and did so before this session too. It runs on `wasmtime`, `wasm3`, `node` and, on the device, `jscontext`.
- `coreutils.wasm` weighs 10.8 MB (see B-004).

## Known flaky CI failures (not understood, not fixed; the owner chose to treat it as a flake, 2026-10-07)

- **A segmentation fault on `macos-latest / pypy3.11`, in the step `pytest --wasm-backend wasmtime`.** Run 37613538125
  (`main`, `7f3c548`, only `publish.yml` changed since a green run). The process died in
  `tests/test_jscontext.py::test_the_api_through_it[rubicon]`, in a ctypes call `JSValueToStringCopy` (`_capi.py`, `_text`, called from
  `evaluate`), and CI stopped there. The same commit on another run (37613713803, branch `v0.1.0b1`) was green, and in the
  failed job the `jsc` step, which runs the same tests, passed (188 passed). Seen once.
  - It is PyPy: the traceback shows `.venv/lib/pypy3.11` and `pypy-3.11.16-macos-aarch64`, so it is not a venv
    that fell back to CPython.
  - Guesses (neither checked on macOS): (1) a JavaScriptCore value held only as a Python int between `JSEvaluateScript` and
    `JSValueProtect` (see the comment in `CApi.evaluate`: PyPy moves and drops objects on its own schedule), the window could be
    narrowed by protecting before `JSStringRelease(script)`; (2) in this step a wasmtime engine already lives in the process, and
    wasmtime installs process-wide signal handlers (see `tests/conftest.py`), which may disturb JavaScriptCore.
  - Not reproduced on Linux (PyPy 3.10.16, x86-64, WebKitGTK JavaScriptCore 2.52.6): 40 runs of `test_jscontext.py`
    and 2 whole-suite runs under `--wasm-backend wasmtime` all passed. That says only that it does not show there; macOS, arm64
    and PyPy 3.11 were not tried.
  - If it comes back: re-run the job once; a second failure is a real one. Then try the guesses above on a macOS runner.
  - **It came back (2026-10-07, branch `claude/kind-goldberg-3dsi27`): three times in the eleven runs 124 to 134.** Run 124 (`b774846`): the steps `wasmtime` and `jsc` failed; the segfault shown is in the `jsc` step, in `tests/test_coreutils_example.py::
    test_lua_runs_as_a_program_of_the_shell[jsc]`, in the ctypes call `JSValueToStringCopy` (`_capi.py`, `_text`, called from `evaluate`, here from a host function: `fd_write`). Run 131 (`6f782dc`) and run 132 (`c06ac9e`): the same job
    failed; for 132 the failing step is `pytest --wasm-backend wasmtime` (what the log's tail shows is the last step, which passed with 419 passed). The same job was green in the other runs (125, 127, 130, 133 and 134 among them). None of the three commits touched the code on that
    path. So "seen once" is out of date. The step output of the failed `wasmtime` step was not read (the tool gives only the tail of a log): reading it is the first thing to do.

## General definition of done

Code, tests in `tests/` on all backends that can do it (the rest are skipped with a reason), an **updated
self-test** (see the rule at the top), `pre-commit` green (pyright, ruff, pytest), the change reflected in the README and in
"Not yet", `supports(...)` honestly describes where it does not work.
