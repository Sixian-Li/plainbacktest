from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from quantkit.execution import ExplicitFillPolicy
from quantkit.nasdaq100_stochrsi_rotation import (
    BEAR9_FLOOR10,
    FULL_EQUAL,
    build_target_weights,
    build_rotation_target_share_table,
    case_definitions,
    membership_flags_for_dates,
    prepare_strategy1_score_inputs,
    rank_eligible_scores,
    rebalance_dates,
    run_strategy1_score,
    shifted_membership_bounds,
)
from quantkit.stochrsi_scaled_pools import (
    ScaledPoolSpec,
    prepare_scaled_pool_data,
    run_reference_scaled_pools,
)
from scripts.run_nasdaq100_stochrsi_rotation import (
    _assert_no_synthetic_fills,
    _cross_check_payloads,
    _flatten_cross_checks,
    dense_price_frame,
)


BACKTEST_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT = BACKTEST_ROOT / (
    "experiments/ROT/ROT-v0.50a.1__26-08-26__nasdaq100_stochrsi_strategy1_top20/experiment.json"
)


def synthetic_prices(count: int = 520) -> pd.DataFrame:
    dates = pd.bdate_range("2018-01-02", periods=count)
    phase = np.linspace(0, 42, count)
    close = 100 * np.exp(np.linspace(0, 0.25, count) + 0.09 * np.sin(phase) + 0.025 * np.sin(phase * 3.1))
    open_ = close * (1 + 0.002 * np.sin(phase * 1.7))
    high = np.maximum(open_, close) * 1.01
    low = np.minimum(open_, close) * 0.99
    return pd.DataFrame(
        {
            "date": dates,
            "symbol": "TEST",
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1_000_000.0,
        }
    )


