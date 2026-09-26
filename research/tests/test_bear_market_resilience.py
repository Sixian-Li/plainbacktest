from __future__ import annotations

import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path

import pandas as pd


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/build_bear_market_resilience.py"
SPEC = importlib.util.spec_from_file_location("build_bear_market_resilience", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class BearMarketResilienceTest(unittest.TestCase):
    def test_sp500_snapshot_uses_official_members_and_keeps_unavailable_as_na(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            master = root / "master.csv"
            current = root / "current.csv"
            prices = root / "prices"
            prices.mkdir()
            master.write_text(
                "instrument_id,symbol,company_name\nCUR,CUR,Current Corp\nSTALE,STALE,Stale Corp\n",
                encoding="utf-8",
            )
            current.write_text(
                "as_of_date,symbol,company_name,vendor_instrument_id,vendor_price_available\n"
                "2026-08-11,CUR,Current Corp,CUR,true\n"
                "2026-08-11,MISS,Missing Corp,,false\n",
                encoding="utf-8",
            )
            (prices / "CUR.csv").write_text(
                "date,close\n2026-08-04,10\n",
                encoding="utf-8",
            )

            snapshot, members, series = MODULE.load_sp500_current(master, current, prices)

        self.assertEqual(snapshot, "2026-08-11")
        self.assertEqual(set(members), {"CUR", "MISS"})
        self.assertNotIn("STALE", members)
        self.assertFalse(series["CUR"].empty)
        self.assertTrue(series["MISS"].empty)

    def test_screen_requires_one_positive_interval_and_positive_compound_total(self) -> None:
        intervals = [
            {"interval_id": "bear_1", "label": "大熊市 01", "severity": "major"},
            {"interval_id": "bear_2", "label": "小熊市 02", "severity": "minor"},
        ]

        passing = MODULE.summarize_asset(
            asset={"asset_id": "PASS", "symbol": "PASS", "role": "equity"},
            interval_results=[
                {**intervals[0], "status": "available", "total_return": 0.10, "max_drawdown": -0.08},
                {**intervals[1], "status": "available", "total_return": -0.05, "max_drawdown": -0.20},
            ],
            intervals=intervals,
        )
        negative_total = MODULE.summarize_asset(
            asset={"asset_id": "LOSS", "symbol": "LOSS", "role": "equity"},
            interval_results=[
                {**intervals[0], "status": "available", "total_return": 0.02, "max_drawdown": -0.04},
                {**intervals[1], "status": "available", "total_return": -0.10, "max_drawdown": -0.15},
            ],
            intervals=intervals,
        )
        never_positive = MODULE.summarize_asset(
            asset={"asset_id": "FLAT", "symbol": "FLAT", "role": "equity"},
            interval_results=[
                {**intervals[0], "status": "available", "total_return": 0.0, "max_drawdown": -0.01},
                {**intervals[1], "status": "available", "total_return": 0.0, "max_drawdown": -0.02},
            ],
            intervals=intervals,
        )

        self.assertTrue(passing["passes_screen"])
        self.assertAlmostEqual(passing["all_compound_return"], 1.10 * 0.95 - 1)
        self.assertEqual(passing["positive_interval_count"], 1)
        self.assertEqual(passing["worst_interval_label"], "小熊市 02")
        self.assertFalse(negative_total["passes_screen"])
        self.assertFalse(never_positive["passes_screen"])

    def test_missing_endpoint_is_na_and_never_treated_as_zero(self) -> None:
        series = pd.Series(
            [100.0, 110.0],
            index=pd.to_datetime(["2022-01-03", "2022-01-04"]),
            name="close",
        )
        missing = MODULE.calculate_interval_result(
            series,
            {"start": "2020-02-19", "end": "2020-03-23"},
        )
        available = MODULE.calculate_interval_result(
            series,
            {"start": "2022-01-03", "end": "2022-01-04"},
        )

        self.assertEqual(missing["status"], "not_yet_listed")
        self.assertIsNone(missing["total_return"])
        self.assertEqual(available["status"], "available")
        self.assertAlmostEqual(available["total_return"], 0.10)

    def test_nasdaq_snapshot_excludes_stale_former_members(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "ndx.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr(
                    "OLD.csv",
                    "Date,CompanyName,Symbol,Open,High,Low,Close,Volume,Unadjusted Close,InIndex\n"
                    "2025-01-02,Old Corp,OLD,1,1,1,1,1,1,1\n",
                )
                bundle.writestr(
                    "CUR.csv",
                    "Date,CompanyName,Symbol,Open,High,Low,Close,Volume,Unadjusted Close,InIndex\n"
                    "2025-01-02,Current Corp,CUR,1,1,1,1,1,1,0\n"
                    "2026-08-04,Current Corp,CUR,2,2,2,2,1,2,1\n",
                )

            snapshot, members, prices = MODULE.load_nasdaq100_current(archive)

        self.assertEqual(snapshot, "2026-08-04")
        self.assertEqual(list(members), ["CUR"])
        self.assertEqual(prices["CUR"].index[-1], pd.Timestamp("2026-08-04"))

    def test_html_exposes_filter_sort_detail_and_risk_contract(self) -> None:
        intervals = [
            {
                "ordinal": 1,
                "interval_id": "bear_1",
                "label": "大熊市 01",
                "severity": "major",
                "start": "2020-02-19",
                "end": "2020-03-23",
                "SPY": {"total_return": -0.30},
                "QQQ": {"total_return": -0.25},
            }
        ]
        summary = [
            {
                "asset_id": "AAA",
                "symbol": "AAA",
                "company_name": "Alpha",
                "role": "equity",
                "universe": "Both",
                "sp500_current": True,
                "nasdaq100_current": True,
                "available_interval_count": 1,
                "positive_interval_count": 1,
                "major_compound_return": 0.05,
                "minor_compound_return": None,
                "all_compound_return": 0.05,
                "passes_screen": True,
                "worst_single_bear_return": 0.05,
                "worst_interval_label": "大熊市 01",
            }
        ]
        details = [
            {
                "asset_id": "AAA",
                "symbol": "AAA",
                "interval_id": "bear_1",
                "label": "大熊市 01",
                "severity": "major",
                "status": "available",
                "total_return": 0.05,
                "max_drawdown": -0.15,
                "spy_return": -0.30,
                "qqq_return": -0.25,
            }
        ]

        rendered = MODULE.build_html(
            summaries=summary,
            details=details,
            intervals=intervals,
            metadata={"counts": {"screened_assets": 1, "analyzed_assets": 1}},
            summary_csv_name="summary.csv",
            detail_csv_name="detail.csv",
        )

        self.assertIn('name="quant-view" content="bear_market_resilience_current_constituents_v1"', rendered)
        self.assertIn('data-control="screened-only"', rendered)
        self.assertIn('data-sort="all_compound_return"', rendered)
        self.assertIn('id="detail-bars"', rendered)
        self.assertIn("最差单次熊市收益", rendered)
        self.assertIn("window.__bearResilience", rendered)
        self.assertIn("summary.csv", rendered)
        self.assertIn("detail.csv", rendered)

    def test_real_outputs_have_complete_matrix_and_consistent_screen(self) -> None:
        root = Path(__file__).resolve().parents[2]
        summary_path = root / "research/market_views/bear_market_resilience_current_constituents_summary.csv"
        detail_path = root / "research/market_views/bear_market_resilience_current_constituents_detail.csv"
        metadata_path = root / "research/market_views/bear_market_resilience_current_constituents.json"
        if not (summary_path.exists() and detail_path.exists() and metadata_path.exists()):
            self.skipTest("generated bear-market resilience outputs are not present")

        summary = pd.read_csv(summary_path)
        detail = pd.read_csv(detail_path)
        metadata = __import__("json").loads(metadata_path.read_text(encoding="utf-8"))
        counts = metadata["counts"]

        self.assertEqual(len(summary), counts["analyzed_assets"])
        self.assertEqual(len(detail), len(summary) * counts["intervals"])
        self.assertFalse(summary["asset_id"].duplicated().any())
        self.assertFalse(detail.duplicated(["asset_id", "interval_id"]).any())
        self.assertEqual(
            int(summary["passes_screen"].sum()),
            counts["screened_assets"],
        )
        expected = (summary["positive_interval_count"] >= 1) & (summary["all_compound_return"] > 0)
        self.assertTrue((summary["passes_screen"] == expected).all())
        unavailable = detail["status"] != MODULE.AVAILABLE
        self.assertTrue(detail.loc[unavailable, "total_return"].isna().all())
        self.assertEqual(
            detail.groupby("asset_id").size().nunique(),
            1,
        )
        available = detail[detail["status"] == MODULE.AVAILABLE].copy()
        available["growth"] = 1 + available["total_return"]
        all_compound = available.groupby("asset_id")["growth"].prod() - 1
        major_compound = available[available["severity"] == "major"].groupby("asset_id")["growth"].prod() - 1
        minor_compound = available[available["severity"] == "minor"].groupby("asset_id")["growth"].prod() - 1
        indexed = summary.set_index("asset_id")
        for asset_id, value in all_compound.items():
            self.assertAlmostEqual(indexed.at[asset_id, "all_compound_return"], value)
        for asset_id, value in major_compound.items():
            self.assertAlmostEqual(indexed.at[asset_id, "major_compound_return"], value)
        for asset_id, value in minor_compound.items():
            self.assertAlmostEqual(indexed.at[asset_id, "minor_compound_return"], value)


if __name__ == "__main__":
    unittest.main()
