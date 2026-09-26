#!/usr/bin/env python3
"""Build the v5 report for strict intersections of three 90% position signals."""

from __future__ import annotations

import argparse
import json
import platform
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import plotly
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from quantkit.experiment import assert_run_writable, cost_label, load_experiment, load_run, sha256
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts.run_intraday_sma_backtest import json_safe


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/TIM/TIM-v0.80a.5__26-08-26__qqq_stochrsi_90_and_gates"
)
PATH_IDS = (
    "S1_90",
    "S2_90",
    "S4_90",
    "S1_AND_S2_90",
    "S1_AND_S4_90",
    "S2_AND_S4_90",
    "S1_AND_S2_AND_S4_90",
)
INTERSECTION_IDS = PATH_IDS[3:]
LABELS = {
    "S1_90": "策略1 >90%",
    "S2_90": "策略2 >90%",
    "S4_90": "策略4 >90%",
    "S1_AND_S2_90": "策略1 ∩ 策略2",
    "S1_AND_S4_90": "策略1 ∩ 策略4",
    "S2_AND_S4_90": "策略2 ∩ 策略4",
    "S1_AND_S2_AND_S4_90": "策略1 ∩ 策略2 ∩ 策略4",
}
COLORS = {
    "S1_90": "#2563eb",
    "S2_90": "#7c3aed",
    "S4_90": "#0891b2",
    "S1_AND_S2_90": "#16a34a",
    "S1_AND_S4_90": "#eab308",
    "S2_AND_S4_90": "#f97316",
    "S1_AND_S2_AND_S4_90": "#dc2626",
}


def _read(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if "date" in frame:
        frame["date"] = pd.to_datetime(frame["date"])
    return frame


def _path_daily(block: Path, path_id: str) -> pd.DataFrame:
    return _read(block / "paths" / path_id / "daily.csv")


def _pct(value: object) -> str:
    return "—" if value is None or pd.isna(value) else f"{float(value):.2f}%"


def _number(value: object, digits: int = 3) -> str:
    return "—" if value is None or pd.isna(value) else f"{float(value):.{digits}f}"


def _result_table(rows: pd.DataFrame, naive: dict[str, object]) -> str:
    body = []
    for row in rows.itertuples():
        path_type = "单独90%" if row.path_type == "individual_90" else "信号交集"
        body.append(
            "<tr>"
            f"<td>{LABELS[row.path_id]}</td><td>{path_type}</td>"
            f"<td>{row.cagr_pct:.2f}%</td><td>{_pct(row.holding_period_cagr_pct)}</td>"
            f"<td>{row.holding_time_pct:.2f}%</td><td>{_number(row.sharpe)}</td>"
            f"<td>{row.max_drawdown_pct:.2f}%</td><td>{row.turnover_multiple:.2f}×</td>"
            f"<td>{int(row.order_count)}</td></tr>"
        )
    body.append(
        "<tr><td>QQQ Buy & Hold</td><td>基准</td>"
        f"<td>{float(naive['cagr_pct']):.2f}%</td>"
        f"<td>{_pct(naive['holding_period_cagr_pct'])}</td>"
        f"<td>{float(naive['holding_time_pct']):.2f}%</td>"
        f"<td>{float(naive['sharpe']):.3f}</td>"
        f"<td>{float(naive['max_drawdown_pct']):.2f}%</td>"
        f"<td>{float(naive['turnover_multiple']):.2f}×</td>"
        f"<td>{int(naive['order_count'])}</td></tr>"
    )
    return (
        '<div class="table-wrap"><table><thead><tr><th>路径</th><th>类型</th>'
        '<th>日历CAGR</th><th>持仓期间CAGR</th><th>持仓时间</th><th>Sharpe</th>'
        '<th>最大回撤</th><th>换手倍数</th><th>订单数</th></tr></thead><tbody>'
        + "".join(body) + "</tbody></table></div>"
    )


def signal_figure(block: Path) -> go.Figure:
    indicator = _read(block / "indicator_daily.csv")
    states = np.vstack([
        _path_daily(block, path_id)["is_long"].to_numpy(int) for path_id in PATH_IDS
    ])
    figure = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.07,
        row_heights=[0.40, 0.34, 0.26],
        subplot_titles=("QQQ Close", "三条母策略的收盘后QQQ仓位", "七条90%门控的持仓状态"),
    )
    figure.add_trace(go.Scatter(
        x=indicator.date,
        y=indicator.close,
        name="QQQ Close",
        line={"color": "#111827"},
        meta={"series_key": "qqq_close", "panel": "price", "label": "QQQ Close"},
    ), row=1, col=1)
    for signal_id, color, label in (
        ("S1", "#2563eb", "策略1仓位"),
        ("S2", "#7c3aed", "策略2仓位"),
        ("S4", "#0891b2", "策略4仓位"),
    ):
        figure.add_trace(go.Scatter(
            x=indicator.date,
            y=indicator[f"{signal_id}_mother_weight"] * 100,
            name=label,
            line={"color": color},
            meta={"series_key": f"{signal_id.lower()}_mother_weight", "panel": "market", "label": label, "control_group": "mother_weight", "control_group_label": "母策略仓位"},
        ), row=2, col=1)
    figure.add_hline(y=90, line_dash="dot", line_color="#64748b", row=2, col=1)
    figure.add_trace(go.Heatmap(
        x=indicator.date,
        y=[LABELS[path_id] for path_id in PATH_IDS],
        z=states,
        zmin=0,
        zmax=1,
        colorscale=[[0, "#f1f5f9"], [1, "#16a34a"]],
        showscale=False,
        hovertemplate="%{y}<br>%{x|%Y-%m-%d}<br>%{z}<extra></extra>",
        name="持仓状态",
        meta={"series_key": "gate_state_heatmap", "panel": "market", "label": "门控持仓状态"},
    ), row=3, col=1)
    figure.update_yaxes(title_text="美元", row=1, col=1)
    figure.update_yaxes(title_text="%", range=[-3, 103], row=2, col=1)
    figure.update_layout(height=940, hovermode="x unified", legend={"orientation": "h"})
    return figure


