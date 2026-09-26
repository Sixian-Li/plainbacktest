#!/usr/bin/env python3
"""Analyze the completed RKLB intraday dynamic-SMA grid and build its report."""

from __future__ import annotations

import argparse
import html
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from quantkit.experiment import (
    assert_run_writable,
    cost_label,
    load_experiment,
    load_run,
    record_analysis_complete,
    sha256,
)
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts.run_intraday_sma_backtest import json_safe


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = (
    BACKTEST_ROOT
    / "experiments/TIM/TIM-v0.60b.1__26-08-16__rklb_intraday_sma_threshold_grid"
)


def drawdown(values: pd.Series | np.ndarray) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    return (array / np.maximum.accumulate(array) - 1.0) * 100.0


def load_block(run_root: Path, symbol: str, cost: float) -> tuple[pd.DataFrame, dict[str, Any]]:
    root = run_root / symbol / cost_label(cost)
    frame = pd.read_csv(root / "parameter_results.csv")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    return frame, manifest


def stable_selection(frame: pd.DataFrame) -> tuple[pd.Series, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    windows = sorted(int(value) for value in frame["sma_window"].unique())
    for (sell_pct, buy_pct), group in frame.groupby(
        ["sell_below_sma_pct", "buy_above_sma_pct"]
    ):
        indexed = group.set_index("sma_window")
        for window in windows[1:-1]:
            neighborhood = indexed.loc[[window - 2, window, window + 2], "cagr_pct"].astype(float)
            current = indexed.loc[window]
            rows.append(
                {
                    "case_id": current["case_id"],
                    "sma_window": window,
                    "sell_below_sma_pct": float(sell_pct),
                    "buy_above_sma_pct": float(buy_pct),
                    "neighborhood_median_cagr_pct": float(neighborhood.median()),
                    "neighborhood_std_cagr_pct": float(neighborhood.std(ddof=0)),
                    "cagr_pct": float(current["cagr_pct"]),
                    "sharpe": float(current["sharpe"]),
                    "max_drawdown_pct": float(current["max_drawdown_pct"]),
                    "order_count": int(current["order_count"]),
                }
            )
    diagnostics = pd.DataFrame(rows)
    ordered = diagnostics.sort_values(
        [
            "neighborhood_median_cagr_pct",
            "neighborhood_std_cagr_pct",
            "sharpe",
            "max_drawdown_pct",
        ],
        ascending=[False, True, False, False],
        kind="stable",
    )
    selected_id = str(ordered.iloc[0]["case_id"])
    selected = frame[frame["case_id"] == selected_id]
    if len(selected) != 1:
        raise AssertionError("Stable selection did not resolve to exactly one case.")
    result = selected.iloc[0].copy()
    result["neighborhood_median_cagr_pct"] = ordered.iloc[0]["neighborhood_median_cagr_pct"]
    result["neighborhood_std_cagr_pct"] = ordered.iloc[0]["neighborhood_std_cagr_pct"]
    return result, diagnostics


def parameter_figure(frame: pd.DataFrame, best: pd.Series, stable: pd.Series) -> go.Figure:
    labels = frame.apply(
        lambda row: f"卖{row['sell_below_sma_pct']:g}% / 买{row['buy_above_sma_pct']:g}%",
        axis=1,
    )
    plot = frame.assign(threshold_pair=labels)
    pair_order = [f"卖{s}% / 买{b}%" for s in (1, 2, 3) for b in (1, 2, 3)]
    pivot = plot.pivot(index="threshold_pair", columns="sma_window", values="cagr_pct").reindex(pair_order)
    figure = go.Figure(
        go.Heatmap(
            x=pivot.columns,
            y=pivot.index,
            z=pivot.to_numpy(),
            colorscale="Turbo",
            colorbar={"title": "CAGR %"},
            hovertemplate="SMA=%{x}<br>%{y}<br>CAGR=%{z:.3f}%<extra></extra>",
        )
    )
    for row, symbol, name in ((best, "x", "机械最高 CAGR"), (stable, "circle-open", "相邻窗口稳定代表")):
        figure.add_trace(
            go.Scatter(
                x=[int(row["sma_window"])],
                y=[f"卖{row['sell_below_sma_pct']:g}% / 买{row['buy_above_sma_pct']:g}%"],
                mode="markers",
                name=name,
                marker={"symbol": symbol, "size": 15, "color": "white", "line": {"width": 3}},
            )
        )
    figure.update_layout(
        height=590,
        margin={"l": 110, "r": 55, "t": 55, "b": 55},
        xaxis_title="SMA 窗口 n",
        yaxis_title="阈值组合",
        hovermode="closest",
    )
    return figure


def performance_figure(
    block_root: Path,
    benchmark: pd.DataFrame,
    selected: list[tuple[str, str]],
) -> go.Figure:
    state = np.load(block_root / "daily_state.npz")
    dates = pd.to_datetime(state["dates"])
    ids = [str(value) for value in state["case_id"]]
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.7, 0.3],
        subplot_titles=("5 bps 策略净值", "从各自历史峰值回撤"),
    )
    colors = ("#2563eb", "#0f766e")
    for (case_id, label), color in zip(selected, colors, strict=True):
        values = state["equity"][ids.index(case_id)]
        for panel, series, panel_name, showlegend in (
            (1, values, "equity", True),
            (2, drawdown(values), "drawdown", False),
        ):
            figure.add_trace(
                go.Scatter(
                    x=dates,
                    y=series,
                    mode="lines",
                    name=label,
                    showlegend=showlegend,
                    line={"color": color, "width": 2.2},
                    meta={"series_key": case_id, "panel": panel_name, "label": label},
                ),
                row=panel,
                col=1,
            )
    benchmark_values = benchmark["equity"].to_numpy(float)
    for panel, series, panel_name, showlegend in (
        (1, benchmark_values, "equity", True),
        (2, drawdown(benchmark_values), "drawdown", False),
    ):
        figure.add_trace(
            go.Scatter(
                x=pd.to_datetime(benchmark["date"]),
                y=series,
                mode="lines",
                name="RKLB Buy & Hold",
                showlegend=showlegend,
                line={"color": "#475569", "width": 2, "dash": "dash"},
                meta={
                    "series_key": "buy_hold_5bps",
                    "panel": panel_name,
                    "label": "RKLB Buy & Hold",
                    "is_benchmark": panel_name == "equity",
                    "cost_bps": 5,
                },
            ),
            row=panel,
            col=1,
        )
    figure.update_layout(height=780, margin={"l": 65, "r": 25, "t": 70, "b": 55}, hovermode="x unified")
    figure.update_yaxes(title_text="USD", row=1, col=1)
    figure.update_yaxes(title_text="%", row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def market_figure(prices: pd.DataFrame, orders: pd.DataFrame, stable: pd.Series) -> go.Figure:
    window = int(stable["sma_window"])
    prices = prices.copy()
    prices["sma"] = prices["close"].rolling(window).mean()
    figure = go.Figure(
        go.Candlestick(
            x=prices["date"],
            open=prices["open"],
            high=prices["high"],
            low=prices["low"],
            close=prices["close"],
            name="RKLB 复权 OHLC",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=prices["date"],
            y=prices["sma"],
            mode="lines",
            name=f"SMA{window}",
            line={"color": "#f59e0b", "width": 2},
            meta={"series_key": f"sma{window}", "panel": "market", "label": f"SMA{window}"},
        )
    )
    for side, color, marker, label in (
        ("buy", "#087f5b", "triangle-up", "买入成交"),
        ("sell", "#c92a2a", "triangle-down", "卖出成交"),
    ):
        rows = orders[orders["type"] == side]
        figure.add_trace(
            go.Scatter(
                x=pd.to_datetime(rows["date"]),
                y=rows["fill_price"],
                mode="markers",
                name=label,
                marker={"color": color, "symbol": marker, "size": 10},
                meta={"series_key": side, "panel": "market", "label": label},
            )
        )
    figure.update_layout(height=720, margin={"l": 65, "r": 25, "t": 55, "b": 55}, xaxis_rangeslider_visible=False)
    return figure


def case_text(row: pd.Series) -> str:
    return (
        f"SMA{int(row['sma_window'])}，卖出 {row['sell_below_sma_pct']:g}%，"
        f"买入 {row['buy_above_sma_pct']:g}%"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    assert_run_writable(context, args.run_id)
    record = load_run(context, args.run_id)
    incomplete = [item["block_id"] for item in record["expected_blocks"] if item["status"] != "completed"]
    if incomplete:
        raise RuntimeError(f"Incomplete blocks: {incomplete}")
    run_root = context.run_root(args.run_id)
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(parents=True, exist_ok=True)
    symbol = context.config["symbols"][0]
    zero, zero_manifest = load_block(run_root, symbol, 0)
    five, five_manifest = load_block(run_root, symbol, 5)
    best = five.loc[five["cagr_pct"].idxmax()].copy()
    stable, diagnostics = stable_selection(five)
    base_root = run_root / symbol / cost_label(5)
    benchmark = pd.read_csv(base_root / "buy_hold_daily.csv")
    benchmark_metrics = five_manifest["benchmark_metrics"]
    selected = pd.DataFrame(
        [
            {"selection": "mechanical_cagr_max", **best.to_dict()},
            {"selection": "adjacent_window_stable", **stable.to_dict()},
        ]
    )
    selected.to_csv(analysis_root / "selected_cases.csv", index=False, lineterminator="\n")
    diagnostics.to_csv(analysis_root / "window_stability.csv", index=False, lineterminator="\n")
    cost_compare = five[
        ["case_id", "sma_window", "sell_below_sma_pct", "buy_above_sma_pct", "cagr_pct"]
    ].merge(
        zero[["sma_window", "sell_below_sma_pct", "buy_above_sma_pct", "cagr_pct"]],
        on=["sma_window", "sell_below_sma_pct", "buy_above_sma_pct"],
        suffixes=("_5bps", "_0bps"),
    )
    summary = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "research_stage": "exploratory",
        "case_count_per_cost": len(five),
        "analysis_start": five_manifest["analysis_start"],
        "analysis_end": five_manifest["analysis_end"],
        "mechanical_cagr_max": json_safe(best.to_dict()),
        "adjacent_window_stable": json_safe(stable.to_dict()),
        "benchmark_metrics_5bps": benchmark_metrics,
        "median_cagr_cost_drag_pct_points": float(
            (cost_compare["cagr_pct_0bps"] - cost_compare["cagr_pct_5bps"]).median()
        ),
        "max_cross_check_differences": {
            "0bps": zero_manifest["max_cross_check_differences"],
            "5bps": five_manifest["max_cross_check_differences"],
        },
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    stable_orders_all = pd.read_csv(base_root / "orders.csv")
    stable_orders = stable_orders_all[stable_orders_all["case_id"] == stable["case_id"]]
    prices = pd.read_csv(WORKSPACE_ROOT / f"data/processed/daily/{symbol}.csv")
    prices["date"] = pd.to_datetime(prices["date"])
    prices = prices[
        (prices["date"] >= pd.Timestamp(summary["analysis_start"]))
        & (prices["date"] <= pd.Timestamp(summary["analysis_end"]))
    ].copy()
    figures = [
        ReportFigure(
            "performance-rklb",
            "RKLB：5 bps 净值与回撤",
            performance_figure(
                base_root,
                benchmark,
                [
                    (str(best["case_id"]), f"机械最高：{case_text(best)}"),
                    (str(stable["case_id"]), f"稳定代表：{case_text(stable)}"),
                ],
            ),
            "performance",
        ),
        ReportFigure(
            "parameter-surface-rklb",
            "RKLB：SMA 窗口与九种阈值（5 bps CAGR）",
            parameter_figure(five, best, stable),
            "heatmap",
        ),
        ReportFigure(
            "market-rklb",
            "RKLB：稳定代表的市场路径与成交",
            market_figure(prices, stable_orders, stable),
            "market",
        ),
    ]
    summary_html = (
        "<h2>5 bps 主要结果</h2><table><thead><tr><th>选择</th><th>参数</th>"
        "<th>CAGR</th><th>Sharpe</th><th>最大回撤</th><th>订单</th></tr></thead><tbody>"
        f"<tr><td>机械最高</td><td>{html.escape(case_text(best))}</td><td>{best['cagr_pct']:.2f}%</td>"
        f"<td>{best['sharpe']:.3f}</td><td>{best['max_drawdown_pct']:.2f}%</td><td>{int(best['order_count'])}</td></tr>"
        f"<tr><td>相邻窗口稳定代表</td><td>{html.escape(case_text(stable))}</td><td>{stable['cagr_pct']:.2f}%</td>"
        f"<td>{stable['sharpe']:.3f}</td><td>{stable['max_drawdown_pct']:.2f}%</td><td>{int(stable['order_count'])}</td></tr>"
        f"<tr><td>买入持有</td><td>共同起点 Open 买入</td><td>{benchmark_metrics['cagr_pct']:.2f}%</td>"
        f"<td>{benchmark_metrics['sharpe']:.3f}</td><td>{benchmark_metrics['max_drawdown_pct']:.2f}%</td><td>1</td></tr>"
        "</tbody></table>"
    )
    notes = [
        "这是使用全部获批 RKLB 历史的探索性扫描，不是样本外验证或实盘建议。",
        "所有 459 个参数 case 使用共同起点，避免较长 SMA 因较晚预热而获得不同评价区间。",
        "动态阈值在开盘前由此前 n-1 个完成收盘代数求解；跳空按 Open，盘中触线按理论价。",
        "日线不知道 High 与 Low 的先后，因此每个交易日最多允许一笔成交。",
        "复权 OHLC 适合内部一致的趋势研究，不是交易所级原始价格与公司行动账户回放。",
    ]
    report_html = render_interactive_report(
        title="RKLB 日内动态 SMA 双阈值回测",
        heading="RKLB 日内动态 SMA 15–115 × 九种阈值",
        subtitle=(
            f"{summary['analysis_start']} 至 {summary['analysis_end']}；459 个 case；"
            "零成本与单边 5 bps；全量 PyBroker/独立账本核对。"
        ),
        summary_html=summary_html,
        notes=notes,
        figures=figures,
        experiment=context.config,
        run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    (run_root / "report.html").write_text(report_html, encoding="utf-8")
    markdown = f"""# RKLB 日内动态 SMA 双阈值回测

> 全样本探索，不是样本外验证或实盘建议。

## 口径

- 区间：{summary['analysis_start']}～{summary['analysis_end']}，所有窗口共用评估起点。
- 参数：SMA 15～115、步长 2；卖出阈值 1/2/3%；买入阈值 1/2/3%；每个成本情景 459 个 case。
- 成交：盘前解动态 SMA 阈值；跳空按 Open，High/Low 盘中触线按理论阈值；每日最多一笔。
- 成本：0 bps 与单边 5 bps；初始空仓、全仓进出、允许碎股、不融资。

## 5 bps 结果

- 机械最高：{case_text(best)}；CAGR {best['cagr_pct']:.3f}%，Sharpe {best['sharpe']:.3f}，最大回撤 {best['max_drawdown_pct']:.2f}%，订单 {int(best['order_count'])}。
- 相邻窗口稳定代表：{case_text(stable)}；CAGR {stable['cagr_pct']:.3f}%，Sharpe {stable['sharpe']:.3f}，最大回撤 {stable['max_drawdown_pct']:.2f}%，订单 {int(stable['order_count'])}。
- 买入持有：CAGR {benchmark_metrics['cagr_pct']:.3f}%，Sharpe {benchmark_metrics['sharpe']:.3f}，最大回撤 {benchmark_metrics['max_drawdown_pct']:.2f}%。
- 0→5 bps 的全网格 CAGR 中位损失：{summary['median_cagr_cost_drag_pct_points']:.3f} 个百分点。

## 正确性与限制

- 两个成本区块共 918 个 case 均由 PyBroker 与独立账本逐日核对。
- RKLB 标准历史从 2021-08-25 开始，排除了 VACQ/SPAC 前身；前 114 根仅用于最长 SMA 预热。
- 样本仅约四年，机械最高和稳定代表都只能用于提出后续候选。
"""
    (run_root / "report.md").write_text(markdown, encoding="utf-8")

    created_at = summary["created_at_utc"]
    template_path = context.config["reporting"]["template_path"]
    tracked = [
        "backtest/requirements.lock",
        "backtest/quantkit/experiment.py",
        "backtest/quantkit/execution.py",
        "backtest/quantkit/metrics.py",
        "backtest/quantkit/intraday_sma_threshold.py",
        "backtest/quantkit/reporting.py",
        "backtest/scripts/run_intraday_sma_backtest.py",
        "backtest/scripts/run_intraday_sma200_threshold_grid.py",
        "backtest/scripts/run_rklb_intraday_sma_grid.py",
        "backtest/scripts/analyze_rklb_intraday_sma_grid.py",
        f"{template_path}/page.html",
        f"{template_path}/styles.css",
        f"{template_path}/interactions.js",
        "backtest/scripts/smoke_report_ui.mjs",
        "data/processed/manifest.json",
        "data/processed/daily/RKLB.csv",
    ]
    provenance: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": created_at,
        "canonical_data_build_id": zero_manifest["source_manifest_build_id"],
        "software": {
            "python": platform.python_version(),
            "lib_pybroker": "1.2.12",
            "plotly": plotly.__version__,
        },
        "source_files": {},
    }
    for relative in tracked:
        path = WORKSPACE_ROOT / relative
        provenance["source_files"][relative] = {
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
    (run_root / "provenance.json").write_text(
        json.dumps(json_safe(provenance), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (run_root / "README.md").write_text(
        f"# Run {args.run_id}\n\nFrozen RKLB dynamic-SMA grid run. See `report.html`, `report.md`, `analysis/`, and the two cost blocks.\n",
        encoding="utf-8",
    )
    artifact_manifest: dict[str, Any] = {
        "schema_version": 1,
        "created_at_utc": created_at,
        "artifacts": {},
    }
    for path in sorted(run_root.rglob("*")):
        if path.is_file() and path.name not in {"artifact_manifest.json", "run.json", "validation.json"}:
            relative = str(path.relative_to(run_root))
            artifact_manifest["artifacts"][relative] = {
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
    (run_root / "artifact_manifest.json").write_text(
        json.dumps(artifact_manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    record_analysis_complete(context, args.run_id)
    print(f"Wrote {run_root / 'report.html'}")
    print(f"Mechanical best: {case_text(best)}; CAGR={best['cagr_pct']:.4f}%")
    print(f"Stable representative: {case_text(stable)}; CAGR={stable['cagr_pct']:.4f}%")


if __name__ == "__main__":
    main()
