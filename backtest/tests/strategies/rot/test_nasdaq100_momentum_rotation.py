from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from quantkit.execution import ExplicitFillPolicy
from quantkit.nasdaq100_momentum_rotation import (
    GATE_12_1_AND_6_1_POSITIVE,
    GATE_NONE,
    FORCED_MEMBERSHIP_EXIT,
    FULL_REBALANCE,
    PIT_EQUAL_WEIGHT,
    PORTFOLIO_BUFFER,
    PORTFOLIO_REPLACE,
    TOP10_EXIT20_BUFFER,
    TOP10_MONTHLY_REPLACE,
    AbsoluteMomentumGateCase,
    MomentumSpec,
    build_absolute_gate_target_schedule,
    build_target_schedule,
    build_target_share_table,
    calendar_month_ends,
    rank_monthly_momentum,
    shifted_membership_bounds,
)
from quantkit.trend_score_portfolio import (
    cross_check_portfolios,
    run_pybroker_portfolio,
    run_reference_portfolio,
)


def price_frame(dates: list[str], closes: list[float]) -> pd.DataFrame:
    close = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1_000.0,
        }
    )


class Nasdaq100MomentumSignalTest(unittest.TestCase):
    def test_calendar_month_ends_use_full_calendar_not_partial_slice(self) -> None:
        calendar = pd.to_datetime(
            ["2021-01-28", "2021-01-29", "2021-02-01", "2021-02-26", "2021-03-01"]
        )
        observed = calendar_month_ends(calendar)
        self.assertEqual(
            observed.tolist(),
            [pd.Timestamp("2021-01-29"), pd.Timestamp("2021-02-26"), pd.Timestamp("2021-03-01")],
        )

    def test_one_session_membership_lag_is_applied_to_both_boundaries(self) -> None:
        sessions = pd.bdate_range("2021-01-04", periods=6)
        intervals = pd.DataFrame(
            [{"security_id": "A", "effective_start": sessions[1], "effective_end": sessions[3]}]
        )
        observed = shifted_membership_bounds(intervals, sessions)
        self.assertEqual(observed.iloc[0]["known_start"], sessions[2])
        self.assertEqual(observed.iloc[0]["known_end"], sessions[4])

    def test_12_1_uses_exact_month_ends_and_skips_signal_month(self) -> None:
        full_month_ends = pd.to_datetime(
            ["2020-01-31", "2021-01-29", "2021-02-26", "2021-03-31"]
        )
        dates = ["2020-01-31", "2021-01-29", "2021-02-26", "2021-03-31"]
        original = price_frame(dates, [100.0, 150.0, 999.0, 1_000.0])
        changed = original.copy()
        changed.loc[changed["date"] == pd.Timestamp("2021-02-26"), "close"] = 1.0
        intervals = pd.DataFrame(
            [
                {
                    "security_id": "A",
                    "known_start": pd.Timestamp("2020-01-01"),
                    "known_end": pd.Timestamp("2022-01-01"),
                }
            ]
        )
        signal = [pd.Timestamp("2021-02-26")]
        first, _ = rank_monthly_momentum(
            {"A": original}, signal, full_month_ends, intervals, {"A": "AAA"}
        )
        second, _ = rank_monthly_momentum(
            {"A": changed}, signal, full_month_ends, intervals, {"A": "AAA"}
        )
        self.assertAlmostEqual(float(first.iloc[0]["momentum_12_1"]), 0.5)
        self.assertAlmostEqual(
            float(first.iloc[0]["momentum_12_1"]),
            float(second.iloc[0]["momentum_12_1"]),
        )
        self.assertEqual(first.iloc[0]["momentum_end_date"], pd.Timestamp("2021-01-29"))

    def test_6_1_uses_exact_month_ends_and_skips_signal_month(self) -> None:
        full_month_ends = pd.to_datetime(
            ["2020-01-31", "2020-07-31", "2021-01-29", "2021-02-26", "2021-03-31"]
        )
        dates = ["2020-01-31", "2020-07-31", "2021-01-29", "2021-02-26", "2021-03-31"]
        original = price_frame(dates, [80.0, 100.0, 125.0, 999.0, 1_000.0])
        changed = original.copy()
        changed.loc[changed["date"] == pd.Timestamp("2021-02-26"), "close"] = 1.0
        intervals = pd.DataFrame(
            [
                {
                    "security_id": "A",
                    "known_start": pd.Timestamp("2020-01-01"),
                    "known_end": pd.Timestamp("2022-01-01"),
                }
            ]
        )
        signal = [pd.Timestamp("2021-02-26")]
        spec = MomentumSpec()
        first, _ = rank_monthly_momentum(
            {"A": original}, signal, full_month_ends, intervals, {"A": "AAA"}, spec
        )
        second, _ = rank_monthly_momentum(
            {"A": changed}, signal, full_month_ends, intervals, {"A": "AAA"}, spec
        )
        self.assertAlmostEqual(float(first.iloc[0]["momentum_6_1"]), 0.25)
        self.assertAlmostEqual(
            float(first.iloc[0]["momentum_6_1"]),
            float(second.iloc[0]["momentum_6_1"]),
        )
        self.assertEqual(
            first.iloc[0]["momentum_6_1_end_date"], pd.Timestamp("2021-01-29")
        )

    def test_rank_ties_use_stable_security_id_and_future_changes_do_not_rewrite_history(self) -> None:
        month_ends = pd.to_datetime(["2020-01-31", "2021-01-29", "2021-02-26", "2021-03-31"])
        dates = ["2020-01-31", "2021-01-29", "2021-02-26", "2021-03-31"]
        frames = {
            "B": price_frame(dates, [100.0, 120.0, 121.0, 122.0]),
            "A": price_frame(dates, [100.0, 120.0, 121.0, 122.0]),
        }
        bounds = pd.DataFrame(
            [
                {"security_id": key, "known_start": pd.Timestamp("2020-01-01"), "known_end": pd.Timestamp("2022-01-01")}
                for key in frames
            ]
        )
        signal = [pd.Timestamp("2021-02-26")]
        ranked, _ = rank_monthly_momentum(frames, signal, month_ends, bounds, {})
        self.assertEqual(ranked["security_id"].tolist(), ["A", "B"])
        changed = {key: value.copy() for key, value in frames.items()}
        changed["A"].loc[changed["A"]["date"] == pd.Timestamp("2021-03-31"), "close"] *= 100
        reranked, _ = rank_monthly_momentum(changed, signal, month_ends, bounds, {})
        pd.testing.assert_frame_equal(ranked, reranked)


