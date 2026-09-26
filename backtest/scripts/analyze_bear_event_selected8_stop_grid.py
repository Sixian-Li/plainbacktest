#!/usr/bin/env python3
"""Build the formal selected-eight trailing-stop grid report."""

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
from scripts.run_bear_event_selected8_stop_grid import (
    EXPECTED_STOPS_PCT,
    EXPECTED_UNIVERSE,
    PORTFOLIO_TARGET,
    SYMBOL_BLOCK,
    stop_identifier,
)


WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/TIM/"
    "TIM-v0.40a.2__26-08-15__bear_selected8_sma200_trailing_stop_grid"
)
STOP_ORDER = [stop_identifier(value) for value in EXPECTED_STOPS_PCT]
TARGET_ORDER = [*EXPECTED_UNIVERSE, PORTFOLIO_TARGET]
SCOPE_LABELS = {
    "minor": "总小熊",
    "major": "总大熊",
    "all": "总熊",
    "all_ex_2000_2002": "总熊（剔除2000–2002）",
}


def _read_csv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    for column in (
        "date",
        "start",
        "end",
        "entry_execution_date",
        "exit_execution_date",
        "signal_date",
        "execution_date",
    ):
        if column in frame.columns:
            frame[column] = pd.to_datetime(frame[column])
    return frame


def load_blocks(context: Any, run_id: str) -> dict[float, dict[str, Any]]:
    blocks: dict[float, dict[str, Any]] = {}
    for cost in context.config["cost_scenarios_bps_per_side"]:
        cost_value = float(cost)
        root = block_root(context, run_id, SYMBOL_BLOCK, cost_value)
        manifest_path = root / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"missing completed block manifest: {manifest_path}")
        blocks[cost_value] = {
            "root": root,
            "metrics": _read_csv(root / "metrics.csv"),
            "interval_returns": _read_csv(root / "interval_returns.csv"),
            "scope_summary": _read_csv(root / "scope_summary.csv"),
            "case_index": _read_csv(root / "case_index.csv"),
            "date_index": _read_csv(root / "date_index.csv"),
            "daily": np.load(root / "daily_state.npz"),
        }
    return blocks


def find_stable_plateaus(scope_summary: pd.DataFrame, target: str) -> list[tuple[int, int]]:
    """Return 3+-wide integer runs beating stop-off with and without 2000-2002."""

    frame = scope_summary[
        (scope_summary["target"] == target)
        & (scope_summary["scope"].isin(["all", "all_ex_2000_2002"]))
    ].copy()
    baselines = frame[frame["stop_id"] == "stop_off"].set_index("scope")["compound_return"]
    if set(baselines.index) != {"all", "all_ex_2000_2002"}:
        raise ValueError(f"{target} is missing stop-off baselines")
    numeric = frame[frame["stop_id"] != "stop_off"].pivot(
        index="trailing_drawdown_pct",
        columns="scope",
        values="compound_return",
    )
    eligible = sorted(
        int(threshold)
        for threshold, row in numeric.iterrows()
        if float(row["all"]) > float(baselines["all"])
        and float(row["all_ex_2000_2002"]) > float(baselines["all_ex_2000_2002"])
    )
    return _consecutive_runs(eligible)


