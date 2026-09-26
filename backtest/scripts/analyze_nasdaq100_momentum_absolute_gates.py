#!/usr/bin/env python3
"""Analyze the fixed Nasdaq-100 absolute-momentum gate ablation."""

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
from quantkit.paths import BACKTEST_ROOT
from quantkit.reporting import ReportFigure, render_interactive_report


WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/ROT/ROT-v0.50b.2__26-08-29__nasdaq100_absolute_momentum_gate_factorial"
)
FORMAL_SYMBOL = "NASDAQ100_MOMENTUM_ROTATION"
PRIMARY_COST = 5.0
CONTROL_CASE = "TOP10_BUFFER__NO_GATE"
PRIMARY_CASE = "TOP10_BUFFER__M12_M6_POS"
CASE_LABELS = {
    "TOP10_REPLACE__NO_GATE": "强制Top10｜无门槛",
    "TOP10_BUFFER__NO_GATE": "缓冲Top10/20｜无门槛",
    "TOP10_REPLACE__M12_POS": "强制Top10｜12-1为正",
    "TOP10_BUFFER__M12_POS": "缓冲Top10/20｜12-1为正",
    "TOP10_REPLACE__M6_POS": "强制Top10｜6-1为正",
    "TOP10_BUFFER__M6_POS": "缓冲Top10/20｜6-1为正",
    "TOP10_REPLACE__M12_M6_POS": "强制Top10｜双正",
    "TOP10_BUFFER__M12_M6_POS": "缓冲Top10/20｜双正",
}
GATE_COLORS = {
    "NO_GATE": "#64748b",
    "M12_POS": "#2563eb",
    "M6_POS": "#f59e0b",
    "M12_M6_POS": "#16a34a",
}


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(json_safe(payload), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
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


def metric(block: dict[str, Any], case_id: str) -> dict[str, Any]:
    selected = block["metrics"][block["metrics"]["case_id"] == case_id]
    if len(selected) != 1:
        raise ValueError(f"missing metric row: {case_id}")
    return selected.iloc[0].to_dict()


def gate_name(case_id: str) -> str:
    return case_id.split("__", maxsplit=1)[1]


def portfolio_style(case_id: str) -> str:
    return "BUFFER" if "BUFFER" in case_id else "REPLACE"


def structural_test(block: dict[str, Any]) -> dict[str, Any]:
    control = metric(block, CONTROL_CASE)
    primary = metric(block, PRIMARY_CASE)
    cagr_delta = float(primary["cagr_pct"]) - float(control["cagr_pct"])
    sharpe_delta = float(primary["sharpe"]) - float(control["sharpe"])
    drawdown_improvement = float(primary["max_drawdown_pct"]) - float(
        control["max_drawdown_pct"]
    )
    turnover_change = (
        float(primary["annualized_turnover_multiple"])
        / float(control["annualized_turnover_multiple"])
        - 1.0
    )
    gates = {
        "max_drawdown_improves_at_least_10pp": drawdown_improvement >= 10.0,
        "sharpe_does_not_decline": sharpe_delta >= 0.0,
        "cagr_loss_no_more_than_1pp": cagr_delta >= -1.0,
        "turnover_increase_no_more_than_25pct": turnover_change <= 0.25,
    }
    return {
        "control_case": CONTROL_CASE,
        "primary_case": PRIMARY_CASE,
        "cagr_delta_pct_points": cagr_delta,
        "sharpe_delta": sharpe_delta,
        "max_drawdown_improvement_pct_points": drawdown_improvement,
        "turnover_change_pct": turnover_change * 100.0,
        "gates": gates,
        "all_structural_gates_passed": all(gates.values()),
    }


def normalized_equity(frame: pd.DataFrame) -> pd.Series:
    values = frame.sort_values("date").set_index("date")["equity"].astype(float)
    return values / float(values.iloc[0]) * 100.0


def equity_figure(block: dict[str, Any], case_ids: list[str], title: str) -> go.Figure:
    figure = go.Figure()
    for case_id in case_ids:
        selected = block["daily"][block["daily"]["case_id"] == case_id]
        series = normalized_equity(selected)
        figure.add_trace(
            go.Scatter(
                x=series.index,
                y=series.values,
                name=CASE_LABELS[case_id],
                line={"color": GATE_COLORS[gate_name(case_id)], "width": 2.5 if case_id == PRIMARY_CASE else 1.7},
                meta={
                    "series_key": case_id.lower(),
                    "label": CASE_LABELS[case_id],
                    "panel": "equity",
                    "cost_bps": PRIMARY_COST,
                },
            )
        )
    benchmark = normalized_equity(block["qqq"])
    figure.add_trace(
        go.Scatter(
            x=benchmark.index,
            y=benchmark.values,
            name="QQQ Buy & Hold",
            line={"color": "#111827", "width": 1.5, "dash": "dash"},
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
        title=title,
        height=540,
        hovermode="x unified",
        yaxis={"title": "同起点净值（100，对数）", "type": "log"},
        xaxis={"title": "日期", "rangeslider": {"visible": False}},
        legend={"orientation": "h", "y": 1.18},
        margin={"l": 75, "r": 25, "t": 75, "b": 55},
    )
    return figure


def drawdown_figure(block: dict[str, Any], case_ids: list[str]) -> go.Figure:
    figure = go.Figure()
    for case_id in case_ids:
        selected = block["daily"][block["daily"]["case_id"] == case_id]
        equity = selected.sort_values("date").set_index("date")["equity"].astype(float)
        drawdown = (equity / equity.cummax() - 1.0) * 100.0
        figure.add_trace(
            go.Scatter(
                x=drawdown.index,
                y=drawdown.values,
                name=CASE_LABELS[case_id],
                line={"color": GATE_COLORS[gate_name(case_id)], "width": 2.4 if case_id == PRIMARY_CASE else 1.6},
                meta={"series_key": f"dd_{case_id.lower()}", "label": CASE_LABELS[case_id]},
            )
        )
    qqq = block["qqq"].sort_values("date").set_index("date")["equity"].astype(float)
    figure.add_trace(
        go.Scatter(
            x=qqq.index,
            y=(qqq / qqq.cummax() - 1.0) * 100.0,
            name="QQQ Buy & Hold",
            line={"color": "#111827", "width": 1.3, "dash": "dash"},
            meta={"series_key": "dd_qqq", "label": "QQQ Buy & Hold"},
        )
    )
    figure.update_layout(
        template="plotly_white",
        height=460,
        hovermode="x unified",
        yaxis={"title": "从历史高点回撤", "ticksuffix": "%"},
        xaxis={"title": "日期"},
        legend={"orientation": "h", "y": 1.16},
        margin={"l": 70, "r": 25, "t": 60, "b": 55},
    )
    return figure


def rolling_figure(block: dict[str, Any], window: int = 1260) -> tuple[go.Figure, pd.DataFrame]:
    rows: list[pd.DataFrame] = []
    figure = go.Figure()
    inputs = [
        (CONTROL_CASE, CASE_LABELS[CONTROL_CASE], GATE_COLORS["NO_GATE"], block["daily"][block["daily"]["case_id"] == CONTROL_CASE]),
        (PRIMARY_CASE, CASE_LABELS[PRIMARY_CASE], GATE_COLORS["M12_M6_POS"], block["daily"][block["daily"]["case_id"] == PRIMARY_CASE]),
        ("QQQ_BUY_HOLD", "QQQ Buy & Hold", "#111827", block["qqq"]),
    ]
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
                line={"color": color, "width": 2.3 if case_id == PRIMARY_CASE else 1.5, "dash": "dash" if case_id == "QQQ_BUY_HOLD" else "solid"},
                meta={"series_key": f"rolling_{case_id.lower()}", "label": label},
            )
        )
    figure.add_hline(y=0.0, line={"color": "#94a3b8", "width": 1})
    figure.update_layout(
        template="plotly_white",
        height=440,
        hovermode="x unified",
        yaxis={"title": "滚动5年 CAGR", "ticksuffix": "%"},
        xaxis={"title": "窗口结束日"},
        legend={"orientation": "h", "y": 1.14},
        margin={"l": 70, "r": 25, "t": 50, "b": 55},
    )
    return figure, pd.concat(rows, ignore_index=True)


