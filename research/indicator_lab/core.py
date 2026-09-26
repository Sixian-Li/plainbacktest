"""Pure indicator calculations and the extensible analysis-plugin contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Protocol, Sequence

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKTEST_ROOT = PROJECT_ROOT / "backtest"
if str(BACKTEST_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKTEST_ROOT))

from quantkit.stochrsi import wilder_rsi  # noqa: E402


MA_METHODS = ("SMA", "EMA", "WMA", "RMA")


def _method(value: str | None, *, allow_none: bool = False) -> str | None:
    normalized = "" if value is None else str(value).strip().upper()
    if allow_none and normalized in {"", "NONE", "无"}:
        return None
    if normalized not in MA_METHODS:
        raise ValueError(f"Unsupported smoothing method: {value!r}")
    return normalized


@dataclass(frozen=True)
class MovingAverageSpec:
    """One price moving average with an optional second smoothing pass."""

    label: str
    method: str
    period: int
    smooth_method: str | None = None
    smooth_period: int | None = None

    def __post_init__(self) -> None:
        label = str(self.label).strip()
        if not label:
            raise ValueError("Moving-average label must not be empty")
        method = _method(self.method)
        period = int(self.period)
        if period < 1:
            raise ValueError("Moving-average period must be at least 1")
        smooth_method = _method(self.smooth_method, allow_none=True)
        smooth_period = None if smooth_method is None else int(self.smooth_period or 1)
        if smooth_period is not None and smooth_period < 1:
            raise ValueError("Second smoothing period must be at least 1")
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "method", method)
        object.__setattr__(self, "period", period)
        object.__setattr__(self, "smooth_method", smooth_method)
        object.__setattr__(self, "smooth_period", smooth_period)


@dataclass(frozen=True)
class StochRsiSpec:
    """RSI, stochastic range, and the two independent smoothing passes."""

    rsi_period: int = 14
    stoch_period: int = 14
    k_period: int = 3
    d_period: int = 3
    k_method: str = "SMA"
    d_method: str = "SMA"

    def __post_init__(self) -> None:
        for name in ("rsi_period", "stoch_period"):
            value = int(getattr(self, name))
            if value < 2:
                raise ValueError(f"{name} must be at least 2")
            object.__setattr__(self, name, value)
        for name in ("k_period", "d_period"):
            value = int(getattr(self, name))
            if value < 1:
                raise ValueError(f"{name} must be at least 1")
            object.__setattr__(self, name, value)
        object.__setattr__(self, "k_method", _method(self.k_method))
        object.__setattr__(self, "d_method", _method(self.d_method))


@dataclass(frozen=True)
class LabConfig:
    """Symbol-independent study preset that can be reused with another dataset."""

    moving_averages: tuple[MovingAverageSpec, ...]
    stochrsi: StochRsiSpec
    lower_threshold: float = 0.20
    upper_threshold: float = 0.80
    price_scale: str = "log"

    def __post_init__(self) -> None:
        if not 0.0 <= float(self.lower_threshold) < float(self.upper_threshold) <= 1.0:
            raise ValueError("Thresholds must satisfy 0 <= lower < upper <= 1")
        if self.price_scale not in {"log", "linear"}:
            raise ValueError("price_scale must be 'log' or 'linear'")
        labels = [spec.label for spec in self.moving_averages]
        if len(labels) != len(set(labels)):
            raise ValueError("Moving-average labels must be unique")

    def to_json(self) -> str:
        payload = {"schema_version": 1, **asdict(self)}
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    @classmethod
    def from_json(cls, payload: str) -> "LabConfig":
        raw = json.loads(payload)
        if int(raw.get("schema_version", 1)) != 1:
            raise ValueError("Unsupported indicator-lab config schema version")
        return cls(
            moving_averages=tuple(
                MovingAverageSpec(**item) for item in raw.get("moving_averages", [])
            ),
            stochrsi=StochRsiSpec(**raw["stochrsi"]),
            lower_threshold=float(raw.get("lower_threshold", 0.20)),
            upper_threshold=float(raw.get("upper_threshold", 0.80)),
            price_scale=str(raw.get("price_scale", "log")),
        )


def _rma(values: pd.Series, period: int) -> pd.Series:
    """Wilder moving average, seeded by each valid segment's first SMA."""

    series = pd.Series(values, dtype=float)
    if period == 1:
        return series.copy()
    raw = series.to_numpy(dtype=float)
    result = np.full(len(raw), np.nan, dtype=float)
    index = 0
    while index < len(raw):
        while index < len(raw) and not np.isfinite(raw[index]):
            index += 1
        start = index
        while index < len(raw) and np.isfinite(raw[index]):
            index += 1
        end = index
        if end - start < period:
            continue
        seed_index = start + period - 1
        result[seed_index] = float(np.mean(raw[start : seed_index + 1]))
        for position in range(seed_index + 1, end):
            result[position] = (
                result[position - 1] * (period - 1) + raw[position]
            ) / period
    return pd.Series(result, index=series.index, dtype=float)


