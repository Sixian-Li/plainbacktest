#!/usr/bin/env python3
"""Build a canonical XNYS session calendar and compare the purchased file."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import exchange_calendars as xcals
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_ROOT = PROJECT_ROOT / "data/processed/calendars"
VENDOR_CALENDAR = PROJECT_ROOT / "data/2026-08-05美股数据_全/美股交易日历.csv"
START = "1993-01-01"
END = "2026-12-31"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    calendar = xcals.get_calendar("XNYS", start=START, end=END)
    schedule = calendar.schedule.loc[START:END].copy().reset_index()
    session_col = schedule.columns[0]
    schedule = schedule.rename(
        columns={session_col: "date", "open": "open_utc", "close": "close_utc"}
    )
    schedule["date"] = pd.to_datetime(schedule["date"]).dt.strftime("%Y-%m-%d")
    schedule["open_utc"] = pd.to_datetime(schedule["open_utc"], utc=True).dt.strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    schedule["close_utc"] = pd.to_datetime(schedule["close_utc"], utc=True).dt.strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    regular_seconds = 6.5 * 60 * 60
    duration = (
        pd.to_datetime(schedule["close_utc"], utc=True)
        - pd.to_datetime(schedule["open_utc"], utc=True)
    ).dt.total_seconds()
    schedule["is_early_close"] = duration < regular_seconds
    output_path = OUTPUT_ROOT / "XNYS.csv"
    schedule[["date", "open_utc", "close_utc", "is_early_close"]].to_csv(
        output_path, index=False, lineterminator="\n"
    )

    with VENDOR_CALENDAR.open("r", encoding="utf-8-sig", newline="") as file:
        vendor_rows = list(csv.DictReader(file))
    vendor_flags = {
        row["日期"]: row["是否交易"] == "交易"
        for row in vendor_rows
        if START <= row["日期"] <= END
    }
    xnys_sessions = set(schedule["date"])
    all_dates = sorted(set(vendor_flags) | xnys_sessions)
    comparison = []
    for day in all_dates:
        vendor_open = vendor_flags.get(day)
        xnys_open = day in xnys_sessions
        if vendor_open == xnys_open:
            continue
        comparison.append(
            {
                "date": day,
                "vendor_open": vendor_open,
                "xnys_open": xnys_open,
                "classification": (
                    "vendor_marks_open_but_xnys_closed"
                    if vendor_open
                    else "vendor_marks_closed_but_xnys_open"
                ),
            }
        )
    comparison_path = OUTPUT_ROOT / "vendor_comparison.csv"
    pd.DataFrame(comparison).to_csv(comparison_path, index=False, lineterminator="\n")

    generated = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    manifest = {
        "schema_version": 1,
        "generated_at_utc": generated,
        "calendar": "XNYS",
        "library": "exchange_calendars",
        "library_version": xcals.__version__,
        "range": {"start": START, "end": END},
        "session_count": len(schedule),
        "output": str(output_path.relative_to(PROJECT_ROOT)),
        "output_sha256": sha256(output_path),
        "vendor_calendar": str(VENDOR_CALENDAR.relative_to(PROJECT_ROOT)),
        "vendor_calendar_sha256": sha256(VENDOR_CALENDAR),
        "comparison_output": str(comparison_path.relative_to(PROJECT_ROOT)),
        "comparison_sha256": sha256(comparison_path),
        "difference_count": len(comparison),
        "official_references": [
            "https://www.nyse.com/trade/hours-calendars",
            "https://s2.q4cdn.com/154085107/files/doc_news/NYSE-Group-Announces-2024-2025-and-2026-Holiday-and-Early-Closings-Calendar-2023.pdf",
        ],
        "policy": "XNYS drives validation and scheduling; purchased calendar is comparison-only.",
    }
    manifest_path = OUTPUT_ROOT / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"XNYS sessions: {len(schedule)}")
    print(f"Vendor/XNYS differences: {len(comparison)}")
    for item in comparison:
        if item["date"] in {
            "2015-04-09",
            "2023-06-19",
            "2023-06-20",
            "2024-06-19",
            "2025-01-26",
            "2025-06-19",
        }:
            print(item)


if __name__ == "__main__":
    main()
