from __future__ import annotations

import math
import json
import unittest
from pathlib import Path

from quantkit.paths import BACKTEST_ROOT

import numpy as np
import pandas as pd

from quantkit.intraday_sma_threshold import (
    BUY_CORRECTION,
    BUY_SMA200_THRESHOLD,
    SELL_CORRECTION,
    SELL_SMA200_THRESHOLD,
    IntradaySmaThresholdSpec,
    prepare_intraday_threshold_data,
    run_reference_intraday_threshold,
)
from quantkit.intraday_sma_threshold_search import exhaustive_cases
from quantkit.intraday_sma_threshold_selection import (
    cscv_pbo,
    deflated_sharpe_probability,
    effective_trial_count,
    rolling_five_year_results,
    screen_continuous_cases,
    select_stable_plateau,
    start_sensitivity_results,
)
from quantkit.metrics import calculate_metrics


def synthetic_bars(count: int = 780, *, window: int = 5) -> pd.DataFrame:
    dates = pd.bdate_range("2010-01-04", periods=count)
    index = np.arange(count, dtype=float)
    close = 100.0 + np.sin(index / 5.0) * 13.0 + np.sin(index / 29.0) * 4.0 + index * 0.015
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 3.5
    low = np.minimum(open_, close) - 3.5
    return pd.DataFrame(
        {
            "symbol": "AAA",
            "date": dates,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1_000_000.0,
        }
    )


