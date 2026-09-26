#!/usr/bin/env python3
"""Build or verify the purchased raw-data inventory without modifying raw files."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = WORKSPACE_ROOT / "data"
MANIFEST_PATH = DATA_ROOT / "raw_manifest.json"
RAW_SOURCES = {
    "2026-08-05美股数据_全": "vendor_full_us_market_bundle",
    "2026-08-05多个数据包_rethink": "rethink_financial_data_bundles",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory() -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    source_summary: dict[str, dict[str, Any]] = {}
    for directory, provider in RAW_SOURCES.items():
        root = DATA_ROOT / directory
        if not root.is_dir():
            raise FileNotFoundError(f"Raw source directory is missing: {root}")
        count = 0
        total_bytes = 0
        for path in sorted(
            item for item in root.rglob("*") if item.is_file() and item.name != ".DS_Store"
        ):
            size = path.stat().st_size
            files.append(
                {
                    "path": str(path.relative_to(WORKSPACE_ROOT)),
                    "bytes": size,
                    "sha256": sha256(path),
                    "provider": provider,
                    "acquired_date": "2026-08-05",
                }
            )
            count += 1
            total_bytes += size
        source_summary[provider] = {
            "directory": f"data/{directory}",
            "file_count": count,
            "bytes": total_bytes,
        }
    return {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "hash_algorithm": "sha256",
        "policy": {
            "raw_files_are_immutable": True,
            "minimum_copy_target": "two independent physical locations",
            "current_backup_status": "not_verified",
            "restore_test_status": "not_performed",
        },
        "source_summary": source_summary,
        "file_count": len(files),
        "total_bytes": sum(item["bytes"] for item in files),
        "files": files,
    }


def verify(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    registered = {item["path"]: item for item in manifest.get("files", [])}
    actual: set[str] = set()
    for directory in RAW_SOURCES:
        root = DATA_ROOT / directory
        for path in sorted(
            item for item in root.rglob("*") if item.is_file() and item.name != ".DS_Store"
        ):
            relative = str(path.relative_to(WORKSPACE_ROOT))
            actual.add(relative)
            metadata = registered.get(relative)
            if metadata is None:
                errors.append(f"unregistered raw file: {relative}")
            elif path.stat().st_size != metadata.get("bytes"):
                errors.append(f"byte size mismatch: {relative}")
            elif sha256(path) != metadata.get("sha256"):
                errors.append(f"SHA256 mismatch: {relative}")
    for relative in sorted(set(registered) - actual):
        errors.append(f"registered raw file is missing: {relative}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        errors = verify(manifest)
        if errors:
            print("Raw-data inventory: FAIL")
            for error in errors:
                print(f"- {error}")
            return 1
        print(f"Raw-data inventory: PASS ({manifest['file_count']} files)")
        return 0
    payload = inventory()
    MANIFEST_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {MANIFEST_PATH}")
    print(f"- files: {payload['file_count']}")
    print(f"- bytes: {payload['total_bytes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
