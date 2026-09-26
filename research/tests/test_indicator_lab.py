from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd
from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[2]
LAB_DIR = ROOT / "research/indicator_lab"
BACKTEST_DIR = ROOT / "backtest"
sys.path.insert(0, str(LAB_DIR))
sys.path.insert(0, str(BACKTEST_DIR))

from charts import build_indicator_figure
from core import (
    DEFAULT_REGISTRY,
    LabConfig,
    MovingAverageSpec,
    PluginRegistry,
    StochRsiSpec,
    compute_moving_averages,
    compute_stochrsi,
    moving_average,
    parse_moving_average_rows,
)
from data_sources import canonicalize_frame, discover_approved_datasets
from quantkit.stochrsi import prepare_stochrsi_data


class IndicatorLabTest(unittest.TestCase):
    def frame(self, rows: int = 600) -> pd.DataFrame:
        close = 100 + np.sin(np.arange(rows) / 9) * 4 + np.arange(rows) / 8
        return pd.DataFrame(
            {
                "date": pd.date_range("2020-01-01", periods=rows, freq="D"),
                "symbol": "TEST",
                "open": close - 0.5,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": np.arange(rows) + 1_000,
            }
        )

    def test_workspace_discovery_includes_approved_and_excludes_provisional(self) -> None:
        datasets = discover_approved_datasets(ROOT)
        for symbol in ("AAPL", "NVDA", "MSFT", "GOOGL", "QQQ", "SPY", "RKLB"):
            self.assertIn(symbol, datasets)
        self.assertNotIn("VOO", datasets)

    def test_canonicalize_sorts_and_fills_expected_symbol(self) -> None:
        raw = pd.DataFrame(
            {"Date": ["2024-01-03", "2024-01-02"], "Close": [11.0, 10.0]}
        )
        result = canonicalize_frame(raw, expected_symbol="ABC")
        self.assertEqual(result["symbol"].tolist(), ["ABC", "ABC"])
        self.assertEqual(result["close"].tolist(), [10.0, 11.0])

    def test_canonicalize_rejects_duplicate_dates_and_wrong_symbol(self) -> None:
        raw = pd.DataFrame(
            {
                "date": ["2024-01-02", "2024-01-02"],
                "symbol": ["ABC", "XYZ"],
                "close": [10.0, 11.0],
            }
        )
        with self.assertRaises(ValueError):
            canonicalize_frame(raw, expected_symbol="ABC")
        multi_symbol = raw.assign(date=["2024-01-02", "2024-01-03"])
        with self.assertRaises(ValueError):
            canonicalize_frame(multi_symbol)

    def test_sma_wma_and_rma_values(self) -> None:
        values = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        self.assertEqual(
            moving_average(values, method="SMA", period=3).dropna().tolist(),
            [2.0, 3.0, 4.0],
        )
        self.assertAlmostEqual(
            moving_average(values, method="WMA", period=3).iloc[2], 14.0 / 6.0
        )
        rma = moving_average(values, method="RMA", period=3)
        self.assertAlmostEqual(rma.iloc[2], 2.0)
        self.assertAlmostEqual(rma.iloc[3], (2.0 * 2 + 4.0) / 3.0)

    def test_second_smoothing_matches_explicit_two_pass_calculation(self) -> None:
        close = self.frame()["close"]
        spec = MovingAverageSpec("Smooth SMA", "SMA", 20, "EMA", 5)
        actual = compute_moving_averages(close, (spec,))["Smooth SMA"]
        expected = moving_average(
            moving_average(close, method="SMA", period=20), method="EMA", period=5
        )
        np.testing.assert_allclose(actual, expected, equal_nan=True)

    def test_raw_stochrsi_matches_formal_quantkit_implementation(self) -> None:
        frame = self.frame()
        actual = compute_stochrsi(frame["close"], StochRsiSpec(14, 14, 1, 1))
        expected = prepare_stochrsi_data(frame, 14)["stochrsi"]
        np.testing.assert_allclose(
            actual["Raw StochRSI"], expected, equal_nan=True, atol=0.0, rtol=0.0
        )

    def test_k_and_d_are_first_and_second_smoothing(self) -> None:
        actual = compute_stochrsi(self.frame()["close"], StochRsiSpec(14, 14, 3, 3))
        expected_k = actual["Raw StochRSI"].rolling(3, min_periods=3).mean()
        expected_d = expected_k.rolling(3, min_periods=3).mean()
        np.testing.assert_allclose(actual["K"], expected_k, equal_nan=True)
        np.testing.assert_allclose(actual["D"], expected_d, equal_nan=True)
        self.assertEqual(actual["Raw StochRSI"].first_valid_index(), 27)
        self.assertEqual(actual["K"].first_valid_index(), 29)
        self.assertEqual(actual["D"].first_valid_index(), 31)

    def test_config_round_trip_is_symbol_independent(self) -> None:
        config = LabConfig(
            moving_averages=(MovingAverageSpec("SMA 100 > EMA 10", "SMA", 100, "EMA", 10),),
            stochrsi=StochRsiSpec(14, 21, 5, 3, "EMA", "SMA"),
            lower_threshold=0.15,
            upper_threshold=0.85,
            price_scale="linear",
        )
        restored = LabConfig.from_json(config.to_json())
        self.assertEqual(restored, config)
        self.assertNotIn("symbol", json.loads(config.to_json()))

    def test_row_parser_and_registry_contract(self) -> None:
        specs = parse_moving_average_rows(
            [
                {"enabled": False, "label": "off", "method": "SMA", "period": 10},
                {
                    "enabled": True,
                    "label": "EMA 30",
                    "method": "EMA",
                    "period": 30,
                    "smooth_method": "None",
                    "smooth_period": 1,
                },
            ]
        )
        self.assertEqual(specs, (MovingAverageSpec("EMA 30", "EMA", 30),))
        self.assertEqual(DEFAULT_REGISTRY.ids(), ("moving_averages", "stochrsi"))
        result = DEFAULT_REGISTRY.run("moving_averages", self.frame(), specs)
        self.assertEqual(set(result.price_lines), {"EMA 30"})
        with self.assertRaises(KeyError):
            PluginRegistry().run("missing", self.frame(), None)

    def test_chart_contains_two_panels_and_requested_layers(self) -> None:
        frame = self.frame(100)
        ma = {"SMA 10": frame["close"].rolling(10).mean()}
        oscillator = {"Raw StochRSI": pd.Series(np.linspace(0, 1, 100)), "K": pd.Series(np.linspace(0.1, 0.9, 100))}
        figure = build_indicator_figure(
            frame,
            symbol="TEST",
            price_lines=ma,
            oscillator_lines=oscillator,
            lower_threshold=0.2,
            upper_threshold=0.8,
            price_scale="log",
            price_style="Candlestick",
        )
        self.assertEqual([trace.name for trace in figure.data], ["TEST", "SMA 10", "Raw StochRSI", "K"])
        self.assertEqual(figure.layout.yaxis.type, "log")
        self.assertEqual(tuple(figure.layout.yaxis2.range), (-0.03, 1.03))
        self.assertEqual(figure.layout.paper_bgcolor, "#ffffff")
        self.assertEqual(figure.layout.plot_bgcolor, "#f8fafc")
        self.assertEqual(figure.layout.font.color, "#0f172a")
        self.assertEqual(figure.layout.yaxis.gridcolor, "#cbd5e1")

    def test_streamlit_app_renders_and_reacts_to_parameters(self) -> None:
        app = AppTest.from_file(
            str(LAB_DIR / "app.py"), default_timeout=30
        ).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.title[0].value, "Quant 动态指标与策略实验台")
        self.assertEqual(len(app.get("plotly_chart")), 1)
        app.selectbox[0].select("MSFT")
        app.number_input[0].set_value(21)
        app.number_input[2].set_value(5)
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(app.metric[0].value, "MSFT")
        rendered_config = json.loads(app.code[0].value)
        self.assertEqual(rendered_config["stochrsi"]["rsi_period"], 21)
        self.assertEqual(rendered_config["stochrsi"]["k_period"], 5)
        preset = LabConfig(
            moving_averages=(
                MovingAverageSpec("EMA 33 > RMA 4", "EMA", 33, "RMA", 4),
            ),
            stochrsi=StochRsiSpec(21, 34, 5, 7, "EMA", "RMA"),
            lower_threshold=0.10,
            upper_threshold=0.90,
            price_scale="linear",
        )
        app.get("file_uploader")[0].upload(
            "preset.json", preset.to_json().encode("utf-8"), "application/json"
        ).run()
        app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.metric[0].value, "MSFT")
        self.assertEqual(LabConfig.from_json(app.code[0].value), preset)

    def test_streamlit_app_accepts_an_unapproved_uploaded_data_source(self) -> None:
        uploaded = self.frame(260).rename(
            columns={column: column.title() for column in self.frame(260).columns}
        )
        uploaded["Symbol"] = "CUSTOM"
        app = AppTest.from_file(
            str(LAB_DIR / "app.py"), default_timeout=30
        ).run()
        app.radio[0].set_value("上传 canonical CSV").run()
        app.get("file_uploader")[0].upload(
            "custom.csv", uploaded.to_csv(index=False).encode("utf-8"), "text/csv"
        ).run()
        self.assertFalse(app.exception)
        metrics = {metric.label: metric.value for metric in app.metric}
        self.assertEqual(metrics["标的"], "CUSTOM")
        self.assertEqual(metrics["批准状态"], "uploaded_unapproved")
        self.assertEqual(metrics["全部交易日"], "260")
        self.assertEqual(len(app.get("plotly_chart")), 1)


if __name__ == "__main__":
    unittest.main()
