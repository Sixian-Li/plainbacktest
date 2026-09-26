#!/usr/bin/env python3
"""Render six Hold-versus-target-specific-SMA response charts."""

from __future__ import annotations

import argparse
import html
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
import plotly
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from quantkit.experiment import block_root, load_experiment, load_run, sha256
from quantkit.paths import BACKTEST_ROOT
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts.run_bear6_sma_fine_windows import (
    EXPECTED_RANGES,
    EXPECTED_TARGETS,
    STRATEGY_HOLD,
    SYMBOL_BLOCK,
    build_windows_by_target,
)


WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/TIM/"
    "TIM-v0.40b.3__26-08-24__bear6_sma_fine_window_scan"
)
SCOPE_LABELS = {
    "minor": "总小熊",
    "major": "总大熊",
    "all": "总熊",
    "all_ex_2000_2002": "总熊（不包含2000–2002）",
}
DEFAULT_OBSERVATION = "scope:all_ex_2000_2002"
PLATEAU_TOLERANCE_PP = 5.0
PLATEAU_MIN_WINDOWS = 3


def read_csv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    for column in (
        "date",
        "start",
        "end",
        "entry_execution_date",
        "exit_execution_date",
        "signal_date",
        "entry_date",
        "exit_date",
    ):
        if column in frame:
            frame[column] = pd.to_datetime(frame[column])
    return frame


def load_blocks(context: Any, run_id: str) -> dict[float, dict[str, Any]]:
    blocks: dict[float, dict[str, Any]] = {}
    for cost in context.config["cost_scenarios_bps_per_side"]:
        cost_value = float(cost)
        root = block_root(context, run_id, SYMBOL_BLOCK, cost_value)
        if not (root / "manifest.json").is_file():
            raise FileNotFoundError(f"missing completed block: {root}")
        blocks[cost_value] = {
            "root": root,
            "metrics": read_csv(root / "metrics.csv"),
            "interval_returns": read_csv(root / "interval_returns.csv"),
            "scope_summary": read_csv(root / "scope_summary.csv"),
            "case_index": read_csv(root / "case_index.csv"),
            "date_index": read_csv(root / "date_index.csv"),
            "daily": np.load(root / "daily_state.npz"),
        }
    return blocks


def observation_definitions(interval_returns: pd.DataFrame) -> list[dict[str, Any]]:
    baseline = interval_returns[
        (interval_returns["target"] == EXPECTED_TARGETS[0])
        & (interval_returns["strategy_id"] == STRATEGY_HOLD)
    ].sort_values("ordinal")
    if len(baseline) != 12:
        raise ValueError("Hold baseline must contain all 12 bear intervals")
    rows: list[dict[str, Any]] = []
    for order, row in enumerate(baseline.itertuples(index=False)):
        rows.append(
            {
                "observation_order": order,
                "observation_id": f"interval:{row.interval_id}",
                "label": (
                    f"{row.label} · {pd.Timestamp(row.start).date()}～"
                    f"{pd.Timestamp(row.end).date()}"
                ),
                "kind": "interval",
                "key": str(row.interval_id),
            }
        )
    for scope in ("minor", "major", "all", "all_ex_2000_2002"):
        rows.append(
            {
                "observation_order": len(rows),
                "observation_id": f"scope:{scope}",
                "label": SCOPE_LABELS[scope],
                "kind": "scope",
                "key": scope,
            }
        )
    return rows


