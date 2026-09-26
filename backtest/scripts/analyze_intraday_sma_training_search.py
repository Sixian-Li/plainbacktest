#!/usr/bin/env python3
"""Build the chart-first report for a broad stable training search."""

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
from quantkit.intraday_sma import prepare_intraday_sma_data
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts import analyze_intraday_sma_backtest as base
from scripts.analyze_intraday_sma_global_search import search_cloud_figure
from scripts.analyze_intraday_sma_oat_sensitivity import sensitivity_figure
from scripts.run_intraday_sma_backtest import json_safe
from scripts.run_intraday_sma_global_search import spec_from_row


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/DER/DER-v0.50a.1__26-08-14__spy_intraday_sma_training_1993_2002"


def performance_figure(
    daily: pd.DataFrame,
    benchmark: pd.DataFrame,
    selected_case_id: str,
    symbol: str,
) -> go.Figure:
    strategy = daily[daily["case_id"] == selected_case_id].sort_values("date")
    buy_hold = benchmark[benchmark["case_id"] == selected_case_id].sort_values("date")
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.7, 0.3],
        subplot_titles=("训练期账户净值", "从各自峰值回撤"),
    )
    for frame, key, label, color, dash, is_benchmark in (
        (strategy, "strategy", "稳健邻域代表", "#0f766e", "solid", False),
        (buy_hold, "buy_hold", f"{symbol} 同窗口买入持有", "#334155", "dash", True),
    ):
        for row, values, panel in (
            (1, frame["equity"], "equity"),
            (2, base.drawdown(frame["equity"]), "drawdown"),
        ):
            figure.add_trace(
                go.Scatter(
                    x=frame["date"],
                    y=values,
                    mode="lines",
                    name=label,
                    line={"color": color, "width": 2.4, "dash": dash},
                    meta={
                        "series_key": key,
                        "panel": panel,
                        "label": label,
                        "is_benchmark": is_benchmark and panel == "equity",
                        "cost_bps": 0,
                    },
                    hovertemplate=f"{label}<br>%{{x|%Y-%m-%d}}<br>%{{y:,.2f}}<extra></extra>",
                ),
                row=row,
                col=1,
            )
    figure.update_layout(
        height=770,
        hovermode="x unified",
        showlegend=False,
        margin={"l": 65, "r": 25, "t": 70, "b": 55},
        uirevision=f"{symbol.lower()}-training-search-v1",
    )
    figure.update_yaxes(title_text="USD", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="%", fixedrange=False, row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def neighborhood_figure(summary: pd.DataFrame, selected_parent_id: str) -> go.Figure:
    view = summary.sort_values("robust_joint_score", ascending=False).head(120).copy()
    view["selected"] = view["parent_id"].eq(selected_parent_id)
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=view["neighbor_cagr_min_pct"],
            y=view["neighbor_sharpe_min"],
            mode="markers",
            marker={
                "size": view["selected"].map({True: 14, False: 7}),
                "color": view["robust_joint_score"],
                "colorscale": "Viridis",
                "showscale": True,
                "colorbar": {"title": "maximin分位"},
                "symbol": view["selected"].map({True: "diamond", False: "circle"}),
            },
            customdata=view[
                [
                    "parent_id",
                    "center_cagr_pct",
                    "center_sharpe",
                    "neighbor_count",
                    "one_sided_dimension_count",
                ]
            ],
            hovertemplate=(
                "%{customdata[0]}<br>最差邻点 CAGR %{x:.3f}%<br>最差邻点 Sharpe %{y:.3f}"
                "<br>中心 %{customdata[1]:.3f}% / %{customdata[2]:.3f}"
                "<br>邻域点 %{customdata[3]}<br>单侧维度 %{customdata[4]}<extra></extra>"
            ),
        )
    )
    figure.update_layout(
        height=620,
        margin={"l": 70, "r": 70, "t": 50, "b": 65},
        showlegend=False,
    )
    figure.update_xaxes(title_text="相邻一步扰动的最差 CAGR（%）", fixedrange=False)
    figure.update_yaxes(title_text="相邻一步扰动的最差 Sharpe", fixedrange=False)
    return figure


