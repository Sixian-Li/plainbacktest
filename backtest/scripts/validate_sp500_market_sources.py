#!/usr/bin/env python3
"""Run an isolated multi-source validation for a representative SPY sample.

The script never mutates the approved price database.  It archives provider
responses under data/raw/public and writes normalized comparison evidence under
data/processed/validations.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import statistics
import subprocess
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlencode


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
CURRENT_CONSTITUENTS = WORKSPACE_ROOT / "data/processed/universes/sp500/current_constituents.csv"
DEFAULT_PROCESSED_ROOT = WORKSPACE_ROOT / "data/processed/validations/sp500_market_sources"
DEFAULT_RAW_ROOT = WORKSPACE_ROOT / "data/raw/public/market_data_validation"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
EASTMONEY_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
TWELVE_URL = "https://api.twelvedata.com/time_series"
TIINGO_URL = "https://api.tiingo.com/tiingo/daily/{symbol}/prices"
EASTMONEY_MARKETS = ("105", "106", "107")
FIELDS = ("open", "high", "low", "close", "volume")
PRICE_FIELDS = ("open", "high", "low", "close")
SAMPLE = (
    ("SPY", "ETF and NYSE Arca routing control"),
    ("FERG", "official-current constituent missing from the frozen vendor archive"),
    ("BRK.B", "dot-class ticker mapping"),
    ("BF.B", "second dot-class ticker mapping"),
    ("AAPL", "large Nasdaq stock"),
    ("MSFT", "large Nasdaq stock and cash dividends"),
    ("NVDA", "recent split history and high activity"),
    ("AMZN", "Nasdaq split-history control"),
    ("GOOGL", "share-class and split-history control"),
    ("META", "Nasdaq symbol-history control"),
    ("TSLA", "high-volatility Nasdaq stock"),
    ("JPM", "NYSE financial stock"),
    ("XOM", "NYSE dividend stock"),
    ("JNJ", "NYSE defensive dividend stock"),
    ("GE", "corporate-action history"),
    ("PLTR", "high-volatility recent constituent"),
    ("NEM", "gold-miner and commodity sensitivity"),
    ("MO", "high-dividend stock"),
    ("LMT", "lower-turnover industrial stock"),
    ("CBOE", "non-Nasdaq/NYSE primary-listing routing case"),
)


class ProviderError(RuntimeError):
    """A provider failed without exposing credentials."""


class TransportError(ProviderError):
    """A provider transport failed after the configured retry budget."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(data)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def csv_bytes(fieldnames: Iterable[str], rows: Iterable[dict[str, Any]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return output.getvalue().encode("utf-8")


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def finite_number(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ProviderError(f"invalid {label}") from exc
    if not math.isfinite(result):
        raise ProviderError(f"non-finite {label}")
    return result


def curl_json(
    url: str,
    *,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    attempts: int = 4,
    timeout_seconds: int = 25,
) -> tuple[Any, dict[str, Any]]:
    full_url = url
    if params:
        full_url += ("&" if "?" in full_url else "?") + urlencode(params)
    command = [
        "curl",
        "--ipv4",
        "--http1.1",
        "--silent",
        "--show-error",
        "--compressed",
        "--connect-timeout",
        "8",
        "--max-time",
        str(timeout_seconds),
        "--user-agent",
        USER_AGENT,
    ]
    # Feed headers through stdin so API credentials never appear in argv / ps.
    curl_config = "".join(
        f'header = "{key}: {str(value).replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"\n'
        for key, value in (headers or {}).items()
    )
    if curl_config:
        command.extend(("--config", "-"))
    command.append(full_url)
    errors: list[str] = []
    for attempt in range(1, attempts + 1):
        started = time.monotonic()
        process = subprocess.run(
            command,
            input=curl_config,
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 5,
        )
        elapsed = round(time.monotonic() - started, 3)
        if process.returncode == 0:
            try:
                return json.loads(process.stdout), {"attempts": attempt, "elapsed_seconds": elapsed}
            except json.JSONDecodeError:
                errors.append(f"attempt {attempt}: invalid JSON")
        else:
            message = (process.stderr or "curl failure").strip().splitlines()[-1]
            errors.append(f"attempt {attempt}: curl exit {process.returncode}: {message[:180]}")
        if attempt < attempts:
            time.sleep(min(2 ** (attempt - 1), 4))
    raise TransportError("; ".join(errors))


def parse_eastmoney(payload: Any, expected_symbol: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    if not isinstance(payload, dict) or payload.get("rc") != 0:
        raise ProviderError("Eastmoney response rc is not zero")
    data = payload.get("data")
    if not isinstance(data, dict) or not data.get("klines"):
        raise ProviderError("Eastmoney returned no data")
    returned = str(data.get("code", "")).upper()
    aliases = {expected_symbol.upper(), expected_symbol.replace(".", "-").upper()}
    if returned not in aliases:
        raise ProviderError(f"Eastmoney symbol mismatch: {returned}")
    rows = []
    for line in data["klines"]:
        values = str(line).split(",")
        if len(values) < 9:
            raise ProviderError("Eastmoney kline has fewer than nine fields")
        rows.append(
            {
                "date": values[0],
                "open": finite_number(values[1], "Eastmoney open"),
                "close": finite_number(values[2], "Eastmoney close"),
                "high": finite_number(values[3], "Eastmoney high"),
                "low": finite_number(values[4], "Eastmoney low"),
                "volume": finite_number(values[5], "Eastmoney volume"),
            }
        )
    return rows, {"returned_symbol": returned, "name": str(data.get("name", ""))}


def parse_twelve(payload: Any, symbol: str) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and payload.get("status") == "error":
        raise ProviderError(f"Twelve Data error: {payload.get('message', 'unknown error')}")
    selected = payload
    if isinstance(payload, dict) and symbol in payload and isinstance(payload[symbol], dict):
        selected = payload[symbol]
    if not isinstance(selected, dict) or selected.get("status") == "error":
        raise ProviderError(f"Twelve Data returned no usable object for {symbol}")
    values = selected.get("values")
    if not isinstance(values, list) or not values:
        raise ProviderError(f"Twelve Data returned no values for {symbol}")
    rows = []
    for item in values:
        rows.append(
            {
                "date": str(item["datetime"])[:10],
                "open": finite_number(item["open"], "Twelve open"),
                "high": finite_number(item["high"], "Twelve high"),
                "low": finite_number(item["low"], "Twelve low"),
                "close": finite_number(item["close"], "Twelve close"),
                "volume": finite_number(item["volume"], "Twelve volume"),
            }
        )
    return sorted(rows, key=lambda row: row["date"])


def parse_tiingo(payload: Any, *, adjusted: bool) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        raise ProviderError(f"Tiingo error: {payload.get('detail') or payload.get('message') or 'unknown error'}")
    if not isinstance(payload, list) or not payload:
        raise ProviderError("Tiingo returned no prices")
    prefix = "adj" if adjusted else ""
    def key(field: str) -> str:
        return f"{prefix}{field.title()}" if adjusted else field
    rows = []
    for item in payload:
        rows.append(
            {
                "date": str(item["date"])[:10],
                "open": finite_number(item[key("open")], "Tiingo open"),
                "high": finite_number(item[key("high")], "Tiingo high"),
                "low": finite_number(item[key("low")], "Tiingo low"),
                "close": finite_number(item[key("close")], "Tiingo close"),
                "volume": finite_number(item[key("volume")], "Tiingo volume"),
            }
        )
    return sorted(rows, key=lambda row: row["date"])


def tiingo_symbol(symbol: str) -> str:
    return symbol.replace(".", "-")


def validate_rows(rows: list[dict[str, Any]], provider: str, symbol: str) -> None:
    dates = [row["date"] for row in rows]
    if dates != sorted(dates) or len(dates) != len(set(dates)):
        raise ProviderError(f"{provider} {symbol} dates are not unique ascending")
    for row in rows:
        if min(row[field] for field in PRICE_FIELDS) <= 0 or row["volume"] < 0:
            raise ProviderError(f"{provider} {symbol} has nonpositive price or negative volume")
        if row["low"] > min(row["open"], row["close"]) or row["high"] < max(row["open"], row["close"]):
            raise ProviderError(f"{provider} {symbol} has invalid OHLC on {row['date']}")


def compare_series(
    symbol: str,
    mode: str,
    left_name: str,
    left_rows: list[dict[str, Any]],
    right_name: str,
    right_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    left = {row["date"]: row for row in left_rows}
    right = {row["date"]: row for row in right_rows}
    common = sorted(set(left) & set(right))
    left_only = sorted(set(left) - set(right))
    right_only = sorted(set(right) - set(left))
    relative: dict[str, list[float]] = {field: [] for field in FIELDS}
    for day in common:
        for field in FIELDS:
            denominator = max(abs(left[day][field]), abs(right[day][field]), 1e-12)
            relative[field].append(abs(left[day][field] - right[day][field]) / denominator)
    return_differences: list[float] = []
    for prior, current in zip(common, common[1:]):
        left_return = left[current]["close"] / left[prior]["close"] - 1
        right_return = right[current]["close"] / right[prior]["close"] - 1
        return_differences.append(abs(left_return - right_return))
    metrics: dict[str, Any] = {
        "symbol": symbol,
        "mode": mode,
        "left": left_name,
        "right": right_name,
        "left_rows": len(left),
        "right_rows": len(right),
        "common_dates": len(common),
        "latest_common_date": common[-1] if common else None,
        "left_only_dates": left_only,
        "right_only_dates": right_only,
    }
    for field in FIELDS:
        metrics[f"{field}_relative_median"] = percentile(relative[field], 0.5)
        metrics[f"{field}_relative_p95"] = percentile(relative[field], 0.95)
        metrics[f"{field}_relative_max"] = max(relative[field]) if relative[field] else None
    metrics["close_return_abs_diff_p95"] = percentile(return_differences, 0.95)
    metrics["close_return_abs_diff_max"] = max(return_differences) if return_differences else None
    metrics["comparison_status"] = classify_comparison(metrics)
    return metrics


def classify_comparison(metrics: dict[str, Any]) -> str:
    if metrics["common_dates"] < 20:
        return "insufficient_overlap"
    close_p95 = metrics["close_relative_p95"]
    return_p95 = metrics["close_return_abs_diff_p95"]
    if metrics["mode"] == "raw":
        if close_p95 is not None and close_p95 <= 0.0025:
            return "candidate_pass"
    elif return_p95 is not None and return_p95 <= 0.0025:
        return "candidate_pass"
    return "review"


def raw_archive_path(raw_run: Path, provider: str, name: str) -> Path:
    return raw_run / provider / f"{name}.json"


def archive_payload(raw_run: Path, provider: str, name: str, payload: Any) -> dict[str, Any]:
    path = raw_archive_path(raw_run, provider, name)
    content = json_bytes(payload)
    atomic_write(path, content)
    return {
        "path": str(path.relative_to(WORKSPACE_ROOT)),
        "sha256": sha256_bytes(content),
        "bytes": len(content),
    }


def fetch_eastmoney(
    symbol: str,
    start: str,
    end: str,
    raw_run: Path,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any], list[dict[str, Any]]]:
    transport: list[dict[str, Any]] = []
    market: str | None = None
    payload_raw: Any = None
    rows_raw: list[dict[str, Any]] | None = None
    metadata: dict[str, str] = {}
    for candidate in EASTMONEY_MARKETS:
        params = {
            "secid": f"{candidate}.{symbol}",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59",
            "klt": "101",
            "fqt": "0",
            "beg": start.replace("-", ""),
            "end": end.replace("-", ""),
        }
        try:
            payload, call = curl_json(
                EASTMONEY_URL,
                params=params,
                headers={"Accept": "application/json,text/plain,*/*", "Referer": "https://quote.eastmoney.com/"},
            )
            rows, metadata = parse_eastmoney(payload, symbol)
            validate_rows(rows, "eastmoney", symbol)
            market, payload_raw, rows_raw = candidate, payload, rows
            transport.append({"market": candidate, "status": "matched", **call})
            break
        except TransportError:
            raise
        except ProviderError as exc:
            transport.append({"market": candidate, "status": "not_matched_or_failed", "error": str(exc)})
    if market is None or payload_raw is None or rows_raw is None:
        details = "; ".join(
            f"market {item['market']}: {item.get('error', item['status'])}" for item in transport
        )
        raise ProviderError(f"Eastmoney market discovery failed for {symbol}: {details}")
    archive_raw = archive_payload(raw_run, "eastmoney", f"{symbol}_raw", payload_raw)
    adjusted_params = {
        "secid": f"{market}.{symbol}",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59",
        "klt": "101",
        "fqt": "1",
        "beg": start.replace("-", ""),
        "end": end.replace("-", ""),
    }
    payload_adjusted, adjusted_call = curl_json(
        EASTMONEY_URL,
        params=adjusted_params,
        headers={"Accept": "application/json,text/plain,*/*", "Referer": "https://quote.eastmoney.com/"},
    )
    rows_adjusted, _ = parse_eastmoney(payload_adjusted, symbol)
    validate_rows(rows_adjusted, "eastmoney_adjusted", symbol)
    archive_adjusted = archive_payload(raw_run, "eastmoney", f"{symbol}_adjusted", payload_adjusted)
    metadata_out = {
        "market": market,
        **metadata,
        "raw_archive": archive_raw,
        "adjusted_archive": archive_adjusted,
        "adjusted_call": adjusted_call,
    }
    return {"raw": rows_raw, "adjusted": rows_adjusted}, metadata_out, transport


def fetch_twelve_batch(
    symbols: list[str],
    start: str,
    end: str,
    api_key: str,
    raw_run: Path,
) -> tuple[dict[str, dict[str, list[dict[str, Any]]]], dict[str, Any]]:
    result: dict[str, dict[str, list[dict[str, Any]]]] = {symbol: {} for symbol in symbols}
    metadata: dict[str, Any] = {"credit_policy": {"credits_per_minute": 8, "credits_per_symbol": 1}}
    credits_used = 0
    credit_minute: str | None = None

    def reset_credit_window_if_needed() -> None:
        nonlocal credits_used, credit_minute
        marker = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
        if marker != credit_minute:
            credits_used = 0
            credit_minute = marker

    def wait_for_next_credit_window() -> None:
        nonlocal credits_used, credit_minute
        original = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
        while datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ") == original:
            time.sleep(5)
        credits_used = 0
        credit_minute = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")

    for mode, adjustment in (("raw", "none"), ("adjusted", "all")):
        metadata[mode] = {"adjust": adjustment, "batches": []}
        offset = 0
        batch_number = 0
        while offset < len(symbols):
            reset_credit_window_if_needed()
            if credits_used >= 8:
                wait_for_next_credit_window()
            batch = symbols[offset : offset + (8 - credits_used)]
            payload, call = curl_json(
                TWELVE_URL,
                params={
                    "symbol": ",".join(batch),
                    "interval": "1day",
                    "start_date": start,
                    "end_date": end,
                    "outputsize": "5000",
                    "adjust": adjustment,
                },
                headers={"Authorization": f"apikey {api_key}"},
            )
            if isinstance(payload, dict) and payload.get("status") == "error" and "API credits" in str(payload.get("message")):
                wait_for_next_credit_window()
                continue
            batch_number += 1
            archive = archive_payload(raw_run, "twelve_data", f"{mode}_batch_{batch_number:02d}", payload)
            metadata[mode]["batches"].append(
                {"symbols": batch, "credits": len(batch), "archive": archive, "call": call}
            )
            for symbol in batch:
                rows = parse_twelve(payload, symbol)
                validate_rows(rows, f"twelve_{mode}", symbol)
                result[symbol][mode] = rows
            credits_used += len(batch)
            offset += len(batch)
    return result, metadata


def fetch_tiingo(
    symbol: str,
    start: str,
    end: str,
    api_token: str,
    raw_run: Path,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    alias = tiingo_symbol(symbol)
    payload, call = curl_json(
        TIINGO_URL.format(symbol=alias),
        params={"startDate": start, "endDate": end, "resampleFreq": "daily"},
        headers={"Authorization": f"Token {api_token}"},
    )
    raw = parse_tiingo(payload, adjusted=False)
    adjusted = parse_tiingo(payload, adjusted=True)
    validate_rows(raw, "tiingo_raw", symbol)
    validate_rows(adjusted, "tiingo_adjusted", symbol)
    return {"raw": raw, "adjusted": adjusted}, {
        "requested_symbol": alias,
        "archive": archive_payload(raw_run, "tiingo", symbol, payload),
        "call": call,
    }


def check_current_sample(symbols: list[str]) -> dict[str, dict[str, str]]:
    with CURRENT_CONSTITUENTS.open(newline="", encoding="utf-8") as file:
        current = {row["symbol"]: row for row in csv.DictReader(file)}
    missing = sorted(set(symbols) - set(current) - {"SPY"})
    if missing:
        raise ValueError(f"Sample symbols are not in the dated SPY snapshot: {missing}")
    return {symbol: current[symbol] for symbol in symbols if symbol in current}


def normalized_rows(all_data: dict[str, dict[str, dict[str, list[dict[str, Any]]]]]) -> list[dict[str, Any]]:
    output = []
    for symbol in sorted(all_data):
        for provider in sorted(all_data[symbol]):
            for mode in ("raw", "adjusted"):
                for row in all_data[symbol][provider][mode]:
                    output.append({"symbol": symbol, "provider": provider, "mode": mode, **row})
    return output


def flatten_comparison(row: dict[str, Any]) -> dict[str, Any]:
    output = dict(row)
    output["left_only_dates"] = "|".join(row["left_only_dates"])
    output["right_only_dates"] = "|".join(row["right_only_dates"])
    return output


def build_recommendations(comparisons: list[dict[str, Any]]) -> dict[str, Any]:
    preferred_pairs = (
        frozenset(("eastmoney", "twelve_data")),
        frozenset(("twelve_data", "tiingo")),
        frozenset(("eastmoney", "tiingo")),
    )
    basis_pair: frozenset[str] | None = None
    primary: list[dict[str, Any]] = []
    for pair in preferred_pairs:
        candidate = [row for row in comparisons if frozenset((row["left"], row["right"])) == pair]
        if candidate:
            basis_pair, primary = pair, candidate
            break
    raw_close_p95 = [row["close_relative_p95"] for row in primary if row["mode"] == "raw" and row["close_relative_p95"] is not None]
    adjusted_return_p95 = [row["close_return_abs_diff_p95"] for row in primary if row["mode"] == "adjusted" and row["close_return_abs_diff_p95"] is not None]
    raw_date_mismatches = sum(len(row["left_only_dates"]) + len(row["right_only_dates"]) for row in primary if row["mode"] == "raw")
    return {
        "status": "provisional_from_small_sample",
        "basis_pair": sorted(basis_pair) if basis_pair else [],
        "basis_comparisons": len(primary),
        "raw_close_relative_tolerance_candidate": max(0.001, 3 * (percentile(raw_close_p95, 0.95) or 0.0)),
        "adjusted_close_return_abs_diff_tolerance_candidate": max(
            0.001, 3 * (percentile(adjusted_return_p95, 0.95) or 0.0)
        ),
        "date_policy_candidate": "require identical completed-session dates" if raw_date_mismatches == 0 else "review date mismatches before approval",
        "volume_policy_candidate": "warning_only_until_full-market-volume semantics are independently confirmed",
        "notes": [
            "Candidate tolerances are descriptive and must not gate production until repeated shadow runs are stable.",
            "Compare raw price levels directly; compare adjusted daily returns before comparing adjusted absolute levels.",
            "Never fill a missing bar or overwrite an approved row solely because two public vendors agree.",
        ],
    }


def markdown_report(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# SPY 小样本多源行情验证",
        "",
        f"- Run：`{report['run_id']}`",
        f"- 查询窗口：{report['query']['start']} 至 {report['query']['end']}（最终比较各源共同完整交易日）",
        f"- 样本：{summary['sample_symbols']} 个；至少两源完整：{summary['symbols_with_at_least_two_sources']}；三源完整：{summary['symbols_with_all_three_sources']}。",
        f"- 数据源覆盖：东方财富 {summary['provider_coverage']['eastmoney']}、Twelve Data {summary['provider_coverage']['twelve_data']}、Tiingo {summary['provider_coverage']['tiingo']}。",
        f"- 成对比较：{summary['comparisons']}；候选通过：{summary['candidate_pass']}；需复核：{summary['review']}；重叠不足：{summary['insufficient_overlap']}",
        f"- 结论状态：`{report['validation_status']}`，本报告不批准正式数据库写入。",
        "",
        "## 样本与路由",
        "",
        "| Symbol | 选择原因 | 东方财富市场号 | Tiingo 请求代码 | 状态 |",
        "|---|---|---:|---|---|",
    ]
    failures: dict[str, list[str]] = {}
    for item in report["failures"]:
        failures.setdefault(item["symbol"], []).append(f"{item['provider']}: {item['error']}")
    reasons = {item["symbol"]: item["reason"] for item in report["sample"]}
    for symbol in [item["symbol"] for item in report["sample"]]:
        meta = report["provider_metadata"].get(symbol, {})
        east = meta.get("eastmoney", {})
        tiingo = meta.get("tiingo", {})
        status = f"部分失败：{'；'.join(failures[symbol])}" if symbol in failures else "完成"
        lines.append(
            f"| {symbol} | {reasons[symbol]} | {east.get('market', '')} | {tiingo.get('requested_symbol', '')} | {status} |"
        )
    lines.extend(
        [
            "",
            "## 关键观察",
            "",
            f"- 依据数据源对的原始收盘价候选容差：{report['recommendations']['raw_close_relative_tolerance_candidate']:.6%}。",
            f"- 依据数据源对的复权收盘收益差候选容差：{report['recommendations']['adjusted_close_return_abs_diff_tolerance_candidate']:.6%}（绝对收益率差）。",
            f"- 本轮阈值估计依据：{' + '.join(report['recommendations']['basis_pair']) or '无可用数据源对'}。",
            f"- 日期策略：{report['recommendations']['date_policy_candidate']}。",
            f"- 成交量策略：{report['recommendations']['volume_policy_candidate']}。",
            "- 原始价格用于直接逐日比较；复权价格优先比较收益率连续性；成交量目前只报警。",
            "",
            "## 需复核的比较",
            "",
            "| Symbol | 口径 | 左源 | 右源 | 共同日 | Close P95 | 收益差 P95 | 状态 |",
            "|---|---|---|---|---:|---:|---:|---|",
        ]
    )
    review_rows = [row for row in report["comparisons"] if row["comparison_status"] != "candidate_pass"]
    if not review_rows:
        lines.append("| — | — | — | — | — | — | — | 无 |")
    else:
        for row in review_rows:
            close = row["close_relative_p95"]
            ret = row["close_return_abs_diff_p95"]
            lines.append(
                f"| {row['symbol']} | {row['mode']} | {row['left']} | {row['right']} | {row['common_dates']} | "
                f"{'' if close is None else f'{close:.6%}'} | {'' if ret is None else f'{ret:.6%}'} | {row['comparison_status']} |"
            )
    lines.extend(
        [
            "",
            "## 使用边界",
            "",
            "本次数据和阈值仅用于验证接口与制定更新规则。正式增量更新器必须经过多次影子运行，且任何异常都应隔离而不是静默修补。原始响应的路径和 SHA256 记录在同目录 JSON 报告中。",
            "",
        ]
    )
    return "\n".join(lines)


def run_validation(args: argparse.Namespace) -> dict[str, Any]:
    twelve_key = os.environ.get("TWELVE_DATA_API_KEY", "")
    tiingo_token = os.environ.get("TIINGO_API_TOKEN", "")
    if not twelve_key or not tiingo_token:
        missing = [name for name, value in (("TWELVE_DATA_API_KEY", twelve_key), ("TIINGO_API_TOKEN", tiingo_token)) if not value]
        raise ValueError(f"Missing credential environment variable(s): {', '.join(missing)}")
    sample = list(SAMPLE if not args.symbols else ((symbol.strip().upper(), "CLI-selected validation symbol") for symbol in args.symbols.split(",")))
    symbols = [symbol for symbol, _reason in sample]
    check_current_sample(symbols)
    end_date = date.fromisoformat(args.end) if args.end else date.today()
    start_date = end_date - timedelta(days=args.lookback_calendar_days)
    start, end = start_date.isoformat(), end_date.isoformat()
    now = datetime.now(timezone.utc)
    run_id = args.run_id or now.strftime("run_%Y%m%dT%H%M%SZ")
    raw_run = DEFAULT_RAW_ROOT / run_id
    processed_run = DEFAULT_PROCESSED_ROOT / run_id

    all_data: dict[str, dict[str, dict[str, list[dict[str, Any]]]]] = {symbol: {} for symbol in symbols}
    provider_metadata: dict[str, dict[str, Any]] = {symbol: {} for symbol in symbols}
    if raw_run.exists() or processed_run.exists():
        raise FileExistsError(f"Validation run already exists and is immutable: {run_id}")
    failures: list[dict[str, str]] = []
    try:
        twelve_data, twelve_metadata = fetch_twelve_batch(symbols, start, end, twelve_key, raw_run)
        for symbol in symbols:
            all_data[symbol]["twelve_data"] = twelve_data[symbol]
            provider_metadata[symbol]["twelve_data"] = twelve_metadata
    except (ProviderError, subprocess.TimeoutExpired) as exc:
        for symbol in symbols:
            failures.append({"symbol": symbol, "provider": "twelve_data", "error": str(exc)})
    eastmoney_circuit_error: str | None = None
    for symbol in symbols:
        if eastmoney_circuit_error is not None:
            failures.append(
                {
                    "symbol": symbol,
                    "provider": "eastmoney",
                    "error": f"provider circuit open after prior transport failure: {eastmoney_circuit_error}",
                }
            )
        else:
            try:
                east_data, east_meta, transport = fetch_eastmoney(symbol, start, end, raw_run)
                all_data[symbol]["eastmoney"] = east_data
                provider_metadata[symbol]["eastmoney"] = {**east_meta, "market_discovery": transport}
            except TransportError as exc:
                eastmoney_circuit_error = str(exc)
                failures.append({"symbol": symbol, "provider": "eastmoney", "error": str(exc)})
            except (ProviderError, subprocess.TimeoutExpired) as exc:
                failures.append({"symbol": symbol, "provider": "eastmoney", "error": str(exc)})
        try:
            tiingo_data, tiingo_meta = fetch_tiingo(symbol, start, end, tiingo_token, raw_run)
            all_data[symbol]["tiingo"] = tiingo_data
            provider_metadata[symbol]["tiingo"] = tiingo_meta
        except (ProviderError, subprocess.TimeoutExpired) as exc:
            failures.append({"symbol": symbol, "provider": "tiingo", "error": str(exc)})

    comparisons: list[dict[str, Any]] = []
    pairs = (("eastmoney", "twelve_data"), ("eastmoney", "tiingo"), ("twelve_data", "tiingo"))
    for symbol in symbols:
        providers = all_data[symbol]
        for left, right in pairs:
            if left not in providers or right not in providers:
                continue
            for mode in ("raw", "adjusted"):
                comparisons.append(compare_series(symbol, mode, left, providers[left][mode], right, providers[right][mode]))
    recommendations = build_recommendations(comparisons)
    status_counts: dict[str, int] = {}
    for comparison in comparisons:
        status = comparison["comparison_status"]
        status_counts[status] = status_counts.get(status, 0) + 1
    provider_coverage = {
        provider: sum(1 for symbol in symbols if provider in all_data[symbol])
        for provider in ("eastmoney", "twelve_data", "tiingo")
    }
    two_source_complete = sum(1 for symbol in symbols if len(all_data[symbol]) >= 2)
    three_source_complete = sum(1 for symbol in symbols if len(all_data[symbol]) == 3)
    if two_source_complete < len(symbols):
        validation_status = "insufficient_multi_source_coverage"
    elif three_source_complete < len(symbols):
        validation_status = "two_source_cross_check_passed_with_provider_gap"
    elif status_counts.get("review", 0) or status_counts.get("insufficient_overlap", 0):
        validation_status = "three_source_cross_check_requires_review"
    else:
        validation_status = "three_source_cross_check_candidate_pass"
    report: dict[str, Any] = {
        "schema_version": 2,
        "run_id": run_id,
        "generated_at_utc": now.isoformat().replace("+00:00", "Z"),
        "purpose": "isolated small-sample source validation; no approved database mutation",
        "validation_status": validation_status,
        "query": {"start": start, "end": end, "lookback_calendar_days": args.lookback_calendar_days},
        "sample": [{"symbol": symbol, "reason": reason} for symbol, reason in sample],
        "providers": {
            "eastmoney": {"role": "candidate_primary", "adjustments": {"raw": "fqt=0", "adjusted": "fqt=1"}},
            "twelve_data": {"role": "daily_independent_validator", "adjustments": {"raw": "none", "adjusted": "all"}},
            "tiingo": {"role": "rotating_independent_validator", "adjustments": {"raw": "unprefixed", "adjusted": "adj*"}},
        },
        "summary": {
            "sample_symbols": len(symbols),
            "symbols_with_at_least_two_sources": two_source_complete,
            "symbols_with_all_three_sources": three_source_complete,
            "symbols_with_fewer_than_two_sources": len(symbols) - two_source_complete,
            "provider_coverage": provider_coverage,
            "provider_failures": len(failures),
            "comparisons": len(comparisons),
            "candidate_pass": status_counts.get("candidate_pass", 0),
            "review": status_counts.get("review", 0),
            "insufficient_overlap": status_counts.get("insufficient_overlap", 0),
        },
        "failures": failures,
        "recommendations": recommendations,
        "provider_metadata": provider_metadata,
        "comparisons": comparisons,
    }
    normalized = normalized_rows(all_data)
    normalized_content = csv_bytes(("symbol", "provider", "mode", "date", *FIELDS), normalized)
    comparison_rows = [flatten_comparison(row) for row in comparisons]
    comparison_fields = tuple(comparison_rows[0]) if comparison_rows else ("symbol",)
    comparisons_content = csv_bytes(comparison_fields, comparison_rows)
    normalized_path = processed_run / "normalized_prices.csv"
    comparisons_path = processed_run / "pairwise_comparisons.csv"
    atomic_write(normalized_path, normalized_content)
    atomic_write(comparisons_path, comparisons_content)
    report["artifacts"] = {
        "normalized_prices": {
            "path": str(normalized_path.relative_to(WORKSPACE_ROOT)),
            "sha256": sha256_bytes(normalized_content),
            "rows": len(normalized),
        },
        "pairwise_comparisons": {
            "path": str(comparisons_path.relative_to(WORKSPACE_ROOT)),
            "sha256": sha256_bytes(comparisons_content),
            "rows": len(comparison_rows),
        },
    }
    report_content = json_bytes(report)
    markdown_content = markdown_report(report).encode("utf-8")
    atomic_write(processed_run / "report.json", report_content)
    atomic_write(processed_run / "report.md", markdown_content)
    latest = {
        "schema_version": 1,
        "run_id": run_id,
        "report_json": str((processed_run / "report.json").relative_to(WORKSPACE_ROOT)),
        "report_markdown": str((processed_run / "report.md").relative_to(WORKSPACE_ROOT)),
        "report_sha256": sha256_bytes(report_content),
        "summary": report["summary"],
    }
    if not args.symbols or getattr(args, "set_latest", False):
        atomic_write(DEFAULT_PROCESSED_ROOT / "latest.json", json_bytes(latest))
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--end", help="Inclusive query end date (YYYY-MM-DD); defaults to local today")
    parser.add_argument("--lookback-calendar-days", type=int, default=100)
    parser.add_argument("--symbols", help="Optional comma-separated current-SPY subset")
    parser.add_argument("--run-id", help="Explicit immutable run directory name")
    parser.add_argument(
        "--set-latest",
        action="store_true",
        help="Allow an explicit --symbols diagnostic subset to replace the representative latest pointer",
    )
    return parser.parse_args()


def main() -> int:
    report = run_validation(parse_args())
    print(json.dumps({"run_id": report["run_id"], "summary": report["summary"]}, ensure_ascii=False))
    return 0 if report["validation_status"] == "three_source_cross_check_candidate_pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