def build_curve_points(
    block: dict[str, Any],
    windows_by_target: Mapping[str, list[int]],
) -> pd.DataFrame:
    intervals = block["interval_returns"]
    scopes = block["scope_summary"]
    observations = observation_definitions(intervals)
    rows: list[dict[str, Any]] = []
    for target in EXPECTED_TARGETS:
        own_intervals = intervals[intervals["target"] == target]
        own_scopes = scopes[scopes["target"] == target]
        expected_windows = windows_by_target[target]
        step = EXPECTED_RANGES[target][2]
        for observation in observations:
            if observation["kind"] == "interval":
                selected = own_intervals[own_intervals["interval_id"] == observation["key"]]
                value_column = "total_return"
            else:
                selected = own_scopes[own_scopes["scope"] == observation["key"]]
                value_column = "compound_return"
            hold = selected[selected["strategy_id"] == STRATEGY_HOLD]
            if len(hold) != 1:
                raise ValueError(f"{target} {observation['observation_id']} lacks one Hold value")
            hold_pct = float(hold.iloc[0][value_column]) * 100.0
            sma = selected[selected["strategy_id"] != STRATEGY_HOLD].copy()
            sma["sma_window"] = sma["sma_window"].astype(int)
            sma = sma.sort_values("sma_window")
            if sma["sma_window"].tolist() != expected_windows:
                raise ValueError(f"{target} {observation['observation_id']} has an incomplete grid")
            for item in sma.itertuples(index=False):
                sma_pct = float(getattr(item, value_column)) * 100.0
                rows.append(
                    {
                        "target": target,
                        "target_type": "single",
                        "grid_step": step,
                        **observation,
                        "sma_window": int(item.sma_window),
                        "sma_return_pct": sma_pct,
                        "hold_return_pct": hold_pct,
                        "sma_minus_hold_pp": sma_pct - hold_pct,
                    }
                )
    return pd.DataFrame(rows)


def _contiguous_runs(windows: list[int], step: int) -> list[list[int]]:
    if not windows:
        return []
    runs = [[windows[0]]]
    for window in windows[1:]:
        if window == runs[-1][-1] + step:
            runs[-1].append(window)
        else:
            runs.append([window])
    return runs


def plateau_summary(curve: pd.DataFrame) -> dict[str, Any]:
    ordered = curve.sort_values("sma_window")
    values = ordered.set_index("sma_window")["sma_return_pct"].astype(float)
    steps = ordered["grid_step"].astype(int).unique().tolist()
    if len(steps) != 1:
        raise ValueError("one target curve must have one frozen grid step")
    step = int(steps[0])
    maximum = float(values.max())
    best_windows = values[np.isclose(values, maximum, atol=1e-12, rtol=0)].index.astype(int)
    eligible = values[
        values >= maximum - PLATEAU_TOLERANCE_PP - 1e-12
    ].index.astype(int).tolist()
    candidates = [
        run
        for run in _contiguous_runs(eligible, step)
        if len(run) >= PLATEAU_MIN_WINDOWS
    ]
    chosen: list[int] | None = None
    if candidates:
        chosen = max(
            candidates,
            key=lambda run: (len(run), float(values.loc[run].mean()), -run[0]),
        )
    hold = float(ordered["hold_return_pct"].iloc[0])
    return {
        "target": str(ordered["target"].iloc[0]),
        "observation_order": int(ordered["observation_order"].iloc[0]),
        "observation_id": str(ordered["observation_id"].iloc[0]),
        "label": str(ordered["label"].iloc[0]),
        "grid_step": step,
        "hold_return_pct": hold,
        "best_sma_window": int(min(best_windows)),
        "best_sma_return_pct": maximum,
        "best_sma_minus_hold_pp": maximum - hold,
        "window_count_beating_hold": int((values > hold).sum()),
        "window_count": int(len(values)),
        "plateau_found": chosen is not None,
        "plateau_start": chosen[0] if chosen else np.nan,
        "plateau_end": chosen[-1] if chosen else np.nan,
        "plateau_window_count": len(chosen) if chosen else 0,
        "plateau_mean_return_pct": float(values.loc[chosen].mean()) if chosen else np.nan,
        "plateau_min_return_pct": float(values.loc[chosen].min()) if chosen else np.nan,
    }


def build_plateaus(points: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        [
            plateau_summary(group)
            for _, group in points.groupby(["target", "observation_id"], sort=False)
        ]
    ).sort_values(["target", "observation_order"]).reset_index(drop=True)


