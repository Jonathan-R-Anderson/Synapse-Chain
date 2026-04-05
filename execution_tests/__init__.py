from __future__ import annotations

import sys
from pathlib import Path
from pkgutil import extend_path


_ROOT = Path(__file__).resolve().parents[1] / "execution" / "src" / "crates"
for _crate in ("primitives", "crypto", "encoding", "state", "zk", "transactions", "evm", "execution"):
    _source = _ROOT / _crate / "src"
    if str(_source) not in sys.path:
        sys.path.insert(0, str(_source))

__path__ = extend_path(__path__, __name__)
_PACKAGE_SOURCE = _ROOT / "execution" / "src" / "execution_tests"
if str(_PACKAGE_SOURCE) not in __path__:
    __path__.append(str(_PACKAGE_SOURCE))
