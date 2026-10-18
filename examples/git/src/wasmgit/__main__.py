import sys

from ._core import main


def main_cli():
    """The `wasmgit` command: console_scripts passes nothing, so take the arguments here."""
    return main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main_cli())
