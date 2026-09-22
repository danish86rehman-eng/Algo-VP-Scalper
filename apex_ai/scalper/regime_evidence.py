"""Observation-only regime evidence snapshots and deterministic summaries.

This module has no trading authority.  It records facts already computed by the
decision path and provides replay-safe aggregation over those records.
"""
from __future__ import annotations

import json
import hashlib
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable, Optional
from numbers import Real


SCHEMA = "SA_REGIME_EVIDENCE_V1"
BAR_RECORD = "REGIME_BAR_SNAPSHOT"
CANDIDATE_RECORD = "CANDIDATE_EVIDENCE_SNAPSHOT"
OUTCOME_RECORD = "CANDIDATE_OUTCOME_SNAPSHOT"


def candidate_id(symbol: str, bar_timestamp: Any, trigger_type: str,
                direction: str, entry: Any, stop: Any, tp1: Any) -> str:
    payload = "|".join(map(str, (symbol.upper(), _utc(bar_timestamp),
                                  trigger_type, direction, entry, stop, tp1)))
    return "RC1_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _utc(value: Any) -> str:
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).isoformat()


def _finite(value: Any) -> Any:
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return value
    return value if math.isfinite(value) else None


@dataclass(frozen=True)
class RegimeBarSnapshot:
    timestamp_utc: str
    symbol: str
    decision_timeframe: str
    completed_bar_timestamp_utc: str
    ict_context: str
    context_score: Optional[float]
    atr_ratio: Optional[float]
    displacement_momentum: Optional[float]
    structure_trend: str
    manipulation_detected: bool
    manipulation_confidence: Optional[float]
    liquidity_state: str
    statistical_context: str
    autocorrelation: Optional[float]
    efficiency_ratio: Optional[float]
    volatility_ratio: Optional[float]
    source_bar_m5_utc: Optional[str]
    source_bar_m15_utc: Optional[str]
    source_bar_h1_utc: Optional[str]
    source_bar_h4_utc: Optional[str]
    completed_bars_only: bool = True

    def record(self) -> dict:
        value = asdict(self)
        value.update({"schema": SCHEMA, "schema_version": 1,
                      "record_type": BAR_RECORD,
                      "telemetry_authority": "OBSERVATION_ONLY"})
        return _sanitize(value)


@dataclass(frozen=True)
class CandidateEvidenceSnapshot:
    timestamp_utc: str
    symbol: str
    decision_timeframe: str
    completed_bar_timestamp_utc: str
    candidate_id: str
    direction: str
    trigger_type: str
    entry_price: Optional[float]
    stop_loss: Optional[float]
    tp1: Optional[float]
    tp2: Optional[float]
    ict_context: str
    context_score: Optional[float]
    atr_ratio: Optional[float]
    displacement_momentum: Optional[float]
    structure_trend: str
    manipulation_detected: bool
    manipulation_confidence: Optional[float]
    liquidity_state: str
    statistical_context: str
    autocorrelation: Optional[float]
    efficiency_ratio: Optional[float]
    volatility_ratio: Optional[float]
    previous_regime: str
    bars_in_regime: int
    transition: bool
    allowed_regimes: tuple[str, ...]
    regime_gate: str
    veto_reason: str
    stb_state: str
    stb_confidence: str
    displacement_gate: str
    session_state: str
    source_bar_m5_utc: Optional[str]
    source_bar_m15_utc: Optional[str]
    source_bar_h1_utc: Optional[str]
    source_bar_h4_utc: Optional[str]
    completed_bars_only: bool = True
    downstream_rejection: str = ""

    def record(self) -> dict:
        value = asdict(self)
        value["schema"] = SCHEMA
        value["schema_version"] = 1
        value["record_type"] = CANDIDATE_RECORD
        value["telemetry_authority"] = "OBSERVATION_ONLY"
        return _sanitize(value)


# Compatibility name for callers written during the first instrumentation pass.
MarketEvidenceSnapshot = CandidateEvidenceSnapshot


def _sanitize(value: Any) -> Any:
    """Convert non-finite numerics to null before strict JSON serialization."""
    if isinstance(value, dict):
        return {str(k): _sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(v) for v in value]
    if isinstance(value, Real) and not isinstance(value, bool):
        number = float(value)
        return number if math.isfinite(number) else None
    return value


