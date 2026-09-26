#!/usr/bin/env python3
"""Analyze the fixed QQQ dual-StochRSI comparison and render its v5 report."""

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
    assert_run_writable,
    cost_label,
    load_experiment,
    load_run,
    record_analysis_complete,
    sha256,
)
from quantkit.reporting import ReportFigure, render_interactive_report


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/TIM/TIM-v0.70__26-08-24__qqq_dual_stochrsi_timing"
LABELS = {
    "BUY_HOLD": "Buy & Hold",
    "SMA200": "SMA200 +3%/-3%",
    "LEVEL": "双周期低于0.2/高于0.8",
    "EXTREME": "双周期等于0/等于1",
    "CROSS": "双周期上穿0.2/下穿0.8",
}
COLORS = {
    "BUY_HOLD": "#334155", "SMA200": "#7c3aed", "LEVEL": "#2563eb",
    "EXTREME": "#e67e22", "CROSS": "#16a085",
}


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def combined_results(run_root: Path, costs: list[float]) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for cost in costs:
        block = run_root / "QQQ" / cost_label(cost)
        result = pd.read_csv(block / "parameter_results.csv")
        result.insert(0, "cost_bps", cost)
        benchmark = json.loads((block / "metrics.json").read_text(encoding="utf-8"))["benchmark"]
        rows.append(result)
        rows.append(
            pd.DataFrame([{
                "cost_bps": cost, "case_id": "BUY_HOLD", "label": LABELS["BUY_HOLD"],
                "mode": "BUY_HOLD", **benchmark,
            }])
        )
    combined = pd.concat(rows, ignore_index=True)
    order = {key: index for index, key in enumerate(LABELS)}
    combined["sort_order"] = combined["case_id"].map(order)
    return combined.sort_values(["cost_bps", "sort_order"]).drop(columns="sort_order").reset_index(drop=True)


def results_table(frame: pd.DataFrame) -> str:
    body = []
    for row in frame.itertuples():
        body.append(
            "<tr>"
            f"<td>{row.cost_bps:g}</td><td>{html.escape(LABELS[str(row.case_id)])}</td>"
            f"<td>{row.total_return_pct:.2f}%</td><td>{row.cagr_pct:.3f}%</td>"
            f"<td>{row.sharpe:.3f}</td><td>{row.max_drawdown_pct:.2f}%</td>"
            f"<td>{int(row.order_count)}</td><td>{row.exposure_pct:.1f}%</td></tr>"
        )
    return (
        "<h2>固定规则结果</h2><table><thead><tr><th>单边成本(bps)</th><th>路径</th>"
        "<th>总收益</th><th>CAGR</th><th>Sharpe</th><th>最大回撤</th><th>订单</th>"
        "<th>持仓率</th></tr></thead><tbody>" + "".join(body) + "</tbody></table>"
        "<p>三种 StochRSI 规则全部原样报告，没有从本区间挑选或改写参数。</p>"
    )


def market_figure(block: Path) -> go.Figure:
    indicator = pd.read_csv(block / "indicator_daily.csv", parse_dates=["date"])
    orders = pd.read_csv(block / "orders.csv", parse_dates=["date"])
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=indicator["date"], y=indicator["close"], mode="lines", name="QQQ Close",
            line={"color": "#111827", "width": 1.5},
            meta={"series_key": "qqq_close", "panel": "price", "label": "QQQ Close"},
        )
    )
    for case_id in ("LEVEL", "EXTREME", "CROSS"):
        for side, symbol in (("buy", "triangle-up"), ("sell", "triangle-down")):
            current = orders[(orders["case_id"] == case_id) & (orders["type"] == side)]
            figure.add_trace(
                go.Scatter(
                    x=current["date"], y=current["raw_fill_price"], mode="markers",
                    marker={"color": COLORS[case_id], "size": 7, "symbol": symbol},
                    name=f"{LABELS[case_id]} {side}", visible="legendonly",
                    meta={
                        "series_key": f"{case_id.lower()}_{side}", "panel": "market",
                        "label": f"{LABELS[case_id]} {side}",
                    },
                )
            )
    figure.update_layout(
        template="plotly_white", height=620, hovermode="x unified", dragmode="pan",
        xaxis_rangeslider_visible=False, yaxis_title="QQQ adjusted Close / fill price",
    )
    return figure


