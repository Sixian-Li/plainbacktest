#!/usr/bin/env python3
"""Analyze and report the full-history QQQ dual-StochRSI CROSS threshold grid."""

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
DEFAULT_EXPERIMENT = (
    BACKTEST_ROOT
    / "experiments/TIM/TIM-v0.70a.1__26-08-24__qqq_stochrsi_cross_grid_full_history"
)
COLORS = {
    "BUY_HOLD": "#334155",
    "SMA200": "#7c3aed",
    "ANCHOR": "#2563eb",
    "MAX_CAGR": "#dc2626",
    "MAX_SHARPE": "#e67e22",
    "STABLE": "#16a085",
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


def load_results(run_root: Path, costs: list[float]) -> pd.DataFrame:
    frames = []
    for cost in costs:
        frame = pd.read_csv(run_root / "QQQ" / cost_label(cost) / "parameter_results.csv")
        frame.insert(0, "cost_bps", cost)
        frames.append(frame)
    result = pd.concat(frames, ignore_index=True)
    if result.duplicated(["cost_bps", "case_id"]).any():
        raise RuntimeError("Duplicate cost/case rows in combined grid")
    return result


def connected_components(points: set[tuple[int, int]]) -> list[set[tuple[int, int]]]:
    remaining = set(points)
    components: list[set[tuple[int, int]]] = []
    while remaining:
        seed = remaining.pop()
        component = {seed}
        frontier = [seed]
        while frontier:
            buy, sell = frontier.pop()
            for buy_step in (-1, 0, 1):
                for sell_step in (-1, 0, 1):
                    if buy_step == sell_step == 0:
                        continue
                    neighbor = (buy + buy_step, sell + sell_step)
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                        component.add(neighbor)
                        frontier.append(neighbor)
        components.append(component)
    return sorted(components, key=lambda item: (-len(item), min(item)))


def analyze_surface(five: pd.DataFrame) -> dict[str, Any]:
    ordered_cagr = five.sort_values(
        ["cagr_pct", "sharpe", "max_drawdown_pct", "case_id"],
        ascending=[False, False, False, True],
    )
    ordered_sharpe = five.sort_values(
        ["sharpe", "cagr_pct", "max_drawdown_pct", "case_id"],
        ascending=[False, False, False, True],
    )
    max_cagr = float(ordered_cagr.iloc[0]["cagr_pct"])
    max_sharpe = float(ordered_sharpe.iloc[0]["sharpe"])
    high = five[
        five["cagr_pct"].ge(0.95 * max_cagr)
        & five["sharpe"].ge(0.95 * max_sharpe)
    ].copy()
    points = {
        (int(round(row.buy_threshold * 100)), int(round(row.sell_threshold * 100)))
        for row in high.itertuples()
    }
    components = connected_components(points)
    component_records = []
    qualifying: list[set[tuple[int, int]]] = []
    for component in components:
        touches_boundary = any(
            buy in {0, 40} or sell in {60, 100} for buy, sell in component
        )
        qualifies = len(component) >= 9 and not touches_boundary
        if qualifies:
            qualifying.append(component)
        component_records.append({
            "size": len(component),
            "touches_boundary": touches_boundary,
            "qualifies": qualifies,
            "buy_min": min(point[0] for point in component) / 100,
            "buy_max": max(point[0] for point in component) / 100,
            "sell_min": min(point[1] for point in component) / 100,
            "sell_max": max(point[1] for point in component) / 100,
        })

    stable: dict[str, Any] | None = None
    if qualifying:
        candidates = []
        for component_index, component in enumerate(qualifying):
            center_buy = sum(point[0] for point in component) / len(component)
            center_sell = sum(point[1] for point in component) / len(component)
            for buy, sell in component:
                row = five[
                    five["buy_threshold"].eq(buy / 100)
                    & five["sell_threshold"].eq(sell / 100)
                ].iloc[0]
                candidates.append((
                    min(float(row["cagr_pct"]) / max_cagr, float(row["sharpe"]) / max_sharpe),
                    len(component),
                    -math.hypot(buy - center_buy, sell - center_sell),
                    str(row["case_id"]),
                    component_index,
                    row,
                ))
        chosen = sorted(candidates, key=lambda item: (-item[0], -item[1], -item[2], item[3]))[0]
        stable = {
            **json_safe(chosen[5].to_dict()),
            "component_index": chosen[4],
            "component_size": chosen[1],
            "joint_normalized_score": chosen[0],
        }

    anchor = five[
        five["buy_threshold"].eq(0.2) & five["sell_threshold"].eq(0.8)
    ]
    if len(anchor) != 1:
        raise RuntimeError("Parent 0.20/0.80 anchor is missing or duplicated")
    return {
        "parent_anchor": json_safe(anchor.iloc[0].to_dict()),
        "mechanical_max_cagr": json_safe(ordered_cagr.iloc[0].to_dict()),
        "mechanical_max_sharpe": json_safe(ordered_sharpe.iloc[0].to_dict()),
        "high_performance_case_count": len(high),
        "component_count": len(components),
        "components": component_records,
        "stable_representative": stable,
    }


def heatmap_figure(results: pd.DataFrame) -> go.Figure:
    figure = make_subplots(
        rows=2, cols=2,
        subplot_titles=("0 bps CAGR", "5 bps CAGR", "0 bps Sharpe", "5 bps Sharpe"),
        horizontal_spacing=0.1, vertical_spacing=0.13,
    )
    cagr_min, cagr_max = results["cagr_pct"].min(), results["cagr_pct"].max()
    sharpe_min, sharpe_max = results["sharpe"].min(), results["sharpe"].max()
    for column, cost in enumerate((0.0, 5.0), start=1):
        current = results[results["cost_bps"].eq(cost)]
        for row_number, metric, zmin, zmax, colorscale in (
            (1, "cagr_pct", cagr_min, cagr_max, "Viridis"),
            (2, "sharpe", sharpe_min, sharpe_max, "Cividis"),
        ):
            matrix = current.pivot(
                index="sell_threshold", columns="buy_threshold", values=metric
            ).sort_index()
            figure.add_trace(
                go.Heatmap(
                    x=matrix.columns, y=matrix.index, z=matrix.to_numpy(),
                    zmin=zmin, zmax=zmax, colorscale=colorscale,
                    colorbar={"title": "CAGR %" if metric == "cagr_pct" else "Sharpe"},
                    hovertemplate="买线 %{x:.2f}<br>卖线 %{y:.2f}<br>值 %{z:.3f}<extra></extra>",
                    showscale=column == 2,
                    meta={"panel": "heatmap", "metric": metric, "cost_bps": cost},
                ),
                row=row_number, col=column,
            )
    figure.update_xaxes(title_text="买入阈值")
    figure.update_yaxes(title_text="卖出阈值")
    figure.update_layout(template="plotly_white", height=900)
    return figure


def selected_case_map(surface: dict[str, Any]) -> dict[str, dict[str, Any]]:
    selected = {
        "ANCHOR": surface["parent_anchor"],
        "MAX_CAGR": surface["mechanical_max_cagr"],
        "MAX_SHARPE": surface["mechanical_max_sharpe"],
    }
    if surface["stable_representative"] is not None:
        selected["STABLE"] = surface["stable_representative"]
    unique: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for role, record in selected.items():
        if record["case_id"] in seen:
            continue
        seen.add(record["case_id"])
        unique[role] = record
    return unique


def case_label(record: dict[str, Any]) -> str:
    return f"买{record['buy_threshold']:.2f} / 卖{record['sell_threshold']:.2f}"


def performance_figure(block: Path, selected: dict[str, dict[str, Any]]) -> go.Figure:
    state = np.load(block / "daily_signal_state.npz")
    dates = pd.to_datetime(state["dates"])
    case_ids = list(state["case_ids"].astype(str))
    benchmark = pd.read_csv(block / "buy_hold_daily.csv", parse_dates=["date"])
    sma = pd.read_csv(block / "sma200_daily.csv", parse_dates=["date"])
    series: list[tuple[str, str, pd.DatetimeIndex | pd.Series, np.ndarray]] = [
        ("BUY_HOLD", "Buy & Hold", benchmark["date"], benchmark["equity"].to_numpy(float)),
        ("SMA200", "SMA200 ±3%", sma["date"], sma["equity"].to_numpy(float)),
    ]
    for role, record in selected.items():
        index = case_ids.index(str(record["case_id"]))
        series.append((role, case_label(record), dates, state["actual_equity"][index]))
    figure = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.07,
        row_heights=[0.7, 0.3], subplot_titles=("账户净值", "回撤"),
    )
    for key, label, x, equity in series:
        visible: bool | str = key in {"BUY_HOLD", "ANCHOR", "STABLE", "MAX_CAGR"}
        if not visible:
            visible = "legendonly"
        meta = {
            "series_key": key.lower(), "panel": "equity", "label": label,
            "is_benchmark": key == "BUY_HOLD", "cost_bps": 5,
        }
        figure.add_trace(
            go.Scatter(
                x=x, y=equity, mode="lines", name=label,
                line={"color": COLORS[key], "width": 1.8}, visible=visible, meta=meta,
            ), row=1, col=1,
        )
        drawdown = equity / np.maximum.accumulate(equity) - 1
        figure.add_trace(
            go.Scatter(
                x=x, y=drawdown * 100, mode="lines", showlegend=False,
                line={"color": COLORS[key], "width": 1.1}, visible=visible,
                meta={"series_key": key.lower(), "panel": "drawdown", "label": label},
            ), row=2, col=1,
        )
    figure.update_layout(template="plotly_white", height=730, hovermode="x unified", dragmode="pan")
    figure.update_yaxes(title_text="美元", row=1, col=1)
    figure.update_yaxes(title_text="%", row=2, col=1)
    return figure


