#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
I2P_ADDRESS_FILENAME = "i2p-destination.pub"

SERVICE_MAP = {
    "i2p": "i2p-router",
    "rpc": "execution-rpc",
    "full": "execution-full",
    "light": "execution-light",
    "archive": "execution-archive",
    "bootnode": "execution-bootnode",
    "state-provider": "execution-state-provider",
    "validator": "execution-validator",
    "consensus": "consensus-sim",
}

LONG_RUNNING_TARGETS = ("i2p", "rpc", "full", "light", "archive", "bootnode", "state-provider", "validator")
NODE_CONFIGS = {
    "full": ROOT / "execution/src/crates/execution/examples/configs/full_node.json",
    "light": ROOT / "execution/src/crates/execution/examples/configs/light_node.json",
    "archive": ROOT / "execution/src/crates/execution/examples/configs/archive_node.json",
    "bootnode": ROOT / "execution/src/crates/execution/examples/configs/bootnode.json",
    "state-provider": ROOT / "execution/src/crates/execution/examples/configs/state_provider.json",
    "validator": ROOT / "execution/src/crates/execution/examples/configs/validator_node.json",
}


@dataclass(frozen=True, slots=True)
class CommandPlan:
    argv: list[str]
    cwd: Path


@dataclass(frozen=True, slots=True)
class I2PAddressRecord:
    target: str
    service: str
    status: str
    reason: str | None = None
    node_name: str | None = None
    address_file: Path | None = None
    i2p_destination: str | None = None
    i2p_endpoint: str | None = None

    def to_dict(self) -> dict[str, object | None]:
        return {
            "target": self.target,
            "service": self.service,
            "status": self.status,
            "reason": self.reason,
            "node_name": self.node_name,
            "address_file": str(self.address_file) if self.address_file is not None else None,
            "i2p_destination": self.i2p_destination,
            "i2p_endpoint": self.i2p_endpoint,
        }


def _print_command(argv: list[str]) -> None:
    print(" ".join(shlex.quote(part) for part in argv), flush=True)


def _run(argv: list[str], *, dry_run: bool) -> int:
    _print_command(argv)
    if dry_run:
        return 0
    completed = subprocess.run(argv, cwd=ROOT)
    return int(completed.returncode)


def _compose_base() -> list[str]:
    return ["docker", "compose"]


def _build_up_plan(service: str, *, build: bool, detach: bool, profile: str | None = None) -> CommandPlan:
    argv = _compose_base()
    if profile is not None:
        argv.extend(["--profile", profile])
    argv.append("up")
    if build:
        argv.append("--build")
    if detach:
        argv.append("-d")
    argv.append(service)
    return CommandPlan(argv=argv, cwd=ROOT)


def _build_run_plan(service: str, *, build: bool, profile: str | None = None) -> CommandPlan:
    argv = _compose_base()
    if profile is not None:
        argv.extend(["--profile", profile])
    argv.extend(["run", "--rm"])
    if build:
        argv.append("--build")
    argv.append(service)
    return CommandPlan(argv=argv, cwd=ROOT)


def _build_stop_plan(services: list[str], *, remove: bool) -> CommandPlan:
    argv = _compose_base()
    if remove:
        argv.extend(["rm", "-f", "-s"])
        argv.extend(services)
    else:
        argv.append("stop")
        argv.extend(services)
    return CommandPlan(argv=argv, cwd=ROOT)


def _build_logs_plan(services: list[str], *, follow: bool, tail: int) -> CommandPlan:
    argv = _compose_base()
    argv.extend(["logs", f"--tail={tail}"])
    if follow:
        argv.append("-f")
    argv.extend(services)
    return CommandPlan(argv=argv, cwd=ROOT)


def _resolve_targets(target: str) -> list[str]:
    if target == "all":
        return [SERVICE_MAP[name] for name in LONG_RUNNING_TARGETS]
    return [SERVICE_MAP[target]]


def _load_env_file(path: Path = ENV_PATH) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in raw_line:
            continue
        name, value = raw_line.split("=", 1)
        cleaned_name = name.strip()
        if not cleaned_name or cleaned_name.startswith("#"):
            continue
        values[cleaned_name] = value.strip().strip("'\"")
    return values


def _resolve_env_setting(name: str, *, env: dict[str, str] | None = None) -> str | None:
    if env is not None and name in env and env[name].strip():
        return env[name].strip()
    from_process = os.environ.get(name, "").strip()
    if from_process:
        return from_process
    return _load_env_file().get(name)


def _resolve_execution_data_dir(*, env: dict[str, str] | None = None) -> Path:
    configured = _resolve_env_setting("EXECUTION_DATA_DIR", env=env) or "./data/execution"
    path = Path(configured)
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def _resolve_i2p_wait_seconds(*, env: dict[str, str] | None = None, override: float | None = None) -> float | None:
    del env
    if override is None:
        return None
    return max(0.0, override)


def _privacy_network(*, env: dict[str, str] | None = None) -> str:
    return (_resolve_env_setting("EXECUTION_PRIVACY_NETWORK", env=env) or "plain").strip().lower()


