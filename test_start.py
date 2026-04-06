from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import start


class StartI2PAddressTests(unittest.TestCase):
    def test_waits_until_destination_is_published(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            state_root = Path(tempdir)
            destination_file = state_root / "demo-full" / "i2p-destination.pub"
            destination_file.parent.mkdir(parents=True, exist_ok=True)

            def publish_destination() -> None:
                time.sleep(0.05)
                destination_file.write_text("example-destination.i2p", encoding="utf-8")

            publisher = threading.Thread(target=publish_destination)
            publisher.start()
            with mock.patch.object(
                start,
                "_compose_service_statuses",
                return_value={"execution-full": {"Service": "execution-full", "State": "running", "ExitCode": 0}},
            ):
                records = start._collect_i2p_records(
                    "full",
                    env={
                        "EXECUTION_PRIVACY_NETWORK": "i2p",
                        "EXECUTION_DATA_DIR": str(state_root),
                    },
                )
            publisher.join()

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "available")

    def test_collects_available_i2p_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            state_root = Path(tempdir)
            destination_file = state_root / "demo-full" / "i2p-destination.pub"
            destination_file.parent.mkdir(parents=True, exist_ok=True)
            destination_file.write_text("example-destination.i2p", encoding="utf-8")

            records = start._collect_i2p_records(
                "full",
                env={
                    "EXECUTION_PRIVACY_NETWORK": "i2p",
                    "EXECUTION_DATA_DIR": str(state_root),
                },
                wait_seconds=0,
            )

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.target, "full")
        self.assertEqual(record.service, "execution-full")
        self.assertEqual(record.node_name, "demo-full")
        self.assertEqual(record.status, "available")
        self.assertEqual(record.address_file, destination_file)
        self.assertEqual(record.i2p_destination, "example-destination.i2p")
        self.assertEqual(record.i2p_endpoint, "i2p://example-destination.i2p")

    def test_marks_non_overlay_services_as_not_applicable(self) -> None:
        records = start._collect_i2p_records(
            "rpc",
            env={
                "EXECUTION_PRIVACY_NETWORK": "i2p",
                "EXECUTION_DATA_DIR": "/tmp/unused",
            },
            wait_seconds=0,
        )

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.target, "rpc")
        self.assertEqual(record.status, "not_applicable")
        self.assertIsNone(record.i2p_destination)

    def test_marks_i2p_reporting_disabled_when_privacy_network_is_not_i2p(self) -> None:
        records = start._collect_i2p_records(
            "full",
            env={
                "EXECUTION_PRIVACY_NETWORK": "plain",
                "EXECUTION_DATA_DIR": "/tmp/unused",
            },
            wait_seconds=0,
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "disabled")

    def test_timeout_reports_error_instead_of_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            state_root = Path(tempdir)
            destination_dir = state_root / "demo-full"
            destination_dir.mkdir(parents=True, exist_ok=True)

            with mock.patch.object(
                start,
                "_compose_service_statuses",
                return_value={"execution-full": {"Service": "execution-full", "State": "running", "ExitCode": 0}},
            ):
                records = start._collect_i2p_records(
                    "full",
                    env={
                        "EXECUTION_PRIVACY_NETWORK": "i2p",
                        "EXECUTION_DATA_DIR": str(state_root),
                    },
                    wait_seconds=0,
                )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "error")
        self.assertIn("Timed out after 0 seconds", records[0].reason or "")

    def test_reports_container_exit_as_i2p_error(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            state_root = Path(tempdir)
            destination_dir = state_root / "demo-full"
            destination_dir.mkdir(parents=True, exist_ok=True)

            with mock.patch.object(
                start,
                "_compose_service_statuses",
                return_value={"execution-full": {"Service": "execution-full", "State": "exited", "ExitCode": 2}},
            ):
                records = start._collect_i2p_records(
                    "full",
                    env={
                        "EXECUTION_PRIVACY_NETWORK": "i2p",
                        "EXECUTION_DATA_DIR": str(state_root),
                    },
                )

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.status, "error")
        self.assertIn("state exited", record.reason or "")
        self.assertIn("exit code 2", record.reason or "")


if __name__ == "__main__":
    unittest.main()
