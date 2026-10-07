# wasmhost backlog

A phased plan. The phases are ordered from what is cheap and needed by other items to what is expensive and depends on the engines.
Marks: `[x]` done, `[ ]` not done, `[~]` partly done. Size: **S** (up to a day), **M** (a few days),
**L** (a week or more). "Backends" says where this can actually be done; the rest honestly report `supports(...) == False`.

The state on `main` (commit `260f11c`) was checked against the code and `git log`. Whatever lies outside this repository was not checked here
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

## Current state (2026-10-07; `examples/zigcc` was merged into `main` by the owner and deleted, work goes on in `claude/loving-hawking-u966j0`)

**Done in the branch and verified** (pre-commit is green; the full `pytest` passes on wasmtime, jsc, node, bun, wasm3; CI is green on all platforms;
the self-test has 33 steps on wasmtime/wasm3 and 39 on the JS engines): B-201 (steps 1-4: `Function`, `signature`, `type()`, identity, `elem`, the
safety net through `call_indirect`), B-202 (the initial value of a table), B-204 (multi-value in a batch), B-301 (`Memory.view`), B-302 (`bench`
with buffer transfer), B-304 (a lock per backend, `await compile/instantiate`, threads only with `threaded=True`), B-507 (the `wasmhost` command),
B-509 (`wasi_sh.py`, `--home`), fast buffers (a typed array through the C API on jsc/jscontext, base64 on node/bun), Bun as a backend in CI, tests for
`pyodide.py` and `wasi_sh.py`. **Verified by the owner on the device (Pythonista, iPhone 16, iOS 26, `jscontext`):** self-test 36/36 (38/38 after B-401, see there), buffers intact
from 1 byte to 8 MiB at 5400/12700 MB/s (1 MiB write/read), `await compile/instantiate`, multi-value in a batch, `wasi_sh --home`, `bench`.

**Deferred by owner decision:** B-304a (examples on asyncio; the shape of the `pyodide.py` API is not settled: add `async` methods alongside (the
proposal) or convert it fully), WASI in the library itself (phase 5), Deno (B-701b: a bug in Deno itself).

**Waiting for the owner before the PR:** the branch name and tidying (B-003), the release (B-801); `coreutils.wasm` stays in git (B-004, owner
decision, the history has two copies, about 21 MB: a squash would leave one); `tiny-bclibc-wasm` (B-803).

