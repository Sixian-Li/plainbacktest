#!/usr/bin/env python3
"""Hash the printed report and finish the Nasdaq-100 Strategy1 rotation run."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from quantkit.experiment import load_experiment, load_run, record_analysis_complete, sha256


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/ROT/ROT-v0.50a.1__26-08-26__nasdaq100_stochrsi_strategy1_top20"
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    record = load_run(context, args.run_id)
    if record.get("status") not in {"running", "completed_unvalidated"}:
        raise RuntimeError(
            f"Run must be running or completed_unvalidated, found {record.get('status')!r}"
        )
    run_root = context.run_root(args.run_id)
    required = [
        run_root / "report.html",
        run_root / "report.md",
        run_root / "report.pdf",
        run_root / "analysis/summary.json",
        run_root / "provenance.json",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"Required analysis artifacts are missing: {missing}")
    pdf = run_root / "report.pdf"
    if pdf.read_bytes()[:5] != b"%PDF-":
        raise RuntimeError("report.pdf is not a valid PDF")
    manifest = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "artifacts": {},
    }
    for path in sorted(run_root.rglob("*")):
        if path.is_file() and path.name not in {
            "artifact_manifest.json",
            "run.json",
            "validation.json",
        }:
            manifest["artifacts"][str(path.relative_to(run_root))] = {
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
    (run_root / "artifact_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    if record.get("status") == "running":
        record_analysis_complete(context, args.run_id)
    print(f"Finalized {args.run_id} with {len(manifest['artifacts'])} hashed artifacts")


if __name__ == "__main__":
    main()
