#!/usr/bin/env python3
"""Build the formal report for the flat-start forced-reentry grid."""

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
from plotly import colors
from plotly.subplots import make_subplots

from quantkit.experiment import block_root, load_experiment, load_run, record_analysis_complete, sha256
from quantkit.intraday_sma import IntradaySmaSpec, prepare_intraday_sma_data
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts import analyze_intraday_sma_backtest as base


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/DER/DER-v0.20a.1__26-08-13__qqq_intraday_sma_reentry_grid_2021"
DISPLAY_SIGNAL_LABELS = {
    **base.SIGNAL_LABELS,
    "SELL_FAST_DROP": "SMA25/30/35 变化率均达到 -0.20%",
    "BUY_FORCED_REENTRY": "卖出价 +R% 强制买回",
}


def build_performance_figure(
    daily: pd.DataFrame,
    benchmark: pd.DataFrame,
    *,
    display_r: float,
) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.7, 0.3],
        subplot_titles=("初次正常买入后的账户净值", "从各自峰值回撤"),
    )
    grid = sorted(daily["R_forced_rebuy_pct"].unique())
    palette = colors.sample_colorscale("Viridis", [index / (len(grid) - 1) for index in range(len(grid))])
    for r_pct, color in zip(grid, palette, strict=True):
        case = daily[daily["R_forced_rebuy_pct"] == r_pct].sort_values("date")
        key = f"strategy_r{int(r_pct):02d}"
        label = f"策略 R={r_pct:g}%"
        visible: bool | str = True if np.isclose(r_pct, display_r) else "legendonly"
        for row, values, panel, showlegend in (
            (1, case["equity"], "equity", True),
            (2, base.drawdown(case["equity"]), "drawdown", False),
        ):
            figure.add_trace(
                go.Scatter(
                    x=case["date"],
                    y=values,
                    mode="lines",
                    name=label,
                    showlegend=showlegend,
                    visible=visible,
                    line={"color": color, "width": 2.5 if np.isclose(r_pct, display_r) else 1.7},
                    meta={"series_key": key, "panel": panel, "label": label},
                ),
                row=row,
                col=1,
            )
    for row, values, panel, showlegend in (
        (1, benchmark["equity"], "equity", True),
        (2, base.drawdown(benchmark["equity"]), "drawdown", False),
    ):
        figure.add_trace(
            go.Scatter(
                x=benchmark["date"],
                y=values,
                mode="lines",
                name="QQQ 同时点 Buy & Hold",
                showlegend=showlegend,
                line={"color": "#111827", "width": 2.4, "dash": "dash"},
                meta={
                    "series_key": "buy_hold",
                    "panel": panel,
                    "label": "QQQ 同时点 Buy & Hold",
                    "is_benchmark": panel == "equity",
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
        uirevision="qqq-reentry-grid-v1",
    )
    figure.update_yaxes(title_text="USD", row=1, col=1, fixedrange=False)
    figure.update_yaxes(title_text="%", row=2, col=1, fixedrange=False)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def build_response_figure(results: pd.DataFrame, benchmark: dict[str, Any]) -> go.Figure:
    specs = (
        ("cagr_pct", "CAGR", "%", benchmark["cagr_pct"]),
        ("sharpe", "Sharpe", "", benchmark["sharpe"]),
        ("max_drawdown_pct", "最大回撤", "%", benchmark["max_drawdown_pct"]),
        ("total_return_pct", "总收益", "%", benchmark["total_return_pct"]),
        ("order_count", "成交笔数", "笔", None),
        ("exposure_pct", "持仓率", "%", 100.0),
    )
    figure = make_subplots(rows=3, cols=2, subplot_titles=tuple(item[1] for item in specs))
    for index, (column, title, unit, benchmark_value) in enumerate(specs):
        row = index // 2 + 1
        col = index % 2 + 1
        figure.add_trace(
            go.Scatter(
                x=results["R_forced_rebuy_pct"],
                y=results[column],
                mode="lines+markers",
                name=title,
                marker={"size": 8},
                line={"color": "#0f766e", "width": 2.2},
                hovertemplate=f"R=%{{x:.0f}}%<br>{title}=%{{y:.4f}}{unit}<extra></extra>",
            ),
            row=row,
            col=col,
        )
        if benchmark_value is not None:
            figure.add_hline(
                y=float(benchmark_value),
                line={"color": "#475569", "dash": "dash", "width": 1.2},
                annotation_text="Buy & Hold",
                row=row,
                col=col,
            )
        figure.update_xaxes(title_text="R 强制买回阈值（%）", dtick=1, row=row, col=col)
        figure.update_yaxes(title_text=unit, fixedrange=False, row=row, col=col)
    figure.update_layout(height=980, showlegend=False, margin={"l": 65, "r": 30, "t": 70, "b": 55})
    return figure


def build_ablation_figure(ablations: pd.DataFrame) -> go.Figure:
    disabled = ablations[ablations["disabled_signal"].notna()].copy()
    r_values = sorted(disabled["R_forced_rebuy_pct"].unique())
    signals = list(DISPLAY_SIGNAL_LABELS)
    signals = [signal for signal in signals if signal in set(disabled["disabled_signal"])]
    labels = [DISPLAY_SIGNAL_LABELS.get(signal, signal) for signal in signals]
    specs = (
        ("delta_cagr_pct_points", "去掉规则后的 CAGR 变化", "百分点", "RdBu"),
        ("delta_sharpe", "去掉规则后的 Sharpe 变化", "", "RdBu"),
    )
    figure = make_subplots(rows=1, cols=2, subplot_titles=tuple(item[1] for item in specs))
    for col, (metric, _title, unit, scale) in enumerate(specs, start=1):
        matrix = (
            disabled.pivot(index="disabled_signal", columns="R_forced_rebuy_pct", values=metric)
            .reindex(index=signals, columns=r_values)
        )
        limit = float(np.nanmax(np.abs(matrix.to_numpy(float))))
        if not np.isfinite(limit) or np.isclose(limit, 0):
            limit = 1.0
        figure.add_trace(
            go.Heatmap(
                x=r_values,
                y=labels,
                z=matrix.to_numpy(float),
                zmin=-limit,
                zmax=limit,
                zmid=0,
                colorscale=scale,
                colorbar={"title": unit, "x": 0.44 if col == 1 else 1.02},
                customdata=np.array([[signals[row]] * len(r_values) for row in range(len(signals))]),
                hovertemplate=(
                    "R=%{x:.0f}%<br>去掉 %{y}<br>变化=%{z:+.4f}"
                    + unit
                    + "<extra></extra>"
                ),
            ),
            row=1,
            col=col,
        )
        figure.update_xaxes(title_text="R 强制买回阈值（%）", dtick=1, row=1, col=col)
    figure.update_layout(height=620, margin={"l": 210, "r": 80, "t": 70, "b": 60})
    return figure


def results_table(results: pd.DataFrame) -> str:
    rows = []
    for item in results.sort_values("R_forced_rebuy_pct").itertuples(index=False):
        rows.append(
            "<tr>"
            f"<td>{item.R_forced_rebuy_pct:g}%</td>"
            f"<td>{item.final_equity:,.2f}</td>"
            f"<td>{item.total_return_pct:.2f}%</td>"
            f"<td>{item.cagr_pct:.2f}%</td>"
            f"<td>{item.sharpe:.3f}</td>"
            f"<td>{item.max_drawdown_pct:.2f}%</td>"
            f"<td>{item.exposure_pct:.2f}%</td>"
            f"<td>{int(item.order_count)}</td>"
            f"<td>{int(item.forced_reentry_count)}</td>"
            "</tr>"
        )
    return "".join(rows)


def ablation_table(ablations: pd.DataFrame, display_r: float) -> str:
    frame = ablations[np.isclose(ablations["R_forced_rebuy_pct"], display_r)].copy()
    frame["sort_key"] = frame["disabled_signal"].fillna("").map(
        {"": 0, **{signal: index + 1 for index, signal in enumerate(DISPLAY_SIGNAL_LABELS)}}
    )
    rows: list[str] = []
    for item in frame.sort_values("sort_key").itertuples(index=False):
        label = (
            "全部规则"
            if pd.isna(item.disabled_signal)
            else "去掉：" + DISPLAY_SIGNAL_LABELS.get(str(item.disabled_signal), str(item.disabled_signal))
        )
        rows.append(
            "<tr>"
            f"<td>{html.escape(label)}</td>"
            f"<td>{item.cagr_pct:.3f}%</td><td>{item.delta_cagr_pct_points:+.3f}</td>"
            f"<td>{item.sharpe:.3f}</td><td>{item.delta_sharpe:+.3f}</td>"
            f"<td>{item.max_drawdown_pct:.2f}%</td><td>{int(item.order_count)}</td>"
            "</tr>"
        )
    return "".join(rows)


def summary_html(
    summary: dict[str, Any],
    results: pd.DataFrame,
    display_orders: pd.DataFrame,
    ablations: pd.DataFrame,
    display_r: float,
) -> str:
    benchmark = summary["benchmark"]
    best_cagr = results.loc[results["cagr_pct"].idxmax()]
    best_sharpe = results.loc[results["sharpe"].idxmax()]
    return f"""
<h2>本轮改动与统一起点</h2>
<p>所有 case 均从 {summary['analysis_start']} 起保持 100,000 美元现金，直到 <strong>{summary['first_entry_date']}</strong> 收到第一笔普通买入信号（{html.escape(DISPLAY_SIGNAL_LABELS.get(summary['first_entry_signal'], summary['first_entry_signal']))}），以 ${summary['first_entry_fill']:.4f} 成交。策略和 Buy &amp; Hold 都从同一笔成交价、同一资本和同一日期开始比较，之前的空仓等待不计入绩效区间。</p>
<p>快速下跌卖出已改为：此前两天短均线平均变化为负，并且当日预挂阈值必须让 SMA25、SMA30、SMA35 <strong>各自</strong>变化率全部 ≤ -0.20%。仅扫描强制买回 R=0%…10%，步长 1%；其他参数不变。</p>
<h2>11 组 R 响应</h2>
<p>样本内最高 CAGR 是 R={best_cagr.R_forced_rebuy_pct:g}%（{best_cagr.cagr_pct:.2f}%）；样本内最高 Sharpe 是 R={best_sharpe.R_forced_rebuy_pct:g}%（{best_sharpe.sharpe:.3f}）。这两个只是全样本描述，<strong>不构成参数采用结论</strong>；是否平滑应结合下方完整曲线及邻近点判断。</p>
<table><thead><tr><th>R</th><th>期末净值</th><th>总收益</th><th>CAGR</th><th>Sharpe</th><th>最大回撤</th><th>持仓率</th><th>成交</th><th>强制买回</th></tr></thead><tbody>{results_table(results)}</tbody></table>
<h2>共同 Buy &amp; Hold 基准</h2>
<p>同期基准总收益 {benchmark['total_return_pct']:.2f}%、CAGR {benchmark['cagr_pct']:.2f}%、Sharpe {benchmark['sharpe']:.3f}、最大回撤 {benchmark['max_drawdown_pct']:.2f}%。</p>
<h2>逐条剔除诊断（R={display_r:g}% 展示表）</h2>
<p>对每个 R 都分别关闭七条规则；下表只展示预声明的 R={display_r:g}% 以便阅读，完整 R×规则变化见后方热力图。所有版本都从原策略共同首买日计绩效，去掉买入规则造成的额外空仓不会被跳过。</p>
<table><thead><tr><th>组合</th><th>CAGR</th><th>ΔCAGR</th><th>Sharpe</th><th>ΔSharpe</th><th>最大回撤</th><th>成交</th></tr></thead><tbody>{ablation_table(ablations, display_r)}</tbody></table>
<h2>R=5% 展示 case 的全部成交</h2>
<p>主 K 线的买卖标记固定展示预先声明的 R=5% case，共 {len(display_orders)} 笔；它不是根据结果挑出的最优参数。</p>
<table><thead><tr><th>日期</th><th>方向</th><th>主因</th><th>同时满足</th><th>理论阈值</th><th>实际成交</th><th>方式</th><th>当时锚点</th><th>股数</th></tr></thead><tbody>{base.trade_rows(display_orders)}</tbody></table>
"""


def markdown_report(
    summary: dict[str, Any],
    results: pd.DataFrame,
    ablations: pd.DataFrame,
    display_r: float,
    run_id: str,
) -> str:
    benchmark = summary["benchmark"]
    lines = [
        "# QQQ 初始空仓与强制买回 R 网格",
        "",
        f"- Run：`{run_id}`",
        f"- 观察起点：{summary['analysis_start']}；绩效共同起点：{summary['first_entry_date']}，共同成交价 ${summary['first_entry_fill']:.4f}",
        "- 初始：100,000 美元现金；第一笔正常买入前保持空仓，禁止无历史卖价的强制买回",
        "- 改动：C=-0.20%，且 SMA25/30/35 当日动态变化率必须全部 ≤ C；D=3 的前两日短均线平均为负条件保留",
        "- 扫描：R=0%～10%，步长 1%；R=5% 仅为预声明图表展示 case，不做参数选择",
        "",
        "| R | 总收益 | CAGR | Sharpe | 最大回撤 | 持仓率 | 成交 | 强制买回 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in results.sort_values("R_forced_rebuy_pct").itertuples(index=False):
        lines.append(
            f"| {item.R_forced_rebuy_pct:g}% | {item.total_return_pct:.2f}% | {item.cagr_pct:.2f}% | {item.sharpe:.3f} | {item.max_drawdown_pct:.2f}% | {item.exposure_pct:.2f}% | {int(item.order_count)} | {int(item.forced_reentry_count)} |"
        )
    display = ablations[np.isclose(ablations["R_forced_rebuy_pct"], display_r)].copy()
    lines.extend(
        [
            "",
            f"## 逐条剔除诊断（R={display_r:g}% 展示表）",
            "",
            "| 组合 | CAGR | ΔCAGR | Sharpe | ΔSharpe | 最大回撤 | 成交 |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for item in display.itertuples(index=False):
        label = "全部规则" if pd.isna(item.disabled_signal) else f"去掉 {DISPLAY_SIGNAL_LABELS.get(str(item.disabled_signal), str(item.disabled_signal))}"
        lines.append(
            f"| {label} | {item.cagr_pct:.3f}% | {item.delta_cagr_pct_points:+.3f} | {item.sharpe:.3f} | {item.delta_sharpe:+.3f} | {item.max_drawdown_pct:.2f}% | {int(item.order_count)} |"
        )
    lines.extend(
        [
            "",
            f"共同 Buy & Hold：总收益 {benchmark['total_return_pct']:.2f}%，CAGR {benchmark['cagr_pct']:.2f}%，Sharpe {benchmark['sharpe']:.3f}，最大回撤 {benchmark['max_drawdown_pct']:.2f}%。",
            "",
            "样本内最高 CAGR 与 Sharpe 只用于描述响应曲线；本轮没有锁定、推广或用于模拟盘的 R 参数。",
            "",
            "## 解释边界",
            "",
            "- 调整后日线 OHLC 不包含真实夜盘路径；隔夜越过按常规时段 Open，盘中按预先冻结的理论阈值，每天最多一笔。",
            "- 本轮是同一全样本上的一维参数扫描，不是样本外验证。",
            "- 逐条剔除覆盖全部 R；它用于判断规则贡献与交互，不代表看到结果后即可删除规则。",
            "- HTML 的区间重基准与定投只改变浏览器显示，不改写正式账本。",
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
    results = pd.read_csv(block / "parameter_results.csv")
    ablations = pd.read_csv(block / "ablation_results.csv")
    metrics = json.loads((block / "metrics.json").read_text(encoding="utf-8"))
    parameters = context.config["parameters"]
    display_r = float(parameters["display_R_forced_rebuy_pct"])
    display_orders = orders[np.isclose(orders["R_forced_rebuy_pct"], display_r)].copy()
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    summary: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": created_at,
        "purpose": "flat-start engineering change and full-sample one-dimensional R response; no parameter promotion",
        "analysis_start": parameters["analysis_start"],
        "analysis_end": parameters["analysis_end"],
        **metrics,
        "parameter_results": base.json_safe(results.to_dict("records")),
        "ablation_results": base.json_safe(ablations.to_dict("records")),
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(base.json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    raw = pd.read_csv(WORKSPACE_ROOT / f"data/processed/daily/{symbol}.csv", parse_dates=["date"])
    display_parameters = dict(parameters)
    display_parameters["R_forced_rebuy_pct"] = display_r
    prepared = prepare_intraday_sma_data(raw, IntradaySmaSpec.from_parameters(display_parameters))
    prices = prepared[
        (prepared["date"] >= pd.Timestamp(parameters["analysis_start"]))
        & (prepared["date"] <= pd.Timestamp(parameters["analysis_end"]))
    ].reset_index(drop=True)
    base.SIGNAL_LABELS = DISPLAY_SIGNAL_LABELS
    figures = [
        ReportFigure(
            f"market-{symbol.lower()}",
            "价格、可选均线、自动缩放变化率与 R=5% 成交原因",
            base.build_market_figure(prices, display_orders),
            "market",
        ),
        ReportFigure(
            f"performance-{symbol.lower()}",
            "11 组策略净值与共同 Buy & Hold",
            build_performance_figure(daily, benchmark, display_r=display_r),
            "performance",
        ),
        ReportFigure(
            "reentry-response",
            "R=0%…10% 完整参数响应",
            build_response_figure(results, metrics["benchmark"]),
            "generic",
        ),
        ReportFigure(
            "rule-ablation-response",
            "逐条去掉规则后的 CAGR / Sharpe 变化",
            build_ablation_figure(ablations),
            "generic",
        ),
    ]
    (run_root / "report.md").write_text(
        markdown_report(summary, results, ablations, display_r, args.run_id), encoding="utf-8"
    )
    report = render_interactive_report(
        title="QQQ 初始空仓与强制买回 R 网格",
        heading="QQQ 初始空仓与强制买回 R 网格",
        subtitle=f"第一笔正常买入后才开始比较；SMA25/30/35 各自变化率均需 ≤ -0.20%；R=0%…10%，步长 1%。",
        summary_html=summary_html(summary, results, display_orders, ablations, display_r),
        notes=[
            "日变化副图会按当前可见时间段和已勾选曲线单独缩放纵轴；主图价格轴也继续随可见 K 线缩放。",
            "主图保留 SMA25/30/35、三线平均、SMA70–450（间隔 10）和 R=5% case 的逐笔原因，均可勾选。",
            "净值图默认显示预声明的 R=5% 与共同 Buy & Hold；其余 R case 可逐条勾选，区间左端对齐与定投只做浏览器端情景显示。",
            "样本内最佳点不是采用参数；后续需要结合邻点平滑性并另做走步或锁定样本外验证。",
            "逐条剔除覆盖每个 R 与七条规则；正向变化只形成待验证假设，不直接回写策略。",
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
        "backtest/scripts/run_intraday_sma_reentry_grid.py",
        "backtest/scripts/analyze_intraday_sma_reentry_grid.py",
        "backtest/scripts/smoke_report_ui.mjs",
        f"{template_path}/page.html",
        f"{template_path}/styles.css",
        f"{template_path}/interactions.js",
        "data/processed/manifest.json",
        f"data/processed/daily/{symbol}.csv",
    ]
    provenance: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": created_at,
        "software": {"python": platform.python_version(), "lib_pybroker": "1.2.12", "plotly": plotly.__version__},
        "source_files": {},
    }
    for relative in tracked:
        path = WORKSPACE_ROOT / relative
        provenance["source_files"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    (run_root / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (run_root / "README.md").write_text(
        f"""# Run {args.run_id}

This immutable run evaluates all 11 flat-start forced-reentry cases in `experiment_snapshot.json`.

- `report.html` / `report.md`: interactive and compact human reports.
- `analysis/summary.json`: machine-readable grid and benchmark summary.
- `QQQ/cost_0bps/`: every case's PyBroker/reference ledger, orders, trades, plans, metrics, and hashes.
- `provenance.json`: exact source, dependency, template, manifest, and price-data hashes.
- `validation.json`: mandatory tests, audit, hash checks, and real-browser evidence.
""",
        encoding="utf-8",
    )
    artifact_manifest: dict[str, Any] = {"schema_version": 1, "created_at_utc": created_at, "artifacts": {}}
    for path in sorted(run_root.rglob("*")):
        if path.is_file() and path.name not in {"artifact_manifest.json", "run.json", "validation.json"}:
            relative = str(path.relative_to(run_root))
            artifact_manifest["artifacts"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    (run_root / "artifact_manifest.json").write_text(
        json.dumps(artifact_manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    record_analysis_complete(context, args.run_id)
    print(f"Wrote {run_root / 'report.html'}")


if __name__ == "__main__":
    main()