**Not covered by tests:** `examples/wasmclang.py`, `jslinux.py` (unavailable in the assistant's environment: `bellard.org` is blocked), `coremark.py`;
the "Examples" step in CI runs only `basic.py` and `imports.py` (B-006). B-302a is only partly done (see its entry).

**Next candidates:** B-203 (`externref`), a binary channel for node/bun (about 50 MB/s through the pipe and JSON now), phase 4 is done but for fuel (B-401 the memory ceiling, B-402/B-403 the timeout and its tests, where an engine can: none on iOS). Fact (confirmed by the owner, matches the measurements): `jscontext` on iOS has **no JIT**, so for pure computation `wasm3` would be faster than
JavaScriptCore there, but `pywasm3` cannot be installed on iOS (a C extension).

## Order of work

Owner decision: everything is done in the current branch (the owner opens the PR and tidies the branch), and `B-304` (async) goes **after** `B-201`. Each step comes with tests, a step in the
self-test and a green pre-commit; each is a separate commit.

1. ✅ **B-201, step 1** (done; `tests/test_functions.py`, the `functions` step in the self-test): `Function` with `signature`, `type()`, a cache keyed by the function, `call_ref` in three backends,
   `FuncRef` is removed, `Table.get/set` on `Function`, a call without a signature resolves it lazily.
2. ✅ **B-201, step 2** (done, `CallStep` carries the handle, not the name): `Batch` on `handle`, a single call path.
3. ✅ **B-201, step 3** (done: `ModuleInfo.elems`, `_learn_table_entries`, `tests/test_elem_signatures.py`; the offset through `global.get` of an imported global and the form with `ref.func` expressions are both covered, there is a step in the self-test; not covered: passive segments and `table.init`, where in principle only a manual `signature` works): a snapshot by function identity (auto-resolution by `elem` on the JS engines).
4. ✅ **B-201, step 4** (done: `_trampoline.py`, `supports("table.signatures")` in wasmtime, the test `test_a_wrong_signature_is_refused_not_obeyed`, a step in the self-test; overhead: ~+12 µs on jsc, within noise on node/bun, because the trampoline caches the last function in the slot; the plan was: ① in `src/wasmhost/` a new `_trampoline.py` with a wasm generator: a module
   that imports `env.t` (a `funcref` table, 1 slot) and exports `call(f)`, whose body is `call_indirect (type N)`; the type N is
   the signature from `FuncType`, with a module cache keyed by `FuncType`; a prototype is in `tests/wasm_builder.py` (`tables`/`call_indirect`);
   ② in `_api.py::Function.__call__`, when `self._instance is None` (a table entry, not an export) and `signature` is set, and
   the backend lacks `supports("table.signatures")`: `scratch.set(0, self)` into a service table and a call of this trampoline's `call`;
   the "signature mismatch" trap (`Trap`) is turned into `TypeError("функція не має підпису ...")`;
   ③ exports and wasmtime stay direct calls; ④ `supports("table.signatures")` in `_native.py`/`_js.py`;
   ⑤ tests: a function of another type in a slot with an annotation on jsc/node/bun, on wasmtime without the trampoline; a step in the self-test;
   remove from the README the caveat about a wrong annotation.) It was: a safety net through `call_indirect` (a wasm generator, the type is checked by the engine).
5. ✅ **B-304** (done, a thread only with `threaded=True`, no threads by default: `Backend._lock`; `compile`/`instantiate` as `async`, `instantiate_sync`, `supports("threads")` in wasmtime/node/bun, `tests/test_async.py`, a step in the self-test; source: async) (`async def compile/instantiate`, `instantiate_sync`, no races).
6. ✅ **B-204** (a `Ref` tuple for multi-value in `Batch`), ✅ **B-507** (the `wasmhost` command), ✅ **B-509** (`examples/wasi_sh.py`).
7. ✅ **B-202**, ✅ **B-301**, ✅ **B-302**. Next: B-203, phase 4 (restrictions for untrusted code).

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
- [~] Examples in the branch `examples/zigcc` (not in `main`): `wasmclang.py` (clang/lld in wasm, C and C++),
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
- [ ] **B-302a** (PARTLY DONE 2026-10-07: the `bench` command, the measurements below and the buffer measurement of B-302 are done; still open: the
      `AUTO_ORDER` comment about the missing JIT on `jscontext` (the README says it already), the rows of other iPhones, and what is listed as
      not measured at the end) The results of comparing the backends (measured 2026-10-06, Linux, CPython 3.11; the best of three series) and what to
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
      the advantage of `wasm3` there is only an estimate. Conclusion: where JIT is unavailable, `wasm3` wins several times over, but it cannot do multi-value, reference types, bulk memory
      (coreutils.wasm does not run on it). An idea: mention this difference (there is no JIT on `jscontext`) in `AUTO_ORDER` and the README;
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
- [ ] **B-303** Batch `read/write` for many regions in one call (fewer engine↔Python transitions).
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
      `Module` keeps its bytes and `Module._variant(pages, timed)` (it was `_held_to`) compiles the changed copy once per ceiling (a module that needs no change is the module
      itself, nothing is compiled); `type()` of an exported memory says the maximum that holds. A memory the module imports is checked against the
      ceiling: no maximum, or a larger one, is a `LinkError`. Tests: `tests/test_max_memory.py`.
      Limits of this: only memories; no time or fuel (B-402); a bigger-than-needed ceiling costs nothing, but a module compiled with a custom page
      size is not supported. **Verified by the owner on the device (Pythonista, iPhone 16, iOS 26, `jscontext`, 2026-10-07): self-test 38/38**, both steps
      run (not "not available"), a call 36 us, a batch of 3 83 us. CI was green on all platforms (run 37616628414).
- [x] **B-402** (DONE 2026-10-07, on the owner's word; CI green on every platform, run 37626340443; not run on a device: there `supports("timeout")` is false and the step says so; self-test step "timeout: Instance(timeout=) stops an endless loop, where the engine can", `tests/test_timeout.py`)
      A timeout: `Instance(module, imports, timeout=seconds)`, also `instantiate` and `instantiate_sync`; a call that runs longer ends with `wasmhost.Timeout`
      (a `Trap`), and the instance can be called again. Wall-clock time, per call (a batch is one call), the start function included.
      Measured before building (2026-10-07, Linux): **wasmtime** epochs stop a loop (0.30 s for 0.3 s asked) but a tight loop runs about 3 times slower (0.032 -> 0.105 s for
      1e8 iterations), so the epoch engine is a second `Engine`, made only for instances that ask (`Backend.compile_timed`, `Module._variant`: the module is
      compiled for it once and kept), and such an instance lives in a store of its own (it can't share a `Memory`, `Table` or `Global` made outside it); a daemon watchdog thread
      (`_Watchdog`) per backend, made when first needed, calls `increment_epoch`. **node**: `vm.runInContext(src, ctx, {timeout})` stops a wasm loop (0.33 s),
      the context is fine afterwards; the timeout goes with the request, and is kept for an instance, its exported functions and tables (and what is taken from them),
      so a function that is not one of those is not timed; a `Timeout` in a batch drops the results of the steps before it (the termination can't be caught in the script).
      **Not possible, and `supports("timeout")` is false:** **bun** (its `vm` timeout does not stop a wasm loop: the process hangs, had to be killed); **JavaScriptCore**
      (`jscontext`, `jsc`, `gi-jsc`: `JSContextGroupSetExecutionTimeLimit` is exported and stops a JavaScript loop in 0.30 s, but a wasm loop runs on: probed with
      `libjavascriptcoregtk` 2.52.6 on Linux; not probed on a device, but the owner's view that JSC has no interruption holds), so **on iOS there is no time limit**; **wasm3** (no gas or
      interruption in pywasm3). A backend without it raises `NotImplementedError` from `Instance(timeout=)`, and the self-test step does not run the loop there.
      Open: no fuel (a count of instructions, deterministic, no thread: wasmtime has `consume_fuel`, same cost issue as epochs); not verified on macOS/Windows or a device (CI will say).
      Found on the way: a trap in the start function leaked out of wasmtime as its own `Trap` (fixed: `Trap` is not a `WasmtimeError`); `wasm3` does not run the start function when the
      instance is made, but at the first call.
- [x] **B-403** (DONE 2026-10-07, with B-402: `tests/test_timeout.py`, 36 tests) Tests "a module with an infinite loop" on every backend that can do this:
      `wasmtime` and `node` stop it; the others are skipped with the reason, and `test_a_backend_that_cannot_says_so` checks that they refuse honestly.

Risks: JSC has no interruption, so on iOS this is not guaranteed; a separate note is needed about what the sandbox does not
promise.
Self-test: the steps "memory limit" and "interruption of an infinite loop" with `supports("timeout")`; on JSC they say
"not supported" instead of failing.

Done when: an infinite loop is interrupted with a `Trap`-like error where the engine supports it.

## Phase 5. WASI (deferred by owner decision, L)

Decision: "WASI for later". Touch nothing without a command. Preparatory material already exists in the branch `examples/zigcc`:
the class `Wasi` in `examples/coreutils.py` (over 40 calls, both snapshots `wasi_snapshot_preview1` and `wasi_unstable`,
files in a real folder, stdio, arguments, a clock, `sleep`), tests on wasmtime and node.

- [ ] **B-501** Decide the form: an optional module `wasmhost.wasi` or a separate package `wasmhost-wasi`.
- [ ] **B-502** Move `Wasi` from the example, with an API along the lines of `Wasi(args=…, preopens={"/": dir}, stdin=…, stdout=…)`.
- [ ] **B-503** A sandbox through `openat` with `dir_fd`, to remove the gap between checking the path and opening
      (right now `resolve()` checks, then `os.open`).
- [ ] **B-504** Several preopen folders and a "read-only" mode.
- [ ] **B-505** Tests on all backends; `wasm3` does not take Rust builds (multi-value, reference types), this goes into `supports`.
- [ ] **B-506** A command to run a module along the lines of `wasmtime myapp.wasm -- arg1 arg2 --verbose`. The grammar:
      `wasmhost [ОПЦІЇ ХОСТА] myapp.wasm [-- АРГУМЕНТИ ПРОГРАМИ]`: the options before the module belong to the host (`--backend`,
      `--dir ХОСТ::ГІСТЬ`, `--env K=V`), everything after the module (and after `--`) goes unparsed to the program through
      `args_get`. The command names (`self`/`test`) are checked first, everything else is treated as a module file; a file with
      the name of a command is run as `./self`. The subcommand `self test` is the canonical form. Depends on B-502.
- [x] **B-507** (DONE: `[project.scripts]`, `tests/test_cli_entry.py`; we will extend it when running a module appears, B-506) The entry point `wasmhost`: `[project.scripts] wasmhost = "wasmhost._cli:main"` in `pyproject.toml`, so that one can
      write `wasmhost myapp.wasm` and `wasmhost self test`, and not `python -m wasmhost`. Right now there is no such command.
      Check `uv lock --check` and the wheels.
- [ ] **B-508** Running a module without WASI (like our `fib` and `sum`): it has no `_start` and no arguments, so a separate
      subcommand with a call of an exported function, for example `wasmhost call myapp.wasm add 2 3` (the types from
      `Module.exports`). This is an idea, not a decision: discuss before doing it.

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

- [ ] **B-801** Merge the PR with the examples (see B-003), release the next version.
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
- `coreutils.wasm` does not run on `wasm3` (multi-value, reference types, bulk memory).
- Whether JavaScriptCore on your iOS accepts the remaining features of `coreutils.wasm` has not been verified on a device.
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

## General definition of done

Code, tests in `tests/` on all backends that can do it (the rest are skipped with a reason), an **updated
self-test** (see the rule at the top), `pre-commit` green (pyright, ruff, pytest), the change reflected in the README and in
"Not yet", `supports(...)` honestly describes where it does not work.
