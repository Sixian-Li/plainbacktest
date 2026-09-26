#!/usr/bin/env python3
"""Analyze and report the QQQ dual-StochRSI CROSS period grid."""

from __future__ import annotations

import argparse
import html
import json
import math
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
    assert_run_writable, cost_label, load_experiment, load_run,
    record_analysis_complete, sha256,
)
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts.analyze_stochrsi_cross_threshold_grid import connected_components, json_safe


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = (
    BACKTEST_ROOT
    / "experiments/TIM/TIM-v0.70a.2__26-08-25__qqq_stochrsi_period_grid_full_history"
)
COLORS = {
    "BUY_HOLD": "#334155", "SMA200": "#7c3aed", "ANCHOR": "#2563eb",
    "MAX_CAGR": "#dc2626", "MAX_SHARPE": "#e67e22", "STABLE": "#16a085",
}


def load_results(run_root: Path, costs: list[float]) -> pd.DataFrame:
    frames = []
    for cost in costs:
        frame = pd.read_csv(run_root / "QQQ" / cost_label(cost) / "parameter_results.csv")
        frame.insert(0, "cost_bps", cost)
        frames.append(frame)
    result = pd.concat(frames, ignore_index=True)
    if result.duplicated(["cost_bps", "case_id"]).any():
        raise RuntimeError("Duplicate cost/case rows")
    return result