class Nasdaq100MomentumPortfolioTest(unittest.TestCase):
    def test_no_gate_factorial_cases_reproduce_parent_selection_rules(self) -> None:
        securities = [f"S{i:02d}" for i in range(25)]
        jan = pd.Timestamp("2021-01-29")
        feb = pd.Timestamp("2021-02-26")
        rows = []
        for rank, security_id in enumerate(securities[:20], start=1):
            rows.append(
                {
                    "date": jan,
                    "security_id": security_id,
                    "rank": rank,
                    "momentum_12_1": 1.0 - rank / 100.0,
                    "momentum_6_1": 0.1,
                }
            )
        feb_order = securities[10:20] + securities[:5] + securities[20:25] + securities[5:10]
        for rank, security_id in enumerate(feb_order, start=1):
            rows.append(
                {
                    "date": feb,
                    "security_id": security_id,
                    "rank": rank,
                    "momentum_12_1": 1.0 - rank / 100.0,
                    "momentum_6_1": 0.1,
                }
            )
        rankings = pd.DataFrame(rows)
        bounds = pd.DataFrame(
            [
                {"security_id": value, "known_start": jan, "known_end": pd.Timestamp("2021-12-31")}
                for value in securities
            ]
        )
        calendar = pd.to_datetime(["2021-01-29", "2021-02-01", "2021-02-26", "2021-03-01"])
        parent, _ = build_target_schedule(rankings, [jan, feb], calendar, bounds, {})
        factorial, _ = build_absolute_gate_target_schedule(
            rankings,
            [jan, feb],
            calendar,
            bounds,
            {},
            [
                AbsoluteMomentumGateCase("NO_GATE_REPLACE", PORTFOLIO_REPLACE, GATE_NONE),
                AbsoluteMomentumGateCase("NO_GATE_BUFFER", PORTFOLIO_BUFFER, GATE_NONE),
            ],
            MomentumSpec(),
        )
        for parent_id, factorial_id in (
            (TOP10_MONTHLY_REPLACE, "NO_GATE_REPLACE"),
            (TOP10_EXIT20_BUFFER, "NO_GATE_BUFFER"),
        ):
            expected = parent[parent["case_id"] == parent_id][
                ["date", "instrument_id", "target_weight", "rank"]
            ].reset_index(drop=True)
            observed = factorial[factorial["case_id"] == factorial_id][
                ["date", "instrument_id", "target_weight", "rank"]
            ].reset_index(drop=True)
            pd.testing.assert_frame_equal(expected, observed, check_dtype=False)

    def test_dual_positive_gate_overrides_buffer_retention(self) -> None:
        jan = pd.Timestamp("2021-01-29")
        feb = pd.Timestamp("2021-02-26")
        rankings = pd.DataFrame(
            [
                {"date": jan, "security_id": "A", "momentum_12_1": 0.50, "momentum_6_1": 0.20},
                {"date": jan, "security_id": "B", "momentum_12_1": 0.40, "momentum_6_1": 0.10},
                {"date": feb, "security_id": "A", "momentum_12_1": 0.60, "momentum_6_1": -0.01},
                {"date": feb, "security_id": "C", "momentum_12_1": 0.30, "momentum_6_1": 0.05},
                {"date": feb, "security_id": "B", "momentum_12_1": 0.20, "momentum_6_1": 0.02},
            ]
        )
        bounds = pd.DataFrame(
            [
                {"security_id": symbol, "known_start": jan, "known_end": pd.Timestamp("2021-12-31")}
                for symbol in ("A", "B", "C")
            ]
        )
        cases = [
            AbsoluteMomentumGateCase(
                case_id="DUAL_BUFFER",
                portfolio_style=PORTFOLIO_BUFFER,
                gate=GATE_12_1_AND_6_1_POSITIVE,
            )
        ]
        schedule, audit = build_absolute_gate_target_schedule(
            rankings,
            [jan, feb],
            [jan, pd.Timestamp("2021-02-01"), feb, pd.Timestamp("2021-03-01")],
            bounds,
            {},
            cases,
            MomentumSpec(entry_rank=2, exit_rank=3),
        )
        feb_targets = schedule[(schedule["date"] == feb) & schedule["instrument_id"].notna()]
        self.assertEqual(set(feb_targets["instrument_id"]), {"B", "C"})
        self.assertNotIn("A", set(feb_targets["instrument_id"]))
        feb_audit = audit[audit["date"] == feb].iloc[0]
        self.assertEqual(int(feb_audit["eligible_count"]), 2)
        self.assertEqual(int(feb_audit["gate_rejected_count"]), 1)

    def test_zero_eligible_gate_emits_cash_event_and_liquidates_next_open(self) -> None:
        jan = pd.Timestamp("2021-01-29")
        feb = pd.Timestamp("2021-02-26")
        mar_open = pd.Timestamp("2021-03-01")
        rankings = pd.DataFrame(
            [
                {"date": jan, "security_id": "A", "momentum_12_1": 0.50, "momentum_6_1": 0.20},
                {"date": feb, "security_id": "A", "momentum_12_1": -0.01, "momentum_6_1": -0.02},
            ]
        )
        bounds = pd.DataFrame(
            [{"security_id": "A", "known_start": jan, "known_end": pd.Timestamp("2021-12-31")}]
        )
        cases = [
            AbsoluteMomentumGateCase(
                case_id="DUAL_BUFFER",
                portfolio_style=PORTFOLIO_BUFFER,
                gate=GATE_12_1_AND_6_1_POSITIVE,
            )
        ]
        calendar = pd.to_datetime(["2021-01-29", "2021-02-01", "2021-02-26", "2021-03-01"])
        schedule, _ = build_absolute_gate_target_schedule(
            rankings,
            [jan, feb],
            calendar,
            bounds,
            {},
            cases,
            MomentumSpec(),
        )
        cash_event = schedule[(schedule["date"] == feb) & (schedule["case_id"] == "DUAL_BUFFER")]
        self.assertEqual(len(cash_event), 1)
        self.assertTrue(pd.isna(cash_event.iloc[0]["instrument_id"]))

        panel = price_frame([value.strftime("%Y-%m-%d") for value in calendar], [10, 10, 9, 9])
        panel = panel.rename(columns={"symbol": "unused"})
        panel["symbol"] = "A"
        panel["synthetic_bar"] = 0
        panel["terminal_settlement_proxy"] = 0
        targets = build_target_share_table(
            panel,
            schedule,
            case_id="DUAL_BUFFER",
            initial_cash=100.0,
            policy=ExplicitFillPolicy("open", 0.0),
        )
        liquidation = targets[
            (targets["execution_date"] == mar_open) & (targets["symbol"] == "A")
        ].iloc[0]
        self.assertAlmostEqual(float(liquidation["target_shares"]), 0.0)

    def test_top10_exit20_buffer_retains_incumbents_and_stays_below_twenty(self) -> None:
        securities = [f"S{i:02d}" for i in range(25)]
        jan = pd.Timestamp("2021-01-29")
        feb = pd.Timestamp("2021-02-26")
        rows = []
        for rank, security_id in enumerate(securities[:20], start=1):
            rows.append({"date": jan, "security_id": security_id, "rank": rank})
        feb_order = securities[10:20] + securities[:5] + securities[20:25] + securities[5:10]
        for rank, security_id in enumerate(feb_order, start=1):
            rows.append({"date": feb, "security_id": security_id, "rank": rank})
        rankings = pd.DataFrame(rows)
        bounds = pd.DataFrame(
            [
                {"security_id": value, "known_start": jan, "known_end": pd.Timestamp("2021-12-31")}
                for value in securities
            ]
        )
        calendar = pd.to_datetime(["2021-01-29", "2021-02-01", "2021-02-26", "2021-03-01"])
        schedule, audit = build_target_schedule(
            rankings,
            [jan, feb],
            calendar,
            bounds,
            {value: value for value in securities},
        )
        buffer = schedule[
            (schedule["date"] == feb) & (schedule["case_id"] == TOP10_EXIT20_BUFFER)
        ]
        self.assertEqual(len(buffer), 15)
        self.assertTrue(set(securities[:5]).issubset(set(buffer["instrument_id"])))
        self.assertTrue(set(securities[10:20]).issubset(set(buffer["instrument_id"])))
        self.assertAlmostEqual(float(buffer["target_weight"].sum()), 1.0)
        forced = schedule[
            (schedule["date"] == feb) & (schedule["case_id"] == TOP10_MONTHLY_REPLACE)
        ]
        self.assertEqual(set(forced["instrument_id"]), set(securities[10:20]))
        maximum = audit[audit["case_id"] == TOP10_EXIT20_BUFFER]["selected_count"].max()
        self.assertLessEqual(int(maximum), 20)

    def test_fewer_than_ten_keeps_unfilled_slots_in_cash(self) -> None:
        date = pd.Timestamp("2021-01-29")
        rankings = pd.DataFrame(
            [{"date": date, "security_id": f"S{i}", "rank": i + 1} for i in range(3)]
        )
        bounds = pd.DataFrame(
            [
                {"security_id": f"S{i}", "known_start": date, "known_end": pd.Timestamp("2021-12-31")}
                for i in range(3)
            ]
        )
        schedule, _ = build_target_schedule(
            rankings,
            [date],
            [date, pd.Timestamp("2021-02-01")],
            bounds,
            {},
        )
        for case_id in (TOP10_MONTHLY_REPLACE, TOP10_EXIT20_BUFFER):
            target = schedule[schedule["case_id"] == case_id]
            self.assertEqual(target["target_weight"].tolist(), [0.1, 0.1, 0.1])
            self.assertAlmostEqual(float(target["target_weight"].sum()), 0.3)
        point_in_time = schedule[schedule["case_id"] == PIT_EQUAL_WEIGHT]
        self.assertAlmostEqual(float(point_in_time["target_weight"].sum()), 1.0)

    def test_membership_exit_is_retried_each_session_until_monthly_rebalance(self) -> None:
        dates = pd.to_datetime(
            ["2021-01-29", "2021-02-01", "2021-02-02", "2021-02-03", "2021-02-04", "2021-02-26"]
        )
        rankings = pd.DataFrame([{"date": dates[0], "security_id": "A", "rank": 1}])
        bounds = pd.DataFrame(
            [{"security_id": "A", "known_start": dates[0], "known_end": dates[1]}]
        )
        schedule, _ = build_target_schedule(rankings, [dates[0], dates[-1]], dates, bounds, {"A": "A"})
        exits = schedule[
            (schedule["case_id"] == TOP10_EXIT20_BUFFER)
            & (schedule["event_type"] == FORCED_MEMBERSHIP_EXIT)
        ]
        self.assertEqual(exits["date"].tolist(), list(dates[2:-1]))
        self.assertTrue((exits["target_weight"] == 0).all())

    def test_close_sizing_defers_buy_when_next_open_is_synthetic(self) -> None:
        dates = pd.to_datetime(["2021-01-29", "2021-02-01", "2021-02-02"])
        rows = []
        for symbol in ("A", "B"):
            for offset, date in enumerate(dates):
                rows.append(
                    {
                        "date": date,
                        "symbol": symbol,
                        "open": 10.0,
                        "close": 10.0,
                        "synthetic_bar": int(symbol == "B" and offset == 1),
                        "terminal_settlement_proxy": 0,
                    }
                )
        panel = pd.DataFrame(rows)
        targets = pd.DataFrame(
            [
                {
                    "case_id": TOP10_EXIT20_BUFFER,
                    "date": dates[0],
                    "event_type": FULL_REBALANCE,
                    "instrument_id": symbol,
                    "target_weight": 0.5,
                }
                for symbol in ("A", "B")
            ]
        )
        shares = build_target_share_table(
            panel,
            targets,
            case_id=TOP10_EXIT20_BUFFER,
            initial_cash=100.0,
            policy=ExplicitFillPolicy("open", 0.0),
        )
        execution = shares[shares["execution_date"] == dates[1]].set_index("symbol")
        self.assertAlmostEqual(float(execution.at["A", "target_shares"]), 5.0)
        self.assertAlmostEqual(float(execution.at["B", "target_shares"]), 0.0)
        self.assertEqual(int(execution.at["B", "execution_deferred_unavailable"]), 1)

    def test_next_open_forced_exit_matches_independent_fifo_ledger(self) -> None:
        dates = pd.to_datetime(["2021-01-29", "2021-02-01", "2021-02-02", "2021-02-03"])
        rows = []
        for symbol, multiplier in (("A", 1.0), ("B", 2.0)):
            for offset, date in enumerate(dates):
                price = (10.0 + offset) * multiplier
                rows.append(
                    {
                        "date": date,
                        "symbol": symbol,
                        "open": price,
                        "high": price * 1.01,
                        "low": price * 0.99,
                        "close": price,
                        "volume": 1_000.0,
                        "synthetic_bar": 0,
                        "terminal_settlement_proxy": 0,
                    }
                )
        panel = pd.DataFrame(rows)
        schedule = pd.DataFrame(
            [
                {
                    "case_id": TOP10_EXIT20_BUFFER,
                    "date": dates[0],
                    "event_type": FULL_REBALANCE,
                    "instrument_id": symbol,
                    "target_weight": 0.5,
                }
                for symbol in ("A", "B")
            ]
            + [
                {
                    "case_id": TOP10_EXIT20_BUFFER,
                    "date": dates[2],
                    "event_type": FORCED_MEMBERSHIP_EXIT,
                    "instrument_id": "A",
                    "target_weight": 0.0,
                }
            ]
        )
        policy = ExplicitFillPolicy("open", 5.0)
        targets = build_target_share_table(
            panel,
            schedule,
            case_id=TOP10_EXIT20_BUFFER,
            initial_cash=1_000.0,
            policy=policy,
        )
        result, positions = run_pybroker_portfolio(
            panel,
            targets,
            initial_cash=1_000.0,
            policy=policy,
        )
        reference = run_reference_portfolio(
            panel,
            targets,
            initial_cash=1_000.0,
            policy=policy,
        )
        differences = cross_check_portfolios(result, positions, reference)
        self.assertLessEqual(max(differences.values()), 1e-9)
        sell = result.orders.reset_index().query("symbol == 'A' and type == 'sell'")
        self.assertEqual(sell["date"].tolist(), [dates[3]])


if __name__ == "__main__":
    unittest.main()