def _consecutive_runs(eligible: list[int]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    if not eligible:
        return runs
    start = previous = eligible[0]
    for value in eligible[1:]:
        if value == previous + 1:
            previous = value
            continue
        if previous - start + 1 >= 3:
            runs.append((start, previous))
        start = previous = value
    if previous - start + 1 >= 3:
        runs.append((start, previous))
    return runs


def find_absolute_positive_plateaus(
    scope_summary: pd.DataFrame,
    target: str,
) -> list[tuple[int, int]]:
    """Require both full-history improvement and positive post-2000 returns."""

    frame = scope_summary[
        (scope_summary["target"] == target)
        & (scope_summary["scope"].isin(["all", "all_ex_2000_2002"]))
    ].copy()
    baseline_all = frame[
        (frame["scope"] == "all") & (frame["stop_id"] == "stop_off")
    ]["compound_return"]
    if len(baseline_all) != 1:
        raise ValueError(f"{target} is missing its all-bear stop-off baseline")
    numeric = frame[frame["stop_id"] != "stop_off"].pivot(
        index="trailing_drawdown_pct",
        columns="scope",
        values="compound_return",
    )
    eligible = sorted(
        int(threshold)
        for threshold, row in numeric.iterrows()
        if float(row["all"]) > float(baseline_all.iloc[0])
        and float(row["all_ex_2000_2002"]) > 0.0
    )
    return _consecutive_runs(eligible)


def _stop_label(stop_id: str) -> str:
    return "不止损" if stop_id == "stop_off" else f"{int(stop_id[-2:])}%"


def _cell_style(value: float, scale: float) -> str:
    if not np.isfinite(value) or scale <= 0:
        return ""
    alpha = min(abs(value) / scale, 1.0) * 0.32 + 0.04
    color = f"rgba(22,163,74,{alpha:.3f})" if value >= 0 else f"rgba(220,38,38,{alpha:.3f})"
    return f' style="background:{color}"'


def return_matrix(
    interval_returns: pd.DataFrame,
    scope_summary: pd.DataFrame,
    target: str,
) -> tuple[list[str], list[str], np.ndarray]:
    intervals = interval_returns[interval_returns["target"] == target].copy()
    scopes = scope_summary[scope_summary["target"] == target].copy()
    first_case = intervals[intervals["stop_id"] == "stop_off"].sort_values("ordinal")
    if len(first_case) != 12:
        raise ValueError(f"{target} does not have 12 interval rows")
    row_keys = first_case["interval_id"].astype(str).tolist()
    row_labels = [
        f"{row.label} · {pd.Timestamp(row.start).date()}～{pd.Timestamp(row.end).date()}"
        for row in first_case.itertuples(index=False)
    ]
    row_keys.extend(["scope:minor", "scope:major", "scope:all", "scope:all_ex_2000_2002"])
    row_labels.extend([SCOPE_LABELS[key] for key in ("minor", "major", "all", "all_ex_2000_2002")])
    values = np.full((len(row_keys), len(STOP_ORDER)), np.nan, dtype=float)
    for column, stop_id in enumerate(STOP_ORDER):
        own_intervals = intervals[intervals["stop_id"] == stop_id].set_index("interval_id")
        own_scopes = scopes[scopes["stop_id"] == stop_id].set_index("scope")
        for row, key in enumerate(row_keys[:12]):
            values[row, column] = float(own_intervals.loc[key, "total_return"]) * 100.0
        for offset, scope in enumerate(("minor", "major", "all", "all_ex_2000_2002"), start=12):
            values[offset, column] = float(own_scopes.loc[scope, "compound_return"]) * 100.0
    return row_labels, [_stop_label(stop_id) for stop_id in STOP_ORDER], values


def target_diagnostics(block: dict[str, Any], target: str) -> dict[str, Any]:
    scopes = block["scope_summary"]
    metrics = block["metrics"]
    all_rows = scopes[(scopes["target"] == target) & (scopes["scope"] == "all")]
    ex_rows = scopes[
        (scopes["target"] == target) & (scopes["scope"] == "all_ex_2000_2002")
    ]
    full_best = all_rows.loc[all_rows["compound_return"].idxmax()]
    ex_best = ex_rows.loc[ex_rows["compound_return"].idxmax()]
    baseline_all = all_rows[all_rows["stop_id"] == "stop_off"].iloc[0]
    baseline_ex = ex_rows[ex_rows["stop_id"] == "stop_off"].iloc[0]
    first = block["interval_returns"]
    first = first[(first["target"] == target) & (first["ordinal"] == 1)].set_index("stop_id")
    plateau = find_stable_plateaus(scopes, target)
    absolute_plateau = find_absolute_positive_plateaus(scopes, target)
    return {
        "target": target,
        "stop_off_all_bear_return_pct": float(baseline_all["compound_return"] * 100.0),
        "stop_off_ex_2000_2002_return_pct": float(baseline_ex["compound_return"] * 100.0),
        "full_sample_best_stop_id": str(full_best["stop_id"]),
        "full_sample_best_return_pct": float(full_best["compound_return"] * 100.0),
        "ex_2000_2002_best_stop_id": str(ex_best["stop_id"]),
        "ex_2000_2002_best_return_pct": float(ex_best["compound_return"] * 100.0),
        "full_best_first_bear_return_pct": float(first.loc[full_best["stop_id"], "total_return"] * 100.0),
        "stable_plateaus_pct": [list(item) for item in plateau],
        "has_stable_plateau": bool(plateau),
        "absolute_positive_plateaus_pct": [list(item) for item in absolute_plateau],
        "has_absolute_positive_plateau": bool(absolute_plateau),
        "full_best_max_drawdown_pct": float(
            metrics.loc[metrics["case_id"] == full_best["case_id"], "max_drawdown_pct"].iloc[0]
        ),
        "full_best_forced_exit_count": int(
            metrics.loc[metrics["case_id"] == full_best["case_id"], "forced_exit_count"].iloc[0]
        ),
    }


def overview_table(diagnostics: list[dict[str, Any]]) -> str:
    rows = []
    for item in diagnostics:
        plateaus = "、".join(f"{left}%–{right}%" for left, right in item["stable_plateaus_pct"]) or "无"
        absolute = "、".join(
            f"{left}%–{right}%" for left, right in item["absolute_positive_plateaus_pct"]
        ) or "无"
        rows.append(
            "<tr>"
            f"<td>{html.escape(item['target'])}</td>"
            f"<td>{item['stop_off_all_bear_return_pct']:+.2f}%</td>"
            f"<td>{html.escape(_stop_label(item['full_sample_best_stop_id']))} · {item['full_sample_best_return_pct']:+.2f}%</td>"
            f"<td>{item['stop_off_ex_2000_2002_return_pct']:+.2f}%</td>"
            f"<td>{html.escape(_stop_label(item['ex_2000_2002_best_stop_id']))} · {item['ex_2000_2002_best_return_pct']:+.2f}%</td>"
            f"<td>{html.escape(plateaus)}</td>"
            f"<td>{html.escape(absolute)}</td>"
            f"<td>{item['full_best_first_bear_return_pct']:+.2f}%</td>"
            "</tr>"
        )
    return (
        '<div class="summary-grid"><div class="summary-card"><strong>固定设计</strong>'
        '<span>8个单标的 + 1个等权组合；不止损与3%–20%逐1%；SMA200±3%；0/5 bps</span></div>'
        '<div class="summary-card"><strong>稳定定义</strong>'
        '<span>至少3个连续阈值同时在总熊和剔除2000–2002总熊上胜过不止损</span></div></div>'
        '<section class="matrix-section"><h2>阈值诊断总览（5 bps）</h2>'
        '<div class="table-wrap"><table><thead><tr><th>目标</th><th>不止损总熊</th>'
        '<th>全样本最高</th><th>不含2000不止损</th><th>不含2000最高</th><th>相对改善高原</th><th>不含2000仍为正高原</th>'
        '<th>全样本最高在2000–02</th></tr></thead><tbody>'
        + "".join(rows)
        + "</tbody></table></div></section>"
    )


def matrix_table(
    interval_returns: pd.DataFrame,
    scope_summary: pd.DataFrame,
    target: str,
) -> str:
    row_labels, column_labels, values = return_matrix(interval_returns, scope_summary, target)
    scale = float(np.nanpercentile(np.abs(values), 90)) or 1.0
    body = []
    for row, label in enumerate(row_labels):
        cells = []
        for column in range(len(column_labels)):
            value = float(values[row, column])
            cells.append(f"<td{_cell_style(value, scale)}>{value:+.2f}%</td>")
        emphasis = ' class="aggregate-row"' if row >= 12 else ""
        body.append(f"<tr{emphasis}><th>{html.escape(label)}</th>{''.join(cells)}</tr>")
    return (
        f'<section class="matrix-section" id="matrix-{target.lower()}"><h2>{html.escape(target)} · 熊市收益矩阵（5 bps）</h2>'
        '<p>绿色为正、红色为负；若SMA200择时导致整段未持有，单元格显示0.00%。总计均为逐段几何复合。</p>'
        '<div class="table-wrap matrix-wrap"><table class="return-matrix"><thead><tr><th>熊市 / 阈值</th>'
        + "".join(f"<th>{html.escape(label)}</th>" for label in column_labels)
        + "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></div></section>"
    )


def threshold_response_figure(scope_summary: pd.DataFrame, target: str) -> go.Figure:
    figure = go.Figure()
    own = scope_summary[scope_summary["target"] == target]
    x_labels = [_stop_label(stop_id) for stop_id in STOP_ORDER]
    for scope, color in (
        ("all", "#0f766e"),
        ("all_ex_2000_2002", "#7c3aed"),
        ("major", "#b91c1c"),
        ("minor", "#2563eb"),
    ):
        indexed = own[own["scope"] == scope].set_index("stop_id").reindex(STOP_ORDER)
        figure.add_trace(
            go.Scatter(
                x=x_labels,
                y=indexed["compound_return"] * 100.0,
                mode="lines+markers",
                name=SCOPE_LABELS[scope],
                line={"color": color, "width": 2},
                customdata=indexed.index,
                hovertemplate="%{x}<br>%{y:+.2f}%<extra>%{fullData.name}</extra>",
                meta={"series_key": f"{target}-{scope}", "panel": "threshold", "is_benchmark": False},
            )
        )
    figure.add_hline(y=0, line={"color": "#64748b", "width": 1})
    figure.update_layout(
        template="plotly_white",
        height=520,
        xaxis_title="峰值回撤阈值",
        yaxis_title="复合收益 (%)",
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.12},
        uirevision=f"selected8-threshold-{target}",
    )
    return figure


