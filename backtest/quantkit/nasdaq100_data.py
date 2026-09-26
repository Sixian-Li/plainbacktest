"""Audit purchased point-in-time index packages and build review-only metadata.

The builder deliberately does not emit canonical price files.  It inventories the
immutable evidence, validates both adjustment variants, and writes only compact
candidate membership and identity products under a registry-controlled
``pending_review`` directory.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import tempfile
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import exchange_calendars as xcals


SOURCE_COLUMNS = (
    "Date",
    "CompanyName",
    "Symbol",
    "Open",
    "High",
    "Low",
    "Close",
    "Volume",
    "Unadjusted Close",
    "InIndex",
)
PRICE_COLUMNS = ("Open", "High", "Low", "Close")
SECURITY_MASTER_COLUMNS = (
    "security_id",
    "source_file",
    "source_symbol",
    "display_ticker",
    "company_name",
    "first_price_date",
    "last_price_date",
    "price_rows",
    "first_in_index_date",
    "last_in_index_date",
    "in_index_observation_days",
    "membership_interval_count",
    "active_on_2026_08_04",
    "filename_exit_suffix",
    "identity_status",
    "anomaly_notes",
)
MEMBERSHIP_INTERVAL_COLUMNS = (
    "security_id",
    "source_symbol",
    "effective_start",
    "effective_end",
    "source_file",
    "interval_number",
    "previous_observed_date",
    "next_observed_date",
    "start_boundary",
    "end_boundary",
    "missing_price_sessions",
)
MEMBERSHIP_DAILY_COUNT_COLUMNS = (
    "date",
    "member_count",
    "members_with_price",
    "members_missing_price",
    "missing_price_symbols",
)
MEMBERSHIP_CHANGE_COLUMNS = (
    "effective_date",
    "previous_session",
    "member_count",
    "added_count",
    "deleted_count",
    "added_source_symbols",
    "deleted_source_symbols",
    "added_display_tickers",
    "deleted_display_tickers",
)
DATE_SUFFIX = re.compile(r"-(\d{6})$")


class PointInTimeDataError(RuntimeError):
    """The source evidence or candidate contract failed a safety gate."""


# Backward-compatible public name used by the original Nasdaq-100 CLI/tests.
Nasdaq100DataError = PointInTimeDataError


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def directory_manifest(path: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for item in sorted((candidate for candidate in path.rglob("*") if candidate.is_file()), key=lambda p: p.relative_to(path).as_posix()):
        files.append(
            {
                "path": item.relative_to(path).as_posix(),
                "size_bytes": item.stat().st_size,
                "sha256": sha256_file(item),
            }
        )
    digest = hashlib.sha256()
    for item in files:
        digest.update(f"{item['path']}\0{item['size_bytes']}\0{item['sha256']}\n".encode("utf-8"))
    return {
        "file_count": len(files),
        "size_bytes": sum(int(item["size_bytes"]) for item in files),
        "directory_sha256": digest.hexdigest(),
        "files": files,
    }


def csv_bytes(columns: Iterable[str], rows: Iterable[dict[str, Any]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=tuple(columns), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def clean_display_ticker(source_symbol: str) -> str:
    return DATE_SUFFIX.sub("", source_symbol)


def candidate_security_id(source_file: str, prefix: str = "NDX-CAND") -> str:
    token = hashlib.sha256(source_file.encode("utf-8")).hexdigest()[:16].upper()
    return f"{prefix}-{token}"


def _record(
    counts: Counter[str],
    examples: dict[str, list[dict[str, Any]]],
    maximum: int,
    name: str,
    detail: dict[str, Any],
) -> None:
    counts[name] += 1
    if len(examples[name]) < maximum:
        examples[name].append(detail)


def load_sessions(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    sessions = [str(row["date"]) for row in rows]
    if sessions != sorted(set(sessions)):
        raise Nasdaq100DataError(f"Invalid XNYS calendar ordering: {path}")
    return sessions


def registry_sessions(workspace: Path, registry: dict[str, Any]) -> list[str]:
    """Load the checked-in calendar or build an explicit bounded XNYS calendar."""
    calendar = registry.get("calendar", {})
    if calendar.get("source") == "exchange_calendars":
        start = str(calendar["start"])
        end = str(calendar["end"])
        exchange = xcals.get_calendar(str(calendar.get("name", "XNYS")), start=start, end=end)
        return [session.date().isoformat() for session in exchange.sessions]
    path = workspace / str(calendar.get("path", "data/processed/calendars/XNYS.csv"))
    return load_sessions(path)


def build_membership_intervals(
    source_file: str,
    source_symbol: str,
    security_id: str,
    rows: list[dict[str, Any]],
    sessions: list[str],
) -> list[dict[str, Any]]:
    """Build maximal observed 1-runs; a missing price row does not invent an exit."""
    positions = {day: index for index, day in enumerate(sessions)}
    intervals: list[dict[str, Any]] = []
    run_start: int | None = None
    for index, row in enumerate(rows):
        active = row["in_index"] == 1
        if active and run_start is None:
            run_start = index
        at_end = index == len(rows) - 1
        if run_start is not None and ((not active) or at_end):
            run_end = index if active and at_end else index - 1
            previous = "" if run_start == 0 else rows[run_start - 1]["date"]
            following = "" if run_end == len(rows) - 1 else rows[run_end + 1]["date"]
            start = rows[run_start]["date"]
            end = rows[run_end]["date"]
            if not previous:
                start_boundary = "left_censored"
            elif start in positions and previous in positions and positions[start] - positions[previous] == 1:
                start_boundary = "adjacent_observed_0_to_1"
            else:
                start_boundary = "interval_censored_0_to_1"
            if not following:
                end_boundary = "right_censored"
            elif end in positions and following in positions and positions[following] - positions[end] == 1:
                end_boundary = "adjacent_observed_1_to_0"
            else:
                end_boundary = "interval_censored_1_to_0"
            expected = (
                sessions[positions[start] : positions[end] + 1]
                if start in positions and end in positions
                else []
            )
            observed = {str(item["date"]) for item in rows[run_start : run_end + 1] if item["in_index"] == 1}
            missing = [day for day in expected if day not in observed]
            intervals.append(
                {
                    "security_id": security_id,
                    "source_symbol": source_symbol,
                    "effective_start": start,
                    "effective_end": end,
                    "source_file": source_file,
                    "interval_number": len(intervals) + 1,
                    "previous_observed_date": previous,
                    "next_observed_date": following,
                    "start_boundary": start_boundary,
                    "end_boundary": end_boundary,
                    "missing_price_sessions": ";".join(missing),
                }
            )
            run_start = None
    return intervals


def _identity_status(
    source_symbol: str,
    company_name: str,
    special_cases: dict[str, dict[str, str]] | None = None,
) -> tuple[str, list[str]]:
    display = clean_display_ticker(source_symbol)
    notes: list[str] = []
    if DATE_SUFFIX.search(source_symbol):
        status = "source_suffix_unverified"
        notes.append("dated source suffix preserved; no predecessor/successor merge inferred")
    else:
        status = "source_file_identity_pending_review"
    if display in {"GOOG", "GOOGL"}:
        status = "separate_share_class"
        notes.append("Alphabet share classes remain separate candidate securities")
    if display == "META":
        status = "vendor_backfilled_current_ticker_pending_review"
        notes.append("source backfills META/name to 2012; no FB record is fabricated")
    if display == "BKNG":
        status = "vendor_backfilled_current_ticker_pending_review"
        notes.append("source backfills BKNG/name to 1999; no PCLN record is fabricated")
    if display == "AABA":
        notes.append("AABA source record is not merged with a historical YHOO identity")
    if "Class " in company_name:
        notes.append("share-class label retained from source company name")
    configured = (special_cases or {}).get(display)
    if configured:
        status = configured.get("status", status)
        if configured.get("note"):
            notes.append(str(configured["note"]))
    return status, notes


def parse_source_csv(
    source_file: str,
    content: bytes,
    sessions: list[str],
    policy: dict[str, Any],
    *,
    security_id_prefix: str = "NDX-CAND",
    active_date: str = "2026-08-04",
    identity_special_cases: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    maximum = int(policy.get("max_issue_examples", 10))
    line_endings = {
        "bom_utf8": content.startswith(b"\xef\xbb\xbf"),
        "crlf_lines": content.count(b"\r\n"),
        "bare_lf_lines": content.count(b"\n") - content.count(b"\r\n"),
        "bare_cr_lines": content.count(b"\r") - content.count(b"\r\n"),
    }
    if not content:
        _record(counts, examples, maximum, "empty_file", {"source_file": source_file})
        return {"rows": [], "intervals": [], "issue_counts": dict(counts), "issue_examples": dict(examples), "line_endings": line_endings}
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        _record(counts, examples, maximum, "bad_encoding", {"detail": str(exc)})
        return {"rows": [], "intervals": [], "issue_counts": dict(counts), "issue_examples": dict(examples), "line_endings": line_endings}
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if tuple(reader.fieldnames or ()) != SOURCE_COLUMNS:
        _record(
            counts,
            examples,
            maximum,
            "unexpected_schema",
            {"actual": reader.fieldnames, "expected": list(SOURCE_COLUMNS)},
        )
    stem = Path(source_file).stem
    rows: list[dict[str, Any]] = []
    seen_dates: set[str] = set()
    symbols: set[str] = set()
    companies: set[str] = set()
    previous_date: str | None = None
    previous_close: float | None = None
    previous_in_index: int | None = None
    tolerance_factor = float(policy.get("ohlc_relative_tolerance", 1e-9))
    outlier_threshold = float(policy.get("outlier_abs_close_return_pct", 50.0)) / 100.0
    for row_number, raw in enumerate(reader, start=2):
        if None in raw or any(raw.get(column) is None or not str(raw.get(column)).strip() for column in SOURCE_COLUMNS):
            _record(counts, examples, maximum, "bad_row", {"row": row_number})
            continue
        day = str(raw["Date"]).strip()
        try:
            date.fromisoformat(day)
        except ValueError:
            _record(counts, examples, maximum, "bad_date", {"row": row_number, "value": day})
            continue
        if day in seen_dates:
            _record(counts, examples, maximum, "duplicate_date", {"row": row_number, "date": day})
        if previous_date is not None and day <= previous_date:
            _record(
                counts,
                examples,
                maximum,
                "source_order_violation",
                {"row": row_number, "previous": previous_date, "date": day},
            )
        seen_dates.add(day)
        previous_date = day
        symbol = str(raw["Symbol"]).strip()
        company = str(raw["CompanyName"]).strip()
        symbols.add(symbol)
        companies.add(company)
        try:
            numeric = {column: float(str(raw[column]).strip()) for column in (*PRICE_COLUMNS, "Volume", "Unadjusted Close")}
            if any(not math.isfinite(value) for value in numeric.values()):
                raise ValueError("non-finite value")
        except ValueError as exc:
            _record(counts, examples, maximum, "bad_number", {"row": row_number, "date": day, "detail": str(exc)})
            continue
        prices = [numeric[column] for column in PRICE_COLUMNS]
        if min(prices) <= 0:
            _record(counts, examples, maximum, "nonpositive_price", {"row": row_number, "date": day})
        tolerance = tolerance_factor * max(1.0, *(abs(value) for value in prices))
        if numeric["High"] + tolerance < max(prices) or numeric["Low"] - tolerance > min(prices):
            _record(counts, examples, maximum, "invalid_ohlc", {"row": row_number, "date": day})
        if numeric["Volume"] < 0:
            _record(counts, examples, maximum, "negative_volume", {"row": row_number, "date": day})
        elif numeric["Volume"] == 0:
            _record(counts, examples, maximum, "zero_volume", {"row": row_number, "date": day})
        if numeric["Unadjusted Close"] <= 0:
            _record(counts, examples, maximum, "nonpositive_unadjusted_close", {"row": row_number, "date": day})
        try:
            in_index = int(str(raw["InIndex"]).strip())
        except ValueError:
            in_index = -1
        if in_index not in (0, 1):
            _record(counts, examples, maximum, "bad_in_index", {"row": row_number, "date": day, "value": raw["InIndex"]})
        if previous_close is not None and previous_close > 0:
            move = numeric["Close"] / previous_close - 1.0
            if abs(move) >= outlier_threshold:
                _record(counts, examples, maximum, "outlier_close_return", {"date": day, "return_pct": round(move * 100.0, 8)})
                if previous_in_index == 1 and in_index == 1:
                    _record(counts, examples, maximum, "member_outlier_close_return", {"date": day, "return_pct": round(move * 100.0, 8)})
        previous_close = numeric["Close"]
        previous_in_index = in_index
        rows.append(
            {
                "date": day,
                "company_name": company,
                "symbol": symbol,
                "open": numeric["Open"],
                "high": numeric["High"],
                "low": numeric["Low"],
                "close": numeric["Close"],
                "volume": numeric["Volume"],
                "unadjusted_close": numeric["Unadjusted Close"],
                "in_index": in_index,
            }
        )
    if len(symbols) != 1:
        _record(counts, examples, maximum, "unstable_symbol", {"values": sorted(symbols)})
    if symbols and symbols != {stem}:
        _record(counts, examples, maximum, "filename_symbol_mismatch", {"filename": stem, "values": sorted(symbols)})
    if len(companies) != 1:
        _record(counts, examples, maximum, "unstable_company_name", {"values": sorted(companies)[:maximum]})
    if rows and not any(row["in_index"] == 1 for row in rows):
        _record(counts, examples, maximum, "never_in_index", {"source_file": source_file})
    source_symbol = next(iter(symbols), stem)
    company_name = next(iter(companies), "")
    security_id = candidate_security_id(source_file, security_id_prefix)
    intervals = build_membership_intervals(source_file, source_symbol, security_id, rows, sessions)
    missing_sessions = [
        day
        for interval in intervals
        for day in str(interval["missing_price_sessions"]).split(";")
        if day
    ]
    for day in missing_sessions:
        _record(counts, examples, maximum, "missing_in_index_session", {"date": day})
    if len(intervals) > 1:
        _record(counts, examples, maximum, "reentered_index", {"membership_interval_count": len(intervals)})
    active_rows = [row for row in rows if row["in_index"] == 1]
    suffix_match = DATE_SUFFIX.search(stem)
    suffix = suffix_match.group(1) if suffix_match else ""
    if suffix and active_rows and active_rows[-1]["date"][:7].replace("-", "") != suffix:
        _record(
            counts,
            examples,
            maximum,
            "filename_suffix_not_last_in_index_month",
            {"filename_suffix": suffix, "last_in_index_date": active_rows[-1]["date"]},
        )
    status, notes = _identity_status(source_symbol, company_name, identity_special_cases)
    if suffix and rows and rows[-1]["date"][:7].replace("-", "") != suffix:
        notes.append("dated suffix does not match last price month")
    metadata = {
        "security_id": security_id,
        "source_file": source_file,
        "source_symbol": source_symbol,
        "display_ticker": clean_display_ticker(source_symbol),
        "company_name": company_name,
        "first_price_date": rows[0]["date"] if rows else "",
        "last_price_date": rows[-1]["date"] if rows else "",
        "price_rows": len(rows),
        "first_in_index_date": active_rows[0]["date"] if active_rows else "",
        "last_in_index_date": active_rows[-1]["date"] if active_rows else "",
        "in_index_observation_days": len(active_rows),
        "membership_interval_count": len(intervals),
        "active_on_2026_08_04": str(any(item["effective_start"] <= active_date <= item["effective_end"] for item in intervals)).lower(),
        "filename_exit_suffix": suffix,
        "identity_status": status,
        "anomaly_notes": "; ".join(notes),
    }
    return {
        "rows": rows,
        "metadata": metadata,
        "intervals": intervals,
        "issue_counts": dict(sorted(counts.items())),
        "issue_examples": {name: values for name, values in sorted(examples.items())},
        "line_endings": line_endings,
        "source_sha256": sha256_bytes(content),
    }


def reconstruct_daily_members(
    intervals: list[dict[str, Any]], sessions: list[str]
) -> dict[str, set[str]]:
    members: dict[str, set[str]] = {day: set() for day in sessions}
    positions = {day: index for index, day in enumerate(sessions)}
    for interval in intervals:
        start = str(interval["effective_start"])
        end = str(interval["effective_end"])
        if start not in positions or end not in positions:
            raise Nasdaq100DataError(f"Membership interval falls outside XNYS calendar: {interval}")
        for day in sessions[positions[start] : positions[end] + 1]:
            members[day].add(str(interval["source_symbol"]))
    return {day: value for day, value in members.items() if value}


def _root_inventory(source_root: Path) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for item in sorted(source_root.iterdir(), key=lambda p: p.name):
        if item.is_file():
            inventory.append(
                {
                    "name": item.name,
                    "kind": "file",
                    "size_bytes": item.stat().st_size,
                    "sha256": sha256_file(item),
                }
            )
        elif item.is_dir():
            manifest = directory_manifest(item)
            inventory.append(
                {
                    "name": item.name,
                    "kind": "directory",
                    "size_bytes": manifest["size_bytes"],
                    "file_count": manifest["file_count"],
                    "directory_sha256": manifest["directory_sha256"],
                }
            )
    return inventory


def _compare_package_rows(
    split: dict[str, Any], dividend: dict[str, Any], comparison: Counter[str]
) -> None:
    split_rows = split["rows"]
    dividend_rows = dividend["rows"]
    comparison["compared_files"] += 1
    comparison["split_rows"] += len(split_rows)
    comparison["split_and_dividend_rows"] += len(dividend_rows)
    if len(split_rows) != len(dividend_rows):
        comparison["package_row_key_mismatch"] += abs(len(split_rows) - len(dividend_rows)) or 1
    for left, right in zip(split_rows, dividend_rows):
        key_fields = ("date", "company_name", "symbol")
        if any(left[field] != right[field] for field in key_fields):
            comparison["package_row_key_mismatch"] += 1
            continue
        if left["in_index"] != right["in_index"]:
            comparison["package_in_index_mismatch"] += 1
        if left["unadjusted_close"] != right["unadjusted_close"]:
            comparison["package_unadjusted_close_mismatch"] += 1
        ohlc_differs = False
        for field in ("open", "high", "low", "close"):
            if left[field] == right[field]:
                continue
            ohlc_differs = True
            comparison[f"{field}_different_rows"] += 1
            absolute = abs(float(left[field]) - float(right[field]))
            relative = absolute / max(abs(float(left[field])), abs(float(right[field])), 1e-300)
            comparison[f"{field}_max_absolute_difference"] = max(
                comparison[f"{field}_max_absolute_difference"], absolute
            )
            comparison[f"{field}_max_relative_difference"] = max(
                comparison[f"{field}_max_relative_difference"], relative
            )
        volume_differs = left["volume"] != right["volume"]
        if ohlc_differs:
            comparison["adjusted_ohlc_different_rows"] += 1
        else:
            comparison["adjusted_ohlc_equal_rows"] += 1
        if volume_differs:
            comparison["volume_different_rows"] += 1
            absolute = abs(float(left["volume"]) - float(right["volume"]))
            relative = absolute / max(abs(float(left["volume"])), abs(float(right["volume"])), 1e-300)
            comparison["volume_max_absolute_difference"] = max(
                comparison["volume_max_absolute_difference"], absolute
            )
            comparison["volume_max_relative_difference"] = max(
                comparison["volume_max_relative_difference"], relative
            )
        if ohlc_differs or volume_differs:
            comparison["files_with_adjusted_value_differences"] += 0  # Counted once by caller.


def _official_cross_checks(
    checks: list[dict[str, Any]], daily: dict[str, set[str]]
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for check in checks:
        previous = daily.get(check["previous_session"], set())
        current = daily.get(check["effective_date"], set())
        actual_additions = sorted(clean_display_ticker(item) for item in current - previous)
        actual_deletions = sorted(clean_display_ticker(item) for item in previous - current)
        expected_additions = sorted(check["expected_additions"])
        expected_deletions = sorted(check["expected_deletions"])
        sets_match = actual_additions == expected_additions and actual_deletions == expected_deletions
        if sets_match and check.get("official_ticker_difference"):
            status = "pass_with_identity_review"
        else:
            status = "pass" if sets_match else "review"
        results.append(
            {
                **check,
                "actual_additions": actual_additions,
                "actual_deletions": actual_deletions,
                "status": status,
            }
        )
    return results


def _quality_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    comparison = report["package_comparison"]
    daily = report["point_in_time"]
    blocking = report["blocking_issue_counts"]
    warnings = report["warning_issue_counts"]
    index_name = str(report.get("index_display_name", "Nasdaq-100"))
    nominal_count = int(report.get("nominal_company_count", 100))
    extracted_status = comparison.get("extracted_comparison_status", "compared")
    if extracted_status == "not_applicable_no_extracted_source":
        extracted_line = "- 原始目录没有已解压证据；审计直接读取两个 ZIP，且没有为本任务创建解压副本。"
    else:
        extracted_line = (
            "- 已解压目录与股息复权 ZIP 逐文件一致："
            f"{str(comparison['extracted_matches_archive']).lower()}。"
        )
    lines = [
        f"# {index_name} 历史成分候选数据质量报告",
        "",
        "> 状态：`pending_review` / `candidate`。本产物不是 approved 数据，也没有写入正式价格库。",
        "",
        "## 结论",
        "",
        f"- 工具可解析 {summary['instrument_count']} 个独立来源证券文件，共 {summary['price_row_count']:,} 行。",
        f"- `InIndex=1` 观察范围：{daily['first_membership_date']} 至 {daily['last_membership_date']}；直接可见行每日成分数 {daily['direct_observation_minimum_member_count']}–{daily['direct_observation_maximum_member_count']}，按连续区间重建后为 {daily['minimum_member_count']}–{daily['maximum_member_count']}。",
        f"- 阻断问题：{sum(blocking.values())}；官方变更抽查：{summary['official_cross_checks_passed']}/{summary['official_cross_checks_total']} 通过。",
        "- 该数据保留历史剔除/退市文件和历史时点成员状态，能避免“用今日成分回看历史”这一类幸存者偏差；但来源商、授权、取得日期和早期覆盖仍待人工确认。",
        "",
        "## 原始包一致性",
        "",
        f"- 两个 ZIP 成员数：{comparison['split_member_count']} / {comparison['split_and_dividend_member_count']}；成员集合一致：{str(comparison['member_sets_identical']).lower()}。",
        extracted_line,
        f"- `Date/CompanyName/Symbol/InIndex/Unadjusted Close` 差异行：{comparison['package_row_key_mismatch'] + comparison['package_in_index_mismatch'] + comparison['package_unadjusted_close_mismatch']}。",
        f"- 复权 OHLC 差异行：{comparison['adjusted_ohlc_different_rows']:,}；Volume 差异行：{comparison['volume_different_rows']:,}。",
        "",
        "| 字段 | 差异行 | 最大绝对差 | 最大相对差 |",
        "|---|---:|---:|---:|",
        *(
            f"| {field.title()} | {comparison[f'{field}_different_rows']:,} | "
            f"{comparison[f'{field}_max_absolute_difference']:.9g} | "
            f"{comparison[f'{field}_max_relative_difference']:.6%} |"
            for field in ("open", "high", "low", "close")
        ),
        f"| Volume | {comparison['volume_different_rows']:,} | {comparison['volume_max_absolute_difference']:.9g} | {comparison['volume_max_relative_difference']:.6%} |",
        "",
        "## 复权口径边界",
        "",
        "- 价格趋势指标：根据策略经济含义明确选择“仅拆股” Close（价格趋势）或“拆股及股息” Close（总收益趋势近似），不得隐式混用。",
        "- 实际成交价：`Unadjusted Close` 是唯一明确未复权价列；源中没有完整未复权 OHLC，因而无法仅凭本包精确重建开高低成交。",
        "- 总收益：可使用股息复权 Close 作近似，但没有独立股息/拆股事件账本，不能证明精确现金再投资。",
        "- 股息复权会随后续股息回溯改写历史比例；用于实时信号时必须记录数据快照，否则存在可解释性/可复现性问题。",
        "",
        "## 阻断问题",
        "",
    ]
    if not any(blocking.values()):
        lines.append("- 无。")
    else:
        lines.extend(f"- `{name}`: {count}" for name, count in blocking.items() if count)
    lines.extend(["", "## 待审警告", ""])
    lines.extend(f"- `{name}`: {count}" for name, count in warnings.items() if count)
    lines.extend(
        [
            "",
            f"## 成分证券数不等于 {nominal_count} 的原因",
            "",
            str(report.get("member_count_explanation", "多股类和公司行动可能令证券数与名义公司数不同；异常日期仍需逐项复核。")),
            "",
            "## 官方一手来源",
            "",
        ]
    )
    lines.extend(
        f"- [{item['topic']}]({item['url']})"
        for item in report.get("official_primary_sources", [])
    )
    lines.extend(
        [
            *(
                f"- [{item['effective_date']} 成分调整]({item['url']})：{item['status']}"
                for item in report.get("official_cross_checks", [])
            ),
            "",
            "## 使用门禁",
            "",
            f"- 建议的机器可读范围为 {daily['safe_machine_readable_range'][0]} 至 {daily['safe_machine_readable_range'][1]}；起点明确标为左截断/待复核。",
            "- 在 provider、license/authorization、acquired_date、身份映射和复权用途未完成人工签核前，不得用于正式 ROT 结论或晋升 approved。",
            "- 决策时间早于收盘或没有公告时间证据时，对成分状态至少滞后一个 XNYS 交易日。",
            "",
        ]
    )
    return "\n".join(lines)


def audit_and_build(
    registry_path: Path,
    workspace: Path,
    *,
    write_candidate: bool,
) -> dict[str, Any]:
    registry_bytes = registry_path.read_bytes()
    registry = json.loads(registry_bytes.decode("utf-8"))
    index_name = str(registry.get("index_display_name", "Nasdaq-100"))
    security_id_prefix = str(registry.get("security_id_prefix", "NDX-CAND"))
    active_date = str(registry.get("active_snapshot_date", "2026-08-04"))
    nominal_count = int(registry.get("nominal_company_count", 100))
    sample_dates = tuple(
        str(item)
        for item in registry.get(
            "sample_dates",
            ["1993-10-01", "2000-01-03", "2010-01-04", "2020-01-02", "2026-08-04"],
        )
    )
    if registry.get("production_writes_enabled") is not False:
        raise PointInTimeDataError(f"{index_name} candidate builder requires production_writes_enabled=false")
    if registry.get("review_status") != "pending_review":
        raise PointInTimeDataError(f"{index_name} registry must remain pending_review")
    if registry.get("promotion_policy", {}).get("auto_promote_to_approved") is not False:
        raise PointInTimeDataError("Candidate registry must hard-disable automatic approval")
    output_root = workspace / registry["outputs"]["directory"]
    if "pending_review" not in output_root.parts:
        raise PointInTimeDataError(f"Candidate output is not in pending_review: {output_root}")
    approved_root = (workspace / "data/processed/daily/equities").resolve()
    if output_root.resolve() == approved_root or approved_root in output_root.resolve().parents:
        raise PointInTimeDataError("Candidate output cannot be inside the approved price library")
    source_root = workspace / registry["source_root"]
    before_inventory = _root_inventory(source_root)
    sessions = registry_sessions(workspace, registry)
    policy = registry["quality_policy"]
    split_path = workspace / registry["packages"]["split_only"]["path"]
    dividend_path = workspace / registry["packages"]["split_and_dividend"]["path"]
    extracted_evidence = registry.get("extracted_evidence")
    extracted_path = workspace / extracted_evidence["path"] if extracted_evidence else None
    for package in registry["packages"].values():
        path = workspace / package["path"]
        actual_hash = sha256_file(path)
        if actual_hash != package["sha256"]:
            raise PointInTimeDataError(f"Source archive hash mismatch: {path}: {actual_hash}")
        if path.stat().st_size != int(package["size_bytes"]):
            raise PointInTimeDataError(f"Source archive size mismatch: {path}")
    if extracted_path is not None:
        extracted_manifest = directory_manifest(extracted_path)
        if extracted_manifest["directory_sha256"] != extracted_evidence["directory_sha256"]:
            raise PointInTimeDataError("Extracted source directory hash mismatch")

    aggregate_counts: Counter[str] = Counter()
    aggregate_examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    comparison: Counter[str] = Counter()
    securities: list[dict[str, Any]] = []
    intervals: list[dict[str, Any]] = []
    instrument_reports: list[dict[str, Any]] = []
    observed_price_dates: dict[str, set[str]] = {}
    direct_daily: dict[str, set[str]] = defaultdict(set)
    split_hashes: Counter[str] = Counter()
    dividend_hashes: Counter[str] = Counter()
    encoding_summary = Counter()
    archive_test_results: dict[str, Any] = {}
    extracted_mismatch_examples: list[str] = []
    with zipfile.ZipFile(split_path) as split_archive, zipfile.ZipFile(dividend_path) as dividend_archive:
        split_bad = split_archive.testzip()
        dividend_bad = dividend_archive.testzip()
        archive_test_results = {"split_only_bad_member": split_bad, "split_and_dividend_bad_member": dividend_bad}
        if split_bad or dividend_bad:
            aggregate_counts["archive_integrity_failure"] += int(bool(split_bad)) + int(bool(dividend_bad))
        split_members = sorted(split_archive.namelist())
        dividend_members = sorted(dividend_archive.namelist())
        if split_members != dividend_members:
            aggregate_counts["archive_member_set_mismatch"] += len(set(split_members) ^ set(dividend_members)) or 1
        common_members = sorted(set(split_members) & set(dividend_members))
        comparison["split_member_count"] = len(split_members)
        comparison["split_and_dividend_member_count"] = len(dividend_members)
        comparison["split_uncompressed_bytes"] = sum(item.file_size for item in split_archive.infolist())
        comparison["split_and_dividend_uncompressed_bytes"] = sum(item.file_size for item in dividend_archive.infolist())
        comparison["split_non_csv_member_count"] = sum(not member.endswith(".csv") for member in split_members)
        comparison["split_and_dividend_non_csv_member_count"] = sum(
            not member.endswith(".csv") for member in dividend_members
        )
        comparison["member_sets_identical"] = int(split_members == dividend_members)
        for variant, members, uncompressed in (
            ("split_only", split_members, comparison["split_uncompressed_bytes"]),
            ("split_and_dividend", dividend_members, comparison["split_and_dividend_uncompressed_bytes"]),
        ):
            declared = registry["packages"][variant]
            if "archive_members" in declared and len(members) != int(declared["archive_members"]):
                aggregate_counts["archive_member_count_mismatch"] += 1
            if "uncompressed_bytes" in declared and int(uncompressed) != int(declared["uncompressed_bytes"]):
                aggregate_counts["archive_uncompressed_size_mismatch"] += 1
        for member in common_members:
            split_content = split_archive.read(member)
            dividend_content = dividend_archive.read(member)
            split_hashes[sha256_bytes(split_content)] += 1
            dividend_hashes[sha256_bytes(dividend_content)] += 1
            if extracted_path is not None:
                extracted_file = extracted_path / member
                if not extracted_file.is_file() or sha256_file(extracted_file) != sha256_bytes(dividend_content):
                    aggregate_counts["extracted_content_mismatch"] += 1
                    if len(extracted_mismatch_examples) < int(policy["max_issue_examples"]):
                        extracted_mismatch_examples.append(member)
            parse_options = {
                "security_id_prefix": security_id_prefix,
                "active_date": active_date,
                "identity_special_cases": registry.get("identity_special_cases", {}),
            }
            split_parsed = parse_source_csv(member, split_content, sessions, policy, **parse_options)
            dividend_parsed = parse_source_csv(member, dividend_content, sessions, policy, **parse_options)
            before_differences = comparison["adjusted_ohlc_different_rows"] + comparison["volume_different_rows"]
            _compare_package_rows(split_parsed, dividend_parsed, comparison)
            after_differences = comparison["adjusted_ohlc_different_rows"] + comparison["volume_different_rows"]
            if after_differences > before_differences:
                comparison["files_with_adjusted_value_differences"] += 1
            for variant, parsed in (("split_only", split_parsed), ("split_and_dividend", dividend_parsed)):
                for name, count in parsed["issue_counts"].items():
                    if variant == "split_and_dividend" or name in policy["blocking_checks"]:
                        aggregate_counts[name] += int(count)
                        for example in parsed["issue_examples"].get(name, []):
                            if len(aggregate_examples[name]) < int(policy["max_issue_examples"]):
                                aggregate_examples[name].append({"variant": variant, "source_file": member, **example})
                line = parsed["line_endings"]
                encoding_summary[f"{variant}_bom_files"] += int(line["bom_utf8"])
                encoding_summary[f"{variant}_crlf_only_files"] += int(line["crlf_lines"] > 0 and line["bare_lf_lines"] == 0 and line["bare_cr_lines"] == 0)
            securities.append(dividend_parsed["metadata"])
            intervals.extend(dividend_parsed["intervals"])
            observed_price_dates[dividend_parsed["metadata"]["source_symbol"]] = {row["date"] for row in dividend_parsed["rows"]}
            for row in dividend_parsed["rows"]:
                if row["in_index"] == 1:
                    direct_daily[row["date"]].add(dividend_parsed["metadata"]["source_symbol"])
            instrument_reports.append(
                {
                    "source_file": member,
                    "source_sha256_split_only": split_parsed["source_sha256"],
                    "source_sha256_split_and_dividend": dividend_parsed["source_sha256"],
                    "price_rows": dividend_parsed["metadata"]["price_rows"],
                    "first_price_date": dividend_parsed["metadata"]["first_price_date"],
                    "last_price_date": dividend_parsed["metadata"]["last_price_date"],
                    "first_in_index_date": dividend_parsed["metadata"]["first_in_index_date"],
                    "last_in_index_date": dividend_parsed["metadata"]["last_in_index_date"],
                    "membership_interval_count": dividend_parsed["metadata"]["membership_interval_count"],
                    "issue_counts_split_and_dividend": dividend_parsed["issue_counts"],
                }
            )
    comparison_payload: dict[str, Any] = {
        **dict(comparison),
        "member_sets_identical": bool(comparison["member_sets_identical"]),
        "extracted_matches_archive": (
            aggregate_counts["extracted_content_mismatch"] == 0 if extracted_path is not None else None
        ),
        "extracted_comparison_status": (
            "compared" if extracted_path is not None else "not_applicable_no_extracted_source"
        ),
        "split_duplicate_content_groups": sum(count > 1 for count in split_hashes.values()),
        "split_and_dividend_duplicate_content_groups": sum(count > 1 for count in dividend_hashes.values()),
        "archive_test_results": archive_test_results,
        "extracted_mismatch_examples": extracted_mismatch_examples,
        "schemas_identical": aggregate_counts["unexpected_schema"] == 0,
        "additional_manifest_or_metadata_members": comparison["split_non_csv_member_count"]
        + comparison["split_and_dividend_non_csv_member_count"],
    }
    for name in (
        "package_row_key_mismatch",
        "package_in_index_mismatch",
        "package_unadjusted_close_mismatch",
        "adjusted_ohlc_different_rows",
        "adjusted_ohlc_equal_rows",
        "volume_different_rows",
        "files_with_adjusted_value_differences",
        "open_different_rows",
        "high_different_rows",
        "low_different_rows",
        "close_different_rows",
        "open_max_absolute_difference",
        "high_max_absolute_difference",
        "low_max_absolute_difference",
        "close_max_absolute_difference",
        "open_max_relative_difference",
        "high_max_relative_difference",
        "low_max_relative_difference",
        "close_max_relative_difference",
        "volume_max_absolute_difference",
        "volume_max_relative_difference",
    ):
        comparison_payload.setdefault(name, 0)
    for name in ("package_row_key_mismatch", "package_in_index_mismatch", "package_unadjusted_close_mismatch"):
        aggregate_counts[name] += comparison[name]

    daily = reconstruct_daily_members(intervals, sessions)
    direct_count_distribution = Counter(len(members) for members in direct_daily.values())
    daily_rows: list[dict[str, Any]] = []
    count_distribution: Counter[int] = Counter()
    for day, members in sorted(daily.items()):
        missing = sorted(symbol for symbol in members if day not in observed_price_dates.get(symbol, set()))
        count_distribution[len(members)] += 1
        daily_rows.append(
            {
                "date": day,
                "member_count": len(members),
                "members_with_price": len(members) - len(missing),
                "members_missing_price": len(missing),
                "missing_price_symbols": ";".join(missing),
            }
        )
    below = sum(1 for row in daily_rows if int(row["member_count"]) < nominal_count)
    above = sum(1 for row in daily_rows if int(row["member_count"]) > nominal_count)
    warning_keys = registry.get("member_count_warning_keys", {})
    below_key = str(warning_keys.get("below", f"daily_member_count_below_{nominal_count}"))
    above_key = str(warning_keys.get("above", f"daily_member_count_above_{nominal_count}"))
    aggregate_counts[below_key] += below
    aggregate_counts[above_key] += above
    if daily_rows:
        aggregate_counts["left_censored_history"] += 1
    changes: list[dict[str, Any]] = []
    previous_day = ""
    previous_members: set[str] = set()
    for day, members in sorted(daily.items()):
        added = sorted(members - previous_members)
        deleted = sorted(previous_members - members)
        if added or deleted:
            changes.append(
                {
                    "effective_date": day,
                    "previous_session": previous_day,
                    "member_count": len(members),
                    "added_count": len(added),
                    "deleted_count": len(deleted),
                    "added_source_symbols": ";".join(added),
                    "deleted_source_symbols": ";".join(deleted),
                    "added_display_tickers": ";".join(clean_display_ticker(item) for item in added),
                    "deleted_display_tickers": ";".join(clean_display_ticker(item) for item in deleted),
                }
            )
        previous_day = day
        previous_members = members
    official_results = _official_cross_checks(registry.get("official_cross_checks", []), daily)
    blocking_counts = {
        name: aggregate_counts[name]
        for name in policy["blocking_checks"]
        if aggregate_counts[name]
    }
    warning_counts = {
        name: aggregate_counts[name]
        for name in policy["warning_checks"]
        if aggregate_counts[name]
    }
    summary = {
        "instrument_count": len(securities),
        "price_row_count": sum(int(item["price_rows"]) for item in securities),
        "membership_interval_count": len(intervals),
        "reentered_security_count": sum(int(item["membership_interval_count"]) > 1 for item in securities),
        "dated_suffix_file_count": sum(bool(item["filename_exit_suffix"]) for item in securities),
        "dated_suffix_matching_last_price_month": sum(
            bool(item["filename_exit_suffix"])
            and item["last_price_date"][:7].replace("-", "") == item["filename_exit_suffix"]
            for item in securities
        ),
        "dated_suffix_matching_last_in_index_month": sum(
            bool(item["filename_exit_suffix"])
            and item["last_in_index_date"][:7].replace("-", "") == item["filename_exit_suffix"]
            for item in securities
        ),
        "active_on_2026_08_04": sum(item["active_on_2026_08_04"] == "true" for item in securities),
        "active_snapshot_date": active_date,
        "files_with_member_price_gaps": sum(bool(item["missing_price_sessions"]) for item in intervals),
        "blocking_issue_total": sum(blocking_counts.values()),
        "official_cross_checks_total": len(official_results),
        "official_cross_checks_passed": sum(item["status"].startswith("pass") for item in official_results),
    }
    point_in_time = {
        "first_price_date": min(item["first_price_date"] for item in securities),
        "last_price_date": max(item["last_price_date"] for item in securities),
        "first_membership_date": daily_rows[0]["date"],
        "last_membership_date": daily_rows[-1]["date"],
        "minimum_member_count": min(int(item["member_count"]) for item in daily_rows),
        "maximum_member_count": max(int(item["member_count"]) for item in daily_rows),
        "member_count_distribution": {str(key): value for key, value in sorted(count_distribution.items())},
        "direct_observation_minimum_member_count": min(map(len, direct_daily.values())),
        "direct_observation_maximum_member_count": max(map(len, direct_daily.values())),
        "direct_observation_count_distribution": {
            str(key): value for key, value in sorted(direct_count_distribution.items())
        },
        "nominal_company_count": nominal_count,
        "days_below_nominal_count": below,
        "days_above_nominal_count": above,
        "days_with_missing_member_prices": sum(int(item["members_missing_price"]) > 0 for item in daily_rows),
        "missing_member_price_observations": sum(int(item["members_missing_price"]) for item in daily_rows),
        "membership_change_dates_excluding_initial_snapshot": max(0, len(changes) - 1),
        "membership_change_weekday_distribution": dict(
            sorted(Counter(date.fromisoformat(item["effective_date"]).strftime("%A") for item in changes[1:]).items())
        ),
        "membership_change_month_distribution": dict(
            sorted(Counter(item["effective_date"][5:7] for item in changes[1:]).items())
        ),
        "membership_change_shape": {
            "add_and_delete": sum(int(item["added_count"]) > 0 and int(item["deleted_count"]) > 0 for item in changes[1:]),
            "add_only": sum(int(item["added_count"]) > 0 and int(item["deleted_count"]) == 0 for item in changes[1:]),
            "delete_only": sum(int(item["added_count"]) == 0 and int(item["deleted_count"]) > 0 for item in changes[1:]),
        },
        "sample_counts": {
            day: len(daily.get(day, set()))
            for day in sample_dates
        },
        "sample_direct_observation_counts": {
            day: len(direct_daily.get(day, set()))
            for day in sample_dates
        },
        "survivorship_bias_assessment": "supports historical point-in-time membership and retains deleted/delisted records; avoids current-constituent lookback bias when used with the stated decision-time lag",
        "safe_machine_readable_range": [daily_rows[0]["date"], daily_rows[-1]["date"]],
        "formal_research_gate": "not_ready_pending_provenance_identity_adjustment_and_early_history_review",
        "active_source_symbols_on_2026_08_04": sorted(daily.get(active_date, set())),
    }
    report: dict[str, Any] = {
        "schema_version": 1,
        "dataset_id": registry["dataset_id"],
        "index_display_name": index_name,
        "nominal_company_count": nominal_count,
        "member_count_explanation": registry.get("member_count_explanation"),
        "status": "candidate_pending_review" if not blocking_counts else "blocked_candidate_build",
        "approved": False,
        "production_writes": 0,
        "summary": summary,
        "package_comparison": comparison_payload,
        "encoding_and_format": dict(sorted(encoding_summary.items())),
        "point_in_time": point_in_time,
        "blocking_issue_counts": blocking_counts,
        "warning_issue_counts": warning_counts,
        "issue_examples": {name: values for name, values in sorted(aggregate_examples.items())},
        "official_cross_checks": official_results,
        "official_primary_sources": registry.get("official_primary_sources", []),
        "identity_review": {
            "same_company_name_multiple_source_files": [],
            "special_cases": registry.get("identity_review_special_cases", {
                "AABA": "kept as its own suffixed source-file identity; no YHOO continuity inferred",
                "GOOG_GOOGL": "kept as separate share-class securities",
                "META_FB": "META source identity retained; no FB record fabricated from backfilled source fields",
                "BKNG_PCLN": "BKNG source identity retained; no PCLN record fabricated from backfilled source fields",
                "dated_suffixes": "preserved in source_symbol; display ticker is presentation-only",
            }),
        },
        "instruments": instrument_reports,
    }
    company_groups: dict[str, list[str]] = defaultdict(list)
    for item in securities:
        company_groups[str(item["company_name"])].append(str(item["source_symbol"]))
    security_by_symbol = {str(item["source_symbol"]): item for item in securities}
    duplicate_company_groups: list[dict[str, Any]] = []
    for company, symbols in sorted(company_groups.items()):
        if len(symbols) <= 1:
            continue
        ordered = sorted(symbols)
        pairwise: list[dict[str, Any]] = []
        for left_index, left in enumerate(ordered):
            peers = [symbol for symbol in ordered if symbol != left]
            security_by_symbol[left]["anomaly_notes"] = "; ".join(
                filter(
                    None,
                    (
                        security_by_symbol[left]["anomaly_notes"],
                        f"same source company_name also appears in {','.join(peers)}; not auto-merged",
                    ),
                )
            )
            for right in ordered[left_index + 1 :]:
                left_item = security_by_symbol[left]
                right_item = security_by_symbol[right]
                price_overlap_start = max(left_item["first_price_date"], right_item["first_price_date"])
                price_overlap_end = min(left_item["last_price_date"], right_item["last_price_date"])
                pairwise.append(
                    {
                        "left": left,
                        "right": right,
                        "price_date_ranges_overlap": price_overlap_start <= price_overlap_end,
                        "price_overlap_start": price_overlap_start if price_overlap_start <= price_overlap_end else None,
                        "price_overlap_end": price_overlap_end if price_overlap_start <= price_overlap_end else None,
                        "membership_overlap_sessions": sum(
                            left in members and right in members for members in daily.values()
                        ),
                    }
                )
        duplicate_company_groups.append(
            {
                "company_name": company,
                "source_symbols": ordered,
                "pairwise_overlap": pairwise,
                "auto_merged": False,
            }
        )
    report["identity_review"]["same_company_name_multiple_source_files"] = duplicate_company_groups
    if blocking_counts:
        if write_candidate:
            raise PointInTimeDataError(f"Blocking source issues prevent candidate output: {blocking_counts}")
        return report

    security_bytes = csv_bytes(SECURITY_MASTER_COLUMNS, sorted(securities, key=lambda item: item["source_file"]))
    interval_bytes = csv_bytes(
        MEMBERSHIP_INTERVAL_COLUMNS,
        sorted(intervals, key=lambda item: (item["source_file"], int(item["interval_number"]))),
    )
    daily_bytes = csv_bytes(MEMBERSHIP_DAILY_COUNT_COLUMNS, daily_rows)
    changes_bytes = csv_bytes(MEMBERSHIP_CHANGE_COLUMNS, changes)
    builder_hash = sha256_file(Path(__file__).resolve())
    build_payload = {
        "builder_sha256": builder_hash,
        "registry_sha256": sha256_bytes(registry_bytes),
        "source_inventory": before_inventory,
        "security_master_sha256": sha256_bytes(security_bytes),
        "membership_intervals_sha256": sha256_bytes(interval_bytes),
        "membership_daily_counts_sha256": sha256_bytes(daily_bytes),
        "membership_changes_sha256": sha256_bytes(changes_bytes),
    }
    build_id = sha256_bytes(json.dumps(build_payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    old_manifest_path = workspace / registry["outputs"]["source_manifest"]
    if old_manifest_path.is_file():
        try:
            old_manifest = json.loads(old_manifest_path.read_text(encoding="utf-8"))
            if old_manifest.get("build_id") == build_id:
                generated_at = old_manifest["generated_at_utc"]
        except (json.JSONDecodeError, KeyError):
            pass
    output_hashes = {
        "security_master": sha256_bytes(security_bytes),
        "membership_intervals": sha256_bytes(interval_bytes),
        "membership_daily_counts": sha256_bytes(daily_bytes),
        "membership_changes": sha256_bytes(changes_bytes),
    }
    manifest = {
        "schema_version": 1,
        "dataset_id": registry["dataset_id"],
        "build_id": build_id,
        "generated_at_utc": generated_at,
        "status": "candidate_pending_review",
        "approved": False,
        "production_writes_enabled": False,
        "builder": str(registry.get("builder", "backtest/quantkit/nasdaq100_data.py")),
        "builder_sha256": builder_hash,
        "registry": str(registry_path.relative_to(workspace)),
        "registry_sha256": sha256_bytes(registry_bytes),
        "provider": registry["provider"],
        "license_or_authorization": registry["license_or_authorization"],
        "acquired_date": registry["acquired_date"],
        "review_status": registry["review_status"],
        "filename_snapshot_token": registry["filename_snapshot_token"],
        "snapshot_token_interpretation": registry.get("snapshot_token_interpretation", "pending_review"),
        "source_root": registry["source_root"],
        "packages": registry["packages"],
        "extracted_evidence": extracted_evidence,
        "source_schema": registry["source_schema"],
        "source_inventory": before_inventory,
        "package_comparison": comparison_payload,
        "coverage": point_in_time,
        "membership_contract": registry["membership_contract"],
        "identity_contract": registry["identity_contract"],
        "adjustment_guidance": {
            "indicator_price": "Choose split-only Close for ex-distribution price trend or split-and-dividend Close for total-return trend; record the choice.",
            "execution_price": "Unadjusted Close is the only explicitly unadjusted price. Complete unadjusted OHLC is unavailable.",
            "total_return": "Split-and-dividend Close is only a vendor-derived approximation without an independent corporate-action ledger.",
            "adjusted_ohlc_internal_consistency": "passed OHLC envelope checks in both packages",
            "future_dividend_restatement_risk": "Historical adjusted values may change after future distributions; freeze and hash the source snapshot for reproducibility.",
            "formal_selection": "pending_review; both variants remain source candidates",
        },
        "official_primary_sources": registry.get("official_primary_sources", []),
        "official_cross_checks": official_results,
        "outputs": {
            name: {"path": registry["outputs"][name], "sha256": digest}
            for name, digest in output_hashes.items()
        },
        "quality_report_json": registry["outputs"]["quality_report_json"],
        "quality_report_markdown": registry["outputs"]["quality_report_markdown"],
        "promotion_policy": registry["promotion_policy"],
    }
    after_inventory = _root_inventory(source_root)
    if before_inventory != after_inventory:
        raise PointInTimeDataError("Source evidence mutated during candidate build")
    report["build_id"] = build_id
    report["generated_at_utc"] = generated_at
    report["source_immutability_verified"] = True
    report["candidate_outputs_written"] = bool(write_candidate)
    report["output_directory"] = registry["outputs"]["directory"]
    report_bytes = json_bytes(report)
    markdown_bytes = (_quality_markdown(report).rstrip() + "\n").encode("utf-8")
    manifest["quality_outputs"] = {
        "quality_report_json": {
            "path": registry["outputs"]["quality_report_json"],
            "sha256": sha256_bytes(report_bytes),
        },
        "quality_report_markdown": {
            "path": registry["outputs"]["quality_report_markdown"],
            "sha256": sha256_bytes(markdown_bytes),
        },
    }
    if write_candidate:
        atomic_write(workspace / registry["outputs"]["security_master"], security_bytes)
        atomic_write(workspace / registry["outputs"]["membership_intervals"], interval_bytes)
        atomic_write(workspace / registry["outputs"]["membership_daily_counts"], daily_bytes)
        atomic_write(workspace / registry["outputs"]["membership_changes"], changes_bytes)
        atomic_write(workspace / registry["outputs"]["source_manifest"], json_bytes(manifest))
        atomic_write(workspace / registry["outputs"]["quality_report_json"], report_bytes)
        atomic_write(workspace / registry["outputs"]["quality_report_markdown"], markdown_bytes)
    return report
