from __future__ import annotations

import unittest

import pandas as pd

from scripts.analyze_rklb_intraday_sma_grid import stable_selection
from scripts.run_rklb_intraday_sma_grid import _case_id


class RklbIntradaySmaGridTest(unittest.TestCase):
    def test_case_id_encodes_all_dimensions(self) -> None:
        self.assertEqual(
            _case_id("RKLB", 5, 115, 3, 2),
            "RKLB_c05_n115_s03_b02",
        )

    def test_stable_selection_uses_three_adjacent_windows(self) -> None:
        rows = []
        for sell in (1.0, 2.0, 3.0):
            for buy in (1.0, 2.0, 3.0):
                for window in (15, 17, 19, 21, 23):
                    cagr = 1.0
                    if sell == 2 and buy == 3:
                        cagr = {15: 4.0, 17: 8.0, 19: 9.0, 21: 8.0, 23: 4.0}[window]
                    rows.append(
                        {
                            "case_id": f"{sell}-{buy}-{window}",
                            "sma_window": window,
                            "sell_below_sma_pct": sell,
                            "buy_above_sma_pct": buy,
                            "cagr_pct": cagr,
                            "sharpe": cagr / 10,
                            "max_drawdown_pct": -20.0,
                            "order_count": 10,
                        }
                    )
        selected, diagnostics = stable_selection(pd.DataFrame(rows))
        self.assertEqual(int(selected["sma_window"]), 19)
        self.assertEqual(float(selected["sell_below_sma_pct"]), 2.0)
        self.assertEqual(float(selected["buy_above_sma_pct"]), 3.0)
        self.assertEqual(len(diagnostics), 27)


if __name__ == "__main__":
    unittest.main()
