#!/usr/bin/env python3
"""Analyze and report the QQQ two-SMA recovery-probation grid."""

from __future__ import annotations

import argparse
import html
import json
import platform
import subprocess
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
    block_root,
    load_experiment,
    load_run,
    record_analysis_complete,
    sha256,
)
from quantkit.reporting import ReportFigure, render_interactive_report
from quantkit.sma_recovery_probation import (
    CONFIRMED_SMA_SELL,
    FORCED_PROBATION_FAILURE,
    FORCED_REBUY,
    ORDINARY_PROBATION_FAILURE,
    ORDINARY_SMA_BUY,
)
from scripts.run_intraday_sma_backtest import json_safe


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = (
    BACKTEST_ROOT
    / "experiments/TIM/TIM-v0.50b.1__26-08-21__qqq_sma_recovery_probation_grid_2000_2015"
)
SIGNAL_LABELS = {
    ORDINARY_SMA_BUY: "长均线收盘上穿买入",
    FORCED_REBUY: "3% 强制买回",
    CONFIRMED_SMA_SELL: "确认多头下穿短均线卖出",
    ORDINARY_PROBATION_FAILURE: "普通恢复失败退出",
    FORCED_PROBATION_FAILURE: "强制买回失败退出",
}
COLORS = {
    "representative": "#2563eb",
    "anchor": "#d97706",
    "control": "#0f766e",
    "other": "#7c3aed",
    "benchmark": "#111827",
}


def drawdown(values: pd.Series | np.ndarray) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    return (array / np.maximum.accumulate(array) - 1.0) * 100.0


def _case_role(row: pd.Series | Any, representative_id: str) -> tuple[str, str]:
    if hasattr(row, "case_id"):
        case_id = str(row.case_id)
        buy = int(row.buy_window)
        sell = int(row.sell_window)
    else:
        case_id = str(row["case_id"])
        buy = int(row["buy_window"])
        sell = int(row["sell_window"])
    if case_id == representative_id:
        return "representative", f"稳定平台代表：买 SMA{buy} / 卖 SMA{sell}"
    if buy == 310 and sell == 190:
        return "anchor", "继承锚点：买 SMA310 / 卖 SMA190"
    if buy == 200 and sell == 200:
        return "control", "SMA200 / SMA200 对照"
    return "other", f"补充冠军：买 SMA{buy} / 卖 SMA{sell}"


