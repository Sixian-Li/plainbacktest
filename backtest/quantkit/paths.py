"""Stable workspace paths that do not depend on a caller's directory depth."""

from pathlib import Path


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
EXPERIMENTS_ROOT = BACKTEST_ROOT / "experiments"
