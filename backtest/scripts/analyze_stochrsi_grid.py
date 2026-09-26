#!/usr/bin/env python3
"""Analyze the RKLB Stochastic RSI grid and build formal reports."""

from __future__ import annotations

import argparse
import hashlib
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
)
from quantkit.reporting import ReportFigure, render_interactive_report


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/TIM/TIM-v0.60a.1__26-08-16__rklb_stochrsi_threshold_grid"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def point(row: pd.Series) -> dict[str, Any]:
    fields = (
        "case_id", "period", "buy_threshold", "sell_threshold", "cost_bps",
        "start", "end", "final_equity", "total_return_pct", "cagr_pct", "sharpe",
        "sortino", "max_drawdown_pct", "order_count", "closed_trade_count",
        "exposure_pct", "benchmark_cagr_pct", "benchmark_sharpe", "excess_cagr_pct_points",
    )
    return json_safe({field: row[field] for field in fields})


def markdown_table(frame: pd.DataFrame) -> str:
    lines = [
        "| 卖出阈值 | 买入阈值 | Period | CAGR | Sharpe | 最大回撤 | 订单 | 持仓率 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in frame.itertuples():
        lines.append(
            f"| {row.sell_threshold:.1f} | {row.buy_threshold:.1f} | {row.period} | "
            f"{row.cagr_pct:.3f}% | {row.sharpe:.3f} | {row.max_drawdown_pct:.2f}% | "
            f"{row.order_count} | {row.exposure_pct:.1f}% |"
        )
    return "\n".join(lines)


def curve_figure(results: pd.DataFrame, metric: str, title: str) -> go.Figure:
    figure = go.Figure()
    colors = ["#2563eb", "#16a085", "#e67e22", "#c0392b"]
    for color, ((sell, buy), group) in zip(
        colors, results.groupby(["sell_threshold", "buy_threshold"], sort=True), strict=True
    ):
        figure.add_trace(
            go.Scatter(
                x=group["period"],
                y=group[metric],
                mode="lines+markers",
                name=f"卖 {sell:g} / 买 {buy:g}",
                line={"color": color, "width": 2},
                hovertemplate="Period %{x}<br>值 %{y:.4f}<extra></extra>",
            )
        )
    figure.update_layout(
        template="plotly_white", height=530, title=title, hovermode="x unified",
        xaxis_title="Period（日）", yaxis_title="CAGR (%)" if metric == "cagr_pct" else "Sharpe",
    )
    return figure


def performance_figure(
    dates: pd.DatetimeIndex,
    selected_equity: np.ndarray,
    benchmark: pd.DataFrame,
) -> go.Figure:
    figure = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08, row_heights=[0.68, 0.32],
        subplot_titles=("账户净值", "回撤"),
    )
    series = (
        ("buy_hold", "Buy & Hold", benchmark["equity"].to_numpy(dtype=float), "#334155", True),
        ("global_cagr", "全局最高 CAGR case", selected_equity, "#2563eb", False),
    )
    for key, label, values, color, benchmark_flag in series:
        meta = {
            "series_key": key, "panel": "equity", "label": label,
            "is_benchmark": benchmark_flag, "cost_bps": 5.0,
        }
        figure.add_trace(
            go.Scatter(x=dates, y=values, name=label, line={"color": color, "width": 2}, meta=meta),
            row=1, col=1,
        )
        drawdown = values / np.maximum.accumulate(values) - 1.0
        figure.add_trace(
            go.Scatter(
                x=dates, y=drawdown * 100.0, name=f"{label} 回撤",
                line={"color": color, "width": 1.5}, showlegend=False,
                meta={"series_key": key, "panel": "drawdown", "label": label},
            ), row=2, col=1,
        )
    figure.update_layout(template="plotly_white", height=680, hovermode="x unified")
    figure.update_yaxes(title_text="Equity", row=1, col=1)
    figure.update_yaxes(title_text="Drawdown (%)", row=2, col=1)
    return figure


