#!/usr/bin/env python3
"""Build the v5 report for the 2005-2010 Strategy1 rotation factorial."""

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

from quantkit.experiment import assert_run_writable, load_experiment, load_run, sha256
from quantkit.reporting import ReportFigure, render_interactive_report


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/ROT/ROT-v0.50a.4__26-08-28__nasdaq100_strategy1_rotation_2005_2010"
)
SYMBOL = "NASDAQ100_STRATEGY1_ROTATION_2005_2010"
CASE_IDS = (
    "FAST0_TOP20_90_REBAL",
    "FAST0_TOP20_90_DRIFT",
    "FAST0_ALL_90_REBAL",
    "FAST0_ALL_90_DRIFT",
    "FAST0_ALL_80_REBAL",
    "FAST0_ALL_80_DRIFT",
    "FAST1_TOP20_90_REBAL",
    "FAST1_TOP20_90_DRIFT",
    "FAST1_ALL_90_REBAL",
    "FAST1_ALL_90_DRIFT",
    "FAST1_ALL_80_REBAL",
    "FAST1_ALL_80_DRIFT",
)
ALL_CASE_IDS = CASE_IDS + ("NDX100_ALL_REBAL", "QQQ_BUY_HOLD")
CASE_LABELS = {
    "FAST0_TOP20_90_REBAL": "无快卖 · Top20>90 · 恢复等权",
    "FAST0_TOP20_90_DRIFT": "无快卖 · Top20>90 · 只进出",
    "FAST0_ALL_90_REBAL": "无快卖 · 全部>90 · 恢复等权",
    "FAST0_ALL_90_DRIFT": "无快卖 · 全部>90 · 只进出",
    "FAST0_ALL_80_REBAL": "无快卖 · 全部>80 · 恢复等权",
    "FAST0_ALL_80_DRIFT": "无快卖 · 全部>80 · 只进出",
    "FAST1_TOP20_90_REBAL": "快卖 · Top20>90 · 恢复等权",
    "FAST1_TOP20_90_DRIFT": "快卖 · Top20>90 · 只进出",
    "FAST1_ALL_90_REBAL": "快卖 · 全部>90 · 恢复等权",
    "FAST1_ALL_90_DRIFT": "快卖 · 全部>90 · 只进出",
    "FAST1_ALL_80_REBAL": "快卖 · 全部>80 · 恢复等权",
    "FAST1_ALL_80_DRIFT": "快卖 · 全部>80 · 只进出",
    "NDX100_ALL_REBAL": "同期全部成分等权",
    "QQQ_BUY_HOLD": "QQQ Buy & Hold",
}
COLORS = {
    "FAST0_TOP20_90_REBAL": "#2563eb",
    "FAST0_TOP20_90_DRIFT": "#93c5fd",
    "FAST0_ALL_90_REBAL": "#7c3aed",
    "FAST0_ALL_90_DRIFT": "#c4b5fd",
    "FAST0_ALL_80_REBAL": "#0f766e",
    "FAST0_ALL_80_DRIFT": "#5eead4",
    "FAST1_TOP20_90_REBAL": "#dc2626",
    "FAST1_TOP20_90_DRIFT": "#fca5a5",
    "FAST1_ALL_90_REBAL": "#c2410c",
    "FAST1_ALL_90_DRIFT": "#fdba74",
    "FAST1_ALL_80_REBAL": "#a16207",
    "FAST1_ALL_80_DRIFT": "#fde047",
    "NDX100_ALL_REBAL": "#475569",
    "QQQ_BUY_HOLD": "#111827",
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


def drawdown(equity: pd.Series) -> pd.Series:
    values = equity.astype(float)
    return (values / values.cummax() - 1.0) * 100.0


def load_blocks(run_root: Path) -> dict[float, dict[str, Any]]:
    blocks: dict[float, dict[str, Any]] = {}
    for cost in (0.0, 5.0):
        root = run_root / SYMBOL / f"cost_{cost:g}bps"
        blocks[cost] = {
            "root": root,
            "metrics": pd.read_csv(root / "metrics.csv"),
            "metrics_json": json.loads((root / "metrics.json").read_text(encoding="utf-8")),
            "daily": pd.read_csv(root / "daily.csv.gz", parse_dates=["date"]),
            "benchmark": pd.read_csv(root / "qqq_buy_hold_daily.csv", parse_dates=["date"]),
            "selections": pd.read_csv(root / "selections.csv.gz", parse_dates=["date"]),
        }
    return blocks


def metric_row(metrics: pd.DataFrame, case_id: str) -> pd.Series:
    return metrics.set_index("case_id").loc[case_id]


def performance_figure(blocks: dict[float, dict[str, Any]]) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.68, 0.32],
        subplot_titles=("12格策略、全部成分等权与QQQ净值", "从各自历史峰值回撤"),
    )
    for cost in (0.0, 5.0):
        daily = blocks[cost]["daily"]
        for case_id in CASE_IDS + ("NDX100_ALL_REBAL",):
            frame = daily[daily["case_id"].eq(case_id)].sort_values("date")
            key = f"{case_id.lower()}_{cost:g}bps"
            for row, values, panel, showlegend in (
                (1, frame["equity"], "equity", True),
                (2, drawdown(frame["equity"]), "drawdown", False),
            ):
                figure.add_trace(
                    go.Scatter(
                        x=frame["date"],
                        y=values,
                        name=f"{CASE_LABELS[case_id]} · {cost:g} bps",
                        showlegend=showlegend,
                        visible=True if cost == 5 else "legendonly",
                        line={
                            "color": COLORS[case_id],
                            "width": 2.8 if case_id == "FAST0_TOP20_90_REBAL" else 1.35,
                            "dash": "solid" if cost == 5 else "dot",
                        },
                        meta={
                            "series_key": key,
                            "panel": panel,
                            "label": f"{CASE_LABELS[case_id]} · {cost:g} bps",
                            "cost_bps": cost,
                        },
                    ),
                    row=row,
                    col=1,
                )
        qqq = blocks[cost]["benchmark"].sort_values("date")
        key = f"qqq_buy_hold_{cost:g}bps"
        for row, values, panel, showlegend in (
            (1, qqq["equity"], "equity", True),
            (2, drawdown(qqq["equity"]), "drawdown", False),
        ):
            figure.add_trace(
                go.Scatter(
                    x=qqq["date"],
                    y=values,
                    name=f"QQQ Buy & Hold · {cost:g} bps",
                    showlegend=showlegend,
                    visible=True if cost == 5 else "legendonly",
                    line={"color": COLORS["QQQ_BUY_HOLD"], "width": 3.0, "dash": "dash"},
                    meta={
                        "series_key": key,
                        "panel": panel,
                        "label": f"QQQ Buy & Hold · {cost:g} bps",
                        "is_benchmark": panel == "equity" and cost == 5,
                        "cost_bps": cost,
                    },
                ),
                row=row,
                col=1,
            )
    figure.update_layout(
        height=900,
        hovermode="x unified",
        showlegend=False,
        uirevision="strategy1-2005-2010-performance-v1",
    )
    figure.update_yaxes(title_text="10万美元账户净值", row=1, col=1)
    figure.update_yaxes(title_text="%", row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def market_figure(qqq: pd.DataFrame, selections: pd.DataFrame) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.07,
        row_heights=[0.65, 0.35],
        subplot_titles=("QQQ拆股及股息调整价格", "每两日决策时可持有证券数"),
    )
    figure.add_trace(
        go.Candlestick(
            x=qqq["date"],
            open=qqq["open"],
            high=qqq["high"],
            low=qqq["low"],
            close=qqq["close"],
            name="QQQ",
            increasing_line_color="#15803d",
            decreasing_line_color="#dc2626",
            meta={"series_key": "qqq_price", "panel": "price", "label": "QQQ"},
        ),
        row=1,
        col=1,
    )
    for case_id, label in (
        ("FAST0_TOP20_90_REBAL", "Top20且>90"),
        ("FAST0_ALL_90_REBAL", "全部>90"),
        ("FAST0_ALL_80_REBAL", "全部>80"),
        ("NDX100_ALL_REBAL", "全部历史成分"),
    ):
        counts = (
            selections[selections["case_id"].eq(case_id)]
            .groupby("date", as_index=False)
            .size()
            .rename(columns={"size": "count"})
        )
        figure.add_trace(
            go.Scatter(
                x=counts["date"],
                y=counts["count"],
                name=label,
                line={"color": COLORS[case_id], "width": 1.8},
                meta={
                    "series_key": case_id.lower(),
                    "panel": "market",
                    "label": label,
                    "control_group": "breadth",
                    "control_group_label": "候选范围",
                },
            ),
            row=2,
            col=1,
        )
    figure.update_layout(height=830, hovermode="x unified", showlegend=False)
    figure.update_yaxes(title_text="美元", row=1, col=1)
    figure.update_yaxes(title_text="只", row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def exposure_figure(block: dict[str, Any]) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        subplot_titles=("股票仓位与现金", "实际持股数与快卖锁数量"),
    )
    chosen = (
        "FAST0_TOP20_90_REBAL",
        "FAST1_TOP20_90_REBAL",
        "FAST0_ALL_80_REBAL",
        "FAST0_ALL_80_DRIFT",
        "NDX100_ALL_REBAL",
    )
    daily = block["daily"]
    for case_id in chosen:
        frame = daily[daily["case_id"].eq(case_id)].sort_values("date")
        figure.add_trace(
            go.Scatter(
                x=frame["date"],
                y=frame["gross_exposure"] * 100.0,
                name=f"{CASE_LABELS[case_id]} · 股票",
                line={"color": COLORS[case_id], "width": 1.7},
            ),
            row=1,
            col=1,
        )
        figure.add_trace(
            go.Scatter(
                x=frame["date"],
                y=(1.0 - frame["gross_exposure"]) * 100.0,
                name=f"{CASE_LABELS[case_id]} · 现金",
                visible="legendonly",
                line={"color": COLORS[case_id], "width": 1.2, "dash": "dot"},
            ),
            row=1,
            col=1,
        )
        figure.add_trace(
            go.Scatter(
                x=frame["date"],
                y=frame["holdings_count"],
                name=f"{CASE_LABELS[case_id]} · 持股数",
                line={"color": COLORS[case_id], "width": 1.7},
            ),
            row=2,
            col=1,
        )
        if case_id.startswith("FAST1"):
            figure.add_trace(
                go.Scatter(
                    x=frame["date"],
                    y=frame["fast_locked_count"],
                    name=f"{CASE_LABELS[case_id]} · 快卖锁",
                    line={"color": "#111827", "width": 1.1, "dash": "dot"},
                ),
                row=2,
                col=1,
            )
    figure.update_layout(height=780, hovermode="x unified", legend={"orientation": "h"})
    figure.update_yaxes(title_text="净值占比 %", range=[0, 102], row=1, col=1)
    figure.update_yaxes(title_text="只", row=2, col=1)
    return figure


