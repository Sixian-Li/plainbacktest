#!/usr/bin/env python3
"""Verify the requested combined PDF, then run the standard run validator."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from quantkit.experiment import load_experiment, sha256


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/ROT/ROT-v0.10b.1__26-08-13__qqq_sma_regime_two_measure_ablation_two_periods"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    run_root = context.run_root(args.run_id)
    pdf = run_root / "report.pdf"
    print_source = run_root / "report_print.html"
    errors: list[str] = []
    if not pdf.is_file() or pdf.read_bytes()[:5] != b"%PDF-":
        errors.append("report.pdf is missing or not a PDF")
    elif pdf.stat().st_size < 10_000:
        errors.append("report.pdf is unexpectedly small")
    source_text = print_source.read_text(encoding="utf-8") if print_source.is_file() else ""
    for required in ("我们测的是什么", "旧 8% 止损已完全删除", "措施 1", "措施 2"):
        if required not in source_text:
            errors.append(f"print source missing required first-page wording: {required}")
    artifact_path = run_root / "artifact_manifest.json"
    if not artifact_path.is_file():
        errors.append("artifact_manifest.json is missing")
    else:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        for name in ("report.pdf", "report_print.html"):
            metadata = artifact.get("artifacts", {}).get(name)
            path = run_root / name
            if not metadata or metadata.get("sha256") != sha256(path):
                errors.append(f"artifact manifest does not hash {name}")
    if errors:
        print("Two-measure PDF gate: FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    process = subprocess.run(
        [str(BACKTEST_ROOT / ".venv/bin/python"), "-m", "scripts.validate_run",
         "--experiment", str(args.experiment), "--run-id", args.run_id],
        cwd=BACKTEST_ROOT,
        check=False,
    )
    if process.returncode == 0:
        print(f"Two-measure PDF gate: PASS ({pdf.stat().st_size} bytes)")
    return process.returncode


if __name__ == "__main__":
    raise SystemExit(main())
