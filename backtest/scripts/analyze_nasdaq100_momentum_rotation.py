#!/usr/bin/env python3
"""Analyze and render the Nasdaq-100 12-1 momentum buffer experiment."""

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

from quantkit.experiment import block_root, load_experiment, load_run, sha256
from quantkit.nasdaq100_momentum_rotation import FORMAL_CASES
from quantkit.paths import BACKTEST_ROOT
from quantkit.reporting import ReportFigure, render_interactive_report


WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/ROT/ROT-v0.50b.1__26-08-29__nasdaq100_12_1_momentum_top10_top20_buffer"
)
FORMAL_SYMBOL = "NASDAQ100_MOMENTUM_ROTATION"
PRIMARY_COST = 5.0
CASE_LABELS = {
    "PIT_EQUAL_WEIGHT": "点时成分等权",
    "TOP10_MONTHLY_REPLACE": "每月强制Top10",
    "TOP10_EXIT20_BUFFER": "进Top10／跌出Top20",
}
CASE_COLORS = {
    "PIT_EQUAL_WEIGHT": "#64748b",
    "TOP10_MONTHLY_REPLACE": "#2563eb",
    "TOP10_EXIT20_BUFFER": "#16a34a",
}


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def read_csv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    for column in frame.columns:
        if column == "date" or column.endswith("_date"):
            frame[column] = pd.to_datetime(frame[column], errors="coerce")
    return frame


def load_block(context: Any, run_id: str, cost: float) -> dict[str, Any]:
    root = block_root(context, run_id, FORMAL_SYMBOL, cost)
    if not (root / "manifest.json").is_file():
        raise FileNotFoundError(f"missing completed block: {root}")
    return {
        "root": root,
        "metrics": read_csv(root / "metrics.csv"),
        "daily": read_csv(root / "daily.csv.gz"),
        "orders": read_csv(root / "orders.csv.gz"),
        "qqq": read_csv(root / "qqq_buy_hold_daily.csv"),
        "metrics_json": json.loads((root / "metrics.json").read_text(encoding="utf-8")),
        "manifest": json.loads((root / "manifest.json").read_text(encoding="utf-8")),
    }


def _metrics_lookup(block: dict[str, Any], case_id: str) -> dict[str, Any]:
    row = block["metrics"][block["metrics"]["case_id"] == case_id]
    if len(row) != 1:
        raise ValueError(f"missing metric row: {case_id}")
    return row.iloc[0].to_dict()


def mechanism_test(primary: dict[str, Any]) -> dict[str, Any]:
    forced = _metrics_lookup(primary, "TOP10_MONTHLY_REPLACE")
    buffered = _metrics_lookup(primary, "TOP10_EXIT20_BUFFER")
    turnover_reduction = 1.0 - float(buffered["annualized_turnover_multiple"]) / float(
        forced["annualized_turnover_multiple"]
    )
    cagr_delta = float(buffered["cagr_pct"]) - float(forced["cagr_pct"])
    sharpe_delta = float(buffered["sharpe"]) - float(forced["sharpe"])
    drawdown_delta = float(buffered["max_drawdown_pct"]) - float(forced["max_drawdown_pct"])
    gates = {
        "turnover_reduction_at_least_20pct": turnover_reduction >= 0.20,
        "cagr_loss_no_more_than_1pp": cagr_delta >= -1.0,
        "sharpe_loss_no_more_than_0_05": sharpe_delta >= -0.05,
        "max_drawdown_worsening_no_more_than_3pp": drawdown_delta >= -3.0,
    }
    return {
        "turnover_reduction_pct": turnover_reduction * 100.0,
        "cagr_delta_pct_points": cagr_delta,
        "sharpe_delta": sharpe_delta,
        "max_drawdown_delta_pct_points": drawdown_delta,
        "gates": gates,
        "all_mechanism_gates_passed": all(gates.values()),
    }


def _normalized_series(frame: pd.DataFrame) -> pd.Series:
    values = frame.sort_values("date").set_index("date")["equity"].astype(float)
    return values / float(values.iloc[0]) * 100.0


