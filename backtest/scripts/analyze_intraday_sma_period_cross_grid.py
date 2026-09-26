#!/usr/bin/env python3
"""Build the report for the QQQ independent buy/sell SMA-period grid."""

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
    block_root,
    load_experiment,
    load_run,
    record_analysis_complete,
    sha256,
)
from quantkit.intraday_sma_period_cross import (
    BUY_SMA_CROSS,
    FORCED_REBUY,
    SELL_SMA_CROSS,
    STOP_LOSS,
    prepare_sma_period_data,
)
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts.analyze_intraday_sma200_threshold_grid import drawdown
from scripts.run_intraday_sma_backtest import json_safe


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = (
    BACKTEST_ROOT
    / "experiments/TIM/TIM-v0.50__26-08-15__qqq_intraday_sma_period_cross_grid_1999_2015"
)
FORCED_REBUY_VALUES = (3.0, 5.0, 8.0, 10.0)
STOP_VALUES: tuple[float | None, ...] = (None, 3.0, 5.0, 8.0, 10.0)
COLORS = {3.0: "#2563eb", 5.0: "#0f766e", 8.0: "#d97706", 10.0: "#7c3aed"}
STOP_DASHES = {None: "solid", 3.0: "dash", 5.0: "dot", 8.0: "dashdot", 10.0: "longdash"}
SIGNAL_LABELS = {
    BUY_SMA_CROSS: "普通上穿买入",
    FORCED_REBUY: "强制纠错买回",
    SELL_SMA_CROSS: "普通下穿卖出",
    STOP_LOSS: "止损卖出",
}


def stop_label(value: float | None) -> str:
    return "关闭" if value is None or pd.isna(value) else f"{float(value):g}%"


def mode_label(row: pd.Series | Any) -> str:
    stop = getattr(row, "stop_loss_pct", None)
    rebuy = float(getattr(row, "forced_rebuy_pct"))
    return f"c={rebuy:g}%，d={stop_label(stop)}"


def _representative_row(
    representatives: pd.DataFrame,
    *,
    forced_rebuy_pct: float,
    stop_loss_pct: float | None,
) -> pd.Series:
    selected = representatives[np.isclose(representatives["forced_rebuy_pct"], forced_rebuy_pct)]
    if stop_loss_pct is None:
        selected = selected[selected["stop_loss_pct"].isna()]
    else:
        selected = selected[np.isclose(selected["stop_loss_pct"], stop_loss_pct)]
    if len(selected) != 1:
        raise AssertionError(
            f"Expected one representative for c={forced_rebuy_pct:g}, d={stop_loss_pct}."
        )
    return selected.iloc[0]


