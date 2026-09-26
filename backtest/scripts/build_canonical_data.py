#!/usr/bin/env python3
"""Build canonical daily OHLCV files without modifying purchased data.

The registry is deliberately JSON-compatible YAML, allowing this bootstrap
builder to run on the macOS system Python before project dependencies exist.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import tempfile
import zipfile
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable


CANONICAL_COLUMNS = ("date", "symbol", "open", "high", "low", "close", "volume")
PRICE_COLUMNS = ("open", "high", "low", "close")
NUMERIC_COLUMNS = PRICE_COLUMNS + ("volume",)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_registry(path: Path) -> dict[str, Any]:
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{path} must remain JSON-compatible YAML for the bootstrap builder: {exc}"
        ) from exc
    if registry.get("schema_version") != 1:
        raise ValueError("Unsupported registry schema_version")
    return registry


def read_source_bytes(project_root: Path, source: dict[str, Any]) -> bytes:
    source_path = project_root / source["path"]
    if source["kind"] == "csv":
        return source_path.read_bytes()
    if source["kind"] == "zip_member":
        with zipfile.ZipFile(source_path) as archive:
            return archive.read(source["member"])
    raise ValueError(f"Unsupported source kind: {source['kind']}")


def parse_finite_number(raw: str | None, field: str, row_number: int) -> float:
    text = "" if raw is None else raw.strip()
    if not text:
        raise ValueError(f"row {row_number}: missing {field}")
    try:
        value = float(text)
    except ValueError as exc:
        raise ValueError(f"row {row_number}: invalid {field}={text!r}") from exc
    if not math.isfinite(value):
        raise ValueError(f"row {row_number}: non-finite {field}={text!r}")
    return value


def format_number(value: float) -> str:
    # 15 significant digits preserves the supplied daily data while avoiding
    # platform-dependent repr noise in generated CSV files.
    return format(value, ".15g")


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(data)
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def load_trading_calendar(project_root: Path, config: dict[str, Any]) -> set[str]:
    path = project_root / config["path"]
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        return {
            row[config["date_column"]].strip()
            for row in reader
            if row[config["trading_flag_column"]].strip() == config["trading_flag_value"]
        }


def canonical_csv_bytes(rows: Iterable[dict[str, Any]]) -> bytes:
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=CANONICAL_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                "date": row["date"],
                "symbol": row["symbol"],
                "open": format_number(row["open"]),
                "high": format_number(row["high"]),
                "low": format_number(row["low"]),
                "close": format_number(row["close"]),
                "volume": format_number(row["volume"]),
            }
        )
    return text.getvalue().encode("utf-8")


def build_dataset(
    project_root: Path,
    dataset: dict[str, Any],
    trading_dates: set[str],
    quality_policy: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_bytes = read_source_bytes(project_root, dataset["source"])
    source_hash = sha256_bytes(source_bytes)
    source_text = source_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(source_text, newline=""))
    source_headers = reader.fieldnames or []
    column_map = dataset["column_map"]
    missing_headers = sorted(set(column_map) - set(source_headers))

    issues: dict[str, list[dict[str, Any]]] = {
        "missing_required_value": [],
        "bad_date": [],
        "bad_number": [],
        "wrong_symbol": [],
        "duplicate_date": [],
        "nonpositive_price": [],
        "invalid_ohlc": [],
        "unexpected_coverage": [],
        "nonpositive_volume": [],
        "outlier_close_return": [],
        "source_order_violation": [],
        "missing_trading_date": [],
        "nontrading_calendar_date": [],
    }
    if missing_headers:
        issues["missing_required_value"].append({"missing_headers": missing_headers})

    parsed_rows: list[dict[str, Any]] = []
    rejected_rows = 0
    seen_dates: set[str] = set()
    previous_source_date: date | None = None

    for row_number, source_row in enumerate(reader, start=2):
        canonical = {
            canonical_name: (source_row.get(source_name) or "").strip()
            for source_name, canonical_name in column_map.items()
        }
        date_text = canonical.get("date", "")
        symbol = canonical.get("symbol", "").upper()
        row_has_fatal_parse_error = False

        if not date_text or not symbol:
            issues["missing_required_value"].append(
                {"row": row_number, "date": date_text or None, "symbol": symbol or None}
            )
            row_has_fatal_parse_error = True

        try:
            parsed_date = date.fromisoformat(date_text)
        except ValueError:
            issues["bad_date"].append({"row": row_number, "value": date_text})
            parsed_date = None
            row_has_fatal_parse_error = True

        numeric: dict[str, float] = {}
        for field in NUMERIC_COLUMNS:
            try:
                numeric[field] = parse_finite_number(canonical.get(field), field, row_number)
            except ValueError as exc:
                issues["bad_number"].append({"row": row_number, "detail": str(exc)})
                row_has_fatal_parse_error = True

        if row_has_fatal_parse_error:
            rejected_rows += 1
            continue

        assert parsed_date is not None
        if symbol != dataset["symbol"]:
            issues["wrong_symbol"].append(
                {"row": row_number, "date": date_text, "actual": symbol, "expected": dataset["symbol"]}
            )
        if date_text in seen_dates:
            issues["duplicate_date"].append({"row": row_number, "date": date_text})
        seen_dates.add(date_text)
        if previous_source_date is not None and parsed_date < previous_source_date:
            issues["source_order_violation"].append({"row": row_number, "date": date_text})
        previous_source_date = parsed_date

        prices = [numeric[field] for field in PRICE_COLUMNS]
        if min(prices) <= 0:
            issues["nonpositive_price"].append({"row": row_number, "date": date_text, **numeric})

        open_, high, low, close = (numeric[field] for field in PRICE_COLUMNS)
        scale = max(1.0, *(abs(value) for value in prices))
        tolerance = float(quality_policy["ohlc_relative_tolerance"]) * scale
        if high + tolerance < max(open_, low, close) or low - tolerance > min(open_, high, close):
            issues["invalid_ohlc"].append(
                {
                    "row": row_number,
                    "date": date_text,
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                }
            )
        if numeric["volume"] <= 0:
            issues["nonpositive_volume"].append(
                {"row": row_number, "date": date_text, "volume": numeric["volume"]}
            )

        parsed_rows.append({"date": date_text, "symbol": symbol, **numeric})

    parsed_rows.sort(key=lambda row: (row["symbol"], row["date"]))
    exclusions: dict[str, dict[str, Any]] = {}
    canonical_start = dataset.get("canonical_start")
    if canonical_start:
        excluded = [row for row in parsed_rows if row["date"] < canonical_start]
        parsed_rows = [row for row in parsed_rows if row["date"] >= canonical_start]
        exclusions["before_canonical_start"] = {
            "rows": len(excluded),
            "source_start": excluded[0]["date"] if excluded else None,
            "source_end": excluded[-1]["date"] if excluded else None,
            "canonical_start": canonical_start,
            "reason": dataset["canonical_start_reason"],
        }
    start = parsed_rows[0]["date"] if parsed_rows else None
    end = parsed_rows[-1]["date"] if parsed_rows else None
    expected = dataset["expected"]
    if start != expected["start"] or end != expected["end"]:
        issues["unexpected_coverage"].append(
            {"actual_start": start, "actual_end": end, **expected}
        )

    if start and end:
        expected_dates = {day for day in trading_dates if start <= day <= end}
        actual_dates = {row["date"] for row in parsed_rows}
        issues["missing_trading_date"] = [{"date": day} for day in sorted(expected_dates - actual_dates)]
        issues["nontrading_calendar_date"] = [
            {"date": day} for day in sorted(actual_dates - expected_dates)
        ]

    outlier_threshold = float(quality_policy["outlier_abs_close_return_pct"]) / 100.0
    previous_close: float | None = None
    for row in parsed_rows:
        if previous_close is not None and previous_close > 0:
            daily_return = row["close"] / previous_close - 1.0
            if abs(daily_return) > outlier_threshold:
                issues["outlier_close_return"].append(
                    {"date": row["date"], "return_pct": round(daily_return * 100.0, 9)}
                )
        previous_close = row["close"]

    blocking_names = set(quality_policy["blocking_checks"])
    blocking_counts = {name: len(values) for name, values in issues.items() if name in blocking_names and values}
    quality_status = "failed" if blocking_counts else "passed"
    registered_status = dataset["registered_status"]
    effective_status = "quality_failed" if quality_status == "failed" else registered_status

    output_bytes = canonical_csv_bytes(parsed_rows)
    output_path = project_root / dataset["output"]
    atomic_write(output_path, output_bytes)

    issue_counts = {name: len(values) for name, values in issues.items()}
    report = {
        "symbol": dataset["symbol"],
        "registered_status": registered_status,
        "quality_status": quality_status,
        "effective_status": effective_status,
        "role": dataset["role"],
        "rows_written": len(parsed_rows),
        "rows_rejected": rejected_rows,
        "start": start,
        "end": end,
        "source_headers": source_headers,
        "issue_counts": issue_counts,
        "blocking_counts": blocking_counts,
        "issues": issues,
        "exclusions": exclusions,
        "known_issues": dataset["known_issues"],
    }
    manifest_entry = {
        "symbol": dataset["symbol"],
        "role": dataset["role"],
        "registered_status": registered_status,
        "quality_status": quality_status,
        "effective_status": effective_status,
        "source": dataset["source"],
        "source_content_sha256": source_hash,
        "adjustment": dataset["adjustment"],
        "output": dataset["output"],
        "canonical_sha256": sha256_bytes(output_bytes),
        "rows": len(parsed_rows),
        "start": start,
        "end": end,
        "blocking_counts": blocking_counts,
        "exclusions": exclusions,
    }
    return manifest_entry, report


def render_quality_markdown(reports: list[dict[str, Any]], generated_at: str) -> str:
    lines = [
        "# Canonical 数据质量报告",
        "",
        f"> 生成时间：{generated_at}",
        "> 标准化只改变字段和格式，不会自动修正供应商价格。",
        "",
        "## 汇总",
        "",
        "| 标的 | 登记状态 | 自动检查 | 有效状态 | 行数 | 区间 | 阻断问题 |",
        "|---|---|---|---|---:|---|---|",
    ]
    for report in reports:
        blocking = ", ".join(
            f"{name}={count}" for name, count in report["blocking_counts"].items()
        ) or "无"
        lines.append(
            f"| {report['symbol']} | {report['registered_status']} | "
            f"{report['quality_status']} | {report['effective_status']} | "
            f"{report['rows_written']:,} | {report['start']}～{report['end']} | {blocking} |"
        )

    lines.extend(["", "## 逐标的详情", ""])
    for report in reports:
        lines.extend(
            [
                f"### {report['symbol']}",
                "",
                f"- 用途：`{report['role']}`",
                f"- 有效状态：`{report['effective_status']}`",
                f"- 写入/拒绝：{report['rows_written']:,} / {report['rows_rejected']:,}",
                "- 检查计数："
                + ", ".join(
                    f"`{name}={count}`"
                    for name, count in report["issue_counts"].items()
                    if count
                )
                if any(report["issue_counts"].values())
                else "- 检查计数：全部为 0",
                "",
                "已知限制：",
                "",
            ]
        )
        lines.extend(f"- {issue}" for issue in report["known_issues"])
        if report["exclusions"]:
            lines.extend(["", "明确排除：", ""])
            for exclusion in report["exclusions"].values():
                lines.append(
                    f"- {exclusion['source_start']}～{exclusion['source_end']}："
                    f"{exclusion['rows']:,} 行；{exclusion['reason']}"
                )
        if report["blocking_counts"]:
            lines.extend(["", "阻断证据：", ""])
            for name in report["blocking_counts"]:
                examples = report["issues"][name][:5]
                lines.append(f"- `{name}`：`{json.dumps(examples, ensure_ascii=False)}`")
        lines.append("")

    lines.extend(
        [
            "## 使用规则",
            "",
            "- 只有 `effective_status=approved` 的文件可以默认进入正式回测。",
            "- `provisional` 和 `quality_failed` 需要实验配置显式覆盖，并在报告中显示警告。",
            "- 完整逐行证据见同目录 `quality_report.json`；本文件只展示计数和最多五条例子。",
            "",
        ]
    )
    return "\n".join(lines)


def analyze_calendar_disagreements(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Separate likely calendar defects from symbol-specific missing sessions.

    The purchased calendar is useful evidence but is not treated as truth. A
    date absent from every covered ETF, or a weekend marked as trading, points
    to the calendar. A date present in peer ETFs but absent from one symbol
    points to that symbol's source data.
    """

    def covered_symbols(day: str) -> set[str]:
        return {
            report["symbol"]
            for report in reports
            if report["start"] is not None
            and report["end"] is not None
            and report["start"] <= day <= report["end"]
        }

    missing_by_date: dict[str, set[str]] = {}
    nontrading_by_date: dict[str, set[str]] = {}
    for report in reports:
        symbol = report["symbol"]
        for issue in report["issues"]["missing_trading_date"]:
            missing_by_date.setdefault(issue["date"], set()).add(symbol)
        for issue in report["issues"]["nontrading_calendar_date"]:
            nontrading_by_date.setdefault(issue["date"], set()).add(symbol)

    calendar_suspect: list[dict[str, Any]] = []
    dataset_specific_missing: list[dict[str, Any]] = []
    for day, missing_symbols in sorted(missing_by_date.items()):
        covered = covered_symbols(day)
        weekday = date.fromisoformat(day).strftime("%A")
        if missing_symbols == covered or date.fromisoformat(day).weekday() >= 5:
            calendar_suspect.append(
                {
                    "date": day,
                    "calendar_flag": "trading",
                    "evidence": "absent_from_all_covered_datasets"
                    if missing_symbols == covered
                    else "weekend_marked_trading",
                    "covered_symbols": sorted(covered),
                    "weekday": weekday,
                }
            )
        else:
            dataset_specific_missing.append(
                {
                    "date": day,
                    "missing_symbols": sorted(missing_symbols),
                    "peer_symbols_with_data": sorted(covered - missing_symbols),
                    "weekday": weekday,
                }
            )

    for day, symbols_with_data in sorted(nontrading_by_date.items()):
        covered = covered_symbols(day)
        calendar_suspect.append(
            {
                "date": day,
                "calendar_flag": "closed",
                "evidence": "data_present_for_all_covered_datasets"
                if symbols_with_data == covered
                else "data_present_despite_closed_flag",
                "symbols_with_data": sorted(symbols_with_data),
                "covered_symbols": sorted(covered),
                "weekday": date.fromisoformat(day).strftime("%A"),
            }
        )

    return {
        "calendar_status": "suspect" if calendar_suspect else "passed",
        "calendar_suspect_dates": calendar_suspect,
        "dataset_specific_missing_dates": dataset_specific_missing,
    }


