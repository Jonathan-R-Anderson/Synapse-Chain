#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CRATES = ROOT / "execution" / "src" / "crates"

for crate in ("primitives", "crypto", "encoding", "state", "zk", "transactions", "evm", "execution"):
    sys.path.insert(0, str(CRATES / crate / "src"))

from execution.contracts.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
