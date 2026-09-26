#!/usr/bin/env python3
"""Build the formal report for the QQQ three-rule combination ablation."""

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

from quantkit.experiment import block_root, load_experiment, load_run, record_analysis_complete, sha256
from quantkit.intraday_sma import IntradaySmaSpec, prepare_intraday_sma_data
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts import analyze_intraday_sma_backtest as base


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = (
    BACKTEST_ROOT / "experiments/DER/DER-v0.20b.1__26-08-13__qqq_intraday_sma_three_rule_ablation_full_history"
)
SIGNAL_LABELS = {
    **base.SIGNAL_LABELS,
    "SELL_FAST_DROP": "SMA25/30/35 变化率均达到 -0.20%",
}


def build_performance_figure(
    daily: pd.DataFrame,
    benchmark: pd.DataFrame,
    context_daily: pd.DataFrame,
) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.7, 0.3],
        subplot_titles=("共同首买后的账户净值", "从各自峰值回撤"),
    )
    series = [
        ("combined", "本轮：同时去掉三条规则", daily, "#0f766e", True, None),
        (
            "prior_r02",
            "上一版全部规则 R=2%",
            context_daily[context_daily["case_id"] == "R02"],
            "#7c3aed",
            False,
            "dash",
        ),
        (
            "prior_r05",
            "上一版全部规则 R=5%",
            context_daily[context_daily["case_id"] == "R05"],
            "#d97706",
            False,
            "dot",
        ),
        ("buy_hold", "QQQ 同时点 Buy & Hold", benchmark, "#111827", True, "dash"),
    ]
    for key, label, frame, color, visible_by_default, dash in series:
        frame = frame.sort_values("date")
        visible: bool | str = True if visible_by_default else "legendonly"
        for row, values, panel, showlegend in (
            (1, frame["equity"], "equity", True),
            (2, base.drawdown(frame["equity"]), "drawdown", False),
        ):
            figure.add_trace(
                go.Scatter(
                    x=frame["date"],
                    y=values,
                    mode="lines",
                    name=label,
                    showlegend=showlegend,
                    visible=visible,
                    line={"color": color, "width": 2.4, **({"dash": dash} if dash else {})},
                    meta={
                        "series_key": key,
                        "panel": panel,
                        "label": label,
                        "is_benchmark": key == "buy_hold" and panel == "equity",
                        "cost_bps": 0,
                    },
                ),
                row=row,
                col=1,
            )
    figure.update_layout(
        height=780,
        margin={"l": 65, "r": 25, "t": 70, "b": 55},
        hovermode="x unified",
        showlegend=False,
        uirevision="qqq-three-rule-ablation-v1",
    )
    figure.update_yaxes(title_text="USD", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="%", fixedrange=False, row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def comparison_rows(summary: dict[str, Any], prior: pd.DataFrame) -> list[dict[str, Any]]:
    strategy = summary["strategy"]
    benchmark = summary["benchmark"]
    rows = [
        {"name": "本轮：同时去掉三条规则", **strategy},
        {"name": "QQQ 同时点 Buy & Hold", **benchmark},
    ]
    for case_id in ("R02", "R05"):
        item = prior[prior["case_id"] == case_id]
        if len(item) != 1:
            raise ValueError(f"Prior comparison case missing or duplicated: {case_id}")
        rows.append({"name": f"上一版全部规则 {case_id.replace('R0', 'R=')}%", **item.iloc[0].to_dict()})
    return rows


def comparison_table(rows: list[dict[str, Any]]) -> str:
    rendered: list[str] = []
    for item in rows:
        rendered.append(
            "<tr>"
            f"<td>{html.escape(str(item['name']))}</td>"
            f"<td>{float(item['final_equity']):,.2f}</td>"
            f"<td>{float(item['total_return_pct']):.2f}%</td>"
            f"<td>{float(item['cagr_pct']):.3f}%</td>"
            f"<td>{float(item['sharpe']):.3f}</td>"
            f"<td>{float(item['max_drawdown_pct']):.2f}%</td>"
            f"<td>{float(item['exposure_pct']):.2f}%</td>"
            f"<td>{int(item['order_count'])}</td>"
            "</tr>"
        )
    return "".join(rendered)


def summary_html(
    summary: dict[str, Any],
    rows: list[dict[str, Any]],
    orders: pd.DataFrame,
) -> str:
    metrics = summary["strategy"]
    benchmark = summary["benchmark"]
    delta_cagr = metrics["cagr_pct"] - benchmark["cagr_pct"]
    delta_sharpe = metrics["sharpe"] - benchmark["sharpe"]
    delta_dd = metrics["max_drawdown_pct"] - benchmark["max_drawdown_pct"]
    return f"""
<h2>本轮到底改了什么</h2>
<p>同时关闭三条规则：<strong>卖出价 +R% 强制买回</strong>、<strong>成本线 -1.5% 强止损</strong>、<strong>短均线连续转弱且跌破 SMA130</strong>。因此 R 不再是策略参数；A、B、L、R 只为复用同一已验证代码接口而保留，不会产生订单。</p>
<p>剩余卖出只有 SMA25/30/35 各自快速下跌与 SMA200 下穿；剩余买入只有 SMA200 上穿与短均线恢复。仍为初始空仓、全仓切换、每天最多一笔。</p>
<h2>统一比较</h2>
<p>第一笔普通买入发生在 <strong>{summary['first_entry_date']}</strong>，由 {html.escape(SIGNAL_LABELS.get(summary['first_entry_signal'], summary['first_entry_signal']))} 触发，成交价 ${summary['first_entry_fill']:.4f}。本轮策略与 Buy &amp; Hold 都从这笔成交开始；上一版 R=2% / R=5% 也采用相同起点，仅作历史上下文。</p>
<table><thead><tr><th>版本</th><th>期末净值</th><th>总收益</th><th>CAGR</th><th>Sharpe</th><th>最大回撤</th><th>持仓率</th><th>成交</th></tr></thead><tbody>{comparison_table(rows)}</tbody></table>
<p>相对 Buy &amp; Hold：CAGR {delta_cagr:+.3f} 个百分点，Sharpe {delta_sharpe:+.3f}，最大回撤改善 {delta_dd:+.2f} 个百分点（数值越接近 0 越好）。这是完整历史的组合消融，不是样本外结论。</p>
<h2>本轮全部成交</h2>
<p>共 {len(orders)} 笔成交；每一笔均在下表及 K 线 hover 中标明主触发原因和同时满足的信号。</p>
<table><thead><tr><th>日期</th><th>方向</th><th>主因</th><th>同时满足</th><th>理论阈值</th><th>实际成交</th><th>方式</th><th>当时锚点</th><th>股数</th></tr></thead><tbody>{base.trade_rows(orders)}</tbody></table>
"""


def markdown_report(
    summary: dict[str, Any],
    rows: list[dict[str, Any]],
    run_id: str,
) -> str:
    lines = [
        "# QQQ 同时去掉三条规则：完整历史组合消融",
        "",
        f"- Run：`{run_id}`",
        f"- 观察区间：{summary['analysis_start']}～{summary['analysis_end']}；绩效共同起点：{summary['first_entry_date']}，共同成交价 ${summary['first_entry_fill']:.4f}",
        "- 同时关闭：卖出价 +R% 强制买回；成本线 -1.5% 强止损；短均线连续转弱且跌破 SMA130",
        "- R 已失效，不扫描；剩余规则、动态阈值、隔夜 Open、日内理论阈值和每天最多一笔均不变",
        "",
        "| 版本 | 总收益 | CAGR | Sharpe | 最大回撤 | 持仓率 | 成交 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for item in rows:
        lines.append(
            f"| {item['name']} | {float(item['total_return_pct']):.2f}% | {float(item['cagr_pct']):.3f}% | {float(item['sharpe']):.3f} | {float(item['max_drawdown_pct']):.2f}% | {float(item['exposure_pct']):.2f}% | {int(item['order_count'])} |"
        )
    lines.extend(
        [
            "",
            "## 解释边界",
            "",
            "- 本轮一次关闭三条规则；不能把此前逐条关闭的收益变化相加来预测本轮，结果包含规则交互。",
            "- 上一版 R=2% 与 R=5% 是既有已验证 run 的上下文，不是本轮重新计算的参数候选。",
            "- 调整后日线 OHLC 不含真实夜盘路径；隔夜越过按常规时段 Open，盘中触及按预先冻结阈值。",
            "- 完整历史仍属于样本内探索，不能仅凭本结果晋级模拟盘。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    record = load_run(context, args.run_id)
    incomplete = [item["block_id"] for item in record["expected_blocks"] if item["status"] != "completed"]
    if incomplete:
        raise RuntimeError(f"Cannot analyze an incomplete run: {incomplete}")

    run_root = context.run_root(args.run_id)
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(parents=True, exist_ok=True)
    symbol = context.config["symbols"][0]
    block = block_root(context, args.run_id, symbol, 0.0)
    daily = pd.read_csv(block / "daily.csv", parse_dates=["date"])
    benchmark = pd.read_csv(block / "buy_hold_daily.csv", parse_dates=["date"])
    orders = pd.read_csv(block / "orders.csv", parse_dates=["date", "signal_date"])
    metrics = json.loads((block / "metrics.json").read_text(encoding="utf-8"))

    context_path = WORKSPACE_ROOT / context.config["context_comparison"]["relative_path"]
    prior_results_path = context_path / "parameter_results.csv"
    prior_daily_path = context_path / "daily.csv"
    prior_results = pd.read_csv(prior_results_path)
    prior_daily = pd.read_csv(prior_daily_path, parse_dates=["date"])
    rows = comparison_rows(metrics, prior_results)
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    summary: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": created_at,
        "purpose": "predeclared simultaneous removal of three rules; no parameter selection or promotion",
        **metrics,
        "comparison_rows": base.json_safe(rows),
        "context_source": context.config["context_comparison"],
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(base.json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    raw = pd.read_csv(WORKSPACE_ROOT / f"data/processed/daily/{symbol}.csv", parse_dates=["date"])
    spec = IntradaySmaSpec.from_parameters(context.config["parameters"])
    prepared = prepare_intraday_sma_data(raw, spec)
    prices = prepared[
        (prepared["date"] >= pd.Timestamp(metrics["analysis_start"]))
        & (prepared["date"] <= pd.Timestamp(metrics["analysis_end"]))
    ].reset_index(drop=True)
    base.SIGNAL_LABELS = SIGNAL_LABELS
    figures = [
        ReportFigure(
            f"market-{symbol.lower()}",
            "价格、可选均线、自动缩放变化率与成交原因",
            base.build_market_figure(prices, orders),
            "market",
        ),
        ReportFigure(
            f"performance-{symbol.lower()}",
            "本轮组合消融、上一版与共同 Buy & Hold",
            build_performance_figure(daily, benchmark, prior_daily),
            "performance",
        ),
    ]
    (run_root / "report.md").write_text(
        markdown_report(summary, rows, args.run_id), encoding="utf-8"
    )
    report = render_interactive_report(
        title="QQQ 同时去掉三条规则：完整历史组合消融",
        heading="QQQ 同时去掉三条规则：完整历史组合消融",
        subtitle="强制买回、成本止损、慢性转弱卖出同时关闭；R 不再参与策略。",
        summary_html=summary_html(summary, rows, orders),
        notes=[
            "本轮剩余卖出规则只有三条短均线同时快速下跌和 SMA200 下穿；剩余买入规则只有 SMA200 上穿和短均线恢复。",
            "主图保留 SMA25/30/35、三线平均、SMA70–450（间隔 10）与逐笔原因，均可勾选；变化率副图按可见区间独立缩放。",
            "净值图默认显示本轮与 Buy & Hold；上一版全部规则 R=2% / R=5% 可勾选，区间左端对齐与定投只改变浏览器显示。",
            "组合消融包含规则交互，不能由三项单规则消融的增量相加推导。",
        ],
        figures=figures,
        experiment=context.config,
        run_id=args.run_id,
        template_id=str(context.config["reporting"]["template_id"]),
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")

    template_path = str(context.config["reporting"]["template_path"])
    tracked = [
        "backtest/requirements.lock",
        "backtest/quantkit/execution.py",
        "backtest/quantkit/experiment.py",
        "backtest/quantkit/intraday_sma.py",
        "backtest/quantkit/metrics.py",
        "backtest/quantkit/reporting.py",
        "backtest/scripts/run_intraday_sma_backtest.py",
        "backtest/scripts/analyze_intraday_sma_backtest.py",
        "backtest/scripts/run_intraday_sma_combination_ablation.py",
        "backtest/scripts/analyze_intraday_sma_combination_ablation.py",
        "backtest/scripts/smoke_report_ui.mjs",
        f"{template_path}/page.html",
        f"{template_path}/styles.css",
        f"{template_path}/interactions.js",
        "data/processed/manifest.json",
        f"data/processed/daily/{symbol}.csv",
        str(prior_results_path.relative_to(WORKSPACE_ROOT)),
        str(prior_daily_path.relative_to(WORKSPACE_ROOT)),
    ]
    provenance: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": created_at,
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
        f"""# Run {args.run_id}

This immutable run evaluates the predeclared simultaneous removal of three signals.

- `report.html` / `report.md`: interactive and compact human reports.
- `analysis/summary.json`: machine-readable strategy, benchmark, and prior-run context.
- `QQQ/cost_0bps/`: PyBroker/reference ledgers, orders, trades, plans, metrics, and hashes.
- `provenance.json`: exact source, dependency, template, data, and context-run hashes.
- `validation.json`: mandatory tests, audit, hash checks, and real-browser evidence.
""",
        encoding="utf-8",
    )
    artifact_manifest: dict[str, Any] = {
        "schema_version": 1,
        "created_at_utc": created_at,
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