def cash_figure(block: dict[str, Any], case_ids: list[str]) -> go.Figure:
    figure = go.Figure()
    for case_id in case_ids:
        selected = block["daily"][block["daily"]["case_id"] == case_id]
        figure.add_trace(
            go.Scatter(
                x=selected["date"],
                y=selected["cash_pct"],
                name=CASE_LABELS[case_id],
                line={"color": GATE_COLORS[gate_name(case_id)], "width": 2.1 if case_id == PRIMARY_CASE else 1.4},
                meta={"series_key": f"cash_{case_id.lower()}", "label": CASE_LABELS[case_id]},
            )
        )
    figure.update_layout(
        template="plotly_white",
        height=400,
        hovermode="x unified",
        yaxis={"title": "现金占净值", "ticksuffix": "%", "range": [0, 105]},
        xaxis={"title": "日期"},
        legend={"orientation": "h", "y": 1.16},
        margin={"l": 65, "r": 25, "t": 55, "b": 55},
    )
    return figure


def stress_diagnostics(
    block: dict[str, Any],
    stress_windows: dict[str, list[str]],
    case_ids: list[str],
) -> pd.DataFrame:
    frames = {
        case_id: block["daily"][block["daily"]["case_id"] == case_id]
        for case_id in case_ids
    }
    frames["QQQ_BUY_HOLD"] = block["qqq"]
    rows: list[dict[str, Any]] = []
    for window_id, (start_text, end_text) in stress_windows.items():
        start = pd.Timestamp(start_text)
        end = pd.Timestamp(end_text)
        for case_id, frame in frames.items():
            selected = frame[(frame["date"] >= start) & (frame["date"] <= end)].sort_values("date")
            if len(selected) < 2:
                continue
            equity = selected["equity"].astype(float)
            rows.append(
                {
                    "window_id": window_id,
                    "start": selected.iloc[0]["date"],
                    "end": selected.iloc[-1]["date"],
                    "case_id": case_id,
                    "total_return_pct": (float(equity.iloc[-1]) / float(equity.iloc[0]) - 1.0) * 100.0,
                    "local_max_drawdown_pct": float((equity / equity.cummax() - 1.0).min() * 100.0),
                }
            )
    return pd.DataFrame(rows)


