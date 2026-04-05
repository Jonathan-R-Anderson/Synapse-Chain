from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent

for source_dir in (
    ROOT / "primitives" / "src",
    ROOT / "crypto" / "src",
    ROOT / "encoding" / "src",
    ROOT / "state" / "src",
    ROOT / "zk" / "src",
    ROOT / "transactions" / "src",
    ROOT / "evm" / "src",
    ROOT / "execution" / "src",
):
    sys.path.insert(0, str(source_dir))


def main() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    for crate in ("primitives", "crypto", "encoding", "state", "zk", "transactions", "evm", "execution"):
        suite.addTests(loader.discover(str(ROOT / crate / "tests")))

    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
