"""Build hook: the .wasm modules are not in the repository's package, they are built into src/wasmgit/.

All metadata is in pyproject.toml. When src/wasmgit/ has no git.wasm or git-real.wasm yet, build_py runs build.sh and
real/build.sh, which download libgit2 and git (checking their SHA-256) and compile them with `zig cc` (the `ziglang`
package of the build requirements). They need `sh`, cmake, ninja, make, curl and tar, so a wheel is built on Linux or
macOS; the CI workflow does that, and the wheel (py3-none-any) works everywhere wasmhost does.
"""

import os
import subprocess
import sys

from setuptools import setup  # pyright: ignore[reportMissingModuleSource] -- the build environment has it
from setuptools.command.build_py import build_py  # pyright: ignore[reportMissingModuleSource]

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "src", "wasmgit")
MODULES = (("git.wasm", "build.sh"), ("git-real.wasm", os.path.join("real", "build.sh")))


class BuildPyWithWasm(build_py):
    def run(self):
        env = dict(os.environ, OUT=OUT, ZIG=f"{sys.executable} -m ziglang")
        for module, script in MODULES:
            if not os.path.exists(os.path.join(OUT, module)):
                try:
                    subprocess.run(["sh", os.path.join(HERE, script)], check=True, env=env, cwd=HERE)
                except (OSError, subprocess.CalledProcessError) as exc:
                    raise SystemExit(
                        f"cannot build {module} ({exc}); it needs sh, cmake, ninja, make, curl and tar. "
                        "Put a built module in src/wasmgit/ to skip this."
                    ) from exc
        super().run()


setup(cmdclass={"build_py": BuildPyWithWasm})
