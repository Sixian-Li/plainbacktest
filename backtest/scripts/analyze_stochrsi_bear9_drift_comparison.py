#!/usr/bin/env python3
"""Render the five-path StochRSI/Bear9 drift comparison report."""

from __future__ import annotations

import argparse
import html
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import plotly
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from quantkit.experiment import load_experiment, load_run, sha256
from quantkit.paths import BACKTEST_ROOT
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts.run_qqq_flat_trio_substitution import json_safe


WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/TIM/"
    "TIM-v0.70c.1__26-08-26__qqq_stochrsi_bear9_drift_comparison"
)
FORMAL_SYMBOL = "QQQ_STOCHRSI_BEAR9"
FORMAL_COST_BPS = 5.0
LABELS = {
    "QQQ_BUY_HOLD": "QQQ Buy & Hold",
    "STOCH_12_61_CASH": "StochRSI 12/61＋现金",
    "STOCH_62_111_CASH": "StochRSI 62/111＋现金",
    "STOCH_12_61_BEAR9_DRIFT3": "StochRSI 12/61＋Bear9",
    "STOCH_62_111_BEAR9_DRIFT3": "StochRSI 62/111＋Bear9",
}
COLORS = {
    "QQQ_BUY_HOLD": "#64748b",
    "STOCH_12_61_CASH": "#f59e0b",
    "STOCH_62_111_CASH": "#2563eb",
    "STOCH_12_61_BEAR9_DRIFT3": "#dc2626",
    "STOCH_62_111_BEAR9_DRIFT3": "#059669",
}


def load_block(context: Any, run_id: str) -> dict[str, Any]:
    root = context.run_root(run_id) / FORMAL_SYMBOL / "cost_5bps"
    block = {
        "root": root,
        "metrics": pd.read_csv(root / "metrics.csv"),
        "daily": pd.read_csv(root / "daily.csv", parse_dates=["date"]),
        "orders": pd.read_csv(root / "orders.csv", parse_dates=["date", "signal_date"]),
        "schedule": pd.read_csv(root / "target_weight_schedule.csv", parse_dates=["date", "execution_date"]),
        "state_12_61": pd.read_csv(root / "timing_state_12_61.csv", parse_dates=["date"]),
        "state_62_111": pd.read_csv(root / "timing_state_62_111.csv", parse_dates=["date"]),
        "manifest": json.loads((root / "manifest.json").read_text(encoding="utf-8")),
        "metrics_json": json.loads((root / "metrics.json").read_text(encoding="utf-8")),
    }
    return block


def _monthly(frame: pd.DataFrame) -> pd.DataFrame:
    ordered = frame.sort_values("date").copy()
    return ordered.groupby(ordered["date"].dt.to_period("M"), sort=True).tail(1)


def _drawdown(equity: pd.Series) -> pd.Series:
    values = pd.to_numeric(equity, errors="raise").astype(float)
    return (values / values.cummax() - 1.0) * 100.0


def performance_figure(daily: pd.DataFrame) -> go.Figure:
    figure = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.72, 0.28])
    for case_id, label in LABELS.items():
        selected = _monthly(daily[daily["case_id"] == case_id])
        dash = "dot" if case_id == "QQQ_BUY_HOLD" or case_id.endswith("CASH") else "solid"
        for row, values, panel in (
            (1, selected["equity"], "equity"),
            (2, _drawdown(selected["equity"]), "drawdown"),
        ):
            figure.add_trace(
                go.Scatter(
                    x=selected["date"],
                    y=values,
                    name=label,
                    showlegend=row == 1,
                    line={"color": COLORS[case_id], "width": 2.0, "dash": dash},
                    meta={
                        "series_key": case_id.lower(),
                        "label": label,
                        "panel": panel,
                        "is_benchmark": case_id == "QQQ_BUY_HOLD",
                    },
                ),
                row=row,
                col=1,
            )
    figure.update_yaxes(title_text="账户净值 / 美元", row=1, col=1)
    figure.update_yaxes(title_text="回撤 %", row=2, col=1)
    figure.update_layout(height=760, hovermode="x unified")
    return figure


