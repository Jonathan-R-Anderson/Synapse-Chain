from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from demo_support import configure_logging, run_demo


MODE_TO_CONFIG = {
    "full": "full_node.json",
    "light": "light_node.json",
    "archive": "archive_node.json",
    "bootnode": "bootnode.json",
    "state-provider": "state_provider.json",
    "validator": "validator_node.json",
}


async def _hold_open(mode: str, interval: int) -> None:
    while True:
        print(f"execution node container for mode={mode} remains active", flush=True)
        await asyncio.sleep(interval)


async def _async_main(args: argparse.Namespace) -> int:
    config_path = Path(__file__).resolve().parent / "configs" / MODE_TO_CONFIG[args.mode]
    await run_demo(config_path)
    if args.keep_alive:
        await _hold_open(args.mode, args.status_interval)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Container entry point for execution demo node modes.")
    parser.add_argument("--mode", choices=tuple(MODE_TO_CONFIG), required=True)
    parser.add_argument(
        "--keep-alive",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep the container running after the node reaches steady state.",
    )
    parser.add_argument(
        "--status-interval",
        type=int,
        default=int(os.environ.get("EXECUTION_NODE_STATUS_INTERVAL", "300")),
        help="Seconds between keep-alive status messages.",
    )
    args = parser.parse_args(argv)
    configure_logging()
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
