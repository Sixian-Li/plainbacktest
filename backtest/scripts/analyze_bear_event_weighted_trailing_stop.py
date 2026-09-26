#!/usr/bin/env python3
"""Build the formal report for the weighted-slot and trailing-stop bear study."""

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
from quantkit.paths import BACKTEST_ROOT
from quantkit.reporting import ReportFigure, render_interactive_report


WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/TIM/"
    "TIM-v0.40a.1__26-08-15__bear_market_weighted_trailing_stop_ablation"
)
SYMBOL_BLOCK = "BEAR_EVENT_WEIGHTED_STOPS"
STARRED = {"EQT", "GIS", "LMT", "ORLY", "AZO"}
STOP_ORDER = ["stop_off", "stop_06", "stop_08", "stop_10", "stop_12"]
STOP_LABELS = {
    "stop_off": "无强卖",
    "stop_06": "峰值回撤 6%",
    "stop_08": "峰值回撤 8%",
    "stop_10": "峰值回撤 10%",
    "stop_12": "峰值回撤 12%",
}
STAR_LABELS = {"star_off": "全部一坑", "star_on": "星标双坑"}
SCOPE_LABELS = {"major": "大熊", "minor": "小熊", "all": "总熊"}
STOP_COLORS = {
    "stop_off": "#64748b",
    "stop_06": "#b91c1c",
    "stop_08": "#ea580c",
    "stop_10": "#ca8a04",
    "stop_12": "#0f766e",
}


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def case_label(case_id: str) -> str:
    star_mode, stop_id = case_id.split("__", 1)
    return f"{STAR_LABELS[star_mode]} · {STOP_LABELS[stop_id]}"


def drawdown(equity: pd.Series) -> pd.Series:
    values = equity.astype(float)
    return (values / values.cummax() - 1.0) * 100.0


def load_blocks(context, run_id: str) -> dict[float, dict[str, pd.DataFrame]]:
    parse_dates = {
        "daily": ["date"],
        "transitions": ["signal_date", "execution_date"],
        "interval_returns": ["start", "end", "entry_execution_date", "exit_execution_date"],
        "interval_schedule": ["start", "end", "entry_execution_date", "exit_execution_date"],
        "calendar_exclusions": ["date"],
    }
    plain = (
        "metrics",
        "interval_summary",
        "star_ablation",
        "stop_ablation",
        "symbol_interval_contributions",
        "asset_interval_returns",
        "symbol_holding_stats",
    )
    blocks: dict[float, dict[str, pd.DataFrame]] = {}
    for configured_cost in context.config["cost_scenarios_bps_per_side"]:
        cost = float(configured_cost)
        root = block_root(context, run_id, SYMBOL_BLOCK, cost)
        block = {
            name: pd.read_csv(root / f"{name}.csv", parse_dates=dates)
            for name, dates in parse_dates.items()
        }
        block.update({name: pd.read_csv(root / f"{name}.csv") for name in plain})
        blocks[cost] = block
    return blocks


def market_figure(spy: pd.DataFrame, schedule: pd.DataFrame) -> go.Figure:
    figure = go.Figure()
    figure.add_trace(
        go.Candlestick(
            x=spy["date"],
            open=spy["open"],
            high=spy["high"],
            low=spy["low"],
            close=spy["close"],
            name="SPY 复权 OHLC",
            increasing_line_color="#1b7f5a",
            decreasing_line_color="#c2413b",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=spy["date"],
            y=spy["close"],
            mode="lines",
            name="SPY Close",
            line={"color": "#0f766e", "width": 1.2},
            meta={
                "series_key": "spy_close",
                "panel": "market",
                "label": "SPY Close",
                "control_group": "reference",
                "control_group_label": "参考价格",
            },
            hovertemplate="%{x|%Y-%m-%d}<br>$%{y:,.2f}<extra></extra>",
        )
    )
    for interval in schedule.drop_duplicates("interval_id").sort_values("ordinal").itertuples(index=False):
        figure.add_vrect(
            x0=pd.Timestamp(interval.start),
            x1=pd.Timestamp(interval.end),
            fillcolor="#dc2626" if interval.severity == "major" else "#f59e0b",
            opacity=0.09,
            line_width=0,
            layer="below",
        )
    figure.update_layout(
        height=620,
        yaxis_title="USD",
        xaxis={"rangeslider": {"visible": True}},
        hovermode="x unified",
        showlegend=False,
        margin={"l": 65, "r": 25, "t": 25, "b": 55},
        uirevision="weighted-stop-market-v1",
    )
    figure.update_yaxes(fixedrange=False)
    return figure


