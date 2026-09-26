#!/usr/bin/env python3
"""Analyze the role-constrained QQQ recovery-probation SMA grid."""

from __future__ import annotations

import argparse
import html
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly
import plotly.graph_objects as go

from quantkit.experiment import (
    assert_run_writable,
    block_root,
    load_experiment,
    load_run,
    record_analysis_complete,
    sha256,
)
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts.analyze_sma_recovery_probation_grid import (
    market_figure,
    multiple_testing_figure,
    parent_delta_figure,
    performance_figure,
    subperiod_figure,
)
from scripts.run_intraday_sma_backtest import json_safe


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = (
    BACKTEST_ROOT
    / "experiments/TIM/TIM-v0.50b.2__26-08-22__qqq_sma_recovery_probation_role_grid_2000_2015"
)


def constrained_surface_figure(
    results: pd.DataFrame,
    surface: dict[str, Any],
    representative: pd.Series,
) -> go.Figure:
    eligible = results.loc[results["identifiable"].astype(bool), "primary_metric"].astype(float)
    display = results.copy()
    display.loc[~display["role_consistent"].astype(bool), "primary_metric"] = np.nan
    pivot = (
        display.pivot(index="buy_window", columns="sell_window", values="primary_metric")
        .sort_index()
        .sort_index(axis=1)
    )
    figure = go.Figure(
        go.Heatmap(
            x=pivot.columns,
            y=pivot.index,
            z=pivot.to_numpy(float),
            colorscale="RdYlGn",
            zmin=float(eligible.quantile(0.02)),
            zmax=float(eligible.quantile(0.98)),
            colorbar={"title": "三段最差<br>CAGR %"},
            hovertemplate="卖出 SMA=%{x}<br>买入 SMA=%{y}<br>三段最差 CAGR=%{z:.4f}%<extra></extra>",
        )
    )
    cells = surface["largest_component"]["cells"]
    figure.add_trace(
        go.Scatter(
            x=[item["sell_window"] for item in cells],
            y=[item["buy_window"] for item in cells],
            mode="markers",
            marker={"size": 5, "color": "white", "opacity": 0.65},
            name="最大合法高分连通区",
            hoverinfo="skip",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=[int(representative["sell_window"])],
            y=[int(representative["buy_window"])],
            mode="markers",
            marker={
                "symbol": "star",
                "size": 17,
                "color": "#2563eb",
                "line": {"color": "white", "width": 2},
            },
            name="角色合法平台代表",
            hovertemplate="平台代表<br>卖 SMA=%{x}<br>买 SMA=%{y}<extra></extra>",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=[190],
            y=[310],
            mode="markers",
            marker={"symbol": "x", "size": 14, "color": "#d97706", "line": {"width": 2}},
            name="原 310/190 锚点",
            hovertemplate="原锚点<br>卖 SMA190<br>买 SMA310<extra></extra>",
        )
    )
    axis = sorted(results["buy_window"].unique())
    figure.add_trace(
        go.Scatter(
            x=axis[:-1],
            y=axis[1:],
            mode="lines",
            line={"color": "#64748b", "width": 1.5, "dash": "dash"},
            name="语义边界 buy=sell+10",
            hovertemplate="语义边界<br>卖 SMA=%{x}<br>买 SMA=%{y}<extra></extra>",
        )
    )
    figure.update_layout(
        height=760,
        margin={"l": 70, "r": 70, "t": 55, "b": 60},
        xaxis_title="卖出 SMA 周期（日，必须更短）",
        yaxis_title="买入 SMA 周期（日，必须更长）",
    )
    return figure