def market_figure(block: Path, selected: dict[str, dict[str, Any]]) -> go.Figure:
    indicator = pd.read_csv(block / "indicator_daily.csv", parse_dates=["date"])
    orders = pd.read_csv(block / "orders.csv", parse_dates=["date"])
    figure = go.Figure()
    figure.add_trace(go.Scatter(
        x=indicator["date"], y=indicator["close"], mode="lines", name="QQQ Close",
        line={"color": "#111827", "width": 1.4},
        meta={"series_key": "qqq_close", "panel": "price", "label": "QQQ Close"},
    ))
    for role, record in selected.items():
        current_case = orders[orders["case_id"].eq(record["case_id"])]
        for side, symbol in (("buy", "triangle-up"), ("sell", "triangle-down")):
            current = current_case[current_case["type"].eq(side)]
            figure.add_trace(go.Scatter(
                x=current["date"], y=current["raw_fill_price"], mode="markers",
                marker={"color": COLORS[role], "size": 7, "symbol": symbol},
                name=f"{case_label(record)} {side}", visible="legendonly",
                meta={
                    "series_key": f"{role.lower()}_{side}", "panel": "market",
                    "label": f"{case_label(record)} {side}",
                },
            ))
    figure.update_layout(
        template="plotly_white", height=620, hovermode="x unified", dragmode="pan",
        xaxis_rangeslider_visible=False, yaxis_title="QQQ adjusted Close / fill price",
    )
    return figure