def moving_average(values: pd.Series, *, method: str, period: int) -> pd.Series:
    """Apply SMA, EMA, linearly weighted WMA, or Wilder RMA."""

    normalized = _method(method)
    period = int(period)
    if period < 1:
        raise ValueError("Moving-average period must be at least 1")
    series = pd.Series(values, dtype=float)
    if normalized == "SMA":
        return series.rolling(period, min_periods=period).mean()
    if normalized == "EMA":
        return series.ewm(span=period, adjust=False, min_periods=period).mean()
    if normalized == "WMA":
        weights = np.arange(1, period + 1, dtype=float)
        denominator = float(weights.sum())
        return series.rolling(period, min_periods=period).apply(
            lambda window: float(np.dot(window, weights) / denominator), raw=True
        )
    return _rma(series, period)


def compute_moving_averages(
    close: pd.Series, specs: Sequence[MovingAverageSpec]
) -> dict[str, pd.Series]:
    labels = [spec.label for spec in specs]
    if len(labels) != len(set(labels)):
        raise ValueError("Moving-average labels must be unique")
    result: dict[str, pd.Series] = {}
    for spec in specs:
        line = moving_average(close, method=spec.method, period=spec.period)
        if spec.smooth_method is not None:
            line = moving_average(
                line, method=spec.smooth_method, period=spec.smooth_period or 1
            )
        result[spec.label] = line
    return result


def compute_stochrsi(close: pd.Series, spec: StochRsiSpec) -> pd.DataFrame:
    """Return Wilder RSI, raw StochRSI, first-smoothed K, and second-smoothed D."""

    values = pd.Series(close, dtype=float)
    rsi = wilder_rsi(values, spec.rsi_period)
    low = rsi.rolling(spec.stoch_period, min_periods=spec.stoch_period).min()
    high = rsi.rolling(spec.stoch_period, min_periods=spec.stoch_period).max()
    span = high - low
    raw = (rsi - low) / span
    raw.loc[span.eq(0.0) & low.notna()] = 0.5
    k = moving_average(raw, method=spec.k_method, period=spec.k_period)
    d = moving_average(k, method=spec.d_method, period=spec.d_period)
    return pd.DataFrame({"RSI": rsi / 100.0, "Raw StochRSI": raw, "K": k, "D": d})


@dataclass(frozen=True)
class PluginResult:
    price_lines: Mapping[str, pd.Series]
    oscillator_lines: Mapping[str, pd.Series]
    signal_markers: Mapping[str, pd.Series]
    description: str


class AnalysisPlugin(Protocol):
    plugin_id: str
    label: str

    def calculate(self, frame: pd.DataFrame, parameters: Any) -> PluginResult: ...


class PluginRegistry:
    """Small registry boundary for later indicator and signal strategy modules."""

    def __init__(self) -> None:
        self._plugins: dict[str, AnalysisPlugin] = {}

    def register(self, plugin: AnalysisPlugin) -> None:
        if plugin.plugin_id in self._plugins:
            raise ValueError(f"Duplicate plugin id: {plugin.plugin_id}")
        self._plugins[plugin.plugin_id] = plugin

    def ids(self) -> tuple[str, ...]:
        return tuple(self._plugins)

    def run(self, plugin_id: str, frame: pd.DataFrame, parameters: Any) -> PluginResult:
        try:
            plugin = self._plugins[plugin_id]
        except KeyError as error:
            raise KeyError(f"Unknown plugin id: {plugin_id}") from error
        return plugin.calculate(frame, parameters)


class MovingAveragePlugin:
    plugin_id = "moving_averages"
    label = "Moving averages"

    def calculate(
        self, frame: pd.DataFrame, parameters: Sequence[MovingAverageSpec]
    ) -> PluginResult:
        return PluginResult(
            price_lines=compute_moving_averages(frame["close"], parameters),
            oscillator_lines={},
            signal_markers={},
            description="Configurable price moving averages and optional second smoothing.",
        )


class StochRsiPlugin:
    plugin_id = "stochrsi"
    label = "Stochastic RSI"

    def calculate(self, frame: pd.DataFrame, parameters: StochRsiSpec) -> PluginResult:
        calculated = compute_stochrsi(frame["close"], parameters)
        return PluginResult(
            price_lines={},
            oscillator_lines={column: calculated[column] for column in calculated},
            signal_markers={},
            description="Wilder RSI, raw StochRSI, K first smoothing, and D second smoothing.",
        )


DEFAULT_REGISTRY = PluginRegistry()
DEFAULT_REGISTRY.register(MovingAveragePlugin())
DEFAULT_REGISTRY.register(StochRsiPlugin())


def parse_moving_average_rows(rows: Sequence[Mapping[str, Any]]) -> tuple[MovingAverageSpec, ...]:
    """Convert editable UI rows into validated, enabled moving-average specs."""

    specs: list[MovingAverageSpec] = []
    for row in rows:
        if not bool(row.get("enabled", True)):
            continue
        method = str(row.get("method", "SMA"))
        period = int(row.get("period", 1))
        smooth_method = row.get("smooth_method")
        label = str(row.get("label") or f"{method.upper()} {period}")
        specs.append(
            MovingAverageSpec(
                label=label,
                method=method,
                period=period,
                smooth_method=smooth_method,
                smooth_period=row.get("smooth_period"),
            )
        )
    labels = [spec.label for spec in specs]
    if len(labels) != len(set(labels)):
        raise ValueError("启用的均线名称不能重复。")
    return tuple(specs)