def plateau_shapes(row: pd.Series) -> list[dict[str, Any]]:
    if not bool(row["plateau_found"]):
        return []
    padding = float(row["grid_step"]) / 2.0
    return [
        {
            "type": "rect",
            "xref": "x",
            "yref": "paper",
            "x0": float(row["plateau_start"]) - padding,
            "x1": float(row["plateau_end"]) + padding,
            "y0": 0,
            "y1": 1,
            "fillcolor": "rgba(37,99,235,0.09)",
            "line": {"width": 0},
            "layer": "below",
        }
    ]


def _axis_ticks(windows: list[int], step: int) -> tuple[list[int], list[str]]:
    span = windows[-1] - windows[0]
    if step == 10:
        label_interval = 20 if span <= 250 else 50
    elif span <= 50:
        label_interval = 5
    elif span <= 100:
        label_interval = 10
    else:
        label_interval = 20
    labels = [
        str(window)
        if window in {windows[0], windows[-1]} or (window - windows[0]) % label_interval == 0
        else ""
        for window in windows
    ]
    return windows, labels


def window_curve_figure(
    points: pd.DataFrame,
    plateaus: pd.DataFrame,
    target: str,
) -> go.Figure:
    own = points[points["target"] == target].sort_values(
        ["observation_order", "sma_window"]
    )
    own_plateaus = plateaus[plateaus["target"] == target].set_index("observation_id")
    observation_ids = own.drop_duplicates("observation_id").sort_values(
        "observation_order"
    )["observation_id"].astype(str).tolist()
    figure = go.Figure()
    buttons: list[dict[str, Any]] = []
    for observation_index, observation_id in enumerate(observation_ids):
        curve = own[own["observation_id"] == observation_id].sort_values("sma_window")
        label = str(curve.iloc[0]["label"])
        visible = observation_id == DEFAULT_OBSERVATION
        hold_values = curve["hold_return_pct"].to_numpy(dtype=float)
        delta = curve["sma_minus_hold_pp"].to_numpy(dtype=float)
        figure.add_trace(
            go.Scatter(
                x=curve["sma_window"],
                y=curve["sma_return_pct"],
                mode="lines+markers",
                name="完整SMA择时",
                visible=visible,
                line={"color": "#2563eb", "width": 2.7},
                marker={"size": 4.5, "color": "#2563eb"},
                customdata=np.column_stack([hold_values, delta]),
                meta={
                    "series_key": f"{target}:{observation_id}:sma",
                    "panel": "sma_window_return",
                    "is_benchmark": False,
                },
                hovertemplate=(
                    "SMA%{x}<br>收益 %{y:+.2f}%<br>Hold %{customdata[0]:+.2f}%"
                    "<br>相对Hold %{customdata[1]:+.2f}pp<extra></extra>"
                ),
            )
        )
        figure.add_trace(
            go.Scatter(
                x=curve["sma_window"],
                y=hold_values,
                mode="lines",
                name="A · Hold",
                visible=visible,
                line={"color": "#64748b", "width": 2.2, "dash": "dash"},
                meta={
                    "series_key": f"{target}:{observation_id}:hold",
                    "panel": "hold_baseline",
                    "is_benchmark": True,
                },
                hovertemplate="Hold %{y:+.2f}%<extra></extra>",
            )
        )
        visibility = [False] * (2 * len(observation_ids))
        visibility[2 * observation_index] = True
        visibility[2 * observation_index + 1] = True
        plateau = own_plateaus.loc[observation_id]
        plateau_text = (
            f" · 描述性高原 SMA{int(plateau.plateau_start)}–{int(plateau.plateau_end)}"
            if bool(plateau.plateau_found)
            else " · 未找到连续≥3点的5pp高原"
        )
        buttons.append(
            {
                "label": label,
                "method": "update",
                "args": [
                    {"visible": visibility},
                    {
                        "title.text": label + plateau_text,
                        "shapes": plateau_shapes(plateau),
                        "yaxis.autorange": True,
                    },
                ],
            }
        )
    default_plateau = own_plateaus.loc[DEFAULT_OBSERVATION]
    default_label = str(
        own[own["observation_id"] == DEFAULT_OBSERVATION].iloc[0]["label"]
    )
    default_plateau_text = (
        f" · 描述性高原 SMA{int(default_plateau.plateau_start)}–"
        f"{int(default_plateau.plateau_end)}"
        if bool(default_plateau.plateau_found)
        else " · 未找到连续≥3点的5pp高原"
    )
    windows = own["sma_window"].drop_duplicates().astype(int).sort_values().tolist()
    step = int(own["grid_step"].iloc[0])
    tick_values, tick_text = _axis_ticks(windows, step)
    padding = max(float(step), 1.0)
    figure.update_layout(
        template="plotly_white",
        height=520,
        title={"text": default_label + default_plateau_text, "font": {"size": 15}},
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.02, "x": 0},
        margin={"l": 72, "r": 26, "t": 112, "b": 65},
        shapes=plateau_shapes(default_plateau),
        updatemenus=[
            {
                "type": "dropdown",
                "direction": "down",
                "buttons": buttons,
                "active": observation_ids.index(DEFAULT_OBSERVATION),
                "x": 0,
                "xanchor": "left",
                "y": 1.20,
                "yanchor": "top",
                "showactive": True,
            }
        ],
        uirevision=f"bear6-sma-fine-window-{target}",
    )
    figure.update_xaxes(
        title_text="SMA窗口（日）",
        range=[windows[0] - padding, windows[-1] + padding],
        tickmode="array",
        tickvals=tick_values,
        ticktext=tick_text,
        showgrid=True,
    )
    figure.update_yaxes(
        title_text="熊市收益",
        ticksuffix="%",
        zeroline=True,
        zerolinecolor="#94a3b8",
    )
    return figure