def performance_figure(block: Path) -> go.Figure:
    daily = pd.read_csv(block / "daily.csv", parse_dates=["date"])
    benchmark = pd.read_csv(block / "buy_hold_daily.csv", parse_dates=["date"])
    figure = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.07,
        row_heights=[0.7, 0.3], subplot_titles=("账户净值", "回撤"),
    )
    series: list[tuple[str, pd.DataFrame]] = [("BUY_HOLD", benchmark)]
    series.extend(
        (case_id, daily[daily["case_id"].eq(case_id)].copy())
        for case_id in ("SMA200", "LEVEL", "EXTREME", "CROSS")
    )
    for case_id, current in series:
        current = current.sort_values("date")
        visible: bool | str = True if case_id in ("BUY_HOLD", "LEVEL", "EXTREME", "CROSS") else "legendonly"
        meta = {
            "series_key": case_id.lower(), "panel": "equity", "label": LABELS[case_id],
            "is_benchmark": case_id == "BUY_HOLD", "cost_bps": 5,
        }
        figure.add_trace(
            go.Scatter(
                x=current["date"], y=current["equity"], mode="lines", name=LABELS[case_id],
                line={"color": COLORS[case_id], "width": 1.8}, visible=visible, meta=meta,
            ), row=1, col=1,
        )
        equity = current["equity"].astype(float)
        drawdown = equity / equity.cummax() - 1
        figure.add_trace(
            go.Scatter(
                x=current["date"], y=drawdown * 100, mode="lines", showlegend=False,
                line={"color": COLORS[case_id], "width": 1.2}, visible=visible,
                meta={"series_key": case_id.lower(), "panel": "drawdown", "label": LABELS[case_id]},
            ), row=2, col=1,
        )
    figure.update_layout(template="plotly_white", height=720, hovermode="x unified", dragmode="pan")
    figure.update_yaxes(title_text="美元", row=1, col=1)
    figure.update_yaxes(title_text="%", row=2, col=1)
    return figure