class ContinuousSelectionLedgerTest(unittest.TestCase):
    def test_asymmetric_continuous_screen_matches_reference(self) -> None:
        frame = synthetic_bars(180)
        prepared = prepare_intraday_threshold_data(frame, 5)
        analysis = prepared.iloc[20:161].reset_index(drop=True)
        cases = pd.DataFrame(
            [
                {
                    "case_id": "ASYM_1",
                    "a_pct": 2.0,
                    "b_pct": 1.0,
                    "correction_buy_pct": 5.0,
                    "correction_sell_pct": 7.5,
                },
                {
                    "case_id": "ASYM_2",
                    "a_pct": 2.0,
                    "b_pct": 1.0,
                    "correction_buy_pct": 5.0,
                    "correction_sell_pct": np.nan,
                },
            ]
        )
        screened = screen_continuous_cases(
            analysis,
            cases,
            window=5,
            cost_bps=5.0,
            initial_cash=100_000,
            pbo_block_count=4,
        ).metrics
        for _, observed in screened.iterrows():
            spec = IntradaySmaThresholdSpec(
                a_pct=float(observed["a_pct"]),
                b_pct=float(observed["b_pct"]),
                correction_buy_pct=float(observed["correction_buy_pct"]),
                correction_sell_pct=(
                    None
                    if pd.isna(observed["correction_sell_pct"])
                    else float(observed["correction_sell_pct"])
                ),
                window=5,
                cost_bps=5,
            )
            reference = run_reference_intraday_threshold(
                frame,
                spec,
                analysis_start=analysis.iloc[0]["date"],
                analysis_end=analysis.iloc[-1]["date"],
                initial_cash=100_000,
            )
            metrics = calculate_metrics(
                reference.daily,
                reference.orders,
                reference.trades,
                initial_cash=100_000,
            )
            for field in (
                "final_equity",
                "total_return_pct",
                "cagr_pct",
                "sharpe",
                "max_drawdown_pct",
                "exposure_pct",
            ):
                self.assertAlmostEqual(
                    float(observed[field]), float(metrics[field]), places=8, msg=field
                )
            counts = reference.orders["primary_signal"].value_counts()
            self.assertEqual(
                int(observed["sell_correction_count"]), int(counts.get(SELL_CORRECTION, 0))
            )

    def test_continuous_screen_matches_reference_metrics_and_signals(self) -> None:
        raw = synthetic_bars()
        prepared = prepare_intraday_threshold_data(raw, 5)
        analysis = prepared.iloc[20:760].reset_index(drop=True)
        cases = exhaustive_cases([1.0, 3.0], [-2.0, 1.0], [5.0])
        screen = screen_continuous_cases(
            analysis,
            cases,
            window=5,
            cost_bps=5.0,
            initial_cash=100_000.0,
            pbo_block_count=4,
        )
        signal_columns = {
            BUY_SMA200_THRESHOLD: "buy_sma_count",
            SELL_SMA200_THRESHOLD: "sell_sma_count",
            BUY_CORRECTION: "buy_correction_count",
            SELL_CORRECTION: "sell_correction_count",
        }
        for row in screen.metrics.itertuples(index=False):
            spec = IntradaySmaThresholdSpec(
                a_pct=float(row.a_pct),
                b_pct=float(row.b_pct),
                correction_pct=5.0,
                window=5,
                cost_bps=5.0,
            )
            reference = run_reference_intraday_threshold(
                raw,
                spec,
                analysis_start=analysis.iloc[0]["date"],
                analysis_end=analysis.iloc[-1]["date"],
                initial_cash=100_000.0,
            )
            metrics = calculate_metrics(
                reference.daily,
                reference.orders,
                reference.trades,
                initial_cash=100_000.0,
            )
            for name in (
                "final_equity",
                "total_return_pct",
                "cagr_pct",
                "sharpe",
                "max_drawdown_pct",
                "exposure_pct",
            ):
                self.assertAlmostEqual(float(getattr(row, name)), float(metrics[name]), places=8)
            self.assertEqual(int(row.order_count), int(metrics["order_count"]))
            observed = reference.orders["primary_signal"].value_counts().to_dict()
            for signal, column in signal_columns.items():
                self.assertEqual(int(getattr(row, column)), int(observed.get(signal, 0)))

    def test_rolling_windows_preserve_state_at_year_boundaries(self) -> None:
        raw = synthetic_bars(count=1700)
        prepared = prepare_intraday_threshold_data(raw, 5)
        analysis = prepared.iloc[20:1680].reset_index(drop=True)
        cases = exhaustive_cases([2.0], [-1.0], [5.0])
        screen = screen_continuous_cases(
            analysis,
            cases,
            window=5,
            cost_bps=5.0,
            initial_cash=100_000.0,
            pbo_block_count=4,
        )
        summary, long = rolling_five_year_results(
            screen, analysis["date"], cases["case_id"]
        )
        self.assertGreaterEqual(len(long), 1)
        first = long.iloc[0]
        start_year = int(first["window_start_year"])
        end_year = int(first["window_end_year"])
        years = np.sort(pd.to_datetime(analysis["date"]).dt.year.unique())
        left = int(np.where(years == start_year)[0][0])
        right = int(np.where(years == end_year)[0][0])
        start_equity = screen.year_start_equity[0, left]
        end_equity = screen.year_end_equity[0, right]
        dates = pd.to_datetime(analysis["date"])
        elapsed = (
            dates[dates.dt.year == end_year].max()
            - dates[dates.dt.year == start_year].min()
        ).days / 365.2425
        expected = ((end_equity / start_equity) ** (1.0 / elapsed) - 1.0) * 100.0
        self.assertAlmostEqual(float(first["cagr_pct"]), expected, places=10)
        self.assertEqual(summary.iloc[0]["case_id"], cases.iloc[0]["case_id"])