def performance_figure(block: Path) -> go.Figure:
    figure = go.Figure()
    for path_id in PATH_IDS:
        daily = _path_daily(block, path_id)
        figure.add_trace(go.Scatter(
            x=daily.date,
            y=daily.equity,
            name=LABELS[path_id],
            line={"color": COLORS[path_id], "width": 2.5 if path_id in INTERSECTION_IDS else 1.6},
            meta={"series_key": path_id.lower(), "panel": "equity", "label": LABELS[path_id], "cost_bps": 0},
        ))
    naive = _read(block / "naive_buy_hold_daily.csv")
    figure.add_trace(go.Scatter(
        x=naive.date,
        y=naive.equity,
        name="QQQ Buy & Hold",
        line={"color": "#111827", "width": 3, "dash": "dot"},
        meta={"series_key": "naive_qqq_buy_hold", "panel": "equity", "label": "QQQ Buy & Hold", "is_benchmark": True, "cost_bps": 0},
    ))
    figure.update_layout(
        height=680,
        hovermode="x unified",
        yaxis_title="固定10万美元账户净值",
        legend={"orientation": "h"},
    )
    return figure


def capital_figure(block: Path) -> go.Figure:
    figure = make_subplots(
        rows=4,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.055,
        subplot_titles=tuple(f"{LABELS[path_id]}：QQQ持仓资金与现金" for path_id in INTERSECTION_IDS),
    )
    for row_number, path_id in enumerate(INTERSECTION_IDS, start=1):
        daily = _path_daily(block, path_id)
        position = daily.get("position_value", daily.shares * daily.close)
        figure.add_trace(go.Scatter(
            x=daily.date,
            y=position,
            name=f"{LABELS[path_id]} QQQ",
            stackgroup=f"capital_{row_number}",
            line={"color": COLORS[path_id]},
            meta={"series_key": f"{path_id.lower()}_position", "panel": "equity", "label": f"{LABELS[path_id]} QQQ", "cost_bps": 0},
        ), row=row_number, col=1)
        figure.add_trace(go.Scatter(
            x=daily.date,
            y=daily.cash,
            name=f"{LABELS[path_id]} 现金",
            stackgroup=f"capital_{row_number}",
            line={"color": "#cbd5e1"},
            meta={"series_key": f"{path_id.lower()}_cash", "panel": "equity", "label": f"{LABELS[path_id]} 现金", "cost_bps": 0},
        ), row=row_number, col=1)
        figure.update_yaxes(title_text="美元", row=row_number, col=1)
    figure.update_layout(height=1100, hovermode="x unified", legend={"orientation": "h"})
    return figure