def summary_cards(plateaus: pd.DataFrame) -> str:
    default = plateaus[
        plateaus["observation_id"] == DEFAULT_OBSERVATION
    ].set_index("target")
    cards: list[str] = []
    for target in EXPECTED_TARGETS:
        row = default.loc[target]
        plateau = (
            f"SMA{int(row.plateau_start)}–{int(row.plateau_end)}"
            if bool(row.plateau_found)
            else "无连续高原"
        )
        cards.append(
            '<div class="sma-card">'
            f'<strong>{html.escape(target)}</strong>'
            f'<span>Hold {row.hold_return_pct:+.2f}%</span>'
            f'<span>曲线最高 SMA{int(row.best_sma_window)} · {row.best_sma_return_pct:+.2f}%</span>'
            f'<span>相对Hold {row.best_sma_minus_hold_pp:+.2f}pp</span>'
            f'<span>5pp描述性高原：{html.escape(plateau)}</span>'
            "</div>"
        )
    return (
        '<section class="matrix-section"><h2>默认视图：不包含2000–2002</h2>'
        '<p>以下只帮助定位曲线，不是参数选择。六个范围本身来自父实验读图，因此仍是事后探索。</p>'
        '<div class="sma-cards">' + "".join(cards) + "</div></section>"
    )