class RegimeEvidenceRecorder:
    """Append-only recorder with in-process dwell tracking."""

    def __init__(self, path: str | Path = "logs/regime_evidence.jsonl"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._last_bar: dict[tuple[str, str], str] = {}
        self._bar_records: dict[tuple[str, str, str], dict] = {}

    def seen_bar(self, symbol: str, timeframe: str,
                 completed_bar_timestamp_utc: str) -> bool:
        return self._last_bar.get((symbol, timeframe)) == completed_bar_timestamp_utc

    def record_bar(self, snapshot: RegimeBarSnapshot) -> None:
        """Record one authoritative market-bar state; candidates are separate."""
        key = (snapshot.symbol, snapshot.decision_timeframe)
        bar = snapshot.completed_bar_timestamp_utc
        record = snapshot.record()
        prior_record = self._bar_records.get((snapshot.symbol,
                                              snapshot.decision_timeframe, bar))
        if prior_record is not None:
            if prior_record != record:
                self._write({"record_type": "REGIME_SNAPSHOT_CONFLICT",
                             "symbol": snapshot.symbol,
                             "decision_timeframe": snapshot.decision_timeframe,
                             "completed_bar_timestamp_utc": bar,
                             "first": prior_record, "second": record})
            return
        self._last_bar[key] = bar
        self._bar_records[(snapshot.symbol, snapshot.decision_timeframe, bar)] = record
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, allow_nan=False,
                                    separators=(",", ":")) + "\n")

    def _write(self, record: dict) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_sanitize(record), allow_nan=False,
                                    separators=(",", ":")) + "\n")

    def record(self, snapshot: MarketEvidenceSnapshot) -> None:
        """Record a candidate only; it never contributes to bar dwell statistics."""
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(snapshot.record(), allow_nan=False,
                                    separators=(",", ":")) + "\n")

    def record_outcome(self, *, candidate_id: str, outcome: str,
                       exit_reason: str, close_time_utc: str,
                       r_multiple: float | None) -> None:
        self._write({"schema": SCHEMA, "schema_version": 1,
                     "record_type": OUTCOME_RECORD,
                     "telemetry_authority": "OBSERVATION_ONLY",
                     "candidate_id": candidate_id, "outcome": outcome,
                     "exit_reason": exit_reason,
                     "close_time_utc": close_time_utc,
                     "r_multiple": r_multiple})

    def context_for(self, symbol: str, timeframe: str,
                    completed_bar_timestamp_utc: str) -> tuple[str, int, bool]:
        """Return prior regime, current dwell, and transition for a candidate."""
        rows = [r for (s, tf, _), r in self._bar_records.items()
                if s == symbol and tf == timeframe]
        rows.sort(key=lambda r: r["completed_bar_timestamp_utc"])
        prior = "UNKNOWN"
        dwell = 0
        for row in rows:
            if row["completed_bar_timestamp_utc"] >= completed_bar_timestamp_utc:
                break
            regime = row.get("ict_context", "UNKNOWN")
            if regime == prior:
                dwell += 1
            else:
                prior = regime
                dwell = 1
        current = next((r.get("ict_context", "UNKNOWN") for r in rows
                        if r["completed_bar_timestamp_utc"] == completed_bar_timestamp_utc),
                       "UNKNOWN")
        return prior, dwell, bool(prior != "UNKNOWN" and prior != current)


def load_snapshots(path: str | Path) -> list[dict]:
    result = []
    file = Path(path)
    if not file.exists():
        return result
    for line in file.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("schema") == SCHEMA or row.get("record_type") == "REGIME_SNAPSHOT_CONFLICT":
            result.append(row)
    return result


def summarize_regimes(rows: Iterable[dict]) -> dict:
    rows = [r for r in rows if r.get("record_type") == BAR_RECORD]
    unique = {}
    for row in rows:
        key = (row.get("symbol", ""), row.get("decision_timeframe", ""),
               row.get("completed_bar_timestamp_utc", ""))
        unique.setdefault(key, row)
    rows = sorted(unique.values(), key=lambda r: (r.get("symbol", ""), r.get("decision_timeframe", ""), r.get("completed_bar_timestamp_utc", "")))
    counts = Counter(r.get("ict_context", "UNKNOWN") for r in rows)
    transitions = Counter()
    runs = defaultdict(list)
    previous = {}
    current_len = {}
    for row in rows:
        key = (row.get("symbol", ""), row.get("decision_timeframe", ""))
        regime = row.get("ict_context", "UNKNOWN")
        if key in previous and previous[key] != regime:
            transitions[(previous[key], regime)] += 1
            runs[(key, previous[key])].append(current_len[key])
            current_len[key] = 1
        else:
            current_len[key] = current_len.get(key, 0) + 1
        previous[key] = regime
    for key, regime in previous.items():
        runs[(key, regime)].append(current_len[key])
    total = sum(counts.values())
    dwell = {}
    for (_, regime), values in runs.items():
        dwell.setdefault(regime, []).extend(values)
    def pct(values, q):
        if not values:
            return None
        values = sorted(values)
        index = min(len(values) - 1, max(0, math.ceil(q * len(values)) - 1))
        return values[index]
    return {
        "observations": total,
        "counts": dict(counts),
        "percent": {k: (v / total * 100.0 if total else 0.0) for k, v in counts.items()},
        "dwell": {k: {"median": median(v), "mean": mean(v), "p90": pct(v, .90)} for k, v in dwell.items()},
        "transitions": {f"{a}->{b}": n for (a, b), n in sorted(transitions.items())},
        "transitions_per_100_bars": (sum(transitions.values()) / total * 100.0 if total else 0.0),
    }