def candidate_table(
    formal: pd.DataFrame,
    comparison: pd.DataFrame,
    representative_id: str,
) -> str:
    fields = [
        "case_id",
        "role_consistent",
        "event_identifiable",
        "delta_primary_metric",
        "delta_cagr_pct",
        "delta_sharpe",
        "delta_max_drawdown_pct",
        "delta_order_count",
    ]
    selected = formal.merge(comparison[fields], on="case_id", validate="one_to_one")
    rows: list[str] = []
    for row in selected.itertuples(index=False):
        if str(row.case_id) == representative_id:
            role = "合法稳定平台代表"
        elif row.buy_window == 310 and row.sell_window == 190:
            role = "继承 310/190 锚点"
        elif row.buy_window == 200 and row.sell_window == 200:
            role = "200/200 执行对照（不参与选参）"
        else:
            reasons = str(row.selection_reason).replace("FULL_", "").replace("_CHAMPION", "冠军")
            role = reasons.replace("CAGR", "CAGR").replace("SHARPE", "Sharpe").replace("MAX_DRAWDOWN", "最浅回撤")
        eligible = bool(row.role_consistent) and bool(row.event_identifiable)
        rows.append(
            "<tr>"
            f"<td>{html.escape(role)}</td><td>{int(row.buy_window)}/{int(row.sell_window)}</td>"
            f"<td>{'是' if eligible else '否'}</td><td>{float(row.primary_metric):.3f}%</td>"
            f"<td>{float(row.cagr_pct):.3f}%</td><td>{float(row.sharpe):.3f}</td>"
            f"<td>{float(row.max_drawdown_pct):.2f}%</td><td>{int(row.order_count)}</td>"
            f"<td>{float(row.delta_primary_metric):+.3f}点</td>"
            f"<td>{float(row.delta_max_drawdown_pct):+.3f}点</td>"
            f"<td>{float(row.delta_order_count):+.0f}</td></tr>"
        )
    return (
        "<table><thead><tr><th>角色</th><th>买/卖 SMA</th><th>可参与选择</th>"
        "<th>三段最差 CAGR</th><th>全史 CAGR</th><th>Sharpe</th><th>最大回撤</th>"
        "<th>成交</th><th>较旧语义最差段变化</th><th>回撤变化</th><th>成交变化</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def markdown_report(
    run_id: str,
    selection: dict[str, Any],
    formal: pd.DataFrame,
    comparison: pd.DataFrame,
) -> str:
    representative = selection["stable_representative"]
    representative_id = str(representative["case_id"])
    anchor = selection["inherited_310_190_comparison"]
    surface = selection["surface_analysis"]
    benchmark = selection["benchmark_metrics"]
    merged = formal.merge(
        comparison[["case_id", "role_consistent", "delta_primary_metric", "delta_max_drawdown_pct", "delta_order_count"]],
        on="case_id",
        validate="one_to_one",
    )
    table = [
        "| 角色 | 买/卖 SMA | 合法 | 三段最差 CAGR | 全史 CAGR | Sharpe | 最大回撤 | 成交 | 较旧语义最差段变化 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in merged.itertuples(index=False):
        if str(row.case_id) == representative_id:
            label = "稳定平台代表"
        elif row.buy_window == 310 and row.sell_window == 190:
            label = "310/190锚点"
        elif row.buy_window == 200 and row.sell_window == 200:
            label = "200/200执行对照"
        else:
            label = str(row.selection_reason)
        table.append(
            f"| {label} | {int(row.buy_window)}/{int(row.sell_window)} | "
            f"{'是' if bool(row.role_consistent) else '否'} | {row.primary_metric:.3f}% | "
            f"{row.cagr_pct:.3f}% | {row.sharpe:.3f} | {row.max_drawdown_pct:.2f}% | "
            f"{int(row.order_count)} | {row.delta_primary_metric:+.3f}点 |"
        )
    verdict = (
        "历史诊断门禁通过，但复用探索期使它仍不能直接晋升"
        if bool(selection["promotion_eligible"])
        else "历史诊断门禁未全部通过，不冻结唯一参数"
    )
    return "\n".join(
        [
            "# QQQ 恢复试探状态机：长买入 / 短卖出角色约束搜索",
            "",
            f"> Run `{run_id}`；计算1,444格，只有买入周期长于卖出周期的703格可参与选择。",
            "",
            "## 结论",
            "",
            f"- 判定：**{verdict}**。",
            f"- 合法平台代表为买 SMA{int(representative['buy_window'])} / 卖 SMA{int(representative['sell_window'])}：三段最差 CAGR {float(representative['primary_metric']):.3f}%，全史 CAGR {float(representative['cagr_pct']):.3f}%，Sharpe {float(representative['sharpe']):.3f}，最大回撤 {float(representative['max_drawdown_pct']):.2f}%。",
            f"- 最大合法高分连通区 {int(surface['largest_component']['cell_count'])} 格，边界 {surface['largest_component']['boundary_sides'] or '无'}，结构门禁{'通过' if bool(surface['largest_component']['structural_pass']) else '未通过'}。",
            f"- 310/190 新语义仍为 CAGR {float(anchor['cagr_pct']):.3f}%、Sharpe {float(anchor['sharpe']):.3f}、回撤 {float(anchor['max_drawdown_pct']):.2f}%；相对旧语义 CAGR {float(anchor['delta_cagr_pct']):+.3f}点、Sharpe {float(anchor['delta_sharpe']):+.3f}、回撤 {float(anchor['delta_max_drawdown_pct']):+.3f}点、成交 {float(anchor['delta_order_count']):+.0f}次。",
            f"- PBO={float(selection['pbo']['pbo']):.2%}；有效试验数 DSR={float(representative['dsr_effective_probability']):.2%}。",
            f"- Buy & Hold：CAGR {float(benchmark['cagr_pct']):.3f}%，Sharpe {float(benchmark['sharpe']):.3f}，最大回撤 {float(benchmark['max_drawdown_pct']):.2f}%。",
            "",
            "## 正式候选",
            "",
            *table,
            "",
            "## 研究边界",
            "",
            "- 全部1,444格都通过双账本计算；角色相反或相等的741格只保留作诊断，不参与任何赢家或统计选择。",
            "- 平台若触及买入450、卖出80或buy=sell+10语义边界即失败；代表必须有完整合法且事件可识别的3×3邻域。",
            "- 本轮只改变选参门禁，不改变任何一笔成交，因此310/190的结果与无约束首轮完全相同。",
            "- 2000–2015及此前查看过的2016–2026都不是这套新语义的纯净样本外证据。",
        ]
    ) + "\n"


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
        raise RuntimeError(f"Cannot analyze incomplete run: {incomplete}")
    run_root = context.run_root(args.run_id)
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(parents=True, exist_ok=True)
    block = block_root(context, args.run_id, "QQQ", 5.0)

    results = pd.read_csv(block / "parameter_results.csv")
    formal = pd.read_csv(block / "formal_candidate_results.csv")
    daily = pd.read_csv(block / "daily.csv", parse_dates=["date"])
    reference_daily = pd.read_csv(block / "reference_daily.csv", parse_dates=["date"])
    benchmark = pd.read_csv(block / "buy_hold_daily.csv", parse_dates=["date"])
    comparison = pd.read_csv(block / "parent_control_comparison.csv")
    splits = pd.read_csv(block / "pbo_splits.csv")
    dsr = pd.read_csv(block / "dsr_results.csv")
    orders = pd.read_csv(block / "orders.csv", parse_dates=["date"])
    plans = pd.read_csv(block / "signal_plans.csv", parse_dates=["date"])
    selection = json.loads((block / "selection_summary.json").read_text(encoding="utf-8"))
    representative = pd.Series(selection["stable_representative"])
    representative_id = str(representative["case_id"])
    if len(results) != 1_444 or int(results["role_consistent"].sum()) != 703:
        raise AssertionError("Role-constrained run must retain 1,444 cells and exactly 703 valid role pairs.")
    if not int(representative["buy_window"]) > int(representative["sell_window"]):
        raise AssertionError("Surface representative violates the frozen SMA role constraint.")

    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    analysis_summary = {
        **selection,
        "created_at_utc": created_at,
        "formal_candidates": json_safe(formal.to_dict("records")),
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(json_safe(analysis_summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    results[["case_id", "buy_window", "sell_window", "event_identifiable", "role_consistent", "identifiable"]].to_csv(
        analysis_root / "role_eligibility.csv", index=False, lineterminator="\n"
    )
    comparison.to_csv(analysis_root / "old_new_surface_comparison.csv", index=False, lineterminator="\n")
    formal.to_csv(analysis_root / "formal_candidates.csv", index=False, lineterminator="\n")

    anchor = selection["inherited_310_190_comparison"]
    component = selection["surface_analysis"]["largest_component"]
    gate = selection["stable_representative"]
    verdict = (
        "历史门禁通过；只保留为未来研究候选"
        if bool(selection["promotion_eligible"])
        else "历史门禁未全部通过；不冻结唯一参数"
    )
    compact = (
        f"<p>全部 <strong>1,444</strong> 格都计算，只有 <strong>703</strong> 个买入周期严格长于卖出周期的组合参与选择。"
        f"合法平台代表是 <strong>买 SMA{int(representative['buy_window'])} / 卖 SMA{int(representative['sell_window'])}</strong>，"
        f"三段最差 CAGR <strong>{float(representative['primary_metric']):.3f}%</strong>，"
        f"全史 CAGR <strong>{float(representative['cagr_pct']):.3f}%</strong>，Sharpe <strong>{float(representative['sharpe']):.3f}</strong>，"
        f"最大回撤 <strong>{float(representative['max_drawdown_pct']):.2f}%</strong>。最大高分区 {int(component['cell_count'])} 格，"
        f"边界 {html.escape(str(component['boundary_sides'] or '无'))}；PBO={float(selection['pbo']['pbo']):.2%}，"
        f"有效试验数 DSR={float(gate['dsr_effective_probability']):.2%}。最终判定：<strong>{html.escape(verdict)}</strong>。</p>"
        f"<h2>310/190 没有因为加角色门禁而改变</h2><p>它的新语义 CAGR {float(anchor['cagr_pct']):.3f}%、Sharpe {float(anchor['sharpe']):.3f}、"
        f"最大回撤 {float(anchor['max_drawdown_pct']):.2f}%、成交 {int(anchor['order_count'])} 次；相对旧语义，CAGR {float(anchor['delta_cagr_pct']):+.3f}点、"
        f"Sharpe {float(anchor['delta_sharpe']):+.3f}、回撤 {float(anchor['delta_max_drawdown_pct']):+.3f}点、成交 {float(anchor['delta_order_count']):+.0f}次。</p>"
        "<h2>合法正式候选与执行对照</h2>" + candidate_table(formal, comparison, representative_id)
    )

    raw = pd.read_csv(WORKSPACE_ROOT / "data/processed/daily/QQQ.csv", parse_dates=["date"])
    anchor_formal = formal[(formal["buy_window"] == 310) & (formal["sell_window"] == 190)]
    if len(anchor_formal) != 1:
        raise AssertionError("Formal results are missing the 310/190 anchor.")
    anchor_id = str(anchor_formal.iloc[0]["case_id"])
    display_dsr = dsr.copy()
    display_dsr["trial_policy"] = display_dsr["trial_policy"].replace(
        {"all_eligible_role_cases": "全部703个合法组合"}
    )
    figures = [
        ReportFigure(
            "performance-qqq",
            "合法平台代表、310/190 与正式候选的净值和回撤",
            performance_figure(daily, benchmark, formal, representative_id),
            "performance",
        ),
        ReportFigure(
            "role-period-surface",
            "只在长买入 / 短卖出合法域内选择的稳健曲面",
            constrained_surface_figure(results, selection["surface_analysis"], representative),
            "generic",
        ),
        ReportFigure(
            "parent-deltas",
            "全部周期对相对旧 c=3%、d关闭语义的变化",
            parent_delta_figure(comparison),
            "generic",
        ),
        ReportFigure(
            "subperiod-robustness",
            "合法代表、310/190 与 200/200 执行对照的三个继承状态分段",
            subperiod_figure(formal, representative_id),
            "generic",
        ),
        ReportFigure(
            "multiple-testing",
            "仅合法且事件可识别周期对的 PBO 与 DSR",
            multiple_testing_figure(splits, display_dsr, selection["pbo"]),
            "generic",
        ),
        ReportFigure(
            "market-qqq",
            "310/190 诊断路径：试探仓、失败退出、确认多头与一次性 3% 买回",
            market_figure(
                raw,
                reference_daily[reference_daily["case_id"].astype(str).eq(anchor_id)].copy(),
                plans[plans["case_id"].astype(str).eq(anchor_id)].copy(),
                orders[orders["case_id"].astype(str).eq(anchor_id)].copy(),
                buy_window=310,
                sell_window=190,
            ),
            "market",
        ),
    ]
    report = render_interactive_report(
        title="QQQ 恢复试探状态机：长买入 / 短卖出角色约束搜索",
        heading="QQQ 恢复试探状态机：长买入 / 短卖出角色约束搜索",
        subtitle="完整计算80～450日的1,444格；703个买入周期长于卖出周期的组合参与选择。",
        summary_html=compact,
        notes=[
            "本轮只改变研究选择域，不改变成交语义；角色相反或相等的741格保留结果但不能参与冠军、平台、PBO或DSR。",
            "普通买入与失败退出由完成收盘确认、下一开盘成交；短均线卖出线和一次性3%强制买回线在盘前冻结。",
            "高分区触及买入450、卖出80或buy=sell+10语义对角线即失败；代表必须拥有完整合法且事件可识别的3×3邻域。",
            "三个分段继承现金、仓位、试探状态与c使用状态；全历史CAGR、Sharpe和最浅回撤冠军只是补充诊断。",
            "本轮继续复用2000–2015探索期，因此即使门禁通过也不是样本外证据或交易建议。",
        ],
        figures=figures,
        experiment=context.config,
        run_id=args.run_id,
        template_id=str(context.config["reporting"]["template_id"]),
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")
    (run_root / "report_print.html").write_text(report, encoding="utf-8")
    (run_root / "report.md").write_text(
        markdown_report(args.run_id, selection, formal, comparison), encoding="utf-8"
    )

    pdf_process = subprocess.run(
        [
            "node",
            "scripts/print_html_pdf.mjs",
            str(run_root / "report_print.html"),
            str(run_root / "report.pdf"),
            "这项策略怎么运行",
            "什么时候买",
            "什么时候卖",
            "信号如何变成成交",
        ],
        cwd=BACKTEST_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if pdf_process.returncode != 0:
        raise RuntimeError("PDF generation failed:\n" + "\n".join([pdf_process.stdout, pdf_process.stderr]))
    pdf_payload = json.loads(pdf_process.stdout.split("\n", 1)[1])
    (analysis_root / "pdf_print_gate.json").write_text(
        json.dumps(pdf_payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    template_path = str(context.config["reporting"]["template_path"])
    tracked = [
        "backtest/requirements.lock",
        "backtest/quantkit/execution.py",
        "backtest/quantkit/experiment.py",
        "backtest/quantkit/intraday_sma_period_cross.py",
        "backtest/quantkit/intraday_sma_period_cross_search.py",
        "backtest/quantkit/intraday_sma_threshold_selection.py",
        "backtest/quantkit/metrics.py",
        "backtest/quantkit/reporting.py",
        "backtest/quantkit/sma_recovery_probation.py",
        "backtest/quantkit/sma_recovery_probation_search.py",
        "backtest/quantkit/sma_recovery_role_selection.py",
        "backtest/scripts/run_intraday_sma_backtest.py",
        "backtest/scripts/run_intraday_sma200_threshold_grid.py",
        "backtest/scripts/run_sma_recovery_probation_grid.py",
        "backtest/scripts/run_sma_recovery_probation_role_grid.py",
        "backtest/scripts/analyze_sma_recovery_probation_grid.py",
        "backtest/scripts/analyze_sma_recovery_probation_role_grid.py",
        "backtest/scripts/print_html_pdf.mjs",
        "backtest/scripts/smoke_report_ui.mjs",
        "backtest/tests/strategies/tim/test_sma_recovery_role_selection.py",
        f"{template_path}/page.html",
        f"{template_path}/styles.css",
        f"{template_path}/interactions.js",
        "data/processed/manifest.json",
        "data/processed/daily/QQQ.csv",
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

This immutable run re-selects the QQQ recovery-probation SMA pair under the strict long-buy/short-sell role constraint.

- `report.html` / `report.pdf` / `report.md`: v5 rules-first reports.
- `analysis/role_eligibility.csv`: all 1,444 cells and the 703-cell hard role mask.
- `analysis/old_new_surface_comparison.csv`: paired deltas versus validated TIM-v0.50 c3/d-off.
- `QQQ/cost_5bps/`: full ledgers, constrained surface, PBO and DSR evidence.
- `provenance.json` / `validation.json`: source hashes and correctness gates.
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
    print(f"Wrote {run_root / 'report.pdf'}")


if __name__ == "__main__":
    main()
