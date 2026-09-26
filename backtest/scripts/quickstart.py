#!/usr/bin/env python3
"""Run an offline, ledger-checked dual-SMA example using the existing framework."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

BACKTEST = Path(__file__).resolve().parents[1]
ROOT = BACKTEST.parent
sys.path.insert(0, str(BACKTEST))

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from dual_sma_lab.data_sources import discover_approved_datasets, load_workspace_dataset
from quantkit.dual_sma_state import DualSmaStateSpec, analysis_slice, prepare_dual_sma_data, run_pybroker_dual_sma, run_reference_dual_sma
from quantkit.execution import ExplicitFillPolicy
from quantkit.metrics import calculate_metrics, pybroker_daily_state
from quantkit.reference import run_buy_and_hold_reference
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts.release_data import sha256, snapshot_files
from scripts.run_sma_threshold_grid import cross_check, json_safe, normalize_frame


def execute(config: dict, output_parent: Path) -> Path:
    symbol = str(config["symbol"]).upper()
    available = discover_approved_datasets(ROOT)
    if symbol not in available:
        raise ValueError(f"No approved distributed daily data for {symbol}; VOO is intentionally excluded.")
    dataset = available[symbol]
    expected = {item["path"]: item for item in snapshot_files()}
    rel = dataset.path.relative_to(ROOT).as_posix()
    if rel not in expected or sha256(dataset.path) != expected[rel]["sha256"]:
        raise ValueError("Dataset differs from the distributed snapshot; register a new snapshot before reusing this example.")
    if not np.isfinite(config["initial_cash"]) or config["initial_cash"] <= 0:
        raise ValueError("initial_cash must be finite and positive")
    if not np.isfinite(config["cost_bps_per_side"]) or not 0 <= config["cost_bps_per_side"] < 10000:
        raise ValueError("cost_bps_per_side must be in [0, 10000)")
    start, end = pd.Timestamp(config["start"]), pd.Timestamp(config["end"])
    if start > end:
        raise ValueError("start must not be after end")
    spec = DualSmaStateSpec(int(config["fast_window"]), int(config["slow_window"]))
    policy = ExplicitFillPolicy("open", cost_bps=float(config["cost_bps_per_side"]))
    data = load_workspace_dataset(dataset)
    required = ["open", "high", "low", "close", "volume"]
    if not np.isfinite(data[required].to_numpy(float)).all():
        raise ValueError("Non-finite OHLCV")
    if ((data["low"] > data[["open", "close"]].min(axis=1)) | (data["high"] < data[["open", "close"]].max(axis=1)) | (data["low"] > data["high"])).any():
        raise ValueError("Invalid OHLC relationship")
    frame = analysis_slice(prepare_dual_sma_data(data, spec), start, end)
    if len(frame) < 2:
        raise ValueError("At least two fully warmed sessions are required")
    initial = float(config["initial_cash"])
    actual = run_pybroker_dual_sma(frame, spec, policy, initial_cash=initial)
    daily = pybroker_daily_state(actual, frame)
    reference = run_reference_dual_sma(frame, spec, policy, initial_cash=initial)
    if pd.to_datetime(daily["date"]).tolist() != pd.to_datetime(reference.daily["date"]).tolist():
        raise AssertionError("Independent daily ledger dates differ")
    differences = cross_check(actual, daily, reference, tolerance=1e-6)
    benchmark = run_buy_and_hold_reference(frame, policy, initial_cash=initial)
    actual_orders = actual.orders.reset_index()
    actual_trades = actual.trades.reset_index()
    metrics = {"strategy": calculate_metrics(daily, actual_orders, actual_trades, initial_cash=initial),
               "benchmark": calculate_metrics(benchmark.daily, benchmark.orders, benchmark.trades, initial_cash=initial)}
    run_id = "example_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + uuid4().hex[:8]
    run = output_parent / run_id
    run.mkdir(parents=True, exist_ok=False)
    story = {
        "summary": f"在 {symbol} 的复权日线上比较 SMA{spec.fast_window} 与 SMA{spec.slow_window}，研究简单趋势持仓规则。",
        "buy": f"每个已完成交易日收盘后，若 SMA{spec.fast_window} 高于 SMA{spec.slow_window} 且当前空仓，就在下一交易日开盘用可用现金买入；这是状态条件，不要求当天首次上穿。",
        "sell": f"持仓时，若收盘后的 SMA{spec.fast_window} 小于或等于 SMA{spec.slow_window}，就于下一交易日开盘全部卖出。",
        "execution": "仅用已完成的日线确认信号，下一真实交易日 Open 成交。区间前的历史只用于均线预热；最后一日的信号没有下一日时不成交。",
        "position": f"初始资金 {initial:,.2f} 美元，允许零碎股、全仓或现金，不做空、不融资、现金不计息。买卖各计 {policy.cost_bps:g} bps 成本。基准在分析区间首日收盘形成买入指令，下一日开盘买入并持有，采用相同成本。"
    }
    frozen = {"schema_version": 1, "experiment_id": "distributed_dual_sma_example", "symbols": [symbol],
              "strategy": {"name": "双均线持仓示例", "description": story["summary"], "buy_rule": story["buy"], "sell_rule": story["sell"],
                           "signal_time": "completed daily Close", "execution_time": "next session Open", "plain_language": story},
              "parameters": config, "initial_cash": initial, "cost_scenarios_bps_per_side": [policy.cost_bps],
              "benchmark": story["position"], "research": {"stage": "exploratory"},
              "reporting": {"template_id": "interactive_research_v5"}}
    def write_json(name: str, payload: object) -> None:
        (run / name).write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    write_json("experiment_snapshot.json", frozen)
    write_json("metrics.json", metrics)
    write_json("validation.json", {"status": "passed", "scope": "example independent-ledger reconciliation; not a formal validate_run or strategy promotion", "daily_rows": len(daily), "orders": len(actual_orders), "tolerance": 1e-6, "differences": differences})
    for name, values in {"input.csv": frame, "daily.csv": daily, "reference_daily.csv": reference.daily,
                         "orders.csv": actual_orders, "reference_orders.csv": reference.orders,
                         "trades.csv": actual_trades, "benchmark_daily.csv": benchmark.daily}.items():
        normalize_frame(values).to_csv(run / name, index=False)
    source_paths = [Path(__file__), BACKTEST / "scripts/release_data.py", BACKTEST / "scripts/run_sma_threshold_grid.py", BACKTEST / "dual_sma_lab/data_sources.py", BACKTEST / "requirements.lock"]
    source_paths += sorted((BACKTEST / "quantkit").glob("*.py"))
    source_paths += list((BACKTEST / "report_templates/interactive_research_v5").glob("*"))
    write_json("provenance.json", {"data_path": rel, "data_sha256": sha256(dataset.path), "input_sha256": sha256(run / "input.csv"),
               "python": platform.python_version(), "dependencies": {name: importlib.metadata.version(name) for name in ("lib-pybroker", "pandas", "numpy", "plotly")},
               "code": {p.relative_to(ROOT).as_posix(): sha256(p) for p in source_paths if p.is_file()},
               "price_semantics": "split and dividend adjusted OHLC; total-return approximation, no independent cash-dividend ledger"})
    performance = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.72, 0.28], vertical_spacing=0.08)
    for name, values, key, is_benchmark in (("策略", daily, "strategy", False), ("买入持有", benchmark.daily, "benchmark", True)):
        performance.add_trace(go.Scatter(x=values["date"], y=values["equity"], name=name, meta={"panel": "equity", "series_key": key, "label": name, "is_benchmark": is_benchmark, "cost_bps": policy.cost_bps}), row=1, col=1)
        drawdown = (values["equity"] / values["equity"].cummax() - 1) * 100
        performance.add_trace(go.Scatter(x=values["date"], y=drawdown, name=name + "回撤", meta={"panel": "drawdown", "series_key": key, "label": name}), row=2, col=1)
    performance.update_layout(yaxis_title="账户净值（USD）", yaxis2_title="回撤（%）", height=620)
    market = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25], vertical_spacing=0.08)
    market.add_trace(go.Candlestick(x=frame["date"], open=frame["open"], high=frame["high"], low=frame["low"], close=frame["close"], name=symbol), row=1, col=1)
    for key, window in (("fast_sma", spec.fast_window), ("slow_sma", spec.slow_window)):
        market.add_trace(go.Scatter(x=frame["date"], y=frame[key], name=f"SMA{window}", meta={"panel": "market", "series_key": key, "label": f"SMA{window}"}), row=1, col=1)
    for side, label, color, marker in (("buy", "买入成交", "#198754", "triangle-up"), ("sell", "卖出成交", "#c0392b", "triangle-down")):
        orders = actual_orders[actual_orders["type"].eq(side)]
        market.add_trace(go.Scatter(x=orders["date"], y=orders["fill_price"], mode="markers", name=label, marker={"color": color, "symbol": marker, "size": 10}, meta={"panel": "market", "series_key": side, "label": label}), row=1, col=1)
    market.add_trace(go.Scatter(x=frame["date"], y=frame["close"].pct_change().fillna(0) * 100, name="收盘日涨跌", meta={"panel": "market", "series_key": "daily_return", "label": "收盘日涨跌"}), row=2, col=1)
    market.update_layout(height=620, xaxis_rangeslider_visible=False, yaxis2_title="日涨跌（%）")
    rows = "".join(f"<tr><td>{label}</td><td>{values['total_return_pct']:.2f}%</td><td>{values['cagr_pct']:.2f}%</td><td>{values['sharpe']:.3f}</td><td>{values['max_drawdown_pct']:.2f}%</td></tr>" for label, values in (("策略", metrics["strategy"]), ("买入持有", metrics["benchmark"])))
    report = render_interactive_report(title=f"{symbol} 双均线回测示例", heading=f"{symbol} 双均线回测示例",
        subtitle=f"{frame.date.min().date()} — {frame.date.max().date()} · 独立账本核对通过 · 探索性示例",
        summary_html="<table><tr><th>路径</th><th>累计收益</th><th>CAGR</th><th>Sharpe</th><th>最大回撤</th></tr>" + rows + "</table>",
        notes=["附带数据快照截至 2026-08-04；这是示例执行与账本核对，不是策略晋级或原历史研究的重新验证。", "报告使用调整后价格近似总回报，未模拟独立现金分红、税费或流动性限制。"],
        figures=[ReportFigure(f"performance-{symbol.lower()}", "账户表现", performance, "performance"), ReportFigure(f"market-{symbol.lower()}", "价格与均线", market, "market")],
        experiment=frozen, run_id=run_id)
    (run / "report.html").write_text(report, encoding="utf-8")
    (run / "report.md").write_text(f"# {symbol} 双均线示例\n\n{story['summary']}\n\n独立账本逐日与逐笔核对通过。指标见 metrics.json；详细规则见 experiment_snapshot.json；交互报告见 report.html。\n", encoding="utf-8")
    write_json("artifact_manifest.json", {p.name: {"sha256": sha256(p), "bytes": p.stat().st_size} for p in sorted(run.iterdir()) if p.is_file()})
    return run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=BACKTEST / "examples/dual_sma.json")
    parser.add_argument("--symbol")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--fast", type=int)
    parser.add_argument("--slow", type=int)
    parser.add_argument("--cost-bps", type=float)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    for argument, field in (("symbol", "symbol"), ("start", "start"), ("end", "end"), ("fast", "fast_window"), ("slow", "slow_window"), ("cost_bps", "cost_bps_per_side")):
        value = getattr(args, argument)
        if value is not None:
            config[field] = value
    # The example prompt belongs to its original config; resolved rules are regenerated above.
    config.pop("natural_language", None)
    try:
        output = execute(config, args.output_dir)
    except (ValueError, AssertionError, KeyError) as exc:
        parser.exit(1, f"Example failed: {exc}\n")
    print(f"Independent ledger: PASS\nReport: {output / 'report.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
