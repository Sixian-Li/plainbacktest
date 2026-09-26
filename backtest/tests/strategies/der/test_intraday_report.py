from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from quantkit.intraday_sma import IntradaySmaSpec, prepare_intraday_sma_data
from scripts.analyze_intraday_sma_backtest import build_market_figure


class IntradayReportTest(unittest.TestCase):
    def test_market_figure_keeps_all_requested_selectable_series_and_reason_hover(self) -> None:
        count = 520
        close = np.linspace(100.0, 150.0, count)
        raw = pd.DataFrame(
            {
                "date": pd.bdate_range("2020-01-01", periods=count),
                "symbol": ["QQQ"] * count,
                "open": close,
                "high": close + 1,
                "low": close - 1,
                "close": close,
                "volume": [1_000_000] * count,
            }
        )
        prepared = prepare_intraday_sma_data(raw, IntradaySmaSpec()).iloc[250:].copy()
        orders = pd.DataFrame(
            [
                {
                    "date": prepared.iloc[5]["date"],
                    "type": "sell",
                    "fill_price": float(prepared.iloc[5]["close"]),
                    "primary_signal": "SELL_FAST_DROP",
                    "matched_signals": "SELL_FAST_DROP|SELL_COST_STOP",
                    "theoretical_trigger": float(prepared.iloc[5]["close"]),
                    "fill_source": "intraday_trigger",
                    "is_initial_seed": False,
                }
            ]
        )
        figure = build_market_figure(prepared, orders)
        long_smas = [
            trace
            for trace in figure.data
            if isinstance(trace.meta, dict) and trace.meta.get("control_group") == "long_sma"
        ]
        derivatives = [
            trace
            for trace in figure.data
            if isinstance(trace.meta, dict) and trace.meta.get("control_group") == "derivative"
        ]
        self.assertEqual(len(long_smas), 39)
        self.assertTrue(all(trace.visible == "legendonly" for trace in long_smas))
        self.assertEqual(len(derivatives), 4)
        marker = next(
            trace
            for trace in figure.data
            if isinstance(trace.meta, dict) and trace.meta.get("series_key") == "trade_sell"
        )
        self.assertIn("主因", marker.hovertemplate)
        self.assertIn("理论阈值", marker.hovertemplate)

    def test_market_figure_uses_the_configured_symbol_instead_of_qqq(self) -> None:
        count = 520
        close = np.linspace(100.0, 150.0, count)
        raw = pd.DataFrame(
            {
                "date": pd.bdate_range("2020-01-01", periods=count),
                "symbol": ["SPY"] * count,
                "open": close,
                "high": close + 1,
                "low": close - 1,
                "close": close,
                "volume": [1_000_000] * count,
            }
        )
        prepared = prepare_intraday_sma_data(raw, IntradaySmaSpec()).iloc[250:].copy()
        orders = pd.DataFrame(
            columns=[
                "date",
                "type",
                "fill_price",
                "primary_signal",
                "matched_signals",
                "theoretical_trigger",
                "fill_source",
                "is_initial_seed",
            ]
        )
        figure = build_market_figure(prepared, orders, symbol="SPY")
        self.assertEqual(figure.layout.annotations[0].text, "SPY 复权 OHLC、均线与成交原因")
        self.assertEqual(figure.data[0].name, "SPY 复权 OHLC")
        self.assertEqual(figure.data[1].name, "SPY Close")
        self.assertEqual(figure.data[1].meta["series_key"], "spy_close")


if __name__ == "__main__":
    unittest.main()
