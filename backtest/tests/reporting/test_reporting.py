from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import plotly.graph_objects as go

from quantkit.reporting import ReportFigure, TEMPLATE_ROOT, render_interactive_report
from scripts import analyze_sma_threshold_grid as analyzer


def report_experiment() -> dict:
    return {
        "experiment_id": "report_contract_v1",
        "symbols": ["QQQ"],
        "strategy": {
            "name": "exact_test_strategy",
            "description": "A <strict> frozen strategy definition.",
            "buy_rule": "Close crosses above SMA200.",
            "sell_rule": "Close crosses below SMA200.",
            "signal_time": "Close",
            "execution_time": "next Open",
            "position_sizing": "all-in or cash",
            "plain_language": {
                "summary": "价格站上长期趋势线时持有，跌回趋势线下方时离场。",
                "buy": "收盘价由下向上突破 SMA200 后准备买入。",
                "sell": "收盘价跌回 SMA200 下方后清仓。",
                "execution": "收盘后确认信号，并在下一个交易日开盘成交。",
                "position": "只在满仓 QQQ 和全现金之间切换。",
            },
        },
        "parameters": {
            "sma_window": 200,
            "threshold_pct": 1.5,
            "selection": {"primary_metric": "Sharpe"},
        },
        "cost_scenarios_bps_per_side": [5],
        "initial_cash": 100_000,
        "financing": "disabled",
        "benchmark": "QQQ buy and hold",
        "research": {"stage": "exploratory"},
    }


