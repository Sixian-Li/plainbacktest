#!/usr/bin/env python3
"""Analyze AAPL dual-SMA holding-period CAGR surfaces and build the report."""

from __future__ import annotations

import argparse
import html
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import plotly
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from quantkit.experiment import load_experiment, load_run, record_analysis_complete, sha256
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts.analyze_aapl_dual_sma_grid import (
    market_figure,
    performance_figure,
    stable_representative,
    surface_matrix,
)
from scripts.run_intraday_sma_backtest import json_safe, normalize_frame


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
PRIMARY_METRIC = "holding_period_cagr_pct"


def ordered_best(group: pd.DataFrame, metric: str = PRIMARY_METRIC) -> pd.Series:
    return group.sort_values(
        [metric, "fast_window", "slow_window"], ascending=[False, True, True]
    ).iloc[0]


def surface_summary(results: pd.DataFrame, benchmark: pd.DataFrame) -> pd.DataFrame:
    required = {
        PRIMARY_METRIC,
        "holding_sessions",
        "holding_time_pct",
        "total_return_pct",
        "cagr_pct",
        "sharpe",
        "max_drawdown_pct",
    }
    missing = required - set(results.columns)
    if missing:
        raise ValueError(f"Formal results are missing holding metrics: {sorted(missing)}")
    rows: list[dict[str, object]] = []
    benchmarks = benchmark.set_index("window_id")
    for window_id, group in results.groupby("window_id", sort=False):
        best = ordered_best(group)
        stable = stable_representative(group, PRIMARY_METRIC)
        stable_row = group[
            group["fast_window"].eq(stable["fast_window"])
            & group["slow_window"].eq(stable["slow_window"])
        ].iloc[0]
        threshold = float(group[PRIMARY_METRIC].quantile(0.9))
        benchmark_row = benchmarks.loc[window_id]
        rows.append(
            {
                "case_id": str(best["case_id"]),
                "window_id": window_id,
                "window_label": best["window_label"],
                "window_start": best["window_start"],
                "window_end": best["window_end"],
                "best_fast_window": int(best["fast_window"]),
                "best_slow_window": int(best["slow_window"]),
                "best_holding_period_cagr_pct": float(best[PRIMARY_METRIC]),
                "best_holding_sessions": int(best["holding_sessions"]),
                "best_holding_time_pct": float(best["holding_time_pct"]),
                "best_calendar_cagr_pct": float(best["cagr_pct"]),
                "best_total_return_pct": float(best["total_return_pct"]),
                "best_sharpe": float(best["sharpe"]),
                "best_max_drawdown_pct": float(best["max_drawdown_pct"]),
                "stable_fast_window": int(stable["fast_window"]),
                "stable_slow_window": int(stable["slow_window"]),
                "stable_holding_period_cagr_pct": float(stable["metric_value"]),
                "stable_holding_sessions": int(stable_row["holding_sessions"]),
                "stable_holding_time_pct": float(stable_row["holding_time_pct"]),
                "stable_calendar_cagr_pct": float(stable_row["cagr_pct"]),
                "stable_total_return_pct": float(stable_row["total_return_pct"]),
                "stable_neighborhood_median_pct": float(stable["neighborhood_median"]),
                "stable_neighborhood_std_pct": float(stable["neighborhood_std"]),
                "stable_score": float(stable["stability_score"]),
                "buy_hold_holding_period_cagr_pct": float(
                    benchmark_row["holding_period_cagr_pct"]
                ),
                "buy_hold_holding_sessions": int(benchmark_row["holding_sessions"]),
                "buy_hold_holding_time_pct": float(benchmark_row["holding_time_pct"]),
                "buy_hold_calendar_cagr_pct": float(benchmark_row["cagr_pct"]),
                "buy_hold_total_return_pct": float(benchmark_row["total_return_pct"]),
                "top_decile_threshold_pct": threshold,
                "pairs_above_buy_hold_holding_cagr": int(
                    (
                        group[PRIMARY_METRIC]
                        > float(benchmark_row["holding_period_cagr_pct"])
                    ).sum()
                ),
                "pairs_above_buy_hold_holding_cagr_pct": float(
                    (
                        group[PRIMARY_METRIC]
                        > float(benchmark_row["holding_period_cagr_pct"])
                    ).mean()
                    * 100.0
                ),
                "best_touches_grid_boundary": bool(
                    int(best["fast_window"]) in {1, 50}
                    or int(best["slow_window"]) in {15, 250}
                ),
                "total_return_pct": float(best["total_return_pct"]),
                "cagr_pct": float(best["cagr_pct"]),
                "sharpe": float(best["sharpe"]),
                "max_drawdown_pct": float(best["max_drawdown_pct"]),
                "exposure_pct": float(best["holding_time_pct"]),
            }
        )
    return pd.DataFrame(rows)


