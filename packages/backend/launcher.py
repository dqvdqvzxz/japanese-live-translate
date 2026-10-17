"""Frozen-build entry point.

PyInstaller executes its entry script as a top-level module, so `jlt.__main__`
cannot be used directly — its relative imports have no parent package. This
imports the package absolutely instead.
"""

from jlt.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
