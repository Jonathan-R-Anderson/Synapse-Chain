from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


TESTS_ROOT = Path(__file__).resolve().parent
EXECUTION_ROOT = TESTS_ROOT.parent
CRATES = EXECUTION_ROOT.parent
for crate in ("execution", "evm", "transactions", "zk", "state", "encoding", "crypto", "primitives"):
    crate_src = str(CRATES / crate / "src")
    if crate_src not in sys.path:
        sys.path.insert(0, crate_src)

from execution.sync import NodeConfig, NodeType, PeerInfo, SyncMode, capabilities_for_roles
from execution.sync.networking.i2p import (
    I2POverlayConfig,
    advertised_i2p_endpoint,
    configured_bootstrap_references,
    is_i2p_destination,
    normalize_i2p_destination,
    unique_peers,
)


class I2POverlayHelperTests(unittest.TestCase):
    def test_destination_helpers_normalize_and_detect_i2p_endpoints(self) -> None:
        self.assertEqual(normalize_i2p_destination("i2p://peer.example.i2p"), "peer.example.i2p")
        self.assertEqual(advertised_i2p_endpoint("peer.example.i2p"), "i2p://peer.example.i2p")
        self.assertTrue(is_i2p_destination("i2p://peer.example.i2p"))
        self.assertTrue(is_i2p_destination("peer.example.i2p"))
        self.assertFalse(is_i2p_destination("execution-bootnode"))

    def test_bootstrap_reference_loader_filters_to_real_i2p_values(self) -> None:
        config = NodeConfig(
            node_name="demo-full",
            node_types=frozenset({NodeType.FULL}),
            sync_mode=SyncMode.FULL,
            bootnodes=("execution-bootnode", "i2p://boot-1.example.i2p"),
            static_peers=("peer-1.example.i2p",),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            bootstrap_file = Path(tmpdir) / "bootstrap.txt"
            bootstrap_file.write_text(
                "\n".join(
                    [
                        "i2p://boot-2.example.i2p",
                        "execution-bootnode",
                        "boot-3.example.i2p",
                    ]
                ),
                encoding="utf-8",
            )
            overlay_config = I2POverlayConfig(
                sam_host="127.0.0.1",
                sam_port=7656,
                timeout_seconds=1.0,
                signature_type=7,
                inbound_quantity=2,
                outbound_quantity=2,
                bootstrap_file=bootstrap_file,
                publish_destination=False,
                bootstrap_wait_seconds=0.1,
            )
            self.assertEqual(
                configured_bootstrap_references(config, overlay_config),
                (
                    "i2p://boot-1.example.i2p",
                    "peer-1.example.i2p",
                    "i2p://boot-2.example.i2p",
                    "boot-3.example.i2p",
                ),
            )

    def test_unique_peers_keeps_latest_peer_by_id(self) -> None:
        capabilities = capabilities_for_roles(frozenset({NodeType.FULL}), serve_state=True, serve_blocks=True)
        peer_a = PeerInfo(peer_id="peer-a", endpoint="i2p://old.example.i2p", capabilities=capabilities)
        peer_b = PeerInfo(peer_id="peer-a", endpoint="i2p://new.example.i2p", capabilities=capabilities, score=4.0)
        peer_c = PeerInfo(peer_id="peer-c", endpoint="i2p://peer-c.example.i2p", capabilities=capabilities)
        result = unique_peers((peer_a, peer_b, peer_c))
        self.assertEqual(len(result), 2)
        indexed = {peer.peer_id: peer for peer in result}
        self.assertEqual(indexed["peer-a"].endpoint, "i2p://new.example.i2p")
        self.assertEqual(indexed["peer-c"].endpoint, "i2p://peer-c.example.i2p")


if __name__ == "__main__":
    unittest.main()