def performance_figure(block: dict[str, Any]) -> go.Figure:
    figure = go.Figure()
    for case_id in FORMAL_CASES:
        selected = block["daily"][block["daily"]["case_id"] == case_id]
        series = _normalized_series(selected)
        figure.add_trace(
            go.Scatter(
                x=series.index,
                y=series.values,
                name=CASE_LABELS[case_id],
                line={"color": CASE_COLORS[case_id], "width": 2.5 if case_id == "TOP10_EXIT20_BUFFER" else 1.8},
                meta={
                    "series_key": case_id.lower(),
                    "label": CASE_LABELS[case_id],
                    "panel": "equity",
                    "cost_bps": PRIMARY_COST,
                },
            )
        )
    qqq = _normalized_series(block["qqq"])
    figure.add_trace(
        go.Scatter(
            x=qqq.index,
            y=qqq.values,
            name="QQQ Buy & Hold",
            line={"color": "#111827", "width": 1.7, "dash": "dash"},
            meta={
                "series_key": "qqq_buy_hold",
                "label": "QQQ Buy & Hold",
                "panel": "equity",
                "is_benchmark": True,
                "cost_bps": PRIMARY_COST,
            },
        )
    )
    figure.update_layout(
        template="plotly_white",
        height=540,
        hovermode="x unified",
        yaxis={"title": "同起点净值（100，对数）", "type": "log"},
        xaxis={"title": "日期", "rangeslider": {"visible": False}},
        legend={"orientation": "h", "y": 1.12},
        margin={"l": 75, "r": 25, "t": 55, "b": 55},
    )
    return figure


def drawdown_figure(block: dict[str, Any]) -> go.Figure:
    figure = go.Figure()
    for case_id in FORMAL_CASES:
        selected = block["daily"][block["daily"]["case_id"] == case_id]
        equity = selected.sort_values("date").set_index("date")["equity"].astype(float)
        drawdown = (equity / equity.cummax() - 1.0) * 100.0
        figure.add_trace(
            go.Scatter(
                x=drawdown.index,
                y=drawdown.values,
                name=CASE_LABELS[case_id],
                line={"color": CASE_COLORS[case_id], "width": 2.3 if case_id == "TOP10_EXIT20_BUFFER" else 1.5},
                meta={"series_key": f"dd_{case_id.lower()}", "label": CASE_LABELS[case_id]},
            )
        )
    qqq = block["qqq"].sort_values("date").set_index("date")["equity"].astype(float)
    qqq_dd = (qqq / qqq.cummax() - 1.0) * 100.0
    figure.add_trace(
        go.Scatter(
            x=qqq_dd.index,
            y=qqq_dd.values,
            name="QQQ Buy & Hold",
            line={"color": "#111827", "width": 1.4, "dash": "dash"},
            meta={"series_key": "dd_qqq", "label": "QQQ Buy & Hold"},
        )
    )
    figure.update_layout(
        template="plotly_white",
        height=450,
        hovermode="x unified",
        yaxis={"title": "从历史高点回撤", "ticksuffix": "%"},
        xaxis={"title": "日期"},
        legend={"orientation": "h", "y": 1.12},
        margin={"l": 70, "r": 25, "t": 50, "b": 55},
    )
    return figure


def rolling_figure(block: dict[str, Any], window: int = 1260) -> tuple[go.Figure, pd.DataFrame]:
    rows: list[pd.DataFrame] = []
    figure = go.Figure()
    inputs: list[tuple[str, str, str, pd.DataFrame]] = [
        (case_id, CASE_LABELS[case_id], CASE_COLORS[case_id], block["daily"][block["daily"]["case_id"] == case_id])
        for case_id in FORMAL_CASES
    ]
    inputs.append(("QQQ_BUY_HOLD", "QQQ Buy & Hold", "#111827", block["qqq"]))
    for case_id, label, color, frame in inputs:
        equity = frame.sort_values("date").set_index("date")["equity"].astype(float)
        rolling = (equity / equity.shift(window)) ** (252.0 / window) - 1.0
        item = rolling.dropna().rename("rolling_5y_cagr").reset_index()
        item.insert(0, "case_id", case_id)
        rows.append(item)
        figure.add_trace(
            go.Scatter(
                x=item["date"],
                y=item["rolling_5y_cagr"] * 100.0,
                name=label,
                line={"color": color, "width": 2.2 if case_id == "TOP10_EXIT20_BUFFER" else 1.4, "dash": "dash" if case_id == "QQQ_BUY_HOLD" else "solid"},
                meta={"series_key": f"rolling_{case_id.lower()}", "label": label},
            )
        )
    figure.add_hline(y=0, line={"color": "#94a3b8", "width": 1})
    figure.update_layout(
        template="plotly_white",
        height=460,
        hovermode="x unified",
        yaxis={"title": "滚动5年 CAGR", "ticksuffix": "%"},
        xaxis={"title": "窗口结束日"},
        legend={"orientation": "h", "y": 1.12},
        margin={"l": 70, "r": 25, "t": 50, "b": 55},
    )
    return figure, pd.concat(rows, ignore_index=True)


