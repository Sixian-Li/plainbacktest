"""Approved daily-price discovery for the dual-SMA lab."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import pandas as pd


REQUIRED_COLUMNS = ("date", "symbol", "open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class DatasetRef:
    symbol: str
    path: Path
    status: str
    source_group: str
    company_name: str = ""

    @property
    def label(self) -> str:
        company = f" · {self.company_name}" if self.company_name else ""
        return f"{self.symbol}{company} · {self.source_group}"


def discover_approved_datasets(project_root: Path) -> dict[str, DatasetRef]:
    """Return registered approved assets and quality-passed S&P histories."""

    root = Path(project_root)
    registry = json.loads(
        (root / "data/source_registry.yaml").read_text(encoding="utf-8")
    )
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

    security_master_path = root / "data/processed/universes/sp500/security_master.csv"
    equity_dir = root / "data/processed/daily/equities"
    if security_master_path.is_file() and equity_dir.is_dir():
        master = pd.read_csv(security_master_path, dtype=str).fillna("")
        allowed = master[
            master["quality_status"].eq("passed")
            & master["member_price_coverage_status"].isin(("complete", "gaps_reported"))
        ]
        for row in allowed.itertuples(index=False):
            symbol = str(row.symbol).upper()
            path = equity_dir / f"{symbol}.csv"
            if not path.is_file():
                continue
            datasets.setdefault(
                symbol,
                DatasetRef(
                    symbol=symbol,
                    path=path,
                    status="approved_sp500_history",
                    source_group="批准 S&P 500 历史证券日线",
                    company_name=str(row.company_name),
                ),
            )
    return dict(sorted(datasets.items()))


def canonicalize_price_frame(
    frame: pd.DataFrame, *, expected_symbol: str | None = None
) -> pd.DataFrame:
    """Validate the canonical adjusted OHLCV shape used by the ledger."""

    if frame.empty:
        raise ValueError("数据文件没有记录。")
    data = frame.rename(columns={column: str(column).strip().lower() for column in frame}).copy()
    missing = set(REQUIRED_COLUMNS).difference(data.columns)
    if missing:
        raise ValueError(f"缺少 canonical 列：{sorted(missing)}")
    data = data[list(REQUIRED_COLUMNS)]
    data["date"] = pd.to_datetime(data["date"], errors="raise").dt.normalize()
    data["symbol"] = data["symbol"].astype(str).str.strip().str.upper()
    if expected_symbol is not None and not data["symbol"].eq(expected_symbol.upper()).all():
        raise ValueError(f"文件包含与 {expected_symbol.upper()} 不符的标的。")
    if data["symbol"].nunique() != 1:
        raise ValueError("每次只能计算一个标的。")
    if data["date"].duplicated().any():
        raise ValueError("数据含重复交易日。")
    for column in ("open", "high", "low", "close", "volume"):
        data[column] = pd.to_numeric(data[column], errors="raise")
    if data[["open", "high", "low", "close"]].isna().any().any():
        raise ValueError("OHLC 不能缺失。")
    if (data[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("OHLC 必须为正数。")
    if data["volume"].isna().any() or (data["volume"] < 0).any():
        raise ValueError("Volume 不能缺失或为负。")
    invalid_ohlc = (
        data["high"].lt(data[["open", "close", "low"]].max(axis=1))
        | data["low"].gt(data[["open", "close", "high"]].min(axis=1))
    )
    if invalid_ohlc.any():
        raise ValueError("数据含不合法的 OHLC 关系。")
    return data.sort_values("date", kind="stable").reset_index(drop=True)


def load_workspace_dataset(dataset: DatasetRef) -> pd.DataFrame:
    return canonicalize_price_frame(
        pd.read_csv(dataset.path), expected_symbol=dataset.symbol
    )
