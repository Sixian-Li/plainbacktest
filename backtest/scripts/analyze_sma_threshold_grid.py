#!/usr/bin/env python3
"""Analyze the SMA threshold experiment and build self-contained reports."""

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

from quantkit.reporting import ReportFigure, render_interactive_report
from quantkit.experiment import (
    assert_run_writable,
    cost_label,
    load_experiment,
    load_run,
    record_analysis_complete,
)
from quantkit.surface import analyze_surface


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/TIM/TIM-v0.10__26-08-08__sma200_threshold_grid"
RUN_ROOT: Path
ANALYSIS_ROOT: Path
SYMBOLS: tuple[str, ...]
COSTS: tuple[float, ...]
INITIAL_CASH: float
CASE_COUNT: int
SMA_WINDOW: int
PARAMETER_A: tuple[float, ...]
PARAMETER_B: tuple[float, ...]
REPORT_TEMPLATE_ID: str
REPORT_TEMPLATE_PATH: str
TOP_QUANTILE = 0.90


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


def result_root(symbol: str, cost_bps: int) -> Path:
    return RUN_ROOT / symbol / cost_label(cost_bps)


def load_results(symbol: str, cost_bps: int) -> pd.DataFrame:
    frame = pd.read_csv(result_root(symbol, cost_bps) / "parameter_results.csv")
    unique_count = len(frame[["a_pct", "b_pct"]].drop_duplicates())
    if len(frame) != CASE_COUNT or unique_count != CASE_COUNT:
        raise RuntimeError(
            f"{symbol} {cost_bps:g}bps does not contain {CASE_COUNT} unique cases"
        )
    return frame.sort_values("case_index").reset_index(drop=True)


def surface_key(cost_bps: float) -> str:
    return f"{float(cost_bps):g}bps"


def row_for(frame: pd.DataFrame, a_pct: float, b_pct: float) -> pd.Series:
    selected = frame[
        np.isclose(frame["a_pct"], a_pct) & np.isclose(frame["b_pct"], b_pct)
    ]
    if len(selected) != 1:
        raise RuntimeError(f"Expected one row for a={a_pct}, b={b_pct}; found {len(selected)}")
    return selected.iloc[0]


def point_summary(row: pd.Series) -> dict[str, Any]:
    fields = (
        "case_id",
        "case_index",
        "a_pct",
        "b_pct",
        "cost_bps",
        "final_equity",
        "total_return_pct",
        "cagr_pct",
        "annual_volatility_pct",
        "sharpe",
        "sortino",
        "max_drawdown_pct",
        "max_drawdown_duration_bars",
        "order_count",
        "closed_trade_count",
        "win_rate_pct",
        "turnover_multiple",
        "exposure_pct",
        "entry_rule",
        "first_buy_signal_date",
        "first_buy_fill_date",
        "benchmark_final_equity",
        "benchmark_cagr_pct",
        "benchmark_sharpe",
        "benchmark_max_drawdown_pct",
        "excess_cagr_pct_points",
    )
    return json_safe({field: row[field] for field in fields})


def compare_cost_surfaces(zero: pd.DataFrame, five: pd.DataFrame) -> dict[str, Any]:
    merged = zero.merge(five, on=["a_pct", "b_pct"], suffixes=("_0bps", "_5bps"))
    metrics = ("cagr_pct", "sharpe", "max_drawdown_pct", "order_count")
    comparisons: dict[str, Any] = {}
    for metric in metrics:
        left = merged[f"{metric}_0bps"].astype(float)
        right = merged[f"{metric}_5bps"].astype(float)
        difference = left - right
        comparisons[metric] = {
            "pearson_correlation": float(left.corr(right)),
            "median_zero_minus_five": float(difference.median()),
            "min_zero_minus_five": float(difference.min()),
            "max_zero_minus_five": float(difference.max()),
        }
    return comparisons


def extract_curve(symbol: str, cost_bps: int, case_index: int) -> pd.DataFrame:
    state = np.load(result_root(symbol, cost_bps) / "daily_state.npz")
    return pd.DataFrame(
        {
            "date": pd.to_datetime(state["dates"]),
            "equity": state["equity"][case_index],
            "cash": state["cash"][case_index],
            "shares": state["shares"][case_index],
            "signal": state["signal"][case_index],
        }
    )


def drawdown(equity: pd.Series) -> pd.Series:
    return equity / equity.cummax() - 1.0


