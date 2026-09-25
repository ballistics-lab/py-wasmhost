# Test modules

`coremark-minimal.wasm`: EEMBC CoreMark (Apache-2.0) built to WebAssembly by the wasm3 project, taken as it is from
[wasm3/pywasm3](https://github.com/wasm3/pywasm3/tree/fce65fd7fdffd8b6866a4a6f7612a0a5bc28b018/examples/wasm)
(MIT). It imports `env.clock_ms: () -> i64` and exports `run: () -> f32` and `memory`.
