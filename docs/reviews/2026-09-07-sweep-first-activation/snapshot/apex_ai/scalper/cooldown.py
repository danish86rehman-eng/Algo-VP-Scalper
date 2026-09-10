"""
SA Cooldown
===========
Scalper-only post-trade entry cooldown.

Policy (owner-specified):
    LOSS -> block new entries until the top of the NEXT UTC hour.
            A loss at 11:15 blocks until 12:00. A loss at 11:55 also blocks
            until 12:00 — the rule is deliberately "until the hour turns",
            not "for N minutes", so the pause length varies with how late in
            the hour the loss happened.
    WIN  -> block new entries for a short fixed break (default 5 minutes).

Why this shape: the loss branch forces the agent to sit out the remainder of
the hourly candle it just lost in, which is the smallest unit of "let the
current context finish" that does not require a regime model. The win branch
only prevents same-bar re-entry stacking.

Breakeven (pnl == 0) is treated as a loss for cooldown purposes: a scratch
trade still means the setup did not work, and costs were paid.

This module is deliberately dependency-free so it can be unit-tested and
reused without an MT5 connection.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger("SA.Cooldown")

LOSS_POLICY_NEXT_UTC_HOUR = "NEXT_UTC_HOUR"
LOSS_POLICY_FIXED_MINUTES = "FIXED_MINUTES"


@dataclass
class CooldownState:
    active: bool
    reason: str
    until: Optional[datetime]
    remaining_seconds: int


class SACooldown:
    """Global entry cooldown for the isolated scalper agent."""

    def __init__(
        self,
        win_minutes: float = 5.0,
        loss_policy: str = LOSS_POLICY_NEXT_UTC_HOUR,
        loss_minutes: float = 60.0,
        enabled: bool = True,
    ):
        """
        Args:
            win_minutes  : Break after a winning trade.
            loss_policy  : NEXT_UTC_HOUR (default) or FIXED_MINUTES.
            loss_minutes : Only used when loss_policy is FIXED_MINUTES.
            enabled      : Master switch. When False, `can_enter()` is always
                           True but results are still recorded, so the switch
                           can be flipped without restarting the agent.
        """
        self.enabled = bool(enabled)
        self.win_cooldown = timedelta(minutes=float(win_minutes))
        self.loss_policy = str(loss_policy).upper()
        self.loss_cooldown = timedelta(minutes=float(loss_minutes))
        self.active_until: Optional[datetime] = None
        self.last_reason: str = ""

    # ── Recording ─────────────────────────────────────────────────────────────

    def record_trade_result(self, pnl_usd: float,
                            now: Optional[datetime] = None) -> CooldownState:
        """Arm the cooldown from a closed trade's realised P&L."""
        now = self._utc(now)

        if pnl_usd > 0:
            self.active_until = now + self.win_cooldown
            self.last_reason = (
                f"WIN ${pnl_usd:+.2f} -> {self.win_cooldown.total_seconds()/60:.0f}m break"
            )
        else:
            self.active_until = self._loss_expiry(now)
            self.last_reason = (
                f"LOSS ${pnl_usd:+.2f} -> hold until "
                f"{self.active_until.strftime('%H:%M')} UTC"
            )

        logger.info(
            f"SA Cooldown armed: {self.last_reason} "
            f"({self.remaining_seconds(now)}s)"
        )
        return self.state(now)

    def _loss_expiry(self, now: datetime) -> datetime:
        if self.loss_policy == LOSS_POLICY_FIXED_MINUTES:
            return now + self.loss_cooldown
        # NEXT_UTC_HOUR — truncate to the hour, then step forward one hour.
        return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

    # ── Querying ──────────────────────────────────────────────────────────────

    def can_enter(self, now: Optional[datetime] = None) -> bool:
        if not self.enabled:
            return True
        if self.active_until is None:
            return True
        return self._utc(now) >= self.active_until

    def remaining_seconds(self, now: Optional[datetime] = None) -> int:
        if self.active_until is None:
            return 0
        delta = (self.active_until - self._utc(now)).total_seconds()
        return max(0, int(delta))

    def state(self, now: Optional[datetime] = None) -> CooldownState:
        now = self._utc(now)
        blocking = self.enabled and not self.can_enter(now)
        return CooldownState(
            active=blocking,
            reason=self.last_reason if blocking else "",
            until=self.active_until if blocking else None,
            remaining_seconds=self.remaining_seconds(now) if blocking else 0,
        )

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Clear the pause — used by the daily reset."""
        self.active_until = None
        self.last_reason = ""

    def restore(self, last_close_time: Optional[datetime],
                last_pnl: Optional[float],
                now: Optional[datetime] = None) -> None:
        """
        Restart-safe priming. Re-arms the cooldown from the last closed trade so
        a process restart cannot be used — accidentally or deliberately — to
        escape an active pause.
        """
        if last_close_time is None or last_pnl is None:
            return
        now = self._utc(now)
        close_time = self._utc(last_close_time)

        if last_pnl > 0:
            expiry = close_time + self.win_cooldown
        else:
            expiry = self._loss_expiry(close_time)

        if expiry > now:
            self.active_until = expiry
            self.last_reason = (
                f"restored from last close ${last_pnl:+.2f} at "
                f"{close_time.strftime('%H:%M')} UTC"
            )
            logger.warning(
                f"SA Cooldown restored from history: holds until "
                f"{expiry.strftime('%H:%M')} UTC "
                f"({self.remaining_seconds(now)}s left)"
            )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _utc(moment: Optional[datetime]) -> datetime:
        if moment is None:
            return datetime.now(timezone.utc)
        if moment.tzinfo is None:
            return moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc)