def drawdown_figure(block: Path) -> go.Figure:
    figure = go.Figure()
    for path_id in PATH_IDS:
        daily = _path_daily(block, path_id)
        equity = daily.equity.to_numpy(float)
        drawdown = equity / np.maximum.accumulate(equity) - 1.0
        figure.add_trace(go.Scatter(
            x=daily.date,
            y=drawdown * 100,
            name=LABELS[path_id],
            line={"color": COLORS[path_id]},
            meta={"series_key": f"{path_id.lower()}_drawdown", "panel": "drawdown", "label": LABELS[path_id], "cost_bps": 0},
        ))
    figure.update_layout(
        height=560,
        hovermode="x unified",
        yaxis_title="回撤 %",
        legend={"orientation": "h"},
    )
    return figure


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    assert_run_writable(context, args.run_id)
    run = load_run(context, args.run_id)
    incomplete = [item["block_id"] for item in run["expected_blocks"] if item["status"] != "completed"]
    if incomplete:
        raise RuntimeError(f"Incomplete blocks: {incomplete}")
    run_root = context.run_root(args.run_id)
    block = run_root / "QQQ" / cost_label(0)
    metrics = json.loads((block / "metrics.json").read_text(encoding="utf-8"))
    rows = pd.read_csv(block / "parameter_results.csv")
    if rows.path_id.tolist() != list(PATH_IDS):
        raise AssertionError("Report path order does not match the seven frozen gates")
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(parents=True, exist_ok=True)
    rows.to_csv(analysis_root / "results_table.csv", index=False, lineterminator="\n")
    intersections = rows[rows.path_type.eq("intersection")].copy()
    intersections["cagr_gap_vs_qqq_pct_points"] = (
        intersections.cagr_pct - float(metrics["naive_buy_hold"]["cagr_pct"])
    )
    intersections.to_csv(
        analysis_root / "intersection_comparison.csv", index=False, lineterminator="\n"
    )
    summary = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "purpose": "strict intersections of corrected strategy 1, 2 and 4 90% position signals",
        "analysis_start": metrics["analysis_start"],
        "analysis_end": metrics["analysis_end"],
        "strategy_path_count": metrics["strategy_path_count"],
        "paths": metrics["paths"],
        "naive_buy_hold": metrics["naive_buy_hold"],
        "truth_table_audit": metrics["truth_table_audit"],
        "strict_90_equality_audit": metrics["strict_90_equality_audit"],
        "holding_period_cagr_definition": metrics["holding_period_cagr_definition"],
        "max_cross_check_difference": metrics["max_cross_check_difference"],
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# QQQ StochRSI 90%持仓信号交集",
        "",
        "- 窗口：2015-01-02～2026-08-04；成本：0 bps；当天收盘确认并成交。",
        "- AND表示所有指定母策略的收盘后QQQ仓位都必须严格大于90%。",
        "- 持仓期间CAGR是按有仓位交易日比例压缩的描述性指标，不是独立回测或子区间IRR。",
        "",
        "| 路径 | 类型 | 日历CAGR | 持仓期间CAGR | 持仓时间 | Sharpe | 最大回撤 | 订单数 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows.itertuples():
        lines.append(
            f"| {LABELS[row.path_id]} | {row.path_type} | {row.cagr_pct:.3f}% | "
            f"{_pct(row.holding_period_cagr_pct)} | {row.holding_time_pct:.2f}% | "
            f"{_number(row.sharpe)} | {row.max_drawdown_pct:.2f}% | {int(row.order_count)} |"
        )
    (run_root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = render_interactive_report(
        title="QQQ StochRSI 90%持仓信号交集",
        heading="三条单独信号、四条AND交集与QQQ",
        subtitle="2015–2026 · 零成本 · 同Close成交 · 严格大于90%",
        summary_html=_result_table(rows, metrics["naive_buy_hold"]),
        notes=[
            "AND表示组合内每一条90%信号必须在同一个收盘日都为真；任何一条变假，账户立即回到现金。",
            "三条母策略只提供收盘后仓位信号；七条门控账户不继承母策略的现金、份额或外部注资。",
            "等于90%不算持仓；所有门控账户固定10万美元并按普通QQQ收盘价成交。",
            "持仓期间CAGR假设空仓收益为零，只描述收益在持仓时间中的几何强度，必须与日历CAGR和持仓时间一起看。",
            "完整区间是探索性结果，交集减少暴露也会减少样本数量，不能直接视为未来最优组合。",
        ],
        figures=[
            ReportFigure("market-qqq", "QQQ、三条母策略仓位与七条持仓状态", signal_figure(block), "market"),
            ReportFigure("performance-qqq", "七条固定资本门控与QQQ", performance_figure(block), "performance"),
            ReportFigure("capital-qqq", "四条AND账户的QQQ持仓资金与现金", capital_figure(block), "market"),
            ReportFigure("drawdown-qqq", "七条门控的回撤路径", drawdown_figure(block), "drawdown"),
        ],
        experiment=context.config,
        run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")
    tracked = [
        "backtest/requirements.lock",
        "backtest/quantkit/experiment.py",
        "backtest/quantkit/dual_stochrsi_timing.py",
        "backtest/quantkit/stochrsi_scaled_pools.py",
        "backtest/quantkit/stochrsi_position_gates.py",
        "backtest/quantkit/stochrsi_pruned_accumulation.py",
        "backtest/quantkit/reporting.py",
        "backtest/scripts/run_stochrsi_90_and_gates.py",
        "backtest/scripts/analyze_stochrsi_90_and_gates.py",
        "backtest/scripts/finalize_stochrsi_90_and_gates.py",
        "backtest/report_templates/interactive_research_v5/page.html",
        "backtest/report_templates/interactive_research_v5/styles.css",
        "backtest/report_templates/interactive_research_v5/interactions.js",
        "data/processed/manifest.json",
        "data/processed/daily/QQQ.csv",
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
        provenance["source_files"][relative] = {
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
    (run_root / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (run_root / "README.md").write_text(
        f"# Run {args.run_id}\n\nFrozen strict 90% signal intersections; print report.pdf, then finalize.\n",
        encoding="utf-8",
    )
    print(f"Wrote {run_root / 'report.html'}; PDF printing and finalization remain pending")


if __name__ == "__main__":
    main()
