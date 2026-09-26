#!/usr/bin/env python3
"""Normalize an annotator export into a validated subjective bear-market dataset."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd


SYMBOLS = ("QQQ", "SPY")
BOUNDARY_POLICIES = ("preserve", "worst-peak-to-trough")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_prices(path: Path, symbol: str) -> pd.Series:
    frame = pd.read_csv(path, usecols=["date", "symbol", "close"])
    frame["date"] = pd.to_datetime(frame["date"], errors="raise")
    frame["close"] = pd.to_numeric(frame["close"], errors="raise")
    frame = frame.loc[frame["symbol"] == symbol, ["date", "close"]]
    frame = frame.sort_values("date")
    if frame.empty or frame["date"].duplicated().any():
        raise ValueError(f"{symbol} price data is empty or has duplicate dates")
    if frame["close"].isna().any() or (frame["close"] <= 0).any():
        raise ValueError(f"{symbol} close must be positive and non-null")
    return frame.set_index("date")["close"]


def interval_metrics(series: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> dict[str, Any]:
    values = series.loc[start:end]
    if values.empty or values.index[0] != start or values.index[-1] != end:
        raise ValueError(f"interval endpoints must be trading days: {start.date()} through {end.date()}")
    drawdown = values / values.cummax() - 1
    trough = drawdown.idxmin()
    peak = values.loc[:trough].idxmax()
    return {
        "start_close": float(values.iloc[0]),
        "end_close": float(values.iloc[-1]),
        "total_return": float(values.iloc[-1] / values.iloc[0] - 1),
        "max_drawdown": float(drawdown.loc[trough]),
        "drawdown_peak_date": peak.date().isoformat(),
        "drawdown_trough_date": trough.date().isoformat(),
        "drawdown_recovered_within_interval": bool(values.loc[trough:].max() >= values.loc[peak]),
        "trading_sessions": int(len(values)),
    }


def normalize(
    export: dict[str, Any],
    *,
    prices: dict[str, pd.Series],
    major_threshold: float,
    source_export: Path,
    source_paths: dict[str, Path],
    boundary_policy: str = "preserve",
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    if major_threshold <= 0 or major_threshold >= 1:
        raise ValueError("major_threshold must be between 0 and 1")
    if boundary_policy not in BOUNDARY_POLICIES:
        raise ValueError(f"boundary_policy must be one of {BOUNDARY_POLICIES}")
    raw_intervals = export.get("intervals")
    if not isinstance(raw_intervals, list) or not raw_intervals:
        raise ValueError("export must contain a non-empty intervals list")

    normalized = []
    previous_end: pd.Timestamp | None = None
    for index, raw in enumerate(sorted(raw_intervals, key=lambda item: item["start"]), start=1):
        start = pd.Timestamp(raw["start"])
        end = pd.Timestamp(raw["end"])
        if start > end:
            raise ValueError(f"interval {index} starts after it ends")
        if previous_end is not None and start <= previous_end:
            raise ValueError(f"interval {index} overlaps the preceding interval")
        previous_end = end
        candidate_metrics = {
            symbol: interval_metrics(prices[symbol], start, end) for symbol in SYMBOLS
        }
        candidate_sessions = {
            values["trading_sessions"] for values in candidate_metrics.values()
        }
        if len(candidate_sessions) != 1:
            raise ValueError(f"interval {index} has different QQQ/SPY trading-session counts")
        candidate_worst_symbol = min(
            SYMBOLS, key=lambda symbol: candidate_metrics[symbol]["max_drawdown"]
        )
        adjusted_start = start
        adjusted_end = end
        if boundary_policy == "worst-peak-to-trough":
            driver = candidate_metrics[candidate_worst_symbol]
            adjusted_start = pd.Timestamp(driver["drawdown_peak_date"])
            adjusted_end = pd.Timestamp(driver["drawdown_trough_date"])

        metrics = {
            symbol: interval_metrics(prices[symbol], adjusted_start, adjusted_end)
            for symbol in SYMBOLS
        }
        sessions = {values["trading_sessions"] for values in metrics.values()}
        if len(sessions) != 1:
            raise ValueError(f"interval {index} has different QQQ/SPY trading-session counts")
        candidate_index = prices[candidate_worst_symbol].loc[start:end].index
        adjusted_index = prices[candidate_worst_symbol].loc[adjusted_start:adjusted_end].index
        leading_removed = int(candidate_index.get_loc(adjusted_start))
        trailing_removed = int(len(candidate_index) - candidate_index.get_loc(adjusted_end) - 1)
        worst_symbol = min(SYMBOLS, key=lambda symbol: metrics[symbol]["max_drawdown"])
        worst_drawdown = metrics[worst_symbol]["max_drawdown"]
        severity = "major" if worst_drawdown <= -major_threshold else "minor"
        normalized.append(
            {
                "ordinal": index,
                "interval_id": f"subjective_bear_{index:02d}",
                "source_interval_id": str(raw.get("id", "")),
                "source_label": str(raw.get("label", f"熊市 {index}")),
                "label": f"{'大熊市' if severity == 'major' else '小熊市'} {index:02d}",
                "severity": severity,
                "start": adjusted_start.date().isoformat(),
                "end": adjusted_end.date().isoformat(),
                "trading_sessions": sessions.pop(),
                "note": str(raw.get("note", "")),
                "candidate_window": {
                    "start": start.date().isoformat(),
                    "end": end.date().isoformat(),
                    "trading_sessions": candidate_sessions.pop(),
                },
                "boundary_adjustment": {
                    "policy": boundary_policy,
                    "driving_symbol": candidate_worst_symbol,
                    "leading_sessions_removed": leading_removed,
                    "trailing_sessions_removed": trailing_removed,
                    "rebound_excluded": trailing_removed > 0,
                },
                "classification": {
                    "worst_symbol": worst_symbol,
                    "worst_max_drawdown": worst_drawdown,
                    "threshold": major_threshold,
                },
                "QQQ": metrics["QQQ"],
                "SPY": metrics["SPY"],
            }
        )

    generated = generated_at_utc or datetime.now(UTC).isoformat().replace("+00:00", "Z")
    counts = {
        "total": len(normalized),
        "major": sum(item["severity"] == "major" for item in normalized),
        "minor": sum(item["severity"] == "minor" for item in normalized),
    }
    return {
        "schema_version": 1,
        "dataset_id": (
            "subjective_spy_qqq_bear_market_candidates_v2"
            if boundary_policy == "preserve"
            else "subjective_spy_qqq_bear_markets_peak_to_trough_v2"
        ),
        "generated_at_utc": generated,
        "description": "User-drawn subjective QQQ/SPY bear-market candidate windows refined into major and minor decline intervals.",
        "subjective_boundaries_preserved": boundary_policy == "preserve",
        "candidate_boundaries_preserved_as_fields": True,
        "boundary_policy": {
            "name": boundary_policy,
            "rule": (
                "preserve each user-drawn candidate window"
                if boundary_policy == "preserve"
                else "within each user-drawn candidate window, choose the symbol with the deeper maximum drawdown and trim to that drawdown's peak-through-trough dates"
            ),
            "never_expands_beyond_candidate_window": True,
            "post_trough_rebound_excluded": boundary_policy == "worst-peak-to-trough",
        },
        "interval_semantics": "inclusive start and end trading sessions; refined intervals are sorted and non-overlapping",
        "severity_policy": {
            "major": f"QQQ or SPY interval maximum drawdown is at least {major_threshold:.0%}",
            "minor": f"both QQQ and SPY interval maximum drawdowns are less than {major_threshold:.0%}",
            "threshold": major_threshold,
            "uses_worst_of_symbols": list(SYMBOLS),
        },
        "counts": counts,
        "source_export": {
            "path": str(source_export),
            "sha256": sha256(source_export),
            "view_id": export.get("view_id"),
            "exported_at": export.get("exported_at"),
        },
        "price_sources": {
            symbol: {"path": str(source_paths[symbol]), "sha256": sha256(source_paths[symbol])}
            for symbol in SYMBOLS
        },
        "intervals": normalized,
    }


def write_csv(payload: dict[str, Any], path: Path) -> None:
    fields = [
        "ordinal", "interval_id", "label", "severity", "start", "end", "trading_sessions",
        "candidate_start", "candidate_end", "candidate_trading_sessions", "boundary_driving_symbol",
        "leading_sessions_removed", "trailing_sessions_removed", "rebound_excluded",
        "qqq_total_return", "qqq_max_drawdown", "qqq_drawdown_peak_date", "qqq_drawdown_trough_date",
        "spy_total_return", "spy_max_drawdown", "spy_drawdown_peak_date", "spy_drawdown_trough_date",
        "classification_worst_symbol", "classification_worst_max_drawdown", "note", "source_label",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for item in payload["intervals"]:
            writer.writerow(
                {
                    "ordinal": item["ordinal"],
                    "interval_id": item["interval_id"],
                    "label": item["label"],
                    "severity": item["severity"],
                    "start": item["start"],
                    "end": item["end"],
                    "trading_sessions": item["trading_sessions"],
                    "candidate_start": item["candidate_window"]["start"],
                    "candidate_end": item["candidate_window"]["end"],
                    "candidate_trading_sessions": item["candidate_window"]["trading_sessions"],
                    "boundary_driving_symbol": item["boundary_adjustment"]["driving_symbol"],
                    "leading_sessions_removed": item["boundary_adjustment"]["leading_sessions_removed"],
                    "trailing_sessions_removed": item["boundary_adjustment"]["trailing_sessions_removed"],
                    "rebound_excluded": item["boundary_adjustment"]["rebound_excluded"],
                    "qqq_total_return": item["QQQ"]["total_return"],
                    "qqq_max_drawdown": item["QQQ"]["max_drawdown"],
                    "qqq_drawdown_peak_date": item["QQQ"]["drawdown_peak_date"],
                    "qqq_drawdown_trough_date": item["QQQ"]["drawdown_trough_date"],
                    "spy_total_return": item["SPY"]["total_return"],
                    "spy_max_drawdown": item["SPY"]["max_drawdown"],
                    "spy_drawdown_peak_date": item["SPY"]["drawdown_peak_date"],
                    "spy_drawdown_trough_date": item["SPY"]["drawdown_trough_date"],
                    "classification_worst_symbol": item["classification"]["worst_symbol"],
                    "classification_worst_max_drawdown": item["classification"]["worst_max_drawdown"],
                    "note": item["note"],
                    "source_label": item["source_label"],
                }
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--qqq", type=Path, required=True)
    parser.add_argument("--spy", type=Path, required=True)
    parser.add_argument("--major-threshold", type=float, default=0.20)
    parser.add_argument("--boundary-policy", choices=BOUNDARY_POLICIES, default="preserve")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    args = parser.parse_args()

    export = json.loads(args.input.read_text(encoding="utf-8"))
    source_paths = {"QQQ": args.qqq, "SPY": args.spy}
    prices = {symbol: load_prices(source_paths[symbol], symbol) for symbol in SYMBOLS}
    payload = normalize(
        export,
        prices=prices,
        major_threshold=args.major_threshold,
        source_export=args.input,
        source_paths=source_paths,
        boundary_policy=args.boundary_policy,
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(payload, args.output_csv)
    print(f"Wrote {args.output_json}")
    print(f"Wrote {args.output_csv}")
    print(json.dumps(payload["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