def _node_name_for_target(target: str) -> str | None:
    config_path = NODE_CONFIGS.get(target)
    if config_path is None:
        return None
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    node_name = payload.get("node_name")
    if not isinstance(node_name, str) or not node_name.strip():
        raise ValueError(f"missing node_name in {config_path}")
    return node_name.strip()


def _address_file_for_target(target: str, *, env: dict[str, str] | None = None) -> Path | None:
    node_name = _node_name_for_target(target)
    if node_name is None:
        return None
    return _resolve_execution_data_dir(env=env) / node_name / I2P_ADDRESS_FILENAME


def _available_target_names(target: str) -> list[str]:
    if target == "all":
        return list(LONG_RUNNING_TARGETS)
    return [target]


def _compose_service_statuses(targets: list[str]) -> dict[str, dict[str, object]] | None:
    if not targets:
        return {}
    completed = subprocess.run(
        _compose_base() + ["ps", "--all", "--format", "json", *[SERVICE_MAP[item] for item in targets]],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    raw = completed.stdout.strip()
    if not raw:
        return {}
    entries: list[dict[str, object]] = []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, list):
        entries = [entry for entry in payload if isinstance(entry, dict)]
    elif isinstance(payload, dict):
        entries = [payload]
    else:
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError:
                return None
            if isinstance(entry, dict):
                entries.append(entry)
    return {
        service: entry
        for entry in entries
        if isinstance((service := entry.get("Service")), str) and service
    }


def _error_i2p_record(item: str, *, node_name: str, address_file: Path, reason: str) -> I2PAddressRecord:
    return I2PAddressRecord(
        target=item,
        service=SERVICE_MAP[item],
        status="error",
        reason=reason,
        node_name=node_name,
        address_file=address_file,
    )


def _collect_runtime_i2p_errors(pending_paths: dict[str, tuple[str, Path]]) -> dict[str, I2PAddressRecord]:
    statuses = _compose_service_statuses(list(pending_paths))
    if statuses is None:
        return {}
    errors: dict[str, I2PAddressRecord] = {}
    for item, (node_name, address_file) in pending_paths.items():
        service = SERVICE_MAP[item]
        payload = statuses.get(service)
        if payload is None:
            errors[item] = _error_i2p_record(
                item,
                node_name=node_name,
                address_file=address_file,
                reason="The service is not running, so it cannot publish an I2P destination.",
            )
            continue
        state = str(payload.get("State", "")).strip().lower()
        if state in {"exited", "dead", "removing"}:
            exit_code = payload.get("ExitCode")
            detail = f" (exit code {exit_code})" if exit_code not in (None, "", 0, "0") else ""
            errors[item] = _error_i2p_record(
                item,
                node_name=node_name,
                address_file=address_file,
                reason=f"The service entered state {state}{detail} before publishing its I2P destination.",
            )
    return errors


def _collect_i2p_records(target: str, *, env: dict[str, str] | None = None, wait_seconds: float | None = None) -> list[I2PAddressRecord]:
    targets = _available_target_names(target)
    if _privacy_network(env=env) != "i2p":
        return [
            I2PAddressRecord(
                target=item,
                service=SERVICE_MAP[item],
                status="disabled",
                reason="EXECUTION_PRIVACY_NETWORK is not set to i2p.",
            )
            for item in targets
        ]
    pending_paths: dict[str, tuple[str, Path]] = {}
    records: dict[str, I2PAddressRecord] = {}
    for item in targets:
        address_file = _address_file_for_target(item, env=env)
        if address_file is None:
            records[item] = I2PAddressRecord(
                target=item,
                service=SERVICE_MAP[item],
                status="not_applicable",
                reason="This service does not publish an execution I2P destination.",
            )
            continue
        node_name = _node_name_for_target(item)
        assert node_name is not None
        pending_paths[item] = (node_name, address_file)

    timeout_seconds = _resolve_i2p_wait_seconds(env=env, override=wait_seconds)
    deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
    while pending_paths:
        completed_items: list[str] = []
        for item, (node_name, address_file) in pending_paths.items():
            if not address_file.exists():
                continue
            try:
                destination = address_file.read_text(encoding="utf-8").strip()
            except OSError as exc:
                records[item] = _error_i2p_record(
                    item,
                    node_name=node_name,
                    address_file=address_file,
                    reason=f"Failed to read the published I2P destination: {exc}.",
                )
                completed_items.append(item)
                continue
            if not destination:
                continue
            records[item] = I2PAddressRecord(
                target=item,
                service=SERVICE_MAP[item],
                status="available",
                node_name=node_name,
                address_file=address_file,
                i2p_destination=destination,
                i2p_endpoint=f"i2p://{destination}",
            )
            completed_items.append(item)
        for item in completed_items:
            pending_paths.pop(item, None)
        if not pending_paths:
            break
        runtime_errors = _collect_runtime_i2p_errors(pending_paths)
        for item, record in runtime_errors.items():
            records[item] = record
            pending_paths.pop(item, None)
        if not pending_paths:
            break
        if deadline is not None and time.monotonic() >= deadline:
            break
        time.sleep(0.25)

    for item, (node_name, address_file) in pending_paths.items():
        waited = 0.0 if deadline is None else max(0.0, timeout_seconds or 0.0)
        records[item] = _error_i2p_record(
            item,
            node_name=node_name,
            address_file=address_file,
            reason=f"Timed out after {waited:g} seconds waiting for the node to publish its I2P destination.",
        )
    return [records[item] for item in targets]


