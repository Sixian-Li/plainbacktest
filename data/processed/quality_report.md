# Canonical 数据质量报告

> 生成时间：2026-08-16T14:36:45+00:00
> 标准化只改变字段和格式，不会自动修正供应商价格。

## 汇总

| 标的 | 登记状态 | 自动检查 | 有效状态 | 行数 | 区间 | 阻断问题 |
|---|---|---|---|---:|---|---|
| QQQ | approved | passed | approved | 6,893 | 1999-03-10～2026-08-04 | 无 |
| VOO | provisional | failed | quality_failed | 3,998 | 2010-09-09～2026-08-04 | invalid_ohlc=1 |
| SPY | approved | passed | approved | 8,435 | 1993-01-29～2026-08-04 | 无 |
| RKLB | approved | passed | approved | 1,240 | 2021-08-25～2026-08-04 | 无 |

## 逐标的详情

### QQQ

- 用途：`primary_backtest_input`
- 有效状态：`approved`
- 写入/拒绝：6,893 / 0
- 检查计数：`missing_trading_date=4`, `nontrading_calendar_date=1`

已知限制：

- Adjusted prices approximate dividend reinvestment; no independent corporate-action cash ledger is available.
- Vendor-adjusted volume is not raw exchange volume and must not drive liquidity or fill assumptions.

### VOO

- 用途：`future_trade_asset`
- 有效状态：`quality_failed`
- 写入/拒绝：3,998 / 0
- 检查计数：`invalid_ohlc=1`, `missing_trading_date=5`, `nontrading_calendar_date=1`

已知限制：

- 2018-11-15 has an invalid OHLC relationship: vendor open is below vendor low.
- The vendor's exact qfq formula has not been independently verified.
- This dataset must not be promoted to approved until the anomalous row is verified from an independent source.

阻断证据：

- `invalid_ohlc`：`[{"row": 2063, "date": "2018-11-15", "open": 218.706, "high": 222.575, "low": 218.918, "close": 222.097}]`

### SPY

- 用途：`long_history_proxy_and_validation`
- 有效状态：`approved`
- 写入/拒绝：8,435 / 0
- 检查计数：`missing_trading_date=4`, `nontrading_calendar_date=1`

已知限制：

- SPY is a proxy and validation series, not a price segment to splice directly into VOO.
- Adjusted prices approximate dividend reinvestment; no independent corporate-action cash ledger is available.
- Vendor-adjusted volume is not raw exchange volume and must not drive liquidity or fill assumptions.

### RKLB

- 用途：`single_asset_backtest_input`
- 有效状态：`approved`
- 写入/拒绝：1,240 / 0
- 检查计数：`outlier_close_return=3`, `missing_trading_date=4`, `nontrading_calendar_date=1`

已知限制：

- Rows before 2021-08-25 describe the VACQ SPAC predecessor period and are explicitly excluded from canonical RKLB history.
- The primary source and the full-market vendor qfq source have identical dates, closes, and daily close returns throughout the canonical range.
- Adjusted prices approximate total return; no independent corporate-action cash ledger is available.
- Vendor-adjusted volume must not drive liquidity or fill assumptions.

明确排除：

- 2020-11-24～2021-08-24：187 行；Exclude the pre-combination VACQ SPAC period; SEC evidence states RKLB trading began on 2021-08-25.

## 使用规则

- 只有 `effective_status=approved` 的文件可以默认进入正式回测。
- `provisional` 和 `quality_failed` 需要实验配置显式覆盖，并在报告中显示警告。
- 完整逐行证据见同目录 `quality_report.json`；本文件只展示计数和最多五条例子。

## 交易日历交叉检查

- 供应商日历状态：`suspect`
- 判断方法：同时参考登记标的；全部标的共同缺失或周末被标为交易日时，优先判定日历可疑，不把它误算成所有行情源同时缺失。

疑似供应商日历错误：

- `2023-06-19`：absent_from_all_covered_datasets（Monday）
- `2024-06-19`：absent_from_all_covered_datasets（Wednesday）
- `2025-01-26`：absent_from_all_covered_datasets（Sunday）
- `2025-06-19`：absent_from_all_covered_datasets（Thursday）
- `2023-06-20`：data_present_for_all_covered_datasets（Tuesday）

疑似单标的数据缺口：

- `2015-04-09`：缺少 VOO；同日有数据 QQQ, SPY。

供应商交易日历当前不能作为唯一权威来源。第二阶段应使用经过测试的交易所日历实现，购买日历只作为交叉检查。
