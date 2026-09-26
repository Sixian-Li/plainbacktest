#!/usr/bin/env python3
"""Analyze and render the QQQ timing/Bear9 rebalance robustness experiment."""

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

from quantkit.experiment import load_experiment, load_run, sha256
from quantkit.paths import BACKTEST_ROOT
from quantkit.qqq_bear9_rebalance import BEAR_RESIDUAL, BEAR_ZERO_ONLY, case_definitions
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts.run_qqq_flat_trio_substitution import json_safe


WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/TIM/"
    "TIM-v0.40d.1__26-08-25__qqq_timing_bear9_rebalance_robustness"
)
FORMAL_SYMBOL = "QQQ_BEAR9_REBALANCE"
FORMAL_COST_BPS = 5.0
TIMING_LABELS = {
    "SMA160": "SMA160 ±3%",
    "SMA180": "SMA180 ±3%",
    "SMA200": "SMA200 ±3%",
    "SMA220": "SMA220 ±3%",
    "SMA240": "SMA240 ±3%",
    "LAYERED_190_310": "SMA190/310 分层",
}
MODE_LABELS = {
    "CASH": "非QQQ仓位留现金",
    BEAR_ZERO_ONLY: "仅QQQ为0%时持Bear9",
    BEAR_RESIDUAL: "Bear9填满非QQQ仓位",
}
FREQUENCY_ORDER = [None, 1, 2, 3, 4, 5, 6, 8, 10, 15, 20, 25, 30]


def load_block(context: Any, run_id: str, cost_bps: float) -> dict[str, Any]:
    root = context.run_root(run_id) / FORMAL_SYMBOL / f"cost_{cost_bps:g}bps"
    names = {
        "metrics": "parameter_results.csv",
        "daily": "daily.csv",
        "orders": "orders.csv",
        "schedules": "target_weight_schedule.csv",
        "hold": "qqq_hold_daily.csv",
        "layered_state": "timing_state_layered_190_310.csv",
    }
    block = {key: pd.read_csv(root / name, parse_dates=["date"] if key in {"daily", "schedules", "hold", "layered_state"} else None) for key, name in names.items()}
    block["metrics_json"] = json.loads((root / "metrics.json").read_text(encoding="utf-8"))
    block["manifest"] = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    return block


def frequency_label(value: Any) -> str:
    if value is None or pd.isna(value):
        return "不再平衡"
    return f"{int(value)}日"


def frequency_rank(value: Any) -> int:
    normalized = None if value is None or pd.isna(value) else int(value)
    return FREQUENCY_ORDER.index(normalized)