def analyze_surface(five: pd.DataFrame, parameters: dict[str, Any]) -> dict[str, Any]:
    by_cagr = five.sort_values(
        ["cagr_pct", "sharpe", "max_drawdown_pct", "case_id"],
        ascending=[False, False, False, True],
    )
    by_sharpe = five.sort_values(
        ["sharpe", "cagr_pct", "max_drawdown_pct", "case_id"],
        ascending=[False, False, False, True],
    )
    max_cagr, max_sharpe = float(by_cagr.iloc[0]["cagr_pct"]), float(by_sharpe.iloc[0]["sharpe"])
    high = five[
        five["cagr_pct"].ge(0.95 * max_cagr) & five["sharpe"].ge(0.95 * max_sharpe)
    ].copy()
    short_start, long_start, step = (
        int(parameters["short_period_start"]), int(parameters["long_period_start"]),
        int(parameters["period_step"]),
    )
    points = {
        ((int(row.short_period) - short_start) // step, (int(row.long_period) - long_start) // step)
        for row in high.itertuples()
    }
    components = connected_components(points)
    qualifying: list[set[tuple[int, int]]] = []
    component_records = []
    short_last = (int(parameters["short_period_end"]) - short_start) // step
    long_last = (int(parameters["long_period_end"]) - long_start) // step
    for component in components:
        touches = any(i in {0, short_last} or j in {0, long_last} for i, j in component)
        qualifies = len(component) >= 9 and not touches
        if qualifies:
            qualifying.append(component)
        component_records.append({
            "size": len(component), "touches_boundary": touches, "qualifies": qualifies,
            "short_min": short_start + min(p[0] for p in component) * step,
            "short_max": short_start + max(p[0] for p in component) * step,
            "long_min": long_start + min(p[1] for p in component) * step,
            "long_max": long_start + max(p[1] for p in component) * step,
        })
    stable = None
    if qualifying:
        candidates = []
        for component_index, component in enumerate(qualifying):
            center_i = sum(p[0] for p in component) / len(component)
            center_j = sum(p[1] for p in component) / len(component)
            for i, j in component:
                short, long = short_start + i * step, long_start + j * step
                row = five[five["short_period"].eq(short) & five["long_period"].eq(long)].iloc[0]
                candidates.append((
                    min(float(row["cagr_pct"]) / max_cagr, float(row["sharpe"]) / max_sharpe),
                    len(component), -math.hypot(i - center_i, j - center_j), str(row["case_id"]),
                    component_index, row,
                ))
        chosen = sorted(candidates, key=lambda x: (-x[0], -x[1], -x[2], x[3]))[0]
        stable = {
            **json_safe(chosen[5].to_dict()), "component_index": chosen[4],
            "component_size": chosen[1], "joint_normalized_score": chosen[0],
        }
    anchor = five[
        five["short_period"].eq(int(parameters["parent_anchor_short_period"]))
        & five["long_period"].eq(int(parameters["parent_anchor_long_period"]))
    ]
    if len(anchor) != 1:
        raise RuntimeError("Parent 42/100 anchor is missing or duplicated")
    return {
        "parent_anchor": json_safe(anchor.iloc[0].to_dict()),
        "mechanical_max_cagr": json_safe(by_cagr.iloc[0].to_dict()),
        "mechanical_max_sharpe": json_safe(by_sharpe.iloc[0].to_dict()),
        "high_performance_case_count": len(high), "component_count": len(components),
        "components": component_records, "stable_representative": stable,
    }


def case_label(record: dict[str, Any]) -> str:
    return f"周期{int(record['short_period'])}/{int(record['long_period'])}"


def selected_map(surface: dict[str, Any]) -> dict[str, dict[str, Any]]:
    candidates = {
        "ANCHOR": surface["parent_anchor"], "MAX_CAGR": surface["mechanical_max_cagr"],
        "MAX_SHARPE": surface["mechanical_max_sharpe"],
    }
    if surface["stable_representative"] is not None:
        candidates["STABLE"] = surface["stable_representative"]
    result, seen = {}, set()
    for role, record in candidates.items():
        if record["case_id"] in seen:
            continue
        seen.add(record["case_id"])
        result[role] = record
    return result


def heatmap_figure(results: pd.DataFrame) -> go.Figure:
    figure = make_subplots(
        rows=2, cols=2,
        subplot_titles=("0 bps CAGR", "5 bps CAGR", "0 bps Sharpe", "5 bps Sharpe"),
        horizontal_spacing=0.1, vertical_spacing=0.13,
    )
    ranges = {
        "cagr_pct": (results["cagr_pct"].min(), results["cagr_pct"].max(), "Viridis", "CAGR %"),
        "sharpe": (results["sharpe"].min(), results["sharpe"].max(), "Cividis", "Sharpe"),
    }
    for column, cost in enumerate((0.0, 5.0), start=1):
        current = results[results["cost_bps"].eq(cost)]
        for row_number, metric in ((1, "cagr_pct"), (2, "sharpe")):
            matrix = current.pivot(index="long_period", columns="short_period", values=metric).sort_index()
            zmin, zmax, colorscale, title = ranges[metric]
            figure.add_trace(go.Heatmap(
                x=matrix.columns, y=matrix.index, z=matrix.to_numpy(), zmin=zmin, zmax=zmax,
                colorscale=colorscale, showscale=column == 2, colorbar={"title": title},
                hovertemplate="短周期 %{x}<br>长周期 %{y}<br>值 %{z:.3f}<extra></extra>",
                meta={"panel": "heatmap", "metric": metric, "cost_bps": cost},
            ), row=row_number, col=column)
    figure.update_xaxes(title_text="短周期")
    figure.update_yaxes(title_text="长周期")
    figure.update_layout(template="plotly_white", height=900)
    return figure


def performance_figure(block: Path, selected: dict[str, dict[str, Any]]) -> go.Figure:
    state = np.load(block / "daily_signal_state.npz")
    dates, case_ids = pd.to_datetime(state["dates"]), list(state["case_ids"].astype(str))
    benchmark = pd.read_csv(block / "buy_hold_daily.csv", parse_dates=["date"])
    sma = pd.read_csv(block / "sma200_daily.csv", parse_dates=["date"])
    series = [
        ("BUY_HOLD", "Buy & Hold", benchmark["date"], benchmark["equity"].to_numpy(float)),
        ("SMA200", "SMA200 ±3%", sma["date"], sma["equity"].to_numpy(float)),
    ]
    for role, record in selected.items():
        series.append((role, case_label(record), dates, state["actual_equity"][case_ids.index(record["case_id"])]))
    figure = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.07,
        row_heights=[0.7, 0.3], subplot_titles=("账户净值", "回撤"),
    )
    for key, label, x, equity in series:
        visible: bool | str = True if key in {"BUY_HOLD", "ANCHOR", "STABLE", "MAX_CAGR"} else "legendonly"
        figure.add_trace(go.Scatter(
            x=x, y=equity, mode="lines", name=label,
            line={"color": COLORS[key], "width": 1.8}, visible=visible,
            meta={"series_key": key.lower(), "panel": "equity", "label": label,
                  "is_benchmark": key == "BUY_HOLD", "cost_bps": 5},
        ), row=1, col=1)
        drawdown = equity / np.maximum.accumulate(equity) - 1
        figure.add_trace(go.Scatter(
            x=x, y=drawdown * 100, mode="lines", showlegend=False,
            line={"color": COLORS[key], "width": 1.1}, visible=visible,
            meta={"series_key": key.lower(), "panel": "drawdown", "label": label},
        ), row=2, col=1)
    figure.update_layout(template="plotly_white", height=730, hovermode="x unified", dragmode="pan")
    figure.update_yaxes(title_text="美元", row=1, col=1)
    figure.update_yaxes(title_text="%", row=2, col=1)
    return figure