def performance_figure(
    block: dict[str, Any],
    windows_by_target: Mapping[str, list[int]],
) -> go.Figure:
    """Provide the v5 performance context using each frozen range midpoint."""

    cases = block["case_index"]
    dates = pd.to_datetime(block["date_index"]["date"])
    equity = block["daily"]["equity"]
    colors = {
        "AZO": "#2563eb",
        "TLT": "#7c3aed",
        "SO": "#059669",
        "ED": "#0891b2",
        "MO": "#d97706",
        "GIS": "#dc2626",
    }
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.70, 0.30],
        vertical_spacing=0.06,
    )
    for target in EXPECTED_TARGETS:
        windows = windows_by_target[target]
        midpoint = windows[(len(windows) - 1) // 2]
        strategy_ids = (STRATEGY_HOLD, f"sma_{midpoint:03d}")
        own = cases[
            (cases["target"] == target)
            & (cases["strategy_id"].isin(strategy_ids))
        ]
        if len(own) != 2:
            raise ValueError(f"{target} must have Hold and frozen range midpoint")
        for row in own.itertuples(index=False):
            values = np.asarray(equity[int(row.case_index)], dtype=float)
            normalized = values / float(values[0]) * 100.0
            drawdown = (normalized / np.maximum.accumulate(normalized) - 1.0) * 100.0
            strategy_id = str(row.strategy_id)
            label = "Hold" if strategy_id == STRATEGY_HOLD else f"SMA{midpoint}范围中点"
            name = f"{target} · {label}"
            is_benchmark = target == "AZO" and strategy_id == STRATEGY_HOLD
            line = {
                "color": colors[target],
                "width": 2.2 if strategy_id == STRATEGY_HOLD else 1.8,
                "dash": "solid" if strategy_id == STRATEGY_HOLD else "dash",
            }
            for panel_row, panel, series in (
                (1, "equity", normalized),
                (2, "drawdown", drawdown),
            ):
                figure.add_trace(
                    go.Scatter(
                        x=dates,
                        y=series,
                        mode="lines",
                        name=name,
                        legendgroup=target,
                        showlegend=panel == "equity",
                        line=line,
                        meta={
                            "series_key": str(row.case_id),
                            "panel": panel,
                            "is_benchmark": is_benchmark,
                        },
                        hovertemplate=(
                            "%{x|%Y-%m-%d}<br>%{y:.2f}"
                            + ("%" if panel == "drawdown" else "")
                            + "<extra>%{fullData.name}</extra>"
                        ),
                    ),
                    row=panel_row,
                    col=1,
                )
    figure.update_layout(
        template="plotly_white",
        height=720,
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.18},
        uirevision="bear6-sma-fine-window-performance-v1",
    )
    figure.update_yaxes(title_text="净值（起点100）", row=1, col=1)
    figure.update_yaxes(title_text="回撤", ticksuffix="%", row=2, col=1)
    figure.update_xaxes(title_text="日期", row=2, col=1)
    return figure


