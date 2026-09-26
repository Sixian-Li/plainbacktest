"""Point-in-time Nasdaq-100 12-1 momentum ranking and buffered rotation."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from quantkit.execution import ExplicitFillPolicy


PIT_EQUAL_WEIGHT = "PIT_EQUAL_WEIGHT"
TOP10_MONTHLY_REPLACE = "TOP10_MONTHLY_REPLACE"
TOP10_EXIT20_BUFFER = "TOP10_EXIT20_BUFFER"
FORMAL_CASES = (PIT_EQUAL_WEIGHT, TOP10_MONTHLY_REPLACE, TOP10_EXIT20_BUFFER)
PORTFOLIO_REPLACE = "monthly_replace"
PORTFOLIO_BUFFER = "entry10_exit20_buffer"
GATE_NONE = "none"
GATE_12_1_POSITIVE = "momentum_12_1_positive"
GATE_6_1_POSITIVE = "momentum_6_1_positive"
GATE_12_1_AND_6_1_POSITIVE = "momentum_12_1_and_6_1_positive"
ABSOLUTE_GATES = (
    GATE_NONE,
    GATE_12_1_POSITIVE,
    GATE_6_1_POSITIVE,
    GATE_12_1_AND_6_1_POSITIVE,
)
FULL_REBALANCE = "full_rebalance"
FORCED_MEMBERSHIP_EXIT = "forced_membership_exit"


@dataclass(frozen=True)
class MomentumSpec:
    lookback_months: int = 12
    absolute_lookback_months: int = 6
    skip_recent_months: int = 1
    entry_rank: int = 10
    exit_rank: int = 20
    membership_lag_sessions: int = 1

    def validate(self) -> None:
        if self.lookback_months < 1:
            raise ValueError("lookback_months must be positive")
        if self.absolute_lookback_months < 1:
            raise ValueError("absolute_lookback_months must be positive")
        if self.skip_recent_months != 1:
            raise ValueError("this frozen experiment requires exactly one skipped month")
        if self.entry_rank < 1 or self.exit_rank < self.entry_rank:
            raise ValueError("rank thresholds are invalid")
        if self.membership_lag_sessions != 1:
            raise ValueError("this frozen experiment requires one-session membership lag")


@dataclass(frozen=True)
class AbsoluteMomentumGateCase:
    case_id: str
    portfolio_style: str
    gate: str

    def validate(self) -> None:
        if not self.case_id:
            raise ValueError("absolute-gate case_id is empty")
        if self.portfolio_style not in {PORTFOLIO_REPLACE, PORTFOLIO_BUFFER}:
            raise ValueError(f"unknown portfolio style: {self.portfolio_style}")
        if self.gate not in ABSOLUTE_GATES:
            raise ValueError(f"unknown absolute momentum gate: {self.gate}")


def normalized_calendar(values: Sequence[pd.Timestamp] | pd.Series) -> pd.DatetimeIndex:
    calendar = pd.DatetimeIndex(pd.to_datetime(list(values))).normalize().sort_values().unique()
    if calendar.empty:
        raise ValueError("calendar is empty")
    return calendar


def calendar_month_ends(calendar: Sequence[pd.Timestamp] | pd.Series) -> pd.DatetimeIndex:
    """Return actual completed calendar-month endings from a full XNYS calendar."""

    dates = normalized_calendar(calendar)
    series = pd.Series(dates, index=dates)
    return pd.DatetimeIndex(series.groupby(dates.to_period("M")).max().to_numpy())


def shifted_membership_bounds(
    intervals: pd.DataFrame,
    sessions: Sequence[pd.Timestamp] | pd.Series,
    *,
    lag_sessions: int = 1,
) -> pd.DataFrame:
    """Shift observed membership intervals forward by the frozen decision lag."""

    required = {"security_id", "effective_start", "effective_end"}
    if missing := required.difference(intervals.columns):
        raise ValueError(f"membership intervals miss {sorted(missing)}")
    if lag_sessions != 1:
        raise ValueError("only the frozen one-session lag is supported")
    calendar = normalized_calendar(sessions)
    positions = {day: offset for offset, day in enumerate(calendar)}
    rows: list[dict[str, Any]] = []
    for item in intervals.itertuples(index=False):
        start = pd.Timestamp(item.effective_start).normalize()
        end = pd.Timestamp(item.effective_end).normalize()
        if start not in positions or end not in positions:
            raise ValueError(f"membership boundary outside XNYS calendar: {start}..{end}")
        start_offset = positions[start] + lag_sessions
        end_offset = positions[end] + lag_sessions
        if start_offset >= len(calendar):
            continue
        rows.append(
            {
                "security_id": str(item.security_id),
                "known_start": calendar[start_offset],
                "known_end": calendar[min(end_offset, len(calendar) - 1)],
            }
        )
    result = pd.DataFrame(rows, columns=["security_id", "known_start", "known_end"])
    if result.empty:
        raise ValueError("shifted membership is empty")
    return result.sort_values(["security_id", "known_start"]).reset_index(drop=True)


def member_ids_on(bounds: pd.DataFrame, date: pd.Timestamp) -> set[str]:
    day = pd.Timestamp(date).normalize()
    active = bounds[(bounds["known_start"] <= day) & (bounds["known_end"] >= day)]
    return set(active["security_id"].astype(str))


def membership_exit_events(
    bounds: pd.DataFrame,
    sessions: Sequence[pd.Timestamp] | pd.Series,
) -> dict[pd.Timestamp, set[str]]:
    """Map the first completed session known to be outside each membership run."""

    calendar = normalized_calendar(sessions)
    positions = {day: offset for offset, day in enumerate(calendar)}
    events: dict[pd.Timestamp, set[str]] = defaultdict(set)
    for item in bounds.itertuples(index=False):
        end = pd.Timestamp(item.known_end).normalize()
        offset = positions.get(end)
        if offset is None or offset + 1 >= len(calendar):
            continue
        first_false = calendar[offset + 1]
        security_id = str(item.security_id)
        still_member = bounds[
            (bounds["security_id"].astype(str) == security_id)
            & (bounds["known_start"] <= first_false)
            & (bounds["known_end"] >= first_false)
        ]
        if still_member.empty:
            events[first_false].add(security_id)
    return dict(events)


def _price_series(frame: pd.DataFrame, security_id: str) -> pd.Series:
    required = {"date", "close"}
    if missing := required.difference(frame.columns):
        raise ValueError(f"{security_id} price frame misses {sorted(missing)}")
    data = frame[["date", "close"]].copy()
    data["date"] = pd.to_datetime(data["date"]).dt.normalize()
    if data["date"].duplicated().any():
        raise ValueError(f"{security_id} has duplicate price dates")
    data["close"] = pd.to_numeric(data["close"], errors="raise")
    if data.empty or not np.isfinite(data["close"]).all() or (data["close"] <= 0).any():
        raise ValueError(f"{security_id} has invalid closes")
    return data.sort_values("date").set_index("date")["close"]


def momentum_endpoint_pairs(
    signal_dates: Sequence[pd.Timestamp] | pd.Series,
    full_month_ends: Sequence[pd.Timestamp] | pd.Series,
    spec: MomentumSpec,
) -> dict[pd.Timestamp, tuple[pd.Timestamp, pd.Timestamp]]:
    spec.validate()
    month_ends = normalized_calendar(full_month_ends)
    by_period = {day.to_period("M"): day for day in month_ends}
    pairs: dict[pd.Timestamp, tuple[pd.Timestamp, pd.Timestamp]] = {}
    for value in normalized_calendar(signal_dates):
        end_period = value.to_period("M") - spec.skip_recent_months
        start_period = end_period - spec.lookback_months
        if start_period not in by_period or end_period not in by_period:
            continue
        pairs[value] = (by_period[start_period], by_period[end_period])
    return pairs


def rank_monthly_momentum(
    price_frames: Mapping[str, pd.DataFrame],
    signal_dates: Sequence[pd.Timestamp] | pd.Series,
    full_month_ends: Sequence[pd.Timestamp] | pd.Series,
    membership_bounds: pd.DataFrame,
    display_tickers: Mapping[str, str],
    spec: MomentumSpec | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Calculate exact-endpoint 12-1 scores and deterministic monthly ranks."""

    resolved = spec or MomentumSpec()
    resolved.validate()
    signals = normalized_calendar(signal_dates)
    pairs = momentum_endpoint_pairs(signals, full_month_ends, resolved)
    absolute_pairs = momentum_endpoint_pairs(
        signals,
        full_month_ends,
        MomentumSpec(
            lookback_months=resolved.absolute_lookback_months,
            absolute_lookback_months=resolved.absolute_lookback_months,
            skip_recent_months=resolved.skip_recent_months,
            entry_rank=resolved.entry_rank,
            exit_rank=resolved.exit_rank,
            membership_lag_sessions=resolved.membership_lag_sessions,
        ),
    )
    rows: list[dict[str, Any]] = []
    for security_id in sorted(price_frames):
        close = _price_series(price_frames[security_id], security_id)
        for signal_date in signals:
            endpoints = pairs.get(signal_date)
            if endpoints is None or signal_date not in close.index:
                continue
            start_date, end_date = endpoints
            if start_date not in close.index or end_date not in close.index:
                continue
            start_close = float(close.at[start_date])
            end_close = float(close.at[end_date])
            score = end_close / start_close - 1.0
            if not np.isfinite(score):
                continue
            absolute_start_date = pd.NaT
            absolute_end_date = pd.NaT
            absolute_start_close = np.nan
            absolute_end_close = np.nan
            absolute_score = np.nan
            absolute_endpoints = absolute_pairs.get(signal_date)
            if absolute_endpoints is not None:
                candidate_start, candidate_end = absolute_endpoints
                if candidate_start in close.index and candidate_end in close.index:
                    absolute_start_date = candidate_start
                    absolute_end_date = candidate_end
                    absolute_start_close = float(close.at[candidate_start])
                    absolute_end_close = float(close.at[candidate_end])
                    absolute_score = absolute_end_close / absolute_start_close - 1.0
                    if not np.isfinite(absolute_score):
                        absolute_score = np.nan
            rows.append(
                {
                    "date": signal_date,
                    "security_id": security_id,
                    "display_ticker": str(display_tickers.get(security_id, security_id)),
                    "momentum_start_date": start_date,
                    "momentum_end_date": end_date,
                    "momentum_start_close": start_close,
                    "momentum_end_close": end_close,
                    "signal_close": float(close.at[signal_date]),
                    "momentum_12_1": score,
                    "momentum_6_1_start_date": absolute_start_date,
                    "momentum_6_1_end_date": absolute_end_date,
                    "momentum_6_1_start_close": absolute_start_close,
                    "momentum_6_1_end_close": absolute_end_close,
                    "momentum_6_1": absolute_score,
                    "in_universe": security_id in member_ids_on(membership_bounds, signal_date),
                }
            )
    columns = [
        "date",
        "security_id",
        "display_ticker",
        "momentum_start_date",
        "momentum_end_date",
        "momentum_start_close",
        "momentum_end_close",
        "signal_close",
        "momentum_12_1",
        "momentum_6_1_start_date",
        "momentum_6_1_end_date",
        "momentum_6_1_start_close",
        "momentum_6_1_end_close",
        "momentum_6_1",
        "in_universe",
    ]
    scored = pd.DataFrame(rows, columns=columns)
    eligible = scored[scored["in_universe"]].sort_values(
        ["date", "momentum_12_1", "security_id"],
        ascending=[True, False, True],
        kind="stable",
    )
    eligible["rank"] = eligible.groupby("date").cumcount() + 1
    ranked = eligible.reset_index(drop=True)
    rank_counts = ranked.groupby("date").size()
    audit_rows = []
    for date in signals:
        members = member_ids_on(membership_bounds, date)
        audit_rows.append(
            {
                "date": date,
                "member_count": len(members),
                "eligible_count": int(rank_counts.get(date, 0)),
                "missing_score_count": len(members) - int(rank_counts.get(date, 0)),
            }
        )
    return ranked, pd.DataFrame(audit_rows)