def holdings_figure(block: dict[str, Any]) -> go.Figure:
    figure = go.Figure()
    for case_id in ("TOP10_MONTHLY_REPLACE", "TOP10_EXIT20_BUFFER"):
        selected = block["daily"][block["daily"]["case_id"] == case_id]
        figure.add_trace(
            go.Scatter(
                x=selected["date"],
                y=selected["holdings_count"],
                name=CASE_LABELS[case_id],
                line={"color": CASE_COLORS[case_id], "width": 1.8},
                meta={"series_key": f"holdings_{case_id.lower()}", "label": CASE_LABELS[case_id]},
            )
        )
    figure.update_layout(
        template="plotly_white",
        height=390,
        hovermode="x unified",
        yaxis={"title": "实际持仓只数", "range": [0, 21], "dtick": 2},
        xaxis={"title": "日期"},
        legend={"orientation": "h", "y": 1.12},
        margin={"l": 65, "r": 25, "t": 45, "b": 55},
    )
    return figure


def friction_figure(block_zero: dict[str, Any], block_five: dict[str, Any]) -> go.Figure:
    labels = [CASE_LABELS[case_id] for case_id in FORMAL_CASES]
    zero = [_metrics_lookup(block_zero, case_id)["cagr_pct"] for case_id in FORMAL_CASES]
    five = [_metrics_lookup(block_five, case_id)["cagr_pct"] for case_id in FORMAL_CASES]
    figure = go.Figure()
    figure.add_trace(go.Bar(x=labels, y=zero, name="0 bps", marker_color="#94a3b8"))
    figure.add_trace(go.Bar(x=labels, y=five, name="5 bps", marker_color="#2563eb"))
    figure.update_layout(
        template="plotly_white",
        height=390,
        barmode="group",
        yaxis={"title": "CAGR", "ticksuffix": "%"},
        xaxis={"title": "组合路径"},
        legend={"orientation": "h", "y": 1.12},
        margin={"l": 65, "r": 25, "t": 45, "b": 85},
    )
    return figure