def markdown_summary(plateaus: pd.DataFrame) -> str:
    rows = [
        "| 标的 | Hold | 曲线最高点 | 相对Hold | 5pp描述性高原 | 高于Hold窗口数 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    selected = plateaus[
        plateaus["observation_id"] == DEFAULT_OBSERVATION
    ].set_index("target")
    for target in EXPECTED_TARGETS:
        row = selected.loc[target]
        plateau = (
            f"SMA{int(row.plateau_start)}–{int(row.plateau_end)}"
            if bool(row.plateau_found)
            else "无"
        )
        rows.append(
            f"| {target} | {row.hold_return_pct:+.2f}% | SMA{int(row.best_sma_window)} "
            f"{row.best_sma_return_pct:+.2f}% | {row.best_sma_minus_hold_pp:+.2f}pp | "
            f"{plateau} | {int(row.window_count_beating_hold)}/{int(row.window_count)} |"
        )
    return "\n".join(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    record = load_run(context, args.run_id)
    if record.get("status") != "running" or record.get("analysis", {}).get("status") != "pending":
        raise RuntimeError("analysis requires a running run with pending analysis")
    blocks = load_blocks(context, args.run_id)
    if set(blocks) != {0.0, 5.0}:
        raise ValueError("formal report requires exactly 0 and 5 bps")
    windows_by_target = build_windows_by_target(context.config["parameters"])

    points5 = build_curve_points(blocks[5.0], windows_by_target)
    points0 = build_curve_points(blocks[0.0], windows_by_target)
    plateaus5 = build_plateaus(points5)
    plateaus0 = build_plateaus(points0)
    run_root = context.run_root(args.run_id)
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(exist_ok=True)
    points5.to_csv(analysis_root / "curve_points_5bps.csv", index=False, lineterminator="\n")
    points0.to_csv(analysis_root / "curve_points_0bps.csv", index=False, lineterminator="\n")
    plateaus5.to_csv(analysis_root / "plateaus_5bps.csv", index=False, lineterminator="\n")
    plateaus0.to_csv(analysis_root / "plateaus_0bps.csv", index=False, lineterminator="\n")

    default5 = plateaus5[plateaus5["observation_id"] == DEFAULT_OBSERVATION]
    default0 = plateaus0[plateaus0["observation_id"] == DEFAULT_OBSERVATION]
    summary = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "formal_cost_bps": 5.0,
        "default_observation": DEFAULT_OBSERVATION,
        "plateau_rule": {
            "tolerance_pp_below_curve_maximum": PLATEAU_TOLERANCE_PP,
            "minimum_contiguous_windows": PLATEAU_MIN_WINDOWS,
            "target_specific_steps": {
                target: EXPECTED_RANGES[target][2] for target in EXPECTED_TARGETS
            },
        },
        "default_observation_5bps": json.loads(default5.to_json(orient="records")),
        "default_observation_0bps": json.loads(default0.to_json(orient="records")),
        "direct_promotion_allowed": False,
        "direct_promotion_blocker": "六只标的、局部范围、12段熊市边界均来自事后全样本观察",
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    summary_html = summary_cards(plateaus5) + (
        '<section class="matrix-section"><h2>怎么看这6张图</h2>'
        '<p>蓝色实线是每只标的自己的冻结SMA细网格，灰色水平虚线是同一标的、同一熊市范围的A持有收益。'
        '下拉菜单可切换12段单独熊市、总小熊、总大熊、总熊和不包含2000–2002。'
        '浅蓝区域只标记“距当前曲线最高值不超过5个百分点、按该标的步长连续至少3点”的描述性高原；它不等于未来最优参数。</p>'
        '</section>'
    )
    figures = [
        ReportFigure(
            div_id="performance-bear6_sma_fine_window_grid",
            title="连续事件净值：Hold与各冻结范围中点（5 bps，仅作上下文）",
            figure=performance_figure(blocks[5.0], windows_by_target),
            kind="performance",
        ),
        *[
            ReportFigure(
                div_id=f"sma-window-{target.lower()}",
                title=f"局部细网格｜{target}",
                figure=window_curve_figure(points5, plateaus5, target),
                kind="generic",
            )
            for target in EXPECTED_TARGETS
        ],
    ]
    notes = [
        "12段熊市峰谷边界、六只标的和本轮局部范围均由全样本事后观察确定，不能直接晋级模拟盘。",
        "Hold与全部目标专属SMA窗口统一使用SMA500预热完成的熊市样本，避免短均线只因更早有数据而虚增优势。",
        "SMA路径统一使用上下3%滞回；跌破下轨卖出后保持武装，可以在同一熊市后续严格上穿上轨时再次买入。",
        "SO与ED的用户下界0不构造不存在的SMA0；图中从SMA1开始，且SMA1因Close等于自身一日均线而无法越过3%上轨，是明确的退化边界。",
        "本实验完全关闭ATR硬止损、峰值回撤止损、10%成交锁、星标权重和分组资金再分配。",
        "单边5bps是正式结果，0bps只用于成本方向检查；完整参数和全部精确规则保存在折叠附录与experiment_snapshot.json。",
        "复权OHLC提供内部一致的总回报近似，不等同于原始价格、现金分红和真实开盘滑点的逐事件回放。",
    ]
    report = render_interactive_report(
        title="六标的SMA局部细网格诊断",
        heading="A持有虚线 vs 目标专属SMA细网格",
        subtitle="AZO / TLT / SO / ED / MO / GIS；默认不包含2000–2002。",
        summary_html=summary_html,
        notes=notes,
        figures=figures,
        experiment=context.config,
        run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    css = """
<style>
.matrix-section{margin:28px 0}.sma-cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}
.sma-card{display:flex;flex-direction:column;gap:6px;border:1px solid #dbe3ec;border-radius:12px;padding:14px;background:#fff}
.sma-card strong{font-size:1.05rem}.sma-card span{font-variant-numeric:tabular-nums;color:#334155}
@media(max-width:900px){.sma-cards{grid-template-columns:1fr}}
@media print{.sma-card{break-inside:avoid}}
</style>
"""
    report = report.replace("</head>", css + "</head>")
    downloads = (
        '<section class="chart"><h2>结果下载</h2><p>'
        '<a download href="analysis/curve_points_5bps.csv">5 bps全部折线点</a> · '
        '<a download href="analysis/plateaus_5bps.csv">5 bps描述性高原</a> · '
        '<a download href="BEAR6_SMA_FINE_WINDOW_GRID/cost_5bps/interval_returns.csv">5 bps逐段收益</a> · '
        '<a download href="BEAR6_SMA_FINE_WINDOW_GRID/cost_5bps/scope_summary.csv">5 bps大小熊汇总</a> · '
        '<a download href="BEAR6_SMA_FINE_WINDOW_GRID/cost_5bps/metrics.csv">5 bps全部指标</a> · '
        '<a download href="analysis/curve_points_0bps.csv">0 bps敏感性</a>'
        "</p></section>"
    )
    report = report.replace("</main>", downloads + "</main>")
    (run_root / "report.html").write_text(report, encoding="utf-8")
    (run_root / "report.md").write_text(
        f"""# 六标的SMA局部细网格诊断

## 读图规则

- 每只标的一张折线图：蓝色为该标的冻结SMA细网格，灰色水平虚线为A持有。
- 默认显示总熊（不包含2000–2002）；HTML下拉可切换12段单独熊市、总小熊、总大熊和总熊。
- 浅蓝区域是距曲线最高值不超过5个百分点、按目标步长连续至少3个窗口的描述性高原，不是正式选参。
- 全部窗口共用SMA500预热样本；SMA上下3%滞回和卖出后重新买入保留，所有止损关闭。

## 默认视图（5 bps）

{markdown_summary(plateaus5)}

## 研究边界

- 六只标的、局部范围和熊市边界均含事后信息，不允许直接晋级模拟盘或实盘。
- SO/ED的SMA1是用户0下界映射到最小有效周期后的退化边界，不能解释为有效策略。
- 单边5bps为正式结果，0bps只检查成本方向。
""",
        encoding="utf-8",
    )

    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    tracked = [
        "backtest/quantkit/bear_atr_hard_stop.py",
        "backtest/quantkit/execution.py",
        "backtest/quantkit/metrics.py",
        "backtest/quantkit/experiment.py",
        "backtest/quantkit/reporting.py",
        "backtest/scripts/run_bear24_sma_window_plateau.py",
        "backtest/scripts/run_bear6_sma_fine_windows.py",
        "backtest/scripts/analyze_bear6_sma_fine_windows.py",
        "backtest/scripts/finalize_bear6_sma_fine_windows.py",
        "backtest/scripts/smoke_report_ui.mjs",
        "backtest/scripts/smoke_bear6_sma_fine_window_report.mjs",
        "backtest/scripts/print_html_pdf.mjs",
        "backtest/scripts/validate_run.py",
        "backtest/tests/strategies/tim/test_bear6_sma_fine_window_scan.py",
        "backtest/experiments/TIM/TIM-v0.40b.3__26-08-24__bear6_sma_fine_window_scan/experiment.json",
        "backtest/requirements.lock",
        "backtest/report_templates/interactive_research_v5/page.html",
        "backtest/report_templates/interactive_research_v5/styles.css",
        "backtest/report_templates/interactive_research_v5/interactions.js",
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

This immutable run compares Hold with six target-specific SMA fine grids over 12 hindsight bear intervals.

- `report.html` / `report.md`: v5 strategy-first report with six interactive response charts.
- `report.pdf`: Chrome-printed default view, excluding the 2000-2002 interval.
- `analysis/curve_points_5bps.csv`: all plotted 5 bps points and Hold deltas.
- `analysis/plateaus_5bps.csv`: frozen target-step descriptive plateau diagnostics.
- `BEAR6_SMA_FINE_WINDOW_GRID/cost_0bps/` and `cost_5bps/`: all cases, ledgers, daily state, hashes, and interval summaries.
- `provenance.json`: exact source and market-data hashes.
- `validation.json`: full test, audit, browser, PDF, and lifecycle evidence.
""",
        encoding="utf-8",
    )
    print(f"Wrote {run_root / 'report.html'} with six response charts and one performance context")


if __name__ == "__main__":
    main()