def stress_figure(stress: pd.DataFrame) -> go.Figure:
    figure = go.Figure()
    for case_id, label, color in (
        (CONTROL_CASE, CASE_LABELS[CONTROL_CASE], GATE_COLORS["NO_GATE"]),
        (PRIMARY_CASE, CASE_LABELS[PRIMARY_CASE], GATE_COLORS["M12_M6_POS"]),
        ("QQQ_BUY_HOLD", "QQQ Buy & Hold", "#111827"),
    ):
        selected = stress[stress["case_id"] == case_id]
        figure.add_trace(
            go.Bar(
                x=selected["window_id"],
                y=selected["total_return_pct"],
                name=label,
                marker_color=color,
            )
        )
    figure.update_layout(
        template="plotly_white",
        height=410,
        barmode="group",
        yaxis={"title": "冻结区间收益", "ticksuffix": "%"},
        xaxis={"title": "预先登记的压力窗口"},
        legend={"orientation": "h", "y": 1.14},
        margin={"l": 65, "r": 25, "t": 50, "b": 75},
    )
    return figure


def metric_table(block: dict[str, Any], formal_cases: list[str]) -> str:
    rows = []
    for case_id in formal_cases:
        item = metric(block, case_id)
        invested_days = float(
            (
                block["daily"].loc[
                    block["daily"]["case_id"] == case_id, "holdings_count"
                ]
                > 0
            ).mean()
            * 100.0
        )
        rows.append(
            "<tr>"
            f"<td>{html.escape(CASE_LABELS[case_id])}</td>"
            f"<td>{float(item['cagr_pct']):.2f}%</td>"
            f"<td>{float(item['sharpe']):.3f}</td>"
            f"<td>{float(item['max_drawdown_pct']):.2f}%</td>"
            f"<td>{float(item['annualized_turnover_multiple']):.2f}×</td>"
            f"<td>{float(item['average_cash_pct']):.1f}%</td>"
            f"<td>{invested_days:.1f}%</td>"
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
        "<td>0.0%</td><td>100.0%</td>"
        "</tr>"
    )
    return (
        '<div class="table-wrap"><table><thead><tr>'
        "<th>5 bps路径</th><th>CAGR</th><th>Sharpe</th><th>最大回撤</th>"
        "<th>年化换手</th><th>平均现金</th><th>有仓位日</th>"
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
    formal_cases = [str(value) for value in context.config["parameters"]["formal_cases"]]
    if set(formal_cases) != set(CASE_LABELS):
        raise ValueError("formal case matrix differs from the frozen eight-case analysis")

    run_root = context.run_root(args.run_id)
    block_zero = load_block(context, args.run_id, 0.0)
    block_five = load_block(context, args.run_id, PRIMARY_COST)
    structural = structural_test(block_five)
    primary = metric(block_five, PRIMARY_CASE)
    control = metric(block_five, CONTROL_CASE)
    qqq = block_five["metrics_json"]["benchmark"]
    buffer_cases = [case_id for case_id in formal_cases if portfolio_style(case_id) == "BUFFER"]
    forced_cases = [case_id for case_id in formal_cases if portfolio_style(case_id) == "REPLACE"]
    rolling_chart, rolling = rolling_figure(block_five)
    stress = stress_diagnostics(
        block_five,
        context.config["parameters"]["stress_windows"],
        [CONTROL_CASE, PRIMARY_CASE],
    )

    analysis_root = run_root / "analysis"
    analysis_root.mkdir(parents=True, exist_ok=True)
    all_metrics = pd.concat(
        [
            block_zero["metrics"].assign(cost_bps=0.0),
            block_five["metrics"].assign(cost_bps=5.0),
        ],
        ignore_index=True,
    )
    exposure_rows = []
    for case_id in formal_cases:
        daily = block_five["daily"][block_five["daily"]["case_id"] == case_id]
        exposure_rows.append(
            {
                "case_id": case_id,
                "average_cash_pct": float(daily["cash_pct"].mean()),
                "invested_day_pct": float((daily["holdings_count"] > 0).mean() * 100.0),
                "all_cash_day_pct": float((daily["holdings_count"] == 0).mean() * 100.0),
            }
        )
    exposure = pd.DataFrame(exposure_rows)
    all_metrics.to_csv(analysis_root / "case_comparison.csv", index=False, lineterminator="\n")
    rolling.to_csv(analysis_root / "rolling_5y_cagr.csv", index=False, lineterminator="\n")
    stress.to_csv(analysis_root / "stress_windows.csv", index=False, lineterminator="\n")
    exposure.to_csv(analysis_root / "exposure.csv", index=False, lineterminator="\n")

    summary = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "data_status": "candidate_pending_review",
        "research_character": "fixed mechanism ablation; no parameter selection; PBO/DSR not applicable",
        "primary_metrics": {"symbol": FORMAL_SYMBOL, "cost_bps": PRIMARY_COST, **primary},
        "control_metrics": control,
        "qqq_buy_hold_metrics": qqq,
        "structural_test": structural,
        "cost_case_metrics": all_metrics.to_dict("records"),
        "exposure": exposure.to_dict("records"),
        "stress_windows": stress.to_dict("records"),
    }
    write_json(analysis_root / "summary.json", summary)

    passed_text = "通过" if structural["all_structural_gates_passed"] else "未通过"
    summary_html = (
        '<section class="summary-callout">'
        f"<p><strong>预声明双正门槛：{passed_text}。</strong>相对无门槛缓冲组合，"
        f"最大回撤改善 {structural['max_drawdown_improvement_pct_points']:+.2f} 个百分点，"
        f"Sharpe变化 {structural['sharpe_delta']:+.3f}，"
        f"CAGR变化 {structural['cagr_delta_pct_points']:+.2f} 个百分点，"
        f"年化换手变化 {structural['turnover_change_pct']:+.1f}%。</p>"
        "<p><strong>研究边界：</strong>四种门槛均事先固定，单门槛只做归因，不按全样本赢家换候选；"
        "历史成分数据仍为 pending_review，因此本结果不是样本外或可直接交易结论。</p>"
        "</section>"
        + metric_table(block_five, formal_cases)
    )
    notes = [
        "12-1与6-1都使用上个月末作为终点，信号月收益不参与计算；缺少本路径所需精确端点即不合格。",
        "先应用绝对门槛，再在合格集合中按12-1排名；门槛失败会覆盖Top20缓冲，下一Open退出。",
        "合格证券不足10只时不从不合格股票补位，完全无人合格时组合可以全部持有现金。",
        "所有目标股数由月末完成Close和当时账户权益决定，下一Open只负责先卖后买；没有使用未来Open选股。",
        "本轮是固定结构消融，不进行参数选择，因此PBO与DSR不适用；任何保留结构都需另建时间顺序验证。",
    ]
    figures = [
        ReportFigure(
            div_id="performance-nasdaq100-absolute-gates-buffer",
            title="缓冲组合：四种绝对动量门槛",
            figure=equity_figure(block_five, buffer_cases, "缓冲Top10/20 · 5 bps"),
            kind="performance",
        ),
        ReportFigure(
            div_id="drawdown-nasdaq100-absolute-gates-buffer",
            title="缓冲组合历史回撤",
            figure=drawdown_figure(block_five, buffer_cases),
            kind="static",
        ),
        ReportFigure(
            div_id="performance-nasdaq100-absolute-gates-forced",
            title="强制Top10：四种绝对动量门槛",
            figure=equity_figure(block_five, forced_cases, "每月强制Top10 · 5 bps"),
            kind="static",
        ),
        ReportFigure(
            div_id="cash-nasdaq100-absolute-gates",
            title="门槛带来的现金暴露",
            figure=cash_figure(block_five, buffer_cases),
            kind="static",
        ),
        ReportFigure(
            div_id="rolling-nasdaq100-absolute-gates",
            title="预指定双正路径与无门槛基线的滚动5年 CAGR",
            figure=rolling_chart,
            kind="static",
        ),
        ReportFigure(
            div_id="stress-nasdaq100-absolute-gates",
            title="冻结压力窗口收益",
            figure=stress_figure(stress),
            kind="static",
        ),
    ]
    report_html = render_interactive_report(
        title="Nasdaq-100 12-1排名绝对动量门槛消融",
        heading="只持有相对强者，还是要求它自身也在上涨",
        subtitle="1999-03-31信号至2026-08-04 · 历史时点成分候选数据 · 0/5 bps",
        summary_html=summary_html,
        notes=notes,
        figures=figures,
        experiment=context.config,
        run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    (run_root / "report.html").write_text(report_html, encoding="utf-8")

    report_md = f"""# Nasdaq-100 12-1排名绝对动量门槛消融

- 数据状态：`candidate_pending_review`，不是 approved 数据。
- 预指定主路径（双正缓冲，5 bps）：CAGR {float(primary['cagr_pct']):.3f}%，Sharpe {float(primary['sharpe']):.3f}，最大回撤 {float(primary['max_drawdown_pct']):.3f}%。
- 无门槛缓冲对照（5 bps）：CAGR {float(control['cagr_pct']):.3f}%，Sharpe {float(control['sharpe']):.3f}，最大回撤 {float(control['max_drawdown_pct']):.3f}%。
- QQQ持有（5 bps）：CAGR {float(qqq['cagr_pct']):.3f}%，Sharpe {float(qqq['sharpe']):.3f}，最大回撤 {float(qqq['max_drawdown_pct']):.3f}%。
- 双正门槛相对对照：回撤改善 {structural['max_drawdown_improvement_pct_points']:+.3f}pp，Sharpe变化 {structural['sharpe_delta']:+.3f}，CAGR变化 {structural['cagr_delta_pct_points']:+.3f}pp，换手变化 {structural['turnover_change_pct']:+.2f}%；预声明结构门槛{passed_text}。
- 本轮不做参数选择，PBO与DSR不适用；完整规则见 `experiment_snapshot.json`。
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
        Path("backtest/scripts/analyze_nasdaq100_momentum_absolute_gates.py"),
        Path("backtest/scripts/finalize_nasdaq100_momentum_absolute_gates.py"),
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
        f"Analyzed {args.run_id}: dual-positive buffer CAGR={float(primary['cagr_pct']):.3f}% "
        f"Sharpe={float(primary['sharpe']):.3f} structural_gate={passed_text}",
        flush=True,
    )


if __name__ == "__main__":
    main()