def state_figure(block: dict[str, Any]) -> go.Figure:
    figure = make_subplots(rows=3, cols=1, shared_xaxes=True, row_heights=[0.5, 0.25, 0.25])
    daily = block["daily"]
    benchmark = daily[daily["case_id"] == "QQQ_BUY_HOLD"].copy()
    benchmark["qqq_close_proxy"] = benchmark["equity"] / benchmark["equity"].iloc[1] * 100.0
    figure.add_trace(
        go.Scatter(
            x=benchmark["date"], y=benchmark["qqq_close_proxy"], name="QQQ归一化价格",
            line={"color": "#111827", "width": 1.5},
            meta={"series_key": "qqq_price", "label": "QQQ归一化价格", "panel": "price"},
        ), row=1, col=1
    )
    for row, state, periods, colors in (
        (2, block["state_12_61"], (12, 61), ("#f59e0b", "#dc2626")),
        (3, block["state_62_111"], (62, 111), ("#2563eb", "#059669")),
    ):
        for period, color in zip(periods, colors, strict=True):
            figure.add_trace(
                go.Scatter(
                    x=state["date"], y=state[f"stochrsi_{period}"], name=f"StochRSI {period}",
                    line={"color": color, "width": 1.1},
                    meta={"series_key": f"stochrsi_{period}", "label": f"StochRSI {period}", "panel": "market"},
                ), row=row, col=1
            )
        for level in (0.2, 0.8):
            figure.add_hline(y=level, line={"color": "#94a3b8", "dash": "dot", "width": 1}, row=row, col=1)
    figure.update_yaxes(title_text="归一化价格", row=1, col=1)
    figure.update_yaxes(title_text="12 / 61", range=[-0.05, 1.05], row=2, col=1)
    figure.update_yaxes(title_text="62 / 111", range=[-0.05, 1.05], row=3, col=1)
    figure.update_layout(height=820, hovermode="x unified")
    return figure


