#!/usr/bin/env python3
"""Analyze the two AAPL dual-SMA total-return surfaces and build the report."""

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
from scripts.run_intraday_sma_backtest import json_safe, normalize_frame


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent


def ordered_best(group: pd.DataFrame, metric: str) -> pd.Series:
    return group.sort_values(
        [metric, "fast_window", "slow_window"], ascending=[False, True, True]
    ).iloc[0]


def stable_representative(group: pd.DataFrame, metric: str) -> dict[str, float | int]:
    lookup = group.set_index(["fast_window", "slow_window"])[metric]
    candidates: list[dict[str, float | int]] = []
    for row in group.itertuples():
        keys = [
            (fast, slow)
            for fast in range(int(row.fast_window) - 1, int(row.fast_window) + 2)
            for slow in range(int(row.slow_window) - 1, int(row.slow_window) + 2)
        ]
        if not all(key in lookup.index for key in keys):
            continue
        values = np.asarray([lookup.loc[key] for key in keys], dtype=float)
        candidates.append(
            {
                "fast_window": int(row.fast_window),
                "slow_window": int(row.slow_window),
                "metric_value": float(getattr(row, metric)),
                "neighborhood_median": float(np.median(values)),
                "neighborhood_std": float(np.std(values, ddof=0)),
                "stability_score": float(np.median(values) - 0.5 * np.std(values, ddof=0)),
            }
        )
    if not candidates:
        raise ValueError("No dual-SMA pair has a complete 3x3 neighborhood")
    return sorted(
        candidates,
        key=lambda item: (
            -float(item["stability_score"]),
            int(item["fast_window"]),
            int(item["slow_window"]),
        ),
    )[0]


