"""
Structured rejection telemetry — what the gates threw away, counted.

Why this module exists
----------------------
Every gate in `_scan_symbol` already logs its own name on veto, and the
operational playbook's answer to "why didn't it trade?" is to grep the log and
read down the chain. That works for one incident and fails for the question
that actually matters: *how many* setups did each gate kill this week, and
which gate is doing the most damage.

It also makes a standing invariant mechanical. CLAUDE.md 13.9 requires any
comparison across risk levels to report `LOT_FLOOR` counts, because a run that
takes 604 lot-floor rejections is measuring the residue rather than the
strategy. That number was previously obtainable only by grepping a log that
rotates. Here it is a field.

Scope
-----
Counting only. Like `postmortem`, this module has **zero decision authority**:
it is called after a gate has already decided, it returns nothing the caller
uses, and no gate reads it back. That is deliberate — a counter that fed back
into gating would be a strategy change wearing telemetry's clothes, and would
have to be mirrored under invariant #2. It is not, and must not become one.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("SA.Rejects")


#: Canonical gate names, in the order `_scan_symbol` evaluates them. The first
#: match is the answer, so a rejection is attributed to exactly one stage.
#: Mirrors the diagnostic table in `.claude/skills/apex-ops/SKILL.md`.
STAGES = (
    "NO_DATA",              # OHLCV fetch failed — a silent scan outage
    "SESSION_IDLE",         # outside every kill-zone window
    "COOLDOWN",             # post-trade pause
    "DEDUP",                # symbol already has an open position
    "NO_TRIGGER",           # nothing detected — the common case, counted for a denominator
    "PULLBACK_RECOVERY_UNAVAILABLE",
    "PULLBACK_QUOTE_STALE",
    "EMA_BAND",             # H1 EMA(18) band veto (off by default)
    "STB",                  # short-term-bias gate
    "THIN_LIQ",             # thin-liquidity hours need HIGH confidence
    "CONSULT_UNAVAILABLE",  # consultation failed outright
    "CONSULT_STALE",        # council snapshot older than 60s
    "CONSULT_LATENCY",      # consultation slower than the budget
    "GATE1_REGIME",         # regime not whitelisted for this trigger
    "GATE2_DISPLACEMENT",   # BOS_RETEST without confirmed displacement
    "STEP3_VALIDATE",       # spread cap, spread:SL ratio, SL floor, net-R
    "CRG",                  # daily loss, drawdown, position count, news
    "LOT_FLOOR",            # below broker minimum volume
    "EXECUTE_FAIL",         # broker rejected the order
)

#: How many distinct reason strings to retain per stage. Reasons carry prices
#: and percentages, so the cardinality is unbounded; keeping a bounded sample
#: preserves the flavour without letting the file grow without limit.
MAX_REASON_SAMPLES = 8


class RejectionLog:
    """
    Daily, per-symbol, per-stage rejection counts.

    Persisted as a JSON object keyed by UTC date. Writes are batched — a veto
    happens several times per scan cycle and this is not worth a disk write
    each time — and forced on a date roll so a day's totals are never split
    across a restart.
    """

    def __init__(self, path: str = "logs/sa_rejections.json",
                 flush_every: int = 25):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.flush_every = max(1, int(flush_every))
        self._pending = 0
        self._current_day: Optional[str] = None
        self._data: Dict[str, Any] = self._load()

    # -- Recording ------------------------------------------------------------

    def record(self, stage: str, symbol: str, reason: str = "",
               now: Optional[datetime] = None) -> None:
        """
        Count one rejection. Never raises: telemetry must not be able to break
        the trading loop it is observing.
        """
        try:
            now = now or datetime.now(timezone.utc)
            day = now.date().isoformat()

            if self._current_day is not None and day != self._current_day:
                self._write()          # seal the previous day before rolling
            self._current_day = day

            stage = stage if stage in STAGES else "UNKNOWN"
            bucket = self._data.setdefault(day, {})
            sym = bucket.setdefault(symbol, {"counts": {}, "reasons": {}})
            sym["counts"][stage] = sym["counts"].get(stage, 0) + 1

            if reason:
                samples = sym["reasons"].setdefault(stage, [])
                if reason not in samples and len(samples) < MAX_REASON_SAMPLES:
                    samples.append(reason)

            self._pending += 1
            if self._pending >= self.flush_every:
                self._write()
        except Exception as exc:                  # pragma: no cover - guard
            logger.warning(f"rejection telemetry failed: {exc}")

    def flush(self) -> None:
        """Force a write. Called at end-of-day reset and on shutdown."""
        self._write()

    # -- Reading --------------------------------------------------------------

    def counts_for(self, day: str, symbol: Optional[str] = None
                   ) -> Dict[str, int]:
        """Stage counts for one day, for one symbol or summed across all."""
        bucket = self._data.get(day, {})
        if symbol is not None:
            return dict(bucket.get(symbol, {}).get("counts", {}))
        total: Dict[str, int] = {}
        for sym in bucket.values():
            for stage, n in sym.get("counts", {}).items():
                total[stage] = total.get(stage, 0) + n
        return total

    def totals(self) -> Dict[str, int]:
        """Stage counts summed over every recorded day."""
        total: Dict[str, int] = {}
        for day in self._data:
            for stage, n in self.counts_for(day).items():
                total[stage] = total.get(stage, 0) + n
        return total

    def summary_line(self, day: Optional[str] = None) -> str:
        """One-line digest for the daily log, busiest gate first."""
        day = day or datetime.now(timezone.utc).date().isoformat()
        counts = self.counts_for(day)
        if not counts:
            return f"rejections {day}: none recorded"
        ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
        body = " ".join(f"{stage}={n}" for stage, n in ranked)
        return f"rejections {day}: {body}"

    # -- Persistence ----------------------------------------------------------

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8")) or {}
        except (json.JSONDecodeError, OSError):
            logger.warning(f"unreadable rejection log at {self.path} — starting fresh")
            return {}

    def _write(self) -> None:
        try:
            self.path.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False),
                encoding="utf-8")
            self._pending = 0
        except Exception as exc:                  # pragma: no cover - guard
            logger.warning(f"rejection log write failed: {exc}")
