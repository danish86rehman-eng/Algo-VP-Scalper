"""Observation-only candidate funnel telemetry.

This module deliberately has no trading dependencies.  A recorder instance may
be attached to a live scan or a replay, but its return values must never be
used to make a decision.  Events are append-only and candidate identity is
derived only from market/setup facts, never evaluation time.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

STAGES = (
    "RAW_DETECTED", "CANDIDATE_CREATED", "FRESHNESS_CHECKED",
    "STRUCTURE_CHECKED", "M5_CONFIRMATION_CHECKED", "REGIME_CHECKED",
    "STB_CHECKED", "LOCATION_CHECKED", "SESSION_CHECKED",
    "GEOMETRY_CHECKED", "COST_CHECKED", "RISK_CHECKED", "EXECUTABLE",
    "SIMULATED_ENTRY", "TERMINATED",
)
STATUSES = ("PASS", "FAIL", "WAIT", "NOT_APPLICABLE", "ERROR")
TERMINALS = (
    "EXECUTED", "EXPIRED", "INVALIDATED", "REJECTED", "PREEMPTED",
    "SUPERSEDED", "SIMULATED_ENTRY", "UNRESOLVED_AT_END", "ERROR",
)


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return str(value)


def make_candidate_id(symbol: str, trigger: str, direction: str,
                      originating_event_ts: Any, reference_level: Any = None,
                      setup_id: Any = None, formation_ts: Any = None,
                      frozen_location_id: Any = None,
                      pool_id: Any = None) -> str:
    """Return a stable ID; wall-clock evaluation time is intentionally absent."""
    identity = {
        "symbol": str(symbol).upper(), "trigger": str(trigger).upper(),
        "direction": str(direction).upper(),
        "originating_event_ts": _jsonable(originating_event_ts),
        "reference_level": _jsonable(reference_level),
        "setup_id": _jsonable(setup_id),
        "formation_ts": _jsonable(formation_ts),
        "frozen_location_id": _jsonable(frozen_location_id),
        "pool_id": _jsonable(pool_id),
    }
    raw = json.dumps(identity, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def candidate_id_for_trigger(symbol: str, trigger: Any,
                             fallback_event_ts: Any = None) -> str:
    """Stable setup identity shared by live and replay candidate telemetry."""
    context = getattr(trigger, "location_context", None)
    permission = getattr(trigger, "location_permission", None)
    event_ts = (getattr(trigger, "sweep_time", None)
                if getattr(trigger, "trigger_type", "") == "SWEEP_REJECTION"
                else None) or fallback_event_ts
    return make_candidate_id(
        symbol, getattr(trigger, "trigger_type", "NONE"),
        getattr(trigger, "direction", "NONE"), event_ts,
        getattr(trigger, "swept_level", None),
        frozen_location_id=getattr(permission, "location_id", None),
        pool_id=getattr(context, "liquidity_pool_id", None),
    )


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    symbol: str
    trigger: str
    direction: str
    originating_event_ts: Any
    reference_level: Any = None
    setup_id: Any = None
    formation_ts: Any = None
    source_timeframes: tuple[str, ...] = ()
    frozen_location_id: Any = None
    pool_id: Any = None


@dataclass
class _State:
    candidate: Candidate
    stages: dict[str, dict] = field(default_factory=dict)
    terminal: Optional[str] = None


class CandidateFunnelRecorder:
    """Append-only, idempotent recorder suitable for live and replay paths."""

    def __init__(self, path: Optional[str | Path] = None, enabled: bool = True):
        self.enabled = bool(enabled)
        self.path = Path(path) if path else None
        self._states: dict[str, _State] = {}
        self._events: list[dict] = []

    def candidate(self, *, symbol: str, trigger: str, direction: str,
                  originating_event_ts: Any, reference_level: Any = None,
                  setup_id: Any = None, formation_ts: Any = None,
                  source_timeframes: Iterable[str] = (),
                  frozen_location_id: Any = None, pool_id: Any = None) -> str:
        cid = make_candidate_id(symbol, trigger, direction,
                                originating_event_ts, reference_level,
                                setup_id, formation_ts,
                                frozen_location_id, pool_id)
        c = Candidate(
            cid, str(symbol).upper(), str(trigger).upper(),
            str(direction).upper(), originating_event_ts, reference_level,
            setup_id, formation_ts,
            tuple(sorted(str(x) for x in source_timeframes)),
            frozen_location_id, pool_id)
        if cid not in self._states:
            self._states[cid] = _State(c)
            self._emit({"kind": "candidate", **_jsonable(c.__dict__)})
            self.stage(cid, "RAW_DETECTED", "PASS")
            self.stage(cid, "CANDIDATE_CREATED", "PASS")
        return cid

    def observe_trigger(self, trigger: Any, symbol: str,
                        completed_bar_ts: Any = None,
                        evaluation_ts: Any = None) -> Optional[str]:
        """Observe a SATrigger without becoming part of its decision path."""
        if not getattr(trigger, "detected", False):
            return None
        plan = (getattr(trigger, "s01", None)
                or getattr(trigger, "session_sweep", None)
                or getattr(trigger, "htf_crt", None)
                or getattr(trigger, "fvg_entry", None)
                or getattr(trigger, "vplr", None))
        s01_telemetry = (getattr(plan, "telemetry", None)
                         if getattr(trigger, "s01", None) is not None else None)
        event_ts = ((s01_telemetry or {}).get("raid_start")
                    if s01_telemetry else None)
        event_ts = (event_ts or getattr(plan, "event_at", None)
                    or getattr(plan, "raid_at", None)
                    or getattr(plan, "formed_at", None)
                    or getattr(plan, "displacement_at", None)
                    or completed_bar_ts)
        formation_ts = ((s01_telemetry or {}).get("selected_entry_timestamp")
                        if getattr(trigger, "s01", None) is not None else
                        (getattr(plan, "formed_at", None) if plan else None))
        is_sweep = getattr(trigger, "trigger_type", "") == "SWEEP_REJECTION"
        if is_sweep:
            event_ts = getattr(trigger, "sweep_time", None) or event_ts
        reference = (getattr(trigger, "swept_level", None)
                     or (getattr(plan, "swept_level", None) if plan else None))
        setup_id = (getattr(plan, "telemetry", {}).get("setup_id")
                    if getattr(trigger, "s01", None) is not None else
                    (getattr(plan, "setup_id", None) if plan else None))
        context = getattr(trigger, "location_context", None)
        permission = getattr(trigger, "location_permission", None)
        pool_id = getattr(context, "liquidity_pool_id", None)
        frozen_location_id = getattr(permission, "location_id", None)
        cid = self.candidate(symbol=symbol, trigger=trigger.trigger_type,
                             direction=trigger.direction,
                             originating_event_ts=event_ts,
                             reference_level=reference, setup_id=setup_id,
                             formation_ts=formation_ts,
                             source_timeframes=("M15", "M5"),
                             frozen_location_id=frozen_location_id,
                             pool_id=pool_id)
        self.stage(cid, "FRESHNESS_CHECKED", "WAIT",
                   "detected candidate awaiting downstream funnel evidence",
                   completed_bar_ts=completed_bar_ts, evaluation_ts=evaluation_ts,
                   source_timeframes=("M15", "M5"))
        return cid

    def observe_s01(self, result: Any, symbol: str,
                    completed_bar_ts: Any = None,
                    evaluation_ts: Any = None) -> Optional[str]:
        """Record every stateful S01 observation, including rejected setups.

        S01 is stateful, so a non-detected result is still evidence: the
        reference may be armed, a raid may be awaiting reclaim, or the setup
        may have been invalidated.  The candidate ID is the immutable S01
        setup ID, never the evaluation time.
        """
        setup_id = getattr(result, "setup_id", None)
        if not setup_id:
            return None
        telemetry = getattr(result, "telemetry", None) or {}
        reference = telemetry.get("reference", {}) if isinstance(telemetry, dict) else {}
        direction = getattr(result, "direction", "NONE")
        event_ts = (telemetry.get("raid_start") or
                    telemetry.get("reference_armed_at") or completed_bar_ts)
        cid = self.candidate(
            symbol=symbol, trigger="S01_REFERENCE_CANDLE_RAID_VP",
            direction=direction, originating_event_ts=event_ts,
            reference_level=(reference.get("reference_high") if direction == "BEARISH"
                             else reference.get("reference_low")),
            setup_id=setup_id, formation_ts=telemetry.get("reclaim_timestamp"),
            source_timeframes=("D1", "M15", "M5"),
        )
        self._emit({
            "kind": "s01_observation", "candidate_id": cid,
            "completed_bar_ts": _jsonable(completed_bar_ts),
            "evaluation_ts": _jsonable(evaluation_ts),
            "result": _jsonable(getattr(result, "record", lambda: {})()),
        })
        state = str(getattr(result, "state", ""))
        if state in {"INVALIDATED", "EXPIRED", "BREAKOUT_ACCEPTED", "COMPLETE"}:
            terminal = {
                "BREAKOUT_ACCEPTED": "INVALIDATED",
                "COMPLETE": "EXECUTED",
            }.get(state, state)
            self.terminal(cid, terminal, getattr(result, "rejection_reason", ""),
                          values=telemetry)
        return cid

    def record_selection(self, observed: Iterable[tuple[Any, str]],
                         winner: Any, completed_bar_ts: Any = None,
                         evaluation_ts: Any = None) -> None:
        """Record overlap/winner facts without changing the selected trigger."""
        rows = []
        for trigger, cid in observed:
            if not cid:
                continue
            context = getattr(trigger, "location_context", None)
            rows.append({
                "candidate_id": cid,
                "trigger": getattr(trigger, "trigger_type", "NONE"),
                "direction": getattr(trigger, "direction", "NONE"),
                "candidate_frozen_location_id": getattr(
                    self._states[cid].candidate, "frozen_location_id", None),
                "candidate_pool_id": getattr(
                    self._states[cid].candidate, "pool_id", None),
                "location_context": (context.record() if context is not None
                                      and hasattr(context, "record") else None),
                "market_location": (getattr(trigger, "market_location", None).record()
                                     if getattr(trigger, "market_location", None) is not None
                                     and hasattr(getattr(trigger, "market_location", None), "record")
                                     else None),
                "location_permission": (getattr(trigger, "location_permission", None).record()
                                         if getattr(trigger, "location_permission", None) is not None
                                         and hasattr(getattr(trigger, "location_permission", None), "record")
                                         else None),
                "reaction_state": getattr(trigger, "reaction_state", "NO_REACTION"),
                "confirmation_state": getattr(trigger, "confirmation_state", "NONE"),
                "selected": trigger is winner,
            })
        if not rows:
            return
        winner_id = next((r["candidate_id"] for r in rows if r["selected"]), None)
        self._emit({"kind": "selection", "winner_candidate_id": winner_id,
                    "winner_trigger": getattr(winner, "trigger_type", "NONE"),
                    "matched_triggers": list(getattr(winner, "matched_triggers", ()) or ()),
                    "rows": rows, "completed_bar_ts": _jsonable(completed_bar_ts),
                    "evaluation_ts": _jsonable(evaluation_ts)})

    def record_location_result(self, candidate_id: str, trigger: Any,
                               completed_bar_ts: Any = None,
                               evaluation_ts: Any = None) -> None:
        """Record the shared location/reaction decision for one candidate."""
        permission = getattr(trigger, "location_permission", None)
        values = (permission.record() if permission is not None
                  and hasattr(permission, "record") else {})
        if permission is None:
            self.stage(candidate_id, "LOCATION_CHECKED", "NOT_APPLICABLE",
                       "location permission unavailable", values=values,
                       completed_bar_ts=completed_bar_ts,
                       evaluation_ts=evaluation_ts,
                       source_timeframes=("M15", "M5"))
            return
        if getattr(permission, "executable", False):
            self.stage(candidate_id, "LOCATION_CHECKED", "PASS",
                       getattr(permission, "reason", "location permission granted"),
                       values=values, completed_bar_ts=completed_bar_ts,
                       evaluation_ts=evaluation_ts,
                       source_timeframes=("D1", "H4", "M15"))
            self.stage(candidate_id, "M5_CONFIRMATION_CHECKED", "PASS",
                       getattr(permission, "confirmation_state", "TRIGGER_INTERNAL"),
                       values=values, completed_bar_ts=completed_bar_ts,
                       evaluation_ts=evaluation_ts,
                       source_timeframes=("M5",))
            return
        reason = (getattr(permission, "rejection_code", None)
                  or getattr(permission, "reason", "NO_IMPORTANT_LOCATION"))
        self.stage(candidate_id, "LOCATION_CHECKED", "FAIL", reason,
                   values=values, completed_bar_ts=completed_bar_ts,
                   evaluation_ts=evaluation_ts,
                   source_timeframes=("D1", "H4", "M15"))
        confirmation_reason = getattr(permission, "reason", reason) if getattr(permission, "reason", reason) in {
            "NO_REACTION", "NO_RECLAIM", "NO_DISPLACEMENT",
            "LOCATION_INVALIDATED", "SETUP_EXPIRED",
        } else "NOT_APPLICABLE"
        self.stage(candidate_id, "M5_CONFIRMATION_CHECKED",
                   "FAIL" if confirmation_reason != "NOT_APPLICABLE" else "NOT_APPLICABLE",
                   confirmation_reason, values=values,
                   completed_bar_ts=completed_bar_ts,
                   evaluation_ts=evaluation_ts,
                   source_timeframes=("M5",))
        self.terminal(candidate_id, "REJECTED", reason, values)

    def stage(self, candidate_id: str, stage: str, status: str,
              reason: str = "", values: Optional[dict] = None,
              completed_bar_ts: Any = None, evaluation_ts: Any = None,
              source_timeframes: Iterable[str] = ()) -> None:
        if not self.enabled or candidate_id not in self._states:
            return
        if stage not in STAGES or status not in STATUSES:
            raise ValueError(f"invalid funnel stage/status: {stage}/{status}")
        state = self._states[candidate_id]
        if state.terminal:
            return
        # A repeated stage is idempotent, except that an explicitly observed
        # WAIT may be resolved once by a later PASS/FAIL observation.
        if stage in state.stages:
            previous = state.stages[stage]
            if previous["status"] != "WAIT" or status == "WAIT":
                return
        event = {"kind": "stage", "candidate_id": candidate_id,
                 "stage": stage, "status": status, "reason": reason,
                 "values": _jsonable(values or {}),
                 "completed_bar_ts": _jsonable(completed_bar_ts),
                 "evaluation_ts": _jsonable(evaluation_ts),
                 "source_timeframes": sorted(str(x) for x in source_timeframes)}
        state.stages[stage] = event
        self._emit(event)

    def terminal(self, candidate_id: str, status: str, reason: str = "",
                 values: Optional[dict] = None) -> None:
        if not self.enabled or candidate_id not in self._states:
            return
        if status not in TERMINALS:
            raise ValueError(f"invalid terminal status: {status}")
        state = self._states[candidate_id]
        if state.terminal:
            return
        self.stage(candidate_id, "TERMINATED", "PASS", reason, values)
        # Complete the schema so every candidate has an explicit result for
        # every stage, including stages that did not apply to its trigger.
        for stage in STAGES:
            if stage not in state.stages:
                self.stage(candidate_id, stage, "NOT_APPLICABLE")
        state.terminal = status
        self._emit({"kind": "terminal", "candidate_id": candidate_id,
                    "status": status, "reason": reason,
                    "values": _jsonable(values or {})})

    def preempt(self, candidate_id: str, winning_candidate_id: str,
                winning_trigger: str, current_stage: str = "CANDIDATE_CREATED") -> None:
        self.stage(candidate_id, current_stage, "FAIL", "preempted by priority",
                   values={"winning_candidate_id": winning_candidate_id,
                           "winning_trigger": winning_trigger})
        self.terminal(candidate_id, "PREEMPTED", "higher-priority candidate selected",
                      {"winning_candidate_id": winning_candidate_id,
                       "winning_trigger": winning_trigger})

    def _emit(self, event: dict) -> None:
        if not self.enabled:
            return
        event = {"recorded_at": datetime.now(timezone.utc).isoformat(), **event}
        self._events.append(event)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, sort_keys=True, default=str) + "\n")

    def summary(self) -> dict:
        by_trigger: dict[str, dict[str, int]] = {}
        for state in self._states.values():
            row = by_trigger.setdefault(state.candidate.trigger, {s: 0 for s in TERMINALS})
            if state.terminal:
                row[state.terminal] += 1
        return {"candidates": len(self._states), "by_trigger": by_trigger,
                "events": len(self._events)}

    def integrity(self) -> dict:
        return {"unique_candidate_ids": len(self._states) == len({*self._states}),
                "terminal_once": all(sum(e.get("kind") == "terminal"
                                          and e.get("candidate_id") == cid
                                          for e in self._events) <= 1
                                      for cid in self._states),
                "completed_bar_only": all(
                    not (e.get("completed_bar_ts") and e.get("evaluation_ts")
                         and _jsonable(e["evaluation_ts"]) < _jsonable(e["completed_bar_ts"]))
                    for e in self._events if e.get("kind") == "stage")}

    @property
    def events(self) -> tuple[dict, ...]:
        return tuple(self._events)