def metric_table(block: dict[str, Any]) -> str:
    rows = []
    for case_id in FORMAL_CASES:
        item = _metrics_lookup(block, case_id)
        rows.append(
            "<tr>"
            f"<td>{html.escape(CASE_LABELS[case_id])}</td>"
            f"<td>{float(item['cagr_pct']):.2f}%</td>"
            f"<td>{float(item['sharpe']):.3f}</td>"
            f"<td>{float(item['max_drawdown_pct']):.2f}%</td>"
            f"<td>{float(item['annualized_turnover_multiple']):.2f}×</td>"
            f"<td>{float(item['average_holdings_count']):.1f}</td>"
            f"<td>{int(item['order_count']):,}</td>"
            "</tr>"
        )
    benchmark = block["metrics_json"]["benchmark"]
    rows.append(
        "<tr>"
        "<td>QQQ Buy & Hold</td>"
        f"<td>{float(benchmark['cagr_pct']):.2f}%</td>"
        f"<td>{float(benchmark['sharpe']):.3f}</td>"
        f"<td>{float(benchmark['max_drawdown_pct']):.2f}%</td>"
        f"<td>{float(benchmark['annualized_turnover_multiple']):.2f}×</td>"
        "<td>1.0</td>"
        f"<td>{int(benchmark['order_count']):,}</td>"
        "</tr>"
    )
    return (
        '<div class="table-wrap"><table><thead><tr>'
        "<th>5 bps路径</th><th>CAGR</th><th>Sharpe</th><th>最大回撤</th>"
        "<th>年化换手</th><th>平均持仓</th><th>订单</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    record = load_run(context, args.run_id)
    if record.get("status") != "running":
        raise RuntimeError("analysis requires a running run")
    if any(item.get("status") != "completed" for item in record.get("expected_blocks", [])):
        raise RuntimeError("all formal blocks must finish before analysis")
    run_root = context.run_root(args.run_id)
    block_zero = load_block(context, args.run_id, 0.0)
    block_five = load_block(context, args.run_id, PRIMARY_COST)
    mechanism = mechanism_test(block_five)
    primary = _metrics_lookup(block_five, "TOP10_EXIT20_BUFFER")
    forced = _metrics_lookup(block_five, "TOP10_MONTHLY_REPLACE")
    point_in_time = _metrics_lookup(block_five, "PIT_EQUAL_WEIGHT")
    qqq = block_five["metrics_json"]["benchmark"]
    rolling_chart, rolling = rolling_figure(block_five)

    analysis_root = run_root / "analysis"
    analysis_root.mkdir(parents=True, exist_ok=True)
    all_metrics = pd.concat(
        [
            block_zero["metrics"].assign(cost_bps=0.0),
            block_five["metrics"].assign(cost_bps=5.0),
        ],
        ignore_index=True,
    )
    all_metrics.to_csv(analysis_root / "case_comparison.csv", index=False, lineterminator="\n")
    rolling.to_csv(analysis_root / "rolling_5y_cagr.csv", index=False, lineterminator="\n")

    summary = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "data_status": "candidate_pending_review",
        "primary_metrics": {"symbol": FORMAL_SYMBOL, "cost_bps": 5.0, **primary},
        "forced_top10_metrics": forced,
        "point_in_time_equal_weight_metrics": point_in_time,
        "qqq_buy_hold_metrics": qqq,
        "mechanism_test": mechanism,
        "cost_case_metrics": all_metrics.to_dict("records"),
    }
    write_json(analysis_root / "summary.json", summary)

    passed_text = "通过" if mechanism["all_mechanism_gates_passed"] else "未通过"
    summary_html = (
        '<section class="summary-callout">'
        f"<p><strong>缓冲机制门槛：{passed_text}。</strong>"
        f"相对强制Top10，年化换手减少 {mechanism['turnover_reduction_pct']:.1f}%，"
        f"CAGR差 {mechanism['cagr_delta_pct_points']:+.2f} 个百分点，"
        f"Sharpe差 {mechanism['sharpe_delta']:+.3f}，"
        f"最大回撤差 {mechanism['max_drawdown_delta_pct_points']:+.2f} 个百分点。</p>"
        "<p><strong>证据边界：</strong>Nasdaq-100历史成分仍是 pending_review 候选数据；"
        "本页只能比较机制，不能把全历史表现称为样本外、approved或可直接交易结论。</p>"
        "</section>"
        + metric_table(block_five)
    )
    notes = [
        "12-1严格使用上个月末和再往前12个月末的真实Close，信号月收益完全不参与排名。",
        "成分资格滞后一个XNYS交易日；新成分可用入选前历史预热，但不能提前交易。",
        "所有目标股数在信号Close确定，下一Open只负责成交；没有用未来Open选择或放大赢家。",
        "缺失真实Open时不在前值合成价买入；历史证券无后续报价时只允许卖出型终止清算代理，并在指标中计数。",
        "全历史同时承担提出和观察机制的作用；下一步若保留该结构，应另建时间顺序或封存期实验。",
    ]
    figures = [
        ReportFigure(
            div_id="performance-nasdaq100_momentum_rotation",
            title="同起点净值：5 bps",
            figure=performance_figure(block_five),
            kind="performance",
        ),
        ReportFigure(
            div_id="drawdown-nasdaq100-momentum",
            title="历史回撤路径",
            figure=drawdown_figure(block_five),
            kind="static",
        ),
        ReportFigure(
            div_id="rolling-five-year-nasdaq100-momentum",
            title="滚动5年 CAGR",
            figure=rolling_chart,
            kind="static",
        ),
        ReportFigure(
            div_id="holdings-nasdaq100-momentum",
            title="Top10与缓冲组合的实际持仓只数",
            figure=holdings_figure(block_five),
            kind="static",
        ),
        ReportFigure(
            div_id="friction-nasdaq100-momentum",
            title="0／5 bps 成本敏感性",
            figure=friction_figure(block_zero, block_five),
            kind="static",
        ),
    ]
    report_html = render_interactive_report(
        title="Nasdaq-100 12-1动量 Top10/Top20 缓冲基线",
        heading="Nasdaq-100 12-1动量：换手缓冲是否值得",
        subtitle="1999-03-31信号至2026-08-04 · 点时成分候选数据 · 0/5 bps",
        summary_html=summary_html,
        notes=notes,
        figures=figures,
        experiment=context.config,
        run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    (run_root / "report.html").write_text(report_html, encoding="utf-8")

    report_md = f"""# Nasdaq-100 12-1动量 Top10/Top20 缓冲基线

- 数据状态：`candidate_pending_review`，不是 approved 数据。
- 样本：1999-03-31 收盘信号至 2026-08-04；首笔可成交日 1999-04-01。
- 主路径（5 bps）：CAGR {float(primary['cagr_pct']):.3f}%，Sharpe {float(primary['sharpe']):.3f}，最大回撤 {float(primary['max_drawdown_pct']):.3f}%。
- 强制Top10（5 bps）：CAGR {float(forced['cagr_pct']):.3f}%，Sharpe {float(forced['sharpe']):.3f}，最大回撤 {float(forced['max_drawdown_pct']):.3f}%。
- 点时等权（5 bps）：CAGR {float(point_in_time['cagr_pct']):.3f}%，Sharpe {float(point_in_time['sharpe']):.3f}，最大回撤 {float(point_in_time['max_drawdown_pct']):.3f}%。
- QQQ持有（5 bps）：CAGR {float(qqq['cagr_pct']):.3f}%，Sharpe {float(qqq['sharpe']):.3f}，最大回撤 {float(qqq['max_drawdown_pct']):.3f}%。
- 缓冲相对强制Top10：换手 {mechanism['turnover_reduction_pct']:.2f}% 的降幅，CAGR差 {mechanism['cagr_delta_pct_points']:+.3f}pp，Sharpe差 {mechanism['sharpe_delta']:+.3f}，回撤差 {mechanism['max_drawdown_delta_pct_points']:+.3f}pp；预声明机制门槛{passed_text}。

完整机器规则见同目录 `experiment_snapshot.json`；HTML首页只保留自然语言流程。
"""
    (run_root / "report.md").write_text(report_md, encoding="utf-8")

    source_paths = [
        Path("data/nasdaq100_history_registry.json"),
        Path(
            "data/2026-08-05多个数据包_rethink/纳斯达克 100 成分股/"
            "纳斯达克 100 成分股-拆股及股息调整_20260805.zip"
        ),
        Path("data/processed/universes/nasdaq100/pending_review/source_manifest.json"),
        Path("data/processed/universes/nasdaq100/pending_review/security_master.csv"),
        Path("data/processed/universes/nasdaq100/pending_review/membership_intervals.csv"),
        Path("data/processed/calendars/XNYS.csv"),
        Path("data/processed/daily/QQQ.csv"),
        Path("backtest/quantkit/nasdaq100_momentum_rotation.py"),
        Path("backtest/quantkit/trend_score_portfolio.py"),
        Path("backtest/scripts/run_nasdaq100_momentum_rotation.py"),
        Path("backtest/scripts/analyze_nasdaq100_momentum_rotation.py"),
        Path("backtest/requirements.lock"),
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
        "source_files": {
            path.as_posix(): {
                "bytes": (WORKSPACE_ROOT / path).stat().st_size,
                "sha256": sha256(WORKSPACE_ROOT / path),
            }
            for path in source_paths
        },
        "source_archive": block_five["manifest"]["shared_manifest_sha256"],
    }
    write_json(run_root / "provenance.json", provenance)
    print(
        f"Analyzed {args.run_id}: buffer CAGR={float(primary['cagr_pct']):.3f}% "
        f"Sharpe={float(primary['sharpe']):.3f} gate={passed_text}",
        flush=True,
    )


if __name__ == "__main__":
    main()