def factorial_figure(metrics: pd.DataFrame, baseline_cagr: float) -> go.Figure:
    indexed = metrics.set_index("case_id")
    figure = make_subplots(
        rows=1,
        cols=2,
        horizontal_spacing=0.12,
        subplot_titles=("不使用快卖", "使用快卖"),
    )
    selections = (("TOP20_90", "Top20且>90"), ("ALL_90", "全部>90"), ("ALL_80", "全部>80"))
    allocations = (("REBAL", "恢复等权"), ("DRIFT", "只进出"))
    for column, fast in enumerate((0, 1), start=1):
        z: list[list[float]] = []
        text_values: list[list[str]] = []
        for selection_key, _ in selections:
            row_values: list[float] = []
            row_text: list[str] = []
            for allocation_key, _ in allocations:
                case_id = f"FAST{fast}_{selection_key}_{allocation_key}"
                cagr = float(indexed.at[case_id, "cagr_pct"])
                row_values.append(cagr - baseline_cagr)
                row_text.append(f"CAGR {cagr:.2f}%<br>相对基线 {cagr - baseline_cagr:+.2f}pp")
            z.append(row_values)
            text_values.append(row_text)
        figure.add_trace(
            go.Heatmap(
                z=z,
                x=[item[1] for item in allocations],
                y=[item[1] for item in selections],
                text=text_values,
                texttemplate="%{text}",
                hovertemplate="%{y} · %{x}<br>%{text}<extra></extra>",
                colorscale="RdBu",
                zmid=0,
                colorbar={"title": "相对基线<br>CAGR pp"} if column == 2 else None,
                showscale=column == 2,
            ),
            row=1,
            col=column,
        )
    figure.update_layout(height=560, margin={"l": 80, "r": 60, "t": 80, "b": 70})
    return figure


