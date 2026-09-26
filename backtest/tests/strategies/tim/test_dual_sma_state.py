from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from quantkit.dual_sma_state import (
    FAST_SMA_COLUMN,
    SLOW_SMA_COLUMN,
    DualSmaStateSpec,
    analysis_slice,
    prepare_dual_sma_data,
    run_pybroker_dual_sma,
    run_reference_dual_sma,
    valid_parameter_pairs,
)
from quantkit.execution import ExplicitFillPolicy
from quantkit.metrics import calculate_holding_period_metrics, pybroker_daily_state
from quantkit.reporting import market_controls
from scripts.analyze_aapl_dual_sma_grid import market_figure
from scripts.analyze_aapl_dual_sma_holding_cagr_grid import surface_summary
from scripts.run_aapl_dual_sma_grid import (
    configure_worker,
    precompute_smas,
    run_fast_window,
)


def bars(closes: list[float]) -> pd.DataFrame:
    dates = pd.bdate_range("2020-01-02", periods=len(closes))
    close = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "date": dates,
            "symbol": "AAPL",
            "open": close + 0.25,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": 1_000,
        }
    )


class DualSmaStateTests(unittest.TestCase):
    def test_holding_cagr_uses_post_open_position_sessions(self) -> None:
        daily = pd.DataFrame(
            {
                "date": pd.bdate_range("2020-01-02", periods=4),
                "shares": [0.0, 10.0, 10.0, 0.0],
                "equity": [100.0, 105.0, 115.0, 121.0],
            }
        )
        metrics = calculate_holding_period_metrics(
            daily,
            initial_cash=100.0,
            annual_bars=4,
        )
        self.assertEqual(metrics["holding_sessions"], 2)
        self.assertAlmostEqual(metrics["holding_time_pct"], 50.0)
        self.assertAlmostEqual(metrics["holding_period_cagr_pct"], 46.41, places=10)

    def test_zero_holding_has_undefined_holding_cagr(self) -> None:
        daily = pd.DataFrame(
            {
                "date": pd.bdate_range("2020-01-02", periods=3),
                "is_long": [0, 0, 0],
                "equity": [100.0, 100.0, 100.0],
            }
        )
        metrics = calculate_holding_period_metrics(daily, initial_cash=100.0)
        self.assertEqual(metrics["holding_sessions"], 0)
        self.assertTrue(np.isnan(metrics["holding_period_cagr_pct"]))

    def test_holding_surface_selects_holding_cagr_not_total_return(self) -> None:
        rows = []
        for fast in range(1, 4):
            for slow in range(5, 8):
                holding_cagr = 30.0 if (fast, slow) == (2, 6) else 20.0
                total_return = 500.0 if (fast, slow) == (1, 5) else 100.0
                rows.append(
                    {
                        "case_id": f"F{fast}_S{slow}",
                        "window_id": "W1",
                        "window_label": "first",
                        "window_start": "2020-01-02",
                        "window_end": "2020-12-31",
                        "fast_window": fast,
                        "slow_window": slow,
                        "holding_period_cagr_pct": holding_cagr,
                        "holding_sessions": 126,
                        "holding_time_pct": 50.0,
                        "total_return_pct": total_return,
                        "cagr_pct": 10.0,
                        "sharpe": 1.0,
                        "max_drawdown_pct": -10.0,
                    }
                )
        benchmark = pd.DataFrame(
            [
                {
                    "window_id": "W1",
                    "holding_period_cagr_pct": 12.0,
                    "holding_sessions": 251,
                    "holding_time_pct": 99.6,
                    "cagr_pct": 11.9,
                    "total_return_pct": 12.0,
                }
            ]
        )
        summary = surface_summary(pd.DataFrame(rows), benchmark).iloc[0]
        self.assertEqual((summary["best_fast_window"], summary["best_slow_window"]), (2, 6))
        self.assertEqual(
            (summary["stable_fast_window"], summary["stable_slow_window"]),
            (2, 6),
        )
        self.assertAlmostEqual(summary["best_total_return_pct"], 100.0)

    def test_grid_masks_pairs_that_do_not_preserve_fast_slow_roles(self) -> None:
        pairs = valid_parameter_pairs(1, 50, 15, 250)
        self.assertEqual(len(pairs), 11_134)
        self.assertIn((1, 15), pairs)
        self.assertIn((50, 250), pairs)
        self.assertNotIn((15, 15), pairs)
        self.assertNotIn((50, 49), pairs)

    def test_window_slice_uses_pre_window_history_for_warmup(self) -> None:
        spec = DualSmaStateSpec(2, 3)
        prepared = prepare_dual_sma_data(bars([1, 2, 3, 4, 5]), spec)
        window = analysis_slice(prepared, prepared.loc[2, "date"], prepared.loc[4, "date"])
        self.assertAlmostEqual(float(window.loc[0, FAST_SMA_COLUMN]), 2.5)
        self.assertAlmostEqual(float(window.loc[0, SLOW_SMA_COLUMN]), 2.0)

    def test_level_condition_can_buy_from_first_window_close_and_sells_on_equality(self) -> None:
        spec = DualSmaStateSpec(2, 3)
        prepared = prepare_dual_sma_data(bars([1, 2, 3, 4, 3, 3, 3]), spec)
        window = analysis_slice(prepared, prepared.loc[2, "date"], prepared.loc[6, "date"])
        policy = ExplicitFillPolicy("open", cost_bps=5)
        reference = run_reference_dual_sma(window, spec, policy)
        self.assertEqual(reference.orders["type"].tolist(), ["buy", "sell"])
        self.assertEqual(
            pd.to_datetime(reference.orders["signal_date"]).tolist(),
            [pd.Timestamp(prepared.loc[2, "date"]), pd.Timestamp(prepared.loc[5, "date"])],
        )
        self.assertEqual(
            pd.to_datetime(reference.orders["date"]).tolist(),
            [pd.Timestamp(prepared.loc[3, "date"]), pd.Timestamp(prepared.loc[6, "date"])],
        )
        self.assertAlmostEqual(
            float(reference.orders.iloc[0]["fill_price"]),
            float(prepared.loc[3, "open"]) * 1.0005,
        )

    def test_pybroker_matches_independent_reference(self) -> None:
        spec = DualSmaStateSpec(2, 3)
        prepared = prepare_dual_sma_data(bars([1, 2, 3, 4, 3, 2, 1, 2, 3, 4]), spec)
        window = analysis_slice(prepared, prepared.loc[2, "date"], prepared.loc[9, "date"])
        policy = ExplicitFillPolicy("open", cost_bps=5)
        actual_result = run_pybroker_dual_sma(window, spec, policy)
        actual = pybroker_daily_state(actual_result, window)
        reference = run_reference_dual_sma(window, spec, policy)
        for column in ("cash", "shares", "equity"):
            np.testing.assert_allclose(
                actual[column].to_numpy(float),
                reference.daily[column].to_numpy(float),
                rtol=0,
                atol=1e-8,
            )
        actual_orders = actual_result.orders.reset_index()
        self.assertEqual(actual_orders["type"].tolist(), reference.orders["type"].tolist())
        self.assertEqual(
            pd.to_datetime(actual_orders["date"]).tolist(),
            pd.to_datetime(reference.orders["date"]).tolist(),
        )
        np.testing.assert_allclose(
            actual_orders["fill_price"].to_numpy(float),
            reference.orders["fill_price"].to_numpy(float),
            rtol=0,
            atol=1e-9,
        )

    def test_grid_worker_runs_a_complete_pair_window_case(self) -> None:
        raw = bars([1, 2, 3, 4, 3, 2, 1, 2, 3, 4])
        window = {
            "window_id": "WTEST",
            "label": "test",
            "start": pd.Timestamp(raw.loc[2, "date"]),
            "end": pd.Timestamp(raw.loc[9, "date"]),
            "bar_count": 8,
        }
        parameters = {"slow_window_start": 3, "slow_window_end": 3}
        case_indexes = {("WTEST", 2, 3): 0}
        configure_worker(
            raw,
            precompute_smas(raw, 3),
            [window],
            parameters,
            case_indexes,
            100_000.0,
            5.0,
        )
        output = run_fast_window(2)
        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]["case_id"], "F002_S003__WTEST")
        self.assertEqual(len(output[0]["equity"]), 8)
        self.assertLessEqual(
            output[0]["differences"]["max_abs_equity_difference"], 1e-6
        )
        self.assertGreater(output[0]["metrics"]["holding_sessions"], 0)
        self.assertIn("holding_period_cagr_pct", output[0]["metrics"])

    def test_market_figure_exposes_sma_overlay_checkboxes(self) -> None:
        raw = bars([float(value) for value in range(1, 31)])
        summaries = pd.DataFrame(
            [
                {
                    "window_id": "W1",
                    "window_label": "first",
                    "stable_fast_window": 2,
                    "stable_slow_window": 5,
                },
                {
                    "window_id": "W2",
                    "window_label": "second",
                    "stable_fast_window": 3,
                    "stable_slow_window": 6,
                },
            ]
        )
        controls = market_controls("market-aapl", market_figure(raw, summaries))
        self.assertEqual(controls.count('data-series="'), 4)
        self.assertIn('data-series="W1_fast"', controls)
        self.assertIn('data-series="W2_slow"', controls)


if __name__ == "__main__":
    unittest.main()