class Nasdaq100StochrsiRotationTests(unittest.TestCase):
    def test_cross_check_manifest_is_flat_for_standard_validation(self) -> None:
        detailed = {
            "CASE_B": {"max_abs_cash_difference": 2e-9},
            "CASE_A": {
                "max_abs_cash_difference": 1e-9,
                "max_abs_equity_difference": 3e-9,
            },
        }
        metrics_payload, flattened = _cross_check_payloads(detailed)
        self.assertIs(metrics_payload, detailed)
        self.assertTrue(all(isinstance(value, dict) for value in metrics_payload.values()))
        self.assertTrue(all(isinstance(value, float) for value in flattened.values()))
        self.assertEqual(
            list(flattened),
            [
                "CASE_A.max_abs_cash_difference",
                "CASE_A.max_abs_equity_difference",
                "CASE_B.max_abs_cash_difference",
            ],
        )
        self.assertEqual(flattened["CASE_A.max_abs_equity_difference"], 3e-9)

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(EXPERIMENT.read_text(encoding="utf-8"))

    def test_fast_score_engine_matches_frozen_strategy1_reference(self) -> None:
        raw = synthetic_prices()
        fast_prepared = prepare_strategy1_score_inputs(raw)
        fast = run_strategy1_score(fast_prepared)
        frozen_prepared = prepare_scaled_pool_data(raw, ScaledPoolSpec())
        frozen = run_reference_scaled_pools(
            frozen_prepared,
            ScaledPoolSpec(),
            analysis_start=raw.iloc[0]["date"],
            analysis_end=raw.iloc[-1]["date"],
        )
        np.testing.assert_allclose(
            fast.daily["strategy1_weight"],
            frozen.daily["qqq_weight"],
            rtol=0.0,
            atol=1e-12,
        )
        for left, right in (
            ("cash", "cash"),
            ("shares", "shares"),
            ("pool_a_shares", "pool_a_shares"),
            ("pool_b_shares", "pool_b_shares"),
            ("mechanism_a_armed", "mechanism_a_armed"),
            ("b2_count", "b2_count"),
        ):
            np.testing.assert_allclose(fast.daily[left], frozen.daily[right], rtol=0.0, atol=1e-10)
        self.assertEqual(
            fast.diagnostics["external_contribution_count"],
            len(frozen.contributions),
        )
        self.assertAlmostEqual(
            float(fast.diagnostics["external_contribution_total"]),
            float(frozen.contributions["amount"].sum()) if len(frozen.contributions) else 0.0,
            places=8,
        )

    def test_membership_is_lagged_one_xnys_session(self) -> None:
        sessions = pd.bdate_range("2024-01-02", periods=8)
        intervals = pd.DataFrame(
            {
                "security_id": ["A"],
                "effective_start": [sessions[1]],
                "effective_end": [sessions[4]],
            }
        )
        bounds = shifted_membership_bounds(intervals, sessions)
        flags = membership_flags_for_dates(pd.Series(sessions), bounds, "A")
        expected = np.array([False, False, True, True, True, True, False, False])
        np.testing.assert_array_equal(flags, expected)

    def test_strict_top20_ranking_and_security_id_tie_break(self) -> None:
        date = pd.Timestamp("2024-01-02")
        rows = []
        for index in range(23):
            rows.append(
                {
                    "date": date,
                    "security_id": f"S{22-index:02d}",
                    "display_ticker": f"T{index:02d}",
                    "strategy1_weight": 0.99 if index < 22 else 0.90,
                    "in_universe": True,
                }
            )
        selected, audit = rank_eligible_scores(pd.DataFrame(rows), [date])
        self.assertEqual(len(selected), 20)
        self.assertEqual(audit.iloc[0]["eligible_count"], 22)
        self.assertNotIn("S21", selected["security_id"].tolist())
        self.assertNotIn("S22", selected["security_id"].tolist())
        self.assertNotIn("S00", selected[selected["strategy1_weight"].eq(0.90)]["security_id"].tolist())
        self.assertEqual(selected.iloc[0]["security_id"], "S01")

    def test_rebalance_calendar_and_frozen_case_count(self) -> None:
        dates = pd.bdate_range("2024-01-02", periods=11)
        self.assertEqual(rebalance_dates(dates, 3).tolist(), dates[[0, 3, 6, 9]].tolist())
        cases = case_definitions(self.config["parameters"])
        self.assertEqual(len(cases), 8)
        self.assertEqual(
            len(cases) * len(self.config["cost_scenarios_bps_per_side"]),
            self.config["parameters"]["formal_path_count"],
        )

    def test_full_equal_and_bear9_floor10_under_ten(self) -> None:
        dates = pd.bdate_range("2024-01-02", periods=2)
        selected = pd.DataFrame(
            {
                "date": [dates[0]] * 5,
                "security_id": [f"S{i}" for i in range(5)],
                "display_ticker": [f"T{i}" for i in range(5)],
                "strategy1_weight": [0.99 - i * 0.001 for i in range(5)],
                "rank": range(1, 6),
            }
        )
        bear = {"AZO": 0.4, "WMT": 0.6}
        available = {symbol: pd.Series(True, index=dates) for symbol in bear}
        full = build_target_weights(
            selected,
            dates,
            case_id="RB01_FULL_EQUAL",
            rebalance_days=1,
            allocation_mode=FULL_EQUAL,
            bear_weights=bear,
            bear_available=available,
        )
        first_full = full[full["date"].eq(dates[0])]
        self.assertAlmostEqual(first_full["target_weight"].sum(), 1.0)
        self.assertTrue(np.allclose(first_full["target_weight"], 0.2))
        floor = build_target_weights(
            selected,
            dates,
            case_id="RB01_BEAR9_FLOOR10",
            rebalance_days=1,
            allocation_mode=BEAR9_FLOOR10,
            bear_weights=bear,
            bear_available=available,
        )
        first_floor = floor[floor["date"].eq(dates[0])]
        stock = first_floor[first_floor["asset_type"].eq("nasdaq100_member")]
        hedge = first_floor[first_floor["asset_type"].eq("bear9")]
        self.assertTrue(np.allclose(stock["target_weight"], 0.1))
        self.assertAlmostEqual(stock["target_weight"].sum(), 0.5)
        self.assertAlmostEqual(hedge["target_weight"].sum(), 0.5)
        self.assertAlmostEqual(hedge.set_index("display_ticker").at["AZO", "target_weight"], 0.2)

    def test_unavailable_bear_weight_remains_cash(self) -> None:
        date = pd.Timestamp("2024-01-02")
        targets = build_target_weights(
            pd.DataFrame(columns=["date", "security_id", "display_ticker", "strategy1_weight", "rank"]),
            [date],
            case_id="RB01_BEAR9_FLOOR10",
            rebalance_days=1,
            allocation_mode=BEAR9_FLOOR10,
            bear_weights={"AZO": 0.4, "DG": 0.6},
            bear_available={
                "AZO": pd.Series([True], index=[date]),
                "DG": pd.Series([False], index=[date]),
            },
        )
        self.assertAlmostEqual(targets["target_weight"].sum(), 0.4)
        self.assertNotIn("DG", targets["display_ticker"].tolist())

    def test_terminal_price_proxy_only_allows_sell(self) -> None:
        dates = pd.bdate_range("2024-01-02", periods=4)
        raw = pd.DataFrame(
            {
                "date": dates[:2],
                "symbol": "OLD",
                "open": [10.0, 11.0],
                "high": [10.5, 11.5],
                "low": [9.5, 10.5],
                "close": [10.2, 11.2],
                "volume": [100.0, 100.0],
            }
        )
        panel = dense_price_frame(
            raw,
            dates,
            instrument_id="OLD",
            display_ticker="OLD",
            asset_type="nasdaq100_member",
        )
        self.assertEqual(panel["terminal_settlement_proxy"].tolist(), [0, 0, 1, 1])
        sell = pd.DataFrame(
            [{"date": dates[2], "symbol": "OLD", "type": "sell"}]
        ).set_index("date")
        self.assertEqual(_assert_no_synthetic_fills(sell, panel), 1)
        buy = sell.copy()
        buy["type"] = "buy"
        with self.assertRaisesRegex(AssertionError, "forbidden synthetic"):
            _assert_no_synthetic_fills(buy, panel)
        dust = pd.DataFrame(
            [
                {
                    "date": dates[2],
                    "symbol": "OLD",
                    "type": "buy",
                    "shares": 2.5e-11,
                    "fill_price": 0.15,
                }
            ]
        ).set_index("date")
        self.assertEqual(
            _assert_no_synthetic_fills(
                dust, panel, numerical_zero_notional=1e-9
            ),
            0,
        )
        targets = pd.DataFrame(
            {
                "case_id": ["CASE", "CASE"],
                "date": dates[:2],
                "symbol": ["OLD", "OLD"],
                "target_weight": [0.5, 1.0],
            }
        )
        shares = build_rotation_target_share_table(
            panel,
            targets,
            case_id="CASE",
            initial_cash=100.0,
            policy=ExplicitFillPolicy("open", 0.0),
        )
        self.assertEqual(int(shares["execution_deferred_unavailable"].sum()), 1)
        self.assertAlmostEqual(
            shares.set_index("signal_date").at[dates[1], "target_shares"],
            shares.set_index("signal_date").at[dates[0], "target_shares"],
        )

    def test_temporary_missing_bar_defers_change_until_a_real_rebalance(self) -> None:
        dates = pd.bdate_range("2024-01-02", periods=5)
        raw = pd.DataFrame(
            {
                "date": dates[[0, 1, 3, 4]],
                "symbol": "A",
                "open": [10.0, 10.0, 10.0, 10.0],
                "high": [10.0, 10.0, 10.0, 10.0],
                "low": [10.0, 10.0, 10.0, 10.0],
                "close": [10.0, 10.0, 10.0, 10.0],
                "volume": [100.0, 100.0, 100.0, 100.0],
            }
        )
        panel = dense_price_frame(
            raw,
            dates,
            instrument_id="A",
            display_ticker="A",
            asset_type="nasdaq100_member",
        )
        targets = pd.DataFrame(
            {
                "case_id": ["CASE"] * 4,
                "date": dates[:4],
                "symbol": ["A"] * 4,
                "target_weight": [1.0, 0.0, 0.0, 0.0],
            }
        )
        shares = build_rotation_target_share_table(
            panel,
            targets,
            case_id="CASE",
            initial_cash=100.0,
            policy=ExplicitFillPolicy("open", 0.0),
        )
        by_signal = shares.set_index("signal_date")
        self.assertAlmostEqual(by_signal.at[dates[0], "target_shares"], 10.0)
        self.assertAlmostEqual(by_signal.at[dates[1], "target_shares"], 10.0)
        self.assertAlmostEqual(by_signal.at[dates[2], "target_shares"], 10.0)
        self.assertAlmostEqual(by_signal.at[dates[3], "target_shares"], 0.0)
        self.assertEqual(int(shares["execution_deferred_unavailable"].sum()), 2)


if __name__ == "__main__":
    unittest.main()