def monthly(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.sort_values("date").copy()
    return data.groupby(data["date"].dt.to_period("M"), sort=True).tail(1)


def drawdown(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="raise").astype(float)
    return (numeric / numeric.cummax() - 1.0) * 100.0


def build_rebalance_comparison(metrics: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (timing_id, bear_mode), group in metrics[metrics["bear_mode"] != "CASH"].groupby(
        ["timing_id", "bear_mode"], sort=False
    ):
        hold = group[group["rebalance_days"].isna()]
        if len(hold) != 1:
            raise AssertionError(f"{timing_id}/{bear_mode} must have one no-rebalance row")
        baseline = hold.iloc[0]
        for item in group.itertuples(index=False):
            row = item._asdict()
            row["frequency_label"] = frequency_label(item.rebalance_days)
            row["frequency_rank"] = frequency_rank(item.rebalance_days)
            for column in (
                "total_return_pct",
                "cagr_pct",
                "sharpe",
                "max_drawdown_pct",
                "post_dotcom_total_return_pct",
                "post_dotcom_cagr_pct",
                "post_dotcom_sharpe",
                "post_dotcom_max_drawdown_pct",
            ):
                row[f"delta_vs_hold_{column}"] = float(getattr(item, column)) - float(
                    baseline[column]
                )
            rows.append(row)
    return pd.DataFrame(rows).sort_values(
        ["timing_id", "bear_mode", "frequency_rank"]
    ).reset_index(drop=True)


def timing_robustness(metrics: pd.DataFrame) -> pd.DataFrame:
    primary = metrics[metrics["bear_mode"] == BEAR_RESIDUAL]
    rows = []
    for timing_id in TIMING_LABELS:
        selected = primary[primary["timing_id"] == timing_id]
        if len(selected) != 13:
            raise AssertionError(f"{timing_id} primary path count changed")
        rows.append(
            {
                "timing_id": timing_id,
                "timing_label": TIMING_LABELS[timing_id],
                "policy_count": len(selected),
                "full_positive_total_count": int((selected["delta_vs_cash_total_return_pct"] > 0).sum()),
                "post_dotcom_positive_total_count": int(
                    (selected["delta_vs_cash_post_dotcom_total_return_pct"] > 0).sum()
                ),
                "median_full_total_uplift_pp": float(selected["delta_vs_cash_total_return_pct"].median()),
                "median_post_dotcom_total_uplift_pp": float(
                    selected["delta_vs_cash_post_dotcom_total_return_pct"].median()
                ),
                "median_sharpe_uplift": float(selected["delta_vs_cash_sharpe"].median()),
                "median_drawdown_change_pp": float(
                    selected["delta_vs_cash_max_drawdown_pct"].median()
                ),
                "worst_full_total_uplift_pp": float(selected["delta_vs_cash_total_return_pct"].min()),
                "worst_post_dotcom_total_uplift_pp": float(
                    selected["delta_vs_cash_post_dotcom_total_return_pct"].min()
                ),
            }
        )
    return pd.DataFrame(rows)


def frequency_summary(comparison: pd.DataFrame) -> pd.DataFrame:
    primary = comparison[comparison["bear_mode"] == BEAR_RESIDUAL]
    rows = []
    for raw_interval in FREQUENCY_ORDER:
        selected = primary[
            primary["rebalance_days"].isna()
            if raw_interval is None
            else primary["rebalance_days"] == raw_interval
        ]
        if len(selected) != 6:
            raise AssertionError(f"frequency {raw_interval} must have six primary rows")
        rows.append(
            {
                "rebalance_days": raw_interval,
                "frequency_label": frequency_label(raw_interval),
                "frequency_rank": frequency_rank(raw_interval),
                "timing_count": len(selected),
                "median_delta_vs_cash_total_return_pp": float(
                    selected["delta_vs_cash_total_return_pct"].median()
                ),
                "median_delta_vs_cash_post_dotcom_total_return_pp": float(
                    selected["delta_vs_cash_post_dotcom_total_return_pct"].median()
                ),
                "median_delta_vs_hold_total_return_pp": float(
                    selected["delta_vs_hold_total_return_pct"].median()
                ),
                "median_delta_vs_hold_post_dotcom_total_return_pp": float(
                    selected["delta_vs_hold_post_dotcom_total_return_pct"].median()
                ),
                "median_delta_vs_hold_max_drawdown_pp": float(
                    selected["delta_vs_hold_max_drawdown_pct"].median()
                ),
                "positive_vs_hold_timing_count": int(
                    (selected["delta_vs_hold_total_return_pct"] > 0).sum()
                ),
            }
        )
    return pd.DataFrame(rows)


def longest_positive_band(frequencies: pd.DataFrame) -> list[str]:
    numeric = frequencies[frequencies["rebalance_days"].notna()].sort_values("frequency_rank")
    current: list[str] = []
    longest: list[str] = []
    for row in numeric.itertuples(index=False):
        qualifies = (
            float(row.median_delta_vs_hold_total_return_pp) > 0
            and float(row.median_delta_vs_hold_post_dotcom_total_return_pp) > 0
            and float(row.median_delta_vs_hold_max_drawdown_pp) >= -2.0
        )
        if qualifies:
            current.append(str(row.frequency_label))
            if len(current) > len(longest):
                longest = current.copy()
        else:
            current = []
    return longest


def performance_figure(block: dict[str, Any]) -> go.Figure:
    figure = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3])
    daily = block["daily"]
    selected_cases: list[tuple[str, str, str, bool | str]] = []
    for timing_id, timing_label in TIMING_LABELS.items():
        selected_cases.append(
            (f"{timing_id}__CASH__HOLD", f"{timing_label}＋现金", "#64748b", True if timing_id == "SMA200" else "legendonly")
        )
        selected_cases.append(
            (f"{timing_id}__{BEAR_RESIDUAL}__HOLD", f"{timing_label}＋Bear9不再平衡", "#2563eb", True if timing_id in {"SMA200", "LAYERED_190_310"} else "legendonly")
        )
    for case_id, label, color, visible in selected_cases:
        selected = monthly(daily[daily["case_id"] == case_id])
        dash = "dot" if "CASH" in case_id else "solid"
        for row, values, panel in (
            (1, selected["equity"], "equity"),
            (2, drawdown(selected["equity"]), "drawdown"),
        ):
            figure.add_trace(
                go.Scatter(
                    x=selected["date"],
                    y=values,
                    name=label,
                    visible=visible,
                    showlegend=row == 1,
                    line={"color": color, "width": 1.6, "dash": dash},
                    meta={"series_key": case_id.lower(), "label": label, "panel": panel},
                ),
                row=row,
                col=1,
            )
    hold = monthly(block["hold"])
    for row, values, panel in (
        (1, hold["equity"], "equity"),
        (2, drawdown(hold["equity"]), "drawdown"),
    ):
        figure.add_trace(
            go.Scatter(
                x=hold["date"],
                y=values,
                name="QQQ Buy & Hold",
                showlegend=row == 1,
                line={"color": "#111827", "width": 1.8, "dash": "dash"},
                meta={"series_key": "qqq_hold", "label": "QQQ Buy & Hold", "panel": panel, "is_benchmark": True},
            ),
            row=row,
            col=1,
        )
    figure.update_yaxes(title_text="账户净值", row=1, col=1)
    figure.update_yaxes(title_text="回撤 %", row=2, col=1)
    figure.update_layout(height=720, hovermode="x unified")
    return figure