def markdown_table(frame: pd.DataFrame) -> str:
    lines = [
        "| 成本(bps) | 路径 | 总收益 | CAGR | Sharpe | 最大回撤 | 订单 | 持仓率 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in frame.itertuples():
        lines.append(
            f"| {row.cost_bps:g} | {LABELS[str(row.case_id)]} | {row.total_return_pct:.2f}% | "
            f"{row.cagr_pct:.3f}% | {row.sharpe:.3f} | {row.max_drawdown_pct:.2f}% | "
            f"{int(row.order_count)} | {row.exposure_pct:.1f}% |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    assert_run_writable(context, args.run_id)
    record = load_run(context, args.run_id)
    incomplete = [item["block_id"] for item in record["expected_blocks"] if item["status"] != "completed"]
    if incomplete:
        raise RuntimeError(f"Incomplete run blocks: {incomplete}")
    run_root = context.run_root(args.run_id)
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(parents=True, exist_ok=True)
    costs = [float(value) for value in context.config["cost_scenarios_bps_per_side"]]
    results = combined_results(run_root, costs)
    if len(results) != 10 or set(results["case_id"]) != set(LABELS):
        raise RuntimeError("Expected five fixed paths for each of two cost scenarios")
    results.to_csv(analysis_root / "results_table.csv", index=False, lineterminator="\n")
    five = results[results["cost_bps"].eq(5)].copy()
    summary = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "purpose": "fixed full-sample exploratory comparison; not out-of-sample validation",
        "analysis_start": str(five.iloc[0]["start"]),
        "analysis_end": str(five.iloc[0]["end"]),
        "zero_bps_cases": json_safe(results[results["cost_bps"].eq(0)].to_dict("records")),
        "five_bps_cases": json_safe(five.to_dict("records")),
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    (run_root / "report.md").write_text(
        "# QQQ 双周期 Stochastic RSI 三种择时语义\n\n"
        "- 阶段：2020–2026 完整样本探索，不是样本外验证。\n"
        "- 指标：raw StochRSI 42 与 100，不计算 K/D。\n"
        "- 成交：前一日及更早数据盘前解价；Open 跳空或 Open-to-Close 定向触线。\n"
        "- 对照：Buy & Hold、SMA200 +3%/-3%，以及报告内等额定投情景。\n\n"
        + markdown_table(results) + "\n",
        encoding="utf-8",
    )
    five_block = run_root / "QQQ" / cost_label(5)
    report = render_interactive_report(
        title="QQQ 双周期 Stochastic RSI 三种择时语义",
        heading="QQQ 2020–2026 固定规则比较",
        subtitle="StochRSI 42/100 · 三种配对语义 · 0/5 bps · 盘前解价、Open-to-Close 触发",
        summary_html=results_table(results),
        notes=[
            "市场图只显示 QQQ Close；买卖点可从图例按策略打开。",
            "阈值价只使用前一日及更早完成数据；当日 High/Low 不用于推断触发。",
            "SMA200 基线也使用同一跳空/Open-to-Close 成交口径，便于公平比较。",
            "等额定投是浏览器端显示情景，不修改正式策略、基准或指标。",
            "本区间用于探索三种语义差别，不能据此称任何一种未来最优。",
        ],
        figures=[
            ReportFigure("market-qqq", "QQQ Close 与三种策略成交点", market_figure(five_block), "market"),
            ReportFigure("performance-qqq", "5 bps 净值与回撤", performance_figure(five_block), "performance"),
        ],
        experiment=context.config,
        run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")
    tracked = [
        "backtest/requirements.lock", "backtest/quantkit/experiment.py",
        "backtest/quantkit/dual_stochrsi_timing.py", "backtest/quantkit/metrics.py",
        "backtest/quantkit/reporting.py", "backtest/scripts/run_dual_stochrsi_timing.py",
        "backtest/scripts/analyze_dual_stochrsi_timing.py",
        "backtest/report_templates/interactive_research_v5/page.html",
        "backtest/report_templates/interactive_research_v5/styles.css",
        "backtest/report_templates/interactive_research_v5/interactions.js",
        "data/processed/manifest.json", "data/processed/daily/QQQ.csv",
    ]
    provenance = {
        "schema_version": 1, "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id, "created_at_utc": summary["created_at_utc"],
        "software": {"python": platform.python_version(), "lib_pybroker": "1.2.12", "plotly": plotly.__version__},
        "source_files": {},
    }
    for relative in tracked:
        path = WORKSPACE_ROOT / relative
        provenance["source_files"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    (run_root / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    (run_root / "README.md").write_text(
        f"# Run {args.run_id}\n\nFormal fixed QQQ dual-StochRSI comparison; see report.html, report.md, analysis/, and QQQ cost blocks.\n",
        encoding="utf-8",
    )
    artifact_manifest = {"schema_version": 1, "created_at_utc": summary["created_at_utc"], "artifacts": {}}
    for path in sorted(run_root.rglob("*")):
        if path.is_file() and path.name not in {"artifact_manifest.json", "run.json", "validation.json"}:
            relative = str(path.relative_to(run_root))
            artifact_manifest["artifacts"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    (run_root / "artifact_manifest.json").write_text(
        json.dumps(artifact_manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    record_analysis_complete(context, args.run_id)
    print(f"Wrote {run_root / 'report.html'}")


if __name__ == "__main__":
    main()