def _print_i2p_records(records: list[I2PAddressRecord], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps([record.to_dict() for record in records], indent=2, sort_keys=True), flush=True)
        return
    print("I2P addresses:", flush=True)
    for record in records:
        prefix = f"- {record.target} ({record.service})"
        if record.status == "available":
            print(f"{prefix}: {record.i2p_endpoint}", flush=True)
            continue
        detail = record.reason or record.status
        print(f"{prefix}: {record.status} ({detail})", flush=True)


def handle_start(args: argparse.Namespace) -> CommandPlan:
    if args.target == "all":
        argv = _compose_base() + ["up"]
        if args.build:
            argv.append("--build")
        if args.detach:
            argv.append("-d")
        argv.extend(_resolve_targets("all"))
        return CommandPlan(argv=argv, cwd=ROOT)
    if args.target == "consensus":
        return _build_run_plan(SERVICE_MAP["consensus"], build=args.build, profile="consensus")
    return _build_up_plan(SERVICE_MAP[args.target], build=args.build, detach=args.detach)


def handle_stop(args: argparse.Namespace) -> CommandPlan:
    return _build_stop_plan(_resolve_targets(args.target), remove=args.remove)


def handle_logs(args: argparse.Namespace) -> CommandPlan:
    return _build_logs_plan(_resolve_targets(args.target), follow=args.follow, tail=args.tail)


def handle_ps(_: argparse.Namespace) -> CommandPlan:
    return CommandPlan(argv=_compose_base() + ["ps"], cwd=ROOT)


def handle_addresses(args: argparse.Namespace) -> int:
    records = _collect_i2p_records(args.target, wait_seconds=args.wait_seconds)
    _print_i2p_records(records, json_output=bool(args.json))
    return 1 if any(record.status == "error" for record in records) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Docker-based launcher for the project services.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="Start one or more services as Docker containers.")
    start.add_argument("target", choices=tuple(SERVICE_MAP) + ("all",))
    start.add_argument("--dry-run", action="store_true")
    start.add_argument("--build", action=argparse.BooleanOptionalAction, default=True)
    start.add_argument("--detach", action=argparse.BooleanOptionalAction, default=True)
    start.add_argument("--wait-seconds", type=float, default=None, help="How long to wait for published I2P addresses after startup before reporting an error. Defaults to waiting indefinitely.")
    start.set_defaults(handler=handle_start)

    stop = subparsers.add_parser("stop", help="Stop one or more running containers.")
    stop.add_argument("target", choices=tuple(SERVICE_MAP) + ("all",))
    stop.add_argument("--dry-run", action="store_true")
    stop.add_argument("--remove", action=argparse.BooleanOptionalAction, default=False)
    stop.set_defaults(handler=handle_stop)

    logs = subparsers.add_parser("logs", help="Show container logs.")
    logs.add_argument("target", choices=tuple(SERVICE_MAP) + ("all",))
    logs.add_argument("--dry-run", action="store_true")
    logs.add_argument("--follow", action=argparse.BooleanOptionalAction, default=True)
    logs.add_argument("--tail", type=int, default=100)
    logs.set_defaults(handler=handle_logs)

    ps = subparsers.add_parser("ps", help="Show compose service status.")
    ps.add_argument("--dry-run", action="store_true")
    ps.set_defaults(handler=handle_ps)

    addresses = subparsers.add_parser("addresses", help="Show published I2P destinations for launched services.")
    addresses.add_argument("target", choices=tuple(SERVICE_MAP) + ("all",))
    addresses.add_argument("--wait-seconds", type=float, default=None, help="How long to wait for destination files before reporting an error. Defaults to waiting indefinitely.")
    addresses.add_argument("--json", action="store_true", help="Emit the address report as JSON.")
    addresses.set_defaults(handler=handle_addresses)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "addresses":
        return int(args.handler(args))
    plan = args.handler(args)
    result = _run(plan.argv, dry_run=bool(args.dry_run))
    if result != 0 or bool(args.dry_run):
        return result
    if (
        args.command == "start"
        and bool(getattr(args, "detach", False))
        and args.target != "consensus"
        and _privacy_network() == "i2p"
    ):
        records = _collect_i2p_records(args.target, wait_seconds=args.wait_seconds)
        if any(record.status != "not_applicable" for record in records):
            _print_i2p_records(records, json_output=False)
        if any(record.status == "error" for record in records):
            return 1
    return result


if __name__ == "__main__":
    raise SystemExit(main())