def _case_weights(case_id: str, selected: Sequence[str]) -> dict[str, float]:
    ids = sorted(set(str(value) for value in selected))
    count = len(ids)
    if not count:
        return {}
    if case_id == PIT_EQUAL_WEIGHT:
        weight = 1.0 / count
    elif case_id == TOP10_MONTHLY_REPLACE:
        if count > 10:
            raise AssertionError("forced Top10 selected more than 10 securities")
        weight = 0.10
    elif case_id == TOP10_EXIT20_BUFFER:
        if count > 20:
            raise AssertionError("buffer selected more than 20 securities")
        weight = 0.10 if count < 10 else 1.0 / count
    else:
        raise ValueError(f"unknown case: {case_id}")
    result = {security_id: weight for security_id in ids}
    if sum(result.values()) > 1.0 + 1e-12:
        raise AssertionError("target weights exceed long-only budget")
    if case_id != PIT_EQUAL_WEIGHT and max(result.values()) > 0.10 + 1e-12:
        raise AssertionError("selection-case target exceeds 10%")
    return result


def build_target_schedule(
    rankings: pd.DataFrame,
    signal_dates: Sequence[pd.Timestamp] | pd.Series,
    analysis_calendar: Sequence[pd.Timestamp] | pd.Series,
    membership_bounds: pd.DataFrame,
    display_tickers: Mapping[str, str],
    spec: MomentumSpec | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build monthly complete targets plus persistent off-cycle exit retries."""

    resolved = spec or MomentumSpec()
    resolved.validate()
    calendar = normalized_calendar(analysis_calendar)
    monthly = set(normalized_calendar(signal_dates))
    exits = membership_exit_events(membership_bounds, calendar)
    ranked = rankings.copy()
    ranked["date"] = pd.to_datetime(ranked["date"]).dt.normalize()
    snapshots = {date: frame for date, frame in ranked.groupby("date")}
    selected: dict[str, set[str]] = {case_id: set() for case_id in FORMAL_CASES}
    pending_exits: dict[str, set[str]] = {case_id: set() for case_id in FORMAL_CASES}
    rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []

    for date in calendar:
        exiting = exits.get(date, set())
        for case_id in FORMAL_CASES:
            held_exits = selected[case_id].intersection(exiting)
            if held_exits:
                selected[case_id].difference_update(held_exits)
                pending_exits[case_id].update(held_exits)

        if date in monthly:
            snapshot = snapshots.get(date, ranked.iloc[0:0])
            rank_map = {
                str(item.security_id): int(item.rank)
                for item in snapshot.itertuples(index=False)
            }
            members = member_ids_on(membership_bounds, date)
            selected[PIT_EQUAL_WEIGHT] = set(members)
            selected[TOP10_MONTHLY_REPLACE] = {
                security_id for security_id, rank in rank_map.items() if rank <= resolved.entry_rank
            }
            retained = {
                security_id
                for security_id in selected[TOP10_EXIT20_BUFFER]
                if rank_map.get(security_id, resolved.exit_rank + 1) <= resolved.exit_rank
            }
            entrants = {
                security_id for security_id, rank in rank_map.items() if rank <= resolved.entry_rank
            }
            selected[TOP10_EXIT20_BUFFER] = retained.union(entrants)
            if len(selected[TOP10_EXIT20_BUFFER]) > resolved.exit_rank:
                raise AssertionError("buffer holdings exceed exit-rank capacity")

            for case_id in FORMAL_CASES:
                pending_exits[case_id].clear()
                weights = _case_weights(case_id, sorted(selected[case_id]))
                for security_id, target_weight in weights.items():
                    rows.append(
                        {
                            "case_id": case_id,
                            "date": date,
                            "event_type": FULL_REBALANCE,
                            "instrument_id": security_id,
                            "display_ticker": str(display_tickers.get(security_id, security_id)),
                            "target_weight": target_weight,
                            "rank": rank_map.get(security_id, np.nan),
                        }
                    )
                audit_rows.append(
                    {
                        "date": date,
                        "case_id": case_id,
                        "event_type": FULL_REBALANCE,
                        "selected_count": len(selected[case_id]),
                        "target_weight_sum": float(sum(weights.values())),
                        "cash_target_weight": float(1.0 - sum(weights.values())),
                    }
                )
            continue

        for case_id in FORMAL_CASES:
            if not pending_exits[case_id]:
                continue
            for security_id in sorted(pending_exits[case_id]):
                rows.append(
                    {
                        "case_id": case_id,
                        "date": date,
                        "event_type": FORCED_MEMBERSHIP_EXIT,
                        "instrument_id": security_id,
                        "display_ticker": str(display_tickers.get(security_id, security_id)),
                        "target_weight": 0.0,
                        "rank": np.nan,
                    }
                )
            audit_rows.append(
                {
                    "date": date,
                    "case_id": case_id,
                    "event_type": FORCED_MEMBERSHIP_EXIT,
                    "selected_count": len(selected[case_id]),
                    "target_weight_sum": np.nan,
                    "cash_target_weight": np.nan,
                }
            )

    schedule = pd.DataFrame(
        rows,
        columns=[
            "case_id",
            "date",
            "event_type",
            "instrument_id",
            "display_ticker",
            "target_weight",
            "rank",
        ],
    )
    audit = pd.DataFrame(audit_rows)
    observed = audit[audit["event_type"] == FULL_REBALANCE].groupby("case_id")[
        "selected_count"
    ].max()
    if int(observed.get(TOP10_MONTHLY_REPLACE, 0)) > 10:
        raise AssertionError("Top10 audit exceeds 10 holdings")
    if int(observed.get(TOP10_EXIT20_BUFFER, 0)) > 20:
        raise AssertionError("buffer audit exceeds 20 holdings")
    return schedule, audit


def _absolute_gate_mask(snapshot: pd.DataFrame, gate: str) -> pd.Series:
    if gate not in ABSOLUTE_GATES:
        raise ValueError(f"unknown absolute momentum gate: {gate}")
    momentum_12_1 = pd.to_numeric(snapshot["momentum_12_1"], errors="coerce")
    valid = pd.Series(np.isfinite(momentum_12_1), index=snapshot.index)
    if gate in {GATE_12_1_POSITIVE, GATE_12_1_AND_6_1_POSITIVE}:
        valid &= momentum_12_1 > 0.0
    if gate in {GATE_6_1_POSITIVE, GATE_12_1_AND_6_1_POSITIVE}:
        if "momentum_6_1" not in snapshot.columns:
            raise ValueError("rankings miss momentum_6_1 required by the absolute gate")
        momentum_6_1 = pd.to_numeric(snapshot["momentum_6_1"], errors="coerce")
        valid &= np.isfinite(momentum_6_1) & (momentum_6_1 > 0.0)
    return valid


def _absolute_gate_weights(
    case: AbsoluteMomentumGateCase,
    selected: Sequence[str],
    spec: MomentumSpec,
) -> dict[str, float]:
    ids = sorted(set(str(value) for value in selected))
    count = len(ids)
    if not count:
        return {}
    slot_weight = 1.0 / float(spec.entry_rank)
    if case.portfolio_style == PORTFOLIO_REPLACE:
        if count > spec.entry_rank:
            raise AssertionError("replacement portfolio exceeds its entry rank")
        weight = slot_weight
    elif case.portfolio_style == PORTFOLIO_BUFFER:
        if count > spec.exit_rank:
            raise AssertionError("buffer portfolio exceeds its exit rank")
        weight = slot_weight if count < spec.entry_rank else 1.0 / float(count)
    else:  # validated by AbsoluteMomentumGateCase, retained defensively
        raise ValueError(f"unknown portfolio style: {case.portfolio_style}")
    result = {security_id: weight for security_id in ids}
    if sum(result.values()) > 1.0 + 1e-12:
        raise AssertionError("absolute-gate target weights exceed long-only budget")
    if max(result.values()) > slot_weight + 1e-12:
        raise AssertionError("absolute-gate target exceeds the frozen slot weight")
    return result


def build_absolute_gate_target_schedule(
    rankings: pd.DataFrame,
    signal_dates: Sequence[pd.Timestamp] | pd.Series,
    analysis_calendar: Sequence[pd.Timestamp] | pd.Series,
    membership_bounds: pd.DataFrame,
    display_tickers: Mapping[str, str],
    cases: Sequence[AbsoluteMomentumGateCase],
    spec: MomentumSpec | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build monthly targets for fixed absolute-momentum gate ablations.

    Each gate first removes ineligible securities, then ranks the survivors by
    12-1 momentum. For buffered portfolios the gate is authoritative: an
    incumbent that fails the gate is removed even when its ungated rank would
    still be inside the exit band.
    """

    resolved = spec or MomentumSpec()
    resolved.validate()
    frozen_cases = tuple(cases)
    if not frozen_cases:
        raise ValueError("absolute-gate cases are empty")
    for case in frozen_cases:
        case.validate()
    case_ids = [case.case_id for case in frozen_cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("absolute-gate case IDs are not unique")
    required = {"date", "security_id", "momentum_12_1"}
    if missing := required.difference(rankings.columns):
        raise ValueError(f"rankings miss {sorted(missing)}")

    calendar = normalized_calendar(analysis_calendar)
    monthly = set(normalized_calendar(signal_dates))
    exits = membership_exit_events(membership_bounds, calendar)
    ranked = rankings.copy()
    ranked["date"] = pd.to_datetime(ranked["date"]).dt.normalize()
    ranked["security_id"] = ranked["security_id"].astype(str)
    snapshots = {date: frame for date, frame in ranked.groupby("date")}
    selected: dict[str, set[str]] = {case.case_id: set() for case in frozen_cases}
    pending_exits: dict[str, set[str]] = {case.case_id: set() for case in frozen_cases}
    rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []

    for date in calendar:
        exiting = exits.get(date, set())
        for case in frozen_cases:
            held_exits = selected[case.case_id].intersection(exiting)
            if held_exits:
                selected[case.case_id].difference_update(held_exits)
                pending_exits[case.case_id].update(held_exits)

        if date in monthly:
            snapshot = snapshots.get(date, ranked.iloc[0:0]).copy()
            members = member_ids_on(membership_bounds, date)
            snapshot = snapshot[snapshot["security_id"].isin(members)]
            for case in frozen_cases:
                gate_mask = _absolute_gate_mask(snapshot, case.gate)
                eligible = snapshot[gate_mask].sort_values(
                    ["momentum_12_1", "security_id"],
                    ascending=[False, True],
                    kind="stable",
                ).copy()
                eligible["gate_rank"] = np.arange(1, len(eligible) + 1)
                rank_map = dict(
                    zip(
                        eligible["security_id"].astype(str),
                        eligible["gate_rank"].astype(int),
                        strict=True,
                    )
                )
                if case.portfolio_style == PORTFOLIO_REPLACE:
                    next_selected = {
                        security_id
                        for security_id, rank in rank_map.items()
                        if rank <= resolved.entry_rank
                    }
                else:
                    retained = {
                        security_id
                        for security_id in selected[case.case_id]
                        if rank_map.get(security_id, resolved.exit_rank + 1)
                        <= resolved.exit_rank
                    }
                    entrants = {
                        security_id
                        for security_id, rank in rank_map.items()
                        if rank <= resolved.entry_rank
                    }
                    next_selected = retained.union(entrants)
                selected[case.case_id] = next_selected
                capacity = (
                    resolved.entry_rank
                    if case.portfolio_style == PORTFOLIO_REPLACE
                    else resolved.exit_rank
                )
                if len(next_selected) > capacity:
                    raise AssertionError(f"{case.case_id} exceeds holding capacity")

                pending_exits[case.case_id].clear()
                weights = _absolute_gate_weights(case, sorted(next_selected), resolved)
                eligible_by_id = eligible.set_index("security_id")
                if not weights:
                    rows.append(
                        {
                            "case_id": case.case_id,
                            "date": date,
                            "event_type": FULL_REBALANCE,
                            "instrument_id": None,
                            "display_ticker": "现金",
                            "target_weight": 0.0,
                            "rank": np.nan,
                            "gate": case.gate,
                            "portfolio_style": case.portfolio_style,
                            "momentum_12_1": np.nan,
                            "momentum_6_1": np.nan,
                        }
                    )
                for security_id, target_weight in weights.items():
                    item = eligible_by_id.loc[security_id]
                    rows.append(
                        {
                            "case_id": case.case_id,
                            "date": date,
                            "event_type": FULL_REBALANCE,
                            "instrument_id": security_id,
                            "display_ticker": str(display_tickers.get(security_id, security_id)),
                            "target_weight": target_weight,
                            "rank": rank_map[security_id],
                            "gate": case.gate,
                            "portfolio_style": case.portfolio_style,
                            "momentum_12_1": float(item["momentum_12_1"]),
                            "momentum_6_1": (
                                float(item["momentum_6_1"])
                                if "momentum_6_1" in item.index
                                and pd.notna(item["momentum_6_1"])
                                else np.nan
                            ),
                        }
                    )
                audit_rows.append(
                    {
                        "date": date,
                        "case_id": case.case_id,
                        "event_type": FULL_REBALANCE,
                        "gate": case.gate,
                        "portfolio_style": case.portfolio_style,
                        "member_count": len(members),
                        "scored_count": len(snapshot),
                        "eligible_count": len(eligible),
                        "gate_rejected_count": len(snapshot) - len(eligible),
                        "selected_count": len(next_selected),
                        "target_weight_sum": float(sum(weights.values())),
                        "cash_target_weight": float(1.0 - sum(weights.values())),
                    }
                )
            continue

        for case in frozen_cases:
            if not pending_exits[case.case_id]:
                continue
            for security_id in sorted(pending_exits[case.case_id]):
                rows.append(
                    {
                        "case_id": case.case_id,
                        "date": date,
                        "event_type": FORCED_MEMBERSHIP_EXIT,
                        "instrument_id": security_id,
                        "display_ticker": str(display_tickers.get(security_id, security_id)),
                        "target_weight": 0.0,
                        "rank": np.nan,
                        "gate": case.gate,
                        "portfolio_style": case.portfolio_style,
                        "momentum_12_1": np.nan,
                        "momentum_6_1": np.nan,
                    }
                )
            audit_rows.append(
                {
                    "date": date,
                    "case_id": case.case_id,
                    "event_type": FORCED_MEMBERSHIP_EXIT,
                    "gate": case.gate,
                    "portfolio_style": case.portfolio_style,
                    "member_count": np.nan,
                    "scored_count": np.nan,
                    "eligible_count": np.nan,
                    "gate_rejected_count": np.nan,
                    "selected_count": len(selected[case.case_id]),
                    "target_weight_sum": np.nan,
                    "cash_target_weight": np.nan,
                }
            )

    schedule = pd.DataFrame(rows)
    audit = pd.DataFrame(audit_rows)
    monthly_audit = audit[audit["event_type"] == FULL_REBALANCE]
    for case in frozen_cases:
        maximum = int(
            monthly_audit.loc[monthly_audit["case_id"] == case.case_id, "selected_count"].max()
        )
        capacity = (
            resolved.entry_rank
            if case.portfolio_style == PORTFOLIO_REPLACE
            else resolved.exit_rank
        )
        if maximum > capacity:
            raise AssertionError(f"{case.case_id} audit exceeds holding capacity")
    return schedule, audit


def dense_price_frame(
    frame: pd.DataFrame,
    calendar: Sequence[pd.Timestamp] | pd.Series,
    *,
    instrument_id: str,
    display_ticker: str,
) -> pd.DataFrame:
    """Align one security while explicitly marking every carried price."""

    required = {"date", "open", "high", "low", "close", "volume"}
    if missing := required.difference(frame.columns):
        raise ValueError(f"{instrument_id} price frame misses {sorted(missing)}")
    dates = normalized_calendar(calendar)
    source = frame.copy()
    source["date"] = pd.to_datetime(source["date"]).dt.normalize()
    source = source[source["date"].isin(dates)].sort_values("date").drop_duplicates("date")
    if source.empty:
        raise ValueError(f"{instrument_id} has no price in the analysis calendar")
    source_last = pd.Timestamp(source["date"].max())
    active_dates = dates[dates >= source["date"].min()]
    indexed = source.set_index("date").reindex(active_dates)
    synthetic = indexed["close"].isna()
    for column in ("open", "high", "low", "close"):
        indexed[column] = pd.to_numeric(indexed[column], errors="coerce").ffill()
    indexed["volume"] = pd.to_numeric(indexed["volume"], errors="coerce").fillna(0.0)
    if indexed[["open", "high", "low", "close"]].isna().any().any():
        raise ValueError(f"{instrument_id} dense panel could not fill an internal gap")
    indexed["symbol"] = instrument_id
    indexed["display_ticker"] = display_ticker
    indexed["synthetic_bar"] = synthetic.astype(int)
    indexed["terminal_settlement_proxy"] = (indexed.index > source_last).astype(int)
    return indexed.reset_index(names="date")


def build_target_share_table(
    price_panel: pd.DataFrame,
    target_schedule: pd.DataFrame,
    *,
    case_id: str,
    initial_cash: float,
    policy: ExplicitFillPolicy,
) -> pd.DataFrame:
    """Close-size full rebalances and sell-only membership exit retries."""

    required_panel = {
        "date",
        "symbol",
        "open",
        "close",
        "synthetic_bar",
        "terminal_settlement_proxy",
    }
    if missing := required_panel.difference(price_panel.columns):
        raise ValueError(f"price panel misses {sorted(missing)}")
    panel = price_panel.copy()
    panel["date"] = pd.to_datetime(panel["date"]).dt.normalize()
    if panel.duplicated(["date", "symbol"]).any():
        raise ValueError("price panel contains duplicate instrument dates")
    dates = pd.DatetimeIndex(panel["date"].drop_duplicates().sort_values())
    opens = panel.pivot(index="date", columns="symbol", values="open").reindex(dates)
    closes = panel.pivot(index="date", columns="symbol", values="close").reindex(dates)
    synthetic = (
        panel.pivot(index="date", columns="symbol", values="synthetic_bar")
        .reindex(index=dates, columns=closes.columns)
        .fillna(1)
        .astype(bool)
    )
    terminal = (
        panel.pivot(index="date", columns="symbol", values="terminal_settlement_proxy")
        .reindex(index=dates, columns=closes.columns)
        .fillna(0)
        .astype(bool)
    )
    requested = target_schedule[target_schedule["case_id"] == case_id].copy()
    requested["date"] = pd.to_datetime(requested["date"]).dt.normalize()
    events = {date: frame for date, frame in requested.groupby("date")}
    signal_to_execution = {dates[index]: dates[index + 1] for index in range(len(dates) - 1)}
    current = pd.Series(0.0, index=closes.columns)
    cash = float(initial_cash)
    rows: list[dict[str, Any]] = []

    for signal_date in sorted(events):
        execution_date = signal_to_execution.get(pd.Timestamp(signal_date))
        if execution_date is None:
            continue
        event = events[signal_date]
        kinds = set(event["event_type"].astype(str))
        if len(kinds) != 1:
            raise ValueError(f"mixed event types on {signal_date}")
        event_type = kinds.pop()
        signal_close = closes.loc[signal_date]
        equity = cash + float((current * signal_close.fillna(0.0)).sum())
        desired = current.copy() if event_type == FORCED_MEMBERSHIP_EXIT else pd.Series(
            0.0, index=closes.columns
        )
        target_rows = event[event["instrument_id"].notna()].copy()
        target_rows = target_rows[target_rows["instrument_id"].astype(str).str.len() > 0]
        requested_weights = target_rows.set_index("instrument_id")["target_weight"].astype(float)
        if event_type == FULL_REBALANCE:
            for symbol, weight in requested_weights.items():
                if symbol not in desired.index:
                    raise ValueError(f"target has no price instrument: {symbol}")
                if pd.notna(signal_close[symbol]):
                    desired[symbol] = equity * float(weight) / float(signal_close[symbol])
        elif event_type == FORCED_MEMBERSHIP_EXIT:
            for symbol in requested_weights.index:
                if symbol in desired.index:
                    desired[symbol] = 0.0
        else:
            raise ValueError(f"unknown target event: {event_type}")

        signal_internal_gap = synthetic.loc[signal_date] & ~terminal.loc[signal_date]
        execution_internal_gap = synthetic.loc[execution_date] & ~terminal.loc[execution_date]
        deferred = signal_internal_gap | execution_internal_gap
        changed = (desired - current).abs() > 1e-12
        deferred_change = deferred & changed
        desired.loc[deferred] = current.loc[deferred]
        terminal_buy = terminal.loc[execution_date] & (desired > current + 1e-12)
        deferred_change |= terminal_buy
        desired.loc[terminal_buy] = current.loc[terminal_buy]

        execution_open = opens.loc[execution_date]
        sell_delta = (current - desired).clip(lower=0.0)
        terminal_sell = terminal.loc[execution_date] & (sell_delta > 1e-12)
        for symbol in sorted(closes.columns):
            shares = float(sell_delta[symbol])
            if shares <= 1e-12:
                continue
            fill = policy.expected_fill("sell", float(execution_open[symbol]))
            if not np.isfinite(fill):
                raise ValueError(f"{symbol} has no sell Open on {execution_date.date()}")
            cash += shares * fill
            current[symbol] -= shares
        buy_delta = (desired - current).clip(lower=0.0)
        for symbol in sorted(closes.columns):
            shares = float(buy_delta[symbol])
            if shares <= 1e-12:
                continue
            fill = policy.expected_fill("buy", float(execution_open[symbol]))
            if not np.isfinite(fill):
                raise ValueError(f"{symbol} has no buy Open on {execution_date.date()}")
            filled = min(shares, max(cash, 0.0) / fill)
            cash -= filled * fill
            current[symbol] += filled
        for symbol in closes.columns:
            rows.append(
                {
                    "signal_date": signal_date,
                    "execution_date": execution_date,
                    "symbol": symbol,
                    "event_type": event_type,
                    "target_weight": float(requested_weights.get(symbol, 0.0)),
                    "target_shares": float(desired[symbol]),
                    "execution_deferred_unavailable": int(deferred_change[symbol]),
                    "terminal_settlement_execution": int(terminal_sell[symbol]),
                }
            )
    return pd.DataFrame(rows)