def plateau_table(plateau: pd.DataFrame) -> go.Figure:
    view = plateau.copy()
    labels = {
        "two_sided_plateau": "双侧高原",
        "one_sided_plateau": "单侧高原",
        "narrow_or_spike": "窄区间/尖峰",
    }
    view["plateau"] = view.apply(
        lambda row: f"{float(row.plateau_min_value):g} – {float(row.plateau_max_value):g}",
        axis=1,
    )
    figure = go.Figure(
        go.Table(
            header={
                "values": ["参数", "选定值", "相连高原", "点数", "判定"],
                "fill_color": "#0f766e",
                "font": {"color": "white"},
                "align": "left",
            },
            cells={
                "values": [
                    view["parameter_label"],
                    view["baseline_value"].map(lambda value: f"{float(value):g}"),
                    view["plateau"],
                    view["plateau_point_count"],
                    view["classification"].map(labels),
                ],
                "fill_color": "#f8fafc",
                "align": "left",
                "height": 28,
            },
        )
    )
    figure.update_layout(height=510, margin={"l": 20, "r": 20, "t": 25, "b": 20})
    return figure


def parameter_text(parameters: dict[str, float]) -> str:
    center = int(parameters["F_short_sma_center"])
    spacing = int(parameters["F_short_sma_spacing"])
    windows = f"{center-spacing}/{center}/{center+spacing}"
    return (
        f"A={parameters['A_negative_days_slow']:g}、B={parameters['B_slow_sma_window']:g}、"
        f"C={parameters['C_fast_derivative_pct']:g}%、D={parameters['D_negative_days_fast']:g}、"
        f"E={parameters['E_fallback_sma_window']:g}、F=SMA{windows}、"
        f"G={parameters['G_short_recovery_below_pct']:g}%、H={parameters['H_reentry_sma_window']:g}、"
        f"L={parameters['L_cost_stop_pct']:g}%、R={parameters['R_forced_rebuy_pct']:g}%"
    )


