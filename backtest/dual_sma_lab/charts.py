"""Plotly figures for the dual-SMA Streamlit lab."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from dual_sma_lab.core import GridResult, SingleRunResult


LIGHT = {
    "paper": "#ffffff",
    "plot": "#f8fafc",
    "text": "#0f172a",
    "grid": "#cbd5e1",
    "axis": "#64748b",
}

GRID_METRICS = {
    "持仓 CAGR": ("holding_period_cagr_pct", "%", "RdYlGn"),
    "持仓 Sharpe": ("holding_return_sharpe", "", "RdYlGn"),
    "日历 CAGR": ("cagr_pct", "%", "RdYlGn"),
    "日历 Sharpe": ("sharpe", "", "RdYlGn"),
    "累计收益": ("total_return_pct", "%", "RdYlGn"),
    "最大回撤": ("max_drawdown_pct", "%", "RdYlGn"),
    "持仓率": ("holding_time_pct", "%", "Viridis"),
    "订单数": ("order_count", "", "Viridis"),
}


def _base_layout(figure: go.Figure, *, height: int, uirevision: str) -> go.Figure:
    figure.update_layout(
        template="plotly_white",
        paper_bgcolor=LIGHT["paper"],
        plot_bgcolor=LIGHT["plot"],
        font={"color": LIGHT["text"]},
        height=height,
        hovermode="x unified",
        margin={"l": 68, "r": 30, "t": 76, "b": 50},
        legend={
            "orientation": "h",
            "y": 1.06,
            "x": 0,
            "bgcolor": "rgba(255,255,255,0.9)",
            "bordercolor": LIGHT["grid"],
            "borderwidth": 1,
        },
        uirevision=uirevision,
    )
    figure.update_xaxes(
        rangebreaks=[{"bounds": ["sat", "mon"]}],
        gridcolor=LIGHT["grid"],
        linecolor=LIGHT["axis"],
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
    )
    figure.update_yaxes(gridcolor=LIGHT["grid"], linecolor=LIGHT["axis"])
    return figure


def build_single_figure(result: SingleRunResult, *, price_scale: str = "log") -> go.Figure:
    if price_scale not in {"log", "linear"}:
        raise ValueError("price_scale must be log or linear")
    daily = result.daily.copy()
    benchmark = result.benchmark_daily.copy()
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.64, 0.36],
        vertical_spacing=0.08,
        subplot_titles=(
            f"{result.symbol} 调整价格、SMA{result.fast_window} / SMA{result.slow_window} 与成交",
            "账户净值",
        ),
    )
    figure.add_trace(
        go.Scatter(
            x=daily["date"],
            y=daily["close"],
            name=f"{result.symbol} Close",
            mode="lines",
            line={"color": "#111827", "width": 1.8},
        ),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Scatter(
            x=daily["date"],
            y=daily["fast_sma"],
            name=f"快线 SMA{result.fast_window}",
            mode="lines",
            line={"color": "#2563eb", "width": 1.5},
        ),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Scatter(
            x=daily["date"],
            y=daily["slow_sma"],
            name=f"慢线 SMA{result.slow_window}",
            mode="lines",
            line={"color": "#f97316", "width": 1.5},
        ),
        row=1,
        col=1,
    )
    for side, color, marker in (("buy", "#15803d", "triangle-up"), ("sell", "#dc2626", "triangle-down")):
        orders = result.orders[result.orders.get("type", pd.Series(dtype=str)).eq(side)]
        if orders.empty:
            continue
        figure.add_trace(
            go.Scatter(
                x=orders["date"],
                y=orders["fill_price"],
                name="买入" if side == "buy" else "卖出",
                mode="markers",
                marker={"color": color, "symbol": marker, "size": 10},
                customdata=np.asarray(
                    [
                        [pd.Timestamp(signal_date).date().isoformat(), float(shares)]
                        for signal_date, shares in zip(
                            orders["signal_date"], orders["shares"], strict=True
                        )
                    ],
                    dtype=object,
                ),
                hovertemplate=(
                    "%{x|%Y-%m-%d}<br>成交价 %{y:.4f}<br>"
                    "信号日 %{customdata[0]}<br>股数 %{customdata[1]:,.3f}<extra></extra>"
                ),
            ),
            row=1,
            col=1,
        )
    figure.add_trace(
        go.Scatter(
            x=daily["date"],
            y=daily["equity"],
            name="双均线策略",
            mode="lines",
            line={"color": "#0f766e", "width": 2.1},
        ),
        row=2,
        col=1,
    )
    figure.add_trace(
        go.Scatter(
            x=benchmark["date"],
            y=benchmark["equity"],
            name=f"{result.symbol} Buy & Hold",
            mode="lines",
            line={"color": "#7c3aed", "width": 1.8, "dash": "dash"},
        ),
        row=2,
        col=1,
    )
    _base_layout(
        figure,
        height=850,
        uirevision=f"dual-sma-single-{result.symbol}-{result.fast_window}-{result.slow_window}-{price_scale}",
    )
    figure.update_yaxes(title="调整价格", type=price_scale, row=1, col=1)
    figure.update_yaxes(title="美元", row=2, col=1)
    figure.update_xaxes(title="交易日", row=2, col=1)
    return figure


def build_heatmap(result: GridResult, metric_label: str) -> go.Figure:
    if metric_label not in GRID_METRICS:
        raise ValueError(f"Unknown grid metric: {metric_label}")
    column, suffix, colorscale = GRID_METRICS[metric_label]
    rows = result.metrics
    fast_values = list(result.spec.fast_values)
    slow_values = list(result.spec.slow_values)

    def surface(name: str) -> np.ndarray:
        return (
            rows.pivot(index="fast_window", columns="slow_window", values=name)
            .reindex(index=fast_values, columns=slow_values)
            .to_numpy(float)
        )

    z = surface(column)
    custom = np.stack(
        [
            surface("holding_period_cagr_pct"),
            surface("holding_return_sharpe"),
            surface("cagr_pct"),
            surface("sharpe"),
            surface("total_return_pct"),
            surface("max_drawdown_pct"),
            surface("holding_time_pct"),
            surface("order_count"),
        ],
        axis=-1,
    )
    figure = go.Figure(
        go.Heatmap(
            x=slow_values,
            y=fast_values,
            z=z,
            customdata=custom,
            colorscale=colorscale,
            colorbar={"title": f"{metric_label}{suffix}"},
            hoverongaps=False,
            hovertemplate=(
                "快线 %{y}<br>慢线 %{x}<br>"
                "持仓 CAGR %{customdata[0]:.2f}%<br>"
                "持仓 Sharpe %{customdata[1]:.3f}<br>"
                "日历 CAGR %{customdata[2]:.2f}%<br>"
                "日历 Sharpe %{customdata[3]:.3f}<br>"
                "累计收益 %{customdata[4]:.2f}%<br>"
                "最大回撤 %{customdata[5]:.2f}%<br>"
                "持仓率 %{customdata[6]:.2f}%<br>"
                "订单数 %{customdata[7]:.0f}<extra></extra>"
            ),
        )
    )
    figure.update_layout(
        template="plotly_white",
        paper_bgcolor=LIGHT["paper"],
        plot_bgcolor=LIGHT["plot"],
        font={"color": LIGHT["text"]},
        title=(
            f"{result.symbol} · {result.effective_start.date()} 至 {result.effective_end.date()} · "
            f"{metric_label} 参数面"
        ),
        xaxis_title="慢线周期",
        yaxis_title="快线周期",
        height=760,
        margin={"l": 70, "r": 35, "t": 80, "b": 60},
        uirevision=f"dual-sma-grid-{result.symbol}-{metric_label}",
    )
    figure.update_xaxes(gridcolor=LIGHT["grid"], linecolor=LIGHT["axis"])
    figure.update_yaxes(gridcolor=LIGHT["grid"], linecolor=LIGHT["axis"])
    return figure