def performance_figure(block: dict[str, Any]) -> go.Figure:
    cases = block["case_index"]
    dates = pd.to_datetime(block["date_index"]["date"])
    equity = block["daily"]["equity"]
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.06,
        row_heights=[0.72, 0.28],
    )
    portfolio = cases[cases["target"] == PORTFOLIO_TARGET]
    for row in portfolio.itertuples(index=False):
        values = equity[int(row.case_index)]
        normalized = values / float(values[0]) * 100.0
        figure.add_trace(
            go.Scatter(
                x=dates,
                y=normalized,
                mode="lines",
                name=_stop_label(str(row.stop_id)),
                visible=True if str(row.stop_id) == "stop_off" else "legendonly",
                line={"width": 2.4 if str(row.stop_id) == "stop_off" else 1.4},
                meta={
                    "series_key": str(row.case_id),
                    "panel": "equity",
                    "is_benchmark": str(row.stop_id) == "stop_off",
                },
                hovertemplate="%{x|%Y-%m-%d}<br>%{y:.2f}<extra>%{fullData.name}</extra>",
            ),
            row=1,
            col=1,
        )
        drawdown = (normalized / np.maximum.accumulate(normalized) - 1.0) * 100.0
        figure.add_trace(
            go.Scatter(
                x=dates,
                y=drawdown,
                mode="lines",
                name=_stop_label(str(row.stop_id)),
                visible=True if str(row.stop_id) == "stop_off" else "legendonly",
                showlegend=False,
                line={"width": 1.8 if str(row.stop_id) == "stop_off" else 1.2},
                meta={
                    "series_key": str(row.case_id),
                    "panel": "drawdown",
                    "is_benchmark": str(row.stop_id) == "stop_off",
                },
                hovertemplate="%{x|%Y-%m-%d}<br>%{y:+.2f}%<extra>%{fullData.name}</extra>",
            ),
            row=2,
            col=1,
        )
    figure.update_layout(
        template="plotly_white",
        height=680,
        hovermode="x unified",
        uirevision="selected8-portfolio-performance-v1",
    )
    figure.update_yaxes(title_text="连续事件账户（起点=100）", row=1, col=1)
    figure.update_yaxes(title_text="回撤 (%)", row=2, col=1)
    figure.update_xaxes(title_text="日期", row=2, col=1)
    return figure


