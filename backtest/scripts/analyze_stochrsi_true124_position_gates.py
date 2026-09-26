#!/usr/bin/env python3
"""Build the v5 report for the corrected QQQ StochRSI mother 1/2/4 study."""

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
    "experiments/TIM/TIM-v0.80a.4__26-08-26__qqq_stochrsi_true124_position_gates"
)
MOTHER_IDS = ("M1_D0_F0_S0", "M2_D0_F1_S1", "M4_PRUNED_ACCUMULATION")
THRESHOLDS = (70, 80, 90)
LABELS = {
    "M1_D0_F0_S0": "策略1 原始累计 D0_F0_S0",
    "M2_D0_F1_S1": "策略2 D0_F1_S1",
    "M4_PRUNED_ACCUMULATION": "策略4 精简累计仓位",
}
COLORS = {
    "M1_D0_F0_S0": "#2563eb",
    "M2_D0_F1_S1": "#7c3aed",
    "M4_PRUNED_ACCUMULATION": "#0891b2",
}


def _read(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if "date" in frame:
        frame["date"] = pd.to_datetime(frame["date"])
    return frame


def _unitized(frame: pd.DataFrame, initial_cash: float = 100_000.0) -> np.ndarray:
    equity = frame["equity"].to_numpy(float)
    flows = frame.get("external_contribution", pd.Series(0.0, index=frame.index)).to_numpy(float)
    returns = np.zeros(len(frame), dtype=float)
    if len(frame) > 1:
        returns[1:] = (equity[1:] - flows[1:]) / equity[:-1] - 1.0
    return initial_cash * np.cumprod(1.0 + returns)


def _path_daily(block: Path, path_id: str) -> pd.DataFrame:
    return _read(block / "paths" / path_id / "daily.csv")


def _pct(value: object) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):.2f}%"


def _result_table(rows: pd.DataFrame, naive: dict[str, object]) -> str:
    body = []
    for row in rows.itertuples():
        threshold = "—" if pd.isna(row.gate_threshold) else f"{row.gate_threshold * 100:.0f}%"
        body.append(
            "<tr>"
            f"<td>{row.path_id}</td><td>{'母策略' if row.path_type == 'mother' else '全仓/空仓'}</td>"
            f"<td>{threshold}</td><td>{row.cagr_pct:.2f}%</td>"
            f"<td>{_pct(row.holding_period_cagr_pct)}</td><td>{row.holding_time_pct:.1f}%</td>"
            f"<td>{row.sharpe:.3f}</td><td>{row.max_drawdown_pct:.2f}%</td>"
            f"<td>{row.average_qqq_weight_pct:.1f}%</td><td>{row.turnover_multiple:.2f}×</td>"
            f"<td>${row.external_contributions:,.0f}</td></tr>"
        )
    body.append(
        "<tr><td>QQQ_NAIVE_BUY_HOLD</td><td>基准</td><td>—</td>"
        f"<td>{float(naive['cagr_pct']):.2f}%</td>"
        f"<td>{_pct(naive['holding_period_cagr_pct'])}</td>"
        f"<td>{float(naive['holding_time_pct']):.1f}%</td>"
        f"<td>{float(naive['sharpe']):.3f}</td>"
        f"<td>{float(naive['max_drawdown_pct']):.2f}%</td><td>100.0%</td>"
        f"<td>{float(naive['turnover_multiple']):.2f}×</td><td>$0</td></tr>"
    )
    return (
        '<div class="table-wrap"><table><thead><tr><th>路径</th><th>类型</th><th>门槛</th>'
        '<th>日历CAGR</th><th>持仓期间CAGR</th><th>持仓时间</th><th>Sharpe</th>'
        '<th>最大回撤</th><th>平均QQQ仓位</th><th>换手倍数</th><th>外部注资</th>'
        '</tr></thead><tbody>' + "".join(body) + "</tbody></table></div>"
    )