def build_representative_files(
    symbol: str,
    zero: pd.DataFrame,
    five: pd.DataFrame,
    stable_a: float,
    stable_b: float,
    global_a: float,
    global_b: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    selections = {
        "base_5bps": row_for(five, 0.0, 0.0),
        "global_cagr_5bps": row_for(five, global_a, global_b),
        "stable_5bps": row_for(five, stable_a, stable_b),
        "stable_0bps": row_for(zero, stable_a, stable_b),
    }
    merged: pd.DataFrame | None = None
    for label, row in selections.items():
        cost = int(row["cost_bps"])
        curve = extract_curve(symbol, cost, int(row["case_index"]))
        curve = curve.rename(
            columns={
                "equity": f"{label}_equity",
                "cash": f"{label}_cash",
                "shares": f"{label}_shares",
                "signal": f"{label}_signal",
            }
        )
        merged = curve if merged is None else merged.merge(curve, on="date", how="inner")
    benchmark = pd.read_csv(result_root(symbol, 5) / "buy_hold_daily.csv")
    benchmark["date"] = pd.to_datetime(benchmark["date"])
    benchmark = benchmark[["date", "equity"]].rename(columns={"equity": "buy_hold_5bps_equity"})
    merged = merged.merge(benchmark, on="date", how="inner")
    for column in [item for item in merged.columns if item.endswith("_equity")]:
        merged[column.replace("_equity", "_drawdown")] = drawdown(merged[column])
    merged["date"] = merged["date"].dt.strftime("%Y-%m-%d")
    daily_path = ANALYSIS_ROOT / f"{symbol}_representative_daily.csv"
    merged.to_csv(daily_path, index=False, lineterminator="\n")

    stable_row = selections["stable_5bps"]
    orders = pd.read_csv(result_root(symbol, 5) / "orders.csv")
    stable_orders = orders[orders["case_index"] == int(stable_row["case_index"])].copy()
    stable_orders.to_csv(
        ANALYSIS_ROOT / f"{symbol}_stable_orders.csv", index=False, lineterminator="\n"
    )
    return merged, stable_orders


def market_figure(
    symbol: str,
    stable_a: float,
    stable_b: float,
    stable_orders: pd.DataFrame,
) -> go.Figure:
    canonical = pd.read_csv(WORKSPACE_ROOT / f"data/processed/daily/{symbol}.csv")
    canonical["date"] = pd.to_datetime(canonical["date"])
    canonical["sma200"] = canonical["close"].rolling(
        SMA_WINDOW, min_periods=SMA_WINDOW
    ).mean()
    canonical["buy_threshold"] = canonical["sma200"] * (1.0 + stable_b / 100.0)
    canonical["sell_threshold"] = canonical["sma200"] * (1.0 - stable_a / 100.0)
    warmup_end = canonical.loc[canonical["sma200"].first_valid_index(), "date"]

    figure = go.Figure()
    figure.add_trace(
        go.Candlestick(
            x=canonical["date"],
            open=canonical["open"],
            high=canonical["high"],
            low=canonical["low"],
            close=canonical["close"],
            name=f"{symbol} adjusted OHLC",
            increasing_line_color="#16a085",
            decreasing_line_color="#c0392b",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=canonical["date"],
            y=canonical["sma200"],
            name=f"SMA{SMA_WINDOW}",
            line={"color": "#34495e", "width": 1.4},
            meta={"series_key": "sma200", "panel": "market", "label": f"SMA{SMA_WINDOW}"},
        )
    )
    figure.add_trace(
        go.Scatter(
            x=canonical["date"],
            y=canonical["buy_threshold"],
            name=f"Buy threshold +{stable_b:g}%",
            line={"color": "#27ae60", "width": 1, "dash": "dot"},
            meta={"series_key": "buy_threshold", "panel": "market", "label": "买入阈值"},
        )
    )
    figure.add_trace(
        go.Scatter(
            x=canonical["date"],
            y=canonical["sell_threshold"],
            name=f"Sell threshold -{stable_a:g}%",
            line={"color": "#e67e22", "width": 1, "dash": "dot"},
            meta={"series_key": "sell_threshold", "panel": "market", "label": "卖出阈值"},
        )
    )
    for side, color, symbol_shape in (("buy", "#00a878", "triangle-up"), ("sell", "#d63031", "triangle-down")):
        selected = stable_orders[stable_orders["type"] == side]
        figure.add_trace(
            go.Scatter(
                x=pd.to_datetime(selected["date"]),
                y=selected["fill_price"],
                mode="markers",
                name=f"{side.title()} next open",
                marker={"color": color, "size": 9, "symbol": symbol_shape, "line": {"color": "white", "width": 0.5}},
                meta={
                    "series_key": f"{side}_fills",
                    "panel": "market",
                    "label": "买入成交点" if side == "buy" else "卖出成交点",
                },
                customdata=np.column_stack((selected["signal_date"], selected["raw_price"], selected["implicit_cost"])),
                hovertemplate=(
                    f"{side.title()} fill %{{y:.3f}}<br>Execution %{{x|%Y-%m-%d}}"
                    "<br>Signal %{customdata[0]}<br>Raw open %{customdata[1]:.3f}"
                    "<br>Implicit cost $%{customdata[2]:.2f}<extra></extra>"
                ),
            )
        )
    figure.update_layout(
        template="plotly_white",
        height=690,
        dragmode="pan",
        hovermode="x unified",
        legend={
            "orientation": "v",
            "x": 0.99,
            "xanchor": "right",
            "y": 0.99,
            "yanchor": "top",
            "bgcolor": "rgba(255,255,255,0.78)",
        },
        margin={"l": 60, "r": 25, "t": 65, "b": 45},
        yaxis_title="Adjusted price (USD-like level)",
        yaxis={"fixedrange": False},
    )
    figure.add_vrect(
        x0=canonical.iloc[0]["date"],
        x1=warmup_end,
        fillcolor="#94a3b8",
        opacity=0.12,
        line_width=0,
        annotation_text=f"SMA{SMA_WINDOW} warmup — no entry",
        annotation_position="top left",
    )
    figure.update_xaxes(
        rangeslider_visible=True,
        rangeselector={
            "buttons": [
                {"count": 1, "label": "1Y", "step": "year", "stepmode": "backward"},
                {"count": 5, "label": "5Y", "step": "year", "stepmode": "backward"},
                {"count": 10, "label": "10Y", "step": "year", "stepmode": "backward"},
                {"step": "all", "label": "All"},
            ]
        },
    )
    return figure


def performance_figure(symbol: str, daily: pd.DataFrame) -> go.Figure:
    frame = daily.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    series = (
        ("buy_hold_5bps", "Buy & hold, 5bps", "#2c3e50", None),
        ("base_5bps", "a=0%, b=0%, 5bps", "#7f8c8d", "dot"),
        ("global_cagr_5bps", "Global CAGR best, 5bps", "#c0392b", None),
        ("stable_5bps", "Stable plateau representative, 5bps", "#2980b9", None),
        ("stable_0bps", "Stable representative, 0bps", "#8e44ad", "dash"),
    )
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.67, 0.33],
        subplot_titles=("Account equity", "Drawdown"),
    )
    for key, label, color, dash in series:
        line = {"color": color, "width": 2}
        if dash:
            line["dash"] = dash
        figure.add_trace(
            go.Scatter(
                x=frame["date"],
                y=frame[f"{key}_equity"],
                name=label,
                line=line,
                meta={
                    "series_key": key,
                    "panel": "equity",
                    "label": label,
                    "is_benchmark": key == "buy_hold_5bps",
                    "cost_bps": 5 if key == "buy_hold_5bps" else None,
                },
            ),
            row=1,
            col=1,
        )
        figure.add_trace(
            go.Scatter(
                x=frame["date"],
                y=frame[f"{key}_drawdown"] * 100.0,
                name=label,
                line=line,
                showlegend=False,
                meta={
                    "series_key": key,
                    "panel": "drawdown",
                    "label": label,
                    "is_benchmark": key == "buy_hold_5bps",
                    "cost_bps": 5 if key == "buy_hold_5bps" else None,
                },
            ),
            row=2,
            col=1,
        )
    figure.update_layout(
        template="plotly_white",
        height=760,
        dragmode="pan",
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.10, "itemclick": False, "itemdoubleclick": False},
        margin={"l": 65, "r": 25, "t": 110, "b": 45},
        uirevision=f"performance-{symbol}-v1",
    )
    figure.update_yaxes(title_text="Equity ($)", row=1, col=1)
    figure.update_yaxes(title_text="Drawdown (%)", row=2, col=1)
    figure.update_xaxes(rangeslider_visible=True, row=2, col=1)
    return figure


