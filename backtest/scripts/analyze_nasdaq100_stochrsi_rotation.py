#!/usr/bin/env python3
"""Build the v5 report for the point-in-time Nasdaq-100 Strategy1 rotation."""

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
    "experiments/ROT/ROT-v0.50a.1__26-08-26__nasdaq100_stochrsi_strategy1_top20"
)
SYMBOL = "NASDAQ100_PIT_STRATEGY1_ROTATION"
CASE_IDS = (
    "RB01_FULL_EQUAL",
    "RB02_FULL_EQUAL",
    "RB03_FULL_EQUAL",
    "RB05_FULL_EQUAL",
    "RB01_BEAR9_FLOOR10",
    "RB02_BEAR9_FLOOR10",
    "RB03_BEAR9_FLOOR10",
    "RB05_BEAR9_FLOOR10",
)
CASE_LABELS = {
    "RB01_FULL_EQUAL": "1日 · 合格股全等权",
    "RB02_FULL_EQUAL": "2日 · 合格股全等权",
    "RB03_FULL_EQUAL": "3日 · 合格股全等权",
    "RB05_FULL_EQUAL": "5日 · 合格股全等权",
    "RB01_BEAR9_FLOOR10": "1日 · 不足10只用Bear9",
    "RB02_BEAR9_FLOOR10": "2日 · 不足10只用Bear9",
    "RB03_BEAR9_FLOOR10": "3日 · 不足10只用Bear9",
    "RB05_BEAR9_FLOOR10": "5日 · 不足10只用Bear9",
}
COLORS = {
    "RB01_FULL_EQUAL": "#93c5fd",
    "RB02_FULL_EQUAL": "#2563eb",
    "RB03_FULL_EQUAL": "#60a5fa",
    "RB05_FULL_EQUAL": "#1d4ed8",
    "RB01_BEAR9_FLOOR10": "#86efac",
    "RB02_BEAR9_FLOOR10": "#15803d",
    "RB03_BEAR9_FLOOR10": "#4ade80",
    "RB05_BEAR9_FLOOR10": "#166534",
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
        name = f"cost_{cost:g}bps"
        root = run_root / SYMBOL / name
        blocks[cost] = {
            "root": root,
            "metrics": pd.read_csv(root / "metrics.csv"),
            "metrics_json": json.loads((root / "metrics.json").read_text(encoding="utf-8")),
            "daily": pd.read_csv(root / "daily.csv.gz", parse_dates=["date"]),
            "benchmark": pd.read_csv(root / "qqq_buy_hold_daily.csv", parse_dates=["date"]),
        }
    return blocks


def market_figure(qqq: pd.DataFrame, selection: pd.DataFrame) -> go.Figure:
    view = qqq.merge(selection, on="date", how="left", validate="one_to_one")
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.07,
        row_heights=[0.68, 0.32],
        subplot_titles=("QQQ 拆股及股息调整价格", "当日严格超过90%的证券数与Top20入选数"),
    )
    figure.add_trace(
        go.Candlestick(
            x=view["date"],
            open=view["open"],
            high=view["high"],
            low=view["low"],
            close=view["close"],
            name="QQQ",
            increasing_line_color="#15803d",
            decreasing_line_color="#dc2626",
            meta={"series_key": "qqq_price", "panel": "price", "label": "QQQ"},
        ),
        row=1,
        col=1,
    )
    for column, label, color in (
        ("eligible_count", "合格证券数", "#7c3aed"),
        ("selected_count", "Top20入选数", "#0891b2"),
    ):
        figure.add_trace(
            go.Scatter(
                x=view["date"],
                y=view[column],
                name=label,
                line={"color": color, "width": 1.7},
                meta={
                    "series_key": column,
                    "panel": "market",
                    "label": label,
                    "control_group": "selection",
                    "control_group_label": "候选与入选数量",
                },
            ),
            row=2,
            col=1,
        )
    figure.add_hline(y=10, line_dash="dot", line_color="#64748b", row=2, col=1)
    figure.add_hline(y=20, line_dash="dot", line_color="#94a3b8", row=2, col=1)
    figure.update_layout(
        height=830,
        hovermode="x unified",
        showlegend=False,
        uirevision="ndx-strategy1-market-v1",
    )
    figure.update_yaxes(title_text="美元", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="只", fixedrange=False, row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def performance_figure(blocks: dict[float, dict[str, Any]]) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.68, 0.32],
        subplot_titles=("16条路径与QQQ净值", "从各自历史峰值回撤"),
    )
    for cost in sorted(blocks):
        daily = blocks[cost]["daily"]
        for case_id in CASE_IDS:
            frame = daily[daily["case_id"].eq(case_id)].sort_values("date")
            key = f"{case_id.lower()}_{cost:g}bps"
            visible = True if cost == 5 else "legendonly"
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
                        visible=visible,
                        line={
                            "color": COLORS[case_id],
                            "width": 2.1 if case_id == "RB02_BEAR9_FLOOR10" else 1.25,
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
        benchmark = blocks[cost]["benchmark"].sort_values("date")
        key = f"qqq_buy_hold_{cost:g}bps"
        for row, values, panel, showlegend in (
            (1, benchmark["equity"], "equity", True),
            (2, drawdown(benchmark["equity"]), "drawdown", False),
        ):
            figure.add_trace(
                go.Scatter(
                    x=benchmark["date"],
                    y=values,
                    name=f"QQQ Buy & Hold · {cost:g} bps",
                    showlegend=showlegend,
                    visible=True if cost == 5 else "legendonly",
                    line={"color": "#111827", "width": 2.8, "dash": "dash"},
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
        height=880,
        hovermode="x unified",
        showlegend=False,
        uirevision="ndx-strategy1-performance-v1",
    )
    figure.update_yaxes(title_text="10万美元账户净值", type="log", row=1, col=1)
    figure.update_yaxes(title_text="%", row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def exposure_figure(block: dict[str, Any]) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        subplot_titles=("2日调仓：合格股全等权", "2日调仓：不足10只用Bear9补位"),
    )
    for row_number, case_id in enumerate(
        ("RB02_FULL_EQUAL", "RB02_BEAR9_FLOOR10"), start=1
    ):
        frame = block["daily"][block["daily"]["case_id"].eq(case_id)].sort_values("date")
        for column, label, color in (
            ("member_exposure_pct", "历史成分股", "#2563eb"),
            ("bear9_exposure_pct", "Bear9", "#16a34a"),
            ("cash_pct", "现金", "#cbd5e1"),
        ):
            figure.add_trace(
                go.Scatter(
                    x=frame["date"],
                    y=frame[column],
                    name=f"{CASE_LABELS[case_id]} · {label}",
                    stackgroup=f"capital_{row_number}",
                    line={"color": color, "width": 0.8},
                    hovertemplate="%{x|%Y-%m-%d}<br>%{y:.2f}%<extra></extra>",
                ),
                row=row_number,
                col=1,
            )
        figure.update_yaxes(title_text="净值占比 %", range=[0, 101], row=row_number, col=1)
    figure.update_layout(height=760, hovermode="x unified", legend={"orientation": "h"})
    return figure


def sensitivity_figure(blocks: dict[float, dict[str, Any]]) -> go.Figure:
    figure = go.Figure()
    x = [CASE_LABELS[case_id] for case_id in CASE_IDS]
    for cost, color in ((0.0, "#94a3b8"), (5.0, "#0f766e")):
        metrics = blocks[cost]["metrics"].set_index("case_id")
        figure.add_trace(
            go.Bar(
                x=x,
                y=[metrics.at[case_id, "cagr_pct"] for case_id in CASE_IDS],
                name=f"{cost:g} bps",
                marker_color=color,
            )
        )
    qqq = float(blocks[5.0]["metrics_json"]["benchmark"]["cagr_pct"])
    figure.add_hline(
        y=qqq,
        line_dash="dash",
        line_color="#111827",
        annotation_text=f"QQQ 5 bps：{qqq:.2f}%",
    )
    figure.update_layout(
        barmode="group",
        height=560,
        yaxis_title="CAGR %",
        xaxis={"tickangle": -25},
        legend={"orientation": "h"},
    )
    return figure


def latest_ranking_figure(selected: pd.DataFrame) -> go.Figure:
    latest = selected["date"].max()
    frame = selected[selected["date"].eq(latest)].sort_values("rank")
    display = pd.DataFrame(
        {
            "名次": frame["rank"].astype(int),
            "Ticker": frame["display_ticker"],
            "策略1仓位分数": frame["strategy1_weight"].map(lambda value: f"{value:.2%}"),
            "稳定证券ID": frame["security_id"],
        }
    )
    figure = go.Figure(
        data=[
            go.Table(
                header={
                    "values": list(display.columns),
                    "fill_color": "#0f766e",
                    "font": {"color": "white"},
                    "align": "left",
                },
                cells={
                    "values": [display[column] for column in display.columns],
                    "fill_color": "#f8fafc",
                    "align": "left",
                    "height": 25,
                },
            )
        ]
    )
    figure.update_layout(height=630, margin={"l": 20, "r": 20, "t": 20, "b": 20})
    return figure


def metric_table(blocks: dict[float, dict[str, Any]]) -> str:
    rows: list[str] = []
    for cost in (0.0, 5.0):
        metrics = blocks[cost]["metrics"].set_index("case_id")
        for case_id in CASE_IDS:
            item = metrics.loc[case_id]
            rows.append(
                "<tr>"
                f"<td>{html.escape(CASE_LABELS[case_id])}</td><td>{cost:g}</td>"
                f"<td>{item['cagr_pct']:.2f}%</td><td>{item['sharpe']:.3f}</td>"
                f"<td>{item['max_drawdown_pct']:.2f}%</td>"
                f"<td>{item['average_member_exposure_pct']:.1f}%</td>"
                f"<td>{item['average_bear9_exposure_pct']:.1f}%</td>"
                f"<td>{item['turnover_multiple']:.1f}×</td>"
                f"<td>{int(item['order_count']):,}</td>"
                f"<td>{int(item['unavailable_target_deferral_count'])}</td></tr>"
            )
        benchmark = blocks[cost]["metrics_json"]["benchmark"]
        rows.append(
            "<tr><td>QQQ Buy & Hold</td>"
            f"<td>{cost:g}</td><td>{benchmark['cagr_pct']:.2f}%</td>"
            f"<td>{benchmark['sharpe']:.3f}</td>"
            f"<td>{benchmark['max_drawdown_pct']:.2f}%</td>"
            "<td>100.0%</td><td>0.0%</td>"
            f"<td>{benchmark['turnover_multiple']:.1f}×</td>"
            f"<td>{int(benchmark['order_count']):,}</td><td>0</td></tr>"
        )
    selection = blocks[5.0]["metrics_json"]["selection_summary"]
    return (
        '<div class="summary-grid">'
        '<div class="summary-card"><strong>数据状态</strong><span>pending_review 候选包；仅作探索，不是正式 ROT 结论</span></div>'
        '<div class="summary-card"><strong>完整矩阵</strong><span>4种调仓频率 × 2种资金分配 × 2种成本 = 16条</span></div>'
        f'<div class="summary-card"><strong>合格证券数</strong><span>均值 {selection["mean_eligible_count"]:.1f}；范围 {selection["minimum_eligible_count"]}–{selection["maximum_eligible_count"]}</span></div>'
        f'<div class="summary-card"><strong>不足10只</strong><span>{int(selection["days_below_ten"]):,} 个交易日，用于Bear9消融</span></div>'
        '</div><div class="table-wrap"><table><thead><tr>'
        '<th>路径</th><th>bps/边</th><th>CAGR</th><th>Sharpe</th><th>最大回撤</th>'
        '<th>成员股均仓</th><th>Bear9均仓</th><th>换手</th><th>订单</th><th>延后变更</th>'
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
    incomplete = [
        item["block_id"]
        for item in record["expected_blocks"]
        if item["status"] != "completed"
    ]
    if incomplete:
        raise RuntimeError(f"Incomplete blocks: {incomplete}")
    run_root = context.run_root(args.run_id)
    blocks = load_blocks(run_root)
    five = blocks[5.0]
    metrics = five["metrics"].set_index("case_id")
    best_case = str(metrics["cagr_pct"].idxmax())
    best = metrics.loc[best_case]
    benchmark = five["metrics_json"]["benchmark"]
    cagr_gap = float(best["cagr_pct"] - benchmark["cagr_pct"])
    sharpe_gap = float(best["sharpe"] - benchmark["sharpe"])
    drawdown_improvement = float(best["max_drawdown_pct"] - benchmark["max_drawdown_pct"])
    promotion_passed = bool(cagr_gap >= 0 and sharpe_gap > 0 and drawdown_improvement > 0)

    shared = run_root / "shared"
    selection = pd.read_csv(shared / "selection_daily.csv", parse_dates=["date"])
    selected = pd.read_csv(shared / "selected_rankings.csv.gz", parse_dates=["date"])
    qqq = pd.read_csv(WORKSPACE_ROOT / "data/processed/daily/QQQ.csv", parse_dates=["date"])
    start = pd.Timestamp(context.config["parameters"]["analysis_start"])
    end = pd.Timestamp(context.config["parameters"]["analysis_end"])
    qqq = qqq[qqq["date"].between(start, end)].copy()

    analysis_root = run_root / "analysis"
    analysis_root.mkdir(exist_ok=True)
    combined = []
    for cost in (0.0, 5.0):
        frame = blocks[cost]["metrics"].copy()
        frame.insert(1, "cost_bps", cost)
        combined.append(frame)
    results = pd.concat(combined, ignore_index=True)
    results.to_csv(analysis_root / "results_table.csv", index=False, lineterminator="\n")
    latest = selected[selected["date"].eq(selected["date"].max())].sort_values("rank")
    latest.to_csv(analysis_root / "latest_selected_rankings.csv", index=False, lineterminator="\n")
    summary = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "data_status": five["metrics_json"]["data_status"],
        "formal_path_count": 16,
        "primary_cost_bps": 5.0,
        "best_case_by_full_period_cagr": best_case,
        "best_case_metrics": json_safe(best.to_dict()),
        "qqq_buy_hold_metrics": json_safe(benchmark),
        "best_cagr_gap_vs_qqq_pct_points": cagr_gap,
        "best_sharpe_gap_vs_qqq": sharpe_gap,
        "best_drawdown_improvement_vs_qqq_pct_points": drawdown_improvement,
        "promotion_criteria_passed": promotion_passed,
        "selection_summary": five["metrics_json"]["selection_summary"],
        "latest_ranking_date": latest["date"].max().date().isoformat(),
        "latest_selected_count": int(len(latest)),
        "max_cross_check_differences": five["metrics_json"]["max_cross_check_differences"],
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    report_html = render_interactive_report(
        title="Nasdaq-100历史成分股 Strategy1 Top20 轮动",
        heading="策略1 >90%能否从历史成分股中挑出上涨最强的20只",
        subtitle=(
            f"1999–2026 · 候选数据探索 · 5 bps最佳：{CASE_LABELS[best_case]} "
            f"CAGR {best['cagr_pct']:.2f}% / QQQ {benchmark['cagr_pct']:.2f}%"
        ),
        summary_html=metric_table(blocks),
        notes=[
            "Nasdaq-100历史成分与个股行情包目前是pending_review：成员、行情和执行均已做时点约束，但提供方、许可、获取日期与证券身份仍未批准，因此不能称为正式或样本外结论。",
            "每只股票独立运行冻结的策略1；轮动账户只读取策略1收盘后股票仓位比例，不继承它的外部注资、现金或份额。",
            "成员资格滞后一XNYS交易日；调仓收盘确认排名，下一交易日Open完整再平衡。临时缺价时冻结该证券，终止上市代理价只允许卖出。",
            "Bear9只在合格股少于10只时补足十个10%槽位；其平均历史占仓约7.24%，没有改变大部分日期几乎满仓成员股的事实。",
            "5 bps最佳路径未跑赢QQQ的CAGR或Sharpe，只轻微改善最大回撤；按预声明标准不晋级。",
        ],
        figures=[
            ReportFigure(
                "market-nasdaq100_pit_strategy1_rotation",
                "QQQ与每天超过90%的候选数量",
                market_figure(qqq, selection),
                "market",
            ),
            ReportFigure(
                "performance-nasdaq100_pit_strategy1_rotation",
                "16条正式路径、QQQ与各自回撤",
                performance_figure(blocks),
                "performance",
            ),
            ReportFigure(
                "exposure-nasdaq100_pit_strategy1_rotation",
                "2日调仓两种资金分配的成员股、Bear9与现金",
                exposure_figure(five),
                "generic",
            ),
            ReportFigure(
                "sensitivity-nasdaq100_pit_strategy1_rotation",
                "调仓频率、Bear9与5 bps成本消融",
                sensitivity_figure(blocks),
                "generic",
            ),
            ReportFigure(
                "latest-nasdaq100_pit_strategy1_rotation",
                f"结束日Top20排名（{latest['date'].max().date()} Close）",
                latest_ranking_figure(selected),
                "generic",
            ),
        ],
        experiment=context.config,
        run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    (run_root / "report.html").write_text(report_html, encoding="utf-8")
    decision = (
        "达到晋级标准。" if promotion_passed else "未达到晋级标准；保留为候选数据上的否定性探索证据。"
    )
    (run_root / "report.md").write_text(
        f"""# Nasdaq-100历史成分股 Strategy1 Top20 轮动

## 结论

- 5 bps最佳路径：`{best_case}`（{CASE_LABELS[best_case]}），CAGR `{best['cagr_pct']:.3f}%`、Sharpe `{best['sharpe']:.3f}`、最大回撤 `{best['max_drawdown_pct']:.2f}%`。
- 同期QQQ Buy & Hold：CAGR `{benchmark['cagr_pct']:.3f}%`、Sharpe `{benchmark['sharpe']:.3f}`、最大回撤 `{benchmark['max_drawdown_pct']:.2f}%`。
- 最佳路径相对QQQ：CAGR `{cagr_gap:+.3f}`个百分点、Sharpe `{sharpe_gap:+.3f}`、最大回撤改善 `{drawdown_improvement:+.3f}`个百分点。
- 判定：{decision}

## 研究边界

历史成分与个股行情来自 `pending_review` 候选包。本回测使用一交易日滞后的成员状态、收盘确认与次日Open成交，并完整报告4种调仓频率、2种资金分配及0/5 bps共16条路径；在数据来源与许可完成批准前，结果不能升级为正式ROT结论。
""",
        encoding="utf-8",
    )

    tracked = [
        "backtest/quantkit/nasdaq100_stochrsi_rotation.py",
        "backtest/quantkit/execution.py",
        "backtest/quantkit/trend_score_portfolio.py",
        "backtest/quantkit/experiment.py",
        "backtest/quantkit/reporting.py",
        "backtest/scripts/run_nasdaq100_stochrsi_rotation.py",
        "backtest/scripts/analyze_nasdaq100_stochrsi_rotation.py",
        "backtest/scripts/finalize_nasdaq100_stochrsi_rotation.py",
        "backtest/scripts/validate_run.py",
        "backtest/tests/strategies/rot/test_nasdaq100_stochrsi_rotation.py",
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
        provenance["source_files"][relative] = {
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
    shared_manifest = json.loads((shared / "manifest.json").read_text(encoding="utf-8"))
    for relative, expected_hash in shared_manifest["source_files"].items():
        path = WORKSPACE_ROOT / relative
        actual_hash = sha256(path)
        if actual_hash != expected_hash:
            raise AssertionError(f"Shared source changed during analysis: {relative}")
        provenance["source_files"][relative] = {
            "bytes": path.stat().st_size,
            "sha256": actual_hash,
        }
    (run_root / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (run_root / "README.md").write_text(
        f"""# Run {args.run_id}

Candidate-data point-in-time Nasdaq-100 Strategy1 rotation study.

- `report.html` / `report.pdf`: interactive and printable v5 reports.
- `analysis/`: 16-path table, latest ranking and machine-readable conclusion.
- `{SYMBOL}/`: 0/5 bps PyBroker and independent-ledger artifacts.
- `shared/`: frozen scores, rankings, target weights, prices and source hashes.
""",
        encoding="utf-8",
    )
    print(
        f"Wrote {run_root / 'report.html'}; best={best_case}; "
        f"CAGR gap={cagr_gap:+.3f}pp; promotion={promotion_passed}"
    )


if __name__ == "__main__":
    main()