def append_calendar_markdown(markdown: str, calendar_analysis: dict[str, Any]) -> str:
    lines = [markdown.rstrip(), "", "## 交易日历交叉检查", ""]
    lines.append(f"- 供应商日历状态：`{calendar_analysis['calendar_status']}`")
    lines.append(
        "- 判断方法：同时参考登记标的；全部标的共同缺失或周末被标为交易日时，优先判定日历可疑，不把它误算成所有行情源同时缺失。"
    )
    lines.extend(["", "疑似供应商日历错误：", ""])
    for finding in calendar_analysis["calendar_suspect_dates"]:
        lines.append(f"- `{finding['date']}`：{finding['evidence']}（{finding['weekday']}）")
    lines.extend(["", "疑似单标的数据缺口：", ""])
    if calendar_analysis["dataset_specific_missing_dates"]:
        for finding in calendar_analysis["dataset_specific_missing_dates"]:
            lines.append(
                f"- `{finding['date']}`：缺少 {', '.join(finding['missing_symbols'])}；"
                f"同日有数据 {', '.join(finding['peer_symbols_with_data'])}。"
            )
    else:
        lines.append("- 无")
    lines.extend(
        [
            "",
            "供应商交易日历当前不能作为唯一权威来源。第二阶段应使用经过测试的交易所日历实现，购买日历只作为交叉检查。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--registry", type=Path, default=Path("data/source_registry.yaml"))
    args = parser.parse_args()

    project_root = args.project_root.resolve()
    registry_path = args.registry if args.registry.is_absolute() else project_root / args.registry
    registry = load_registry(registry_path)
    trading_dates = load_trading_calendar(project_root, registry["calendar"])

    manifest_entries: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    for dataset in registry["datasets"]:
        manifest_entry, report = build_dataset(
            project_root, dataset, trading_dates, registry["quality_policy"]
        )
        manifest_entries.append(manifest_entry)
        reports.append(report)

    calendar_analysis = analyze_calendar_disagreements(reports)
    registry_hash = sha256_bytes(registry_path.read_bytes())
    build_payload = json.dumps(
        {
            "registry_sha256": registry_hash,
            "sources": [entry["source_content_sha256"] for entry in manifest_entries],
        },
        sort_keys=True,
    ).encode("utf-8")
    build_id = sha256_bytes(build_payload)
    processed_root = project_root / "data/processed"
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    existing_manifest_path = processed_root / "manifest.json"
    if existing_manifest_path.is_file():
        try:
            existing_manifest = json.loads(existing_manifest_path.read_text(encoding="utf-8"))
            if existing_manifest.get("build_id") == build_id:
                generated_at = existing_manifest["generated_at_utc"]
        except (json.JSONDecodeError, KeyError):
            pass
    manifest = {
        "schema_version": 1,
        "generated_at_utc": generated_at,
        "builder": "backtest/scripts/build_canonical_data.py",
        "registry": str(registry_path.relative_to(project_root)),
        "registry_sha256": registry_hash,
        "build_id": build_id,
        "canonical_schema": registry["canonical_schema"],
        "calendar_analysis": calendar_analysis,
        "datasets": manifest_entries,
    }
    quality = {
        "schema_version": 1,
        "generated_at_utc": generated_at,
        "build_id": manifest["build_id"],
        "calendar_analysis": calendar_analysis,
        "reports": reports,
    }

    atomic_write(
        processed_root / "manifest.json",
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    atomic_write(
        processed_root / "quality_report.json",
        (json.dumps(quality, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    atomic_write(
        processed_root / "quality_report.md",
        append_calendar_markdown(
            render_quality_markdown(reports, generated_at), calendar_analysis
        ).encode("utf-8"),
    )

    for report in reports:
        blocking = sum(report["blocking_counts"].values())
        print(
            f"{report['symbol']}: {report['effective_status']}; "
            f"rows={report['rows_written']}; range={report['start']}..{report['end']}; "
            f"blocking={blocking}"
        )

    approved_failures = [
        report["symbol"]
        for report in reports
        if report["registered_status"] == "approved" and report["quality_status"] != "passed"
    ]
    if approved_failures:
        print("Approved datasets failed quality checks: " + ", ".join(approved_failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
