#!/usr/bin/env python3
"""Create a new immutable-output run for a configured experiment."""

from __future__ import annotations

import argparse
from pathlib import Path

from quantkit.experiment import load_experiment, resume_active_run, start_run


BACKTEST_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--experiment",
        type=Path,
        default=BACKTEST_ROOT / "experiments/TIM/TIM-v0.10__26-08-08__sma200_threshold_grid",
        help="Experiment directory or experiment.json path.",
    )
    parser.add_argument(
        "--resume-active",
        action="store_true",
        help="Return the existing running/completed-unvalidated run instead of creating a duplicate.",
    )
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    if args.resume_active:
        run_id, run_root, record = resume_active_run(context)
        print(run_id)
        print(run_root)
        print(record["status"])
        return
    run_id, run_root = start_run(context)
    print(run_id)
    print(run_root)


if __name__ == "__main__":
    main()
