// Runs wasmhost inside Pyodide (CPython in WebAssembly), on Node, with the repository mounted:
//
//   node tests/pyodide_run.mjs selftest   # python -m wasmhost self test --backend pyodide
//   node tests/pyodide_run.mjs pytest     # the whole suite on the pyodide backend (extra arguments go to pytest)
//
// Pyodide is the npm package (`npm install --global pyodide`, or `npm install pyodide` in the folder you run this
// from), or an unpacked release in $PYODIDE_DIR. Its packages (pytest) come from the CDN of the same version.
import { execSync } from "node:child_process";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const [mode = "selftest", ...rest] = process.argv.slice(2);
const dir = process.env.PYODIDE_DIR;

// The package from ./node_modules, or from the global ones (`npm install --global pyodide`).
function load() {
  if (dir) return createRequire(import.meta.url)(path.join(dir, "pyodide.js"));
  try {
    return createRequire(path.join(process.cwd(), "/"))("pyodide");
  } catch {
    const global = execSync("npm root --global", { encoding: "utf8" }).trim();
    return createRequire(path.join(global, "/"))("pyodide");
  }
}
const { loadPyodide, version } = load();
// The same cache as the examples: $WASMHOST_CACHE, else ~/.cache/wasmhost.
const cache = path.join(process.env.WASMHOST_CACHE ?? path.join(os.homedir(), ".cache", "wasmhost"), "pyodide-packages");
const options = dir ? { indexURL: dir } : {};
const py = await loadPyodide({
  ...options,
  packageCacheDir: cache, // else the wheels land in ./https:/ of the folder you run from
  packageBaseUrl: `https://cdn.jsdelivr.net/pyodide/v${version}/full/`,
});
py.mountNodeFS("/repo", repo);
py.globals.set("args", rest);

let code;
if (mode === "selftest") {
  code = `
import sys
sys.path.insert(0, "/repo/src")
from wasmhost import _cli
status = _cli.main(["self", "test", "--backend", "pyodide"])
`;
} else if (mode === "pytest") {
  await py.loadPackage(["pytest", "packaging"]);
  code = `
import os, pytest
os.chdir("/repo")
status = pytest.main(["-q", "-p", "no:cacheprovider", "--wasm-backend", "pyodide", "tests", *args.to_py()])
`;
} else {
  throw new Error(`unknown mode ${mode}: selftest or pytest`);
}
try {
  await py.runPythonAsync(code);
} catch (e) {
  console.error(e.message ?? e);
  process.exit(1);
}
process.exit(Number(py.globals.get("status")));