def heatmap_figure(metrics: pd.DataFrame) -> go.Figure:
    primary = metrics[metrics["bear_mode"] == BEAR_RESIDUAL].copy()
    primary["frequency_label"] = primary["rebalance_days"].map(frequency_label)
    x = [frequency_label(value) for value in FREQUENCY_ORDER]
    y = list(TIMING_LABELS.values())
    figure = make_subplots(
        rows=1,
        cols=2,
        subplot_titles=("完整历史：Bear9相对现金总收益差", "剔除2000后：Bear9相对现金总收益差"),
        horizontal_spacing=0.1,
    )
    for column, metric in enumerate(
        ("delta_vs_cash_total_return_pct", "delta_vs_cash_post_dotcom_total_return_pct"), start=1
    ):
        pivot = primary.pivot(index="timing_id", columns="frequency_label", values=metric).reindex(
            index=list(TIMING_LABELS), columns=x
        )
        figure.add_trace(
            go.Heatmap(
                z=pivot.to_numpy(float),
                x=x,
                y=y,
                colorscale="RdBu",
                zmid=0,
                colorbar={"title": "百分点", "x": 0.46 if column == 1 else 1.02},
                text=np.round(pivot.to_numpy(float), 1),
                texttemplate="%{text:+.1f}",
                meta={"series_key": f"robustness_heatmap_{column}", "label": "Bear9相对现金"},
            ),
            row=1,
            col=column,
        )
    figure.update_layout(height=560)
    return figure


def timing_frequency_figure(metrics: pd.DataFrame, timing_id: str) -> go.Figure:
    selected = metrics[metrics["timing_id"] == timing_id].copy()
    cash = selected[selected["bear_mode"] == "CASH"].iloc[0]
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        subplot_titles=("完整历史总收益", "剔除2000后连续区间总收益"),
    )
    modes = [BEAR_RESIDUAL] if timing_id != "LAYERED_190_310" else [BEAR_ZERO_ONLY, BEAR_RESIDUAL]
    colors = {BEAR_RESIDUAL: "#2563eb", BEAR_ZERO_ONLY: "#f59e0b"}
    x = [frequency_label(value) for value in FREQUENCY_ORDER]
    for mode in modes:
        group = selected[selected["bear_mode"] == mode].copy()
        group["frequency_rank"] = group["rebalance_days"].map(frequency_rank)
        group = group.sort_values("frequency_rank")
        for row, metric in ((1, "total_return_pct"), (2, "post_dotcom_total_return_pct")):
            figure.add_trace(
                go.Scatter(
                    x=x,
                    y=group[metric],
                    mode="lines+markers",
                    name=MODE_LABELS[mode],
                    legendgroup=mode,
                    showlegend=row == 1,
                    line={"color": colors[mode], "width": 2},
                    meta={"series_key": f"{timing_id.lower()}_{mode.lower()}_{metric}", "label": MODE_LABELS[mode]},
                ),
                row=row,
                col=1,
            )
    for row, metric in ((1, "total_return_pct"), (2, "post_dotcom_total_return_pct")):
        figure.add_trace(
            go.Scatter(
                x=x,
                y=[float(cash[metric])] * len(x),
                mode="lines",
                name="对应现金基线",
                legendgroup="cash",
                showlegend=row == 1,
                line={"color": "#64748b", "width": 1.6, "dash": "dash"},
                meta={"series_key": f"{timing_id.lower()}_cash_{metric}", "label": "对应现金基线", "is_benchmark": True},
            ),
            row=row,
            col=1,
        )
    figure.update_yaxes(title_text="总收益 %", row=1, col=1)
    figure.update_yaxes(title_text="总收益 %", row=2, col=1)
    figure.update_layout(height=650, hovermode="x unified")
    return figure