def cross_window_diagnostics(results: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, object]]:
    windows = list(results["window_id"].drop_duplicates())
    if len(windows) != 2:
        raise ValueError("This analysis requires exactly two windows")
    early = results[results["window_id"].eq(windows[0])]
    later = results[results["window_id"].eq(windows[1])]
    merged = early.merge(
        later,
        on=["fast_window", "slow_window"],
        suffixes=("_early", "_later"),
        validate="one_to_one",
    )
    early_metric = f"{PRIMARY_METRIC}_early"
    later_metric = f"{PRIMARY_METRIC}_later"
    early_threshold = float(merged[early_metric].quantile(0.9))
    later_threshold = float(merged[later_metric].quantile(0.9))
    early_top = set(
        map(
            tuple,
            merged.loc[
                merged[early_metric] >= early_threshold,
                ["fast_window", "slow_window"],
            ].to_numpy(int),
        )
    )
    later_top = set(
        map(
            tuple,
            merged.loc[
                merged[later_metric] >= later_threshold,
                ["fast_window", "slow_window"],
            ].to_numpy(int),
        )
    )
    merged["holding_cagr_change_pct_points"] = merged[later_metric] - merged[early_metric]
    union = early_top | later_top
    summary: dict[str, object] = {
        "early_window_id": windows[0],
        "later_window_id": windows[1],
        "pair_count": int(len(merged)),
        "pearson_holding_period_cagr": float(merged[early_metric].corr(merged[later_metric])),
        "spearman_holding_period_cagr": float(
            merged[early_metric].rank().corr(merged[later_metric].rank())
        ),
        "top_decile_intersection_count": len(early_top & later_top),
        "top_decile_union_count": len(union),
        "top_decile_jaccard": len(early_top & later_top) / len(union),
        "median_5bps_holding_cagr_penalty_pct_points": float(
            np.median(
                merged["zero_cost_holding_period_cagr_pct_early"]
                - merged[early_metric]
            )
        ),
        "surface_0bps_5bps_correlation_early": float(
            merged["zero_cost_holding_period_cagr_pct_early"].corr(merged[early_metric])
        ),
        "surface_0bps_5bps_correlation_later": float(
            merged["zero_cost_holding_period_cagr_pct_later"].corr(merged[later_metric])
        ),
    }
    return merged, summary