def market_figure(
    canonical: pd.DataFrame,
    dates: pd.DatetimeIndex,
    orders: pd.DataFrame,
    selected: pd.Series,
) -> go.Figure:
    frame = canonical[canonical["date"].isin(dates)].copy()
    figure = go.Figure()
    figure.add_trace(
        go.Candlestick(
            x=frame["date"], open=frame["open"], high=frame["high"], low=frame["low"],
            close=frame["close"], name="RKLB OHLC",
        )
    )
    selected_orders = orders[orders["case_id"] == selected["case_id"]]
    for side, color, marker in (("buy", "#16a085", "triangle-up"), ("sell", "#c0392b", "triangle-down")):
        current = selected_orders[selected_orders["type"] == side]
        figure.add_trace(
            go.Scatter(
                x=pd.to_datetime(current["date"]), y=current["fill_price"], mode="markers",
                marker={"color": color, "size": 9, "symbol": marker}, name=side.title(),
                meta={"series_key": f"{side}_fills", "panel": "market", "label": f"{side} fills"},
            )
        )
    figure.update_layout(
        template="plotly_white", height=650, hovermode="x unified", dragmode="pan",
        xaxis_rangeslider_visible=False,
    )
    figure.update_yaxes(title_text="Price")
    return figure


def indicator_figure(dates: pd.DatetimeIndex, stochrsi: np.ndarray, selected: pd.Series) -> go.Figure:
    figure = go.Figure(
        go.Scatter(
            x=dates, y=stochrsi, name=f"StochRSI({int(selected['period'])})",
            line={"color": "#7c3aed", "width": 1.5},
        )
    )
    for value, label, color in (
        (float(selected["buy_threshold"]), "买入阈值", "#16a085"),
        (float(selected["sell_threshold"]), "卖出阈值", "#c0392b"),
    ):
        figure.add_hline(y=value, line_dash="dot", line_color=color, annotation_text=label)
    figure.update_layout(
        template="plotly_white", height=430, hovermode="x unified",
        xaxis_title="Date", yaxis_title="Raw StochRSI", yaxis={"range": [-0.03, 1.03]},
    )
    return figure


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
        raise RuntimeError(f"Incomplete run blocks: {incomplete}")
    run_root = context.run_root(args.run_id)
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(parents=True, exist_ok=True)
    symbol = context.config["symbols"][0]
    cost_bps = float(context.config["cost_scenarios_bps_per_side"][0])
    block_root = run_root / symbol / cost_label(cost_bps)
    results = pd.read_csv(block_root / "parameter_results.csv").sort_values(
        ["sell_threshold", "buy_threshold", "period"]
    ).reset_index(drop=True)
    expected_count = int(context.config["parameters"]["combination_count"])
    unique_count = len(results[["sell_threshold", "buy_threshold", "period"]].drop_duplicates())
    if len(results) != expected_count or unique_count != expected_count:
        raise RuntimeError(f"Expected {expected_count} unique cases; got {len(results)} / {unique_count}")
    results.to_csv(analysis_root / "results_table.csv", index=False, lineterminator="\n")

    combinations: list[dict[str, Any]] = []
    best_rows: list[dict[str, Any]] = []
    for (sell, buy), group in results.groupby(["sell_threshold", "buy_threshold"], sort=True):
        best_cagr = group.loc[group["cagr_pct"].idxmax()]
        best_sharpe = group.loc[group["sharpe"].idxmax()]
        combinations.append(
            {
                "sell_threshold": float(sell), "buy_threshold": float(buy),
                "best_cagr": point(best_cagr), "best_sharpe": point(best_sharpe),
            }
        )
        best_rows.extend(
            [
                {"selection": "best_cagr", **point(best_cagr)},
                {"selection": "best_sharpe", **point(best_sharpe)},
            ]
        )
    global_cagr = results.loc[results["cagr_pct"].idxmax()]
    global_sharpe = results.loc[results["sharpe"].idxmax()]
    block_manifest = json.loads((block_root / "manifest.json").read_text(encoding="utf-8"))
    summary = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "purpose": "full-sample exploratory scan; not out-of-sample validation",
        "case_count": len(results),
        "analysis_start": block_manifest["analysis_start"],
        "analysis_end": block_manifest["analysis_end"],
        "benchmark": block_manifest["benchmark_metrics"],
        "global_best_cagr": point(global_cagr),
        "global_best_sharpe": point(global_sharpe),
        "threshold_combinations": combinations,
        "max_cross_check_differences": block_manifest["max_cross_check_differences"],
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    pd.DataFrame(best_rows).to_csv(analysis_root / "best_by_threshold.csv", index=False, lineterminator="\n")

    table = markdown_table(results)
    report_md = f"""# RKLB Stochastic RSI 参数网格

- 阶段：完整样本探索；不是样本外验证。
- 区间：{summary['analysis_start']}～{summary['analysis_end']}；所有 case 使用共同起点。
- 参数：period 10～130、步长 3；四组买卖阈值；单边成本 5 bps。
- 基准：CAGR {summary['benchmark']['cagr_pct']:.3f}%，Sharpe {summary['benchmark']['sharpe']:.3f}。
- 全局最高 CAGR：period {int(global_cagr['period'])}，卖 {global_cagr['sell_threshold']:.1f} / 买 {global_cagr['buy_threshold']:.1f}，CAGR {global_cagr['cagr_pct']:.3f}%，Sharpe {global_cagr['sharpe']:.3f}。
- 全局最高 Sharpe：period {int(global_sharpe['period'])}，卖 {global_sharpe['sell_threshold']:.1f} / 买 {global_sharpe['buy_threshold']:.1f}，CAGR {global_sharpe['cagr_pct']:.3f}%，Sharpe {global_sharpe['sharpe']:.3f}。

## 全部 164 个 case

{table}
"""
    (run_root / "report.md").write_text(report_md, encoding="utf-8")

    state = np.load(block_root / "daily_state.npz")
    selected_index = int(global_cagr["case_index"])
    dates = pd.DatetimeIndex(pd.to_datetime(state["dates"]))
    benchmark = pd.read_csv(block_root / "buy_hold_daily.csv")
    canonical = pd.read_csv(WORKSPACE_ROOT / f"data/processed/daily/{symbol}.csv", parse_dates=["date"])
    orders = pd.read_csv(block_root / "orders.csv")
    summary_rows = []
    for item in combinations:
        best = item["best_cagr"]
        summary_rows.append(
            "<tr>"
            f"<td>{item['sell_threshold']:.1f}</td><td>{item['buy_threshold']:.1f}</td>"
            f"<td>{int(best['period'])}</td><td>{best['cagr_pct']:.3f}%</td>"
            f"<td>{best['sharpe']:.3f}</td><td>{best['max_drawdown_pct']:.2f}%</td>"
            "</tr>"
        )
    summary_html = (
        "<h2>四组阈值各自最高 CAGR</h2><table><thead><tr><th>卖出</th><th>买入</th>"
        "<th>Period</th><th>CAGR</th><th>Sharpe</th><th>最大回撤</th></tr></thead><tbody>"
        + "".join(summary_rows) + "</tbody></table>"
        f"<p>Buy & Hold：CAGR {summary['benchmark']['cagr_pct']:.3f}%，Sharpe {summary['benchmark']['sharpe']:.3f}。"
        "全部 164 行见 report.md 与 analysis/results_table.csv。</p>"
    )
    figures = [
        ReportFigure(
            "market-rklb", "RKLB 与全局最高 CAGR case 的成交点",
            market_figure(canonical, dates, orders, global_cagr), "market",
        ),
        ReportFigure(
            "stochrsi-rklb", "全局最高 CAGR case 的 Stochastic RSI",
            indicator_figure(dates, state["stochrsi"][selected_index], global_cagr), "generic",
        ),
        ReportFigure(
            "performance-rklb", "全局最高 CAGR case 与 Buy & Hold",
            performance_figure(dates, state["equity"][selected_index], benchmark), "performance",
        ),
        ReportFigure("cagr-period-rklb", "CAGR 随 period 变化", curve_figure(results, "cagr_pct", "CAGR vs period"), "generic"),
        ReportFigure("sharpe-period-rklb", "Sharpe 随 period 变化", curve_figure(results, "sharpe", "Sharpe vs period"), "generic"),
    ]
    report_html = render_interactive_report(
        title="RKLB Stochastic RSI 参数网格",
        heading="RKLB Stochastic RSI 完整样本探索",
        subtitle=(
            f"{summary['analysis_start']}～{summary['analysis_end']} · 164 cases · "
            "收盘信号、次日开盘成交、单边 5 bps"
        ),
        summary_html=summary_html,
        notes=[
            "Raw Stochastic RSI 不做 %K/%D 平滑；同一个 period 同时用于 Wilder RSI 与 RSI 高低区间。",
            "所有 period 使用 period=130 完成预热后的共同分析起点，避免较短 period 因更早开跑而获得不同样本。",
            "空仓时 StochRSI 小于等于买入阈值则全仓买入；持仓时大于等于卖出阈值则全仓卖出。",
            "完整样本最高点只用于形成后续假设，不是未来最优参数或样本外证据。",
            "调整后 OHLC 不等同于原始成交价加公司行动现金账本；成交量不用于流动性假设。",
        ],
        figures=figures,
        experiment=context.config,
        run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    (run_root / "report.html").write_text(report_html, encoding="utf-8")

    canonical_manifest = json.loads(
        (WORKSPACE_ROOT / "data/processed/manifest.json").read_text(encoding="utf-8")
    )
    provenance = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": summary["created_at_utc"],
        "canonical_data_build_id": canonical_manifest["build_id"],
        "software": {
            "python": platform.python_version(), "lib_pybroker": "1.2.12", "plotly": plotly.__version__,
        },
        "source_files": {},
    }
    tracked = [
        "backtest/requirements.lock", "backtest/quantkit/execution.py", "backtest/quantkit/metrics.py",
        "backtest/quantkit/reference.py", "backtest/quantkit/reporting.py", "backtest/quantkit/stochrsi.py",
        "backtest/quantkit/experiment.py", "backtest/scripts/run_stochrsi_grid.py",
        "backtest/scripts/analyze_stochrsi_grid.py", "backtest/scripts/smoke_report_ui.mjs",
        "backtest/report_templates/interactive_research_v4/page.html",
        "backtest/report_templates/interactive_research_v4/styles.css",
        "backtest/report_templates/interactive_research_v4/interactions.js",
        "data/processed/manifest.json", f"data/processed/daily/{symbol}.csv",
    ]
    for relative in tracked:
        path = WORKSPACE_ROOT / relative
        provenance["source_files"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    (run_root / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    (run_root / "README.md").write_text(
        f"# Run {args.run_id}\n\nFormal RKLB Stochastic RSI grid; see report.html, report.md, analysis/, and {symbol}/{cost_label(cost_bps)}/.\n",
        encoding="utf-8",
    )
    artifact_manifest = {"schema_version": 1, "created_at_utc": summary["created_at_utc"], "artifacts": {}}
    for path in sorted(run_root.rglob("*")):
        if path.is_file() and path.name not in {"artifact_manifest.json", "run.json", "validation.json"}:
            relative = str(path.relative_to(run_root))
            artifact_manifest["artifacts"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    (run_root / "artifact_manifest.json").write_text(
        json.dumps(artifact_manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    record_analysis_complete(context, args.run_id)
    print(f"Wrote {run_root / 'report.html'}")
    print(
        f"Global CAGR best: period={int(global_cagr['period'])}, sell={global_cagr['sell_threshold']:.1f}, "
        f"buy={global_cagr['buy_threshold']:.1f}, CAGR={global_cagr['cagr_pct']:.3f}%, Sharpe={global_cagr['sharpe']:.3f}"
    )


if __name__ == "__main__":
    main()
