#!/usr/bin/env python3
"""Run the workspace-owned Quant audit, with a minimal structural fallback."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--require-log-date")
    args = parser.parse_args()
    root = args.workspace.resolve()
    owned = root / "backtest/scripts/audit_workspace.py"
    python = root / "backtest/.venv/bin/python"
    if owned.is_file() and python.is_file():
        command = [str(python), "-m", "scripts.audit_workspace"]
        if args.require_log_date:
            command.extend(["--require-log-date", args.require_log_date])
        return subprocess.run(command, cwd=root / "backtest", check=False).returncode

    errors = []
    for path in ("catalog.md", "log.md", "data", "backtest", "backtest/experiments/index.md"):
        if not (root / path).exists():
            errors.append(f"missing {path}")
    for forbidden in ("experiments", "quantkit", "scripts", "tests", ".venv"):
        if (root / forbidden).exists():
            errors.append(f"backtest-only root entry: {forbidden}")
    if errors:
        print("Quant workspace fallback audit: FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Quant workspace fallback audit: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