def holding_cagr_heatmap(
    group: pd.DataFrame,
    summary: pd.Series,
    *,
    fast_values: list[int],
    slow_values: list[int],
    zmin: float,
    zmax: float,
) -> go.Figure:
    matrix = surface_matrix(group, PRIMARY_METRIC, fast_values=fast_values, slow_values=slow_values)
    exposure = surface_matrix(
        group, "holding_time_pct", fast_values=fast_values, slow_values=slow_values
    )
    held = surface_matrix(
        group, "holding_sessions", fast_values=fast_values, slow_values=slow_values
    )
    calendar_cagr = surface_matrix(
        group, "cagr_pct", fast_values=fast_values, slow_values=slow_values
    )
    total_return = surface_matrix(
        group, "total_return_pct", fast_values=fast_values, slow_values=slow_values
    )
    custom = np.stack([exposure, held, calendar_cagr, total_return], axis=-1)
    fig = go.Figure(
        go.Heatmap(
            x=slow_values,
            y=fast_values,
            z=matrix,
            customdata=custom,
            colorscale="RdYlGn",
            zmin=zmin,
            zmax=zmax,
            colorbar={"title": "持仓CAGR %"},
            hovertemplate=(
                "慢线 SMA%{x}<br>快线 SMA%{y}<br>持仓CAGR %{z:.2f}%"
                "<br>持仓率 %{customdata[0]:.2f}%<br>持仓日 %{customdata[1]:.0f}"
                "<br>日历CAGR %{customdata[2]:.2f}%<br>累计收益 %{customdata[3]:.2f}%"
                "<extra></extra>"
            ),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=[summary["best_slow_window"]],
            y=[summary["best_fast_window"]],
            mode="markers",
            name="机械最高",
            marker={"symbol": "x", "size": 12, "color": "black", "line": {"width": 2}},
            hovertemplate="机械最高：快%{y} / 慢%{x}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=[summary["stable_slow_window"]],
            y=[summary["stable_fast_window"]],
            mode="markers",
            name="3×3稳定代表",
            marker={
                "symbol": "circle-open",
                "size": 13,
                "color": "#111827",
                "line": {"width": 2},
            },
            hovertemplate="稳定代表：快%{y} / 慢%{x}<extra></extra>",
        )
    )
    fig.update_layout(
        template="plotly_white",
        height=650,
        xaxis={"title": "慢线窗口（日）", "range": [14.5, 250.5]},
        yaxis={"title": "快线窗口（日）", "range": [0.5, 50.5]},
        legend={"orientation": "h", "y": -0.13},
        margin={"b": 110},
    )
    return fig


def exposure_figure(
    results: pd.DataFrame,
    summaries: pd.DataFrame,
    *,
    fast_values: list[int],
    slow_values: list[int],
) -> go.Figure:
    fig = make_subplots(
        rows=1,
        cols=2,
        subplot_titles=tuple(f"{label} 持仓率" for label in summaries["window_label"]),
        horizontal_spacing=0.08,
    )
    for column, row in enumerate(summaries.itertuples(), start=1):
        group = results[results["window_id"].eq(row.window_id)]
        matrix = surface_matrix(
            group, "holding_time_pct", fast_values=fast_values, slow_values=slow_values
        )
        fig.add_trace(
            go.Heatmap(
                x=slow_values,
                y=fast_values,
                z=matrix,
                colorscale="Blues",
                zmin=0,
                zmax=100,
                colorbar={"title": "持仓率 %", "x": 1.02} if column == 2 else None,
                showscale=column == 2,
                hovertemplate=(
                    "慢线 SMA%{x}<br>快线 SMA%{y}<br>持仓率 %{z:.2f}%<extra></extra>"
                ),
            ),
            row=1,
            col=column,
        )
        fig.update_xaxes(title_text="慢线窗口（日）", row=1, col=column)
        fig.update_yaxes(title_text="快线窗口（日）", row=1, col=column)
    fig.update_layout(template="plotly_white", height=520)
    return fig


def difference_figure(
    merged: pd.DataFrame,
    *,
    fast_values: list[int],
    slow_values: list[int],
) -> go.Figure:
    matrix = surface_matrix(
        merged,
        "holding_cagr_change_pct_points",
        fast_values=fast_values,
        slow_values=slow_values,
    )
    limit = float(np.nanmax(np.abs(matrix)))
    fig = go.Figure(
        go.Heatmap(
            x=slow_values,
            y=fast_values,
            z=matrix,
            colorscale="RdBu",
            reversescale=True,
            zmin=-limit,
            zmax=limit,
            zmid=0,
            colorbar={"title": "后期-前期<br>百分点"},
            hovertemplate=(
                "慢线 SMA%{x}<br>快线 SMA%{y}<br>持仓CAGR变化 %{z:+.2f}pp"
                "<extra></extra>"
            ),
        )
    )
    fig.update_layout(
        template="plotly_white",
        height=650,
        xaxis_title="慢线窗口（日）",
        yaxis_title="快线窗口（日）",
    )
    return fig