def markdown_overview(diagnostics: list[dict[str, Any]]) -> str:
    lines = [
        "| 目标 | 不止损总熊 | 全样本最高 | 不含2000不止损 | 不含2000最高 | 相对改善高原 | 不含2000仍为正高原 |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for item in diagnostics:
        plateau = "、".join(f"{left}%–{right}%" for left, right in item["stable_plateaus_pct"]) or "无"
        absolute = "、".join(
            f"{left}%–{right}%" for left, right in item["absolute_positive_plateaus_pct"]
        ) or "无"
        lines.append(
            f"| {item['target']} | {item['stop_off_all_bear_return_pct']:+.2f}% | "
            f"{_stop_label(item['full_sample_best_stop_id'])} · {item['full_sample_best_return_pct']:+.2f}% | "
            f"{item['stop_off_ex_2000_2002_return_pct']:+.2f}% | "
            f"{_stop_label(item['ex_2000_2002_best_stop_id'])} · {item['ex_2000_2002_best_return_pct']:+.2f}% | {plateau} | {absolute} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    record = load_run(context, args.run_id)
    if record.get("status") not in {"running", "completed_unvalidated"}:
        raise RuntimeError("Run must be running or completed-unvalidated before analysis")
    analysis_was_complete = record.get("status") == "completed_unvalidated"
    blocks = load_blocks(context, args.run_id)
    if set(blocks) != {0.0, 5.0}:
        raise ValueError("formal report requires exactly 0 and 5 bps blocks")
    five = blocks[5.0]
    diagnostics = [target_diagnostics(five, target) for target in TARGET_ORDER]
    portfolio = next(item for item in diagnostics if item["target"] == PORTFOLIO_TARGET)

    run_root = context.run_root(args.run_id)
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(exist_ok=True)
    pd.DataFrame(diagnostics).to_csv(
        analysis_root / "target_diagnostics.csv", index=False, lineterminator="\n"
    )
    matrices: dict[str, Any] = {}
    for target in TARGET_ORDER:
        rows, columns, values = return_matrix(
            five["interval_returns"], five["scope_summary"], target
        )
        matrices[target] = {"rows": rows, "columns": columns, "values_pct": values.tolist()}
    summary = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "formal_cost_bps": 5.0,
        "target_diagnostics": diagnostics,
        "portfolio_5bps": portfolio,
        "return_matrices_5bps": matrices,
        "direct_promotion_allowed": False,
        "direct_promotion_blocker": "熊市边界和八标的名单均使用事后信息",
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    plateau_text = (
        "、".join(f"{left}%–{right}%" for left, right in portfolio["stable_plateaus_pct"])
        or "无"
    )
    absolute_plateau_text = (
        "、".join(
            f"{left}%–{right}%" for left, right in portfolio["absolute_positive_plateaus_pct"]
        )
        or "无"
    )
    summary_html = overview_table(diagnostics) + "".join(
        matrix_table(five["interval_returns"], five["scope_summary"], target)
        for target in TARGET_ORDER
    )
    notes = [
        "12段熊市峰谷边界由全样本人工标注并收紧，实盘当时并不知道起止日；八标的名单也经过事后筛选。",
        "每个单标的有效时持有100%；PORTFOLIO_8只在当前有效标的间等权，退出后按幸存持仓市值比例分配，新进入标的取得1/n预算。",
        "全部路径使用SMA200±3%；强卖后允许再次买入，但必须先回到上轨之下或等于上轨，再严格上穿SMA200+3%。",
        "峰值以真实状态进入的成本后Open成交价初始化，再用完成Close更新；被动组合再平衡不重置峰值。",
        "信号在完成Close后形成，下一共同复权Open成交；单边5bps是主结果，0bps只作成本敏感性。",
        "若SMA择时使某段完全没有持仓，该段收益严格为0%；总小熊、总大熊、总熊均为逐段几何复合。",
        "稳定高原要求至少3个连续阈值同时改善总熊与剔除2000–2002总熊；单一历史最高点不视为未来最优参数。",
        "复权OHLC只提供内部一致的总回报近似，不是原始成交价、现金分红和拆股事件的精确账户回放。",
    ]
    figures = [
        ReportFigure(
            div_id="performance-bear_selected8_stop_grid",
            title="PORTFOLIO_8：19条连续事件账户路径（5 bps）",
            figure=performance_figure(five),
            kind="performance",
        ),
        ReportFigure(
            div_id="threshold-selected8-portfolio",
            title="PORTFOLIO_8：大小熊、总熊与剔除2000–2002阈值响应",
            figure=threshold_response_figure(five["scope_summary"], PORTFOLIO_TARGET),
            kind="generic",
        ),
    ]
    report_html = render_interactive_report(
        title="八标的 SMA200 峰值回撤阈值网格",
        heading="8个单标的与等权组合：3%–20%哪个区间更稳",
        subtitle=(
            f"PORTFOLIO_8 全样本最高：{_stop_label(portfolio['full_sample_best_stop_id'])} "
            f"{portfolio['full_sample_best_return_pct']:+.2f}%；剔除2000–2002最高："
            f"{_stop_label(portfolio['ex_2000_2002_best_stop_id'])} "
            f"{portfolio['ex_2000_2002_best_return_pct']:+.2f}%；相对改善高原：{plateau_text}；"
            f"不含2000仍为正的三点高原：{absolute_plateau_text}"
        ),
        summary_html=summary_html,
        notes=notes,
        figures=figures,
        experiment=context.config,
        run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    matrix_css = """
<style>
.matrix-section{margin:28px 0}.matrix-wrap{max-height:760px;overflow:auto}
.return-matrix{min-width:2100px;border-collapse:separate;border-spacing:0}
.return-matrix th:first-child{position:sticky;left:0;background:#fff;z-index:3;min-width:260px;text-align:left}
.return-matrix thead th{position:sticky;top:0;background:#f8fafc;z-index:2}
.return-matrix td{font-variant-numeric:tabular-nums;white-space:nowrap;text-align:right}
.return-matrix .aggregate-row th,.return-matrix .aggregate-row td{font-weight:700;border-top:2px solid #94a3b8}
</style>
"""
    report_html = report_html.replace("</head>", matrix_css + "</head>")
    downloads = (
        '<section class="chart"><h2>结果下载</h2><p>'
        '<a download href="BEAR_SELECTED8_STOP_GRID/cost_5bps/interval_returns.csv">5 bps逐段收益</a> · '
        '<a download href="BEAR_SELECTED8_STOP_GRID/cost_5bps/scope_summary.csv">5 bps聚合收益</a> · '
        '<a download href="BEAR_SELECTED8_STOP_GRID/cost_5bps/metrics.csv">5 bps全部指标</a> · '
        '<a download href="BEAR_SELECTED8_STOP_GRID/cost_5bps/orders.csv">5 bps订单</a> · '
        '<a download href="BEAR_SELECTED8_STOP_GRID/cost_5bps/transitions.csv">5 bps状态切换</a> · '
        '<a download href="BEAR_SELECTED8_STOP_GRID/cost_0bps/scope_summary.csv">0 bps聚合收益</a>'
        "</p></section>"
    )
    report_html = report_html.replace("</main>", downloads + "</main>")
    (run_root / "report.html").write_text(report_html, encoding="utf-8")

    matrix_note = (
        "虽然存在相对不止损的连续改善区间，但剔除2000–2002后仍为正的三点高原不存在；不能把相对改善误读成后期赚钱。"
        if portfolio["has_stable_plateau"] and not portfolio["has_absolute_positive_plateau"]
        else "存在剔除2000–2002后仍为正的连续区间，但仍只是事后候选，不能直接用于未来。"
        if portfolio["has_absolute_positive_plateau"]
        else "没有满足冻结定义的三点连续高原，不应从历史最高单点挑一个未来阈值。"
    )
    (run_root / "report.md").write_text(
        f"""# 八标的 SMA200 峰值回撤阈值网格

## 结论

- PORTFOLIO_8 全12段历史最高为 `{_stop_label(portfolio['full_sample_best_stop_id'])}`，总熊复合 `{portfolio['full_sample_best_return_pct']:+.2f}%`。
- 剔除2000–2002后最高为 `{_stop_label(portfolio['ex_2000_2002_best_stop_id'])}`，复合 `{portfolio['ex_2000_2002_best_return_pct']:+.2f}%`。
- PORTFOLIO_8 相对不止损同时改善总熊与剔除2000–2002总熊的三点连续高原：`{plateau_text}`。
- 要求剔除2000–2002后收益仍为正的三点连续高原：`{absolute_plateau_text}`。{matrix_note}
- 熊市边界与八标的名单均含事后信息，本实验不能直接晋级模拟盘或实盘。

## 5 bps 阈值总览

{markdown_overview(diagnostics)}

## 规则边界

- 8个单标的各自有效时100%仓位；组合在当前有效标的间等权，不使用星标。
- SMA200上下3%滞回；峰值止损关闭或3%–20%逐1%，止损后重新武装再上穿可买回。
- 完成Close产生信号，下一共同复权Open成交；5 bps为主结果，0 bps为敏感性。
- 报告HTML包含9张16行×19列表格；完全未持有的熊市单元格为0.00%。
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
        "backtest/scripts/run_bear_event_selected8_stop_grid.py",
        "backtest/scripts/analyze_bear_event_selected8_stop_grid.py",
        "backtest/scripts/smoke_report_ui.mjs",
        "backtest/scripts/validate_run.py",
        "backtest/tests/strategies/tim/test_bear_event_selected8_stop_grid.py",
        "backtest/tests/strategies/tim/test_bear_event_weighted_trailing_stop.py",
        "backtest/experiments/TIM/TIM-v0.40a.2__26-08-15__bear_selected8_sma200_trailing_stop_grid/experiment.json",
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

This immutable run evaluates 8 individual assets and their equal-weight portfolio across 19 trailing-stop states and two cost scenarios over 12 hindsight bear intervals.

- `report.html` / `report.md`: full strategy card, nine return matrices, diagnostics and interactive figures.
- `analysis/summary.json`: machine-readable 5 bps diagnostics and every matrix cell.
- `analysis/target_diagnostics.csv`: per-target winners, ex-2000 results and stable plateaus.
- `BEAR_SELECTED8_STOP_GRID/cost_0bps/` and `cost_5bps/`: metrics, interval results, orders, trades, signals, compressed daily accounts/positions and manifests.
- `provenance.json`: exact code, configuration, research-input and market-data hashes.
- `validation.json`: tests, audit, hash verification and browser evidence.
""",
        encoding="utf-8",
    )
    artifact_manifest = {"schema_version": 1, "created_at_utc": created_at, "artifacts": {}}
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
    if not analysis_was_complete:
        record_analysis_complete(context, args.run_id)
    print(
        f"Wrote {run_root / 'report.html'}; portfolio full best="
        f"{portfolio['full_sample_best_stop_id']} ({portfolio['full_sample_best_return_pct']:+.2f}%); "
        f"stable plateaus={plateau_text}"
    )


if __name__ == "__main__":
    main()
