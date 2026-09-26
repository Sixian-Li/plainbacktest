"""Approved workspace data discovery and canonical CSV validation."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import BinaryIO

import pandas as pd


CANONICAL_ORDER = ("date", "symbol", "open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class DatasetRef:
    symbol: str
    path: Path
    status: str
    source_group: str

    @property
    def label(self) -> str:
        return f"{self.symbol} · {self.source_group}"


def discover_approved_datasets(project_root: Path) -> dict[str, DatasetRef]:
    """Discover registered assets plus the approved S&P 500 history price files."""

    root = Path(project_root)
    registry_path = root / "data/source_registry.yaml"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    datasets: dict[str, DatasetRef] = {}
    for entry in registry.get("datasets", []):
        if entry.get("registered_status") != "approved":
            continue
        symbol = str(entry["symbol"]).upper()
        path = root / str(entry["output"])
        if path.is_file():
            datasets[symbol] = DatasetRef(
                symbol=symbol,
                path=path,
                status="approved",
                source_group="已登记批准数据",
            )

    equity_dir = root / "data/processed/daily/equities"
    if equity_dir.is_dir():
        for path in equity_dir.glob("*.csv"):
            symbol = path.stem.upper()
            datasets.setdefault(
                symbol,
                DatasetRef(
                    symbol=symbol,
                    path=path,
                    status="approved_sp500_history",
                    source_group="批准 S&P 500 历史证券日线",
                ),
            )
    return dict(sorted(datasets.items()))


def canonicalize_frame(
    frame: pd.DataFrame, *, expected_symbol: str | None = None
) -> pd.DataFrame:
    """Validate and normalize a daily-price frame without changing price semantics."""

    if frame.empty:
        raise ValueError("数据文件没有行。")
    renamed = {column: str(column).strip().lower() for column in frame.columns}
    if len(set(renamed.values())) != len(renamed):
        raise ValueError("列名在忽略大小写后重复。")
    data = frame.rename(columns=renamed).copy()
    missing = {"date", "close"}.difference(data.columns)
    if missing:
        raise ValueError(f"缺少必要列：{sorted(missing)}")

    symbol = expected_symbol.upper() if expected_symbol else None
    if "symbol" not in data.columns:
        if symbol is None:
            raise ValueError("上传文件缺少 Symbol 列，请在界面填写标的代码。")
        data["symbol"] = symbol
    data["symbol"] = data["symbol"].astype(str).str.strip().str.upper()
    if data["symbol"].eq("").any():
        raise ValueError("Symbol 不能留空。")
    if symbol is not None:
        unexpected = sorted(set(data["symbol"]).difference({symbol}))
        if unexpected:
            raise ValueError(f"文件包含与 {symbol} 不符的标的：{unexpected[:5]}")
    elif data["symbol"].nunique() != 1:
        raise ValueError("每次只能载入一个标的；请先把多标的文件拆开。")

    data["date"] = pd.to_datetime(data["date"], errors="raise").dt.normalize()
    if data["date"].duplicated().any():
        raise ValueError("数据包含重复交易日期。")
    for column in ("open", "high", "low", "close", "volume"):
        if column in data.columns:
            data[column] = pd.to_numeric(data[column], errors="raise")
    if data["close"].isna().any() or (data["close"] <= 0).any():
        raise ValueError("Close 必须全部为正数且不能缺失。")
    for column in ("open", "high", "low"):
        if column in data.columns and (
            data[column].isna().any() or (data[column] <= 0).any()
        ):
            raise ValueError(f"{column.title()} 必须全部为正数且不能缺失。")
    if "volume" in data.columns and (
        data["volume"].isna().any() or (data["volume"] < 0).any()
    ):
        raise ValueError("Volume 不能缺失或为负。")

    data = data.sort_values("date", kind="stable").reset_index(drop=True)
    ordered = [column for column in CANONICAL_ORDER if column in data.columns]
    return data[ordered]


def load_workspace_dataset(dataset: DatasetRef) -> pd.DataFrame:
    return canonicalize_frame(pd.read_csv(dataset.path), expected_symbol=dataset.symbol)


def load_uploaded_dataset(source: BinaryIO, *, symbol: str | None) -> pd.DataFrame:
    expected = symbol.strip().upper() if symbol and symbol.strip() else None
    return canonicalize_frame(pd.read_csv(source), expected_symbol=expected)
