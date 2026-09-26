"""Plotly assembly for the indicator lab."""

from __future__ import annotations

from collections.abc import Mapping

import pandas as pd
import plotly.graph_objects as go
from plotly import colors
from plotly.subplots import make_subplots


OSCILLATOR_COLORS = {
    "Raw StochRSI": "#94a3b8",
    "K": "#2563eb",
    "D": "#f97316",
    "RSI": "#7c3aed",
}

LIGHT_CHART_COLORS = {
    "paper": "#ffffff",
    "plot": "#f8fafc",
    "text": "#0f172a",
    "grid": "#cbd5e1",
    "axis": "#64748b",
}


def build_indicator_figure(
    frame: pd.DataFrame,
    *,
    symbol: str,
    price_lines: Mapping[str, pd.Series],
    oscillator_lines: Mapping[str, pd.Series],
    lower_threshold: float,
    upper_threshold: float,
    price_scale: str,
    price_style: str,
) -> go.Figure:
    """Build a shared-date price/indicator figure from plugin outputs."""

    if frame.empty:
        raise ValueError("Cannot chart an empty display frame")
    if price_scale not in {"log", "linear"}:
        raise ValueError("price_scale must be 'log' or 'linear'")
    if price_style not in {"Close", "Candlestick"}:
        raise ValueError("price_style must be 'Close' or 'Candlestick'")

    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.66, 0.34],
        vertical_spacing=0.07,
        subplot_titles=(f"{symbol} 价格与均线", "RSI / StochRSI / K / D"),
    )
    candle_available = all(column in frame for column in ("open", "high", "low"))
    if price_style == "Candlestick" and candle_available:
        figure.add_trace(
            go.Candlestick(
                x=frame["date"],
                open=frame["open"],
                high=frame["high"],
                low=frame["low"],
                close=frame["close"],
                name=symbol,
                increasing_line_color="#15803d",
                decreasing_line_color="#dc2626",
            ),
            row=1,
            col=1,
        )
    else:
        figure.add_trace(
            go.Scatter(
                x=frame["date"],
                y=frame["close"],
                mode="lines",
                name=f"{symbol} Close",
                line={"color": "#111827", "width": 2.0},
            ),
            row=1,
            col=1,
        )

    palette = colors.sample_colorscale(
        "Turbo",
        [index / max(len(price_lines) - 1, 1) for index in range(len(price_lines))],
    )
    for color, (label, values) in zip(palette, price_lines.items(), strict=True):
        figure.add_trace(
            go.Scatter(
                x=frame["date"],
                y=values,
                mode="lines",
                name=label,
                line={"color": color, "width": 1.45},
            ),
            row=1,
            col=1,
        )

    for label, values in oscillator_lines.items():
        dash = "dot" if label in {"Raw StochRSI", "RSI"} else "solid"
        width = 1.1 if label == "Raw StochRSI" else 1.8
        figure.add_trace(
            go.Scatter(
                x=frame["date"],
                y=values,
                mode="lines",
                name=label,
                line={
                    "color": OSCILLATOR_COLORS.get(label, "#0f766e"),
                    "width": width,
                    "dash": dash,
                },
            ),
            row=2,
            col=1,
        )

    for threshold, label in (
        (lower_threshold, "Lower"),
        (upper_threshold, "Upper"),
    ):
        figure.add_hline(
            y=threshold,
            line_dash="dash",
            line_color="#64748b",
            line_width=1,
            annotation_text=f"{label} {threshold:.2f}",
            annotation_position="right",
            row=2,
            col=1,
        )

    figure.update_layout(
        template="plotly_white",
        paper_bgcolor=LIGHT_CHART_COLORS["paper"],
        plot_bgcolor=LIGHT_CHART_COLORS["plot"],
        font={"color": LIGHT_CHART_COLORS["text"]},
        height=880,
        hovermode="x unified",
        dragmode="zoom",
        margin={"l": 64, "r": 32, "t": 72, "b": 42},
        legend={
            "orientation": "h",
            "y": 1.06,
            "x": 0,
            "bgcolor": "rgba(255,255,255,0.92)",
            "bordercolor": LIGHT_CHART_COLORS["grid"],
            "borderwidth": 1,
            "font": {"color": LIGHT_CHART_COLORS["text"]},
        },
        hoverlabel={
            "bgcolor": LIGHT_CHART_COLORS["paper"],
            "bordercolor": LIGHT_CHART_COLORS["axis"],
            "font": {"color": LIGHT_CHART_COLORS["text"]},
        },
        uirevision=f"indicator-lab-{symbol}-{price_scale}",
    )
    figure.update_xaxes(
        rangebreaks=[{"bounds": ["sat", "mon"]}],
        color=LIGHT_CHART_COLORS["text"],
        gridcolor=LIGHT_CHART_COLORS["grid"],
        linecolor=LIGHT_CHART_COLORS["axis"],
        zerolinecolor=LIGHT_CHART_COLORS["axis"],
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
        spikecolor=LIGHT_CHART_COLORS["axis"],
    )
    figure.update_xaxes(rangeslider={"visible": False}, row=1, col=1)
    figure.update_xaxes(title="交易日", row=2, col=1)
    figure.update_yaxes(
        title="调整价格",
        type=price_scale,
        color=LIGHT_CHART_COLORS["text"],
        gridcolor=LIGHT_CHART_COLORS["grid"],
        linecolor=LIGHT_CHART_COLORS["axis"],
        zerolinecolor=LIGHT_CHART_COLORS["axis"],
        row=1,
        col=1,
    )
    figure.update_yaxes(
        title="0–1",
        range=[-0.03, 1.03],
        color=LIGHT_CHART_COLORS["text"],
        gridcolor=LIGHT_CHART_COLORS["grid"],
        linecolor=LIGHT_CHART_COLORS["axis"],
        zerolinecolor=LIGHT_CHART_COLORS["axis"],
        row=2,
        col=1,
    )
    return figure