def market_figure(block: Path) -> go.Figure:
    indicator = _read(block / "indicator_daily.csv")
    figure = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.07,
        row_heights=[0.42, 0.29, 0.29],
        subplot_titles=("QQQ Close", "StochRSI 42 / 100", "真正策略1、2、4的收盘后QQQ仓位"),
    )
    figure.add_trace(go.Scatter(
        x=indicator.date,
        y=indicator.close,
        name="QQQ Close",
        line={"color": "#111827"},
        meta={"series_key": "qqq_close", "panel": "price", "label": "QQQ Close"},
    ), row=1, col=1)
    figure.add_trace(go.Scatter(
        x=indicator.date,
        y=indicator.stochrsi_42,
        name="StochRSI42",
        line={"color": "#2563eb"},
        meta={"series_key": "stochrsi42", "panel": "market", "label": "StochRSI42", "control_group": "indicator", "control_group_label": "指标"},
    ), row=2, col=1)
    figure.add_trace(go.Scatter(
        x=indicator.date,
        y=indicator.stochrsi_100,
        name="StochRSI100",
        line={"color": "#f97316"},
        meta={"series_key": "stochrsi100", "panel": "market", "label": "StochRSI100", "control_group": "indicator", "control_group_label": "指标"},
    ), row=2, col=1)
    for level in (0.20, 0.50, 0.80):
        figure.add_hline(y=level, line_dash="dot", line_color="#94a3b8", row=2, col=1)
    for mother_id in MOTHER_IDS:
        daily = _path_daily(block, mother_id)
        figure.add_trace(go.Scatter(
            x=daily.date,
            y=daily.qqq_weight * 100,
            name=LABELS[mother_id],
            line={"color": COLORS[mother_id]},
            meta={"series_key": f"{mother_id.lower()}_weight", "panel": "market", "label": LABELS[mother_id], "control_group": "mother_weight", "control_group_label": "母策略仓位"},
        ), row=3, col=1)
    for level in THRESHOLDS:
        figure.add_hline(y=level, line_dash="dot", line_color="#94a3b8", row=3, col=1)
    figure.update_yaxes(title_text="美元", row=1, col=1)
    figure.update_yaxes(title_text="0–1", range=[-0.03, 1.03], row=2, col=1)
    figure.update_yaxes(title_text="%", range=[-3, 103], row=3, col=1)
    figure.update_layout(height=920, hovermode="x unified", legend={"orientation": "h"})
    return figure