def pair_effects(metrics: pd.DataFrame, cost: float) -> pd.DataFrame:
    indexed = metrics.set_index("case_id")
    rows: list[dict[str, Any]] = []
    for base_id in CASE_IDS[:6]:
        fast_id = base_id.replace("FAST0", "FAST1", 1)
        base = indexed.loc[base_id]
        fast = indexed.loc[fast_id]
        rows.append(
            {
                "cost_bps": cost,
                "configuration": CASE_LABELS[base_id].replace("无快卖 · ", ""),
                "base_case_id": base_id,
                "fast_case_id": fast_id,
                "cagr_delta_pct_points": float(fast["cagr_pct"] - base["cagr_pct"]),
                "sharpe_delta": float(fast["sharpe"] - base["sharpe"]),
                "drawdown_improvement_pct_points": float(
                    fast["max_drawdown_pct"] - base["max_drawdown_pct"]
                ),
                "turnover_delta_multiple": float(
                    fast["turnover_multiple"] - base["turnover_multiple"]
                ),
                "fast_exit_count": int(fast["fast_exit_count"]),
            }
        )
    return pd.DataFrame(rows)


def pair_effect_figure(effects: pd.DataFrame) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        vertical_spacing=0.18,
        subplot_titles=("加入快卖后的CAGR变化", "加入快卖后的最大回撤改善"),
    )
    colors = ["#2563eb" if value >= 0 else "#dc2626" for value in effects["cagr_delta_pct_points"]]
    figure.add_trace(
        go.Bar(
            x=effects["configuration"],
            y=effects["cagr_delta_pct_points"],
            marker_color=colors,
            text=effects["cagr_delta_pct_points"].map(lambda value: f"{value:+.2f}"),
            textposition="outside",
            name="CAGR变化",
        ),
        row=1,
        col=1,
    )
    dd_colors = ["#15803d" if value >= 0 else "#dc2626" for value in effects["drawdown_improvement_pct_points"]]
    figure.add_trace(
        go.Bar(
            x=effects["configuration"],
            y=effects["drawdown_improvement_pct_points"],
            marker_color=dd_colors,
            text=effects["drawdown_improvement_pct_points"].map(lambda value: f"{value:+.2f}"),
            textposition="outside",
            name="回撤改善",
        ),
        row=2,
        col=1,
    )
    figure.add_hline(y=0, line_color="#475569", line_width=1, row=1, col=1)
    figure.add_hline(y=0, line_color="#475569", line_width=1, row=2, col=1)
    figure.update_layout(height=760, showlegend=False, margin={"b": 140})
    figure.update_xaxes(tickangle=-20, row=2, col=1)
    figure.update_yaxes(title_text="百分点", row=1, col=1)
    figure.update_yaxes(title_text="百分点（正数更好）", row=2, col=1)
    return figure


