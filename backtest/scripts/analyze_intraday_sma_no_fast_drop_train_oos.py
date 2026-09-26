#!/usr/bin/env python3
"""Build the chart-first report for QQQ no-C/D training and locked OOS."""

from __future__ import annotations

import argparse
import html
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import plotly
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from quantkit.experiment import (
    block_root,
    load_experiment,
    load_run,
    record_analysis_complete,
    sha256,
)
from quantkit.intraday_sma import prepare_intraday_sma_data
from quantkit.intraday_sma_plateau import FREE_PARAMETER_COLUMNS
from quantkit.paths import BACKTEST_ROOT
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts import analyze_intraday_sma_backtest as base
from scripts.run_intraday_sma_backtest import json_safe
from scripts.run_intraday_sma_global_search import spec_from_row


WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = (
    BACKTEST_ROOT
    / "experiments/DER/DER-v0.60__26-08-14__qqq_intraday_sma_no_fast_drop_train_oos"
)


def performance_figure(
    daily: pd.DataFrame,
    benchmark: pd.DataFrame,
    *,
    window_id: str,
    case_id: str,
    title: str,
) -> go.Figure:
    strategy = daily[
        (daily["window_id"] == window_id) & (daily["case_id"] == case_id)
    ].sort_values("date")
    buy_hold = benchmark[
        (benchmark["window_id"] == window_id) & (benchmark["case_id"] == case_id)
    ].sort_values("date")
    if strategy.empty or buy_hold.empty:
        raise ValueError(f"Missing performance series for {window_id}/{case_id}.")
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.7, 0.3],
        subplot_titles=(title, "从各自峰值回撤"),
    )
    series = [
        ("strategy", "策略", strategy, "#0f766e", False),
        ("buy_hold", "QQQ Buy & Hold", buy_hold, "#64748b", True),
    ]
    for key, label, frame, color, benchmark_flag in series:
        for row, values, panel, showlegend in (
            (1, frame["equity"], "equity", True),
            (2, base.drawdown(frame["equity"]), "drawdown", False),
        ):
            figure.add_trace(
                go.Scatter(
                    x=frame["date"],
                    y=values,
                    mode="lines",
                    name=label,
                    showlegend=showlegend,
                    line={"color": color, "width": 2.4, "dash": "dash" if benchmark_flag else "solid"},
                    meta={
                        "series_key": f"{window_id.lower()}_{key}",
                        "panel": panel,
                        "label": label,
                        "is_benchmark": benchmark_flag and panel == "equity",
                        "cost_bps": 0,
                    },
                    hovertemplate=f"{label}<br>%{{x|%Y-%m-%d}}<br>%{{y:,.2f}}<extra></extra>",
                ),
                row=row,
                col=1,
            )
    figure.update_layout(
        height=760,
        margin={"l": 65, "r": 25, "t": 70, "b": 55},
        hovermode="x unified",
        showlegend=False,
        uirevision=f"qqq-no-fast-{window_id.lower()}",
    )
    figure.update_yaxes(title_text="USD", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="%", fixedrange=False, row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def search_cloud_figure(screening: pd.DataFrame, selected: pd.Series) -> go.Figure:
    stride = max(len(screening) // 20000, 1)
    sample = screening.iloc[::stride].copy()
    figure = go.Figure()
    figure.add_trace(
        go.Scattergl(
            x=sample["cagr_pct"],
            y=sample["sharpe"],
            mode="markers",
            marker={
                "size": 4,
                "opacity": 0.28,
                "color": sample["max_drawdown_pct"],
                "colorscale": "Viridis",
                "showscale": True,
                "colorbar": {"title": "最大回撤%"},
            },
            text=sample["search_stage"],
            name="训练候选",
            hovertemplate="%{text}<br>CAGR %{x:.3f}%<br>Sharpe %{y:.3f}<extra></extra>",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=[selected["cagr_pct"]],
            y=[selected["sharpe"]],
            mode="markers+text",
            text=["联合高原代表"],
            textposition="top center",
            marker={"size": 13, "color": "#dc2626", "symbol": "diamond"},
            name="联合高原代表",
            hovertemplate="联合高原代表<br>CAGR %{x:.3f}%<br>Sharpe %{y:.3f}<extra></extra>",
        )
    )
    figure.update_layout(
        height=650,
        margin={"l": 65, "r": 80, "t": 45, "b": 55},
        xaxis_title="训练 CAGR（%）",
        yaxis_title="训练 Sharpe",
        showlegend=False,
    )
    return figure


def plateau_figure(summary: pd.DataFrame, selected_anchor_id: str) -> go.Figure:
    selected = summary[summary["anchor_id"] == selected_anchor_id]
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=summary["neighbor_cagr_q25_pct"],
            y=summary["neighbor_sharpe_q25"],
            mode="markers",
            marker={
                "size": 9,
                "color": summary["boundary_dimension_count"],
                "colorscale": "YlOrRd",
                "showscale": True,
                "colorbar": {"title": "边界维度"},
            },
            text=summary["anchor_id"],
            customdata=summary[["joint_plateau_score", "neighbor_count"]],
            name="64 个高分锚点",
            hovertemplate=(
                "%{text}<br>邻域 CAGR Q25 %{x:.3f}%<br>邻域 Sharpe Q25 %{y:.3f}"
                "<br>高原分数 %{customdata[0]:.3f}<br>邻域数 %{customdata[1]:.0f}<extra></extra>"
            ),
        )
    )
    figure.add_trace(
        go.Scatter(
            x=selected["neighbor_cagr_q25_pct"],
            y=selected["neighbor_sharpe_q25"],
            mode="markers+text",
            text=["选定"],
            textposition="top center",
            marker={"size": 15, "color": "#0f766e", "symbol": "diamond"},
            name="选定高原",
        )
    )
    figure.update_layout(
        height=620,
        margin={"l": 65, "r": 80, "t": 45, "b": 55},
        xaxis_title="512 组联合扰动 CAGR 下四分位（%）",
        yaxis_title="512 组联合扰动 Sharpe 下四分位",
        showlegend=False,
    )
    return figure