def summarize_admission(rows: Iterable[dict]) -> dict:
    rows = [r for r in rows if r.get("record_type") == CANDIDATE_RECORD]
    matrix = defaultdict(lambda: {"candidates": 0, "passes": 0, "rejects": 0})
    for row in rows:
        key = (row.get("ict_context", "UNKNOWN"), row.get("trigger_type", "UNKNOWN"))
        matrix[key]["candidates"] += 1
        state = row.get("regime_gate", "UNKNOWN")
        if state == "PASS":
            matrix[key]["passes"] += 1
        elif state == "REJECT":
            matrix[key]["rejects"] += 1
        else:
            matrix[key].setdefault("other_error_unknown", 0)
            matrix[key]["other_error_unknown"] += 1
    for value in matrix.values():
        value["pass_rate"] = (value["passes"] / value["candidates"]
                               if value["candidates"] else 0.0)
    return {f"{regime} x {trigger}": value for (regime, trigger), value in sorted(matrix.items())}


def chronological_folds(rows: Iterable[dict]) -> dict[str, list[dict]]:
    """Partition by unique completed-bar timestamps; never split one bar."""
    rows = list(rows)
    timestamps = sorted({r.get("completed_bar_timestamp_utc", "") for r in rows
                         if r.get("completed_bar_timestamp_utc")})
    n = len(timestamps)
    cut1, cut2 = int(n * .6), int(n * .8)
    groups = {"development_60": set(timestamps[:cut1]),
              "validation_20": set(timestamps[cut1:cut2]),
              "test_20": set(timestamps[cut2:])}
    return {name: [r for r in rows if r.get("completed_bar_timestamp_utc") in stamps]
            for name, stamps in groups.items()}


def integrity_report(rows: Iterable[dict]) -> dict:
    rows = list(rows)
    bars = [r for r in rows if r.get("record_type") == BAR_RECORD]
    candidates = [r for r in rows if r.get("record_type") == CANDIDATE_RECORD]
    conflict_events = [r for r in rows
                       if r.get("record_type") == "REGIME_SNAPSHOT_CONFLICT"]
    by_key: dict[tuple, list[dict]] = defaultdict(list)
    for row in bars:
        by_key[(row.get("symbol"), row.get("decision_timeframe"),
                row.get("completed_bar_timestamp_utc"))].append(row)
    unique_bar = {key: values[0] for key, values in by_key.items()}
    duplicate = sum(max(0, len(values) - 1) for values in by_key.values())
    conflicts = len(conflict_events) + sum(1 for values in by_key.values()
                    if len({json.dumps(v, sort_keys=True) for v in values}) > 1)
    lookup = {(r.get("symbol"), r.get("completed_bar_timestamp_utc")): r
              for r in unique_bar.values()}
    orphan = 0
    mismatch = 0
    numeric_fields = ("context_score", "atr_ratio", "displacement_momentum",
                      "manipulation_confidence", "autocorrelation",
                      "efficiency_ratio", "volatility_ratio")
    malformed = 0
    for row in rows:
        for field in numeric_fields:
            value = row.get(field)
            if value is not None and not isinstance(value, (int, float)):
                malformed += 1
    for row in candidates:
        bar = lookup.get((row.get("symbol"), row.get("completed_bar_timestamp_utc")))
        if bar is None:
            orphan += 1
        elif row.get("ict_context") != bar.get("ict_context"):
            mismatch += 1
    folds = chronological_folds(rows)
    fold_sets = [{r.get("completed_bar_timestamp_utc") for r in part}
                 for part in folds.values()]
    overlap = sum(len(fold_sets[i] & fold_sets[j])
                  for i in range(3) for j in range(i + 1, 3))
    return {
        "missing_bar_snapshots": orphan,
        "duplicate_snapshots": duplicate,
        "conflicting_snapshots": conflicts,
        "orphan_candidate_records": orphan,
        "candidate_bar_regime_mismatches": mismatch,
        "malformed_numeric_fields": malformed,
        "fold_timestamp_overlap": overlap,
        "authoritative_unique_bars": len(unique_bar),
        "candidate_records": len(candidates),
    }
