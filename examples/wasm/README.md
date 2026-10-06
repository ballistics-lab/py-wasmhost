# Test modules

`coremark-minimal.wasm`: EEMBC CoreMark (Apache-2.0) built to WebAssembly by the wasm3 project, taken as it is from
[wasm3/pywasm3](https://github.com/wasm3/pywasm3/tree/fce65fd7fdffd8b6866a4a6f7612a0a5bc28b018/examples/wasm)
(MIT). It imports `env.clock_ms: () -> i64` and exports `run: () -> f32` and `memory`.

`coreutils.wasm`: uutils coreutils (Rust, MIT, https://github.com/uutils/coreutils, commit `f29e308`) built to WASI, as one
multi-call binary (`coreutils echo hi`, `coreutils sort file`). Its build set is uutils' `feat_wasm`, the utilities that
compile for `wasm32-wasip1`. Built with Rust 1.97:

    cargo build --release --target wasm32-wasip1 --no-default-features --features feat_wasm

It imports only `wasi_snapshot_preview1` (32 functions: `args_*`, `environ_*`, `fd_*`, `path_*`, `clock_time_get`,
`random_get`, `poll_oneoff`, `sched_yield`, `proc_exit`) and exports `memory` and `_start`. wasmhost has no WASI of its
own, so it needs a host that provides these: `examples/coreutils.py` is one, in Python. It runs on wasmtime and Node;
wasm3 stops it right after `args_get` (the build uses multi-value, reference types and bulk memory).