def _add_cagr_selection_markers(
    figure: go.Figure,
    *,
    row: int,
    col: int,
    stable: dict[str, Any],
    global_best: dict[str, Any],
    showlegend: bool,
) -> None:
    figure.add_trace(
        go.Scatter(
            x=[stable["b_pct"]],
            y=[stable["a_pct"]],
            mode="markers",
            marker={"symbol": "circle-open", "size": 15, "color": "white", "line": {"width": 3}},
            name="stable representative",
            legendgroup="stable",
            showlegend=showlegend,
            hovertemplate="Stable representative<br>a=%{y:.2f}%<br>b=%{x:.2f}%<extra></extra>",
        ),
        row=row,
        col=col,
    )
    figure.add_trace(
        go.Scatter(
            x=[global_best["b_pct"]],
            y=[global_best["a_pct"]],
            mode="markers",
            marker={"symbol": "x", "size": 12, "color": "#e74c3c", "line": {"width": 2}},
            name="global CAGR best",
            legendgroup="global",
            showlegend=showlegend,
            hovertemplate="Global CAGR best<br>a=%{y:.2f}%<br>b=%{x:.2f}%<extra></extra>",
        ),
        row=row,
        col=col,
    )


def cagr_heatmap_figure(
    symbol: str,
    frames: dict[int, pd.DataFrame],
    stable: dict[str, Any],
    global_best: dict[str, Any],
) -> go.Figure:
    values = pd.concat([frames[cost]["cagr_pct"] for cost in COSTS], ignore_index=True)
    figure = make_subplots(
        rows=1,
        cols=len(COSTS),
        subplot_titles=[f"{symbol} CAGR — {cost:g} bps" for cost in COSTS],
        horizontal_spacing=0.09,
    )
    for column_number, cost in enumerate(COSTS, start=1):
        pivot = (
            frames[cost]
            .pivot(index="a_pct", columns="b_pct", values="cagr_pct")
            .sort_index()
            .sort_index(axis=1)
        )
        figure.add_trace(
            go.Heatmap(
                x=pivot.columns,
                y=pivot.index,
                z=pivot.to_numpy(),
                coloraxis="coloraxis",
                hovertemplate="a=%{y:.2f}%<br>b=%{x:.2f}%<br>CAGR=%{z:.4f}%<extra></extra>",
            ),
            row=1,
            col=column_number,
        )
        if cost == 5:
            _add_cagr_selection_markers(
                figure,
                row=1,
                col=column_number,
                stable=stable,
                global_best=global_best,
                showlegend=True,
            )
        figure.update_xaxes(title_text="b: buy buffer (%)", row=1, col=column_number)
        figure.update_yaxes(title_text="a: sell buffer (%)", row=1, col=column_number)
    figure.update_layout(
        template="plotly_white",
        height=650,
        margin={"l": 65, "r": 90, "t": 115, "b": 55},
        legend={"orientation": "h", "y": 1.10},
        coloraxis={
            "colorscale": "Viridis",
            "cmin": float(values.min()),
            "cmax": float(values.max()),
            "colorbar": {"title": "CAGR (%)"},
        },
    )
    return figure


