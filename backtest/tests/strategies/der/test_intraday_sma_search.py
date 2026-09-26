from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from quantkit.intraday_sma import (
    BUY_FORCED_REENTRY,
    SELL_FAST_DROP,
    IntradaySmaSpec,
    run_reference_intraday_sma,
)
from quantkit.intraday_sma_search import (
    FAST_DROP_ENABLED_COLUMN,
    FORCED_REENTRY_COLUMN,
    PARAMETER_COLUMNS,
    sample_cases_with_reentry_modes,
    sample_global_cases,
    screen_cases,
)
from quantkit.metrics import calculate_metrics
from scripts.run_intraday_sma_backtest import cross_check
from scripts.run_intraday_sma_global_search import formal_case
from tests.strategies.der.test_intraday_sma import bars


def parameter_row(spec: IntradaySmaSpec) -> dict[str, float]:
    return {
        "A_negative_days_slow": spec.a_negative_days_slow,
        "B_slow_sma_window": spec.b_slow_sma_window,
        "C_fast_derivative_pct": spec.c_fast_derivative_pct,
        "D_negative_days_fast": spec.d_negative_days_fast,
        "E_fallback_sma_window": spec.e_fallback_sma_window,
        "F_short_sma_center": spec.f_short_sma_windows[1],
        "F_short_sma_spacing": spec.f_short_sma_windows[1] - spec.f_short_sma_windows[0],
        "G_short_recovery_below_pct": spec.g_short_recovery_below_pct,
        "H_reentry_sma_window": spec.h_reentry_sma_window,
        "L_cost_stop_pct": spec.l_cost_stop_pct,
        "R_forced_rebuy_pct": spec.r_forced_rebuy_pct,
    }


