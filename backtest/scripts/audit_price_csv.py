#!/usr/bin/env python3
"""Profile OHLC CSV data without modifying the purchased source files.

Sources can be ordinary CSV files or ZIP members written as:

    archive.zip::member.csv

The script uses only the Python standard library so it can run before the
project environment and backtesting dependencies are installed.
"""

from __future__ import annotations

import argparse
import csv
import io
import math
import zipfile
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, TextIO


COLUMN_ALIASES = {
    "日期": "date",
    "Date": "date",
    "date": "date",
    "代码": "symbol",
    "Symbol": "symbol",
    "symbol": "symbol",
    "开盘价": "open",
    "Open": "open",
    "open": "open",
    "最高价": "high",
    "High": "high",
    "high": "high",
    "最低价": "low",
    "Low": "low",
    "low": "low",
    "收盘价": "close",
    "Close": "close",
    "close": "close",
    "成交量": "volume",
    "Volume": "volume",
    "volume": "volume",
}

REQUIRED = ("date", "symbol", "open", "high", "low", "close")
NUMERIC = ("open", "high", "low", "close", "volume")


@contextmanager
def open_source(source: str) -> Iterator[TextIO]:
    if "::" not in source:
        with Path(source).open("r", encoding="utf-8-sig", newline="") as file:
            yield file
        return

    archive_path, member = source.split("::", 1)
    with zipfile.ZipFile(archive_path) as archive:
        with archive.open(member) as raw:
            with io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as file:
                yield file


def parse_number(value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def profile(source: str) -> dict[str, object]:
    with open_source(source) as file:
        reader = csv.DictReader(file)
        original_headers = reader.fieldnames or []
        header_map = {
            header: COLUMN_ALIASES.get(header, header)
            for header in original_headers
        }
        canonical_headers = set(header_map.values())
        missing_required_columns = sorted(set(REQUIRED) - canonical_headers)

        rows = 0
        bad_dates = 0
        missing_values = Counter()
        nonpositive_prices = 0
        invalid_ohlc = 0
        duplicate_keys = 0
        reverse_dates = 0
        symbols: set[str] = set()
        seen: set[tuple[str, str]] = set()
        first_date: datetime | None = None
        last_date: datetime | None = None
        previous_date: datetime | None = None
        previous_close: dict[str, float] = {}
        largest_abs_return: tuple[float, str, str] | None = None

        for original_row in reader:
            rows += 1
            row = {
                header_map[key]: value
                for key, value in original_row.items()
                if key is not None
            }
            symbol = (row.get("symbol") or "").strip()
            date_text = (row.get("date") or "").strip()
            if symbol:
                symbols.add(symbol)
            key = (symbol, date_text)
            if key in seen:
                duplicate_keys += 1
            seen.add(key)

            try:
                date = datetime.strptime(date_text, "%Y-%m-%d")
            except ValueError:
                bad_dates += 1
                date = None
            if date is not None:
                first_date = date if first_date is None else min(first_date, date)
                last_date = date if last_date is None else max(last_date, date)
                if previous_date is not None and date < previous_date:
                    reverse_dates += 1
                previous_date = date

            values = {name: parse_number(row.get(name)) for name in NUMERIC}
            for name in REQUIRED:
                if name in ("date", "symbol"):
                    if not (row.get(name) or "").strip():
                        missing_values[name] += 1
                elif values.get(name) is None:
                    missing_values[name] += 1

            prices = [values.get(name) for name in ("open", "high", "low", "close")]
            if all(value is not None for value in prices):
                open_, high, low, close = prices
                assert open_ is not None and high is not None
                assert low is not None and close is not None
                if min(open_, high, low, close) <= 0:
                    nonpositive_prices += 1
                if high < max(open_, low, close) or low > min(open_, high, close):
                    invalid_ohlc += 1
                prior_close = previous_close.get(symbol)
                if prior_close and prior_close > 0:
                    daily_return = close / prior_close - 1
                    if (
                        largest_abs_return is None
                        or abs(daily_return) > abs(largest_abs_return[0])
                    ):
                        largest_abs_return = (daily_return, date_text, symbol)
                previous_close[symbol] = close

    return {
        "source": source,
        "headers": original_headers,
        "missing_required_columns": missing_required_columns,
        "rows": rows,
        "symbol_count": len(symbols),
        "symbols": sorted(symbols)[:10],
        "start": first_date.date().isoformat() if first_date else None,
        "end": last_date.date().isoformat() if last_date else None,
        "bad_dates": bad_dates,
        "missing_values": dict(sorted(missing_values.items())),
        "duplicate_symbol_dates": duplicate_keys,
        "reverse_dates": reverse_dates,
        "nonpositive_prices": nonpositive_prices,
        "invalid_ohlc": invalid_ohlc,
        "largest_abs_close_return": (
            {
                "return_pct": round(largest_abs_return[0] * 100, 6),
                "date": largest_abs_return[1],
                "symbol": largest_abs_return[2],
            }
            if largest_abs_return
            else None
        ),
    }


def print_profile(result: dict[str, object]) -> None:
    print(f"SOURCE: {result['source']}")
    for key in (
        "headers",
        "missing_required_columns",
        "rows",
        "symbol_count",
        "symbols",
        "start",
        "end",
        "bad_dates",
        "missing_values",
        "duplicate_symbol_dates",
        "reverse_dates",
        "nonpositive_prices",
        "invalid_ohlc",
        "largest_abs_close_return",
    ):
        print(f"  {key}: {result[key]}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sources", nargs="+")
    args = parser.parse_args()
    for index, source in enumerate(args.sources):
        if index:
            print()
        print_profile(profile(source))


if __name__ == "__main__":
    main()
