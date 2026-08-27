"""
SA Risk Governor (SA-CRG)
==========================
Completely independent of main system CRG.
Runs 5 mandatory checks before EVERY SA trade.

Checks (from SCALPER_AGENT.md):
    1. Is SA within daily loss limit?
    2. Is main account in drawdown > 3%?
    3. Are fewer than 2 SA positions open?
    4. Is the instrument tradeable (spread normal, no news)?
    5. Has the 15-minute pause after 2 consecutive losses elapsed?

If ANY check fails → BLOCK TRADE (no override allowed).
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger("SA.CRG")


@dataclass
class CRGDecision:
    approved: bool
    blocked_reason: str = ""
    checks: dict = None     # Detailed pass/fail per check

    def __post_init__(self):
        if self.checks is None:
            self.checks = {}


class SACRG:
    """
    SA Capital Risk Governor — independent of all main system logic.
    Every method is stateless except for the pause timer tracking.
    """

    # Spec: 15-minute pause after 2 consecutive losses
    PAUSE_AFTER_LOSSES = 2
    PAUSE_DURATION_MINS = 15
    # Spec: main account drawdown > 3% triggers PROTECTED state
    MAIN_DD_THRESHOLD_PCT = 3.0

    def __init__(self):
        self._pause_until: Optional[datetime] = None
        # Tracks the consecutive-loss count at which the current pause was
        # armed. Prevents re-arming on the same streak after the pause
        # elapses (a latent bug pre-fix) and prevents the restart bug where
        # a restored streak would arm a fresh 15-min pause from "now".
        self._pause_armed_at_loss_count: int = 0

    def check(self,
              sa_daily_loss_usd: float,
              sa_max_daily_loss_usd: float,
              sa_open_positions: int,
              sa_consecutive_losses: int,
              current_spread_pips: float,
              max_spread_pips: float,
              main_account_dd_pct: float,
              news_blackout_active: bool = False,
              now: Optional[datetime] = None,
              ) -> CRGDecision:
        """
        Run all 5 SA-CRG checks.
        Returns CRGDecision with approved=True only if ALL checks pass.
        """
        checks = {}

        # ── Check 1: SA within daily loss limit ─────────────────────────────
        daily_ok = sa_daily_loss_usd < sa_max_daily_loss_usd
        checks["daily_loss_limit"] = {
            "passed": daily_ok,
            "detail": f"Daily loss ${sa_daily_loss_usd:.2f} / limit ${sa_max_daily_loss_usd:.2f}"
        }
        if not daily_ok:
            logger.warning(f"SA-CRG BLOCK: Daily loss limit hit "
                           f"(${sa_daily_loss_usd:.2f} >= ${sa_max_daily_loss_usd:.2f})")
            return CRGDecision(approved=False,
                               blocked_reason="Daily loss limit exceeded → SA HALTED",
                               checks=checks)

        # ── Check 2: Main account drawdown ≤ 3% ────────────────────────────
        main_ok = main_account_dd_pct <= self.MAIN_DD_THRESHOLD_PCT
        checks["main_account_dd"] = {
            "passed": main_ok,
            "detail": f"Main DD {main_account_dd_pct:.2f}% (threshold {self.MAIN_DD_THRESHOLD_PCT}%)"
        }
        if not main_ok:
            logger.warning(f"SA-CRG BLOCK: Main account drawdown {main_account_dd_pct:.2f}% "
                           f"> {self.MAIN_DD_THRESHOLD_PCT}% → SA PROTECTED")
            return CRGDecision(approved=False,
                               blocked_reason=f"Main system in {main_account_dd_pct:.1f}% drawdown → PROTECTED",
                               checks=checks)

        # ── Check 3: Fewer than 2 SA positions open ─────────────────────────
        positions_ok = sa_open_positions < 2
        checks["max_positions"] = {
            "passed": positions_ok,
            "detail": f"{sa_open_positions}/2 SA positions open"
        }
        if not positions_ok:
            logger.info(f"SA-CRG BLOCK: Max 2 SA positions already open")
            return CRGDecision(approved=False,
                               blocked_reason="Max 2 SA positions already open",
                               checks=checks)

        # ── Check 4: Instrument tradeable (spread acceptable) ───────────────
        spread_ok = current_spread_pips <= max_spread_pips
        checks["spread_check"] = {
            "passed": spread_ok,
            "detail": f"Spread {current_spread_pips:.1f} pips (max {max_spread_pips:.1f})"
        }
        if not spread_ok:
            logger.info(f"SA-CRG BLOCK: Spread too wide "
                        f"({current_spread_pips:.1f} > {max_spread_pips:.1f})")
            return CRGDecision(approved=False,
                               blocked_reason=f"Spread too wide: {current_spread_pips:.1f} pips",
                               checks=checks)

        # ── Check 4b: News blackout window ───────────────────────────────────
        checks["news_blackout"] = {
            "passed": not news_blackout_active,
            "detail": "No active high-impact news blackout" if not news_blackout_active else "High-impact news blackout active"
        }
        if news_blackout_active:
            logger.info("SA-CRG BLOCK: High-impact news blackout active")
            return CRGDecision(
                approved=False,
                blocked_reason="High-impact news blackout active",
                checks=checks,
            )

        # ── Check 5: 15-minute pause after 2 consecutive losses ─────────────
        # `now` is injected by the simulator so the pause timer advances on the
        # replayed bar clock; it defaults to the wall clock for the live agent.
        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        # Streak broken or below threshold — clear arming memory so the next
        # genuine elevation can arm fresh.
        if sa_consecutive_losses < self.PAUSE_AFTER_LOSSES:
            self._pause_armed_at_loss_count = 0
        elif sa_consecutive_losses > self._pause_armed_at_loss_count:
            # Genuine NEW loss elevation — arm pause from now.
            if self._pause_until is None:
                self._pause_until = now + timedelta(minutes=self.PAUSE_DURATION_MINS)
                self._pause_armed_at_loss_count = sa_consecutive_losses
                logger.warning(f"SA-CRG: {sa_consecutive_losses} consecutive losses — "
                                f"pause until {self._pause_until.strftime('%H:%M')} UTC")

        pause_ok = True
        if self._pause_until and now < self._pause_until:
            pause_ok = False
            mins_left = int((self._pause_until - now).total_seconds() / 60)
            checks["loss_pause"] = {
                "passed": False,
                "detail": f"Pause active: {mins_left}m remaining"
            }
            logger.info(f"SA-CRG BLOCK: Loss pause active, {mins_left}m remaining")
            return CRGDecision(approved=False,
                               blocked_reason=f"15-min loss pause active ({mins_left}m left)",
                               checks=checks)
        else:
            if self._pause_until and now >= self._pause_until:
                self._pause_until = None  # Pause elapsed
            checks["loss_pause"] = {"passed": True, "detail": "No active pause"}

        # ── All checks passed ────────────────────────────────────────────────
        logger.info("SA-CRG: All checks passed — trade APPROVED")
        return CRGDecision(approved=True, blocked_reason="", checks=checks)

    def reset_pause(self):
        """Manually clear pause (e.g., after daily reset)."""
        self._pause_until = None
        self._pause_armed_at_loss_count = 0

    def prime_from_history(self, last_loss_close_time: Optional[datetime],
                           consecutive_losses: int):
        """
        Restart-safe pause priming. Called from scalper_agent after pool.restore()
        so the 15-minute cooldown reflects when the last loss ACTUALLY happened,
        not when the bot restarted.

        Semantics:
          - consecutive_losses < threshold     : no-op.
          - last_loss_close_time is None       : fall back to legacy behaviour
                                                 (next check() will arm pause from now).
          - last_loss + 15min > now            : arm pause to expire at last_loss + 15min.
          - last_loss + 15min <= now           : pause already elapsed during downtime.
                                                 Mark as already-armed so the next check()
                                                 does not redundantly re-arm from now.
        """
        if consecutive_losses < self.PAUSE_AFTER_LOSSES:
            return
        if last_loss_close_time is None:
            # No timestamp available — let normal check() flow arm from now.
            return
        now = datetime.now(timezone.utc)
        expiry = last_loss_close_time + timedelta(minutes=self.PAUSE_DURATION_MINS)
        # Mark this streak as already-armed regardless of expiry, so the
        # check() arming branch does not fire a second time on the same loss
        # count (which would extend the pause incorrectly).
        self._pause_armed_at_loss_count = consecutive_losses
        if expiry > now:
            self._pause_until = expiry
            mins_left = int((expiry - now).total_seconds() / 60)
            logger.warning(
                f"SA-CRG primed from history: {consecutive_losses} prior losses, "
                f"pause expires at {expiry.strftime('%H:%M')} UTC ({mins_left}m left)"
            )
        else:
            elapsed_min = (now - expiry).total_seconds() / 60
            logger.info(
                f"SA-CRG primed from history: {consecutive_losses} prior losses but "
                f"15-min pause already elapsed {elapsed_min:.1f}m ago — no pause armed"
            )
