#!/usr/bin/env python3
"""Hash the printed bear24 PDF and complete the analysis lifecycle."""

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
    if record.get("status") != "running" or record.get("analysis", {}).get("status") != "pending":
        raise RuntimeError("bear24 analysis is not pending finalization")
    run_root = context.run_root(args.run_id)
    pdf = run_root / "report.pdf"
    report = run_root / "report.html"
    if not pdf.is_file() or pdf.read_bytes()[:5] != b"%PDF-" or pdf.stat().st_size < 10_000:
        raise RuntimeError("report.pdf is missing, invalid, or unexpectedly small")
    source = report.read_text(encoding="utf-8")
    for required in (
        "24个单标的",
        "不包含2000",
        "什么时候买",
        "什么时候卖",
        "信号如何变成成交",
        "ATR",
    ):
        if required not in source:
            raise RuntimeError(f"report.html misses required wording: {required}")
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    artifact_manifest: dict[str, object] = {
        "schema_version": 1,
        "created_at_utc": created_at,
        "artifacts": {},
    }
    artifacts = artifact_manifest["artifacts"]
    assert isinstance(artifacts, dict)
    for path in sorted(run_root.rglob("*")):
        if path.is_file() and path.name not in {
            "artifact_manifest.json",
            "run.json",
            "validation.json",
        }:
            relative = str(path.relative_to(run_root))
            artifacts[relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    (run_root / "artifact_manifest.json").write_text(
        json.dumps(artifact_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    record_analysis_complete(context, args.run_id)
    print(f"Finalized {pdf} ({pdf.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