def market_figure(block: Path, selected: dict[str, dict[str, Any]]) -> go.Figure:
    indicator = pd.read_csv(block / "indicator_daily.csv", parse_dates=["date"])
    orders = pd.read_csv(block / "orders.csv", parse_dates=["date"])
    figure = go.Figure(go.Scatter(
        x=indicator["date"], y=indicator["close"], mode="lines", name="QQQ Close",
        line={"color": "#111827", "width": 1.4},
        meta={"series_key": "qqq_close", "panel": "price", "label": "QQQ Close"},
    ))
    for role, record in selected.items():
        current_case = orders[orders["case_id"].eq(record["case_id"])]
        for side, marker in (("buy", "triangle-up"), ("sell", "triangle-down")):
            current = current_case[current_case["type"].eq(side)]
            figure.add_trace(go.Scatter(
                x=current["date"], y=current["raw_fill_price"], mode="markers",
                marker={"color": COLORS[role], "size": 7, "symbol": marker},
                name=f"{case_label(record)} {side}", visible="legendonly",
                meta={"series_key": f"{role.lower()}_{side}", "panel": "market",
                      "label": f"{case_label(record)} {side}"},
            ))
    figure.update_layout(
        template="plotly_white", height=620, hovermode="x unified", dragmode="pan",
        xaxis_rangeslider_visible=False, yaxis_title="QQQ adjusted Close / fill price",
    )
    return figure


def key_rows(surface: dict[str, Any], benchmark: dict, sma: dict) -> list[tuple[str, dict]]:
    rows, seen = [("Buy & Hold", benchmark), ("SMA200 ±3%", sma)], set()
    for label, key in (
        ("父锚点", "parent_anchor"), ("机械最高CAGR", "mechanical_max_cagr"),
        ("机械最高Sharpe", "mechanical_max_sharpe"), ("稳定代表", "stable_representative"),
    ):
        record = surface[key]
        if record is None or record["case_id"] in seen:
            continue
        seen.add(record["case_id"])
        rows.append((f"{label}：{case_label(record)}", record))
    return rows


def html_table(rows: list[tuple[str, dict]], surface: dict[str, Any]) -> str:
    body = "".join(
        "<tr>" + f"<td>{html.escape(label)}</td><td>{r['cagr_pct']:.3f}%</td>"
        f"<td>{r['sharpe']:.3f}</td><td>{r['max_drawdown_pct']:.2f}%</td>"
        f"<td>{int(r['order_count'])}</td><td>{r['exposure_pct']:.1f}%</td></tr>"
        for label, r in rows
    )
    stable = surface["stable_representative"]
    message = (
        f"稳定代表所在内部连通区共{stable['component_size']}组。" if stable
        else "没有内部连通区同时满足至少九组和不触边界的门槛。"
    )
    return (
        "<h2>5 bps关键结果</h2><table><thead><tr><th>路径</th><th>CAGR</th><th>Sharpe</th>"
        "<th>最大回撤</th><th>订单</th><th>持仓率</th></tr></thead><tbody>" + body
        + "</tbody></table>"
        f"<p>95%双指标集合包含{surface['high_performance_case_count']}组；{message}</p>"
    )