def group_figure(block: Path, mother_id: str) -> go.Figure:
    mother = _path_daily(block, mother_id)
    naive = _read(block / "naive_buy_hold_daily.csv")
    matched_path = block / "paths" / mother_id / "matched_buy_hold_daily.csv"
    benchmark = _read(matched_path) if matched_path.is_file() else naive
    benchmark_name = "同步注资 QQQ Buy & Hold" if matched_path.is_file() else "Naive QQQ Buy & Hold"
    figure = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.35, 0.35, 0.30],
        subplot_titles=(
            "母策略实际账户与对应基准",
            "统一按10万美元单位化后的母策略、三门控与QQQ",
            "母策略QQQ持仓资金与现金",
        ),
    )
    figure.add_trace(go.Scatter(
        x=mother.date,
        y=mother.equity,
        name=LABELS[mother_id],
        line={"color": COLORS[mother_id], "width": 2.5},
        meta={"series_key": f"{mother_id.lower()}_actual", "panel": "equity", "label": LABELS[mother_id], "cost_bps": 0},
    ), row=1, col=1)
    figure.add_trace(go.Scatter(
        x=benchmark.date,
        y=benchmark.equity,
        name=benchmark_name,
        line={"color": "#64748b", "dash": "dot"},
        meta={"series_key": f"{mother_id.lower()}_benchmark", "panel": "equity", "label": benchmark_name, "cost_bps": 0},
    ), row=1, col=1)
    figure.add_trace(go.Scatter(
        x=mother.date,
        y=_unitized(mother),
        name=f"{LABELS[mother_id]}（单位化）",
        line={"color": COLORS[mother_id], "width": 2.5},
        meta={"series_key": f"{mother_id.lower()}_unitized", "panel": "equity", "label": f"{LABELS[mother_id]}（单位化）", "cost_bps": 0},
    ), row=2, col=1)
    for threshold, color in zip(THRESHOLDS, ("#16a34a", "#eab308", "#dc2626")):
        path_id = f"{mother_id}_GATE_{threshold}"
        gate = _path_daily(block, path_id)
        label = f">{threshold}% 全仓/空仓"
        figure.add_trace(go.Scatter(
            x=gate.date,
            y=gate.equity,
            name=label,
            line={"color": color},
            meta={"series_key": path_id.lower(), "panel": "equity", "label": label, "cost_bps": 0},
        ), row=2, col=1)
    figure.add_trace(go.Scatter(
        x=naive.date,
        y=naive.equity,
        name="Naive QQQ",
        line={"color": "#111827", "dash": "dot", "width": 2},
        meta={"series_key": f"{mother_id.lower()}_naive_qqq", "panel": "equity", "label": "Naive QQQ", "is_benchmark": True, "cost_bps": 0},
    ), row=2, col=1)
    position = mother.get("position_value", mother.shares * mother.close)
    figure.add_trace(go.Scatter(
        x=mother.date,
        y=position,
        name="QQQ持仓市值",
        line={"color": "#0ea5e9"},
        meta={"series_key": f"{mother_id.lower()}_position", "panel": "equity", "label": "QQQ持仓市值", "cost_bps": 0},
    ), row=3, col=1)
    figure.add_trace(go.Scatter(
        x=mother.date,
        y=mother.cash,
        name="现金",
        line={"color": "#a855f7"},
        meta={"series_key": f"{mother_id.lower()}_cash", "panel": "equity", "label": "现金", "cost_bps": 0},
    ), row=3, col=1)
    figure.update_yaxes(title_text="美元", row=1, col=1)
    figure.update_yaxes(title_text="单位化美元", row=2, col=1)
    figure.update_yaxes(title_text="美元", row=3, col=1)
    figure.update_layout(height=980, hovermode="x unified", legend={"orientation": "h"})
    return figure


