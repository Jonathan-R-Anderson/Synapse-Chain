#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent

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


@dataclass(frozen=True, slots=True)
class CommandPlan:
    argv: list[str]
    cwd: Path


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Docker-based launcher for the project services.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="Start one or more services as Docker containers.")
    start.add_argument("target", choices=tuple(SERVICE_MAP) + ("all",))
    start.add_argument("--dry-run", action="store_true")
    start.add_argument("--build", action=argparse.BooleanOptionalAction, default=True)
    start.add_argument("--detach", action=argparse.BooleanOptionalAction, default=True)
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    plan = args.handler(args)
    return _run(plan.argv, dry_run=bool(args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
