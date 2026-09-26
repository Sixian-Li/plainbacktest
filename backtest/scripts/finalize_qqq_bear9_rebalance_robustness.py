#!/usr/bin/env python3
"""Hash the printed QQQ/Bear9 robustness PDF and complete analysis."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from quantkit.experiment import load_experiment, load_run, record_analysis_complete, sha256


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    record = load_run(context, args.run_id)
    refresh_completed = (
        record.get("status") == "completed_unvalidated"
        and record.get("analysis", {}).get("status") == "completed"
    )
    if not refresh_completed and (
        record.get("status") != "running" or record.get("analysis", {}).get("status") != "pending"
    ):
        raise RuntimeError(
            "QQQ/Bear9 robustness analysis must be pending or completed_unvalidated for metadata refresh"
        )
    if len(record.get("expected_blocks", [])) != 2 or any(
        item.get("status") != "completed" for item in record["expected_blocks"]
    ):
        raise RuntimeError("both formal cost blocks must be complete")
    run_root = context.run_root(args.run_id)
    pdf = run_root / "report.pdf"
    report = run_root / "report.html"
    if not pdf.is_file() or pdf.read_bytes()[:5] != b"%PDF-" or pdf.stat().st_size < 10_000:
        raise RuntimeError("report.pdf is missing, invalid, or unexpectedly small")
    source = report.read_text(encoding="utf-8")
    for required in (
        "Bear9",
        "SMA160",
        "SMA190/310",
        "不再平衡",
        "剔除2000",
        "什么时候买",
        "什么时候卖",
        "信号如何变成成交",
    ):
        if required not in source:
            raise RuntimeError(f"report.html misses required wording: {required}")
    for graph_id in (
        "performance-qqq_bear9_rebalance",
        "robustness-heatmap",
        "layered-state",
        "timing-sma160",
        "timing-sma180",
        "timing-sma200",
        "timing-sma220",
        "timing-sma240",
        "timing-layered-190-310",
    ):
        if f'id="{graph_id}"' not in source:
            raise RuntimeError(f"report.html misses figure: {graph_id}")
    artifact_manifest: dict[str, object] = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "artifacts": {},
    }
    artifacts = artifact_manifest["artifacts"]
    assert isinstance(artifacts, dict)
    for path in sorted(run_root.rglob("*")):
        if path.is_file() and path.name not in {"artifact_manifest.json", "run.json", "validation.json"}:
            relative = str(path.relative_to(run_root))
            artifacts[relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    (run_root / "artifact_manifest.json").write_text(
        json.dumps(artifact_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if not refresh_completed:
        record_analysis_complete(context, args.run_id)
    print(f"Finalized {pdf} ({pdf.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