def final_gate_figure(block: Path) -> go.Figure:
    figure = go.Figure()
    for mother_id in MOTHER_IDS:
        for threshold, dash in zip(THRESHOLDS, ("solid", "dash", "dot")):
            path_id = f"{mother_id}_GATE_{threshold}"
            daily = _path_daily(block, path_id)
            figure.add_trace(go.Scatter(
                x=daily.date,
                y=daily.equity,
                name=f"{mother_id.split('_')[0]} >{threshold}%",
                line={"color": COLORS[mother_id], "dash": dash},
                meta={"series_key": path_id.lower(), "panel": "equity", "label": f"{mother_id.split('_')[0]} >{threshold}%", "cost_bps": 0},
            ))
    naive = _read(block / "naive_buy_hold_daily.csv")
    figure.add_trace(go.Scatter(
        x=naive.date,
        y=naive.equity,
        name="QQQ Buy & Hold",
        line={"color": "#111827", "width": 3},
        meta={"series_key": "naive_qqq_buy_hold", "panel": "equity", "label": "QQQ Buy & Hold", "is_benchmark": True, "cost_bps": 0},
    ))
    figure.update_layout(
        height=660,
        hovermode="x unified",
        yaxis_title="固定10万美元账户净值",
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
    if len(rows) != 12 or int((rows.path_type == "position_gate").sum()) != 9:
        raise AssertionError("Report requires exactly three mothers and nine gates")
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(parents=True, exist_ok=True)
    rows.to_csv(analysis_root / "results_table.csv", index=False, lineterminator="\n")
    fixed = rows[rows.path_type.eq("position_gate")].copy()
    fixed["cagr_gap_vs_qqq_pct_points"] = fixed.cagr_pct - float(metrics["naive_buy_hold"]["cagr_pct"])
    fixed["drawdown_improvement_vs_qqq_pct_points"] = (
        fixed.max_drawdown_pct - float(metrics["naive_buy_hold"]["max_drawdown_pct"])
    )
    fixed.to_csv(
        analysis_root / "fixed_capital_gate_comparison.csv", index=False, lineterminator="\n"
    )
    summary = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "purpose": "corrected true mother 1/2/4 plus nine fixed-capital binary gates",
        "analysis_start": metrics["analysis_start"],
        "analysis_end": metrics["analysis_end"],
        "strategy_path_count": metrics["strategy_path_count"],
        "paths": metrics["paths"],
        "naive_buy_hold": metrics["naive_buy_hold"],
        "holding_period_cagr_definition": metrics["holding_period_cagr_definition"],
        "strict_gate_equality_audit": metrics["strict_gate_equality_audit"],
        "pruned_accumulation_audit": metrics["pruned_accumulation_audit"],
        "max_cross_check_difference": metrics["max_cross_check_difference"],
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# QQQ StochRSI真正1/2/4母策略与持仓门控",
        "",
        "- 窗口：2015-01-02～2026-08-04；成本：0 bps；当天收盘确认并成交。",
        "- 持仓期间CAGR按有QQQ仓位的交易日比例对日历CAGR做几何时间压缩；不是独立回测或子区间IRR。",
        "",
        "| 路径 | 类型 | 日历CAGR | 持仓期间CAGR | 持仓时间 | Sharpe | 最大回撤 | 平均QQQ仓位 | 外部注资 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows.itertuples():
        lines.append(
            f"| {row.path_id} | {row.path_type} | {row.cagr_pct:.3f}% | "
            f"{_pct(row.holding_period_cagr_pct)} | {row.holding_time_pct:.2f}% | "
            f"{row.sharpe:.3f} | {row.max_drawdown_pct:.2f}% | "
            f"{row.average_qqq_weight_pct:.2f}% | ${row.external_contributions:,.2f} |"
        )
    (run_root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = render_interactive_report(
        title="QQQ StochRSI真正1/2/4母策略与持仓门控",
        heading="真正策略1、2、4，九条全仓/空仓信号与QQQ",
        subtitle="2015–2026 · 零成本 · 同Close成交 · 门槛严格大于",
        summary_html=_result_table(rows, metrics["naive_buy_hold"]),
        notes=[
            "策略1是原始D0_F0_S0；策略2是D0_F1_S1；策略4是去掉单标的补充机制后的精简累计仓位策略。",
            "九条门控账户只读取母策略收盘后仓位比例，不继承母策略的现金、份额或外部注资。",
            "持仓期间CAGR按有QQQ仓位的交易日比例压缩日历CAGR；例如日历CAGR 10%、持仓50%时为21%。",
            "该指标假设空仓收益为零，只描述收益在持仓时间中的几何强度；它不是另一次回测，也不是实际持仓子区间IRR，必须和日历CAGR及持仓时间一起看。",
            "对分数仓位母策略，只要仍有少量QQQ就计作持仓日，因此这个指标对九条二元门控最有解释力。",
            "完整区间是探索性结果，不构成QQQ以外股票或真实轮动组合的样本外证据。",
        ],
        figures=[
            ReportFigure("market-qqq", "市场、指标与真正1/2/4母策略仓位", market_figure(block), "market"),
            ReportFigure("mother1-qqq", "策略1：原始累计、三门控与资金构成", group_figure(block, MOTHER_IDS[0]), "performance"),
            ReportFigure("mother2-qqq", "策略2：D0_F1_S1、三门控与资金构成", group_figure(block, MOTHER_IDS[1]), "performance"),
            ReportFigure("mother4-qqq", "策略4：精简累计仓位、三门控与资金构成", group_figure(block, MOTHER_IDS[2]), "performance"),
            ReportFigure("performance-qqq", "最终十线：九条固定资本门控与QQQ", final_gate_figure(block), "performance"),
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
        "backtest/scripts/run_stochrsi_true124_position_gates.py",
        "backtest/scripts/analyze_stochrsi_true124_position_gates.py",
        "backtest/scripts/finalize_stochrsi_true124_position_gates.py",
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
        f"# Run {args.run_id}\n\nCorrected QQQ StochRSI mother 1/2/4 and position-gate comparison; print report.pdf, then finalize.\n",
        encoding="utf-8",
    )
    print(f"Wrote {run_root / 'report.html'}; PDF printing and finalization remain pending")


if __name__ == "__main__":
    main()