def result_rows(
    surface: dict[str, Any], benchmark: dict[str, Any], sma: dict[str, Any]
) -> list[tuple[str, dict[str, Any]]]:
    rows = [("Buy & Hold", benchmark), ("SMA200 ±3%", sma)]
    seen: set[str] = set()
    for label, key in (
        ("父锚点", "parent_anchor"),
        ("机械最高 CAGR", "mechanical_max_cagr"),
        ("机械最高 Sharpe", "mechanical_max_sharpe"),
        ("稳定代表", "stable_representative"),
    ):
        record = surface[key]
        if record is None or record["case_id"] in seen:
            continue
        seen.add(record["case_id"])
        rows.append((f"{label}：{case_label(record)}", record))
    return rows


def results_table(rows: list[tuple[str, dict[str, Any]]], surface: dict[str, Any]) -> str:
    body = []
    for label, record in rows:
        body.append(
            "<tr>"
            f"<td>{html.escape(label)}</td><td>{record['cagr_pct']:.3f}%</td>"
            f"<td>{record['sharpe']:.3f}</td><td>{record['max_drawdown_pct']:.2f}%</td>"
            f"<td>{int(record['order_count'])}</td><td>{record['exposure_pct']:.1f}%</td></tr>"
        )
    stable_text = (
        f"找到符合预声明门槛的内部连通区，代表点所在区域共{surface['stable_representative']['component_size']}组。"
        if surface["stable_representative"] is not None
        else "没有内部连通区同时满足至少九组和不触网格边界的预声明门槛。"
    )
    return (
        "<h2>5 bps 关键结果</h2><table><thead><tr><th>路径</th><th>CAGR</th>"
        "<th>Sharpe</th><th>最大回撤</th><th>订单</th><th>持仓率</th></tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
        f"<p>95%双指标集合包含{surface['high_performance_case_count']}组；{stable_text}</p>"
    )