class ReportingTest(unittest.TestCase):
    def test_cagr_heatmap_uses_one_scale_for_both_cost_surfaces(self) -> None:
        zero = pd.DataFrame(
            {
                "a_pct": [0, 0, 1, 1],
                "b_pct": [0, 1, 0, 1],
                "cagr_pct": [8.0, 9.0, 10.0, 11.0],
            }
        )
        five = zero.assign(cagr_pct=[7.5, 8.5, 9.5, 10.5])
        stable = {"a_pct": 1.0, "b_pct": 0.0}
        global_best = {"a_pct": 1.0, "b_pct": 1.0}
        with patch.object(analyzer, "COSTS", (0.0, 5.0), create=True):
            figure = analyzer.cagr_heatmap_figure(
                "TEST", {0.0: zero, 5.0: five}, stable, global_best
            )
        heatmaps = [trace for trace in figure.data if trace.type == "heatmap"]
        self.assertEqual(len(heatmaps), 2)
        self.assertTrue(all(trace.coloraxis == "coloraxis" for trace in heatmaps))
        self.assertEqual(figure.layout.coloraxis.cmin, 7.5)
        self.assertEqual(figure.layout.coloraxis.cmax, 11.0)

    def test_template_renders_self_contained_controls_and_metadata(self) -> None:
        market = go.Figure()
        market.add_trace(
            go.Candlestick(
                x=["2020-01-01", "2020-01-02"],
                open=[100, 101],
                high=[102, 103],
                low=[99, 100],
                close=[101, 102],
                name="Adjusted OHLC",
            )
        )
        market.add_trace(
            go.Scatter(
                x=["2020-01-01", "2020-01-02"],
                y=[100, 100.5],
                name="SMA200",
                meta={"series_key": "sma200", "panel": "market", "label": "SMA200"},
            )
        )
        market.add_trace(
            go.Scatter(
                x=["2020-01-01", "2020-01-02"],
                y=[102, 102.5],
                name="Buy threshold",
                meta={
                    "series_key": "buy_threshold",
                    "panel": "market",
                    "label": "买入阈值",
                },
            )
        )
        performance = go.Figure()
        performance.add_trace(
            go.Scatter(
                x=["2020-01-01", "2020-01-02"],
                y=[100, 101],
                name="Buy & hold",
                meta={
                    "series_key": "buy_hold_5bps",
                    "panel": "equity",
                    "label": "Buy & hold",
                    "is_benchmark": True,
                    "cost_bps": 5,
                },
            )
        )
        rendered = render_interactive_report(
            title="Test",
            heading="Test",
            subtitle="subtitle",
            summary_html="<h2>Summary</h2>",
            notes=["note"],
            figures=[
                ReportFigure("market-test", "Market", market, "market"),
                ReportFigure("performance-test", "Performance", performance, "performance"),
            ],
            experiment=report_experiment(),
            run_id="run_test",
        )
        self.assertIn('name="quant-report-template" content="interactive_research_v5"', rendered)
        self.assertIn('id="strategy-definition"', rendered)
        self.assertIn("这项策略怎么运行", rendered)
        self.assertIn("什么时候买", rendered)
        self.assertIn("什么时候卖", rendered)
        self.assertIn("信号如何变成成交", rendered)
        self.assertIn("交易对象是 QQQ", rendered)
        self.assertIn("价格站上长期趋势线时持有", rendered)
        self.assertIn("收盘后确认信号，并在下一个交易日开盘成交", rendered)
        self.assertIn("exact_test_strategy", rendered)
        self.assertIn("A &lt;strict&gt; frozen strategy definition.", rendered)
        self.assertIn("运行 run_test", rendered)
        self.assertIn("SMA 周期", rendered)
        self.assertIn("网页中查看精确规则和完整冻结参数（打印版不展开）", rendered)
        self.assertNotIn('<details open class="strategy-parameters"', rendered)
        self.assertNotIn("<pre", rendered)
        self.assertNotIn('<dl class="strategy-rules"', rendered)
        self.assertIn("@media print", rendered)
        self.assertIn(".strategy-parameters,.strategy-identity { display:none", rendered)
        self.assertLess(rendered.index('id="strategy-definition"'), rendered.index('id="summary"'))
        self.assertIn('data-action="rebase-visible"', rendered)
        self.assertIn('data-action="build-dca"', rendered)
        self.assertIn('data-series="buy_hold_5bps"', rendered)
        self.assertIn('data-series="sma200"', rendered)
        self.assertIn('data-series="buy_threshold"', rendered)
        self.assertIn("plotly.js", rendered.lower())
        self.assertIn('"y":[100,101]', rendered)
        for token in ("{{TITLE}}", "{{HEADING}}", "{{SECTIONS}}", "{{INTERACTIONS}}"):
            self.assertNotIn(token, rendered)

    def test_market_controls_group_many_optional_smas(self) -> None:
        market = go.Figure()
        market.add_trace(
            go.Scatter(
                x=["2020-01-01"],
                y=[100],
                name="SMA25",
                meta={
                    "series_key": "sma25",
                    "panel": "market",
                    "label": "SMA25",
                    "control_group": "core_sma",
                    "control_group_label": "短均线",
                },
            )
        )
        market.add_trace(
            go.Scatter(
                x=["2020-01-01"],
                y=[90],
                name="SMA70",
                visible="legendonly",
                meta={
                    "series_key": "sma70",
                    "panel": "market",
                    "label": "SMA70",
                    "control_group": "long_sma",
                    "control_group_label": "长期均线",
                },
            )
        )
        performance = go.Figure(
            go.Scatter(
                x=["2020-01-01"],
                y=[100],
                meta={
                    "series_key": "benchmark",
                    "panel": "equity",
                    "label": "Benchmark",
                    "is_benchmark": True,
                },
            )
        )
        rendered = render_interactive_report(
            title="Grouped",
            heading="Grouped",
            subtitle="test",
            summary_html="test",
            notes=[],
            figures=[
                ReportFigure("market-test", "Market", market, "market"),
                ReportFigure("performance-test", "Performance", performance, "performance"),
            ],
            experiment=report_experiment(),
            run_id="run_test",
        )
        self.assertIn("长期均线（展开逐条选择）", rendered)
        self.assertIn('data-market-group="long_sma"', rendered)
        self.assertNotIn('data-series="sma70" data-market-group="long_sma" checked', rendered)

    def test_performance_controls_honor_initial_trace_visibility(self) -> None:
        performance = go.Figure()
        performance.add_trace(go.Scatter(
            x=["2020-01-01"], y=[100], visible="legendonly",
            meta={"series_key": "r0", "panel": "equity", "label": "R=0%"},
        ))
        performance.add_trace(go.Scatter(
            x=["2020-01-01"], y=[100],
            meta={"series_key": "benchmark", "panel": "equity", "label": "Benchmark", "is_benchmark": True},
        ))
        rendered = render_interactive_report(
            title="Grid", heading="Grid", subtitle="test", summary_html="test", notes=[],
            figures=[ReportFigure("performance-test", "Performance", performance, "performance")],
            experiment=report_experiment(), run_id="run_test",
            template_id="interactive_research_v3",
        )
        self.assertIn('id="strategy-definition"', rendered)
        self.assertLess(rendered.index('id="strategy-definition"'), rendered.index('id="summary"'))
        self.assertIn('data-series="r0">', rendered)
        self.assertNotIn('data-series="r0" checked', rendered)
        self.assertIn('data-series="benchmark" checked', rendered)

    @unittest.skipUnless(shutil.which("node"), "Node is required for JavaScript contract test")
    def test_javascript_dca_and_rebase_math(self) -> None:
        script_path = TEMPLATE_ROOT / "interactive_research_v5/interactions.js"
        program = f"""
const api = require({json.dumps(str(script_path))});
const dates = ['2020-01-01', '2020-01-02', '2020-01-03', '2020-01-04'];
const result = api.calculateDca(dates, [100, 110, 121, 133.1], null, 2, 0);
if (result.scheduleCount !== 2 || Math.abs(result.budget - 100) > 1e-12) process.exit(2);
if (Math.abs(result.equity[0] - 100) > 1e-12) process.exit(3);
if (Math.abs(result.equity[3] - 116.55) > 1e-9) process.exit(4);
const rebased = api.rebaseValues([100, 120], 100, 200);
if (rebased[0] !== 200 || rebased[1] !== 240) process.exit(5);
if (api.evenlySpacedPositions(10, 3).join(',') !== '0,5,9') process.exit(6);
const axis = api.paddedRange([-0.21, -0.19, 0.03], true);
if (!(axis[0] < -0.21 && axis[1] > 0.03 && axis[1] < 0.08)) process.exit(7);
const visibleRange = api.graphRange({{_fullLayout: {{xaxis: {{range: ['full-a', 'full-b']}}, xaxis2: {{range: ['view-a', 'view-b']}}}}}});
if (visibleRange.join(',') !== 'view-a,view-b') process.exit(8);
const closeTrace = {{x: dates, y: [90, 100, 110, 120], meta: {{panel: 'price'}}}};
const selectedPrices = api.visibleMarketPrices(closeTrace, ['2020-01-02', '2020-01-03']);
if (selectedPrices.join(',') !== '100,110') process.exit(9);
if (api.marketPriceTrace({{data: [closeTrace]}}) !== closeTrace) process.exit(10);
"""
        completed = subprocess.run(
            [shutil.which("node"), "-e", program],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
