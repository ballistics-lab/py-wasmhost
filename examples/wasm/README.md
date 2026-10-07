# Test modules

`coremark-minimal.wasm`: EEMBC CoreMark (Apache-2.0) built to WebAssembly by the wasm3 project, taken as it is from
[wasm3/pywasm3](https://github.com/wasm3/pywasm3/tree/fce65fd7fdffd8b6866a4a6f7612a0a5bc28b018/examples/wasm)
(MIT). It imports `env.clock_ms: () -> i64` and exports `run: () -> f32` and `memory`.

`coreutils.wasm`: uutils coreutils (Rust, MIT, https://github.com/uutils/coreutils, commit `f29e308`) built to WASI, as one
multi-call binary (`coreutils echo hi`, `coreutils sort file`). Its build set is uutils' `feat_wasm`, the utilities that
compile for `wasm32-wasip1`. Built with Rust 1.97:

    cargo build --release --target wasm32-wasip1 --no-default-features --features feat_wasm \
        --config "patch.crates-io.bytecount.path='/path/to/bytecount-patched'"

where `bytecount-patched` is bytecount 0.6.9 (used by `wc`) with its two `#[cfg(target_arch = "wasm32")]` and the
`target_arch = "wasm32"` of the `mod simd` condition in `src/lib.rs` made `all(target_arch = "wasm32", target_feature =
"simd128")`. As released, bytecount always calls its WebAssembly SIMD code on wasm32, which puts `simd128` into the
whole link-time-optimized module (172 functions with `v128` locals), and an engine without SIMD, such as the
WebKitGTK JavaScriptCore on CI, refuses the module. With the patch there is no SIMD in it; the module still needs
bulk memory, multi-value, reference types, sign extension and extended constants.

It imports only `wasi_snapshot_preview1` (32 functions: `args_*`, `environ_*`, `fd_*`, `path_*`, `clock_time_get`,
`random_get`, `poll_oneoff`, `sched_yield`, `proc_exit`) and exports `memory` and `_start`. wasmhost has no WASI of its
own, so it needs a host that provides these: `examples/coreutils.py` is one, in Python. It runs on wasmtime and Node;
wasm3 stops it right after `args_get` (the build uses multi-value, reference types and bulk memory).

`lua.wasm`: Lua 5.4.6 (MIT) built to WASI with wasienv, taken as it is from the npm package
[`@antonz/lua-wasi`](https://www.npmjs.com/package/@antonz/lua-wasi) (`dist/lua.wasm`, from
https://github.com/nalgeon/lua-wasi). It imports 19 functions of `wasi_unstable`, the first snapshot of WASI, and
exports `memory` and `_start`. `examples/coreutils.py` runs it as the shell's `lua`. It was built without `longjmp`: an
error in a script (a syntax error, `error()`, a failed `pcall`) ends it with a trap.
