from __future__ import annotations

import unittest

from scripts.validate_sp500_market_sources import (
    build_recommendations,
    compare_series,
    parse_eastmoney,
    parse_tiingo,
    parse_twelve,
    percentile,
    TransportError,
    tiingo_symbol,
    validate_rows,
)


class MarketSourceValidationTest(unittest.TestCase):
    def test_parse_eastmoney_close_is_third_field(self) -> None:
        payload = {
            "rc": 0,
            "data": {
                "code": "AAPL",
                "name": "Apple",
                "klines": ["2026-08-12,305.10,302.25,305.66,300.57,41657768,1,1.67,-0.87"],
            },
        }
        rows, metadata = parse_eastmoney(payload, "AAPL")
        self.assertEqual(rows[0]["open"], 305.10)
        self.assertEqual(rows[0]["close"], 302.25)
        self.assertEqual(rows[0]["high"], 305.66)
        self.assertEqual(metadata["returned_symbol"], "AAPL")

    def test_parse_twelve_batch_and_sort(self) -> None:
        payload = {
            "AAPL": {
                "values": [
                    {"datetime": "2026-08-12", "open": "2", "high": "3", "low": "1", "close": "2.5", "volume": "20"},
                    {"datetime": "2026-08-11", "open": "1", "high": "2", "low": "0.5", "close": "1.5", "volume": "10"},
                ]
            }
        }
        rows = parse_twelve(payload, "AAPL")
        self.assertEqual([row["date"] for row in rows], ["2026-08-11", "2026-08-12"])

    def test_parse_tiingo_raw_and_adjusted(self) -> None:
        payload = [
            {
                "date": "2026-08-12T00:00:00.000Z",
                "open": 10,
                "high": 12,
                "low": 9,
                "close": 11,
                "volume": 100,
                "adjOpen": 5,
                "adjHigh": 6,
                "adjLow": 4.5,
                "adjClose": 5.5,
                "adjVolume": 200,
            }
        ]
        self.assertEqual(parse_tiingo(payload, adjusted=False)[0]["close"], 11)
        self.assertEqual(parse_tiingo(payload, adjusted=True)[0]["close"], 5.5)

    def test_dot_alias_is_explicit(self) -> None:
        self.assertEqual(tiingo_symbol("BRK.B"), "BRK-B")
        self.assertEqual(tiingo_symbol("AAPL"), "AAPL")

    def test_percentile_interpolates(self) -> None:
        self.assertEqual(percentile([0.0, 1.0], 0.95), 0.95)
        self.assertIsNone(percentile([], 0.5))

    def test_pairwise_comparison_uses_relative_price_and_return_differences(self) -> None:
        left = [
            {"date": "2026-08-10", "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1000},
            {"date": "2026-08-11", "open": 101, "high": 104, "low": 100, "close": 103, "volume": 1100},
        ]
        right = [
            {"date": "2026-08-10", "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1000},
            {"date": "2026-08-11", "open": 101, "high": 104, "low": 100, "close": 103.01, "volume": 1090},
        ]
        result = compare_series("X", "raw", "left", left, "right", right)
        self.assertEqual(result["common_dates"], 2)
        self.assertAlmostEqual(result["close_relative_max"], 0.01 / 103.01)
        self.assertGreater(result["close_return_abs_diff_max"], 0)
        self.assertEqual(result["comparison_status"], "insufficient_overlap")

    def test_validate_rows_rejects_bad_ohlc(self) -> None:
        rows = [{"date": "2026-08-12", "open": 10, "high": 9, "low": 8, "close": 10, "volume": 1}]
        with self.assertRaises(Exception):
            validate_rows(rows, "fixture", "X")

    def test_transport_error_is_a_provider_error(self) -> None:
        self.assertTrue(issubclass(TransportError, RuntimeError))

    def test_recommendations_fall_back_to_available_independent_pair(self) -> None:
        rows = [
            {
                "symbol": "AAPL",
                "mode": mode,
                "left": "twelve_data",
                "right": "tiingo",
                "left_rows": 60,
                "right_rows": 60,
                "common_dates": 60,
                "latest_common_date": "2026-08-12",
                "left_only_dates": [],
                "right_only_dates": [],
                **{f"{field}_relative_median": 0 for field in ("open", "high", "low", "close", "volume")},
                **{f"{field}_relative_p95": 0 for field in ("open", "high", "low", "close", "volume")},
                **{f"{field}_relative_max": 0 for field in ("open", "high", "low", "close", "volume")},
                "close_return_abs_diff_p95": 0,
                "close_return_abs_diff_max": 0,
                "comparison_status": "candidate_pass",
            }
            for mode in ("raw", "adjusted")
        ]
        recommendations = build_recommendations(rows)
        self.assertEqual(recommendations["basis_pair"], ["tiingo", "twelve_data"])

    def test_pairwise_can_compare_two_sources_when_third_is_unavailable(self) -> None:
        rows = [
            {
                "date": f"2026-07-{day:02d}",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10,
                "volume": 100,
            }
            for day in range(1, 22)
        ]
        result = compare_series("X", "raw", "twelve_data", rows, "tiingo", rows)
        self.assertEqual(result["comparison_status"], "candidate_pass")


if __name__ == "__main__":
    unittest.main()