def markdown_table(rows: list[tuple[str, dict]]) -> str:
    lines = ["| 路径 | CAGR | Sharpe | 最大回撤 | 订单 | 持仓率 |", "|---|---:|---:|---:|---:|---:|"]
    for label, r in rows:
        lines.append(
            f"| {label} | {r['cagr_pct']:.3f}% | {r['sharpe']:.3f} | "
            f"{r['max_drawdown_pct']:.2f}% | {int(r['order_count'])} | {r['exposure_pct']:.1f}% |"
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
    incomplete = [b["block_id"] for b in record["expected_blocks"] if b["status"] != "completed"]
    if incomplete:
        raise RuntimeError(f"Incomplete run blocks: {incomplete}")
    run_root, parameters = context.run_root(args.run_id), context.config["parameters"]
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(parents=True, exist_ok=True)
    results = load_results(run_root, [float(v) for v in context.config["cost_scenarios_bps_per_side"]])
    expected = int(parameters["combination_count_per_cost"])
    if len(results) != expected * 2:
        raise RuntimeError("Combined result count does not match the frozen grid")
    results.to_csv(analysis_root / "results_table.csv", index=False, lineterminator="\n")
    five = results[results["cost_bps"].eq(5)].copy()
    surface = analyze_surface(five, parameters)
    block = run_root / "QQQ" / cost_label(5)
    metrics = json.loads((block / "metrics.json").read_text(encoding="utf-8"))
    selected = selected_map(surface)
    rows = key_rows(surface, metrics["benchmark"], metrics["sma200"])
    summary = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "purpose": "full-history exploratory period robustness scan; not out-of-sample validation",
        "analysis_start": metrics["analysis_start"], "analysis_end": metrics["analysis_end"],
        "grid_case_count_per_cost": expected, "five_bps": surface,
        "five_bps_benchmark": metrics["benchmark"], "five_bps_sma200": metrics["sma200"],
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    stable = surface["stable_representative"]
    stable_text = (
        f"稳定代表为{case_label(stable)}，所在内部连通区共{stable['component_size']}组。"
        if stable else "没有候选通过预声明的内部九点连通区门槛。"
    )
    (run_root / "report.md").write_text(
        "# QQQ双周期Stochastic RSI CROSS周期网格\n\n"
        "- 阶段：2000–2026完整样本探索，不是样本外验证。\n"
        "- 网格：短周期14–50、长周期70–160、步长2，共874组/成本。\n"
        "- 阈值：买0.20、卖0.80；raw StochRSI共同反向穿越，不计算K/D。\n"
        f"- 稳定性：{stable_text}\n\n" + markdown_table(rows) + "\n",
        encoding="utf-8",
    )
    report = render_interactive_report(
        title="QQQ双周期Stochastic RSI CROSS周期网格",
        heading="QQQ 2000–2026 · 874组短长周期",
        subtitle="买0.20 / 卖0.80 · 0/5 bps · 盘前解价、Open-to-Close触发",
        summary_html=html_table(rows, surface),
        notes=[
            "短周期与长周期独立扫描完整笛卡尔积，父锚点42/100保留在参数面。",
            "市场图只显示QQQ Close；候选成交点可从图例打开。",
            "热力图对同一指标的0/5 bps面使用统一色阶。",
            "机械最高点只是样本内描述；只有预声明内部连通区才能命名稳定代表。",
            "等额定投是浏览器端显示情景，不修改正式账本。",
        ],
        figures=[
            ReportFigure("market-qqq", "QQQ Close与候选成交点", market_figure(block, selected), "market"),
            ReportFigure("performance-qqq", "5 bps关键路径净值与回撤", performance_figure(block, selected), "performance"),
            ReportFigure("period-surfaces", "短长周期参数面", heatmap_figure(results), "analysis"),
        ],
        experiment=context.config, run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")
    tracked = [
        "backtest/requirements.lock", "backtest/quantkit/experiment.py",
        "backtest/quantkit/dual_stochrsi_timing.py", "backtest/quantkit/metrics.py",
        "backtest/quantkit/reporting.py", "backtest/scripts/run_stochrsi_cross_period_grid.py",
        "backtest/scripts/analyze_stochrsi_cross_period_grid.py",
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
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (run_root / "README.md").write_text(
        f"# Run {args.run_id}\n\nFormal QQQ dual-StochRSI CROSS period grid; see report and QQQ cost blocks.\n",
        encoding="utf-8",
    )
    artifact_manifest = {"schema_version": 1, "created_at_utc": summary["created_at_utc"], "artifacts": {}}
    for path in sorted(run_root.rglob("*")):
        if path.is_file() and path.name not in {"artifact_manifest.json", "run.json", "validation.json"}:
            relative = str(path.relative_to(run_root))
            artifact_manifest["artifacts"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    (run_root / "artifact_manifest.json").write_text(
        json.dumps(artifact_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    record_analysis_complete(context, args.run_id)
    print(f"Wrote {run_root / 'report.html'}")


if __name__ == "__main__":
    main()
