from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


TESTS_ROOT = Path(__file__).resolve().parent
EXECUTION_ROOT = TESTS_ROOT.parent
EXAMPLES_ROOT = EXECUTION_ROOT / "examples"
for path in (str(TESTS_ROOT), str(EXAMPLES_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)
CRATES = EXECUTION_ROOT.parent
for crate in ("execution", "evm", "transactions", "zk", "state", "encoding", "crypto", "primitives"):
    crate_src = str(CRATES / crate / "src")
    if crate_src not in sys.path:
        sys.path.insert(0, crate_src)

from demo_support import load_config


class DemoSupportEnvironmentOverrideTests(unittest.TestCase):
    def test_load_config_applies_chain_id_state_root_and_peer_overrides(self) -> None:
        config_path = EXAMPLES_ROOT / "configs" / "full_node.json"
        with patch.dict(
            os.environ,
            {
                "EXECUTION_STATE_ROOT": "/tmp/official-execution",
                "EXECUTION_CHAIN_ID": "0x539",
                "EXECUTION_BOOTNODES": "bootnode-1.example.org:30303, bootnode-2.example.org:30303",
                "EXECUTION_STATIC_PEERS": "peer-1.example.org:30303,peer-2.example.org:30303",
            },
            clear=False,
        ):
            config = load_config(config_path)

        self.assertEqual(config.state_directory, Path("/tmp/official-execution/demo-full"))
        self.assertEqual(config.database_path, Path("/tmp/official-execution/demo-full/sync.sqlite3"))
        self.assertEqual(config.chain_config.chain_id, 1337)
        self.assertEqual(
            config.bootnodes,
            ("bootnode-1.example.org:30303", "bootnode-2.example.org:30303"),
        )
        self.assertEqual(
            config.static_peers,
            ("peer-1.example.org:30303", "peer-2.example.org:30303"),
        )


if __name__ == "__main__":
    unittest.main()
