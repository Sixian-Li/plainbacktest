#!/usr/bin/env python3
"""Analyze and render the 12-security Strategy1 gate factorial experiment."""

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
    "experiments/ROT/ROT-v0.50a.3__26-08-27__nasdaq100_strategy1_gate_factorial"
)
SYMBOL = "NASDAQ100_STRATEGY1_GATE_FACTORIAL"
METRICS = [
    "cagr_pct", "sharpe", "holding_period_cagr_pct", "holding_return_sharpe",
    "max_drawdown_pct", "holding_time_pct",
]
CASE_ORDER = [
    f"STOP{stop}_STRICT{strict}_FAST{fast}"
    for stop in (0, 1) for strict in (0, 1) for fast in (0, 1)
]


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


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(json_safe(payload), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _fmt(value: object, digits: int = 2, suffix: str = "") -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{number:.{digits}f}{suffix}" if np.isfinite(number) else "—"


def case_label(case_id: str) -> str:
    stop, strict, fast = [int(part[-1]) for part in case_id.split("_")]
    enabled = [name for name, value in (("止损", stop), ("严格买", strict), ("快卖", fast)) if value]
    return "基础90门禁" if not enabled else "+".join(enabled)


def factor_effects(metrics: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    definitions = [
        ("mechanical_stop", ["security_id", "strict_entry", "fast_exit"]),
        ("strict_entry", ["security_id", "mechanical_stop", "fast_exit"]),
        ("fast_exit", ["security_id", "mechanical_stop", "strict_entry"]),
    ]
    for factor, keys in definitions:
        off = metrics[metrics[factor].astype(int).eq(0)].set_index(keys)
        on = metrics[metrics[factor].astype(int).eq(1)].set_index(keys)
        paired = on[METRICS] - off[METRICS]
        for metric in METRICS:
            values = paired[metric].dropna().astype(float)
            rows.append({
                "factor": factor,
                "metric": metric,
                "pair_count": len(values),
                "median_on_minus_off": float(values.median()),
                "mean_on_minus_off": float(values.mean()),
                "positive_pair_pct": float((values > 0).mean() * 100.0),
            })
    return pd.DataFrame(rows)


def summary_table(case_summary: pd.DataFrame, effects: pd.DataFrame) -> str:
    ordered = case_summary.set_index("case_id").loc[CASE_ORDER].reset_index()
    rows = []
    for item in ordered.itertuples(index=False):
        rows.append(
            "<tr>"
            f"<td>{html.escape(case_label(item.case_id))}</td>"
            f"<td>{_fmt(item.cagr_pct, 2, '%')}</td>"
            f"<td>{_fmt(item.holding_period_cagr_pct, 2, '%')}</td>"
            f"<td>{_fmt(item.holding_return_sharpe, 3)}</td>"
            f"<td>{_fmt(item.max_drawdown_pct, 2, '%')}</td>"
            f"<td>{_fmt(item.holding_time_pct, 1, '%')}</td>"
            "</tr>"
        )
    effect_rows = []
    names = {
        "mechanical_stop": "5%机械止损",
        "strict_entry": "双周期>0.20严格入场",
        "fast_exit": "StochRSI100下穿0.80快速退出",
    }
    for factor in names:
        subset = effects[effects["factor"].eq(factor)].set_index("metric")
        effect_rows.append(
            "<tr>"
            f"<td>{names[factor]}</td>"
            f"<td>{_fmt(subset.loc['holding_period_cagr_pct', 'median_on_minus_off'], 2, 'pp')}</td>"
            f"<td>{_fmt(subset.loc['cagr_pct', 'median_on_minus_off'], 2, 'pp')}</td>"
            f"<td>{_fmt(subset.loc['max_drawdown_pct', 'median_on_minus_off'], 2, 'pp')}</td>"
            f"<td>{_fmt(subset.loc['holding_time_pct', 'median_on_minus_off'], 2, 'pp')}</td>"
            "</tr>"
        )
    return f"""
<div class="summary-grid">
  <div class="summary-card"><h3>这次确实是完整交叉</h3><p>12只证券 × 8个开关组合 = 96条零成本主路径；同样96条再以5bps复算。每个组合都与同一证券、同一十年窗口配对。</p></div>
  <div class="summary-card"><h3>结果如何读</h3><p>下表先看十二只证券的横截面中位数。因样本本身按历史持仓CAGR挑选，高中低三组只能用于机制压力测试，不能解释成样本外收益。</p></div>
</div>
<h3>八个组合的十二证券中位数（0 bps）</h3>
<div class="table-wrap"><table class="metrics-table"><thead><tr><th>组合</th><th>日历CAGR</th><th>持仓CAGR</th><th>持仓Sharpe</th><th>最大回撤</th><th>持仓时间</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<h3>三项开关的配对主效应中位数（开启减关闭）</h3>
<div class="table-wrap"><table class="metrics-table"><thead><tr><th>开关</th><th>持仓CAGR</th><th>日历CAGR</th><th>最大回撤</th><th>持仓时间</th></tr></thead><tbody>{''.join(effect_rows)}</tbody></table></div>
"""


def case_figure(case_summary: pd.DataFrame) -> go.Figure:
    ordered = case_summary.set_index("case_id").loc[CASE_ORDER].reset_index()
    figure = go.Figure()
    for metric, label, color in (
        ("holding_period_cagr_pct", "持仓CAGR中位数", "#2563eb"),
        ("cagr_pct", "日历CAGR中位数", "#f97316"),
    ):
        figure.add_trace(go.Bar(
            x=[case_label(value) for value in ordered["case_id"]],
            y=ordered[metric], name=label, marker_color=color,
        ))
    figure.update_layout(barmode="group", height=560, yaxis_title="%", hovermode="x unified")
    return figure


def security_heatmap(metrics: pd.DataFrame) -> go.Figure:
    ordered_symbols = (
        metrics[["security_id", "display_ticker", "sample_group"]].drop_duplicates()
        .assign(group_order=lambda x: x["sample_group"].map({"HIGH": 0, "MEDIAN": 1, "LOW": 2}))
        .sort_values(["group_order", "display_ticker"])
    )
    pivot = metrics.pivot(index="security_id", columns="case_id", values="holding_period_cagr_pct")
    pivot = pivot.reindex(index=ordered_symbols["security_id"], columns=CASE_ORDER)
    labels = [
        f"{row.display_ticker} · {row.sample_group}" for row in ordered_symbols.itertuples(index=False)
    ]
    figure = go.Figure(go.Heatmap(
        z=pivot.to_numpy(float), x=[case_label(value) for value in CASE_ORDER], y=labels,
        colorscale="RdBu", zmid=0, colorbar={"title": "持仓CAGR %"},
        hovertemplate="证券 %{y}<br>组合 %{x}<br>持仓CAGR %{z:.2f}%<extra></extra>",
    ))
    figure.update_layout(height=670, xaxis_title="完整交叉组合")
    return figure


def group_figure(group_summary: pd.DataFrame) -> go.Figure:
    figure = go.Figure()
    for group, color in (("HIGH", "#16a34a"), ("MEDIAN", "#2563eb"), ("LOW", "#dc2626")):
        subset = group_summary[group_summary["sample_group"].eq(group)].set_index("case_id").loc[CASE_ORDER]
        figure.add_trace(go.Scatter(
            x=[case_label(value) for value in CASE_ORDER],
            y=subset["holding_period_cagr_pct"], mode="lines+markers", name=group,
            line={"color": color, "width": 2.5},
        ))
    figure.update_layout(height=520, yaxis_title="组内持仓CAGR中位数 %", hovermode="x unified")
    return figure


def factor_figure(effects: pd.DataFrame) -> go.Figure:
    names = {
        "mechanical_stop": "5%止损", "strict_entry": "严格入场", "fast_exit": "快速退出",
    }
    subset = effects[effects["metric"].isin(["holding_period_cagr_pct", "max_drawdown_pct", "holding_time_pct"])]
    figure = go.Figure()
    for metric, label, color in (
        ("holding_period_cagr_pct", "持仓CAGR变化", "#2563eb"),
        ("max_drawdown_pct", "最大回撤变化", "#16a34a"),
        ("holding_time_pct", "持仓时间变化", "#f97316"),
    ):
        rows = subset[subset["metric"].eq(metric)].set_index("factor").loc[list(names)]
        figure.add_trace(go.Bar(
            x=[names[value] for value in rows.index], y=rows["median_on_minus_off"],
            name=label, marker_color=color,
        ))
    figure.add_hline(y=0, line_color="#111827", line_width=1)
    figure.update_layout(barmode="group", height=500, yaxis_title="开启减关闭（百分点）")
    return figure


def representative_performance_figure(daily: pd.DataFrame) -> go.Figure:
    """Show all eight SBUX cases beside a same-window buy-and-hold baseline."""

    selected = daily[daily["display_ticker"].eq("SBUX")].copy()
    if selected["case_id"].nunique() != 8:
        raise AssertionError("SBUX representative ledger must contain all eight cases")
    figure = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.07,
        row_heights=[0.70, 0.30], subplot_titles=("SBUX八组合净值", "从各自峰值回撤"),
    )
    colors = ["#2563eb", "#0f766e", "#f97316", "#7c3aed", "#16a34a", "#dc2626", "#0891b2", "#a16207"]
    for case_id, color in zip(CASE_ORDER, colors):
        frame = selected[selected["case_id"].eq(case_id)].sort_values("date", kind="stable")
        equity = frame["equity"].astype(float)
        drawdown = (equity / equity.cummax() - 1.0) * 100.0
        label = case_label(case_id)
        meta = {
            "series_key": case_id.lower(), "label": label, "cost_bps": 0,
            "is_benchmark": False,
        }
        figure.add_trace(go.Scatter(
            x=frame["date"], y=equity, mode="lines", name=label,
            line={"color": color, "width": 1.5}, meta={**meta, "panel": "equity"},
        ), row=1, col=1)
        figure.add_trace(go.Scatter(
            x=frame["date"], y=drawdown, mode="lines", name=label, showlegend=False,
            line={"color": color, "width": 1.0}, meta={**meta, "panel": "drawdown"},
        ), row=2, col=1)
    anchor = selected[selected["case_id"].eq(CASE_ORDER[0])].sort_values("date", kind="stable")
    buy_hold = float(anchor.iloc[0]["equity"]) * anchor["close"].astype(float) / float(anchor.iloc[0]["close"])
    buy_hold_drawdown = (buy_hold / buy_hold.cummax() - 1.0) * 100.0
    benchmark_meta = {
        "series_key": "sbux_buy_hold", "label": "SBUX同期持有", "cost_bps": 0,
        "is_benchmark": True,
    }
    for row, values, panel, showlegend in (
        (1, buy_hold, "equity", True), (2, buy_hold_drawdown, "drawdown", False),
    ):
        figure.add_trace(go.Scatter(
            x=anchor["date"], y=values, mode="lines", name="SBUX同期持有",
            showlegend=showlegend, line={"color": "#111827", "width": 2.2, "dash": "dash"},
            meta={**benchmark_meta, "panel": panel},
        ), row=row, col=1)
    figure.update_layout(height=780, hovermode="x unified", dragmode="pan")
    figure.update_yaxes(title_text="账户净值", row=1, col=1)
    figure.update_yaxes(title_text="%", row=2, col=1)
    return figure


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    record = load_run(context, args.run_id)
    if record.get("status") == "running":
        assert_run_writable(context, args.run_id)
    elif record.get("status") != "completed_unvalidated":
        raise RuntimeError(f"analysis cannot be repaired from {record.get('status')!r}")
    if any(block.get("status") != "completed" for block in record["expected_blocks"]):
        raise RuntimeError("all cost blocks must complete before analysis")
    run_root = context.run_root(args.run_id)
    blocks = {}
    for cost in (0.0, 5.0):
        root = run_root / SYMBOL / f"cost_{cost:g}bps"
        blocks[cost] = pd.read_csv(root / "metrics.csv")
    daily_primary = pd.read_csv(
        run_root / SYMBOL / "cost_0bps/daily.csv.gz", parse_dates=["date"]
    )
    primary = blocks[0.0]
    if len(primary) != 96 or primary["case_id"].nunique() != 8 or primary["security_id"].nunique() != 12:
        raise AssertionError("formal 12 × 8 primary matrix is incomplete")
    case_summary = primary.groupby("case_id", sort=False)[METRICS].median().reindex(CASE_ORDER).reset_index()
    group_summary = primary.groupby(["sample_group", "case_id"], sort=False)[METRICS].median().reset_index()
    effects = factor_effects(primary)
    cost_effects = blocks[5.0].set_index(["security_id", "case_id"])[METRICS] - primary.set_index(
        ["security_id", "case_id"]
    )[METRICS]
    cost_summary = cost_effects.median().rename("median_5bps_minus_0bps").reset_index()

    analysis = run_root / "analysis"
    analysis.mkdir(exist_ok=True)
    primary.to_csv(analysis / "all_paths_0bps.csv", index=False, lineterminator="\n")
    blocks[5.0].to_csv(analysis / "all_paths_5bps.csv", index=False, lineterminator="\n")
    case_summary.to_csv(analysis / "case_medians_0bps.csv", index=False, lineterminator="\n")
    group_summary.to_csv(analysis / "group_case_medians_0bps.csv", index=False, lineterminator="\n")
    effects.to_csv(analysis / "paired_factor_effects_0bps.csv", index=False, lineterminator="\n")
    cost_summary.to_csv(analysis / "cost_sensitivity_medians.csv", index=False, lineterminator="\n")
    baseline = case_summary.set_index("case_id").loc["STOP0_STRICT0_FAST0"]
    best_holding = case_summary.sort_values(
        ["holding_period_cagr_pct", "case_id"], ascending=[False, True]
    ).iloc[0]
    summary = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "data_status": "candidate_pending_review",
        "security_count": 12,
        "case_count": 8,
        "primary_path_count": 96,
        "cost_path_count": 192,
        "baseline_case_median": baseline.to_dict(),
        "highest_median_holding_cagr_case": best_holding.to_dict(),
        "paired_factor_effects": effects.to_dict("records"),
        "selection_bias_warning": "sample groups were selected using parent full-history holding CAGR",
    }
    write_json(analysis / "summary.json", summary)

    report = render_interactive_report(
        title="Nasdaq-100个股 Strategy1-90 三因子交叉消融",
        heading="更稳健地买、更果断地卖，能否跨强中弱个股改善90%门禁",
        subtitle=(
            f"12只冻结样本 · 8个完整交叉组合 · 96条零成本主路径 · "
            f"最高持仓CAGR中位数组合：{case_label(str(best_holding['case_id']))}"
        ),
        summary_html=summary_table(case_summary, effects),
        notes=[
            "三项规则是完整2×2×2交叉，不是三个互相独立的单因素实验；因此主效应来自同证券、同窗口、另外两项开关相同的配对差。",
            "严格入场只决定空仓时能否买入，不会因为指标后来低于0.20而卖出；快速退出只认StochRSI100由不低于0.80到低于0.80的真实向下穿越。",
            "5%止损从买入后的下一交易日才生效；跳空跌破按开盘成交，日内触及才按止损位成交。止损锁和快速退出锁分别在母仓位严格低于10%和20%时解除。",
            "十年窗口按每只证券最后一次成分结束日回溯；选样后不再加成分资格门槛。若证券自身历史不足十年，报告使用实际可得历史且在selection.csv中明确起止日。",
            "HIGH/MEDIAN/LOW来自父实验持仓CAGR的事后分层，适合检验机制是否只对赢家有效，但不能作为样本外泛化证明。历史成分及行情仍为pending_review候选数据。",
        ],
        figures=[
            ReportFigure(
                "performance-nasdaq100_strategy1_gate_factorial",
                "SBUX八组合的净值、回撤与同期持有",
                representative_performance_figure(daily_primary),
                "performance",
            ),
            ReportFigure("case-median-factorial", "八组合的横截面中位数", case_figure(case_summary), "generic"),
            ReportFigure("security-factorial-heatmap", "每只证券在八组合下的持仓CAGR", security_heatmap(primary), "generic"),
            ReportFigure("group-factorial-paths", "强、中、弱三组是否同向", group_figure(group_summary), "generic"),
            ReportFigure("paired-main-effects", "三个开关的配对主效应", factor_figure(effects), "generic"),
        ],
        experiment=context.config,
        run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")
    (run_root / "report.md").write_text(
        f"""# Nasdaq-100个股 Strategy1-90 三因子交叉消融

- 样本：12只；每只8个完整交叉组合；0/5bps合计192条成本路径。
- 基础90门禁的十二证券持仓CAGR中位数：`{float(baseline['holding_period_cagr_pct']):.3f}%`。
- 持仓CAGR中位数最高组合：`{best_holding['case_id']}`，`{float(best_holding['holding_period_cagr_pct']):.3f}%`。
- 本实验使用事后分层样本及pending_review行情，只能作机制诊断。
""",
        encoding="utf-8",
    )
    tracked = [
        "backtest/quantkit/nasdaq100_strategy1_gate_factorial.py",
        "backtest/quantkit/nasdaq100_stochrsi_rotation.py",
        "backtest/scripts/run_nasdaq100_strategy1_gate_factorial.py",
        "backtest/scripts/analyze_nasdaq100_strategy1_gate_factorial.py",
        "backtest/scripts/smoke_nasdaq100_strategy1_gate_factorial_report.mjs",
        "backtest/quantkit/experiment.py",
        "backtest/quantkit/reporting.py",
        "backtest/scripts/validate_run.py",
        "backtest/tests/strategies/rot/test_nasdaq100_strategy1_gate_factorial.py",
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
        "software": {"python": platform.python_version(), "plotly": plotly.__version__},
        "source_files": {},
    }
    for relative in tracked:
        path = WORKSPACE_ROOT / relative
        provenance["source_files"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    write_json(run_root / "provenance.json", provenance)
    (run_root / "README.md").write_text(
        f"""# Run {args.run_id}

Formal 12-security × 8-case Strategy1 gate factorial on candidate Nasdaq-100 history.

- `report.html` / `report.pdf`: v5 interactive and printable report.
- `analysis/`: all 96 primary paths, 96 cost paths, group medians and paired factor effects.
- `{SYMBOL}/`: per-cost daily ledgers, orders, trades and metrics.
- `shared/selection.csv`: exact frozen sample and actual ten-year coverage.
- `shared/state_panel.csv.gz`: warmed mother score and raw adjusted OHLC/indicators used by every case.
""",
        encoding="utf-8",
    )
    print(f"Wrote {run_root / 'report.html'} with {len(primary)} primary paths")


if __name__ == "__main__":
    main()