def cross_window_scatter(merged: pd.DataFrame) -> go.Figure:
    custom = np.column_stack(
        [
            merged["fast_window"],
            merged["slow_window"],
            merged["holding_cagr_change_pct_points"],
            merged["holding_time_pct_early"],
            merged["holding_time_pct_later"],
        ]
    )
    fig = go.Figure(
        go.Scattergl(
            x=merged[f"{PRIMARY_METRIC}_early"],
            y=merged[f"{PRIMARY_METRIC}_later"],
            mode="markers",
            marker={
                "size": 5,
                "opacity": 0.55,
                "color": merged["slow_window"],
                "colorscale": "Viridis",
                "colorbar": {"title": "慢线日数"},
            },
            customdata=custom,
            hovertemplate=(
                "快线 %{customdata[0]:.0f} / 慢线 %{customdata[1]:.0f}"
                "<br>前期持仓CAGR %{x:.2f}% · 持仓率 %{customdata[3]:.2f}%"
                "<br>后期持仓CAGR %{y:.2f}% · 持仓率 %{customdata[4]:.2f}%"
                "<br>变化 %{customdata[2]:+.2f}pp<extra></extra>"
            ),
        )
    )
    fig.add_hline(y=0, line={"color": "#94a3b8", "dash": "dot"})
    fig.add_vline(x=0, line={"color": "#94a3b8", "dash": "dot"})
    fig.update_layout(
        template="plotly_white",
        height=650,
        xaxis_title="2013–2018 持仓CAGR (%)",
        yaxis_title="2018–2023 持仓CAGR (%)",
    )
    return fig


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    if context.config["parameters"].get("primary_surface_metric") != PRIMARY_METRIC:
        raise ValueError("This analyzer requires a holding-period CAGR experiment")
    record = load_run(context, args.run_id)
    if any(block["status"] != "completed" for block in record["expected_blocks"]):
        raise RuntimeError("Run blocks are incomplete")
    run_root = context.run_root(args.run_id)
    block = run_root / "AAPL/cost_5bps"
    results = pd.read_csv(block / "parameter_results.csv")
    benchmark = pd.read_csv(block / "buy_hold_results.csv")
    summaries = surface_summary(results, benchmark)
    merged, cross_summary = cross_window_diagnostics(results)
    analysis = run_root / "analysis"
    analysis.mkdir(parents=True, exist_ok=True)
    normalize_frame(results).to_csv(
        analysis / "formal_results.csv", index=False, lineterminator="\n"
    )
    normalize_frame(summaries).to_csv(
        analysis / "surface_summary.csv", index=False, lineterminator="\n"
    )
    normalize_frame(merged).to_csv(
        analysis / "cross_window_pairs.csv", index=False, lineterminator="\n"
    )
    (analysis / "cross_window_summary.json").write_text(
        json.dumps(json_safe(cross_summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    fast_values = list(range(1, 51))
    slow_values = list(range(15, 251))
    zmin = float(results[PRIMARY_METRIC].min())
    zmax = float(results[PRIMARY_METRIC].max())
    table_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(row.window_label))}</td>"
        f"<td>{html.escape(str(row.window_start))}—{html.escape(str(row.window_end))}</td>"
        f"<td>{row.best_fast_window}/{row.best_slow_window}</td>"
        f"<td>{row.best_holding_period_cagr_pct:.2f}%</td>"
        f"<td>{row.best_holding_sessions}日 / {row.best_holding_time_pct:.2f}%</td>"
        f"<td>{row.best_calendar_cagr_pct:.2f}% / {row.best_total_return_pct:.2f}%</td>"
        f"<td>{row.stable_fast_window}/{row.stable_slow_window}</td>"
        f"<td>{row.stable_holding_period_cagr_pct:.2f}%</td>"
        f"<td>{row.stable_holding_sessions}日 / {row.stable_holding_time_pct:.2f}%</td>"
        f"<td>{row.buy_hold_holding_period_cagr_pct:.2f}%</td>"
        "</tr>"
        for row in summaries.itertuples()
    )
    early, later = summaries.iloc[0], summaries.iloc[1]
    summary_html = (
        "<h2>两个持仓CAGR网格的直接比较</h2>"
        "<p><strong>持仓CAGR</strong>把净账户期末收益只按实际有AAPL仓位的交易日年化。"
        "空仓日被压缩但成本仍保留，因此必须同时看持仓日、持仓率、日历CAGR和累计收益。</p>"
        "<table><thead><tr><th>窗口</th><th>实际交易日</th><th>机械最高 快/慢</th>"
        "<th>最高持仓CAGR</th><th>持仓日 / 持仓率</th><th>日历CAGR / 累计收益</th>"
        "<th>3×3稳定代表</th><th>代表持仓CAGR</th><th>代表持仓日 / 持仓率</th>"
        "<th>AAPL持有的持仓CAGR</th></tr></thead><tbody>"
        + table_rows
        + "</tbody></table>"
        f"<p>两期全部 {int(cross_summary['pair_count']):,} 个合法参数对的持仓CAGR排名 Spearman 相关为 "
        f"<strong>{float(cross_summary['spearman_holding_period_cagr']):.3f}</strong>；"
        "前10%参数集合的 Jaccard 重合度为 "
        f"<strong>{float(cross_summary['top_decile_jaccard']):.3f}</strong>。</p>"
        f"<p>前期机械最高为 SMA{int(early['best_fast_window'])}/SMA{int(early['best_slow_window'])}，"
        f"后期机械最高为 SMA{int(later['best_fast_window'])}/SMA{int(later['best_slow_window'])}。"
        "这些都是描述性样本内观察点，低持仓率会放大该指标，不代表未来最优参数。</p>"
    )
    raw = pd.read_csv(
        WORKSPACE_ROOT / "data/processed/daily/equities/AAPL.csv", parse_dates=["date"]
    )
    figures = [
        ReportFigure(
            "market-aapl",
            "AAPL价格与两个窗口的持仓CAGR稳定代表均线",
            market_figure(raw, summaries),
            "market",
        ),
        ReportFigure(
            "holding-cagr-grid-2013-2018",
            "2013–2018 · 快线1–50 × 慢线15–250 · 持仓CAGR",
            holding_cagr_heatmap(
                results[results["window_id"].eq(early["window_id"])],
                early,
                fast_values=fast_values,
                slow_values=slow_values,
                zmin=zmin,
                zmax=zmax,
            ),
            "analysis",
        ),
        ReportFigure(
            "holding-cagr-grid-2018-2023",
            "2018–2023 · 快线1–50 × 慢线15–250 · 持仓CAGR",
            holding_cagr_heatmap(
                results[results["window_id"].eq(later["window_id"])],
                later,
                fast_values=fast_values,
                slow_values=slow_values,
                zmin=zmin,
                zmax=zmax,
            ),
            "analysis",
        ),
        ReportFigure(
            "holding-exposure-grids",
            "同一参数面的持仓率",
            exposure_figure(
                results,
                summaries,
                fast_values=fast_values,
                slow_values=slow_values,
            ),
            "analysis",
        ),
        ReportFigure(
            "holding-cagr-grid-difference",
            "持仓CAGR变化热力图 · 2018–2023 减 2013–2018",
            difference_figure(merged, fast_values=fast_values, slow_values=slow_values),
            "analysis",
        ),
        ReportFigure(
            "holding-cagr-cross-window-scatter",
            "同一参数对在两个窗口的持仓CAGR",
            cross_window_scatter(merged),
            "analysis",
        ),
        ReportFigure(
            "performance-aapl",
            "2018–2023：持仓CAGR代表参数与AAPL持有净值",
            performance_figure(block, results, summaries),
            "performance",
        ),
    ]
    report = render_interactive_report(
        title="AAPL 双均线状态策略 · 两个五年持仓CAGR网格",
        heading="快线1–50日 × 慢线15–250日 · 步长1 · 持仓日按252日年化",
        subtitle="收盘确认 · 下一交易日开盘成交 · 单边5 bps · 同时展示持仓率与日历收益",
        summary_html=summary_html,
        notes=[
            "持仓CAGR = (期末净值 / 10万美元)^(252 / 持仓交易日) - 1；买入成交日计入，卖出成交日不计入。",
            "它假设空仓现金收益为零并压缩空仓日，不是逐笔交易收益复合、子区间IRR或另一条回测；低持仓率可能机械性放大数值。",
            "2013–2018表示[2013-01-01, 2018-01-01)，实际交易日为2013-01-02至2017-12-29；后一个窗口同理且不与前窗重叠。",
            "快线必须严格短于慢线；矩形中的快线不短于慢线区域遮罩，不参与排名或稳定性计算。",
            "AAPL复权OHLC近似股息再投资，但不是原始成交价加现金分红和拆股事件的精确公司行动账本。",
        ],
        figures=figures,
        experiment=context.config,
        run_id=args.run_id,
        template_id="interactive_research_v5",
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")
    (run_root / "report_print.html").write_text(report, encoding="utf-8")
    (run_root / "report.md").write_text(
        "# AAPL双均线两窗口持仓CAGR网格\n\n"
        "- 持仓CAGR按实际有AAPL仓位的交易日折算，必须与持仓率和日历收益一起看。\n"
        f"- 2013–2018机械最高：快{int(early['best_fast_window'])}/慢{int(early['best_slow_window'])}，"
        f"持仓CAGR {early['best_holding_period_cagr_pct']:.2f}%，持仓率 {early['best_holding_time_pct']:.2f}%，"
        f"日历CAGR {early['best_calendar_cagr_pct']:.2f}%。\n"
        f"- 2018–2023机械最高：快{int(later['best_fast_window'])}/慢{int(later['best_slow_window'])}，"
        f"持仓CAGR {later['best_holding_period_cagr_pct']:.2f}%，持仓率 {later['best_holding_time_pct']:.2f}%，"
        f"日历CAGR {later['best_calendar_cagr_pct']:.2f}%。\n"
        f"- 两窗口持仓CAGR排名Spearman相关：{float(cross_summary['spearman_holding_period_cagr']):.3f}；"
        f"前10%集合Jaccard：{float(cross_summary['top_decile_jaccard']):.3f}。\n"
        "- 结果属于全样本资金效率诊断，不命名可部署冠军。\n",
        encoding="utf-8",
    )
    summary = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "metric_definition": context.config["parameters"]["holding_period_cagr"],
        "case_count": int(len(results)),
        "valid_pair_count_per_window": int(len(results) / 2),
        "surface_summary": [json_safe(row) for row in summaries.to_dict("records")],
        "cross_window": json_safe(cross_summary),
    }
    (analysis / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    provenance = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "software": {"python": platform.python_version(), "plotly": plotly.__version__},
        "source_files": {},
    }
    for relative in (
        "backtest/quantkit/dual_sma_state.py",
        "backtest/quantkit/metrics.py",
        "backtest/scripts/run_aapl_dual_sma_grid.py",
        "backtest/scripts/analyze_aapl_dual_sma_grid.py",
        "backtest/scripts/analyze_aapl_dual_sma_holding_cagr_grid.py",
        "backtest/quantkit/reporting.py",
        "data/processed/daily/equities/AAPL.csv",
        "data/sp500_history_registry.json",
        "data/processed/universes/sp500/manifest.json",
        "data/processed/universes/sp500/security_master.csv",
    ):
        path = WORKSPACE_ROOT / relative
        provenance["source_files"][relative] = {
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
    (run_root / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (run_root / "README.md").write_text(
        "# AAPL dual-SMA holding-CAGR grid run\n\n"
        "See report.html, report.pdf, and analysis outputs.\n",
        encoding="utf-8",
    )
    subprocess.run(
        [
            "node",
            "scripts/print_html_pdf.mjs",
            str(run_root / "report_print.html"),
            str(run_root / "report.pdf"),
            "什么时候买",
            "什么时候卖",
            "信号如何变成成交",
            "两个持仓CAGR网格",
        ],
        cwd=BACKTEST_ROOT,
        check=True,
    )
    artifacts = {"schema_version": 1, "artifacts": {}}
    for path in sorted(run_root.rglob("*")):
        if path.is_file() and path.name not in {
            "artifact_manifest.json",
            "run.json",
            "validation.json",
        }:
            artifacts["artifacts"][str(path.relative_to(run_root))] = {
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
    (run_root / "artifact_manifest.json").write_text(
        json.dumps(artifacts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if record.get("status") == "running":
        record_analysis_complete(context, args.run_id)
    print(run_root / "report.html")


if __name__ == "__main__":
    main()
