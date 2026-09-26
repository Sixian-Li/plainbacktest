#!/usr/bin/env python3
"""Inspect, interrupt, or explicitly prune one experiment run."""

from __future__ import annotations

import argparse
from pathlib import Path

from quantkit.experiment import (
    active_run,
    load_experiment,
    load_run,
    prune_interrupted_run,
    record_run_interrupted,
)


BACKTEST_ROOT = Path(__file__).resolve().parents[1]


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--experiment", type=Path, required=True)
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Show active/latest pointers and every run status.")
    interrupt = commands.add_parser("interrupt", help="Mark a running session as operationally interrupted.")
    interrupt.add_argument("--run-id")
    interrupt.add_argument("--reason", required=True)
    prune = commands.add_parser("prune", help="Delete an interrupted run after a validated successor exists.")
    prune.add_argument("--run-id", required=True)
    prune.add_argument("--superseded-by", required=True)
    prune.add_argument("--apply", action="store_true", help="Actually delete; without this flag only preview.")
    return root


def main() -> int:
    args = parser().parse_args()
    context = load_experiment(args.experiment)
    if args.command == "status":
        current = active_run(context)
        print(f"active: {current[0] if current else 'none'}")
        print(f"latest validated: {context.config.get('latest_validated_run_id', 'none')}")
        for run_path in sorted(context.runs_root.glob("run_*/run.json")):
            record = load_run(context, run_path.parent.name)
            print(f"{record['run_id']}\t{record['status']}")
        return 0
    if args.command == "interrupt":
        run_id = args.run_id or context.config.get("active_run_id")
        if not run_id:
            raise RuntimeError("No --run-id and no active_run_id")
        record_run_interrupted(context, str(run_id), reason=args.reason)
        print(f"interrupted: {run_id}")
        return 0
    record = load_run(context, args.run_id)
    successor = load_run(context, args.superseded_by)
    print(f"candidate: {args.run_id} ({record.get('status')})")
    print(f"successor: {args.superseded_by} ({successor.get('status')})")
    if not args.apply:
        print("preview only; pass --apply to prune")
        return 0
    summary = prune_interrupted_run(
        context,
        args.run_id,
        superseded_by_run_id=args.superseded_by,
    )
    print(f"pruned: {summary['files']} files / {summary['bytes']} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