def performance_figure(
    daily: pd.DataFrame,
    benchmark: pd.DataFrame,
    representatives: pd.DataFrame,
) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.7, 0.3],
        vertical_spacing=0.08,
        subplot_titles=("20 个模式的确定性曲面代表", "从各自历史峰值回撤"),
    )
    default_modes = {(5.0, None), (5.0, 5.0)}
    for row in representatives.itertuples(index=False):
        case_id = str(row.case_id)
        frame = daily[daily["case_id"] == case_id].sort_values("date")
        if frame.empty:
            raise AssertionError(f"Missing formal daily ledger for {case_id}.")
        stop = None if pd.isna(row.stop_loss_pct) else float(row.stop_loss_pct)
        label = (
            f"{mode_label(row)}；买{int(row.buy_window)}/卖{int(row.sell_window)}"
            f"；门禁{'通过' if bool(row.promotion_gate_pass) else '未通过'}"
        )
        visible: bool | str = (
            True
            if (float(row.forced_rebuy_pct), stop) in default_modes
            or bool(row.promotion_gate_pass)
            else "legendonly"
        )
        for panel, values, row_number, showlegend in (
            ("equity", frame["equity"], 1, True),
            ("drawdown", drawdown(frame["equity"]), 2, False),
        ):
            figure.add_trace(
                go.Scatter(
                    x=frame["date"],
                    y=values,
                    mode="lines",
                    name=label,
                    showlegend=showlegend,
                    visible=visible,
                    line={
                        "color": COLORS[float(row.forced_rebuy_pct)],
                        "width": 2.7 if bool(row.promotion_gate_pass) else 1.7,
                        "dash": STOP_DASHES[stop],
                    },
                    meta={"series_key": case_id, "panel": panel, "label": label},
                    hovertemplate=f"{label}<br>%{{x|%Y-%m-%d}}<br>%{{y:,.2f}}<extra></extra>",
                ),
                row=row_number,
                col=1,
            )
    benchmark = benchmark.sort_values("date")
    benchmark_label = "QQQ Buy & Hold（2000-12-18 Open，5 bps）"
    for panel, values, row_number, showlegend in (
        ("equity", benchmark["equity"], 1, True),
        ("drawdown", drawdown(benchmark["equity"]), 2, False),
    ):
        figure.add_trace(
            go.Scatter(
                x=benchmark["date"],
                y=values,
                mode="lines",
                name=benchmark_label,
                showlegend=showlegend,
                line={"color": "#111827", "width": 2.0, "dash": "dash"},
                meta={
                    "series_key": "buy_hold_5bps",
                    "panel": panel,
                    "label": benchmark_label,
                    "is_benchmark": panel == "equity",
                    "cost_bps": 5,
                },
                hovertemplate=(
                    f"{benchmark_label}<br>%{{x|%Y-%m-%d}}<br>%{{y:,.2f}}<extra></extra>"
                ),
            ),
            row=row_number,
            col=1,
        )
    figure.update_layout(
        height=840,
        margin={"l": 65, "r": 25, "t": 75, "b": 55},
        hovermode="x unified",
        showlegend=False,
        uirevision="qqq-sma-period-cross-performance-v1",
    )
    figure.update_yaxes(title_text="USD", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="%", fixedrange=False, row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def representative_metrics_figure(representatives: pd.DataFrame) -> go.Figure:
    metrics = (
        ("primary_metric", "三段最差 CAGR", "%"),
        ("cagr_pct", "全历史 CAGR", "%"),
        ("sharpe", "全历史 Sharpe", ""),
        ("max_drawdown_pct", "最大回撤", "%"),
        ("order_count", "成交次数", "次"),
        ("stop_loss_count", "止损主触发次数", "次"),
    )
    figure = make_subplots(rows=2, cols=3, subplot_titles=[item[1] for item in metrics])
    for metric_index, (metric, _, unit) in enumerate(metrics):
        row_number = metric_index // 3 + 1
        column = metric_index % 3 + 1
        for rebuy in FORCED_REBUY_VALUES:
            selected = representatives[
                np.isclose(representatives["forced_rebuy_pct"], rebuy)
            ].copy()
            selected["d_axis"] = selected["stop_loss_pct"].fillna(0.0)
            selected = selected.sort_values("d_axis")
            custom = np.column_stack(
                [
                    selected["buy_window"].astype(int),
                    selected["sell_window"].astype(int),
                    selected["structural_pass"].map({True: "通过", False: "未通过"}),
                    selected["promotion_gate_pass"].map({True: "通过", False: "未通过"}),
                ]
            )
            figure.add_trace(
                go.Scatter(
                    x=selected["d_axis"],
                    y=selected[metric],
                    customdata=custom,
                    mode="lines+markers",
                    name=f"c={rebuy:g}%",
                    legendgroup=f"c{rebuy:g}",
                    showlegend=metric_index == 0,
                    line={"color": COLORS[rebuy], "width": 2.2},
                    marker={
                        "size": 9,
                        "symbol": [
                            "star" if bool(value) else "circle-open"
                            for value in selected["promotion_gate_pass"]
                        ],
                    },
                    hovertemplate=(
                        f"c={rebuy:g}%，d=%{{x:g}}%（0=关闭）<br>{metric}=%{{y:.4f}} {unit}"
                        "<br>买/卖 SMA=%{customdata[0]}/%{customdata[1]}"
                        "<br>结构门禁=%{customdata[2]}<br>全部门禁=%{customdata[3]}<extra></extra>"
                    ),
                ),
                row=row_number,
                col=column,
            )
        figure.update_xaxes(title_text="d（%，0=关闭）", row=row_number, col=column)
        figure.update_yaxes(title_text=unit, row=row_number, col=column)
    figure.update_layout(
        height=850,
        margin={"l": 65, "r": 30, "t": 80, "b": 60},
        legend={"orientation": "h", "y": -0.10},
    )
    return figure


def surface_figure(
    results: pd.DataFrame,
    representatives: pd.DataFrame,
    surfaces: dict[str, Any],
) -> go.Figure:
    titles: list[str] = []
    for rebuy in FORCED_REBUY_VALUES:
        for stop in STOP_VALUES:
            representative = _representative_row(
                representatives,
                forced_rebuy_pct=rebuy,
                stop_loss_pct=stop,
            )
            state = "✓" if bool(representative["structural_pass"]) else "×"
            titles.append(f"c={rebuy:g}%，d={stop_label(stop)} · 结构{state}")
    figure = make_subplots(
        rows=4,
        cols=5,
        subplot_titles=titles,
        horizontal_spacing=0.035,
        vertical_spacing=0.055,
    )
    eligible = results.loc[results["identifiable"], "primary_metric"].astype(float)
    cmin = float(eligible.quantile(0.02))
    cmax = float(eligible.quantile(0.98))
    for row_number, rebuy in enumerate(FORCED_REBUY_VALUES, start=1):
        for column, stop in enumerate(STOP_VALUES, start=1):
            selected = results[np.isclose(results["forced_rebuy_pct"], rebuy)]
            if stop is None:
                selected = selected[selected["stop_loss_pct"].isna()]
            else:
                selected = selected[np.isclose(selected["stop_loss_pct"], stop)]
            pivot = (
                selected.pivot(
                    index="buy_window",
                    columns="sell_window",
                    values="primary_metric",
                )
                .sort_index()
                .sort_index(axis=1)
            )
            figure.add_trace(
                go.Heatmap(
                    x=pivot.columns,
                    y=pivot.index,
                    z=pivot.to_numpy(float),
                    coloraxis="coloraxis",
                    hovertemplate=(
                        "买入 SMA=%{y}<br>卖出 SMA=%{x}"
                        "<br>三段最差 CAGR=%{z:.4f}%<extra></extra>"
                    ),
                ),
                row=row_number,
                col=column,
            )
            mode = f"c{rebuy:g}_d{'off' if stop is None else f'{stop:g}'}"
            cells = surfaces[mode]["largest_component"]["cells"]
            figure.add_trace(
                go.Scatter(
                    x=[item["sell_window"] for item in cells],
                    y=[item["buy_window"] for item in cells],
                    mode="markers",
                    marker={"size": 3, "color": "white", "opacity": 0.55},
                    showlegend=False,
                    hoverinfo="skip",
                ),
                row=row_number,
                col=column,
            )
            representative = _representative_row(
                representatives,
                forced_rebuy_pct=rebuy,
                stop_loss_pct=stop,
            )
            figure.add_trace(
                go.Scatter(
                    x=[representative["sell_window"]],
                    y=[representative["buy_window"]],
                    mode="markers",
                    marker={
                        "symbol": "star" if bool(representative["promotion_gate_pass"]) else "circle-open",
                        "size": 12,
                        "color": "white",
                        "line": {"color": "#111827", "width": 2},
                    },
                    showlegend=False,
                    hovertemplate=(
                        "曲面代表<br>买入 SMA=%{y}<br>卖出 SMA=%{x}"
                        f"<br>结构门禁={'通过' if bool(representative['structural_pass']) else '未通过'}"
                        f"<br>全部门禁={'通过' if bool(representative['promotion_gate_pass']) else '未通过'}"
                        "<extra></extra>"
                    ),
                ),
                row=row_number,
                col=column,
            )
            if row_number == 4:
                figure.update_xaxes(title_text="卖出 SMA", row=row_number, col=column)
            if column == 1:
                figure.update_yaxes(title_text="买入 SMA", row=row_number, col=column)
    figure.update_layout(
        height=1700,
        margin={"l": 70, "r": 85, "t": 90, "b": 65},
        coloraxis={
            "colorscale": "RdYlGn",
            "cmin": cmin,
            "cmax": cmax,
            "colorbar": {"title": "三段最差<br>CAGR %"},
        },
    )
    return figure


def subperiod_figure(representatives: pd.DataFrame) -> go.Figure:
    periods = (
        ("P1_2000_2005_cagr_pct", "2000-12～2005", "#2563eb"),
        ("P2_2006_2010_cagr_pct", "2006～2010", "#d97706"),
        ("P3_2011_2015_cagr_pct", "2011～2015", "#0f766e"),
        ("primary_metric", "三段最差值", "#111827"),
    )
    figure = make_subplots(
        rows=2,
        cols=2,
        subplot_titles=[f"c={value:g}%" for value in FORCED_REBUY_VALUES],
        horizontal_spacing=0.10,
        vertical_spacing=0.15,
    )
    for index, rebuy in enumerate(FORCED_REBUY_VALUES):
        row_number = index // 2 + 1
        column = index % 2 + 1
        selected = representatives[
            np.isclose(representatives["forced_rebuy_pct"], rebuy)
        ].copy()
        selected["d_axis"] = selected["stop_loss_pct"].fillna(0.0)
        selected = selected.sort_values("d_axis")
        for field, label, color in periods:
            figure.add_trace(
                go.Scatter(
                    x=selected["d_axis"],
                    y=selected[field],
                    mode="lines+markers",
                    name=label,
                    legendgroup=label,
                    showlegend=index == 0,
                    line={"color": color, "width": 2.4 if field == "primary_metric" else 1.8},
                    hovertemplate=(
                        f"c={rebuy:g}%，d=%{{x:g}}%（0=关闭）"
                        f"<br>{label} CAGR=%{{y:.4f}}%<extra></extra>"
                    ),
                ),
                row=row_number,
                col=column,
            )
        figure.add_hline(y=0, line={"color": "#64748b", "dash": "dash"}, row=row_number, col=column)
        figure.update_xaxes(title_text="d（%，0=关闭）", row=row_number, col=column)
        figure.update_yaxes(title_text="CAGR（%）", row=row_number, col=column)
    figure.update_layout(
        height=850,
        margin={"l": 65, "r": 25, "t": 80, "b": 60},
        legend={"orientation": "h", "y": -0.10},
    )
    return figure


def paired_stop_summary(paired: pd.DataFrame, *, activity_minimum: int) -> pd.DataFrame:
    enabled = paired[paired["stop_loss_pct"].notna()].copy()
    records: list[dict[str, Any]] = []
    for (rebuy, stop), group in enabled.groupby(
        ["forced_rebuy_pct", "stop_loss_pct"], sort=True
    ):
        primary = group["delta_primary_metric"].astype(float)
        cagr = group["delta_cagr_pct"].astype(float)
        records.append(
            {
                "forced_rebuy_pct": float(rebuy),
                "stop_loss_pct": float(stop),
                "period_pair_count": len(group),
                "median_delta_primary_metric": float(primary.median()),
                "q25_delta_primary_metric": float(primary.quantile(0.25)),
                "q75_delta_primary_metric": float(primary.quantile(0.75)),
                "primary_improvement_rate": float((primary > 0.0).mean()),
                "median_delta_cagr_pct": float(cagr.median()),
                "cagr_improvement_rate": float((cagr > 0.0).mean()),
                "median_stop_loss_count": float(group["stop_loss_count"].median()),
                "activity_gate_pass_rate": float(
                    (group["stop_loss_count"] >= int(activity_minimum)).mean()
                ),
            }
        )
    return pd.DataFrame(records)


def paired_stop_figure(summary: pd.DataFrame) -> go.Figure:
    metrics = (
        ("median_delta_primary_metric", "三段最差 CAGR 的中位变化", "%"),
        ("primary_improvement_rate", "改善三段最差 CAGR 的周期对比例", "%"),
        ("median_delta_cagr_pct", "全历史 CAGR 的中位变化", "%"),
        ("activity_gate_pass_rate", "止损主触发≥10次的周期对比例", "%"),
    )
    figure = make_subplots(rows=2, cols=2, subplot_titles=[item[1] for item in metrics])
    for metric_index, (metric, _, unit) in enumerate(metrics):
        row_number = metric_index // 2 + 1
        column = metric_index % 2 + 1
        for rebuy in FORCED_REBUY_VALUES:
            selected = summary[
                np.isclose(summary["forced_rebuy_pct"], rebuy)
            ].sort_values("stop_loss_pct")
            y = selected[metric] * 100.0 if metric.endswith("_rate") else selected[metric]
            error_y: dict[str, Any] | None = None
            if metric == "median_delta_primary_metric":
                error_y = {
                    "type": "data",
                    "symmetric": False,
                    "array": selected["q75_delta_primary_metric"] - selected[metric],
                    "arrayminus": selected[metric] - selected["q25_delta_primary_metric"],
                }
            figure.add_trace(
                go.Scatter(
                    x=selected["stop_loss_pct"],
                    y=y,
                    mode="lines+markers",
                    name=f"c={rebuy:g}%",
                    legendgroup=f"c{rebuy:g}",
                    showlegend=metric_index == 0,
                    line={"color": COLORS[rebuy], "width": 2.2},
                    error_y=error_y,
                    hovertemplate=(
                        f"c={rebuy:g}%，d=%{{x:g}}%<br>{metric}=%{{y:.4f}} {unit}"
                        "<extra></extra>"
                    ),
                ),
                row=row_number,
                col=column,
            )
        figure.update_xaxes(title_text="d（%）", row=row_number, col=column)
        figure.update_yaxes(title_text=unit, row=row_number, col=column)
        if metric in {"median_delta_primary_metric", "median_delta_cagr_pct"}:
            figure.add_hline(
                y=0,
                line={"color": "#64748b", "dash": "dash"},
                row=row_number,
                col=column,
            )
    figure.update_layout(
        height=820,
        margin={"l": 70, "r": 25, "t": 80, "b": 60},
        legend={"orientation": "h", "y": -0.10},
    )
    return figure


def multiple_testing_figure(
    splits: pd.DataFrame,
    dsr: pd.DataFrame,
    representatives: pd.DataFrame,
    pbo: dict[str, Any],
) -> go.Figure:
    figure = make_subplots(
        rows=1,
        cols=2,
        subplot_titles=("CSCV 样本外排名（PBO）", "20 个曲面代表的 DSR"),
        horizontal_spacing=0.12,
    )
    figure.add_trace(
        go.Histogram(
            x=splits["oos_rank_percentile"] * 100.0,
            nbinsx=25,
            marker={"color": "#2563eb"},
            hovertemplate="样本外排名 %{x:.1f}百分位<br>切分数 %{y}<extra></extra>",
        ),
        row=1,
        col=1,
    )
    figure.add_vline(x=50, line={"color": "#dc2626", "dash": "dash"}, row=1, col=1)
    labels = representatives.set_index("case_id").apply(mode_label, axis=1).to_dict()
    policies = (
        ("effective_correlated_trials", "相关性折算试验数", "#0f766e", "circle"),
        ("all_28880_cases", "全部28,880组", "#7c3aed", "diamond-open"),
    )
    case_order = representatives.sort_values(
        ["forced_rebuy_pct", "stop_loss_pct"], na_position="first"
    )["case_id"].astype(str).tolist()
    for policy, label, color, symbol in policies:
        selected = dsr[dsr["trial_policy"] == policy].set_index("case_id").loc[case_order]
        figure.add_trace(
            go.Scatter(
                x=[labels[item] for item in case_order],
                y=selected["probability"] * 100.0,
                mode="markers",
                name=label,
                marker={"color": color, "size": 9, "symbol": symbol},
                hovertemplate=f"{label}<br>%{{x}}<br>DSR=%{{y:.3f}}%<extra></extra>",
            ),
            row=1,
            col=2,
        )
    figure.add_hline(y=95, line={"color": "#dc2626", "dash": "dash"}, row=1, col=2)
    figure.add_annotation(
        text=f"PBO={float(pbo['pbo']):.2%}；{int(pbo['split_count'])}个切分",
        xref="x domain",
        yref="y domain",
        x=0.5,
        y=1.08,
        showarrow=False,
        row=1,
        col=1,
    )
    figure.update_xaxes(title_text="样本外排名百分位", row=1, col=1)
    figure.update_yaxes(title_text="切分数", row=1, col=1)
    figure.update_xaxes(title_text="纠错模式", tickangle=-55, row=1, col=2)
    figure.update_yaxes(title_text="DSR 概率（%）", range=[0, 102], row=1, col=2)
    figure.update_layout(height=700, margin={"l": 65, "r": 25, "t": 85, "b": 150})
    return figure


def market_figure(
    prices: pd.DataFrame,
    plans: pd.DataFrame,
    orders: pd.DataFrame,
    *,
    buy_window: int,
    sell_window: int,
) -> go.Figure:
    frame = prices.merge(
        plans[["date", "side", "sma_trigger", "correction_trigger"]],
        on="date",
        how="left",
    )
    frame["buy_sma_trigger"] = frame["sma_trigger"].where(frame["side"] == "buy")
    frame["sell_sma_trigger"] = frame["sma_trigger"].where(frame["side"] == "sell")
    frame["forced_rebuy_trigger"] = frame["correction_trigger"].where(
        frame["side"] == "buy"
    )
    frame["stop_loss_trigger"] = frame["correction_trigger"].where(
        frame["side"] == "sell"
    )
    figure = go.Figure()
    figure.add_trace(
        go.Candlestick(
            x=frame["date"],
            open=frame["open"],
            high=frame["high"],
            low=frame["low"],
            close=frame["close"],
            name="QQQ 复权 OHLC",
            increasing_line_color="#1b7f5a",
            decreasing_line_color="#c2413b",
        )
    )
    overlays = (
        ("close", "QQQ Close", "#111827", "solid"),
        (f"sma_{buy_window}", f"买入 SMA{buy_window}", "#16a34a", "solid"),
        (f"sma_{sell_window}", f"卖出 SMA{sell_window}", "#dc2626", "solid"),
        ("buy_sma_trigger", "当日普通买入动态线", "#16a34a", "dash"),
        ("sell_sma_trigger", "当日普通卖出动态线", "#dc2626", "dash"),
        ("forced_rebuy_trigger", "强制买回线", "#0891b2", "dot"),
        ("stop_loss_trigger", "止损线", "#9333ea", "dot"),
    )
    seen_columns: set[str] = set()
    for column, label, color, dash in overlays:
        if column in seen_columns:
            continue
        seen_columns.add(column)
        figure.add_trace(
            go.Scatter(
                x=frame["date"],
                y=frame[column],
                mode="lines",
                name=label,
                line={"color": color, "width": 1.8, "dash": dash},
                connectgaps=False,
                meta={
                    "series_key": column,
                    "panel": "market",
                    "label": label,
                    "control_group": "period_lines",
                    "control_group_label": "收盘均线、当日动态线与纠错线",
                },
            )
        )
    for side, color, marker, label in (
        ("buy", "#087f5b", "triangle-up", "买入成交"),
        ("sell", "#c92a2a", "triangle-down", "卖出成交"),
    ):
        selected = orders[orders["type"] == side].copy()
        custom = (
            np.column_stack(
                [
                    selected["primary_signal"].map(SIGNAL_LABELS),
                    selected["theoretical_trigger"],
                    selected["raw_fill_price"],
                    selected["fill_source"],
                ]
            )
            if len(selected)
            else np.empty((0, 4))
        )
        figure.add_trace(
            go.Scatter(
                x=selected["date"],
                y=selected["fill_price"],
                customdata=custom,
                mode="markers",
                name=label,
                marker={"color": color, "symbol": marker, "size": 10},
                meta={
                    "series_key": f"trade_{side}",
                    "panel": "market",
                    "label": label,
                    "control_group": "period_lines",
                    "control_group_label": "收盘均线、当日动态线与纠错线",
                },
                hovertemplate=(
                    f"{label}<br>%{{x|%Y-%m-%d}}<br>账户成交 $%{{y:.4f}}"
                    "<br>原因 %{customdata[0]}<br>理论线 $%{customdata[1]:.4f}"
                    "<br>原始成交 $%{customdata[2]:.4f}<br>方式 %{customdata[3]}<extra></extra>"
                ),
            )
        )
    figure.update_layout(
        height=780,
        margin={"l": 65, "r": 25, "t": 55, "b": 55},
        hovermode="x unified",
        showlegend=False,
        uirevision="qqq-sma-period-cross-market-v1",
    )
    figure.update_xaxes(
        rangebreaks=[{"bounds": ["sat", "mon"]}],
        rangeslider={"visible": True, "thickness": 0.08},
    )
    figure.update_yaxes(title_text="复权价格（USD）", fixedrange=False)
    return figure


def representative_table(representatives: pd.DataFrame) -> str:
    lines = [
        "| c | d | 买/卖 SMA | 三段最差 CAGR | 全史 CAGR | Sharpe | 最大回撤 | 止损触发 | 结构 | PBO/DSR总门禁 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    ordered = representatives.sort_values(
        ["forced_rebuy_pct", "stop_loss_pct"], na_position="first"
    )
    for row in ordered.itertuples(index=False):
        lines.append(
            f"| {row.forced_rebuy_pct:g}% | {stop_label(row.stop_loss_pct)} | "
            f"{int(row.buy_window)}/{int(row.sell_window)} | {row.primary_metric:.3f}% | "
            f"{row.cagr_pct:.3f}% | {row.sharpe:.3f} | {row.max_drawdown_pct:.2f}% | "
            f"{int(row.stop_loss_count)} | {'通过' if row.structural_pass else '未通过'} | "
            f"{'通过' if row.promotion_gate_pass else '未通过'} |"
        )
    return "\n".join(lines)


def markdown_report(
    run_id: str,
    summary: dict[str, Any],
    representatives: pd.DataFrame,
) -> str:
    qualifying = representatives[representatives["promotion_gate_pass"]]
    structural = int(representatives["structural_pass"].sum())
    positive = int((representatives["primary_metric"] > 0.0).sum())
    pbo = float(summary["pbo"]["pbo"])
    benchmark = summary["benchmark_metrics"]
    if len(qualifying):
        verdict = f"{len(qualifying)} 个模式代表通过历史诊断门禁，只能进入未来锁定验证"
    else:
        verdict = "没有模式代表通过全部门禁，本轮不提名唯一买卖均线组合"
    return "\n".join(
        [
            "# QQQ 独立买卖 SMA 周期与纠错网格",
            "",
            f"> Run `{run_id}`；QQQ 2000-12-18～2015-12-31，28,880 组探索性扫描。",
            "",
            "## 结论",
            "",
            f"- 最终判定：**{verdict}**。",
            f"- 20 个纠错模式中，{structural} 个最大高分连通区通过二维结构门禁，{positive} 个代表的三段最差 CAGR 为正。",
            f"- 全网格 CSCV PBO={pbo:.2%}（预登记门槛≤20%）。DSR 同时报告相关性折算与全部 28,880 次试验口径。",
            f"- QQQ Buy & Hold：CAGR {benchmark['cagr_pct']:.3f}%，Sharpe {benchmark['sharpe']:.3f}，最大回撤 {benchmark['max_drawdown_pct']:.2f}%。",
            "- 下表的每一行是机械规则确定的曲面代表；门禁未通过时，它只是诊断锚点，不是推荐参数。",
            "",
            "## 20 个曲面代表",
            "",
            representative_table(representatives),
            "",
            "## 口径与边界",
            "",
            "- 买入与卖出 SMA 各自从80到450、步长10；c=3/5/8/10%，d=关闭/3/5/8/10%。",
            "- 每日开盘前用完成历史解出当日精确动态 SMA 交叉价；跳空越线按 Open，否则按日内精确线，随后施加单边5 bps。",
            "- P1/P2/P3继承持仓、现金、成本价和纠错锚；主指标是三段 CAGR 的最小值。",
            "- d 开启后的保护作用只有在该 case 至少10次以止损为主成交原因时才可解释；否则仅是低频保险描述。",
            "- 本轮全部数据都属于探索期；即使历史门禁通过，也不能替代2016年以后预先锁定的验证。",
            "- 使用拆股及股息调整 OHLC，是总回报价格近似，不是原始成交价与现金股息的账户级重放。",
        ]
    ) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()

    context = load_experiment(args.experiment)
    record = load_run(context, args.run_id)
    incomplete = [
        item["block_id"] for item in record["expected_blocks"] if item["status"] != "completed"
    ]
    if incomplete:
        raise RuntimeError(f"Cannot analyze incomplete run: {incomplete}")
    run_root = context.run_root(args.run_id)
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(parents=True, exist_ok=True)
    block = block_root(context, args.run_id, "QQQ", 5.0)

    results = pd.read_csv(block / "parameter_results.csv")
    formal = pd.read_csv(block / "formal_candidate_results.csv")
    daily = pd.read_csv(block / "daily.csv", parse_dates=["date"])
    benchmark = pd.read_csv(block / "buy_hold_daily.csv", parse_dates=["date"])
    paired = pd.read_csv(block / "paired_stop_loss_deltas.csv")
    pbo_splits = pd.read_csv(block / "pbo_splits.csv")
    dsr = pd.read_csv(block / "dsr_results.csv")
    orders = pd.read_csv(block / "orders.csv", parse_dates=["date"])
    plans = pd.read_csv(block / "signal_plans.csv", parse_dates=["date"])
    selection = json.loads((block / "selection_summary.json").read_text(encoding="utf-8"))
    representatives = pd.DataFrame(selection["stable_representatives"])
    if len(representatives) != 20:
        raise AssertionError(f"Expected 20 correction-mode representatives, found {len(representatives)}.")
    period_columns = [
        column
        for column in formal.columns
        if column.startswith("P") and column.endswith(("_cagr_pct", "_sharpe"))
    ]
    representatives = representatives.merge(
        formal[["case_id", *period_columns]], on="case_id", how="left", validate="one_to_one"
    )
    if representatives[period_columns].isna().any().any():
        raise AssertionError("Surface representatives are missing formal subperiod metrics.")

    activity_minimum = int(
        context.config["parameters"]["identifiability_gate"]["stop_loss_activity_count"]
    )
    paired_summary = paired_stop_summary(paired, activity_minimum=activity_minimum)
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    summary = {
        **selection,
        "created_at_utc": created_at,
        "formal_candidates": json_safe(formal.to_dict("records")),
        "paired_stop_loss_summary": json_safe(paired_summary.to_dict("records")),
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    promotion_count = int(representatives["promotion_gate_pass"].sum())
    structural_count = int(representatives["structural_pass"].sum())
    pbo_pass = float(selection["pbo"]["pbo"]) <= float(
        context.config["parameters"]["multiple_testing"]["pbo_maximum"]
    )
    verdict = (
        f"{promotion_count} 个模式可进入未来锁定验证"
        if promotion_count
        else "无模式通过全部门禁，不提名唯一参数"
    )
    compact = (
        f"<p>完整扫描 <strong>{len(results):,}</strong> 组、20 个 c/d 曲面；"
        f"有 <strong>{structural_count}</strong> 个曲面通过二维平台结构门禁，"
        f"<strong>{promotion_count}</strong> 个代表通过全部历史诊断门禁。"
        f"PBO <strong>{float(selection['pbo']['pbo']):.2%}</strong>"
        f"（{'通过' if pbo_pass else '未通过'}）；最终判定："
        f"<strong>{html.escape(verdict)}</strong>。本轮是2000～2015探索，不直接产生未来最优参数。</p>"
    )

    display = _representative_row(
        representatives,
        forced_rebuy_pct=5.0,
        stop_loss_pct=5.0,
    )
    display_case = str(display["case_id"])
    raw = pd.read_csv(WORKSPACE_ROOT / "data/processed/daily/QQQ.csv", parse_dates=["date"])
    prices = prepare_sma_period_data(
        raw,
        [int(display["buy_window"]), int(display["sell_window"])],
    )
    start = pd.Timestamp(context.config["parameters"]["analysis_start"])
    end = pd.Timestamp(context.config["parameters"]["analysis_end"])
    prices = prices[(prices["date"] >= start) & (prices["date"] <= end)].reset_index(drop=True)
    display_orders = orders[orders["case_id"] == display_case].copy()
    display_plans = plans[plans["case_id"] == display_case].copy()

    figures = [
        ReportFigure(
            "performance-qqq",
            "20 个模式代表的净值与回撤",
            performance_figure(daily, benchmark, representatives),
            "performance",
        ),
        ReportFigure(
            "representative-metrics",
            "纠错模式代表：稳健收益、全史指标与触发次数",
            representative_metrics_figure(representatives),
            "generic",
        ),
        ReportFigure(
            "period-surfaces",
            "20 张买入/卖出 SMA 周期面与最大高分连通区",
            surface_figure(results, representatives, selection["surface_analysis"]),
            "generic",
        ),
        ReportFigure(
            "subperiod-robustness",
            "每个模式代表在三个继承状态分段中的 CAGR",
            subperiod_figure(representatives),
            "generic",
        ),
        ReportFigure(
            "paired-stop-loss",
            "同一买卖周期与 c 下，开启 d 相对关闭 d 的配对变化",
            paired_stop_figure(paired_summary),
            "generic",
        ),
        ReportFigure(
            "multiple-testing",
            "PBO 与两种试验数口径下的 DSR",
            multiple_testing_figure(
                pbo_splits,
                dsr,
                representatives,
                selection["pbo"],
            ),
            "generic",
        ),
        ReportFigure(
            "market-qqq",
            (
                f"诊断展示：c=5%、d=5% 曲面代表（买 SMA{int(display['buy_window'])} / "
                f"卖 SMA{int(display['sell_window'])}）"
            ),
            market_figure(
                prices,
                display_plans,
                display_orders,
                buy_window=int(display["buy_window"]),
                sell_window=int(display["sell_window"]),
            ),
            "market",
        ),
    ]
    report = render_interactive_report(
        title="QQQ 独立买卖 SMA 周期与纠错网格",
        heading="QQQ 独立买卖 SMA 周期与纠错网格",
        subtitle="80～450日买卖均线独立扫描；c=3/5/8/10%，d=关闭/3/5/8/10%；以三个继承状态分段的最差 CAGR 和二维平台为主。",
        summary_html=compact,
        notes=[
            "QQQ 2000-12-18～2015-12-31；初始空仓，单边5 bps，每日最多一笔，允许小数股。",
            "普通买入要求前一日位于买入SMA下方或相等后向上穿越；普通卖出要求前一日位于卖出SMA上方或相等后向下穿越。",
            "每个c/d模式独立分析38×38完整曲面；最大高分区触及80或450边界时不得提名稳定周期区。",
            "止损至少10次成为主成交原因才允许解释；PBO和DSR控制重复尝试风险，但不创造样本外证据。",
        ],
        figures=figures,
        experiment=context.config,
        run_id=args.run_id,
        template_id=str(context.config["reporting"]["template_id"]),
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")
    (run_root / "report.md").write_text(
        markdown_report(args.run_id, selection, representatives),
        encoding="utf-8",
    )

    template_path = str(context.config["reporting"]["template_path"])
    tracked = [
        "backtest/requirements.lock",
        "backtest/quantkit/execution.py",
        "backtest/quantkit/experiment.py",
        "backtest/quantkit/intraday_sma_period_cross.py",
        "backtest/quantkit/intraday_sma_period_cross_search.py",
        "backtest/quantkit/intraday_sma_threshold_selection.py",
        "backtest/quantkit/metrics.py",
        "backtest/quantkit/reporting.py",
        "backtest/quantkit/surface.py",
        "backtest/scripts/run_intraday_sma_backtest.py",
        "backtest/scripts/run_intraday_sma200_threshold_grid.py",
        "backtest/scripts/run_intraday_sma_period_cross_grid.py",
        "backtest/scripts/analyze_intraday_sma_period_cross_grid.py",
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

This immutable run contains the QQQ independent buy/sell SMA-period crossing grid.

- `report.html` / `report.md`: detailed surfaces, correction modes, subperiods and statistical gates.
- `analysis/summary.json`: machine-readable representatives, gates and paired stop-loss summary.
- `QQQ/cost_5bps/`: all 28,880 cases, two compiled-ledger comparisons, formal PyBroker/Python ledgers, PBO and DSR evidence.
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
        if path.is_file() and path.name not in {
            "artifact_manifest.json",
            "run.json",
            "validation.json",
        }:
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
