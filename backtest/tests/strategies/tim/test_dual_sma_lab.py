from __future__ import annotations

from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd
from streamlit.testing.v1 import AppTest


BACKTEST_ROOT = Path(__file__).resolve().parents[3]
PROJECT_ROOT = BACKTEST_ROOT.parent
sys.path.insert(0, str(BACKTEST_ROOT))

from dual_sma_lab.charts import build_heatmap, build_single_figure
from dual_sma_lab.core import (
    GridSpec,
    holding_return_metrics,
    run_grid,
    run_single,
)
from dual_sma_lab.data_sources import discover_approved_datasets


def synthetic_bars(rows: int = 320) -> pd.DataFrame:
    dates = pd.bdate_range("2019-01-02", periods=rows)
    index = np.arange(rows, dtype=float)
    close = 80.0 + index * 0.08 + np.sin(index / 7.0) * 5.0
    open_price = close * (1.0 + np.cos(index / 11.0) * 0.002)
    return pd.DataFrame(
        {
            "date": dates,
            "symbol": "TEST",
            "open": open_price,
            "high": np.maximum(open_price, close) + 0.8,
            "low": np.minimum(open_price, close) - 0.8,
            "close": close,
            "volume": 100_000 + index,
        }
    )


class DualSmaLabTests(unittest.TestCase):
    def test_discovery_uses_only_approved_price_products(self) -> None:
        datasets = discover_approved_datasets(PROJECT_ROOT)
        for symbol in ("AAPL", "MSFT", "NVDA", "QQQ", "SPY", "RKLB"):
            self.assertIn(symbol, datasets)
        self.assertNotIn("VOO", datasets)

    def test_single_uses_pre_window_history_for_sma_warmup(self) -> None:
        bars = synthetic_bars(80)
        start = bars.loc[30, "date"]
        result = run_single(
            bars,
            start=start,
            end=bars.loc[70, "date"],
            fast_window=3,
            slow_window=10,
            cost_bps=5,
        )
        self.assertEqual(result.effective_start, start)
        self.assertAlmostEqual(
            float(result.daily.loc[0, "slow_sma"]),
            float(bars.loc[21:30, "close"].mean()),
        )
        self.assertEqual(pd.Timestamp(result.daily.loc[0, "date"]), start)

    def test_holding_sharpe_mask_includes_exit_session(self) -> None:
        daily = pd.DataFrame(
            {
                "date": pd.bdate_range("2020-01-02", periods=5),
                "equity": [100.0, 102.0, 103.0, 99.0, 99.0],
                "is_long": [0, 1, 1, 0, 0],
                "executed": [0, 1, 0, -1, 0],
            }
        )
        metrics = holding_return_metrics(daily)
        self.assertEqual(metrics["holding_return_observations"], 3)
        self.assertAlmostEqual(metrics["holding_return_compound_pct"], -1.0)

    def test_vectorized_grid_matches_exact_single_ledger(self) -> None:
        bars = synthetic_bars()
        start = bars.loc[40, "date"]
        end = bars.loc[300, "date"]
        spec = GridSpec(1, 5, 2, 7, 11, 2)
        grid = run_grid(
            bars,
            start=start,
            end=end,
            spec=spec,
            cost_bps=7,
            chunk_size=3,
        )
        self.assertEqual(len(grid.metrics), len(spec.pairs))
        columns = (
            "final_equity",
            "total_return_pct",
            "cagr_pct",
            "sharpe",
            "max_drawdown_pct",
            "holding_sessions",
            "holding_time_pct",
            "holding_period_cagr_pct",
            "holding_return_observations",
            "holding_return_sharpe",
            "order_count",
            "closed_trade_count",
        )
        for fast, slow in spec.pairs:
            exact = run_single(
                bars,
                start=start,
                end=end,
                fast_window=fast,
                slow_window=slow,
                cost_bps=7,
            )
            row = grid.metrics[
                grid.metrics["fast_window"].eq(fast)
                & grid.metrics["slow_window"].eq(slow)
            ].iloc[0]
            for column in columns:
                expected = float(exact.metrics[column])
                actual = float(row[column])
                if np.isnan(expected):
                    self.assertTrue(np.isnan(actual), (fast, slow, column))
                else:
                    self.assertAlmostEqual(actual, expected, places=9, msg=(fast, slow, column))

    def test_grid_steps_and_role_constraint(self) -> None:
        spec = GridSpec(1, 5, 2, 3, 7, 2)
        self.assertEqual(spec.fast_values, (1, 3, 5))
        self.assertEqual(spec.slow_values, (3, 5, 7))
        self.assertNotIn((3, 3), spec.pairs)
        self.assertNotIn((5, 5), spec.pairs)
        self.assertIn((5, 7), spec.pairs)

    def test_charts_expose_strategy_benchmark_and_hover_metrics(self) -> None:
        bars = synthetic_bars(120)
        result = run_single(
            bars,
            start=bars.loc[20, "date"],
            end=bars.loc[110, "date"],
            fast_window=4,
            slow_window=12,
        )
        names = [trace.name for trace in build_single_figure(result).data]
        self.assertIn("双均线策略", names)
        self.assertIn("TEST Buy & Hold", names)
        grid = run_grid(
            bars,
            start=bars.loc[20, "date"],
            end=bars.loc[110, "date"],
            spec=GridSpec(1, 3, 1, 4, 6, 1),
        )
        heatmap = build_heatmap(grid, "持仓 CAGR")
        self.assertEqual(heatmap.data[0].type, "heatmap")
        self.assertIn("持仓 Sharpe", heatmap.data[0].hovertemplate)

    def test_streamlit_app_renders_without_generating_grid(self) -> None:
        app = AppTest.from_file(BACKTEST_ROOT / "dual_sma_lab/app.py")
        app.run(timeout=30)
        self.assertFalse(app.exception)
        self.assertEqual(app.title[0].value, "双均线策略实验台")
        self.assertTrue(any("参数热力图" in tab.label for tab in app.tabs))
        self.assertTrue(any(button.label == "生成 / 刷新热力图" for button in app.button))


if __name__ == "__main__":
    unittest.main()