def performance_figure(
    daily: pd.DataFrame,
    benchmark: pd.DataFrame,
    formal: pd.DataFrame,
    representative_id: str,
) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.69, 0.31],
        vertical_spacing=0.08,
        subplot_titles=("正式候选净值", "从各自历史峰值回撤"),
    )
    for row in formal.itertuples(index=False):
        role, label = _case_role(row, representative_id)
        frame = daily[daily["case_id"].astype(str).eq(str(row.case_id))].sort_values("date")
        if frame.empty:
            raise AssertionError(f"Missing formal daily ledger for {row.case_id}.")
        visible: bool | str = True if role in {"representative", "anchor"} else "legendonly"
        dash = "solid" if role != "anchor" else "dash"
        color = COLORS[role]
        for panel, values, row_number, showlegend in (
            ("equity", frame["equity"].to_numpy(float), 1, True),
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
                    line={"color": color, "width": 2.5, "dash": dash},
                    meta={"series_key": str(row.case_id), "panel": panel, "label": label},
                    hovertemplate=f"{label}<br>%{{x|%Y-%m-%d}}<br>%{{y:,.2f}}<extra></extra>",
                ),
                row=row_number,
                col=1,
            )
    benchmark = benchmark.sort_values("date")
    benchmark_label = "QQQ Buy & Hold（同起点、买入计 5 bps）"
    for panel, values, row_number, showlegend in (
        ("equity", benchmark["equity"].to_numpy(float), 1, True),
        ("drawdown", drawdown(benchmark["equity"]), 2, False),
    ):
        figure.add_trace(
            go.Scatter(
                x=benchmark["date"],
                y=values,
                mode="lines",
                name=benchmark_label,
                showlegend=showlegend,
                line={"color": COLORS["benchmark"], "width": 2.0, "dash": "dot"},
                meta={
                    "series_key": "buy_hold_5bps",
                    "panel": panel,
                    "label": benchmark_label,
                    "is_benchmark": panel == "equity",
                    "cost_bps": 5.0,
                },
                hovertemplate=f"{benchmark_label}<br>%{{x|%Y-%m-%d}}<br>%{{y:,.2f}}<extra></extra>",
            ),
            row=row_number,
            col=1,
        )
    figure.update_layout(
        height=800,
        margin={"l": 65, "r": 25, "t": 75, "b": 55},
        hovermode="x unified",
        showlegend=False,
        uirevision="qqq-sma-recovery-probation-performance-v1",
    )
    figure.update_yaxes(title_text="USD", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="%", fixedrange=False, row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def surface_figure(
    results: pd.DataFrame,
    surface: dict[str, Any],
    representative: pd.Series,
) -> go.Figure:
    eligible = results.loc[results["identifiable"].astype(bool), "primary_metric"].astype(float)
    pivot = (
        results.pivot(index="buy_window", columns="sell_window", values="primary_metric")
        .sort_index()
        .sort_index(axis=1)
    )
    figure = go.Figure(
        go.Heatmap(
            x=pivot.columns,
            y=pivot.index,
            z=pivot.to_numpy(float),
            colorscale="RdYlGn",
            zmin=float(eligible.quantile(0.02)),
            zmax=float(eligible.quantile(0.98)),
            colorbar={"title": "三段最差<br>CAGR %"},
            hovertemplate="卖出 SMA=%{x}<br>买入 SMA=%{y}<br>三段最差 CAGR=%{z:.4f}%<extra></extra>",
        )
    )
    cells = surface["largest_component"]["cells"]
    figure.add_trace(
        go.Scatter(
            x=[item["sell_window"] for item in cells],
            y=[item["buy_window"] for item in cells],
            mode="markers",
            marker={"size": 5, "color": "white", "opacity": 0.62},
            name="最大高分连通区",
            hoverinfo="skip",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=[int(representative["sell_window"])],
            y=[int(representative["buy_window"])],
            mode="markers",
            marker={"symbol": "star", "size": 17, "color": "#2563eb", "line": {"color": "white", "width": 2}},
            name="稳定平台代表",
            hovertemplate="稳定平台代表<br>卖 SMA=%{x}<br>买 SMA=%{y}<extra></extra>",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=[190],
            y=[310],
            mode="markers",
            marker={"symbol": "x", "size": 14, "color": "#d97706", "line": {"width": 2}},
            name="原 310/190 锚点",
            hovertemplate="原锚点<br>卖 SMA190<br>买 SMA310<extra></extra>",
        )
    )
    figure.update_layout(
        height=760,
        margin={"l": 70, "r": 70, "t": 55, "b": 60},
        xaxis_title="卖出 SMA 周期（日）",
        yaxis_title="买入 SMA 周期（日）",
    )
    return figure


def parent_delta_figure(comparison: pd.DataFrame) -> go.Figure:
    metrics = (
        ("delta_primary_metric", "三段最差 CAGR 变化", "%"),
        ("delta_cagr_pct", "全历史 CAGR 变化", "%"),
        ("delta_sharpe", "Sharpe 变化", ""),
        ("delta_max_drawdown_pct", "最大回撤变化（正=变浅）", "%"),
    )
    figure = make_subplots(rows=2, cols=2, subplot_titles=[item[1] for item in metrics])
    for index, (field, _, unit) in enumerate(metrics):
        row_number = index // 2 + 1
        column = index % 2 + 1
        pivot = (
            comparison.pivot(index="buy_window", columns="sell_window", values=field)
            .sort_index()
            .sort_index(axis=1)
        )
        bound = float(np.nanquantile(np.abs(pivot.to_numpy(float)), 0.98))
        if not np.isfinite(bound) or bound == 0:
            bound = 1.0
        figure.add_trace(
            go.Heatmap(
                x=pivot.columns,
                y=pivot.index,
                z=pivot.to_numpy(float),
                colorscale="RdBu",
                reversescale=True,
                zmid=0.0,
                zmin=-bound,
                zmax=bound,
                showscale=index == 0,
                colorbar={"title": f"变化 {unit}"} if index == 0 else None,
                hovertemplate=(
                    f"卖出 SMA=%{{x}}<br>买入 SMA=%{{y}}<br>{field}=%{{z:.4f}} {unit}<extra></extra>"
                ),
            ),
            row=row_number,
            col=column,
        )
        figure.add_trace(
            go.Scatter(
                x=[190],
                y=[310],
                mode="markers",
                marker={"symbol": "x", "size": 11, "color": "#111827"},
                showlegend=False,
                hoverinfo="skip",
            ),
            row=row_number,
            col=column,
        )
        figure.update_xaxes(title_text="卖出 SMA", row=row_number, col=column)
        figure.update_yaxes(title_text="买入 SMA", row=row_number, col=column)
    figure.update_layout(height=1050, margin={"l": 70, "r": 70, "t": 75, "b": 65})
    return figure


def subperiod_figure(formal: pd.DataFrame, representative_id: str) -> go.Figure:
    selected = formal[
        formal["case_id"].astype(str).eq(representative_id)
        | ((formal["buy_window"] == 310) & (formal["sell_window"] == 190))
        | ((formal["buy_window"] == 200) & (formal["sell_window"] == 200))
    ].copy()
    periods = (
        ("P1_2000_2005_cagr_pct", "2000-12～2005"),
        ("P2_2006_2010_cagr_pct", "2006～2010"),
        ("P3_2011_2015_cagr_pct", "2011～2015"),
    )
    figure = go.Figure()
    for row in selected.itertuples(index=False):
        role, label = _case_role(row, representative_id)
        figure.add_trace(
            go.Bar(
                x=[item[1] for item in periods],
                y=[float(getattr(row, item[0])) for item in periods],
                name=label,
                marker_color=COLORS[role],
                hovertemplate=f"{label}<br>%{{x}}<br>CAGR=%{{y:.4f}}%<extra></extra>",
            )
        )
    figure.add_hline(y=0.0, line={"color": "#64748b", "dash": "dash"})
    figure.update_layout(
        barmode="group",
        height=580,
        margin={"l": 65, "r": 25, "t": 55, "b": 65},
        yaxis_title="CAGR（%）",
    )
    return figure


def multiple_testing_figure(
    splits: pd.DataFrame,
    dsr: pd.DataFrame,
    pbo: dict[str, Any],
) -> go.Figure:
    figure = make_subplots(
        rows=1,
        cols=2,
        subplot_titles=("CSCV 样本外排名（PBO）", "稳定平台代表的 DSR"),
        horizontal_spacing=0.14,
    )
    figure.add_trace(
        go.Histogram(
            x=splits["oos_rank_percentile"].astype(float) * 100.0,
            nbinsx=20,
            marker={"color": "#2563eb"},
            hovertemplate="样本外排名百分位=%{x:.1f}<br>切分数=%{y}<extra></extra>",
        ),
        row=1,
        col=1,
    )
    labels = {
        "effective_correlated_trials": "相关性折算试验数",
        "all_1444_cases": "全部 1,444 组",
    }
    figure.add_trace(
        go.Bar(
            x=[labels.get(str(value), str(value)) for value in dsr["trial_policy"]],
            y=dsr["probability"].astype(float) * 100.0,
            marker_color=["#0f766e", "#7c3aed"],
            hovertemplate="%{x}<br>DSR=%{y:.4f}%<extra></extra>",
        ),
        row=1,
        col=2,
    )
    figure.add_hline(y=95.0, line={"color": "#dc2626", "dash": "dash"}, row=1, col=2)
    figure.add_annotation(
        text=f"PBO={float(pbo['pbo']):.2%}；{int(pbo['split_count'])} 个切分",
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
    figure.update_yaxes(title_text="DSR 概率（%）", range=[0, 102], row=1, col=2)
    figure.update_layout(height=590, margin={"l": 65, "r": 35, "t": 75, "b": 90}, showlegend=False)
    return figure


def market_figure(
    raw: pd.DataFrame,
    reference_daily: pd.DataFrame,
    plans: pd.DataFrame,
    orders: pd.DataFrame,
    *,
    buy_window: int,
    sell_window: int,
) -> go.Figure:
    start = pd.Timestamp(reference_daily["date"].min())
    end = pd.Timestamp(reference_daily["date"].max())
    prices = raw.sort_values("date").copy()
    prices[f"sma_{buy_window}"] = prices["close"].rolling(buy_window).mean()
    prices[f"sma_{sell_window}"] = prices["close"].rolling(sell_window).mean()
    prices = prices[(prices["date"] >= start) & (prices["date"] <= end)].copy()
    plan_lines = plans[["date", "candidate_signal", "trigger_price"]].copy()
    plan_lines["sell_trigger"] = plan_lines["trigger_price"].where(
        plan_lines["candidate_signal"].eq(CONFIRMED_SMA_SELL)
    )
    plan_lines["c_trigger"] = plan_lines["trigger_price"].where(
        plan_lines["candidate_signal"].eq(FORCED_REBUY)
    )
    prices = prices.merge(plan_lines[["date", "sell_trigger", "c_trigger"]], on="date", how="left")
    figure = go.Figure()
    figure.add_trace(
        go.Candlestick(
            x=prices["date"],
            open=prices["open"],
            high=prices["high"],
            low=prices["low"],
            close=prices["close"],
            name="QQQ 复权 OHLC",
            increasing_line_color="#1b7f5a",
            decreasing_line_color="#c2413b",
        )
    )
    for column, label, color, dash in (
        (f"sma_{buy_window}", f"买入 SMA{buy_window}", "#16a34a", "solid"),
        (f"sma_{sell_window}", f"卖出 SMA{sell_window}", "#dc2626", "solid"),
        ("sell_trigger", "盘前冻结短均线卖出线", "#ef4444", "dash"),
        ("c_trigger", "一次性 3% 强制买回线", "#0891b2", "dot"),
    ):
        figure.add_trace(
            go.Scatter(
                x=prices["date"],
                y=prices[column],
                mode="lines",
                name=label,
                line={"color": color, "width": 1.8, "dash": dash},
                connectgaps=False,
                meta={
                    "series_key": column,
                    "panel": "market",
                    "label": label,
                    "control_group": "state_lines",
                    "control_group_label": "均线、预冻结触发线与成交",
                },
            )
        )
    marker_styles = {
        ORDINARY_SMA_BUY: ("#16a34a", "triangle-up"),
        FORCED_REBUY: ("#0891b2", "triangle-up"),
        CONFIRMED_SMA_SELL: ("#dc2626", "triangle-down"),
        ORDINARY_PROBATION_FAILURE: ("#f97316", "triangle-down"),
        FORCED_PROBATION_FAILURE: ("#7c3aed", "triangle-down"),
    }
    for signal, (color, marker) in marker_styles.items():
        selected = orders[orders["primary_signal"].eq(signal)]
        custom = (
            np.column_stack(
                [selected["raw_fill_price"], selected["fill_source"], selected["state_after_fill"]]
            )
            if len(selected)
            else np.empty((0, 3))
        )
        figure.add_trace(
            go.Scatter(
                x=selected["date"],
                y=selected["fill_price"],
                customdata=custom,
                mode="markers",
                name=SIGNAL_LABELS[signal],
                marker={"color": color, "symbol": marker, "size": 10},
                meta={
                    "series_key": f"trade_{signal.lower()}",
                    "panel": "market",
                    "label": SIGNAL_LABELS[signal],
                    "control_group": "state_lines",
                    "control_group_label": "均线、预冻结触发线与成交",
                },
                hovertemplate=(
                    f"{SIGNAL_LABELS[signal]}<br>%{{x|%Y-%m-%d}}"
                    "<br>计成本成交 $%{y:.4f}<br>原始成交 $%{customdata[0]:.4f}"
                    "<br>方式 %{customdata[1]}<br>成交后状态 %{customdata[2]}<extra></extra>"
                ),
            )
        )
    events = reference_daily[reference_daily["state_event"].astype(str).ne("")].copy()
    if len(events):
        events["event_label"] = events["state_event"].map(
            {
                "probation_confirmed": "试探仓升级为确认多头",
                "forced_to_ordinary_probation": "强制试探转为普通试探",
                "ordinary_recovery_failed": "普通恢复失败（待次日退出）",
                "forced_rebuy_failed": "强制买回失败（待次日退出）",
            }
        ).fillna(events["state_event"])
        figure.add_trace(
            go.Scatter(
                x=events["date"],
                y=events["close"],
                customdata=events[["event_label", "position_state"]].to_numpy(),
                mode="markers",
                name="收盘状态转换",
                marker={"color": "#111827", "symbol": "diamond-open", "size": 8},
                meta={
                    "series_key": "state_events",
                    "panel": "market",
                    "label": "收盘状态转换",
                    "control_group": "state_lines",
                    "control_group_label": "均线、预冻结触发线与成交",
                },
                hovertemplate=(
                    "%{x|%Y-%m-%d}<br>%{customdata[0]}<br>收盘后状态 %{customdata[1]}<extra></extra>"
                ),
            )
        )
    figure.update_layout(
        height=790,
        margin={"l": 65, "r": 25, "t": 55, "b": 55},
        hovermode="x unified",
        showlegend=False,
        uirevision="qqq-sma-recovery-probation-market-v1",
    )
    figure.update_xaxes(
        rangebreaks=[{"bounds": ["sat", "mon"]}],
        rangeslider={"visible": True, "thickness": 0.08},
    )
    figure.update_yaxes(title_text="复权价格（USD）", fixedrange=False)
    return figure


def _metric_table(
    formal: pd.DataFrame,
    comparison: pd.DataFrame,
    representative_id: str,
) -> str:
    selected = formal.copy().merge(
        comparison[
            [
                "case_id",
                "parent_cagr_pct",
                "parent_sharpe",
                "parent_max_drawdown_pct",
                "delta_primary_metric",
                "delta_order_count",
            ]
        ],
        on="case_id",
        how="left",
        validate="one_to_one",
    )
    rows: list[str] = []
    for row in selected.itertuples(index=False):
        role, label = _case_role(row, representative_id)
        if role == "other" and "CHAMPION" not in str(row.selection_reason):
            continue
        rows.append(
            "<tr>"
            f"<td>{html.escape(label)}</td><td>{int(row.buy_window)}/{int(row.sell_window)}</td>"
            f"<td>{float(row.primary_metric):.3f}%</td><td>{float(row.cagr_pct):.3f}%</td>"
            f"<td>{float(row.sharpe):.3f}</td><td>{float(row.max_drawdown_pct):.2f}%</td>"
            f"<td>{int(row.order_count)}</td><td>{float(row.delta_primary_metric):+.3f}点</td>"
            f"<td>{float(row.delta_order_count):+.0f}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>角色</th><th>买/卖 SMA</th><th>三段最差 CAGR</th>"
        "<th>全史 CAGR</th><th>Sharpe</th><th>最大回撤</th><th>成交</th>"
        "<th>较旧语义最差段变化</th><th>成交变化</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def markdown_report(
    run_id: str,
    selection: dict[str, Any],
    formal: pd.DataFrame,
    comparison: pd.DataFrame,
) -> str:
    representative = selection["stable_representative"]
    representative_id = str(representative["case_id"])
    anchor = selection["inherited_310_190_comparison"]
    benchmark = selection["benchmark_metrics"]
    rows = [
        "| 角色 | 买/卖 SMA | 三段最差 CAGR | 全史 CAGR | Sharpe | 最大回撤 | 成交 | 较旧语义最差段变化 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    merged = formal.merge(
        comparison[["case_id", "delta_primary_metric"]],
        on="case_id",
        validate="one_to_one",
    )
    for row in merged.itertuples(index=False):
        role, label = _case_role(row, representative_id)
        rows.append(
            f"| {label} | {int(row.buy_window)}/{int(row.sell_window)} | "
            f"{row.primary_metric:.3f}% | {row.cagr_pct:.3f}% | {row.sharpe:.3f} | "
            f"{row.max_drawdown_pct:.2f}% | {int(row.order_count)} | {row.delta_primary_metric:+.3f}点 |"
        )
    verdict = (
        "历史诊断门禁通过，但本轮仍不能直接晋升，因为新语义复用了探索数据"
        if bool(selection["promotion_eligible"])
        else "历史诊断门禁未全部通过，不提名唯一参数"
    )
    return "\n".join(
        [
            "# QQQ 两均线恢复试探状态机：2000–2015 重新搜索",
            "",
            f"> Run `{run_id}`；1,444 个买/卖 SMA 周期对；c=3%，d关闭，单边成本5 bps。",
            "",
            "## 结论",
            "",
            f"- 判定：**{verdict}**。",
            f"- 稳定二维平台代表为买 SMA{int(representative['buy_window'])} / 卖 SMA{int(representative['sell_window'])}；三段最差 CAGR {float(representative['primary_metric']):.3f}%，全史 CAGR {float(representative['cagr_pct']):.3f}%，Sharpe {float(representative['sharpe']):.3f}，最大回撤 {float(representative['max_drawdown_pct']):.2f}%。",
            f"- 310/190 新语义：三段最差 CAGR {float(anchor['primary_metric']):.3f}%，全史 CAGR {float(anchor['cagr_pct']):.3f}%，Sharpe {float(anchor['sharpe']):.3f}，最大回撤 {float(anchor['max_drawdown_pct']):.2f}%；相对旧语义分别变化 {float(anchor['delta_primary_metric']):+.3f}点、{float(anchor['delta_cagr_pct']):+.3f}点、{float(anchor['delta_sharpe']):+.3f}、{float(anchor['delta_max_drawdown_pct']):+.3f}点。",
            f"- PBO={float(selection['pbo']['pbo']):.2%}；相关性折算试验数下 DSR={float(representative['dsr_effective_probability']):.2%}。",
            f"- Buy & Hold：CAGR {float(benchmark['cagr_pct']):.3f}%，Sharpe {float(benchmark['sharpe']):.3f}，最大回撤 {float(benchmark['max_drawdown_pct']):.2f}%。",
            "",
            "## 正式核对候选",
            "",
            *rows,
            "",
            "## 口径与边界",
            "",
            "- 普通恢复只认完成收盘上穿长均线，次日开盘买；任何新仓先是试探仓。",
            "- 普通试探仓若收盘重新跌破长均线，次日开盘退出；3%强制买回试探仓若收盘跌回原确认卖出计成本价下方，也在次日开盘退出。",
            "- 试探仓完成收盘站上短均线后才成为确认多头；确认多头盘中向下触及预先冻结的动态短均线时卖出。",
            "- P1/P2/P3继承现金、仓位、试探状态和一次性c使用状态；主指标是三个分段 CAGR 的最小值。",
            "- 本轮使用2000–2015反复研究过的数据，是语义修复后的重新探索，不是样本外验证。",
            "- 日线 OHLC 无法识别同一根K线内先触发买入还是先触发卖出；策略用收盘确认与次日成交消除这种不可辨识的日内顺序假设。",
        ]
    ) + "\n"


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
        raise RuntimeError(f"Cannot analyze incomplete run: {incomplete}")
    run_root = context.run_root(args.run_id)
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(parents=True, exist_ok=True)
    block = block_root(context, args.run_id, "QQQ", 5.0)

    results = pd.read_csv(block / "parameter_results.csv")
    formal = pd.read_csv(block / "formal_candidate_results.csv")
    daily = pd.read_csv(block / "daily.csv", parse_dates=["date"])
    reference_daily = pd.read_csv(block / "reference_daily.csv", parse_dates=["date"])
    benchmark = pd.read_csv(block / "buy_hold_daily.csv", parse_dates=["date"])
    comparison = pd.read_csv(block / "parent_control_comparison.csv")
    splits = pd.read_csv(block / "pbo_splits.csv")
    dsr = pd.read_csv(block / "dsr_results.csv")
    orders = pd.read_csv(block / "orders.csv", parse_dates=["date"])
    plans = pd.read_csv(block / "signal_plans.csv", parse_dates=["date"])
    selection = json.loads((block / "selection_summary.json").read_text(encoding="utf-8"))
    representative = pd.Series(selection["stable_representative"])
    representative_id = str(representative["case_id"])
    if len(results) != 1_444 or len(results[["buy_window", "sell_window"]].drop_duplicates()) != 1_444:
        raise AssertionError("Expected exactly 1,444 unique buy/sell SMA pairs.")
    if representative_id not in formal["case_id"].astype(str).tolist():
        raise AssertionError("Stable representative is missing from formal reconciliations.")

    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    analysis_summary = {
        **selection,
        "created_at_utc": created_at,
        "formal_candidates": json_safe(formal.to_dict("records")),
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(json_safe(analysis_summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    comparison.to_csv(analysis_root / "old_new_surface_comparison.csv", index=False, lineterminator="\n")
    formal.to_csv(analysis_root / "formal_candidates.csv", index=False, lineterminator="\n")

    anchor = selection["inherited_310_190_comparison"]
    component = selection["surface_analysis"]["largest_component"]
    gate = selection["stable_representative"]
    verdict = (
        "历史诊断门禁通过；仍只保留为未来研究候选"
        if bool(selection["promotion_eligible"])
        else "历史诊断门禁未全部通过；不提名唯一参数"
    )
    compact = (
        f"<p>完整重扫 <strong>1,444</strong> 个买/卖周期对。稳定平台代表是 "
        f"<strong>买 SMA{int(representative['buy_window'])} / 卖 SMA{int(representative['sell_window'])}</strong>，"
        f"三段最差 CAGR <strong>{float(representative['primary_metric']):.3f}%</strong>，"
        f"全史 CAGR <strong>{float(representative['cagr_pct']):.3f}%</strong>，"
        f"Sharpe <strong>{float(representative['sharpe']):.3f}</strong>，"
        f"最大回撤 <strong>{float(representative['max_drawdown_pct']):.2f}%</strong>。"
        f"最大高分连通区有 {int(component['cell_count'])} 格，结构门禁"
        f"<strong>{'通过' if bool(component['structural_pass']) else '未通过'}</strong>；"
        f"PBO={float(selection['pbo']['pbo']):.2%}，相关性折算 DSR={float(gate['dsr_effective_probability']):.2%}。"
        f"最终判定：<strong>{html.escape(verdict)}</strong>。</p>"
        f"<h2>310/190 在新旧语义下怎么变</h2><p>新语义 CAGR {float(anchor['cagr_pct']):.3f}%、"
        f"Sharpe {float(anchor['sharpe']):.3f}、最大回撤 {float(anchor['max_drawdown_pct']):.2f}%、"
        f"成交 {int(anchor['order_count'])} 次；相对已验证旧语义，CAGR {float(anchor['delta_cagr_pct']):+.3f} 点、"
        f"Sharpe {float(anchor['delta_sharpe']):+.3f}、最大回撤 {float(anchor['delta_max_drawdown_pct']):+.3f} 点、"
        f"成交 {float(anchor['delta_order_count']):+.0f} 次。</p>"
        "<h2>正式候选与旧语义配对对照</h2>"
        + _metric_table(formal, comparison, representative_id)
    )

    raw = pd.read_csv(WORKSPACE_ROOT / "data/processed/daily/QQQ.csv", parse_dates=["date"])
    anchor_formal = formal[(formal["buy_window"] == 310) & (formal["sell_window"] == 190)]
    if len(anchor_formal) != 1:
        raise AssertionError("Formal results are missing the 310/190 anchor.")
    anchor_id = str(anchor_formal.iloc[0]["case_id"])
    anchor_reference = reference_daily[reference_daily["case_id"].astype(str).eq(anchor_id)].copy()
    anchor_plans = plans[plans["case_id"].astype(str).eq(anchor_id)].copy()
    anchor_orders = orders[orders["case_id"].astype(str).eq(anchor_id)].copy()
    figures = [
        ReportFigure(
            "performance-qqq",
            "稳定平台代表、310/190 与正式候选的净值和回撤",
            performance_figure(daily, benchmark, formal, representative_id),
            "performance",
        ),
        ReportFigure(
            "period-surface",
            "新状态机下的买入/卖出 SMA 稳健曲面",
            surface_figure(results, selection["surface_analysis"], representative),
            "generic",
        ),
        ReportFigure(
            "parent-deltas",
            "每个周期对相对旧 c=3%、d关闭语义的变化",
            parent_delta_figure(comparison),
            "generic",
        ),
        ReportFigure(
            "subperiod-robustness",
            "稳定代表、310/190 与 SMA200 对照的三个继承状态分段",
            subperiod_figure(formal, representative_id),
            "generic",
        ),
        ReportFigure(
            "multiple-testing",
            "PBO 与两种试验数口径下的 DSR",
            multiple_testing_figure(splits, dsr, selection["pbo"]),
            "generic",
        ),
        ReportFigure(
            "market-qqq",
            "310/190 诊断路径：试探仓、失败退出、确认多头与一次性 3% 买回",
            market_figure(
                raw,
                anchor_reference,
                anchor_plans,
                anchor_orders,
                buy_window=310,
                sell_window=190,
            ),
            "market",
        ),
    ]
    report = render_interactive_report(
        title="QQQ 两均线恢复试探状态机：2000–2015 重新搜索",
        heading="QQQ 两均线恢复试探状态机：2000–2015 重新搜索",
        subtitle="买入与卖出 SMA 各 80～450 日、步长 10；c=3%，d关闭；1,444 个周期对。",
        summary_html=compact,
        notes=[
            "所有参数从2000-12-18共同起跑；此前数据只用于SMA450预热，初始现金100,000美元且空仓。",
            "普通买入与两类失败退出只用完成收盘确认并在下一开盘成交；短均线卖出线和一次性3%买回线在盘前冻结。",
            "试探失败在同一收盘上优先于升级；失败卖出不刷新3%锚，每个确认卖出后的熊市阶段最多一次强制买回。",
            "最大高分平台、三个继承状态分段、事件次数、PBO与DSR共同决定能否保留候选；全历史最高CAGR或Sharpe只作补充。",
            "本轮修复了策略语义并复用2000–2015探索数据，因此即使门禁通过也不是样本外验证或交易建议。",
            "使用拆股和股息调整OHLC，是内部一致的总回报近似；日线无法还原同一根K线内的精确先后顺序。",
        ],
        figures=figures,
        experiment=context.config,
        run_id=args.run_id,
        template_id=str(context.config["reporting"]["template_id"]),
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")
    (run_root / "report_print.html").write_text(report, encoding="utf-8")
    (run_root / "report.md").write_text(
        markdown_report(args.run_id, selection, formal, comparison),
        encoding="utf-8",
    )

    pdf_process = subprocess.run(
        [
            "node",
            "scripts/print_html_pdf.mjs",
            str(run_root / "report_print.html"),
            str(run_root / "report.pdf"),
            "这项策略怎么运行",
            "什么时候买",
            "什么时候卖",
            "信号如何变成成交",
        ],
        cwd=BACKTEST_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if pdf_process.returncode != 0:
        raise RuntimeError(
            "PDF generation failed:\n" + "\n".join([pdf_process.stdout, pdf_process.stderr])
        )
    pdf_payload = json.loads(pdf_process.stdout.split("\n", 1)[1])
    (analysis_root / "pdf_print_gate.json").write_text(
        json.dumps(pdf_payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
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
        "backtest/quantkit/sma_recovery_probation.py",
        "backtest/quantkit/sma_recovery_probation_search.py",
        "backtest/scripts/run_intraday_sma_backtest.py",
        "backtest/scripts/run_intraday_sma200_threshold_grid.py",
        "backtest/scripts/run_sma_recovery_probation_grid.py",
        "backtest/scripts/analyze_sma_recovery_probation_grid.py",
        "backtest/scripts/print_html_pdf.mjs",
        "backtest/scripts/smoke_report_ui.mjs",
        "backtest/tests/strategies/tim/test_sma_recovery_probation.py",
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

This immutable run re-searches the QQQ buy/sell SMA pair under the recovery-probation state machine.

- `report.html` / `report.pdf` / `report.md`: rules-first interactive, printable and concise reports.
- `analysis/summary.json`: machine-readable representative, gates and formal candidates.
- `analysis/old_new_surface_comparison.csv`: all 1,444 paired deltas versus validated TIM-v0.50 c3/d-off.
- `QQQ/cost_5bps/`: full-grid metrics, two compiled ledgers, formal PyBroker/Python ledgers, PBO and DSR evidence.
- `provenance.json` / `validation.json`: source hashes and correctness gates.
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
    print(f"Wrote {run_root / 'report.pdf'}")


if __name__ == "__main__":
    main()