def performance_figure(blocks: dict[float, dict[str, pd.DataFrame]]) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.68, 0.32],
        subplot_titles=("连续事件账户净值", "连续账户历史峰值回撤"),
    )
    for cost in sorted(blocks):
        daily = blocks[cost]["daily"]
        for star_mode in ("star_off", "star_on"):
            for stop_id in STOP_ORDER:
                case_id = f"{star_mode}__{stop_id}"
                frame = daily[daily["case_id"] == case_id].sort_values("date")
                series_key = f"{case_id}_{cost:g}bps"
                visible: bool | str = (
                    True
                    if cost == 5 and stop_id == "stop_off"
                    else "legendonly"
                )
                dash = "solid" if star_mode == "star_on" else "dash"
                width = 2.4 if stop_id == "stop_off" else 1.7
                label = f"{case_label(case_id)} · {cost:g} bps"
                for row, values, panel, showlegend in (
                    (1, frame["equity"], "equity", True),
                    (2, drawdown(frame["equity"]), "drawdown", False),
                ):
                    figure.add_trace(
                        go.Scatter(
                            x=frame["date"],
                            y=values,
                            mode="lines",
                            name=label,
                            showlegend=showlegend,
                            visible=visible,
                            line={"color": STOP_COLORS[stop_id], "width": width, "dash": dash},
                            meta={
                                "series_key": series_key,
                                "panel": panel,
                                "label": label,
                                "is_benchmark": bool(
                                    panel == "equity"
                                    and case_id == "star_off__stop_off"
                                    and cost == 5
                                ),
                                "cost_bps": cost,
                            },
                            hovertemplate=(
                                "%{x|%Y-%m-%d}<br>$%{y:,.2f}<extra></extra>"
                                if row == 1
                                else "%{x|%Y-%m-%d}<br>%{y:.2f}%<extra></extra>"
                            ),
                        ),
                        row=row,
                        col=1,
                    )
    figure.update_layout(
        height=860,
        hovermode="x unified",
        showlegend=False,
        margin={"l": 65, "r": 25, "t": 65, "b": 55},
        uirevision="weighted-stop-performance-v1",
    )
    figure.update_yaxes(title_text="USD", type="log", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="%", fixedrange=False, row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def scope_heatmap(summary: pd.DataFrame) -> go.Figure:
    case_ids = [f"{star}__{stop}" for star in ("star_off", "star_on") for stop in STOP_ORDER]
    pivot = (
        summary.pivot(index="case_id", columns="scope", values="compound_return")
        .reindex(index=case_ids, columns=["major", "minor", "all"])
        * 100.0
    )
    figure = go.Figure(
        go.Heatmap(
            x=[SCOPE_LABELS[item] for item in pivot.columns],
            y=[case_label(item) for item in pivot.index],
            z=pivot.to_numpy(),
            text=np.vectorize(lambda value: f"{value:+.2f}%")(pivot.to_numpy()),
            texttemplate="%{text}",
            colorscale="RdYlGn",
            zmid=0,
            colorbar={"title": "%"},
            hovertemplate="%{y}<br>%{x}: %{z:.2f}%<extra></extra>",
        )
    )
    figure.update_layout(height=610, margin={"l": 185, "r": 30, "t": 25, "b": 55})
    return figure


def interval_heatmap(interval_returns: pd.DataFrame) -> go.Figure:
    ordered_events = (
        interval_returns[["ordinal", "label"]]
        .drop_duplicates()
        .sort_values("ordinal")
    )
    labels = ordered_events["label"].tolist()
    case_ids = [f"{star}__{stop}" for star in ("star_off", "star_on") for stop in STOP_ORDER]
    pivot = (
        interval_returns.pivot(index="case_id", columns="label", values="total_return")
        .reindex(index=case_ids, columns=labels)
        * 100.0
    )
    figure = go.Figure(
        go.Heatmap(
            x=labels,
            y=[case_label(item) for item in pivot.index],
            z=pivot.to_numpy(),
            text=np.vectorize(lambda value: f"{value:+.1f}%")(pivot.to_numpy()),
            texttemplate="%{text}",
            colorscale="RdYlGn",
            zmid=0,
            colorbar={"title": "%"},
            hovertemplate="%{y}<br>%{x}: %{z:.2f}%<extra></extra>",
        )
    )
    figure.update_layout(height=650, margin={"l": 185, "r": 30, "t": 25, "b": 145})
    return figure


def factor_ablation_figure(
    star_ablation: pd.DataFrame,
    stop_ablation: pd.DataFrame,
) -> go.Figure:
    figure = make_subplots(
        rows=1,
        cols=2,
        subplot_titles=("星标双坑 − 全部一坑", "强制退出 − 同权重无强卖"),
        horizontal_spacing=0.13,
    )
    star_x = ["无强卖", "6%", "8%", "10%", "12%"]
    for scope, dash in (("all", "solid"), ("major", "dash"), ("minor", "dot")):
        scoped = star_ablation[star_ablation["scope"] == scope].set_index("stop_id").reindex(STOP_ORDER)
        figure.add_trace(
            go.Scatter(
                x=star_x,
                y=scoped["star_on_minus_off"] * 100.0,
                mode="lines+markers",
                name=f"星标效应 · {SCOPE_LABELS[scope]}",
                line={"width": 2.1, "dash": dash},
                hovertemplate="%{x}<br>%{y:+.2f}pp<extra></extra>",
            ),
            row=1,
            col=1,
        )
    for star_mode, color in (("star_off", "#64748b"), ("star_on", "#7c3aed")):
        for scope, dash in (("all", "solid"), ("major", "dash"), ("minor", "dot")):
            scoped = stop_ablation[
                (stop_ablation["star_mode"] == star_mode)
                & (stop_ablation["scope"] == scope)
            ].sort_values("trailing_drawdown_pct")
            figure.add_trace(
                go.Scatter(
                    x=scoped["trailing_drawdown_pct"],
                    y=scoped["stop_minus_off"] * 100.0,
                    mode="lines+markers",
                    name=f"{STAR_LABELS[star_mode]} · {SCOPE_LABELS[scope]}",
                    line={"color": color, "width": 2.0, "dash": dash},
                    hovertemplate="回撤阈值 %{x:.0f}%<br>%{y:+.2f}pp<extra></extra>",
                ),
                row=1,
                col=2,
            )
    figure.add_hline(y=0, line={"color": "#475569", "width": 1}, row=1, col=1)
    figure.add_hline(y=0, line={"color": "#475569", "width": 1}, row=1, col=2)
    figure.update_xaxes(title_text="双坑效应所在的止损 case", row=1, col=1)
    figure.update_xaxes(title_text="峰值回撤阈值（%）", row=1, col=2)
    figure.update_yaxes(title_text="配对差（百分点）", row=1, col=1)
    figure.update_yaxes(title_text="配对差（百分点）", row=1, col=2)
    figure.update_layout(
        height=590,
        legend={"orientation": "h", "y": -0.28},
        margin={"l": 65, "r": 25, "t": 65, "b": 145},
    )
    return figure


def family_contribution_frame(
    contributions: pd.DataFrame,
    initial_cash: float,
) -> pd.DataFrame:
    frame = contributions.copy()
    frame["family"] = np.where(frame["symbol"].isin(STARRED), "星标家族", "普通家族")
    grouped = (
        frame.groupby(["case_id", "star_mode", "stop_id", "family", "severity"], as_index=False)[
            "realized_pnl"
        ]
        .sum()
    )
    all_scope = (
        frame.groupby(["case_id", "star_mode", "stop_id", "family"], as_index=False)[
            "realized_pnl"
        ]
        .sum()
    )
    all_scope["severity"] = "all"
    grouped = pd.concat([grouped, all_scope], ignore_index=True)
    grouped["contribution_to_initial_pct"] = grouped["realized_pnl"] / initial_cash * 100.0
    return grouped


def family_figure(family: pd.DataFrame) -> go.Figure:
    cases = [f"{star}__{stop}" for star in ("star_off", "star_on") for stop in STOP_ORDER]
    scoped = family[family["severity"] == "all"]
    figure = go.Figure()
    for name, color in (("星标家族", "#7c3aed"), ("普通家族", "#64748b")):
        values = scoped[scoped["family"] == name].set_index("case_id").reindex(cases)
        figure.add_trace(
            go.Bar(
                x=[case_label(case) for case in cases],
                y=values["contribution_to_initial_pct"],
                name=name,
                marker_color=color,
                hovertemplate="%{x}<br>%{y:+.2f}% 初始资金<extra></extra>",
            )
        )
    figure.add_hline(y=0, line={"color": "#475569", "width": 1})
    figure.update_layout(
        barmode="relative",
        height=590,
        yaxis_title="12 段累计实现盈亏 / 初始资金（%）",
        legend={"orientation": "h", "y": 1.13},
        margin={"l": 70, "r": 25, "t": 65, "b": 155},
    )
    return figure


def compound_asset_returns(asset_returns: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for symbol, frame in asset_returns.groupby("symbol"):
        for scope in ("major", "minor", "all"):
            selected = frame if scope == "all" else frame[frame["severity"] == scope]
            values = selected["close_to_close_return"].dropna().astype(float)
            rows.append(
                {
                    "symbol": symbol,
                    "scope": scope,
                    "event_count": int(len(values)),
                    "compound_close_return": float((1.0 + values).prod() - 1.0) if len(values) else np.nan,
                }
            )
    return pd.DataFrame(rows)


def symbol_contribution_frame(
    contributions: pd.DataFrame,
    initial_cash: float,
) -> pd.DataFrame:
    grouped = contributions.groupby(["case_id", "symbol"], as_index=False)["realized_pnl"].sum()
    grouped["contribution_to_initial_pct"] = grouped["realized_pnl"] / initial_cash * 100.0
    return grouped


def symbol_heatmap(symbol_contributions: pd.DataFrame, symbols: list[str]) -> go.Figure:
    cases = [f"{star}__{stop}" for star in ("star_off", "star_on") for stop in STOP_ORDER]
    pivot = (
        symbol_contributions.pivot(index="case_id", columns="symbol", values="contribution_to_initial_pct")
        .reindex(index=cases, columns=symbols)
    )
    figure = go.Figure(
        go.Heatmap(
            x=pivot.columns,
            y=[case_label(item) for item in pivot.index],
            z=pivot.to_numpy(),
            text=np.vectorize(lambda value: f"{value:+.1f}%")(pivot.to_numpy()),
            texttemplate="%{text}",
            colorscale="RdYlGn",
            zmid=0,
            colorbar={"title": "% 初始资金"},
            hovertemplate="%{y}<br>%{x}: %{z:+.2f}% 初始资金<extra></extra>",
        )
    )
    figure.update_layout(height=650, margin={"l": 185, "r": 35, "t": 25, "b": 70})
    return figure


def evaluate_stability(
    star_ablation: pd.DataFrame,
    stop_ablation: pd.DataFrame,
) -> dict[str, Any]:
    total_stop = stop_ablation[stop_ablation["scope"] == "all"].copy()
    detail_stop = stop_ablation[stop_ablation["scope"].isin(["major", "minor"])].copy()
    stable_pairs: list[list[int]] = []
    for left, right in ((6, 8), (8, 10), (10, 12)):
        total_pair = total_stop[total_stop["trailing_drawdown_pct"].isin([left, right])]
        detail_pair = detail_stop[detail_stop["trailing_drawdown_pct"].isin([left, right])]
        total_positive = len(total_pair) == 4 and bool((total_pair["stop_minus_off"] > 0).all())
        not_systematically_opposite = True
        for threshold in (left, right):
            for star_mode in ("star_off", "star_on"):
                cell = detail_pair[
                    (detail_pair["trailing_drawdown_pct"] == threshold)
                    & (detail_pair["star_mode"] == star_mode)
                ]
                if len(cell) != 2 or bool((cell["stop_minus_off"] < 0).all()):
                    not_systematically_opposite = False
        if total_positive and not_systematically_opposite:
            stable_pairs.append([left, right])
    star_total = star_ablation[star_ablation["scope"] == "all"]
    star_major = star_ablation[star_ablation["scope"] == "major"]
    star_minor = star_ablation[star_ablation["scope"] == "minor"]
    star_total_positive_count = int((star_total["star_on_minus_off"] > 0).sum())
    star_effect_stable = bool(
        len(star_total) == 5
        and (star_total["star_on_minus_off"] > 0).all()
        and (
            (star_major["star_on_minus_off"] > 0).all()
            or (star_minor["star_on_minus_off"] > 0).all()
        )
    )
    return {
        "stable_adjacent_stop_pairs_pct": stable_pairs,
        "stop_descriptive_stability_passed": bool(stable_pairs),
        "star_total_positive_case_count": star_total_positive_count,
        "star_effect_descriptive_stability_passed": star_effect_stable,
        "eligible_for_realtime_followup": bool(stable_pairs and star_effect_stable),
        "direct_promotion_allowed": False,
        "direct_promotion_blocker": "熊市边界和候选池均使用事后信息",
    }


def metric_table(blocks: dict[float, dict[str, pd.DataFrame]]) -> str:
    five = blocks[5.0]["metrics"].set_index("case_id")
    zero = blocks[0.0]["metrics"].set_index("case_id")
    rows: list[str] = []
    for star_mode in ("star_off", "star_on"):
        for stop_id in STOP_ORDER:
            case_id = f"{star_mode}__{stop_id}"
            item = five.loc[case_id]
            cost_delta = item["all_bear_compound_return_pct"] - zero.loc[
                case_id, "all_bear_compound_return_pct"
            ]
            rows.append(
                "<tr>"
                f"<td>{html.escape(case_label(case_id))}</td>"
                f"<td>{item['major_bear_compound_return_pct']:+.2f}%</td>"
                f"<td>{item['minor_bear_compound_return_pct']:+.2f}%</td>"
                f"<td>{item['all_bear_compound_return_pct']:+.2f}%</td>"
                f"<td>{item['all_bear_worst_interval_return_pct']:+.2f}%</td>"
                f"<td>{item['max_drawdown_pct']:.2f}%</td>"
                f"<td>{item['average_bear_gross_exposure_pct']:.1f}%</td>"
                f"<td>{int(item['forced_exit_count'])}</td>"
                f"<td>{item['turnover_multiple']:.1f}×</td>"
                f"<td>{cost_delta:+.2f}pp</td>"
                "</tr>"
            )
    return (
        '<div class="summary-grid"><div class="summary-card"><strong>固定设计</strong>'
        '<span>15 标的；2 种坑位 × 5 种峰值止损；0/5 bps；熊市外全现金</span></div>'
        '<div class="summary-card"><strong>解释边界</strong>'
        '<span>12 段峰谷窗口事后已知；结果只能比较窗口内机制，不能当成实时择时业绩</span></div></div>'
        '<div class="table-wrap"><table><thead><tr><th>case（5 bps）</th>'
        '<th>大熊复合</th><th>小熊复合</th><th>总熊复合</th><th>最差单段</th>'
        '<th>最大回撤</th><th>熊市平均持仓</th><th>强卖</th><th>换手</th><th>相对 0 bps</th>'
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def symbol_table(
    raw_returns: pd.DataFrame,
    symbol_contributions: pd.DataFrame,
    holding: pd.DataFrame,
    symbols: list[str],
) -> str:
    raw = raw_returns.pivot(index="symbol", columns="scope", values="compound_close_return")
    contrib = symbol_contributions.pivot(
        index="symbol", columns="case_id", values="contribution_to_initial_pct"
    )
    forced = (
        holding[holding["stop_id"] != "stop_off"]
        .groupby("symbol")["forced_exit_count"]
        .sum()
    )
    rows = []
    for symbol in symbols:
        rows.append(
            "<tr>"
            f"<td>{html.escape(symbol)}{' *' if symbol in STARRED else ''}</td>"
            f"<td>{raw.loc[symbol, 'major'] * 100:+.1f}%</td>"
            f"<td>{raw.loc[symbol, 'minor'] * 100:+.1f}%</td>"
            f"<td>{raw.loc[symbol, 'all'] * 100:+.1f}%</td>"
            f"<td>{contrib.loc[symbol, 'star_off__stop_off']:+.2f}%</td>"
            f"<td>{contrib.loc[symbol, 'star_on__stop_off']:+.2f}%</td>"
            f"<td>{int(forced.get(symbol, 0))}</td>"
            "</tr>"
        )
    return (
        '<section class="chart" id="symbol-table"><h2>单标的汇总（5 bps）</h2>'
        '<p>原始收益是每段 Close-to-Close 的复合值，不代表策略实际持有；贡献是连续事件账户的实现盈亏除以初始资金。强卖次数为 8 个启用止损 case 的合计。</p>'
        '<div class="table-wrap"><table><thead><tr><th>标的</th><th>原始大熊</th>'
        '<th>原始小熊</th><th>原始总熊</th><th>一坑/无强卖贡献</th>'
        '<th>双坑/无强卖贡献</th><th>强卖次数</th></tr></thead><tbody>'
        + "".join(rows)
        + "</tbody></table></div></section>"
    )


def markdown_case_table(metrics: pd.DataFrame) -> str:
    rows = [
        "| Case（5 bps） | 大熊 | 小熊 | 总熊 | 最差单段 | 最大回撤 | 强卖 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    indexed = metrics.set_index("case_id")
    for star_mode in ("star_off", "star_on"):
        for stop_id in STOP_ORDER:
            case_id = f"{star_mode}__{stop_id}"
            item = indexed.loc[case_id]
            rows.append(
                f"| {case_label(case_id)} | {item['major_bear_compound_return_pct']:+.2f}% | "
                f"{item['minor_bear_compound_return_pct']:+.2f}% | "
                f"{item['all_bear_compound_return_pct']:+.2f}% | "
                f"{item['all_bear_worst_interval_return_pct']:+.2f}% | "
                f"{item['max_drawdown_pct']:.2f}% | {int(item['forced_exit_count'])} |"
            )
    return "\n".join(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    record = load_run(context, args.run_id)
    if record.get("status") != "running":
        raise RuntimeError("Run must still be writable before analysis")
    blocks = load_blocks(context, args.run_id)
    if set(blocks) != {0.0, 5.0}:
        raise ValueError("formal report requires exactly 0 and 5 bps blocks")
    five = blocks[5.0]
    initial_cash = float(context.config["initial_cash"])
    symbols = list(context.config["parameters"]["universe"])

    stability = evaluate_stability(five["star_ablation"], five["stop_ablation"])
    family = family_contribution_frame(five["symbol_interval_contributions"], initial_cash)
    raw_returns = compound_asset_returns(five["asset_interval_returns"])
    symbol_contributions = symbol_contribution_frame(
        five["symbol_interval_contributions"], initial_cash
    )
    all_rows = five["interval_summary"][five["interval_summary"]["scope"] == "all"]
    best_row = all_rows.loc[all_rows["compound_return"].idxmax()]
    worst_row = all_rows.loc[all_rows["compound_return"].idxmin()]

    run_root = context.run_root(args.run_id)
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(exist_ok=True)
    family.to_csv(analysis_root / "family_contributions.csv", index=False, lineterminator="\n")
    raw_returns.to_csv(analysis_root / "symbol_raw_bear_returns.csv", index=False, lineterminator="\n")
    symbol_contributions.to_csv(
        analysis_root / "symbol_case_contributions.csv", index=False, lineterminator="\n"
    )
    summary = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "formal_cost_bps": 5.0,
        "best_descriptive_case": {
            "case_id": best_row["case_id"],
            "all_bear_compound_return_pct": float(best_row["compound_return"] * 100.0),
        },
        "worst_descriptive_case": {
            "case_id": worst_row["case_id"],
            "all_bear_compound_return_pct": float(worst_row["compound_return"] * 100.0),
        },
        "stability": stability,
        "five_bps_case_metrics": json_safe(five["metrics"].to_dict("records")),
        "five_bps_star_ablation": json_safe(five["star_ablation"].to_dict("records")),
        "five_bps_stop_ablation": json_safe(five["stop_ablation"].to_dict("records")),
        "five_bps_family_contributions": json_safe(family.to_dict("records")),
        "five_bps_symbol_raw_returns": json_safe(raw_returns.to_dict("records")),
        "five_bps_symbol_contributions": json_safe(symbol_contributions.to_dict("records")),
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    pair_text = (
        "、".join(f"{left}%–{right}%" for left, right in stability["stable_adjacent_stop_pairs_pct"])
        or "无"
    )
    notes = [
        "这是事件条件化的事后研究：12 段熊市起止峰谷由全样本人工标注并收紧，实盘当时不知道边界。",
        "LMT 去重为一个标的且归入星标；星标双坑仅改变预算权重，不复制证券或持仓状态。",
        "所有 case 使用 SMA200±3%；已取消旧实验的成交价±10%状态锁。强卖以本次真实进入成交价和随后完成 Close 的最高值为峰值。",
        "强卖阈值使用严格超过 6/8/10/12%；等于阈值不卖，卖后必须先回到上轨之下并再次严格上穿才能买回。",
        "信号在完成 Close 后形成，下一共同复权 Open 成交；同日先卖后买，允许小数股，现金不计息。",
        "当前成分和候选名单带有生存偏差/事后选择偏差；所谓最好 case 只作描述，不是参数推荐。",
        "原始单标的收益与策略贡献分开：前者不考虑持仓信号，后者来自共享现金账户的真实成交现金流。",
        "0 bps 是成本敏感性，单边 5 bps 是主结果；20 条路径全部保留。",
    ]
    spy = pd.read_csv(WORKSPACE_ROOT / "data/processed/daily/SPY.csv", parse_dates=["date"])
    start = pd.Timestamp(context.config["parameters"]["analysis_start"])
    end = pd.Timestamp(context.config["parameters"]["analysis_end"])
    spy = spy[(spy["date"] >= start) & (spy["date"] <= end)].copy()
    figures = [
        ReportFigure(
            div_id="market-bear_event_weighted_stops",
            title="SPY 与 12 段事后峰谷熊市窗口",
            figure=market_figure(spy, five["interval_schedule"]),
            kind="market",
        ),
        ReportFigure(
            div_id="performance-bear_event_weighted_stops",
            title="20 条连续事件账户净值与回撤",
            figure=performance_figure(blocks),
            kind="performance",
        ),
        ReportFigure(
            div_id="scope-bear_event_weighted_stops",
            title="5 bps：大熊、小熊与总熊整体表现",
            figure=scope_heatmap(five["interval_summary"]),
            kind="generic",
        ),
        ReportFigure(
            div_id="ablation-bear_event_weighted_stops",
            title="5 bps：星标权重与峰值强卖的配对消融",
            figure=factor_ablation_figure(five["star_ablation"], five["stop_ablation"]),
            kind="generic",
        ),
        ReportFigure(
            div_id="intervals-bear_event_weighted_stops",
            title="5 bps：12 段熊市逐段收益",
            figure=interval_heatmap(five["interval_returns"]),
            kind="generic",
        ),
        ReportFigure(
            div_id="families-bear_event_weighted_stops",
            title="5 bps：星标家族与普通家族的实现盈亏贡献",
            figure=family_figure(family),
            kind="generic",
        ),
        ReportFigure(
            div_id="symbols-bear_event_weighted_stops",
            title="5 bps：15 个标的对各 case 的实现盈亏贡献",
            figure=symbol_heatmap(symbol_contributions, symbols),
            kind="generic",
        ),
    ]
    subtitle = (
        f"5 bps 描述性最好：{case_label(best_row['case_id'])} "
        f"{best_row['compound_return'] * 100:+.2f}%；相邻止损稳定区间：{pair_text}"
    )
    report_html = render_interactive_report(
        title="熊市候选：星标双坑与峰值强卖消融",
        heading="15 标的熊市事件组合：权重与强制卖出是否生效",
        subtitle=subtitle,
        summary_html=metric_table(blocks),
        notes=notes,
        figures=figures,
        experiment=context.config,
        run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    extras = symbol_table(
        raw_returns,
        symbol_contributions,
        five["symbol_holding_stats"],
        symbols,
    ) + (
        '<section class="chart" id="downloads-bear-event-weighted"><h2>结果下载</h2><p>'
        '<a download href="BEAR_EVENT_WEIGHTED_STOPS/cost_5bps/interval_summary.csv">5 bps 大/小/总熊</a> · '
        '<a download href="BEAR_EVENT_WEIGHTED_STOPS/cost_5bps/interval_returns.csv">5 bps 逐段收益</a> · '
        '<a download href="BEAR_EVENT_WEIGHTED_STOPS/cost_5bps/star_ablation.csv">星标消融</a> · '
        '<a download href="BEAR_EVENT_WEIGHTED_STOPS/cost_5bps/stop_ablation.csv">强卖消融</a> · '
        '<a download href="BEAR_EVENT_WEIGHTED_STOPS/cost_5bps/symbol_interval_contributions.csv">逐标的逐段贡献</a> · '
        '<a download href="BEAR_EVENT_WEIGHTED_STOPS/cost_5bps/asset_interval_returns.csv">逐标的原始收益</a> · '
        '<a download href="BEAR_EVENT_WEIGHTED_STOPS/cost_5bps/transitions.csv">状态切换</a> · '
        '<a download href="BEAR_EVENT_WEIGHTED_STOPS/cost_0bps/interval_summary.csv">0 bps 汇总</a>'
        "</p></section>"
    )
    report_html = report_html.replace("</main>", extras + "</main>")
    (run_root / "report.html").write_text(report_html, encoding="utf-8")

    followup = (
        "满足冻结的描述性稳定条件，可考虑另建一个只使用实时可知熊市状态和前瞻候选池的实验；本 run 仍不可直接晋级模拟盘。"
        if stability["eligible_for_realtime_followup"]
        else "未同时满足双坑与相邻止损阈值的描述性稳定条件，不建议据此挑选一个历史最优阈值。"
    )
    (run_root / "report.md").write_text(
        f"""# 熊市候选：星标双坑与峰值强卖消融

## 结论

- 单边 5 bps 描述性最好 case 为 `{best_row['case_id']}`，总熊复合收益 `{best_row['compound_return'] * 100:+.2f}%`；最差 case 为 `{worst_row['case_id']}`，`{worst_row['compound_return'] * 100:+.2f}%`。
- 星标双坑在 5 个止损状态中的总熊正贡献次数为 `{stability['star_total_positive_case_count']}/5`。
- 同时在两种坑位模式下改善总熊、且大熊/小熊不呈系统性反向恶化的相邻止损区间：`{pair_text}`。
- 判定：{followup}

## 5 bps 全部 case

{markdown_case_table(five['metrics'])}

## 规则边界

- 固定 15 标的，其中 EQT、GIS、LMT、ORLY、AZO 为星标；LMT 只出现一次。
- `star_on` 时星标占两个坑，`star_off` 时全部一坑；所有 case 使用 SMA200±3%。
- 已删除成交价±10%锁；峰值强卖分别关闭或设为严格超过 6%、8%、10%、12%。
- 强卖后必须重新武装并再次上穿 SMA200+3%，不能立即买回。
- 熊市区间与候选名单均含事后信息，因此这是机制消融，不是可交易的实时择时结果。

## 交互报告

打开 `report.html` 可切换 20 条净值路径，并查看整体、大熊/小熊、星标家族、单标的、逐段与两组配对消融；底层 CSV 可直接下载。
""",
        encoding="utf-8",
    )

    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    tracked = [
        "backtest/quantkit/bear_event_sma_portfolio.py",
        "backtest/quantkit/trend_score_portfolio.py",
        "backtest/quantkit/execution.py",
        "backtest/quantkit/metrics.py",
        "backtest/quantkit/experiment.py",
        "backtest/quantkit/reporting.py",
        "backtest/scripts/run_bear_event_weighted_trailing_stop.py",
        "backtest/scripts/analyze_bear_event_weighted_trailing_stop.py",
        "backtest/scripts/validate_run.py",
        "backtest/tests/strategies/tim/test_bear_event_weighted_trailing_stop.py",
        "backtest/tests/strategies/tim/test_bear_event_sma_portfolio.py",
        "backtest/experiments/TIM/TIM-v0.40a.1__26-08-15__bear_market_weighted_trailing_stop_ablation/experiment.json",
        "backtest/requirements.lock",
        "backtest/report_templates/interactive_research_v4/page.html",
        "backtest/report_templates/interactive_research_v4/styles.css",
        "backtest/report_templates/interactive_research_v4/interactions.js",
        "research/market_views/subjective_spy_qqq_bear_markets_peak_to_trough.json",
    ]
    provenance = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": created_at,
        "software": {
            "python": platform.python_version(),
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
    for cost in context.config["cost_scenarios_bps_per_side"]:
        manifest_path = block_root(context, args.run_id, SYMBOL_BLOCK, float(cost)) / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for relative in manifest["source_files"]:
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

This immutable run evaluates the frozen 2×5 weighted-slot and peak-drawdown ablation over 12 hindsight bear intervals.

- `report.html` / `report.md`: interactive and compact reports.
- `analysis/summary.json`: machine-readable stability decision and complete 5 bps results.
- `analysis/family_contributions.csv`: starred versus regular realized-P&L attribution.
- `analysis/symbol_raw_bear_returns.csv`: per-symbol unfiltered bear-interval returns.
- `analysis/symbol_case_contributions.csv`: per-symbol strategy contribution by case.
- `BEAR_EVENT_WEIGHTED_STOPS/cost_0bps/` and `cost_5bps/`: ledgers, signals, transitions, ablations, interval and symbol diagnostics.
- `provenance.json`: exact code, configuration, research-input and market-data hashes.
- `validation.json`: tests, audit, hash verification and real-browser evidence.
""",
        encoding="utf-8",
    )
    artifact_manifest = {
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
    print(
        f"Wrote {run_root / 'report.html'}; best={best_row['case_id']} "
        f"({best_row['compound_return'] * 100:+.2f}%); stable stop pairs={pair_text}"
    )


if __name__ == "__main__":
    main()