def summary_html(metrics: pd.DataFrame, weights: dict[str, float]) -> str:
    rows = []
    for item in metrics.itertuples(index=False):
        rows.append(
            "<tr>"
            f"<td>{html.escape(LABELS[item.case_id])}</td>"
            f"<td>${item.final_equity:,.0f}</td>"
            f"<td>{item.cagr_pct:.2f}%</td>"
            f"<td>{item.sharpe:.3f}</td>"
            f"<td>{item.max_drawdown_pct:.2f}%</td>"
            f"<td>{item.turnover_multiple:.1f}×</td>"
            f"<td>{int(item.drift_rebalance_count)}</td>"
            "</tr>"
        )
    best = metrics.loc[metrics["final_equity"].idxmax()]
    weight_rows = "".join(
        f"<tr><td>{html.escape(symbol)}</td><td>{weight:.0%}</td></tr>"
        for symbol, weight in weights.items()
    )
    return (
        '<section class="summary-block">'
        '<div class="result-cards">'
        f'<div class="result-card"><strong>期末净值最高</strong><span>{html.escape(LABELS[best.case_id])}</span><small>${best.final_equity:,.0f}</small></div>'
        f'<div class="result-card"><strong>最高CAGR</strong><span>{best.cagr_pct:.2f}%</span><small>QQQ Buy & Hold为{metrics.iloc[0].cagr_pct:.2f}%</small></div>'
        f'<div class="result-card"><strong>最高Sharpe</strong><span>{metrics.sharpe.max():.3f}</span><small>五条路径统一5bps</small></div>'
        '<div class="result-card"><strong>研究结论边界</strong><span>描述性结果</span><small>Bear9成员和权重有事后选择</small></div>'
        '</div>'
        '<h3>五条路径完整历史指标</h3>'
        '<div class="event-scroll"><table class="event-table"><thead><tr><th>路径</th><th>期末净值</th><th>CAGR</th><th>Sharpe</th><th>最大回撤</th><th>换手</th><th>漂移再平衡</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
        '<details><summary>固定Bear9权重</summary><div class="event-scroll"><table class="event-table"><thead><tr><th>标的</th><th>目标权重</th></tr></thead>'
        f'<tbody>{weight_rows}</tbody></table></div><p>DG上市前9%留现金，不分摊给其他成员。</p></details>'
        '<p><strong>解释：</strong>62/111＋Bear9在这段完整历史上最强，但这不是样本外结论；12/61＋Bear9的改善也主要说明空仓资产选择对结果影响非常大。</p>'
        '</section>'
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    record = load_run(context, args.run_id)
    if record.get("status") not in {"running", "completed_unvalidated"}:
        raise RuntimeError("analysis requires a running or completed_unvalidated run")
    if len(record.get("expected_blocks", [])) != 1 or record["expected_blocks"][0].get("status") != "completed":
        raise RuntimeError("the formal 5bps block must be complete")
    block = load_block(context, args.run_id)
    metrics = block["metrics"]
    if metrics["case_id"].tolist() != list(LABELS):
        raise AssertionError("five-path identity or order changed")

    run_root = context.run_root(args.run_id)
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(exist_ok=True)
    metrics.to_csv(analysis_root / "five_path_metrics.csv", index=False, lineterminator="\n")
    paired = metrics[metrics["flat_asset"] == "BEAR9"][
        ["case_id", "timing_id", "delta_vs_cash_cagr_pct", "delta_vs_cash_sharpe", "delta_vs_cash_max_drawdown_pct", "delta_vs_cash_turnover_multiple"]
    ].copy()
    paired.to_csv(analysis_root / "bear9_delta_vs_cash.csv", index=False, lineterminator="\n")
    summary = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "formal_cost_bps": FORMAL_COST_BPS,
        "five_path_metrics": json_safe(metrics.to_dict("records")),
        "bear9_delta_vs_cash": json_safe(paired.to_dict("records")),
        "promotion_allowed": False,
        "promotion_blocker": "Bear9 members and weights were selected after full-history review",
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )

    figures = [
        ReportFigure(
            "performance-qqq_stochrsi_bear9",
            "五条路径：账户净值与回撤",
            performance_figure(block["daily"]),
            "performance",
        ),
        ReportFigure(
            "market-qqq_stochrsi_bear9",
            "QQQ与两组StochRSI状态",
            state_figure(block),
            "market",
        ),
    ]
    notes = [
        "两条择时路径都从2000-05-30的空仓状态开始，不继承区间前的持仓；只有在第一次双周期同时上穿0.20后才持有QQQ。",
        "本实验使用完成Close确认、下一共同Open成交，与早期日内理论触发价版本不同；这里不能把结果混称为原始精确触发策略。",
        "Bear9版本在QQQ空仓时持有AZO、SO、ED、ORLY、MO、WRB、DLTR、DG和WMT；任一可交易成员绝对总账户权重偏离目标严格超过3个百分点时才恢复全篮子目标。",
        "DG上市前9%保留现金；卖出先于买入、允许小数股、不融资、现金不计息，所有路径统一单边5bps冲击成本。",
        "Bear9成员和权重来自历史复核，因此14.12%的完整历史CAGR只能作为描述性发现，不能当作样本外可实现收益。",
    ]
    report = render_interactive_report(
        title="QQQ StochRSI 12/61、62/111与Bear9空仓替代",
        heading="同一择时信号，空仓留现金还是持有Bear9？",
        subtitle="2000-05-30至2026-08-04；五条净值线，完成Close确认、下一共同Open成交，单边5bps。",
        summary_html=summary_html(metrics, context.config["parameters"]["bear9_weights"]),
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
        '<a download href="analysis/five_path_metrics.csv">五路径指标</a> · '
        '<a download href="analysis/bear9_delta_vs_cash.csv">Bear9相对现金</a> · '
        '<a download href="QQQ_STOCHRSI_BEAR9/cost_5bps/orders.csv">完整成交</a> · '
        '<a download href="QQQ_STOCHRSI_BEAR9/cost_5bps/target_weight_schedule.csv">目标与再平衡</a>'
        '</p></section>'
    )
    report = report.replace("</main>", downloads + "</main>")
    (run_root / "report.html").write_text(report, encoding="utf-8")

    best = metrics.loc[metrics["final_equity"].idxmax()]
    lines = [
        "# QQQ StochRSI与Bear9空仓替代",
        "",
        "## 五路径结果",
        "",
    ]
    for item in metrics.itertuples(index=False):
        lines.append(
            f"- {LABELS[item.case_id]}：期末${item.final_equity:,.0f}，CAGR {item.cagr_pct:.2f}%，Sharpe {item.sharpe:.3f}，最大回撤 {item.max_drawdown_pct:.2f}%。"
        )
    lines.extend(
        [
            "",
            "## 结论边界",
            "",
            f"- 完整历史最高为{LABELS[best.case_id]}，但Bear9成员和权重来自事后研究，不能作为样本外晋级证据。",
            "- 这次是收盘确认、下一开盘成交，不是早期日内理论触发价语义。",
        ]
    )
    (run_root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    tracked = [
        "backtest/quantkit/stochrsi_bear9_drift.py",
        "backtest/quantkit/dual_stochrsi_timing.py",
        "backtest/quantkit/trend_score_portfolio.py",
        "backtest/quantkit/execution.py",
        "backtest/quantkit/reporting.py",
        "backtest/scripts/run_stochrsi_bear9_drift_comparison.py",
        "backtest/scripts/analyze_stochrsi_bear9_drift_comparison.py",
        "backtest/scripts/finalize_stochrsi_bear9_drift_comparison.py",
        "backtest/tests/strategies/tim/test_stochrsi_bear9_drift.py",
        "backtest/experiments/TIM/TIM-v0.70c.1__26-08-26__qqq_stochrsi_bear9_drift_comparison/experiment.json",
        "backtest/requirements.lock",
        "backtest/report_templates/interactive_research_v5/page.html",
        "backtest/report_templates/interactive_research_v5/styles.css",
        "backtest/report_templates/interactive_research_v5/interactions.js",
    ]
    provenance: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "software": {"python": platform.python_version(), "lib_pybroker": "1.2.12", "plotly": plotly.__version__},
        "source_files": {},
    }
    for relative in tracked:
        path = WORKSPACE_ROOT / relative
        provenance["source_files"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    for relative in block["manifest"]["source_files"]:
        path = WORKSPACE_ROOT / relative
        provenance["source_files"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    (run_root / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    (run_root / "README.md").write_text(
        f"# Run {args.run_id}\n\nFive-path QQQ StochRSI and Bear9 drift comparison. Open report.html for the formal v5 report.\n",
        encoding="utf-8",
    )
    print(f"Wrote {run_root / 'report.html'} with {len(figures)} interactive figures")


if __name__ == "__main__":
    main()
