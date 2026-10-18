"""Run wasmgit from a source tree, such as an unpacked ZIP of the repository, with nothing installed:

    python examples/git/git.py clone https://github.com/user/repo
    python examples/git/git.py -i            # a `git>` prompt, for Pythonista's play button

`git.wasm` and `git-real.wasm` are beside this file. The installed package is `wasmgit`; this is its launcher.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))
try:
    import wasmhost  # noqa: F401
except ImportError:  # an unpacked archive of the repository, where wasmhost is not installed
    sys.path.insert(0, os.path.join(HERE, "..", "..", "src"))

from wasmgit import main, run  # noqa: E402  # pyright: ignore[reportMissingImports] -- found through the path above

__all__ = ["main", "run"]

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