def layered_state_figure(block: dict[str, Any]) -> go.Figure:
    state = block["layered_state"]
    figure = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.72, 0.28])
    for column, label, color in (
        ("close", "QQQ Close", "#111827"),
        ("fast_sma", "SMA190", "#2563eb"),
        ("slow_sma", "SMA310", "#f97316"),
    ):
        figure.add_trace(
            go.Scatter(
                x=state["date"], y=state[column], name=label, line={"color": color, "width": 1.4},
                meta={"series_key": f"layered_{column}", "label": label},
            ), row=1, col=1
        )
    figure.add_trace(
        go.Scatter(
            x=state["date"], y=state["qqq_weight"] * 100.0, name="QQQ目标仓位",
            line={"color": "#7c3aed", "width": 1.7, "shape": "hv"},
            meta={"series_key": "layered_qqq_weight", "label": "QQQ目标仓位"},
        ), row=2, col=1
    )
    figure.update_yaxes(title_text="复权价格", row=1, col=1)
    figure.update_yaxes(title_text="QQQ仓位 %", range=[-5, 105], row=2, col=1)
    figure.update_layout(height=720, hovermode="x unified")
    return figure


def summary_html(
    block: dict[str, Any],
    timing: pd.DataFrame,
    frequencies: pd.DataFrame,
    gates: dict[str, Any],
    weights: dict[str, float],
) -> str:
    hold = block["metrics_json"]["qqq_hold_metrics"]
    timing_rows = []
    for item in timing.itertuples(index=False):
        timing_rows.append(
            "<tr>"
            f"<td>{html.escape(item.timing_label)}</td>"
            f"<td>{item.full_positive_total_count}/13</td>"
            f"<td>{item.post_dotcom_positive_total_count}/13</td>"
            f"<td>{item.median_full_total_uplift_pp:+.1f}pp</td>"
            f"<td>{item.median_post_dotcom_total_uplift_pp:+.1f}pp</td>"
            f"<td>{item.median_drawdown_change_pp:+.1f}pp</td>"
            "</tr>"
        )
    weight_rows = "".join(
        f"<tr><td>{html.escape(symbol)}</td><td>{weight:.0%}</td></tr>"
        for symbol, weight in weights.items()
    )
    band = "、".join(gates["longest_positive_rebalance_band"]) or "没有形成"
    return (
        '<section class="summary-block">'
        '<div class="result-cards">'
        f'<div class="result-card"><strong>Bear9稳健择时数</strong><span>{gates["stable_timing_count"]}/6</span><small>完整与剔除2000后均需过半频率胜出</small></div>'
        f'<div class="result-card"><strong>全部78条主路径</strong><span>Sharpe中位差 {gates["median_primary_sharpe_uplift"]:+.3f}</span><small>Bear9填满全部非QQQ仓位</small></div>'
        f'<div class="result-card"><strong>再平衡正向宽带</strong><span>{html.escape(band)}</span><small>相对不再平衡，完整与剔除2000后同时为正</small></div>'
        f'<div class="result-card"><strong>QQQ Buy & Hold</strong><span>{hold["total_return_pct"]:+.1f}%</span><small>CAGR {hold["cagr_pct"]:.2f}%，最大回撤 {hold["max_drawdown_pct"]:.2f}%</small></div>'
        '</div>'
        '<h3>Bear9填满非QQQ仓位：跨频率稳健性</h3>'
        '<div class="event-scroll"><table class="event-table"><thead><tr><th>QQQ择时器</th><th>完整历史胜出</th><th>剔除2000后胜出</th><th>完整收益差中位</th><th>剔除2000收益差中位</th><th>回撤变化中位</th></tr></thead>'
        f'<tbody>{"".join(timing_rows)}</tbody></table></div>'
        '<details><summary>固定Bear9权重</summary><div class="event-scroll"><table class="event-table"><thead><tr><th>标的</th><th>目标权重</th></tr></thead>'
        f'<tbody>{weight_rows}</tbody></table></div><p>DG在上市前的9%保留现金，不分给未来可交易成员。</p></details>'
        '<p><strong>解释边界：</strong>九只股票及其权重来自此前完整历史复核。这次只检查跨QQQ择时窗口和再平衡频率的机械稳健性，不把任何最高点称为未来最优。</p>'
        '</section>'
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    parameters = context.config["parameters"]
    if len(case_definitions(parameters)) != 97:
        raise AssertionError("formal case contract changed")
    record = load_run(context, args.run_id)
    if record.get("status") not in {"running", "completed_unvalidated"}:
        raise RuntimeError("analysis requires a running or completed_unvalidated run")
    if len(record.get("expected_blocks", [])) != 2 or any(
        item.get("status") != "completed" for item in record["expected_blocks"]
    ):
        raise RuntimeError("both cost blocks must be complete before analysis")
    blocks = {
        float(cost): load_block(context, args.run_id, float(cost))
        for cost in context.config["cost_scenarios_bps_per_side"]
    }
    block = blocks[FORMAL_COST_BPS]
    metrics = block["metrics"]
    if len(metrics) != 97:
        raise AssertionError("5bps metrics must contain all 97 cases")
    all_metrics = pd.concat(
        [item["metrics"].assign(cost_bps=cost) for cost, item in blocks.items()], ignore_index=True
    )
    comparison = build_rebalance_comparison(metrics)
    timing = timing_robustness(metrics)
    frequencies = frequency_summary(comparison)
    primary = metrics[metrics["bear_mode"] == BEAR_RESIDUAL]
    stable_count = int(
        (
            (timing["full_positive_total_count"] >= 7)
            & (timing["post_dotcom_positive_total_count"] >= 7)
        ).sum()
    )
    band = longest_positive_band(frequencies)
    gates = {
        "stable_timing_count": stable_count,
        "median_primary_sharpe_uplift": float(primary["delta_vs_cash_sharpe"].median()),
        "median_primary_drawdown_change_pp": float(
            primary["delta_vs_cash_max_drawdown_pct"].median()
        ),
        "longest_positive_rebalance_band": band,
        "bear9_descriptive_robustness_passed": bool(
            stable_count >= 5
            and float(primary["delta_vs_cash_sharpe"].median()) > 0
            and float(primary["delta_vs_cash_max_drawdown_pct"].median()) >= -3.0
        ),
        "rebalance_descriptive_band_passed": len(band) >= 4,
        "promotion_allowed": False,
        "promotion_blocker": "Bear9 members and weights were selected after full-history review",
    }

    run_root = context.run_root(args.run_id)
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(exist_ok=True)
    all_metrics.to_csv(analysis_root / "case_summary_all_costs.csv", index=False, lineterminator="\n")
    metrics.to_csv(analysis_root / "case_summary_5bps.csv", index=False, lineterminator="\n")
    comparison.to_csv(analysis_root / "rebalance_comparison_5bps.csv", index=False, lineterminator="\n")
    timing.to_csv(analysis_root / "timing_robustness_5bps.csv", index=False, lineterminator="\n")
    frequencies.to_csv(analysis_root / "frequency_summary_5bps.csv", index=False, lineterminator="\n")
    summary = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "formal_cost_bps": FORMAL_COST_BPS,
        "robustness_gates": gates,
        "timing_robustness": json.loads(timing.to_json(orient="records")),
        "frequency_summary": json.loads(frequencies.to_json(orient="records")),
        "qqq_hold_metrics": block["metrics_json"]["qqq_hold_metrics"],
        "qqq_hold_post_dotcom_metrics": block["metrics_json"]["qqq_hold_post_dotcom_metrics"],
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    figures = [
        ReportFigure(
            "performance-qqq_bear9_rebalance",
            "六个QQQ择时器：现金与Bear9不再平衡净值",
            performance_figure(block),
            "performance",
        ),
        ReportFigure(
            "robustness-heatmap",
            "Bear9相对现金：择时器×再平衡频率",
            heatmap_figure(metrics),
            "generic",
        ),
        ReportFigure(
            "layered-state",
            "SMA190/310分层策略的QQQ目标仓位",
            layered_state_figure(block),
            "market",
        ),
    ]
    for timing_id, label in TIMING_LABELS.items():
        figures.append(
            ReportFigure(
                f"timing-{timing_id.lower().replace('_', '-')}",
                f"{label}：再平衡频率曲线",
                timing_frequency_figure(metrics, timing_id),
                "generic",
            )
        )
    notes = [
        "五个对称QQQ择时器都使用完成Close相对SMA上下3%的连续滞回；SMA190/310则是0%、30%、70%、100%四档分层仓位，不是普通穿越策略。",
        "Bear9固定为AZO、SO、ED、ORLY、MO、WRB、DLTR、DG和WMT，使用10%、12%、13%、10%、13%、12%、8%、9%、13%，九只都没有自身SMA。",
        "不再平衡只在QQQ仓位档位或可交易成员集合变化时调仓；其余版本从最近一次目标调整起，每隔指定共同交易日恢复完整目标权重。",
        "正式解释使用单边5bps，0bps只检查成本方向；完成Close确认，下一共同Open先卖后买，允许小数股、无融资、现金不计息。",
        "剔除2000后的口径从第一段主观2000-2002熊市结束后的首个共同交易日开始，继承当时真实账户状态并重新基准化，不重启策略。",
        "本实验不选择最佳SMA或最佳再平衡频率；孤立高点不算稳健，九只资产及权重的事后选择偏差仍然存在。",
    ]
    report = render_interactive_report(
        title="QQQ六择时器：Bear9与再平衡稳健性",
        heading="Bear9能否跨均线窗口成立？再平衡有没有宽平台？",
        subtitle="SMA160/180/200/220/240与SMA190/310分层状态；97条路径，完整历史与剔除2000后并列。",
        summary_html=summary_html(block, timing, frequencies, gates, parameters["bear9_weights"]),
        notes=notes,
        figures=figures,
        experiment=context.config,
        run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    css = """
<style>
.result-cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.result-card{display:flex;flex-direction:column;gap:7px;border:1px solid #dbe3ec;border-radius:12px;padding:15px;background:#fff}.result-card strong{font-size:1.02rem}.result-card span{font-variant-numeric:tabular-nums;color:#334155}.result-card small{color:#64748b}.event-scroll{overflow-x:auto}.event-table{width:100%;border-collapse:collapse;margin:12px 0;font-variant-numeric:tabular-nums}.event-table th,.event-table td{padding:8px 10px;border-bottom:1px solid #e2e8f0;text-align:right;white-space:nowrap}.event-table th:first-child,.event-table td:first-child{text-align:left}@media(max-width:1000px){.result-cards{grid-template-columns:repeat(2,1fr)}}@media(max-width:650px){.result-cards{grid-template-columns:1fr}}@media print{.result-card{break-inside:avoid}}
</style>
"""
    report = report.replace("</head>", css + "</head>")
    downloads = (
        '<section class="chart"><h2>结果下载</h2><p>'
        '<a download href="analysis/timing_robustness_5bps.csv">六择时器稳健性</a> · '
        '<a download href="analysis/frequency_summary_5bps.csv">再平衡频率汇总</a> · '
        '<a download href="analysis/rebalance_comparison_5bps.csv">全部Bear9再平衡对照</a> · '
        '<a download href="analysis/case_summary_all_costs.csv">97条路径与0/5bps</a> · '
        '<a download href="QQQ_BEAR9_REBALANCE/cost_5bps/orders.csv">完整成交</a>'
        '</p></section>'
    )
    report = report.replace("</main>", downloads + "</main>")
    (run_root / "report.html").write_text(report, encoding="utf-8")

    lines = [
        "# QQQ六择时器：Bear9与再平衡稳健性",
        "",
        "## 5bps预登记摘要",
        "",
        f"- Bear9主路径在完整与剔除2000后均有过半频率胜出的择时器：{stable_count}/6。",
        f"- 78条Bear9填满剩余仓位路径的Sharpe差中位数：{gates['median_primary_sharpe_uplift']:+.3f}。",
        f"- 相对不再平衡的最长正向频率带：{'、'.join(band) if band else '没有形成'}。",
        f"- Bear9描述性稳健门槛：{'通过' if gates['bear9_descriptive_robustness_passed'] else '未通过'}；再平衡宽带门槛：{'通过' if gates['rebalance_descriptive_band_passed'] else '未通过'}。",
        "",
        "## 六个择时器",
        "",
    ]
    for item in timing.itertuples(index=False):
        lines.append(
            f"- {item.timing_label}：完整历史 {item.full_positive_total_count}/13、剔除2000后 {item.post_dotcom_positive_total_count}/13；总收益差中位 {item.median_full_total_uplift_pp:+.1f}pp / {item.median_post_dotcom_total_uplift_pp:+.1f}pp。"
        )
    lines.extend(
        [
            "",
            "## 研究边界",
            "",
            "- Bear9成员与权重来自完整历史复核，本实验不能直接晋级模拟盘。",
            "- 所有频率完整保留，报告不会把全样本最高频率称为未来最优。",
            "- DG上市前9%留现金，主观熊市区间只定义剔除2000后的报告边界。",
        ]
    )
    (run_root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    tracked = [
        "backtest/quantkit/qqq_bear9_rebalance.py",
        "backtest/quantkit/qqq_flat_substitution.py",
        "backtest/quantkit/trend_score_portfolio.py",
        "backtest/quantkit/execution.py",
        "backtest/quantkit/metrics.py",
        "backtest/quantkit/experiment.py",
        "backtest/quantkit/reporting.py",
        "backtest/scripts/run_qqq_bear9_rebalance_robustness.py",
        "backtest/scripts/analyze_qqq_bear9_rebalance_robustness.py",
        "backtest/scripts/finalize_qqq_bear9_rebalance_robustness.py",
        "backtest/scripts/smoke_qqq_bear9_rebalance_report.mjs",
        "backtest/scripts/smoke_report_ui.mjs",
        "backtest/scripts/print_html_pdf.mjs",
        "backtest/scripts/validate_run.py",
        "backtest/tests/strategies/tim/test_qqq_bear9_rebalance.py",
        "backtest/experiments/TIM/TIM-v0.40d.1__26-08-25__qqq_timing_bear9_rebalance_robustness/experiment.json",
        "backtest/requirements.lock",
        "backtest/report_templates/interactive_research_v5/page.html",
        "backtest/report_templates/interactive_research_v5/styles.css",
        "backtest/report_templates/interactive_research_v5/interactions.js",
        "research/market_views/subjective_spy_qqq_bear_markets_peak_to_trough.json",
    ]
    provenance: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
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
    for relative in block["manifest"]["source_files"]:
        path = WORKSPACE_ROOT / relative
        provenance["source_files"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    (run_root / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (run_root / "README.md").write_text(
        f"""# Run {args.run_id}

This immutable run compares cash and fixed-weight Bear9 residual sleeves across six QQQ timing policies and thirteen rebalance policies.

- `report.html` / `report.md`: v5 strategy-first report and concise Chinese summary.
- `report.pdf`: Chrome-printed report after the v5 print gate.
- `analysis/`: all-case, timing-robustness, and rebalance-frequency summaries.
- `{FORMAL_SYMBOL}/`: immutable 0/5 bps ledgers, target schedules, timing states, and source manifests.
- `provenance.json`: exact source-code, market-data, and bear-boundary hashes.
- `validation.json`: tests, audit, browser, PDF, reconciliation, and lifecycle evidence.
""",
        encoding="utf-8",
    )
    print(f"Wrote {run_root / 'report.html'} with {len(figures)} interactive figures")


if __name__ == "__main__":
    main()