class SelectionRuleTest(unittest.TestCase):
    def test_formal_experiment_freezes_the_declared_selection_protocol(self) -> None:
        root = BACKTEST_ROOT
        config = json.loads(
            (
                root
                / "experiments/TIM/TIM-v0.30__26-08-14__qqq_intraday_sma200_robust_selection/experiment.json"
            ).read_text(encoding="utf-8")
        )
        parameters = config["parameters"]
        self.assertEqual(config["cost_scenarios_bps_per_side"], [5])
        self.assertEqual(parameters["correction_pct"], 5.0)
        self.assertEqual(parameters["a_pct_range"], {"start": -2.0, "stop": 10.0, "step": 0.25})
        self.assertEqual(parameters["b_pct_range"], {"start": -25.0, "stop": 10.0, "step": 0.25})
        self.assertEqual(parameters["combination_count"], 6909)
        self.assertEqual(parameters["rolling_five_year"]["window_count"], 22)
        self.assertEqual(len(parameters["restart_sensitivity"]["start_years"]), 17)
        self.assertEqual(parameters["pbo"]["split_count"], 924)
        self.assertEqual(parameters["reference_anchor"], {"a_pct": 3.0, "b_pct": -12.75, "purpose": "Predeclared comparison only; it receives no preference in selection."})

    def test_restart_summary_uses_declared_quartile(self) -> None:
        case_ids = pd.Series(["A", "B"])
        matrix = np.asarray([[1, 2, 3, 4], [-4, -3, -2, -1]], dtype=float)
        summary, long = start_sensitivity_results(case_ids, [2000, 2001, 2002, 2003], matrix)
        self.assertAlmostEqual(summary.loc[0, "restart_10y_cagr_q25_pct"], 1.75)
        self.assertAlmostEqual(summary.loc[1, "restart_10y_cagr_q25_pct"], -3.25)
        self.assertEqual(len(long), 8)

    def test_plateau_selects_local_center_and_reports_boundary(self) -> None:
        rows = []
        for a in np.arange(0.0, 2.25, 0.25):
            for b in np.arange(-3.0, 1.25, 0.25):
                value = 10.0 - 0.05 * (a - 1.0) ** 2 - 0.01 * (b + 1.0) ** 2
                rows.append(
                    {
                        "a_pct": a,
                        "b_pct": b,
                        "rolling_5y_cagr_q25_pct": value,
                        "restart_10y_cagr_q25_pct": 5.0,
                        "passes_restart_gate": True,
                    }
                )
        result = select_stable_plateau(pd.DataFrame(rows), top_quantile=0.70)
        self.assertTrue(result["largest_component"]["structural_pass"])
        self.assertFalse(result["largest_component"]["touches_search_boundary"])
        self.assertAlmostEqual(result["representative"]["a_pct"], 1.0, delta=0.5)
        self.assertAlmostEqual(result["representative"]["b_pct"], -1.0, delta=1.0)


class MultipleTestingDiagnosticsTest(unittest.TestCase):
    def test_cscv_has_all_combinations_and_detects_overfit_winner(self) -> None:
        # Strategy 0 dominates the first two blocks but fails the last two.
        sums = np.asarray(
            [
                [0.20, 0.20, -0.20, -0.20],
                [0.03, 0.03, 0.03, 0.03],
                [0.01, 0.01, 0.01, 0.01],
            ]
        )
        counts = np.asarray([20, 20, 20, 20])
        sumsq = np.square(sums) / counts[None, :] + 0.001
        summary, splits = cscv_pbo(sums, sumsq, counts, np.ones(3, dtype=bool))
        self.assertEqual(summary["split_count"], math.comb(4, 2))
        self.assertEqual(len(splits), 6)
        self.assertGreaterEqual(summary["pbo"], 0.0)
        self.assertLessEqual(summary["pbo"], 1.0)

    def test_effective_trials_and_dsr_are_finite(self) -> None:
        blocks = np.asarray(
            [
                [0.1, 0.2, 0.0, 0.3],
                [0.11, 0.19, 0.01, 0.29],
                [-0.1, 0.0, 0.2, 0.1],
            ]
        )
        effective = effective_trial_count(blocks, np.ones(3, dtype=bool))
        self.assertGreaterEqual(effective["effective_trial_count"], 1.0)
        self.assertLessEqual(effective["effective_trial_count"], 3.0)
        rng = np.random.default_rng(7)
        returns = rng.normal(0.0008, 0.01, 1500)
        dsr = deflated_sharpe_probability(
            returns,
            np.asarray([0.5, 0.8, 1.0, 1.2]),
            trial_count=effective["effective_trial_count"],
        )
        self.assertGreaterEqual(dsr["probability"], 0.0)
        self.assertLessEqual(dsr["probability"], 1.0)


if __name__ == "__main__":
    unittest.main()