def concentration_diagnostics(run_root: Path, daily: pd.DataFrame) -> pd.DataFrame:
    root = run_root / SYMBOL / "cost_5bps"
    positions = pd.read_csv(root / "positions.csv.gz", parse_dates=["date"])
    positions = positions[positions["shares"].abs().gt(1e-12)].copy()
    prices = pd.read_csv(
        run_root / "shared/price_panel.csv.gz",
        usecols=["date", "symbol", "close"],
        parse_dates=["date"],
    )
    positions = positions.merge(prices, on=["date", "symbol"], how="left", validate="many_to_one")
    if positions["close"].isna().any():
        raise AssertionError("Missing close while building concentration diagnostics")
    positions["market_value"] = positions["shares"] * positions["close"]
    totals = positions.groupby(["case_id", "date"])["market_value"].transform("sum")
    positions["position_weight"] = positions["market_value"] / totals
    positions["weight_sq"] = positions["position_weight"] ** 2
    by_day = positions.groupby(["case_id", "date"], as_index=False).agg(
        maximum_single_name_weight=("position_weight", "max"),
        hhi=("weight_sq", "sum"),
    )
    by_day["effective_holdings"] = 1.0 / by_day["hhi"]
    output = by_day.groupby("case_id", as_index=False).agg(
        average_maximum_single_name_weight=("maximum_single_name_weight", "mean"),
        p95_maximum_single_name_weight=("maximum_single_name_weight", lambda values: values.quantile(0.95)),
        average_effective_holdings=("effective_holdings", "mean"),
        minimum_effective_holdings=("effective_holdings", "min"),
    )
    exposure = daily.groupby("case_id", as_index=False).agg(
        average_gross_exposure=("gross_exposure", "mean"),
        average_holdings_count=("holdings_count", "mean"),
    )
    return output.merge(exposure, on="case_id", how="left", validate="one_to_one")


