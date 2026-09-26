from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

import pandas as pd


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/build_bear_market_annotator.py"
SPEC = importlib.util.spec_from_file_location("build_bear_market_annotator", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class BearMarketAnnotatorTest(unittest.TestCase):
    def test_prepare_prices_uses_only_common_sessions(self) -> None:
        root = Path(self._testMethodName)
        root.mkdir(exist_ok=True)
        self.addCleanup(lambda: root.rmdir())
        qqq = root / "QQQ.csv"
        spy = root / "SPY.csv"
        qqq.write_text(
            "date,symbol,close\n2026-01-02,QQQ,100\n2026-01-05,QQQ,90\n",
            encoding="utf-8",
        )
        spy.write_text(
            "date,symbol,close\n2026-01-01,SPY,80\n2026-01-02,SPY,81\n2026-01-05,SPY,75\n",
            encoding="utf-8",
        )
        self.addCleanup(qqq.unlink)
        self.addCleanup(spy.unlink)

        prices = MODULE.prepare_prices(qqq, spy)

        self.assertEqual(prices["date"].dt.strftime("%Y-%m-%d").tolist(), ["2026-01-02", "2026-01-05"])
        self.assertEqual(prices["QQQ"].tolist(), [100, 90])
        self.assertEqual(prices["SPY"].tolist(), [81, 75])

    def test_html_exposes_annotation_persistence_and_export_contract(self) -> None:
        prices = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"]),
                "QQQ": [100.0, 90.0, 95.0],
                "SPY": [80.0, 75.0, 78.0],
            }
        )
        rendered = MODULE.build_html(
            prices,
            qqq_path=Path("QQQ.csv"),
            spy_path=Path("SPY.csv"),
            qqq_sha256="q" * 64,
            spy_sha256="s" * 64,
        )

        self.assertIn('name="quant-view" content="spy_qqq_bear_market_annotator_v1"', rendered)
        self.assertIn("plotly_selected", rendered)
        self.assertIn("localStorage.setItem", rendered)
        self.assertIn("spy_qqq_bear_markets.json", rendered)
        self.assertIn("spy_qqq_bear_markets.csv", rendered)
        self.assertIn("data-action=\"import-json\"", rendered)
        self.assertIn("maxDrawdown", rendered)
        self.assertIn("window.__bearAnnotator", rendered)
        start = rendered.index('<script type="application/json" id="price-data">')
        start = rendered.index(">", start) + 1
        end = rendered.index("</script>", start)
        embedded = json.loads(rendered[start:end])
        self.assertEqual(embedded["dates"], ["2026-01-02", "2026-01-05", "2026-01-06"])


if __name__ == "__main__":
    unittest.main()
