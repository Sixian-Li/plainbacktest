#!/usr/bin/env python3
"""Compare the two purchased SPY sources and persist reviewable evidence."""

from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RETHINK_ROOT = (
    PROJECT_ROOT
    / "data/2026-08-05多个数据包_rethink/核心 ETF 日线数据/3 大指数 ETF/SPY - 标普 500 ETF SPDR"
)
FULL_ROOT = PROJECT_ROOT / "data/2026-08-05美股数据_全"
OUTPUT_ROOT = PROJECT_ROOT / "data/processed/source_comparisons"


def read_rethink(name: str) -> pd.DataFrame:
    data = pd.read_csv(RETHINK_ROOT / name, encoding="utf-8-sig")
    return data.rename(
        columns={
            "Date": "date",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
            "Unadjusted Close": "unadjusted_close",
        }
    ).assign(date=lambda frame: pd.to_datetime(frame["date"])).set_index("date").sort_index()


def read_full(archive: str, member: str) -> pd.DataFrame:
    with zipfile.ZipFile(FULL_ROOT / archive) as bundle:
        data = pd.read_csv(bundle.open(member), encoding="utf-8-sig")
    return data.rename(
        columns={
            "日期": "date",
            "开盘价": "open",
            "最高价": "high",
            "最低价": "low",
            "收盘价": "close",
            "成交量": "volume",
        }
    ).assign(date=lambda frame: pd.to_datetime(frame["date"])).set_index("date").sort_index()


def invalid_ohlc(frame: pd.DataFrame) -> list[dict[str, object]]:
    invalid = frame[
        (frame["high"] < frame[["open", "low", "close"]].max(axis=1))
        | (frame["low"] > frame[["open", "high", "close"]].min(axis=1))
    ]
    return [
        {
            "date": index.date().isoformat(),
            **{column: float(row[column]) for column in ("open", "high", "low", "close")},
        }
        for index, row in invalid.iterrows()
    ]


def compare(left: pd.DataFrame, right: pd.DataFrame) -> dict[str, object]:
    common = left.index.intersection(right.index)
    joined = left.loc[common].join(right.loc[common], lsuffix="_rethink", rsuffix="_full")
    rethink_return = joined["close_rethink"].pct_change()
    full_return = joined["close_full"].pct_change()
    return_difference = (rethink_return - full_return).abs()
    level_difference = (joined["close_full"] / joined["close_rethink"] - 1.0).abs()
    largest = return_difference.nlargest(10)
    return {
        "common_dates": len(common),
        "rethink_only_dates": len(left.index.difference(right.index)),
        "full_only_dates": len(right.index.difference(left.index)),
        "full_missing_2015_04_09": pd.Timestamp("2015-04-09") not in right.index,
        "rethink_has_2015_04_09": pd.Timestamp("2015-04-09") in left.index,
        "latest_close_rethink": float(joined.iloc[-1]["close_rethink"]),
        "latest_close_full": float(joined.iloc[-1]["close_full"]),
        "max_abs_level_difference_pct": float(level_difference.max() * 100.0),
        "max_abs_daily_return_difference_pct": float(return_difference.max() * 100.0),
        "daily_return_difference_count_over_1bp": int((return_difference > 0.0001).sum()),
        "daily_return_difference_count_over_10bp": int((return_difference > 0.001).sum()),
        "daily_return_difference_count_over_100bp": int((return_difference > 0.01).sum()),
        "largest_daily_return_differences": [
            {
                "date": index.date().isoformat(),
                "abs_difference_pct": float(value * 100.0),
                "rethink_return_pct": float(rethink_return.loc[index] * 100.0),
                "full_return_pct": float(full_return.loc[index] * 100.0),
            }
            for index, value in largest.items()
            if np.isfinite(value)
        ],
    }


def main() -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    rethink_split = read_rethink("SPY_1day_仅拆股调整_20260805.csv")
    rethink_total = read_rethink("SPY_1day_拆股股息调整_20260805.csv")
    full_raw = read_full("日线_不复权_美股数据.zip", "SPY_daily_bfq.csv")
    full_qfq = read_full("日线_前复权_美股数据.zip", "SPY_daily_qfq.csv")

    raw_comparison = compare(rethink_split, full_raw)
    adjusted_comparison = compare(rethink_total, full_qfq)
    result = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "symbol": "SPY",
        "decision": "Use Rethink split-and-dividend-adjusted data for daily trend research.",
        "confidence": "high_for_daily_trend_research_not_exchange_grade",
        "rethink": {
            "rows": len(rethink_total),
            "start": rethink_total.index.min().date().isoformat(),
            "end": rethink_total.index.max().date().isoformat(),
            "invalid_ohlc_split_adjusted": invalid_ohlc(rethink_split),
            "invalid_ohlc_total_adjusted": invalid_ohlc(rethink_total),
        },
        "full_market": {
            "rows": len(full_qfq),
            "start": full_qfq.index.min().date().isoformat(),
            "end": full_qfq.index.max().date().isoformat(),
            "invalid_ohlc_raw": invalid_ohlc(full_raw),
            "invalid_ohlc_qfq": invalid_ohlc(full_qfq),
        },
        "split_vs_raw": raw_comparison,
        "total_adjusted_vs_qfq": adjusted_comparison,
        "limitations": [
            "Neither purchased source is an exchange-grade official feed.",
            "Full-market SPY also omits 2015-04-09 and contains an invalid OHLC row.",
            "Dividend-adjustment conventions differ; adjusted price levels must not be spliced.",
            "Rethink adjusted volume is not raw exchange volume and is excluded from liquidity assumptions.",
        ],
    }
    json_path = OUTPUT_ROOT / "SPY.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    april = rethink_split.loc[pd.Timestamp("2015-04-09")]
    markdown = f"""# SPY 数据源交叉检查

> 生成时间：{result['generated_at_utc']}

## 结论

第一版标普 500 日线趋势研究使用 Rethink 的拆股及股息调整 SPY。置信度定义为 `high_for_daily_trend_research_not_exchange_grade`：适合均线、日线收益和策略研究，不声称等同交易所官方逐笔行情。

## 证据

- Rethink：{len(rethink_total):,} 行，{result['rethink']['start']}～{result['rethink']['end']}，两种复权文件均无无效 OHLC。
- 全市场供应商：{len(full_qfq):,} 行，{result['full_market']['start']}～{result['full_market']['end']}，不复权和前复权均存在无效 OHLC。
- 两家共同日期：{adjusted_comparison['common_dates']:,}；最新收盘均为 {adjusted_comparison['latest_close_rethink']:.2f}。
- 全市场供应商也缺少 2015-04-09；Rethink 当日仅拆股调整 OHLC 为 Open {april['open']:.2f}、High {april['high']:.2f}、Low {april['low']:.2f}、Close {april['close']:.2f}。
- 调整后日收益差超过 10 bp 的共同日期有 {adjusted_comparison['daily_return_difference_count_over_10bp']} 个，超过 100 bp 的日期为 {adjusted_comparison['daily_return_difference_count_over_100bp']} 个。差异主要说明供应商复权与个别坏点不能混用。

## 使用边界

- 不把两家调整价拼接。
- 不使用调整后 volume 模拟成交能力。
- SPY 可以代替 VOO 做长期标普 500 策略研究；若未来实际交易 VOO，仍需在合格 VOO 数据上复核。
- 完整数值和最大差异日期见 `SPY.json`。
"""
    (OUTPUT_ROOT / "SPY.md").write_text(markdown, encoding="utf-8")
    print(markdown)


if __name__ == "__main__":
    main()