def parameter_band_figure(band: pd.DataFrame, selected: pd.Series) -> go.Figure:
    labels = {
        "A_negative_days_slow": "A",
        "B_slow_sma_window": "B",
        "E_fallback_sma_window": "E",
        "F_short_sma_center": "F 中心",
        "F_short_sma_spacing": "F 间隔",
        "G_short_recovery_below_pct": "G%",
        "H_reentry_sma_window": "H",
        "L_cost_stop_pct": "L%",
        "R_forced_rebuy_pct": "R%",
    }
    rows = band.set_index("parameter").loc[list(FREE_PARAMETER_COLUMNS)].reset_index()
    selected_values = [float(selected[name]) for name in FREE_PARAMETER_COLUMNS]
    figure = go.Figure(
        data=[
            go.Table(
                header={
                    "values": ["参数", "选定值", "近似高原 Min", "Q25", "Median", "Q75", "Max", "合格邻居"],
                    "fill_color": "#0f766e",
                    "font": {"color": "white"},
                    "align": "center",
                },
                cells={
                    "values": [
                        [labels[name] for name in rows["parameter"]],
                        selected_values,
                        rows["min"],
                        rows["q25"],
                        rows["median"],
                        rows["q75"],
                        rows["max"],
                        rows["qualified_case_count"],
                    ],
                    "fill_color": "#f8fafc",
                    "align": "center",
                    "format": [None, ".3f", ".3f", ".3f", ".3f", ".3f", ".3f", ".0f"],
                },
            )
        ]
    )
    figure.update_layout(height=520, margin={"l": 25, "r": 25, "t": 30, "b": 20})
    return figure