def markdown_table(rows: list[tuple[str, dict[str, Any]]]) -> str:
    lines = [
        "| 路径 | CAGR | Sharpe | 最大回撤 | 订单 | 持仓率 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for label, record in rows:
        lines.append(
            f"| {label} | {record['cagr_pct']:.3f}% | {record['sharpe']:.3f} | "
            f"{record['max_drawdown_pct']:.2f}% | {int(record['order_count'])} | "
            f"{record['exposure_pct']:.1f}% |"
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
    incomplete = [
        item["block_id"] for item in record["expected_blocks"] if item["status"] != "completed"
    ]
    if incomplete:
        raise RuntimeError(f"Incomplete run blocks: {incomplete}")
    run_root = context.run_root(args.run_id)
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(parents=True, exist_ok=True)
    costs = [float(value) for value in context.config["cost_scenarios_bps_per_side"]]
    results = load_results(run_root, costs)
    expected = int(context.config["parameters"]["combination_count_per_cost"])
    if len(results) != expected * len(costs):
        raise RuntimeError("Combined result count does not match the frozen grid")
    results.to_csv(analysis_root / "results_table.csv", index=False, lineterminator="\n")
    five = results[results["cost_bps"].eq(5)].copy()
    surface = analyze_surface(five)
    five_block = run_root / "QQQ" / cost_label(5)
    metrics = json.loads((five_block / "metrics.json").read_text(encoding="utf-8"))
    selected = selected_case_map(surface)
    rows = result_rows(surface, metrics["benchmark"], metrics["sma200"])
    summary = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "purpose": "full-history exploratory threshold robustness scan; not out-of-sample validation",
        "analysis_start": metrics["analysis_start"],
        "analysis_end": metrics["analysis_end"],
        "grid_case_count_per_cost": expected,
        "five_bps": surface,
        "five_bps_benchmark": metrics["benchmark"],
        "five_bps_sma200": metrics["sma200"],
        "zero_bps_mechanical_max_cagr": json_safe(
            results[results["cost_bps"].eq(0)].sort_values(
                ["cagr_pct", "sharpe", "case_id"], ascending=[False, False, True]
            ).iloc[0].to_dict()
        ),
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    stable_sentence = (
        f"稳定代表为{case_label(surface['stable_representative'])}，所在内部连通区共"
        f"{surface['stable_representative']['component_size']}组。"
        if surface["stable_representative"] is not None
        else "没有候选通过预声明的内部九点连通区门槛，因此不命名稳定代表。"
    )
    (run_root / "report.md").write_text(
        "# QQQ 双周期 Stochastic RSI CROSS 全历史阈值网格\n\n"
        "- 阶段：2000–2026完整样本探索，不是样本外验证。\n"
        "- 网格：买线0.00–0.40、卖线0.60–1.00、步长0.01，共1,681组/成本。\n"
        "- 指标：raw StochRSI 42/100共同反向穿越，不计算K/D。\n"
        "- 成交：盘前解价；Open跳空或Open-to-Close定向触线；不使用High/Low推断。\n"
        f"- 稳定性：{stable_sentence}\n\n"
        + markdown_table(rows)
        + "\n",
        encoding="utf-8",
    )
    report = render_interactive_report(
        title="QQQ 双周期 Stochastic RSI CROSS 全历史阈值网格",
        heading="QQQ 2000–2026 · 1,681组买卖阈值",
        subtitle="StochRSI 42/100 · CROSS · 0/5 bps · 盘前解价、Open-to-Close触发",
        summary_html=results_table(rows, surface),
        notes=[
            "市场图只显示QQQ Close；候选成交点可从图例打开。",
            "买线与卖线独立扫描完整笛卡尔积；端点无信号case也保留在参数面。",
            "热力图对同一指标的0/5 bps面使用统一色阶，便于观察成本与平台形状。",
            "机械最高点只是样本内描述；只有预声明的内部连通区才能命名描述性稳定代表。",
            "等额定投是浏览器端显示情景，不修改正式策略、基准或指标。",
        ],
        figures=[
            ReportFigure("market-qqq", "QQQ Close与候选成交点", market_figure(five_block, selected), "market"),
            ReportFigure("performance-qqq", "5 bps关键路径净值与回撤", performance_figure(five_block, selected), "performance"),
            ReportFigure("threshold-surfaces", "买卖阈值参数面", heatmap_figure(results), "analysis"),
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
        "backtest/quantkit/metrics.py",
        "backtest/quantkit/reporting.py",
        "backtest/scripts/run_stochrsi_cross_threshold_grid.py",
        "backtest/scripts/analyze_stochrsi_cross_threshold_grid.py",
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
        f"# Run {args.run_id}\n\nFormal QQQ dual-StochRSI CROSS threshold grid; see report.html, report.md, analysis/, and QQQ cost blocks.\n",
        encoding="utf-8",
    )
    artifact_manifest = {
        "schema_version": 1,
        "created_at_utc": summary["created_at_utc"],
        "artifacts": {},
    }
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
    record_analysis_complete(context, args.run_id)
    print(f"Wrote {run_root / 'report.html'}")


if __name__ == "__main__":
    main()
