#!/usr/bin/env python3
"""Inspect the distributed data snapshot without rebuilding or fetching data."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "backtest/docs/release/source_snapshot.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_files() -> list[dict]:
    payload = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    return [item for item in payload["copied_files"] if item["path"].startswith("data/")]


def check_snapshot() -> dict:
    files = snapshot_files()
    errors = []
    for item in files:
        path = ROOT / item["path"]
        if not path.is_file():
            errors.append({"path": item["path"], "reason": "missing"})
        elif path.stat().st_size != item["bytes"] or sha256(path) != item["sha256"]:
            errors.append({"path": item["path"], "reason": "snapshot hash differs"})
    return {"status": "passed" if not errors else "failed", "checked_files": len(files),
            "checked_bytes": sum(item["bytes"] for item in files), "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "check"), nargs="?", default="status")
    args = parser.parse_args()
    if args.command == "check":
        result = check_snapshot()
    else:
        result = {"price_as_of": "2026-08-04", "canonical_price_files": len(list((ROOT / "data/processed/daily").rglob("*.csv"))),
                  "rights": "Copyright ownership and distribution authorized by project owner on 2026-09-26",
                  "quality": "QQQ/SPY/RKLB approved; VOO quality_failed; historical membership candidates remain candidate_pending_review",
                  "live_fetch": False, "production_promotion": False,
                  "note": "Distribution permission does not change historical data quality or research-stage gates."}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status", "passed") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
