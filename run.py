"""Entry point for both the hiss CLI and the session process it spawns.

The script's directory is sys.path[0], which makes the `hiss` package importable;
the session process swaps it for "" before running user code, as the REPL has.
"""
from hiss.cli import main

if __name__ == "__main__":
    main()