def diagnostic_heatmap_figure(
    symbol: str,
    frames: dict[int, pd.DataFrame],
    stable: dict[str, Any],
    global_best: dict[str, Any],
) -> go.Figure:
    metrics = (
        ("sharpe", "Sharpe (rf=0)", "Blues"),
        ("max_drawdown_pct", "Max drawdown (%)", "RdYlGn"),
        ("order_count", "Order count", "Cividis"),
    )
    titles = [f"{label} — {cost}bps" for _, label, _ in metrics for cost in COSTS]
    figure = make_subplots(rows=3, cols=2, subplot_titles=titles, horizontal_spacing=0.09, vertical_spacing=0.08)
    for row_number, (metric, _, scale) in enumerate(metrics, start=1):
        for column_number, cost in enumerate(COSTS, start=1):
            pivot = frames[cost].pivot(index="a_pct", columns="b_pct", values=metric).sort_index().sort_index(axis=1)
            figure.add_trace(
                go.Heatmap(
                    x=pivot.columns,
                    y=pivot.index,
                    z=pivot.to_numpy(),
                    colorscale=scale,
                    colorbar={"len": 0.24, "y": 1.0 - (row_number - 0.5) / 3.0},
                    hovertemplate="a=%{y:.2f}%<br>b=%{x:.2f}%<br>value=%{z:.4f}<extra></extra>",
                    showscale=column_number == 2,
                ),
                row=row_number,
                col=column_number,
            )
            if cost == 5:
                _add_cagr_selection_markers(
                    figure,
                    row=row_number,
                    col=column_number,
                    stable=stable,
                    global_best=global_best,
                    showlegend=row_number == 1,
                )
            figure.update_xaxes(title_text="b: buy buffer (%)", row=row_number, col=column_number)
            figure.update_yaxes(title_text="a: sell buffer (%)", row=row_number, col=column_number)
    figure.update_layout(
        template="plotly_white",
        height=1220,
        margin={"l": 65, "r": 65, "t": 115, "b": 50},
        legend={"orientation": "h", "y": 1.055},
    )
    return figure


def format_point(point: dict[str, Any]) -> str:
    return f"a={point['a_pct']:.2f}%, b={point['b_pct']:.2f}%"