def surface_summary(results: pd.DataFrame, benchmark: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    benchmarks = benchmark.set_index("window_id")
    for window_id, group in results.groupby("window_id", sort=False):
        best = ordered_best(group, "total_return_pct")
        stable = stable_representative(group, "total_return_pct")
        threshold = float(group["total_return_pct"].quantile(0.9))
        benchmark_row = benchmarks.loc[window_id]
        rows.append(
            {
                "window_id": window_id,
                "window_label": best["window_label"],
                "window_start": best["window_start"],
                "window_end": best["window_end"],
                "best_fast_window": int(best["fast_window"]),
                "best_slow_window": int(best["slow_window"]),
                "best_total_return_pct": float(best["total_return_pct"]),
                "best_cagr_pct": float(best["cagr_pct"]),
                "best_sharpe": float(best["sharpe"]),
                "best_max_drawdown_pct": float(best["max_drawdown_pct"]),
                "stable_fast_window": int(stable["fast_window"]),
                "stable_slow_window": int(stable["slow_window"]),
                "stable_total_return_pct": float(stable["metric_value"]),
                "stable_neighborhood_median_pct": float(stable["neighborhood_median"]),
                "stable_neighborhood_std_pct": float(stable["neighborhood_std"]),
                "stable_score": float(stable["stability_score"]),
                "buy_hold_total_return_pct": float(benchmark_row["total_return_pct"]),
                "buy_hold_cagr_pct": float(benchmark_row["cagr_pct"]),
                "buy_hold_sharpe": float(benchmark_row["sharpe"]),
                "buy_hold_max_drawdown_pct": float(benchmark_row["max_drawdown_pct"]),
                "top_decile_threshold_pct": threshold,
                "pairs_beating_buy_hold": int(
                    (group["total_return_pct"] > float(benchmark_row["total_return_pct"])).sum()
                ),
                "pairs_beating_buy_hold_pct": float(
                    (group["total_return_pct"] > float(benchmark_row["total_return_pct"])).mean()
                    * 100.0
                ),
                "best_touches_grid_boundary": bool(
                    int(best["fast_window"]) in {1, 50}
                    or int(best["slow_window"]) in {15, 250}
                ),
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
    early_threshold = float(merged["total_return_pct_early"].quantile(0.9))
    later_threshold = float(merged["total_return_pct_later"].quantile(0.9))
    early_top = set(
        map(
            tuple,
            merged.loc[
                merged["total_return_pct_early"] >= early_threshold,
                ["fast_window", "slow_window"],
            ].to_numpy(int),
        )
    )
    later_top = set(
        map(
            tuple,
            merged.loc[
                merged["total_return_pct_later"] >= later_threshold,
                ["fast_window", "slow_window"],
            ].to_numpy(int),
        )
    )
    merged["return_change_pct_points"] = (
        merged["total_return_pct_later"] - merged["total_return_pct_early"]
    )
    summary: dict[str, object] = {
        "early_window_id": windows[0],
        "later_window_id": windows[1],
        "pair_count": int(len(merged)),
        "pearson_total_return": float(
            merged["total_return_pct_early"].corr(merged["total_return_pct_later"])
        ),
        "spearman_total_return": float(
            merged["total_return_pct_early"].rank().corr(
                merged["total_return_pct_later"].rank()
            )
        ),
        "top_decile_intersection_count": len(early_top & later_top),
        "top_decile_union_count": len(early_top | later_top),
        "top_decile_jaccard": len(early_top & later_top) / len(early_top | later_top),
        "median_5bps_cost_penalty_pct_points": float(
            np.median(merged["zero_cost_total_return_pct_early"] - merged["total_return_pct_early"])
        ),
        "surface_0bps_5bps_correlation_early": float(
            merged["zero_cost_total_return_pct_early"].corr(merged["total_return_pct_early"])
        ),
        "surface_0bps_5bps_correlation_later": float(
            merged["zero_cost_total_return_pct_later"].corr(merged["total_return_pct_later"])
        ),
    }
    return merged, summary


def surface_matrix(
    frame: pd.DataFrame,
    value: str,
    *,
    fast_values: list[int],
    slow_values: list[int],
) -> np.ndarray:
    pivot = frame.pivot(index="fast_window", columns="slow_window", values=value)
    return pivot.reindex(index=fast_values, columns=slow_values).to_numpy(float)


def heatmap_figure(
    group: pd.DataFrame,
    summary: pd.Series,
    *,
    metric: str,
    fast_values: list[int],
    slow_values: list[int],
    zmin: float,
    zmax: float,
) -> go.Figure:
    matrix = surface_matrix(group, metric, fast_values=fast_values, slow_values=slow_values)
    fig = go.Figure(
        go.Heatmap(
            x=slow_values,
            y=fast_values,
            z=matrix,
            colorscale="RdYlGn",
            zmin=zmin,
            zmax=zmax,
            colorbar={"title": "累计收益 %"},
            hovertemplate="慢线 SMA%{x}<br>快线 SMA%{y}<br>累计收益 %{z:.2f}%<extra></extra>",
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
            marker={"symbol": "circle-open", "size": 13, "color": "#111827", "line": {"width": 2}},
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


def difference_figure(
    merged: pd.DataFrame,
    *,
    fast_values: list[int],
    slow_values: list[int],
) -> go.Figure:
    matrix = surface_matrix(
        merged,
        "return_change_pct_points",
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
            hovertemplate="慢线 SMA%{x}<br>快线 SMA%{y}<br>收益变化 %{z:+.2f}pp<extra></extra>",
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
        [merged["fast_window"], merged["slow_window"], merged["return_change_pct_points"]]
    )
    fig = go.Figure(
        go.Scattergl(
            x=merged["total_return_pct_early"],
            y=merged["total_return_pct_later"],
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
                "<br>2013–2018 %{x:.2f}%<br>2018–2023 %{y:.2f}%"
                "<br>变化 %{customdata[2]:+.2f}pp<extra></extra>"
            ),
        )
    )
    fig.add_hline(y=0, line={"color": "#94a3b8", "dash": "dot"})
    fig.add_vline(x=0, line={"color": "#94a3b8", "dash": "dot"})
    fig.update_layout(
        template="plotly_white",
        height=650,
        xaxis_title="2013–2018 累计收益 (%)",
        yaxis_title="2018–2023 累计收益 (%)",
    )
    return fig


def market_figure(raw: pd.DataFrame, summaries: pd.DataFrame) -> go.Figure:
    market = raw[raw["date"].between("2012-01-01", "2023-01-01")].copy()
    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=market["date"],
            open=market["open"],
            high=market["high"],
            low=market["low"],
            close=market["close"],
            name="AAPL",
            meta={"panel": "price", "label": "AAPL K线"},
        )
    )
    colors = ["#2563eb", "#60a5fa", "#dc2626", "#fb7185"]
    series: list[tuple[str, int, str]] = []
    for row in summaries.itertuples():
        series.extend(
            [
                (f"{row.window_id}_fast", int(row.stable_fast_window), f"{row.window_label} 稳定快线"),
                (f"{row.window_id}_slow", int(row.stable_slow_window), f"{row.window_label} 稳定慢线"),
            ]
        )
    full = raw.copy()
    for color, (key, window, label) in zip(colors, series):
        values = full["close"].rolling(window, min_periods=window).mean()
        shown = values[full["date"].between("2012-01-01", "2023-01-01")]
        fig.add_trace(
            go.Scatter(
                x=market["date"],
                y=shown,
                mode="lines",
                name=f"{label} · SMA{window}",
                line={"color": color, "width": 1.4},
                visible="legendonly",
                meta={
                    "series_key": key,
                    "panel": "market",
                    "label": f"{label} · SMA{window}",
                    "control_group": "dual_sma",
                    "control_group_label": "显示双窗口稳定代表均线",
                },
            )
        )
    fig.update_layout(
        template="plotly_white",
        height=690,
        hovermode="x unified",
        dragmode="pan",
        xaxis_rangeslider_visible=False,
        yaxis_title="复权价格（美元）",
        legend={"orientation": "h", "y": -0.12},
        margin={"b": 110},
    )
    return fig


def load_case_daily(block: Path, case_id: str) -> pd.DataFrame:
    index = pd.read_csv(block / "daily_state_index.csv")
    row = index[index["case_id"].eq(case_id)]
    if len(row) != 1:
        raise ValueError(f"Daily state index missing {case_id}")
    item = row.iloc[0]
    dates = pd.read_csv(block / "window_dates.csv")
    dates = dates[dates["window_id"].eq(item["window_id"])].sort_values("bar_index")
    bars = int(item["bars"])
    state = np.load(block / "daily_signal_state.npz")
    case_index = int(item["case_index"])
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates["date"].iloc[:bars].to_numpy()),
            "cash": state["cash"][case_index, :bars].astype(float),
            "shares": state["shares"][case_index, :bars].astype(float),
            "equity": state["equity"][case_index, :bars].astype(float),
            "signal": state["signal"][case_index, :bars].astype(int),
        }
    )