def markdown_report(
    summary: dict[str, Any],
    plateau: pd.DataFrame,
    run_id: str,
) -> str:
    metrics = summary["selected_metrics"]
    neighborhood = summary["selected_neighborhood"]
    start = summary["analysis_start"]
    end = summary["analysis_end"]
    held_out_start = summary["held_out_start"]
    title_years = f"{start[:4]}–{end[:4]}"
    lines = [
        f"# SPY {title_years} 宽范围细步长训练选参",
        "",
        f"- Run：`{run_id}`；只使用 {start}～{end}，{held_out_start} 起使用行数为 {summary['held_out_rows_used']}。",
        f"- 搜索：{summary['search_case_count']:,} 个全域/精搜 case，{summary['unique_neighbor_case_count']:,} 个去重邻域 case，{summary['oat_case_count']:,} 个完整 OAT case。",
        f"- 稳健邻域代表：{parameter_text(summary['selected_parameters'])}。",
        "",
        "| 指标 | 稳健邻域代表 | SPY Buy & Hold |",
        "|---|---:|---:|",
        f"| 期末净值 | ${metrics['final_equity']:,.2f} | ${metrics['benchmark_final_equity']:,.2f} |",
        f"| CAGR | {metrics['cagr_pct']:.3f}% | {metrics['benchmark_cagr_pct']:.3f}% |",
        f"| Sharpe | {metrics['sharpe']:.3f} | {metrics['benchmark_sharpe']:.3f} |",
        f"| 最大回撤 | {metrics['max_drawdown_pct']:.2f}% | {metrics['benchmark_max_drawdown_pct']:.2f}% |",
        f"| 持仓率 | {metrics['exposure_pct']:.2f}% | 100.00% |",
        f"| 成交笔数 | {int(metrics['order_count'])} | 0 |",
        "",
        "## 邻域选择",
        "",
        f"- 选定中心及相邻一步扰动共 {int(neighborhood['neighbor_count'])} 点；最差邻点 CAGR {neighborhood['neighbor_cagr_min_pct']:.3f}%，最差邻点 Sharpe {neighborhood['neighbor_sharpe_min']:.3f}。",
        f"- 有 {int(neighborhood['one_sided_dimension_count'])} 个参数位于搜索边界、缺少一侧邻点；正式代表强制要求该值为 0。",
        "",
        "## 完整单参数高原",
        "",
        "| 参数 | 选定值 | 相连区间 | 点数 | 判定 |",
        "|---|---:|---:|---:|---|",
    ]
    labels = {
        "two_sided_plateau": "双侧高原",
        "one_sided_plateau": "单侧高原",
        "narrow_or_spike": "窄区间/尖峰",
    }
    for row in plateau.itertuples(index=False):
        lines.append(
            f"| {row.parameter_label} | {float(row.baseline_value):g} | "
            f"{float(row.plateau_min_value):g}–{float(row.plateau_max_value):g} | "
            f"{int(row.plateau_point_count)} | {labels[row.classification]} |"
        )
    lines.extend(
        [
            "",
            "## 解释边界",
            "",
            f"- 这是训练期选参，报告中的最高点和高原仍是样本内结果；不允许依据 {held_out_start} 起的表现回改参数。",
            "- 梯度下降不适合离散、非光滑的成交状态机；本实验使用固定种子细网格抽样、前沿变异和明确的邻域稳健目标。",
            "- 结果为零费用、零滑点；高换手会使真实可实现表现更差。复权 OHLC 近似总回报，但没有独立现金股息账本。",
            "- 报告不生成逐笔买卖表；完整订单、交易、信号计划和每日账户状态保存在机器账本。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    if len(context.config["symbols"]) != 1:
        raise ValueError("Training report expects exactly one symbol.")
    symbol = str(context.config["symbols"][0])
    record = load_run(context, args.run_id)
    incomplete = [item["block_id"] for item in record["expected_blocks"] if item["status"] != "completed"]
    if incomplete:
        raise RuntimeError(f"Cannot analyze incomplete run: {incomplete}")
    run_root = context.run_root(args.run_id)
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(parents=True, exist_ok=True)
    block = block_root(context, args.run_id, symbol, 0.0)
    screening = pd.read_csv(block / "parameter_results.csv", parse_dates=["first_entry_date"])
    neighborhood = pd.read_csv(block / "neighborhood_summary.csv")
    sensitivity = pd.read_csv(block / "sensitivity_results.csv", parse_dates=["first_entry_date"])
    plateau = pd.read_csv(block / "baseline_plateau_diagnostics.csv")
    formal = pd.read_csv(block / "formal_candidate_results.csv", parse_dates=["first_entry_date"])
    daily = pd.read_csv(block / "daily.csv", parse_dates=["date"])
    benchmark = pd.read_csv(block / "buy_hold_daily.csv", parse_dates=["date"])
    orders = pd.read_csv(block / "orders.csv", parse_dates=["date", "signal_date"])
    summary = json.loads((block / "metrics.json").read_text(encoding="utf-8"))
    title_years = f"{summary['analysis_start'][:4]}–{summary['analysis_end'][:4]}"
    held_out_start = str(summary["held_out_start"])
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    analysis_summary = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": created_at,
        **summary,
        "plateau_diagnostics": json_safe(plateau.to_dict("records")),
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(analysis_summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    selected_id = str(summary["selected_case_id"])
    selected_row = formal[formal["case_id"] == selected_id].iloc[0]
    selected_orders = orders[orders["case_id"] == selected_id]
    raw = pd.read_csv(WORKSPACE_ROOT / f"data/processed/daily/{symbol}.csv", parse_dates=["date"])
    spec = spec_from_row(selected_row)
    prices = prepare_intraday_sma_data(raw, spec)
    start = pd.Timestamp(summary["analysis_start"])
    end = pd.Timestamp(summary["analysis_end"])
    prices = prices[(prices["date"] >= start) & (prices["date"] <= end)].reset_index(drop=True)

    formal_cloud = formal.copy()
    formal_cloud["case_id"] = formal_cloud["case_id"].astype(str)
    figures = [
        ReportFigure(
            f"performance-{symbol.lower()}",
            "稳健邻域代表与 SPY Buy & Hold",
            performance_figure(daily, benchmark, selected_id, symbol),
            "performance",
        ),
        ReportFigure(
            "search-cloud",
            "二十万组训练搜索云与正式候选",
            search_cloud_figure(screening, formal_cloud),
            "generic",
        ),
        ReportFigure(
            "neighborhood-stability",
            "高分中心的一步扰动最差邻点",
            neighborhood_figure(neighborhood, str(summary["selected_neighborhood"]["parent_id"])),
            "generic",
        ),
        ReportFigure(
            "plateau-summary",
            "所选参数的完整单参数相连高原",
            plateau_table(plateau),
            "generic",
        ),
    ]
    for sweep_id, group in sensitivity.groupby("sweep_id", sort=False):
        figures.append(
            ReportFigure(
                f"sensitivity-{str(sweep_id).lower()}",
                str(group.iloc[0]["parameter_label"]),
                sensitivity_figure(group),
                "generic",
            )
        )
    figures.append(
        ReportFigure(
            f"market-{symbol.lower()}",
            "训练期价格、所选均线与成交原因",
            base.build_market_figure(
                prices,
                selected_orders,
                short_windows=spec.f_short_sma_windows,
                symbol=symbol,
            ),
            "market",
        )
    )
    selected = summary["selected_metrics"]
    narrow_count = int(plateau["classification"].eq("narrow_or_spike").sum())
    compact = (
        f"<p><strong>训练期只到 {summary['analysis_end']}</strong>，{held_out_start} 起使用 0 行。"
        f"在 {summary['search_case_count']:,} 个搜索 case 后，从完整双侧邻域中按最差相邻点的 CAGR/Sharpe 选择："
        f"<strong>{html.escape(parameter_text(summary['selected_parameters']))}</strong>。"
        f"训练期 CAGR {selected['cagr_pct']:.3f}%、Sharpe {selected['sharpe']:.3f}，"
        f"同期 SPY 为 {selected['benchmark_cagr_pct']:.3f}% / {selected['benchmark_sharpe']:.3f}。"
        f"完整单参数扫描仍有 {narrow_count} 个窄区间/尖峰维度，不能把该组合解释为全面平滑高原。</p>"
    )
    report = render_interactive_report(
        title=f"SPY {title_years} 宽范围细步长训练选参",
        heading=f"SPY {title_years} 宽范围细步长训练选参",
        subtitle=f"固定种子全域/前沿搜索，再按完整双侧邻域的最差相邻点选择稳健代表；{held_out_start} 起保持未见。",
        summary_html=compact,
        notes=[
            f"训练窗口为 {summary['analysis_start']} 至 {summary['analysis_end']}；{held_out_start} 起的数据没有进入搜索、选参或报告结论。",
            "目标函数来自离散成交状态机，不适用常规梯度下降；本实验保存所有搜索 case、邻域 case 和完整单参数曲线。",
            "机械最高 CAGR/Sharpe 只作为正式核验参照；最终代表必须具有完整双侧邻域，再按一步扰动最差 CAGR/Sharpe 的联合 maximin 选择。",
            f"完整单参数扫描中有 {narrow_count} 个窄区间/尖峰维度；报告将其显式保留为过拟合风险，不把所选组合称为全面平滑高原。",
            f"零成本结果不代表可实现收益；下一阶段只能原样冻结参数测试 {held_out_start} 起的未见期，不能再回改。",
        ],
        figures=figures,
        experiment=context.config,
        run_id=args.run_id,
        template_id=str(context.config["reporting"]["template_id"]),
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")
    (run_root / "report.md").write_text(
        markdown_report(summary, plateau, args.run_id),
        encoding="utf-8",
    )

    template_path = str(context.config["reporting"]["template_path"])
    tracked = [
        "backtest/requirements.lock",
        "backtest/quantkit/execution.py",
        "backtest/quantkit/experiment.py",
        "backtest/quantkit/intraday_sma.py",
        "backtest/quantkit/intraday_sma_search.py",
        "backtest/quantkit/oat_sensitivity.py",
        "backtest/quantkit/training_search.py",
        "backtest/quantkit/metrics.py",
        "backtest/quantkit/reporting.py",
        "backtest/scripts/run_intraday_sma_backtest.py",
        "backtest/scripts/analyze_intraday_sma_backtest.py",
        "backtest/scripts/run_intraday_sma_global_search.py",
        "backtest/scripts/analyze_intraday_sma_global_search.py",
        "backtest/scripts/run_intraday_sma_training_search.py",
        "backtest/scripts/analyze_intraday_sma_training_search.py",
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
        "software": {
            "python": platform.python_version(),
            "numba": __import__("numba").__version__,
            "lib_pybroker": "1.2.12",
            "plotly": plotly.__version__,
        },
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

This immutable run selects an SPY parameter candidate using only {summary['analysis_start']} through {summary['analysis_end']}.

- `report.html` / `report.md`: chart-first training report without a per-order table.
- `analysis/summary.json`: selected parameters, metrics and plateau diagnostics.
- `{symbol}/cost_0bps/`: all search, neighborhood, OAT and formal-ledger artifacts.
- `provenance.json` / `validation.json`: exact source and correctness evidence.
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
            artifact_manifest["artifacts"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    (run_root / "artifact_manifest.json").write_text(
        json.dumps(artifact_manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    record_analysis_complete(context, args.run_id)
    print(f"Wrote {run_root / 'report.html'}")


if __name__ == "__main__":
    main()