def build_markdown(analysis: dict[str, Any]) -> str:
    total_cases = len(SYMBOLS) * len(COSTS) * CASE_COUNT
    a_step = PARAMETER_A[1] - PARAMETER_A[0] if len(PARAMETER_A) > 1 else 0.0
    b_step = PARAMETER_B[1] - PARAMETER_B[0] if len(PARAMETER_B) > 1 else 0.0
    lines = [
        f"# SMA{SMA_WINDOW} 双阈值全样本探索报告",
        "",
        "> 结论边界：这是全样本参数面探索，不是样本外验证，也不是实盘推荐。参数选择之后仍需 walk-forward 或锁定测试期验证。",
        "",
        "## 1. 实验口径",
        "",
        f"- 策略：SMA{SMA_WINDOW} 预热完成后，空仓时必须由 `Close <= SMA × (1+b%)` 向上穿越至 `Close > SMA × (1+b%)` 才产生买入信号；持仓且 `Close < SMA × (1-a%)` 时卖出。",
        "- 首个有效 SMA 日只建立前一状态，不能买入；最早从下一根有效 SMA 日判断穿越。",
        "- 信号：当日常规时段收盘后确认；成交：下一交易日常规时段开盘。",
        f"- 参数：a 为 {min(PARAMETER_A):g}%～{max(PARAMETER_A):g}%（步长 {a_step:g}%），b 为 {min(PARAMETER_B):g}%～{max(PARAMETER_B):g}%（步长 {b_step:g}%），每个曲面 {CASE_COUNT} 个 case。",
        "- 成本：分别为零成本和单边 5 bps；5 bps 通过买入价上调、卖出价下调计入。",
        "- 仓位：只做多、0%/100%、允许碎股、初始资金 $100,000、不融资。",
        f"- 指标：Sharpe/Sortino 的无风险利率为 0；年化按 252 根日线；买入持有从 SMA{SMA_WINDOW} 预热完成后的首个可执行开盘开始。",
        "- 正确性：每个参数组合均由 PyBroker 与独立事件账本逐日核对现金、股数、净值和订单。",
        "",
        "## 2. 主要结果（以 5 bps 为主）",
        "",
        "| 标的 | 区间 | 普通 a=b=0 CAGR | 全局最高 CAGR | 稳定代表 CAGR / Sharpe | 买入持有 CAGR / Sharpe | 平滑性判断 |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for symbol in SYMBOLS:
        item = analysis["symbols"][symbol]
        base = item["selections"]["base_5bps"]
        best = item["selections"]["global_cagr_5bps"]
        stable = item["selections"]["stable_5bps"]
        label = "连续平台" if item["verdict"] == "smooth_plateau" else "平台延伸到搜索边界"
        lines.append(
            f"| {symbol} | {item['analysis_start']}～{item['analysis_end']} | "
            f"{base['cagr_pct']:.2f}% | {best['cagr_pct']:.2f}% ({format_point(best)}) | "
            f"{stable['cagr_pct']:.2f}% / {stable['sharpe']:.3f} ({format_point(stable)}) | "
            f"{base['benchmark_cagr_pct']:.2f}% / {base['benchmark_sharpe']:.3f} | {label} |"
        )

    for number, symbol in enumerate(SYMBOLS, start=3):
        item = analysis["symbols"][symbol]
        cagr = item["surfaces"]["5bps"]["cagr_pct"]
        plateau = cagr["largest_plateau"]
        best = item["selections"]["global_cagr_5bps"]
        stable = item["selections"]["stable_5bps"]
        base = item["selections"]["base_5bps"]
        correlation = item["cost_comparison"]["cagr_pct"]["pearson_correlation"]
        median_cost = item["cost_comparison"]["cagr_pct"]["median_zero_minus_five"]
        lines.extend(
            [
                "",
                f"## {number}. {symbol}",
                "",
                f"- 5 bps 全局 CAGR 最高点为 {format_point(best)}，CAGR {best['cagr_pct']:.3f}%，Sharpe {best['sharpe']:.3f}，最大回撤 {best['max_drawdown_pct']:.2f}%。",
                f"- 稳定平台代表为 {format_point(stable)}，CAGR {stable['cagr_pct']:.3f}%，Sharpe {stable['sharpe']:.3f}，最大回撤 {stable['max_drawdown_pct']:.2f}%。它不是机械最高点，而是最大高表现连通区内 3×3 邻域中位数减半个标准差最高的点。",
                f"- 最大 top-decile 连通区包含 {plateau['cell_count']}/{cagr['top_cell_count']} 个高表现格点，范围 a={plateau['a_min']:.2f}%～{plateau['a_max']:.2f}%、b={plateau['b_min']:.2f}%～{plateau['b_max']:.2f}%。",
                f"- 全局最高点的 3×3 邻域中位 CAGR 为 {cagr['global_best']['neighborhood_median']:.3f}%，局部孤立尖峰判定为 {'是' if cagr['global_best']['locally_isolated_spike'] else '否'}。",
                f"- 0 bps 与 5 bps 的 CAGR 曲面相关系数为 {correlation:.4f}，5 bps 造成的 CAGR 中位损失为 {median_cost:.3f} 个百分点。",
                f"- 普通 a=b=0 的 CAGR 为 {base['cagr_pct']:.3f}%，买入持有为 {base['benchmark_cagr_pct']:.3f}%。",
            ]
        )
        if plateau["touches_search_boundary"]:
            lines.append(
                f"- 边界警告：高表现平台触及 {', '.join(plateau['boundary_sides'])}，说明当前 a={min(PARAMETER_A):g}%～{max(PARAMETER_A):g}%、b={min(PARAMETER_B):g}%～{max(PARAMETER_B):g}% 搜索范围可能截断了平台；不能把边界附近参数视为已充分定位。"
            )
        else:
            lines.append("- 高表现平台未触及 a/b 搜索边界，当前局部范围足以看见平台四周的下降区域。")

    max_error = max(
        item["max_cross_check_equity_error"] for item in analysis["symbols"].values()
    )
    lines.extend(
        [
            "",
            "## 5. 如何解释",
            "",
            "- 参数选择以连续高表现平台为主，不把机械全局最高点直接当作候选；任何触及新搜索边界的平台仍需继续审查。",
            "- 全样本表现只能用于提出候选。它已经使用未来全部历史挑选参数，因此不能把最高 CAGR 或稳定代表称为可预期未来收益。",
            "- SPY 趋势策略的 CAGR 低于买入持有并不等于没有研究价值；它的主要差异需要结合最大回撤和持仓暴露评估。是否值得采用应由样本外结果决定。",
            "- 零成本与 5 bps 曲面高度相关，说明这次较低频策略的形状没有被 5 bps 成本颠覆；但成本仍降低收益，且不能替代真实点差、税务和成交偏差。",
            "",
            "## 6. 正确性与限制",
            "",
            f"- {total_cases:,} 个 case 全部完成；独立账本与 PyBroker 的最大逐日净值绝对误差为 ${max_error:.3g}。",
            "- 所有正式订单强制指定下一交易日开盘成交；PyBroker 的默认 high/low 中间价没有进入实验。",
            f"- 当前 {'/'.join(SYMBOLS)} 使用拆股及股息调整 OHLC，适合内部一致的长期趋势探索，但并不是原始成交价加现金分红/拆股事件的交易所级账户回放。",
            "- 数据截至 2026-08-04；SPY 是 VOO 的长期标普代理。本实验没有拼接 VOO，VOO 仍因已知 OHLC 异常和缺失交易日而禁用。",
            f"- 当前只研究固定 SMA{SMA_WINDOW} 与阈值，不包含 RSI、动态选参、收盘成交、融资、税费或多标的共享仓位策略。",
            "",
            "## 7. 下一轮合理工作",
            "",
            "1. 先预先规定 walk-forward 窗口、更新频率和参数选择规则，再生成连续样本外资金曲线。",
            "2. 对任何触及 a/b 搜索边界的平台继续扩大相应方向；未触边的平台才可进入后续候选区评估。",
            "3. 在不改变信号定义的前提下，再独立比较下一开盘与有明确信息假设的收盘成交版本。",
            "4. 获得公司行动事件数据后，用原始 OHLC、现金分红和份额调整账本做精确复核。",
            "",
            "交互图见 `report.html`；完整参数结果、订单、交易和逐日状态位于各 symbol/cost 子目录。",
            "",
            "净值图的区间对齐与定投是浏览器端情景分析：对齐以可见区间首个共同交易日的买入持有净值为共同起点；定投以该净值为总预算、按指定次数等间隔买入，未投入现金不计息且每笔计 5 bps。两者都不改写正式回测结果。",
        ]
    )
    return "\n".join(lines) + "\n"


def summary_table_html(analysis: dict[str, Any]) -> str:
    rows = []
    for symbol in SYMBOLS:
        item = analysis["symbols"][symbol]
        base = item["selections"]["base_5bps"]
        best = item["selections"]["global_cagr_5bps"]
        stable = item["selections"]["stable_5bps"]
        rows.append(
            "<tr>"
            f"<td>{symbol}</td><td>{item['analysis_start']} – {item['analysis_end']}</td>"
            f"<td>{base['cagr_pct']:.2f}%</td>"
            f"<td>{best['cagr_pct']:.2f}%<br><small>{html.escape(format_point(best))}</small></td>"
            f"<td>{stable['cagr_pct']:.2f}% / {stable['sharpe']:.3f}<br><small>{html.escape(format_point(stable))}</small></td>"
            f"<td>{base['benchmark_cagr_pct']:.2f}% / {base['benchmark_sharpe']:.3f}</td>"
            f"<td>{'平台完整' if item['verdict'] == 'smooth_plateau' else '平台触及边界'}</td>"
            "</tr>"
        )
    return "".join(rows)


def build_html(
    analysis: dict[str, Any],
    figures: list[tuple[str, str, go.Figure, str]],
    experiment: dict[str, Any],
    run_id: str,
) -> str:
    summary = (
        "<h2>5 bps 主要结果</h2><table><thead><tr><th>标的</th><th>区间</th>"
        "<th>a=b=0 CAGR</th><th>全局最高 CAGR</th><th>稳定代表 CAGR / Sharpe</th>"
        "<th>买入持有 CAGR / Sharpe</th><th>判断</th></tr></thead><tbody>"
        f"{summary_table_html(analysis)}</tbody></table>"
    )
    notes = [
        "K 线默认随可见时间窗口自动调整纵轴；也可切换手动模式、拖动纵轴或直接输入上下限。",
        f"K 线上方可独立勾选 SMA{SMA_WINDOW}、买入阈值、卖出阈值及买卖成交点。",
        "净值图可勾选显示系列，并把当前可见区间左端统一对齐到 Buy & Hold 当日净值。",
        "定投情景把区间左端 Buy & Hold 净值作为总预算，按指定次数等间隔投入；未投入现金不计息，每笔买入成本为 5 bps。",
        f"灰色区域是 SMA{SMA_WINDOW} 预热期，预热结束时不会因为价格已经在阈值上方而立即买入；必须等待一次向上穿越。",
        "CAGR 热力图用统一色阶并排比较 0/5 bps；白圈是稳定平台代表，红色 × 是全局 CAGR 最高点。",
        "Sharpe 统一按全区间日收益均值 ÷ 日收益标准差 × √252 计算，无风险利率为 0；策略与 Buy & Hold 使用相同口径和起止日。",
        "K 线使用拆股及股息调整 OHLC，不等同于原始成交价加现金分红的精确账户回放。",
        "如果高表现平台触及 a/b 搜索边界，报告会明确标记；边界参数不能视为已经充分定位。",
    ]
    return render_interactive_report(
        title=f"SMA{SMA_WINDOW} 双阈值回测报告",
        heading=f"SMA{SMA_WINDOW} 双阈值全样本探索",
        subtitle=(
            f"{'、'.join(SYMBOLS)}；SMA{SMA_WINDOW} 预热后等待价格向上穿越买入阈值，"
            f"收盘确认，下一交易日开盘成交；每个成本曲面 {CASE_COUNT} 个 case。"
        ),
        summary_html=summary,
        notes=notes,
        figures=[ReportFigure(*item) for item in figures],
        experiment=experiment,
        run_id=run_id,
        template_id=REPORT_TEMPLATE_ID,
    )


def main() -> None:
    global RUN_ROOT, ANALYSIS_ROOT, SYMBOLS, COSTS, INITIAL_CASH, CASE_COUNT
    global SMA_WINDOW, PARAMETER_A, PARAMETER_B, REPORT_TEMPLATE_ID, REPORT_TEMPLATE_PATH

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    assert_run_writable(context, args.run_id)
    record = load_run(context, args.run_id)
    incomplete = [
        item["block_id"]
        for item in record["expected_blocks"]
        if item["status"] != "completed"
    ]
    if incomplete:
        raise RuntimeError(f"Cannot analyze an incomplete run: {incomplete}")
    RUN_ROOT = context.run_root(args.run_id)
    ANALYSIS_ROOT = RUN_ROOT / "analysis"
    SYMBOLS = tuple(context.config["symbols"])
    COSTS = tuple(float(value) for value in context.config["cost_scenarios_bps_per_side"])
    if set(COSTS) != {0.0, 5.0}:
        raise ValueError("This analysis currently requires configured cost scenarios 0 and 5 bps")
    INITIAL_CASH = float(context.config["initial_cash"])
    SMA_WINDOW = int(context.config["strategy"]["sma_window"])
    PARAMETER_A = tuple(float(value) for value in context.config["parameters"]["a_pct"])
    PARAMETER_B = tuple(float(value) for value in context.config["parameters"]["b_pct"])
    REPORT_TEMPLATE_ID = str(context.config["reporting"]["template_id"])
    REPORT_TEMPLATE_PATH = str(context.config["reporting"]["template_path"])
    CASE_COUNT = len(PARAMETER_A) * len(PARAMETER_B)
    ANALYSIS_ROOT.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    canonical_manifest = json.loads(
        (WORKSPACE_ROOT / "data/processed/manifest.json").read_text(encoding="utf-8")
    )
    analysis: dict[str, Any] = {
        "schema_version": 1,
        "created_at_utc": created_at,
        "purpose": "full-sample exploratory parameter-surface analysis; not out-of-sample validation",
        "entry_rule": f"cross above upper SMA{SMA_WINDOW} threshold after warmup; first valid SMA bar cannot enter",
        "top_plateau_definition": "top 10% CAGR cells joined by four-neighbor adjacency",
        "stable_representative_rule": "within largest plateau, maximize 3x3 median CAGR minus 0.5 times 3x3 standard deviation; prefer a full 3x3 neighborhood",
        "symbols": {},
    }
    summary_records: list[dict[str, Any]] = []
    figures: list[tuple[str, str, go.Figure, str]] = []

    for symbol in SYMBOLS:
        frames = {cost: load_results(symbol, cost) for cost in COSTS}
        manifests = {
            cost: json.loads((result_root(symbol, cost) / "manifest.json").read_text(encoding="utf-8"))
            for cost in COSTS
        }
        surfaces: dict[str, Any] = {}
        for cost in COSTS:
            surfaces[surface_key(cost)] = {
                metric: analyze_surface(frames[cost], metric=metric, top_quantile=TOP_QUANTILE)
                for metric in ("cagr_pct", "sharpe", "max_drawdown_pct")
            }

        primary = surfaces["5bps"]["cagr_pct"]
        stable_point = primary["stable_representative"]
        global_point = primary["global_best"]
        stable_5 = row_for(frames[5], stable_point["a_pct"], stable_point["b_pct"])
        stable_0 = row_for(frames[0], stable_point["a_pct"], stable_point["b_pct"])
        global_5 = row_for(frames[5], global_point["a_pct"], global_point["b_pct"])
        base_5 = row_for(frames[5], 0.0, 0.0)
        global_sharpe_5 = frames[5].loc[frames[5]["sharpe"].idxmax()]
        global_drawdown_5 = frames[5].loc[frames[5]["max_drawdown_pct"].idxmax()]
        verdict = (
            "plateau_but_boundary_truncated"
            if primary["largest_plateau"]["touches_search_boundary"]
            else "smooth_plateau"
        )
        if primary["global_best"]["locally_isolated_spike"] or not primary["global_best"]["in_largest_plateau"]:
            verdict = "isolated_or_disconnected_peak"
        item = {
            "analysis_start": manifests[5]["analysis_start"],
            "analysis_end": manifests[5]["analysis_end"],
            "analysis_bars": manifests[5]["analysis_bars"],
            "verdict": verdict,
            "surfaces": surfaces,
            "cost_comparison": compare_cost_surfaces(frames[0], frames[5]),
            "selections": {
                "base_5bps": point_summary(base_5),
                "global_cagr_5bps": point_summary(global_5),
                "stable_5bps": point_summary(stable_5),
                "stable_0bps": point_summary(stable_0),
                "global_sharpe_5bps": point_summary(global_sharpe_5),
                "least_drawdown_5bps": point_summary(global_drawdown_5),
            },
            "max_cross_check_equity_error": max(
                manifests[cost]["max_cross_check_differences"]["max_abs_equity_difference"]
                for cost in COSTS
            ),
        }
        analysis["symbols"][symbol] = item

        for selection, selected in item["selections"].items():
            summary_records.append({"symbol": symbol, "selection": selection, **selected})
        daily, stable_orders = build_representative_files(
            symbol,
            frames[0],
            frames[5],
            stable_point["a_pct"],
            stable_point["b_pct"],
            global_point["a_pct"],
            global_point["b_pct"],
        )
        figures.extend(
            [
                (
                    f"market-{symbol.lower()}",
                    f"{symbol}：K 线、SMA{SMA_WINDOW}、阈值与买卖点",
                    market_figure(symbol, stable_point["a_pct"], stable_point["b_pct"], stable_orders),
                    "market",
                ),
                (
                    f"performance-{symbol.lower()}",
                    f"{symbol}：净值与回撤",
                    performance_figure(symbol, daily),
                    "performance",
                ),
                (
                    f"cagr-heatmap-{symbol.lower()}",
                    f"{symbol}：CAGR 参数热力图",
                    cagr_heatmap_figure(symbol, frames, stable_point, global_point),
                    "heatmap",
                ),
                (
                    f"diagnostic-heatmap-{symbol.lower()}",
                    f"{symbol}：风险与交易频率参数面",
                    diagnostic_heatmap_figure(symbol, frames, stable_point, global_point),
                    "heatmap",
                ),
            ]
        )

    analysis_path = ANALYSIS_ROOT / "smoothness.json"
    analysis_path.write_text(
        json.dumps(json_safe(analysis), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    pd.DataFrame(summary_records).to_csv(
        ANALYSIS_ROOT / "parameter_summary.csv", index=False, lineterminator="\n"
    )
    report_markdown = build_markdown(analysis)
    (RUN_ROOT / "report.md").write_text(report_markdown, encoding="utf-8")
    (RUN_ROOT / "report.html").write_text(
        build_html(analysis, figures, context.config, args.run_id), encoding="utf-8"
    )

    provenance = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": created_at,
        "canonical_data_build_id": canonical_manifest["build_id"],
        "software": {
            "python": platform.python_version(),
            "lib_pybroker": "1.2.12",
            "plotly": plotly.__version__,
        },
        "source_files": {},
    }
    tracked_sources = [
        "backtest/requirements.lock",
        "backtest/quantkit/execution.py",
        "backtest/quantkit/metrics.py",
        "backtest/quantkit/reference.py",
        "backtest/quantkit/reporting.py",
        "backtest/quantkit/sma_threshold.py",
        "backtest/quantkit/surface.py",
        "backtest/quantkit/experiment.py",
        f"{REPORT_TEMPLATE_PATH}/page.html",
        f"{REPORT_TEMPLATE_PATH}/styles.css",
        f"{REPORT_TEMPLATE_PATH}/interactions.js",
        "backtest/scripts/run_sma_threshold_grid.py",
        "backtest/scripts/analyze_sma_threshold_grid.py",
        "backtest/scripts/smoke_report_ui.mjs",
        "data/processed/manifest.json",
    ]
    tracked_sources.extend(f"data/processed/daily/{symbol}.csv" for symbol in SYMBOLS)
    for relative in tracked_sources:
        path = WORKSPACE_ROOT / relative
        provenance["source_files"][relative] = {
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
    (RUN_ROOT / "provenance.json").write_text(
        json.dumps(json_safe(provenance), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    readme = f"""# Run {args.run_id}

This is one concrete execution of `{context.config['experiment_id']}`. Its strategy and parameter definition is frozen in `experiment_snapshot.json`; runtime state is in `run.json`.

- `report.html` and `report.md`: human-readable analysis.
- `analysis/`: machine-readable surface diagnostics and representative cases.
- `{{symbol}}/cost_{{n}}bps/`: all case metrics, ledgers, daily arrays, benchmark, and independent-ledger evidence.
- `provenance.json`: exact code, dependency, canonical-manifest, and canonical-price hashes.
- `artifact_manifest.json`: hashes for generated files.
- `validation.json`: mandatory gate evidence, written only by the validator.

The browser-side rebasing and DCA controls are visualization scenarios; they do not mutate formal metrics or ledgers. The adjusted OHLC data is suitable for internally consistent trend research, not an exchange-grade raw-price plus corporate-action replay.
"""
    (RUN_ROOT / "README.md").write_text(readme, encoding="utf-8")

    artifact_manifest: dict[str, Any] = {"schema_version": 1, "created_at_utc": created_at, "artifacts": {}}
    for path in sorted(RUN_ROOT.rglob("*")):
        if path.is_file() and path.name not in {"artifact_manifest.json", "run.json", "validation.json"}:
            relative = str(path.relative_to(RUN_ROOT))
            artifact_manifest["artifacts"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    (RUN_ROOT / "artifact_manifest.json").write_text(
        json.dumps(artifact_manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    record_analysis_complete(context, args.run_id)
    print(f"Wrote {RUN_ROOT / 'report.html'}")
    for symbol in SYMBOLS:
        item = analysis["symbols"][symbol]
        print(
            f"{symbol}: {item['verdict']}; global {format_point(item['selections']['global_cagr_5bps'])}; "
            f"stable {format_point(item['selections']['stable_5bps'])}"
        )


if __name__ == "__main__":
    main()
