#!/usr/bin/env python3
"""Hash the printed PDF and complete the sparse-entry factorial analysis."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from quantkit.experiment import load_experiment, load_run, record_analysis_complete, sha256


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/TIM/TIM-v0.80a.2__26-08-25__qqq_stochrsi_sparse_entry_factorial"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    record = load_run(context, args.run_id)
    if record.get("status") != "running":
        raise RuntimeError(f"Run must still be running, found {record.get('status')!r}")
    run_root = context.run_root(args.run_id)
    pdf = run_root / "report.pdf"
    if not pdf.is_file() or pdf.read_bytes()[:5] != b"%PDF-":
        raise RuntimeError("A valid report.pdf must be printed before finalization")
    manifest = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "artifacts": {},
    }
    for path in sorted(run_root.rglob("*")):
        if path.is_file() and path.name not in {"artifact_manifest.json", "run.json", "validation.json"}:
            manifest["artifacts"][str(path.relative_to(run_root))] = {
                "bytes": path.stat().st_size, "sha256": sha256(path),
            }
    (run_root / "artifact_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    record_analysis_complete(context, args.run_id)
    print(f"Finalized {args.run_id} with {pdf.stat().st_size} PDF bytes")


if __name__ == "__main__":
    main()