def concentration_figure(concentration: pd.DataFrame) -> go.Figure:
    frame = concentration[concentration["case_id"].isin(CASE_IDS)].copy()
    frame["label"] = frame["case_id"].map(CASE_LABELS)
    figure = go.Figure()
    figure.add_trace(
        go.Bar(
            x=frame["label"],
            y=frame["average_maximum_single_name_weight"] * 100.0,
            name="日均最大单股权重",
            marker_color="#2563eb",
        )
    )
    figure.add_trace(
        go.Bar(
            x=frame["label"],
            y=frame["p95_maximum_single_name_weight"] * 100.0,
            name="95分位最大单股权重",
            marker_color="#f97316",
        )
    )
    figure.update_layout(
        height=610,
        barmode="group",
        yaxis_title="股票仓位内部占比 %",
        xaxis={"tickangle": -28},
        legend={"orientation": "h"},
        margin={"b": 190},
    )
    return figure


def annual_returns(block: dict[str, Any]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for case_id in CASE_IDS + ("NDX100_ALL_REBAL",):
        frame = block["daily"][block["daily"]["case_id"].eq(case_id)][["date", "equity"]].copy()
        frame["case_id"] = case_id
        frames.append(frame)
    qqq = block["benchmark"][["date", "equity"]].copy()
    qqq["case_id"] = "QQQ_BUY_HOLD"
    data = pd.concat(frames + [qqq], ignore_index=True)
    data["year"] = data["date"].dt.year
    rows = []
    for (case_id, year), group in data.groupby(["case_id", "year"]):
        group = group.sort_values("date")
        rows.append(
            {
                "case_id": case_id,
                "year": int(year),
                "return_pct": (float(group["equity"].iloc[-1]) / float(group["equity"].iloc[0]) - 1.0) * 100.0,
            }
        )
    return pd.DataFrame(rows)


def metric_table(blocks: dict[float, dict[str, Any]]) -> str:
    rows: list[str] = []
    for cost in (5.0, 0.0):
        metrics = blocks[cost]["metrics"].set_index("case_id")
        for case_id in ALL_CASE_IDS:
            item = metrics.loc[case_id]
            rows.append(
                "<tr>"
                f"<td>{html.escape(CASE_LABELS[case_id])}</td><td>{cost:g}</td>"
                f"<td>{item['cagr_pct']:.2f}%</td><td>{item['sharpe']:.3f}</td>"
                f"<td>{item['sortino']:.3f}</td><td>{item['max_drawdown_pct']:.2f}%</td>"
                f"<td>{item['exposure_pct']:.1f}%</td>"
                f"<td>{item['average_holdings_count']:.1f}</td>"
                f"<td>{item['turnover_multiple']:.1f}×</td>"
                f"<td>{int(item['fast_exit_count'])}</td><td>{int(item['order_count']):,}</td></tr>"
            )
    return (
        '<div class="summary-grid">'
        '<div class="summary-card"><strong>正式矩阵</strong><span>快卖开关 × 3种范围 × 2种资金管理 = 12格</span></div>'
        '<div class="summary-card"><strong>主成本</strong><span>5 bps/边；0 bps用于识别摩擦</span></div>'
        '<div class="summary-card"><strong>时间</strong><span>2005-01-03 至 2010-12-31</span></div>'
        '<div class="summary-card"><strong>数据边界</strong><span>历史成分与行情仍为pending_review候选包</span></div>'
        '</div><div class="table-wrap"><table><thead><tr>'
        '<th>路径</th><th>bps/边</th><th>CAGR</th><th>Sharpe</th><th>Sortino</th>'
        '<th>最大回撤</th><th>暴露</th><th>平均持股</th><th>换手</th><th>快卖</th><th>订单</th>'
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    assert_run_writable(context, args.run_id)
    record = load_run(context, args.run_id)
    incomplete = [block["block_id"] for block in record["expected_blocks"] if block["status"] != "completed"]
    if incomplete:
        raise RuntimeError(f"Incomplete blocks: {incomplete}")
    run_root = context.run_root(args.run_id)
    blocks = load_blocks(run_root)
    five_metrics = blocks[5.0]["metrics"]
    strategy_metrics = five_metrics[five_metrics["case_id"].isin(CASE_IDS)].set_index("case_id")
    baseline_id = "FAST0_TOP20_90_REBAL"
    baseline = strategy_metrics.loc[baseline_id]
    qqq = metric_row(five_metrics, "QQQ_BUY_HOLD")
    all_member = metric_row(five_metrics, "NDX100_ALL_REBAL")
    best_strategy_id = str(strategy_metrics["cagr_pct"].idxmax())
    best_strategy = strategy_metrics.loc[best_strategy_id]

    analysis_root = run_root / "analysis"
    analysis_root.mkdir(exist_ok=True)
    combined = []
    effects = []
    for cost in (0.0, 5.0):
        frame = blocks[cost]["metrics"].copy()
        frame.insert(1, "cost_bps_reported", cost)
        combined.append(frame)
        effects.append(pair_effects(frame, cost))
    results = pd.concat(combined, ignore_index=True)
    results.to_csv(analysis_root / "results_table.csv", index=False, lineterminator="\n")
    effects_frame = pd.concat(effects, ignore_index=True)
    effects_frame.to_csv(analysis_root / "fast_exit_pair_effects.csv", index=False, lineterminator="\n")
    five_effects = effects_frame[effects_frame["cost_bps"].eq(5.0)].copy()
    concentration = concentration_diagnostics(run_root, blocks[5.0]["daily"])
    concentration.to_csv(analysis_root / "concentration_diagnostics.csv", index=False, lineterminator="\n")
    years = annual_returns(blocks[5.0])
    years.to_csv(analysis_root / "annual_returns_5bps.csv", index=False, lineterminator="\n")

    shared = run_root / "shared"
    qqq_prices = pd.read_csv(WORKSPACE_ROOT / "data/processed/daily/QQQ.csv", parse_dates=["date"])
    start = pd.Timestamp(context.config["parameters"]["analysis_start"])
    end = pd.Timestamp(context.config["parameters"]["analysis_end"])
    qqq_prices = qqq_prices[qqq_prices["date"].between(start, end)].copy()

    fast_cagr_wins = int(five_effects["cagr_delta_pct_points"].gt(0).sum())
    fast_dd_wins = int(five_effects["drawdown_improvement_pct_points"].gt(0).sum())
    cagr_gap = float(baseline["cagr_pct"] - qqq["cagr_pct"])
    sharpe_gap = float(baseline["sharpe"] - qqq["sharpe"])
    dd_gap = float(baseline["max_drawdown_pct"] - qqq["max_drawdown_pct"])
    promotion_passed = bool(
        baseline["cagr_pct"] > qqq["cagr_pct"]
        and baseline["sharpe"] > qqq["sharpe"]
        and baseline["max_drawdown_pct"] >= qqq["max_drawdown_pct"]
    )
    summary = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "data_status": blocks[5.0]["metrics_json"]["data_status"],
        "formal_strategy_count_per_cost": 12,
        "total_path_count": 28,
        "primary_cost_bps": 5.0,
        "baseline1_case_id": baseline_id,
        "baseline1_metrics": json_safe(baseline.to_dict()),
        "best_strategy_case_id": best_strategy_id,
        "best_strategy_metrics": json_safe(best_strategy.to_dict()),
        "all_member_baseline_metrics": json_safe(all_member.to_dict()),
        "qqq_buy_hold_metrics": json_safe(qqq.to_dict()),
        "baseline1_cagr_gap_vs_qqq_pct_points": cagr_gap,
        "baseline1_sharpe_gap_vs_qqq": sharpe_gap,
        "baseline1_drawdown_gap_vs_qqq_pct_points": dd_gap,
        "fast_exit_cagr_wins_out_of_six": fast_cagr_wins,
        "fast_exit_drawdown_wins_out_of_six": fast_dd_wins,
        "promotion_criteria_passed": promotion_passed,
        "max_cross_check_differences": blocks[5.0]["metrics_json"]["max_cross_check_differences"],
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    report_html = render_interactive_report(
        title="2005–2010 Nasdaq-100 Strategy1轮动完整消融",
        heading="快卖、候选范围与是否恢复等权，哪一个真正改善轮动",
        subtitle=(
            f"历史时点成分 · 12格完整交叉 · 5 bps基线CAGR {baseline['cagr_pct']:.2f}% "
            f"/ QQQ {qqq['cagr_pct']:.2f}%"
        ),
        summary_html=metric_table(blocks),
        notes=[
            "12格中，5 bps表现最好的仍是原始基线：不快卖、Top20且分数严格高于90、每两日恢复等权；它的CAGR和Sharpe都低于同期QQQ，最大回撤也略深。",
            f"快卖在六组一一配对里只有{fast_cagr_wins}组提高CAGR、{fast_dd_wins}组改善最大回撤，不能视为稳定的主效应。",
            "把范围扩大到全部>90或全部>80没有改善原始基线；只处理进出而不恢复等权通常造成更明显的单股集中和结果不稳定。",
            f"全部历史成分每两日等权的5 bps CAGR为{all_member['cagr_pct']:.2f}%，高于QQQ的{qqq['cagr_pct']:.2f}%，但最大回撤为{all_member['max_drawdown_pct']:.2f}%，风险没有同步改善。",
            "历史成分和个股行情仍是pending_review候选包；结果是探索性证据，不升级为正式ROT结论。",
        ],
        figures=[
            ReportFigure(
                "market-strategy1-rotation-2005-2010",
                "QQQ与三种门槛下的候选宽度",
                market_figure(qqq_prices, blocks[5.0]["selections"]),
                "market",
            ),
            ReportFigure(
                "performance-strategy1-rotation-2005-2010",
                "12格策略、全部成分等权、QQQ与回撤",
                performance_figure(blocks),
                "performance",
            ),
            ReportFigure(
                "exposure-strategy1-rotation-2005-2010",
                "代表路径的股票仓位、现金、持股数与快卖锁",
                exposure_figure(blocks[5.0]),
                "generic",
            ),
            ReportFigure(
                "factorial-strategy1-rotation-2005-2010",
                "5 bps完整交叉：颜色是相对原始基线的CAGR变化",
                factorial_figure(five_metrics, float(baseline["cagr_pct"])),
                "generic",
            ),
            ReportFigure(
                "fast-effect-strategy1-rotation-2005-2010",
                "同条件加入快卖后的配对变化",
                pair_effect_figure(five_effects),
                "generic",
            ),
            ReportFigure(
                "concentration-strategy1-rotation-2005-2010",
                "5 bps路径中的单股集中度",
                concentration_figure(concentration),
                "generic",
            ),
        ],
        experiment=context.config,
        run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    (run_root / "report.html").write_text(report_html, encoding="utf-8")
    (run_root / "report.md").write_text(
        f"""# 2005–2010 Nasdaq-100 Strategy1轮动完整消融

## 结论

- 5 bps下，12格中最好的策略是 `{best_strategy_id}`：CAGR `{best_strategy['cagr_pct']:.3f}%`、Sharpe `{best_strategy['sharpe']:.3f}`、最大回撤 `{best_strategy['max_drawdown_pct']:.2f}%`。
- 原始基线 `{baseline_id}` 相对QQQ：CAGR `{cagr_gap:+.3f}`个百分点、Sharpe `{sharpe_gap:+.3f}`、最大回撤差 `{dd_gap:+.3f}`个百分点。
- 快卖在六组配对中有 `{fast_cagr_wins}/6` 组提高CAGR、`{fast_dd_wins}/6` 组改善最大回撤，未形成稳定优势。
- 全部历史成分每两日等权：CAGR `{all_member['cagr_pct']:.3f}%`、Sharpe `{all_member['sharpe']:.3f}`、最大回撤 `{all_member['max_drawdown_pct']:.2f}%`。
- 判定：未通过晋级标准；候选数据状态也禁止晋级。

## 研究边界

本报告完整呈现快卖开关、三种选择范围和两种资金管理的12格，在0与5 bps分别执行，并与同期全部历史成分等权及QQQ Buy & Hold比较。历史成分与个股行情仍为 `pending_review`，结论只作探索。
""",
        encoding="utf-8",
    )

    tracked = [
        "backtest/quantkit/nasdaq100_strategy1_rotation_factorial.py",
        "backtest/quantkit/execution.py",
        "backtest/quantkit/experiment.py",
        "backtest/quantkit/reporting.py",
        "backtest/scripts/run_nasdaq100_strategy1_rotation_factorial.py",
        "backtest/scripts/analyze_nasdaq100_strategy1_rotation_factorial.py",
        "backtest/scripts/finalize_nasdaq100_strategy1_rotation_factorial.py",
        "backtest/scripts/smoke_nasdaq100_strategy1_rotation_2005_2010_factorial_report.mjs",
        "backtest/scripts/validate_run.py",
        "backtest/tests/strategies/rot/test_nasdaq100_strategy1_rotation_factorial.py",
        "backtest/requirements.lock",
        "backtest/report_templates/interactive_research_v5/page.html",
        "backtest/report_templates/interactive_research_v5/styles.css",
        "backtest/report_templates/interactive_research_v5/interactions.js",
    ]
    provenance = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": summary["created_at_utc"],
        "software": {
            "python": platform.python_version(),
            "lib_pybroker": "1.2.12",
            "plotly": plotly.__version__,
        },
        "source_files": {},
    }
    for relative in tracked:
        path = WORKSPACE_ROOT / relative
        provenance["source_files"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    shared_manifest = json.loads((shared / "manifest.json").read_text(encoding="utf-8"))
    for relative, expected_hash in shared_manifest["source_files"].items():
        path = WORKSPACE_ROOT / relative
        actual_hash = sha256(path)
        if actual_hash != expected_hash:
            raise AssertionError(f"Shared source changed during analysis: {relative}")
        provenance["source_files"][relative] = {"bytes": path.stat().st_size, "sha256": actual_hash}
    (run_root / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (run_root / "README.md").write_text(
        f"""# Run {args.run_id}

Candidate-data 2005–2010 Nasdaq-100 Strategy1 rotation factorial.

- `report.html` / `report.pdf`: interactive and printable v5 reports.
- `analysis/`: complete metrics, paired fast-exit effects, annual returns and concentration diagnostics.
- `{SYMBOL}/`: 0/5 bps PyBroker and independent-ledger artifacts.
- `shared/`: frozen point-in-time state, prices and source hashes.
""",
        encoding="utf-8",
    )
    print(
        f"Wrote {run_root / 'report.html'}; best={best_strategy_id}; "
        f"baseline-vs-QQQ CAGR={cagr_gap:+.3f}pp; promotion={promotion_passed}"
    )


if __name__ == "__main__":
    main()
