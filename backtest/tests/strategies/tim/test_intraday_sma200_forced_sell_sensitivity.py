from __future__ import annotations

import json
import unittest
from pathlib import Path

from quantkit.paths import BACKTEST_ROOT

import numpy as np
import pandas as pd

from scripts.run_intraday_sma200_forced_sell_sensitivity import (
    build_cases,
    evaluate_plateau,
    forced_sell_event_attribution,
)


EXPERIMENT = (
    BACKTEST_ROOT
    / "experiments/TIM/TIM-v0.30a.1__26-08-14__qqq_intraday_sma200_forced_sell_sensitivity/experiment.json"
)


class ForcedSellSensitivityTest(unittest.TestCase):
    def test_frozen_experiment_builds_eighteen_unique_cases(self) -> None:
        config = json.loads(EXPERIMENT.read_text(encoding="utf-8"))
        cases = build_cases(config["parameters"])
        self.assertEqual(len(cases), 18)
        self.assertFalse(
            cases.duplicated(
                ["pair_id", "correction_buy_pct", "correction_sell_pct"]
            ).any()
        )
        for _, group in cases.groupby("pair_id"):
            self.assertEqual(len(group), 9)
            sweep = group[np.isclose(group["correction_buy_pct"], 5.0)]
            self.assertEqual(len(sweep), 7)
            self.assertEqual(
                sorted(sweep["correction_sell_pct"].dropna().tolist()),
                [2.5, 5.0, 7.5, 10.0, 12.5, 15.0],
            )

    def test_event_attribution_uses_available_forward_sessions_and_next_buy(self) -> None:
        dates = pd.bdate_range("2024-01-02", periods=70)
        close = np.arange(100.0, 170.0)
        analysis = pd.DataFrame(
            {
                "date": dates,
                "open": close,
                "high": close + 2,
                "low": close - 2,
                "close": close,
            }
        )
        orders = pd.DataFrame(
            [
                {
                    "case_id": "CASE_1",
                    "type": "sell",
                    "date": dates[5],
                    "primary_signal": "SELL_CORRECTION",
                    "raw_fill_price": 105.0,
                    "fill_price": 104.95,
                    "fill_source": "intraday_trigger",
                },
                {
                    "case_id": "CASE_1",
                    "type": "buy",
                    "date": dates[10],
                    "primary_signal": "BUY_CORRECTION",
                    "raw_fill_price": 110.0,
                    "fill_price": 110.05,
                    "fill_source": "intraday_trigger",
                },
            ]
        )
        events = forced_sell_event_attribution(
            analysis, orders, horizons=[20, 60]
        )
        self.assertEqual(len(events), 1)
        event = events.iloc[0]
        self.assertEqual(int(event["flat_sessions"]), 5)
        self.assertEqual(event["next_buy_signal"], "BUY_CORRECTION")
        self.assertAlmostEqual(
            float(event["min_low_vs_raw_sell_pct_20"]),
            (104.0 / 105.0 - 1.0) * 100.0,
        )
        self.assertEqual(int(event["available_sessions_60"]), 60)

    def test_plateau_cannot_nominate_with_underidentified_events(self) -> None:
        rows = []
        for pair_id in ("primary", "drawdown_reference"):
            for d_pct in (np.nan, 2.5, 5.0, 7.5, 10.0, 12.5, 15.0):
                rows.append(
                    {
                        "case_id": f"{pair_id}_{d_pct}",
                        "pair_id": pair_id,
                        "correction_buy_pct": 5.0,
                        "correction_sell_pct": d_pct,
                        "rolling_5y_cagr_q25_pct": 8.0,
                        "restart_10y_cagr_q25_pct": 6.0,
                        "cagr_pct": 10.0 + (0.3 if d_pct == 7.5 else 0.0),
                        "sharpe": 0.7,
                        "max_drawdown_pct": -30.0,
                        "sell_correction_count": 4,
                    }
                )
        result = evaluate_plateau(pd.DataFrame(rows), {})
        self.assertFalse(result["overall_nomination_pass"])
        for item in result["pairs"].values():
            self.assertTrue(item["qualifying_plateaus_before_activity_gate"])
            self.assertFalse(item["activity_gate_any_candidate_plateau"])
            self.assertIsNone(item["selected_d_pct"])


if __name__ == "__main__":
    unittest.main()
