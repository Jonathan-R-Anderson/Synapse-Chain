from __future__ import annotations

from crypto import keccak256
from encoding import encode


EMPTY_CODE_HASH = keccak256(b"")
EMPTY_TRIE_ROOT = keccak256(encode(b""))