def markdown_report(summary: dict[str, Any], run_id: str) -> str:
    params = summary["selected_parameters"]
    train = summary["train_selected"]
    oos = summary["locked_oos"]
    return f"""# QQQ 无 C/D 卖出：训练高原与锁定样本外回测

- Run：`{run_id}`
- 选定参数：A={params['A']}、B={params['B']}、E={params['E']}、F={params['F_center']}±{params['F_spacing']}、G={params['G_pct']}%、H={params['H']}、L={params['L_pct']}%、R={params['R_pct']}%
- C/D 快速导数卖出：关闭；训练搜索 {summary['search_case_count']:,} 组，64 个锚点各联合扰动 512 组。

| 窗口 | 策略 CAGR | 持有 CAGR | 策略 Sharpe | 持有 Sharpe | 最大回撤 | 持有最大回撤 | 成交数 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 训练 2000-07-27～2015-12-31 | {train['cagr_pct']:.3f}% | {train['benchmark_cagr_pct']:.3f}% | {train['sharpe']:.3f} | {train['benchmark_sharpe']:.3f} | {train['max_drawdown_pct']:.2f}% | {train['benchmark_max_drawdown_pct']:.2f}% | {int(train['order_count'])} |
| 锁定样本外 2016-01-04～2026-08-04 | {oos['cagr_pct']:.3f}% | {oos['benchmark_cagr_pct']:.3f}% | {oos['sharpe']:.3f} | {oos['benchmark_sharpe']:.3f} | {oos['max_drawdown_pct']:.2f}% | {oos['benchmark_max_drawdown_pct']:.2f}% | {int(oos['order_count'])} |

## 边界

- 参数只根据训练窗口选择；样本外结果没有反向参与搜索或改参。
- 本轮为零费用、零滑点的首轮结构测试，尚未执行规则消融和成本压力测试。
- 报告不包含逐笔买卖表；订单原因保存在 K 线标记和机器账本。
"""


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
    plateau = pd.read_csv(block / "joint_plateau_summary.csv")
    band = pd.read_csv(block / "local_parameter_band.csv")
    daily = pd.read_csv(block / "daily.csv", parse_dates=["date"])
    benchmark = pd.read_csv(block / "buy_hold_daily.csv", parse_dates=["date"])
    orders = pd.read_csv(block / "orders.csv", parse_dates=["date", "signal_date"])
    summary = json.loads((block / "metrics.json").read_text(encoding="utf-8"))
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    train = formal[
        (formal["window_id"] == "TRAIN")
        & formal["selection_reason"].str.contains("JOINT_PLATEAU_REPRESENTATIVE")
    ].iloc[0]
    oos = formal[formal["window_id"] == "LOCKED_OOS"].iloc[0]
    analysis_summary = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": created_at,
        **summary,
        "formal_candidates": json_safe(formal.to_dict("records")),
        "plateau_top_anchors": json_safe(plateau.head(10).to_dict("records")),
        "local_parameter_band": json_safe(band.to_dict("records")),
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(analysis_summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    raw = pd.read_csv(WORKSPACE_ROOT / "data/processed/daily/QQQ.csv", parse_dates=["date"])
    selected_spec = spec_from_row(oos)
    prepared = prepare_intraday_sma_data(raw, selected_spec)
    test_start = pd.Timestamp(context.config["parameters"]["locked_test_start"])
    test_end = pd.Timestamp(context.config["parameters"]["locked_test_end"])
    prepared = prepared[(prepared["date"] >= test_start) & (prepared["date"] <= test_end)].reset_index(drop=True)
    oos_orders = orders[
        (orders["window_id"] == "LOCKED_OOS") & (orders["case_id"] == "LOCKED_OOS")
    ].copy()

    figures = [
        ReportFigure(
            "performance-qqq",
            "锁定样本外：策略与 QQQ 持有",
            performance_figure(
                daily,
                benchmark,
                window_id="LOCKED_OOS",
                case_id="LOCKED_OOS",
                title="2016–2026 锁定样本外净值",
            ),
            "performance",
        ),
        ReportFigure(
            "training-performance",
            "训练期高原代表与 QQQ 持有",
            performance_figure(
                daily,
                benchmark,
                window_id="TRAIN",
                case_id=str(train["case_id"]),
                title="2000–2015 训练期净值",
            ),
            "performance",
        ),
        ReportFigure(
            "training-search-cloud",
            "训练期 180,000 组全域与前沿搜索",
            search_cloud_figure(screening, train),
            "generic",
        ),
        ReportFigure(
            "joint-plateau",
            "64 个锚点的九维联合扰动下四分位",
            plateau_figure(plateau, str(summary["selected_anchor_id"])),
            "generic",
        ),
        ReportFigure(
            "parameter-band",
            "选定参数及其局部近似高原范围",
            parameter_band_figure(band, train),
            "generic",
        ),
        ReportFigure(
            "market-qqq",
            "锁定样本外价格、均线与成交原因",
            base.build_market_figure(
                prepared,
                oos_orders,
                short_windows=selected_spec.f_short_sma_windows,
            ),
            "market",
        ),
    ]
    params = summary["selected_parameters"]
    compact = (
        f"<p><strong>锁定样本外先展示：</strong>策略 CAGR <strong>{oos['cagr_pct']:.3f}%</strong>"
        f"（持有 {oos['benchmark_cagr_pct']:.3f}%），Sharpe <strong>{oos['sharpe']:.3f}</strong>"
        f"（持有 {oos['benchmark_sharpe']:.3f}），最大回撤 <strong>{oos['max_drawdown_pct']:.2f}%</strong>"
        f"（持有 {oos['benchmark_max_drawdown_pct']:.2f}%）。</p>"
        f"<p>训练期选定：A={params['A']}、B={params['B']}、E={params['E']}、"
        f"F={params['F_center']}±{params['F_spacing']}、G={params['G_pct']}%、"
        f"H={params['H']}、L={params['L_pct']}%、R={params['R_pct']}%；"
        f"C/D 卖出关闭。联合邻域 CAGR Q25={summary['selected_neighbor_cagr_q25_pct']:.3f}%，"
        f"Sharpe Q25={summary['selected_neighbor_sharpe_q25']:.3f}。</p>"
    )
    report = render_interactive_report(
        title="QQQ 无 C/D 卖出：训练高原与锁定样本外回测",
        heading="QQQ 无 C/D 卖出：训练高原与锁定样本外回测",
        subtitle="只用 1999–2015 输入历史选择九维联合高原；参数锁定后一次性评估 2016–2026。",
        summary_html=compact,
        notes=[
            "C/D 快速导数卖出在快速筛选、PyBroker 和独立账本中均明确关闭；C/D 兼容占位值不参与搜索。",
            "所有候选从共同评价起点的 100,000 美元现金开始；Buy & Hold 在同日 Open 买入，策略等待第一笔普通买入。",
            "训练搜索为 100,000 组全域抽样加 80,000 组前沿精搜；64 个锚点再各做 512 组九维联合扰动。",
            "本轮为零费用、零滑点的首轮训练/回测；规则消融和交易成本压力测试尚未执行。",
            "报告不生成逐笔交易表；成交原因只保留在 K 线标记 hover 与机器账本。",
        ],
        figures=figures,
        experiment=context.config,
        run_id=args.run_id,
        template_id=str(context.config["reporting"]["template_id"]),
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")
    (run_root / "report.md").write_text(
        markdown_report(summary, args.run_id), encoding="utf-8"
    )

    template_path = str(context.config["reporting"]["template_path"])
    tracked = [
        "backtest/requirements.lock",
        "backtest/quantkit/execution.py",
        "backtest/quantkit/experiment.py",
        "backtest/quantkit/intraday_sma.py",
        "backtest/quantkit/intraday_sma_search.py",
        "backtest/quantkit/intraday_sma_plateau.py",
        "backtest/quantkit/metrics.py",
        "backtest/quantkit/reporting.py",
        "backtest/scripts/run_intraday_sma_global_search.py",
        "backtest/scripts/run_intraday_sma_no_fast_drop_train_oos.py",
        "backtest/scripts/analyze_intraday_sma_no_fast_drop_train_oos.py",
        "backtest/scripts/smoke_report_ui.mjs",
        f"{template_path}/page.html",
        f"{template_path}/styles.css",
        f"{template_path}/interactions.js",
        "data/processed/manifest.json",
        "data/processed/daily/QQQ.csv",
    ]
    provenance: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": created_at,
        "software": {
            "python": platform.python_version(),
            "numba": __import__("numba").__version__,
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
        json.dumps(provenance, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (run_root / "README.md").write_text(
        f"""# Run {args.run_id}

This immutable run contains the QQQ no-C/D training search, joint plateau selection, and one locked out-of-sample evaluation.

- `report.html` / `report.md`: chart-first reports without a per-order table.
- `analysis/summary.json`: selected parameters, training metrics, locked OOS metrics and plateau summaries.
- `QQQ/cost_0bps/`: all search cases, joint neighbors, formal ledgers and source manifest.
- `provenance.json` / `validation.json`: source and correctness evidence.
""",
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


if __name__ == "__main__":
    main()