def performance_figure(
    block: Path,
    results: pd.DataFrame,
    summaries: pd.DataFrame,
) -> go.Figure:
    later_summary = summaries.iloc[1]
    later = results[results["window_id"].eq(later_summary["window_id"])]
    early_summary = summaries.iloc[0]
    selections = [
        (
            "later_best",
            f"后期机械最高 · {int(later_summary['best_fast_window'])}/{int(later_summary['best_slow_window'])}",
            int(later_summary["best_fast_window"]),
            int(later_summary["best_slow_window"]),
        ),
        (
            "later_stable",
            f"后期稳定代表 · {int(later_summary['stable_fast_window'])}/{int(later_summary['stable_slow_window'])}",
            int(later_summary["stable_fast_window"]),
            int(later_summary["stable_slow_window"]),
        ),
        (
            "early_best_reused",
            f"前期最高移植 · {int(early_summary['best_fast_window'])}/{int(early_summary['best_slow_window'])}",
            int(early_summary["best_fast_window"]),
            int(early_summary["best_slow_window"]),
        ),
    ]
    benchmark = pd.read_csv(block / "buy_hold_daily.csv", parse_dates=["date"])
    benchmark = benchmark[benchmark["window_id"].eq(later_summary["window_id"])]
    series: list[tuple[str, str, pd.DataFrame, bool]] = [
        ("buy_hold", "AAPL Buy & Hold", benchmark[["date", "equity"]], True)
    ]
    seen: set[tuple[int, int]] = set()
    for key, label, fast, slow in selections:
        if (fast, slow) in seen:
            continue
        seen.add((fast, slow))
        match = later[
            later["fast_window"].eq(fast) & later["slow_window"].eq(slow)
        ]
        case_id = str(match.iloc[0]["case_id"])
        series.append((key, label, load_case_daily(block, case_id)[["date", "equity"]], False))
    colors = ["#334155", "#2563eb", "#16a34a", "#dc2626"]
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.7, 0.3],
        vertical_spacing=0.07,
        subplot_titles=("2018–2023账户净值", "回撤"),
    )
    for color, (key, label, daily, benchmark_flag) in zip(colors, series):
        values = daily["equity"].to_numpy(float)
        drawdown = values / np.maximum.accumulate(values) - 1.0
        fig.add_trace(
            go.Scatter(
                x=daily["date"],
                y=values,
                mode="lines",
                name=label,
                line={"color": color, "width": 1.8},
                meta={
                    "series_key": key,
                    "panel": "equity",
                    "label": label,
                    "is_benchmark": benchmark_flag,
                    "cost_bps": 5,
                },
            ),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=daily["date"],
                y=drawdown * 100.0,
                mode="lines",
                showlegend=False,
                line={"color": color, "width": 1.1},
                meta={"series_key": key, "panel": "drawdown", "label": label},
            ),
            row=2,
            col=1,
        )
    fig.update_layout(template="plotly_white", height=700, hovermode="x unified", dragmode="pan")
    fig.update_yaxes(title_text="美元", row=1, col=1)
    fig.update_yaxes(title_text="%", row=2, col=1)
    return fig


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
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
    zmin = float(results["total_return_pct"].min())
    zmax = float(results["total_return_pct"].max())
    table_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(row.window_label))}</td>"
        f"<td>{html.escape(str(row.window_start))}—{html.escape(str(row.window_end))}</td>"
        f"<td>{row.best_fast_window}/{row.best_slow_window}</td>"
        f"<td>{row.best_total_return_pct:.2f}%</td>"
        f"<td>{row.stable_fast_window}/{row.stable_slow_window}</td>"
        f"<td>{row.stable_total_return_pct:.2f}%</td>"
        f"<td>{row.buy_hold_total_return_pct:.2f}%</td>"
        f"<td>{row.pairs_beating_buy_hold}/{len(results[results['window_id'].eq(row.window_id)])}</td>"
        "</tr>"
        for row in summaries.itertuples()
    )
    early, later = summaries.iloc[0], summaries.iloc[1]
    summary_html = (
        "<h2>两个收益网格的直接比较</h2>"
        "<table><thead><tr><th>窗口</th><th>实际交易日</th><th>机械最高 快/慢</th>"
        "<th>机械最高收益</th><th>3×3稳定代表 快/慢</th><th>稳定代表收益</th>"
        "<th>AAPL持有收益</th><th>超过持有的组合</th></tr></thead><tbody>"
        + table_rows
        + "</tbody></table>"
        f"<p>两期全部 {int(cross_summary['pair_count']):,} 个合法参数对的收益排名 Spearman 相关为 "
        f"<strong>{float(cross_summary['spearman_total_return']):.3f}</strong>；前10%参数集合的 Jaccard 重合度为 "
        f"<strong>{float(cross_summary['top_decile_jaccard']):.3f}</strong>。这两个数字直接衡量两个网格的形状是否稳定。</p>"
        f"<p>前期机械最高为 SMA{int(early['best_fast_window'])}/SMA{int(early['best_slow_window'])}，"
        f"后期机械最高为 SMA{int(later['best_fast_window'])}/SMA{int(later['best_slow_window'])}。"
        "它们都是样本内观察点，不代表未来最优参数。</p>"
    )
    raw = pd.read_csv(
        WORKSPACE_ROOT / "data/processed/daily/equities/AAPL.csv", parse_dates=["date"]
    )
    figures = [
        ReportFigure(
            "market-aapl",
            "AAPL价格与两个窗口的稳定代表均线",
            market_figure(raw, summaries),
            "market",
        ),
        ReportFigure(
            "grid-2013-2018",
            "2013–2018 · 快线1–50 × 慢线15–250 · 累计收益",
            heatmap_figure(
                results[results["window_id"].eq(early["window_id"])],
                early,
                metric="total_return_pct",
                fast_values=fast_values,
                slow_values=slow_values,
                zmin=zmin,
                zmax=zmax,
            ),
            "analysis",
        ),
        ReportFigure(
            "grid-2018-2023",
            "2018–2023 · 快线1–50 × 慢线15–250 · 累计收益",
            heatmap_figure(
                results[results["window_id"].eq(later["window_id"])],
                later,
                metric="total_return_pct",
                fast_values=fast_values,
                slow_values=slow_values,
                zmin=zmin,
                zmax=zmax,
            ),
            "analysis",
        ),
        ReportFigure(
            "grid-difference",
            "收益变化热力图 · 2018–2023 减 2013–2018",
            difference_figure(merged, fast_values=fast_values, slow_values=slow_values),
            "analysis",
        ),
        ReportFigure(
            "cross-window-scatter",
            "同一参数对在两个窗口的收益",
            cross_window_scatter(merged),
            "analysis",
        ),
        ReportFigure(
            "performance-aapl",
            "2018–2023：代表参数与 AAPL 持有净值",
            performance_figure(block, results, summaries),
            "performance",
        ),
    ]
    report = render_interactive_report(
        title="AAPL 双均线状态策略 · 两个五年收益网格",
        heading="快线1–50日 × 慢线15–250日 · 步长1 · 两个不重叠窗口",
        subtitle="收盘确认 · 下一交易日开盘成交 · 单边5 bps · 无效角色格遮罩",
        summary_html=summary_html,
        notes=[
            "2013–2018表示[2013-01-01, 2018-01-01)，实际交易日为2013-01-02至2017-12-29；后一个窗口同理且不与前窗重叠。",
            "快线必须严格短于慢线；矩形中的快线不短于慢线区域遮罩，不参与排名或稳定性计算。",
            "零成本收益由相同已核对订单日程推导，只用于成本敏感性；报告主图和全部正式账户使用单边5 bps。",
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
        "# AAPL双均线两窗口收益网格\n\n"
        f"- 2013–2018机械最高：快{int(early['best_fast_window'])}/慢{int(early['best_slow_window'])}，累计收益{early['best_total_return_pct']:.2f}%。\n"
        f"- 2018–2023机械最高：快{int(later['best_fast_window'])}/慢{int(later['best_slow_window'])}，累计收益{later['best_total_return_pct']:.2f}%。\n"
        f"- 两窗口收益排名Spearman相关：{float(cross_summary['spearman_total_return']):.3f}；前10%集合Jaccard：{float(cross_summary['top_decile_jaccard']):.3f}。\n"
        "- 结果属于全样本参数面比较，不命名可部署冠军。\n",
        encoding="utf-8",
    )
    summary = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
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
        "software": {
            "python": platform.python_version(),
            "plotly": plotly.__version__,
        },
        "source_files": {},
    }
    for relative in (
        "backtest/quantkit/dual_sma_state.py",
        "backtest/scripts/run_aapl_dual_sma_grid.py",
        "backtest/scripts/analyze_aapl_dual_sma_grid.py",
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
        "# AAPL dual-SMA two-window grid run\n\nSee report.html, report.pdf, and analysis outputs.\n",
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
            "两个收益网格",
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
