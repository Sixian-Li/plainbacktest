#!/usr/bin/env python3
"""Build the chart-first report for the QQQ global SMA parameter search."""

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

from quantkit.experiment import block_root, load_experiment, load_run, record_analysis_complete, sha256
from quantkit.intraday_sma import prepare_intraday_sma_data
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts import analyze_intraday_sma_backtest as base
from scripts.run_intraday_sma_global_search import spec_from_row


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/DER/DER-v0.30__26-08-13__qqq_intraday_sma_global_search_full_history"


def objective_cases(results: pd.DataFrame) -> tuple[str, str]:
    return (
        str(results.loc[results["cagr_pct"].idxmax(), "case_id"]),
        str(results.loc[results["sharpe"].idxmax(), "case_id"]),
    )


def performance_figure(
    daily: pd.DataFrame,
    benchmark: pd.DataFrame,
    results: pd.DataFrame,
) -> go.Figure:
    best_cagr_id, best_sharpe_id = objective_cases(results)
    selected = [best_cagr_id]
    if best_sharpe_id != best_cagr_id:
        selected.append(best_sharpe_id)
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.7, 0.3],
        subplot_titles=("正式候选净值", "从各自峰值回撤"),
    )
    colors = ["#0f766e", "#7c3aed"]
    for case_id, color in zip(selected, colors, strict=False):
        row = results[results["case_id"] == case_id].iloc[0]
        label = "最高 CAGR 候选" if case_id == best_cagr_id else "最高 Sharpe 候选"
        if best_cagr_id == best_sharpe_id:
            label = "最高 CAGR / Sharpe 共同候选"
        frame = daily[daily["case_id"] == case_id].sort_values("date")
        for subplot, values, panel, showlegend in (
            (1, frame["equity"], "equity", True),
            (2, base.drawdown(frame["equity"]), "drawdown", False),
        ):
            figure.add_trace(
                go.Scatter(
                    x=frame["date"], y=values, mode="lines", name=label,
                    showlegend=showlegend, line={"color": color, "width": 2.5},
                    meta={"series_key": case_id, "panel": panel, "label": label},
                    hovertemplate=f"{label}<br>%{{x|%Y-%m-%d}}<br>%{{y:,.2f}}<extra></extra>",
                ), row=subplot, col=1,
            )
        bench = benchmark[benchmark["case_id"] == case_id].sort_values("date")
        bench_key = f"buy_hold_{case_id}"
        bench_label = f"同起点 Buy & Hold（{label}）"
        for subplot, values, panel, showlegend in (
            (1, bench["equity"], "equity", True),
            (2, base.drawdown(bench["equity"]), "drawdown", False),
        ):
            figure.add_trace(
                go.Scatter(
                    x=bench["date"], y=values, mode="lines", name=bench_label,
                    showlegend=showlegend,
                    line={"color": color, "width": 1.8, "dash": "dash"},
                    visible=True if case_id == best_cagr_id else "legendonly",
                    meta={
                        "series_key": bench_key, "panel": panel, "label": bench_label,
                        "is_benchmark": case_id == best_cagr_id and panel == "equity", "cost_bps": 0,
                    },
                ), row=subplot, col=1,
            )
    figure.update_layout(
        height=780, margin={"l": 65, "r": 25, "t": 70, "b": 55},
        hovermode="x unified", showlegend=False, uirevision="qqq-global-search-v1",
    )
    figure.update_yaxes(title_text="USD", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="%", fixedrange=False, row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def search_cloud_figure(screening: pd.DataFrame, formal: pd.DataFrame) -> go.Figure:
    sample = screening.iloc[::max(len(screening) // 16000, 1)].copy()
    figure = make_subplots(
        rows=1, cols=2,
        subplot_titles=("CAGR 与 Sharpe 搜索云", "相对各自 Buy & Hold 的差值"),
    )
    figure.add_trace(
        go.Scattergl(
            x=sample["cagr_pct"], y=sample["sharpe"], mode="markers",
            marker={"size": 4, "opacity": 0.3, "color": sample["max_drawdown_pct"], "colorscale": "Viridis", "showscale": True, "colorbar": {"title": "最大回撤%", "x": 0.45}},
            text=sample["search_stage"], name="已搜索组合",
            hovertemplate="CAGR %{x:.3f}%<br>Sharpe %{y:.3f}<br>%{text}<extra></extra>",
        ), row=1, col=1,
    )
    figure.add_trace(
        go.Scattergl(
            x=sample["delta_cagr_vs_buy_hold_pct_points"],
            y=sample["delta_sharpe_vs_buy_hold"], mode="markers",
            marker={"size": 4, "opacity": 0.32, "color": "#2563eb"}, name="相对基准",
            hovertemplate="ΔCAGR %{x:+.3f}pp<br>ΔSharpe %{y:+.3f}<extra></extra>",
        ), row=1, col=2,
    )
    figure.add_trace(
        go.Scatter(
            x=formal["cagr_pct"], y=formal["sharpe"], mode="markers+text",
            text=formal["case_id"], textposition="top center",
            marker={"size": 10, "color": "#dc2626", "symbol": "diamond"}, name="正式核验候选",
            hovertemplate="%{text}<br>CAGR %{x:.3f}%<br>Sharpe %{y:.3f}<extra></extra>",
        ), row=1, col=1,
    )
    figure.add_hline(y=0, line={"color": "#64748b", "dash": "dash"}, row=1, col=2)
    figure.add_vline(x=0, line={"color": "#64748b", "dash": "dash"}, row=1, col=2)
    figure.update_xaxes(title_text="CAGR（%）", row=1, col=1)
    figure.update_yaxes(title_text="Sharpe", row=1, col=1)
    figure.update_xaxes(title_text="ΔCAGR（百分点）", row=1, col=2)
    figure.update_yaxes(title_text="ΔSharpe", row=1, col=2)
    figure.update_layout(height=680, showlegend=False, margin={"l": 65, "r": 70, "t": 70, "b": 55})
    return figure


def parameter_figure(formal: pd.DataFrame) -> go.Figure:
    dimensions = [
        dict(label=label, values=formal[column])
        for column, label in (
            ("A_negative_days_slow", "A"), ("B_slow_sma_window", "B"),
            ("C_fast_derivative_pct", "C%"), ("D_negative_days_fast", "D"),
            ("E_fallback_sma_window", "E"), ("F_short_sma_center", "F中心"),
            ("F_short_sma_spacing", "F间距"), ("G_short_recovery_below_pct", "G%"),
            ("H_reentry_sma_window", "H"), ("L_cost_stop_pct", "L%"),
            ("R_forced_rebuy_pct", "R%"), ("cagr_pct", "CAGR%"), ("sharpe", "Sharpe"),
        )
    ]
    figure = go.Figure(
        go.Parcoords(
            line={"color": formal["cagr_pct"], "colorscale": "Turbo", "showscale": True, "colorbar": {"title": "CAGR%"}},
            dimensions=dimensions,
        )
    )
    figure.update_layout(height=650, margin={"l": 70, "r": 80, "t": 40, "b": 50})
    return figure


def compact_summary(summary: dict[str, Any], formal: pd.DataFrame) -> str:
    best_cagr = formal.loc[formal["cagr_pct"].idxmax()]
    best_sharpe = formal.loc[formal["sharpe"].idxmax()]
    return (
        f"<p><strong>{summary['search_case_count']:,}</strong> 组已搜索，"
        f"<strong>{summary['formal_candidate_count']}</strong> 组通过 PyBroker/独立账本正式核验。"
        f"最高 CAGR：<strong>{best_cagr.cagr_pct:.3f}%</strong>（同起点持有 {best_cagr.benchmark_cagr_pct:.3f}%）；"
        f"最高 Sharpe：<strong>{best_sharpe.sharpe:.3f}</strong>（同起点持有 {best_sharpe.benchmark_sharpe:.3f}）。"
        "下方先直接展示图；报告不包含逐笔买卖表。</p>"
    )


def markdown_report(summary: dict[str, Any], formal: pd.DataFrame, run_id: str) -> str:
    lines = [
        "# QQQ 日内 SMA 全参数双目标搜索",
        "",
        f"- Run：`{run_id}`；固定种子搜索 {summary['search_case_count']:,} 组，正式核验 {summary['formal_candidate_count']} 组",
        "- 目标：最高 CAGR、最高 Sharpe，以及各自相对同首买点 Buy & Hold 的增量",
        "- 这是允许过拟合的全样本容量测试；全空间抽样，不是穷举，也不是可采用参数",
        "",
        "| Case | 入选原因 | CAGR | 持有 CAGR | ΔCAGR | Sharpe | 持有 Sharpe | ΔSharpe | 最大回撤 | 成交 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in formal.sort_values(["cagr_pct", "sharpe"], ascending=False).itertuples(index=False):
        lines.append(
            f"| {row.case_id} | {row.selection_reason} | {row.cagr_pct:.3f}% | {row.benchmark_cagr_pct:.3f}% | {row.delta_cagr_vs_buy_hold_pct_points:+.3f} | {row.sharpe:.3f} | {row.benchmark_sharpe:.3f} | {row.delta_sharpe_vs_buy_hold:+.3f} | {row.max_drawdown_pct:.2f}% | {int(row.order_count)} |"
        )
    lines.extend([
        "", "## 边界", "",
        "- 逐笔订单仅保存在机器账本并显示为 K 线标记；报告不生成逐笔买卖表。",
        "- 每个 case 与 Buy & Hold 都从该 case 自己的第一笔普通买入成交开始。",
        "- 搜索直接使用完整历史并按结果选参，过拟合是本轮刻意接受的前提。",
    ])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    record = load_run(context, args.run_id)
    incomplete = [item["block_id"] for item in record["expected_blocks"] if item["status"] != "completed"]
    if incomplete:
        raise RuntimeError(f"Cannot analyze incomplete run: {incomplete}")
    run_root = context.run_root(args.run_id)
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(parents=True, exist_ok=True)
    block = block_root(context, args.run_id, "QQQ", 0.0)
    screening = pd.read_csv(block / "parameter_results.csv", parse_dates=["first_entry_date"])
    formal = pd.read_csv(block / "formal_candidate_results.csv", parse_dates=["first_entry_date"])
    daily = pd.read_csv(block / "daily.csv", parse_dates=["date"])
    benchmark = pd.read_csv(block / "buy_hold_daily.csv", parse_dates=["date"])
    orders = pd.read_csv(block / "orders.csv", parse_dates=["date", "signal_date"])
    summary = json.loads((block / "metrics.json").read_text(encoding="utf-8"))
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    analysis_summary = {"schema_version": 1, "experiment_id": context.config["experiment_id"], "run_id": args.run_id, "created_at_utc": created_at, **summary, "formal_candidates": base.json_safe(formal.to_dict("records"))}
    (analysis_root / "summary.json").write_text(json.dumps(analysis_summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")

    best_id = str(formal.loc[formal["cagr_pct"].idxmax(), "case_id"])
    best_row = formal[formal["case_id"] == best_id].iloc[0]
    raw = pd.read_csv(WORKSPACE_ROOT / "data/processed/daily/QQQ.csv", parse_dates=["date"])
    best_spec = spec_from_row(best_row)
    prices = prepare_intraday_sma_data(raw, best_spec)
    start = pd.Timestamp(context.config["parameters"]["analysis_start"])
    end = pd.Timestamp(context.config["parameters"]["analysis_end"])
    prices = prices[(prices["date"] >= start) & (prices["date"] <= end)].reset_index(drop=True)
    best_orders = orders[orders["case_id"] == best_id].copy()
    figures = [
        ReportFigure("performance-qqq", "最高 CAGR / Sharpe 候选与同起点持有", performance_figure(daily, benchmark, formal), "performance"),
        ReportFigure("search-cloud", "八万组全局搜索结果", search_cloud_figure(screening, formal), "generic"),
        ReportFigure("candidate-parameters", "正式候选参数与表现", parameter_figure(formal), "generic"),
        ReportFigure(
            "market-qqq",
            "最高 CAGR 候选的价格、均线与成交原因",
            base.build_market_figure(prices, best_orders, short_windows=best_spec.f_short_sma_windows),
            "market",
        ),
    ]
    (run_root / "report.md").write_text(markdown_report(summary, formal, args.run_id), encoding="utf-8")
    report = render_interactive_report(
        title="QQQ 日内 SMA 全参数双目标搜索",
        heading="QQQ 日内 SMA 全参数双目标搜索",
        subtitle="允许过拟合的全历史容量测试：同时优化 CAGR 与 Sharpe。",
        summary_html=compact_summary(summary, formal),
        notes=[
            "页面先展示净值和搜索图；不包含逐笔买卖表，成交原因仅保留在 K 线标记 hover 与机器账本。",
            "50,000 组全域固定种子抽样加 30,000 组前沿附近变异；它覆盖整个离散空间，但不是穷举全部组合。",
            "快速筛选使用与参考账本对照测试过的编译账本；报告中的候选均另经 PyBroker 和独立账本核验。",
            "完整历史上直接选择赢家必然高度过拟合，本轮只回答规则族有没有样本内容量。",
        ],
        figures=figures,
        experiment=context.config,
        run_id=args.run_id,
        template_id=str(context.config["reporting"]["template_id"]),
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")

    template_path = str(context.config["reporting"]["template_path"])
    tracked = [
        "backtest/requirements.lock", "backtest/quantkit/execution.py", "backtest/quantkit/experiment.py",
        "backtest/quantkit/intraday_sma.py", "backtest/quantkit/intraday_sma_search.py",
        "backtest/quantkit/metrics.py", "backtest/quantkit/reporting.py",
        "backtest/scripts/run_intraday_sma_backtest.py", "backtest/scripts/analyze_intraday_sma_backtest.py",
        "backtest/scripts/run_intraday_sma_global_search.py", "backtest/scripts/analyze_intraday_sma_global_search.py",
        "backtest/scripts/smoke_report_ui.mjs", f"{template_path}/page.html", f"{template_path}/styles.css",
        f"{template_path}/interactions.js", "data/processed/manifest.json", "data/processed/daily/QQQ.csv",
    ]
    provenance: dict[str, Any] = {"schema_version": 1, "experiment_id": context.config["experiment_id"], "run_id": args.run_id, "created_at_utc": created_at, "software": {"python": platform.python_version(), "numba": __import__("numba").__version__, "lib_pybroker": "1.2.12", "plotly": plotly.__version__}, "source_files": {}}
    for relative in tracked:
        path = WORKSPACE_ROOT / relative
        provenance["source_files"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    (run_root / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (run_root / "README.md").write_text(f"""# Run {args.run_id}

This immutable run contains the seeded global/refined search and formally verified frontier candidates.

- `report.html` / `report.md`: chart-first reports without a per-order table.
- `analysis/summary.json`: machine-readable search outcome.
- `QQQ/cost_0bps/`: all sampled parameters plus candidate ledgers and hashes.
- `provenance.json` / `validation.json`: source and correctness evidence.
""", encoding="utf-8")
    artifact_manifest: dict[str, Any] = {"schema_version": 1, "created_at_utc": created_at, "artifacts": {}}
    for path in sorted(run_root.rglob("*")):
        if path.is_file() and path.name not in {"artifact_manifest.json", "run.json", "validation.json"}:
            relative = str(path.relative_to(run_root))
            artifact_manifest["artifacts"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    (run_root / "artifact_manifest.json").write_text(json.dumps(artifact_manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    record_analysis_complete(context, args.run_id)
    print(f"Wrote {run_root / 'report.html'}")


if __name__ == "__main__":
    main()