class IntradaySmaSearchTest(unittest.TestCase):
    def test_compiled_screen_matches_reference_when_fast_drop_is_removed(self) -> None:
        frame = bars(680)
        initial_cash = 100_000.0
        spec = IntradaySmaSpec(
            a_negative_days_slow=4,
            b_slow_sma_window=110,
            c_fast_derivative_pct=-0.01,
            d_negative_days_fast=2,
            e_fallback_sma_window=160,
            f_short_sma_windows=(35, 45, 55),
            g_short_recovery_below_pct=1.25,
            h_reentry_sma_window=180,
            l_cost_stop_pct=8.0,
            r_forced_rebuy_pct=7.0,
            fast_derivative_mode="all_short_smas",
            fast_drop_enabled=False,
        )
        cases = pd.DataFrame(
            [{**parameter_row(spec), FAST_DROP_ENABLED_COLUMN: False}]
        )
        screened = screen_cases(
            frame,
            cases,
            initial_cash=initial_cash,
            evaluation_start="analysis_start",
        ).iloc[0]
        reference = run_reference_intraday_sma(
            frame,
            spec,
            analysis_start=frame.iloc[0]["date"],
            analysis_end=frame.iloc[-1]["date"],
            initial_position="flat",
            initial_cash=initial_cash,
        )
        self.assertNotIn(SELL_FAST_DROP, set(reference.orders["primary_signal"]))
        metrics = calculate_metrics(
            reference.daily,
            reference.orders,
            reference.trades,
            initial_cash=initial_cash,
        )
        for name in ("final_equity", "cagr_pct", "sharpe", "max_drawdown_pct", "exposure_pct"):
            self.assertAlmostEqual(float(screened[name]), float(metrics[name]), places=8, msg=name)
        self.assertEqual(int(screened["order_count"]), int(metrics["order_count"]))

    def test_cross_check_accepts_explicit_formal_tolerance(self) -> None:
        class Result:
            orders = pd.DataFrame({"type": ["buy"], "date": [pd.Timestamp("2024-01-02")], "shares": [1.0], "fill_price": [100.0]})
            trades = pd.DataFrame(columns=["entry_date", "exit_date", "entry", "exit", "shares", "pnl"])

        class Reference:
            daily = pd.DataFrame({"cash": [1.0 + 2e-8], "shares": [0.0], "equity": [1.0 + 2e-8]})
            orders = Result.orders.copy()
            trades = pd.DataFrame(columns=["entry_date", "exit_date", "entry_price", "exit_price", "shares", "pnl"])

        actual = pd.DataFrame({"cash": [1.0], "shares": [0.0], "equity": [1.0]})
        differences = cross_check(Result(), actual, Reference(), tolerance=1e-6)
        self.assertGreater(differences["max_abs_cash_difference"], 1e-8)

    def test_compiled_screen_matches_reference_metrics(self) -> None:
        frame = bars(680)
        initial_cash = 100_000.0
        specs = [
            IntradaySmaSpec(
                c_fast_derivative_pct=-0.20,
                fast_derivative_mode="all_short_smas",
            ),
            IntradaySmaSpec(
                a_negative_days_slow=5,
                b_slow_sma_window=90,
                c_fast_derivative_pct=-0.35,
                d_negative_days_fast=4,
                e_fallback_sma_window=150,
                f_short_sma_windows=(20, 30, 40),
                g_short_recovery_below_pct=2.0,
                h_reentry_sma_window=170,
                l_cost_stop_pct=4.0,
                r_forced_rebuy_pct=3.0,
                fast_derivative_mode="all_short_smas",
            ),
        ]
        cases = pd.DataFrame([parameter_row(spec) for spec in specs])
        screened = screen_cases(frame, cases, initial_cash=initial_cash)
        for index, spec in enumerate(specs):
            reference = run_reference_intraday_sma(
                frame,
                spec,
                analysis_start=frame.iloc[0]["date"],
                analysis_end=frame.iloc[-1]["date"],
                initial_position="flat",
                initial_cash=initial_cash,
            )
            first_date = pd.Timestamp(reference.orders.iloc[0]["date"])
            daily = reference.daily[reference.daily["date"] >= first_date].reset_index(drop=True)
            metrics = calculate_metrics(
                daily,
                reference.orders,
                reference.trades,
                initial_cash=initial_cash,
            )
            observed = screened.iloc[index]
            for name in ("final_equity", "cagr_pct", "sharpe", "max_drawdown_pct", "exposure_pct"):
                self.assertAlmostEqual(float(observed[name]), float(metrics[name]), places=8, msg=name)
            self.assertEqual(int(observed["order_count"]), int(metrics["order_count"]))
            self.assertEqual(pd.Timestamp(observed["first_entry_date"]), first_date)
            self.assertAlmostEqual(
                float(observed["first_entry_fill"]),
                float(reference.orders.iloc[0]["fill_price"]),
                places=10,
            )

    def test_sampling_is_seeded_unique_and_respects_space(self) -> None:
        space = {name: [1.0, 2.0, 3.0] for name in PARAMETER_COLUMNS}
        first = sample_global_cases(space, count=100, seed=42)
        second = sample_global_cases(space, count=100, seed=42)
        pd.testing.assert_frame_equal(first, second)
        self.assertFalse(first.duplicated().any())
        for name in PARAMETER_COLUMNS:
            self.assertTrue(first[name].isin(space[name]).all())

    def test_window_screen_uses_prior_history_only_for_indicator_warmup(self) -> None:
        frame = bars(680)
        spec = IntradaySmaSpec(
            a_negative_days_slow=5,
            b_slow_sma_window=90,
            c_fast_derivative_pct=-0.35,
            d_negative_days_fast=4,
            e_fallback_sma_window=150,
            f_short_sma_windows=(20, 30, 40),
            g_short_recovery_below_pct=2.0,
            h_reentry_sma_window=170,
            l_cost_stop_pct=4.0,
            r_forced_rebuy_pct=3.0,
            fast_derivative_mode="all_short_smas",
        )
        cases = pd.DataFrame([{**parameter_row(spec), FORCED_REENTRY_COLUMN: False}])
        start = pd.Timestamp(frame.iloc[250]["date"])
        end = pd.Timestamp(frame.iloc[-1]["date"])
        screened = screen_cases(
            frame,
            cases,
            analysis_start=start,
            analysis_end=end,
            initial_cash=100_000.0,
        ).iloc[0]
        reference = run_reference_intraday_sma(
            frame,
            spec,
            analysis_start=start,
            analysis_end=end,
            initial_position="flat",
            initial_cash=100_000.0,
            disabled_signals=(BUY_FORCED_REENTRY,),
        )
        self.assertFalse(reference.orders.empty)
        self.assertTrue((reference.orders["date"] >= start).all())
        first_date = pd.Timestamp(reference.orders.iloc[0]["date"])
        metrics = calculate_metrics(
            reference.daily[reference.daily["date"] >= first_date].reset_index(drop=True),
            reference.orders,
            reference.trades,
            initial_cash=100_000.0,
        )
        for name in ("final_equity", "cagr_pct", "sharpe", "max_drawdown_pct", "exposure_pct"):
            self.assertAlmostEqual(float(screened[name]), float(metrics[name]), places=8, msg=name)
        self.assertEqual(int(screened["order_count"]), int(metrics["order_count"]))
        self.assertEqual(pd.Timestamp(screened["first_entry_date"]), first_date)

    def test_window_start_evaluation_includes_flat_waiting_period(self) -> None:
        frame = bars(680)
        spec = IntradaySmaSpec(
            a_negative_days_slow=5,
            b_slow_sma_window=90,
            c_fast_derivative_pct=-0.35,
            d_negative_days_fast=4,
            e_fallback_sma_window=150,
            f_short_sma_windows=(20, 30, 40),
            g_short_recovery_below_pct=2.0,
            h_reentry_sma_window=170,
            l_cost_stop_pct=4.0,
            r_forced_rebuy_pct=3.0,
            fast_derivative_mode="all_short_smas",
        )
        cases = pd.DataFrame([parameter_row(spec)])
        start = pd.Timestamp(frame.iloc[120]["date"])
        screened = screen_cases(
            frame,
            cases,
            analysis_start=start,
            analysis_end=frame.iloc[-1]["date"],
            initial_cash=100_000.0,
            evaluation_start="analysis_start",
        ).iloc[0]
        reference = run_reference_intraday_sma(
            frame,
            spec,
            analysis_start=start,
            analysis_end=frame.iloc[-1]["date"],
            initial_position="flat",
            initial_cash=100_000.0,
        )
        metrics = calculate_metrics(
            reference.daily,
            reference.orders,
            reference.trades,
            initial_cash=100_000.0,
        )
        self.assertGreater(pd.Timestamp(reference.orders.iloc[0]["date"]), start)
        for name in ("final_equity", "cagr_pct", "sharpe", "max_drawdown_pct", "exposure_pct"):
            self.assertAlmostEqual(float(screened[name]), float(metrics[name]), places=8, msg=name)

    def test_reentry_mode_sampling_is_seeded_unique_and_explicit(self) -> None:
        space = {name: [1.0, 2.0, 3.0] for name in PARAMETER_COLUMNS}
        first = sample_cases_with_reentry_modes(
            space, reentry_modes=(False, True), count=100, seed=42
        )
        second = sample_cases_with_reentry_modes(
            space, reentry_modes=(False, True), count=100, seed=42
        )
        pd.testing.assert_frame_equal(first, second)
        self.assertFalse(first.duplicated().any())
        self.assertEqual(set(first[FORCED_REENTRY_COLUMN]), {False, True})

    def test_formal_case_can_use_common_analysis_start_evaluation(self) -> None:
        frame = bars(680)
        spec = IntradaySmaSpec(
            a_negative_days_slow=5,
            b_slow_sma_window=90,
            c_fast_derivative_pct=-0.35,
            d_negative_days_fast=4,
            e_fallback_sma_window=150,
            f_short_sma_windows=(20, 30, 40),
            g_short_recovery_below_pct=2.0,
            h_reentry_sma_window=170,
            l_cost_stop_pct=4.0,
            r_forced_rebuy_pct=3.0,
            fast_derivative_mode="all_short_smas",
        )
        start = pd.Timestamp(frame.iloc[120]["date"])
        end = pd.Timestamp(frame.iloc[-1]["date"])
        screening = screen_cases(
            frame,
            pd.DataFrame([parameter_row(spec)]),
            analysis_start=start,
            analysis_end=end,
            initial_cash=100_000.0,
            evaluation_start="analysis_start",
        )
        candidate = screening.iloc[0].copy()
        candidate["case_id"] = "COMMON_START"
        candidate["selection_reason"] = "test"
        analysis = frame[(frame["date"] >= start) & (frame["date"] <= end)].reset_index(drop=True)
        record, frames, _ = formal_case(
            frame,
            analysis,
            candidate,
            start=start,
            end=end,
            initial_cash=100_000.0,
            evaluation_start="analysis_start",
        )
        self.assertEqual(record["evaluation_start"], "analysis_start")
        self.assertEqual(pd.Timestamp(frames["daily"].iloc[0]["date"]), start)
        self.assertGreater(pd.Timestamp(record["first_entry_date"]), start)
        self.assertAlmostEqual(record["cagr_pct"], candidate["cagr_pct"], places=8)
        self.assertAlmostEqual(
            record["benchmark_cagr_pct"], candidate["benchmark_cagr_pct"], places=8
        )


if __name__ == "__main__":
    unittest.main()
