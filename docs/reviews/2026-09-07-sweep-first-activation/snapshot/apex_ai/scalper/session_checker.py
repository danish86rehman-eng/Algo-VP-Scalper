"""
SA Session Window Checker
==========================
Determines if SA is allowed to trade based on current UTC time.
SA defines five named micro-windows, but is active by default only during
Tokyo Open and the London-NY overlap.

Session Windows (UTC):
    Tokyo Open         : 00:00 – 02:00  (Asia Kill Zone)
    Pre-London         : 06:30 – 07:00
    London Open        : 07:00 – 08:30  (Primary scalp window)
    London-NY Overlap  : 12:00 – 13:30  (Highest volatility)
    NY Lunch Reversal  : 16:30 – 17:30  (Mean reversion scalps)

Pre-London, London Open, NY Lunch, and all other times → SA goes IDLE by
default. Disabled named windows remain available through an explicit session
whitelist for controlled research runs.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone, time as dtime
from typing import Optional


@dataclass
class SessionWindow:
    name: str
    start: dtime
    end: dtime
    description: str
    priority: int       # 1=highest volatility, 4=lowest


#SESSION_WINDOWS = [
#    SessionWindow("LONDON_OPEN",    dtime(7, 0),  dtime(8, 30),  "Primary scalp window 12:00 - 13:30",          1),
#    SessionWindow("LONDON_NY",      dtime(12, 0), dtime(15, 00), "Highest volatility window 5:00 - 8:00",      1),
#    SessionWindow("PRE_LONDON",     dtime(6, 30), dtime(7, 0),   "Early liquidity grab 11:30 - 12:00",           2),
#    SessionWindow("TOKYO_OPEN",     dtime(0, 0),  dtime(6, 0),   "Tokyo Open / Asia Kill Zone 5:00 - 11:00",   2),
#    SessionWindow("NY_LUNCH_REV",   dtime(16, 30), dtime(17, 30),"Mean reversion scalps 9:30 - 10:30",          3),
#]

SESSION_WINDOWS = [
     SessionWindow("LONDON_OPEN", dtime(7, 0), dtime(8, 30), "Primary scalp window", 1),
     SessionWindow("LONDON_NY", dtime(12, 0), dtime(13, 30), "Highest volatility window", 1),
     SessionWindow("PRE_LONDON", dtime(6, 30), dtime(7, 0), "Early liquidity grab", 2),
     SessionWindow("TOKYO_OPEN", dtime(0, 0), dtime(2, 0), "Tokyo Open / Asia Kill Zone", 2),
     SessionWindow("NY_LUNCH_REV", dtime(16, 30), dtime(17, 30),"Mean reversion scalps", 3), ]


# Operator-selected production default (2026-09-05). Keep all five definitions
# above so a controlled replay can still request an excluded window explicitly.
# Both live and backtest instantiate this checker, so this one default preserves
# decision-path parity.
DEFAULT_ENABLED_SESSIONS = ("TOKYO_OPEN", "LONDON_NY")

# The `Whole_day` 00:00-23:00 fallback used to live at the end of this list.
# Because `get_state()` returns the FIRST matching window, it matched every
# hour outside a kill zone — IDLE became unreachable and the agent scanned 23
# hours a day, which is the opposite of what a kill-zone gate is for. Live logs
# confirmed `Session=Whole_day` during ordinary trading.
#
# Measured directly on this stack 2026-08-26 (RESEARCH_NOTES.md SS15, ledger
# L-010): six paired arms, `--allow-whole-day` the only variable. Kill-zone
# gating is fold-CONSISTENT in all six; the whole-day arm is fold-UNSTABLE in
# all six, and on the favourable window it cuts net P&L 78% (+$2742 -> +$609)
# while nearly doubling drawdown.
#
# The reason is NOT that off-session hours are toxic - those extra 224 trades
# are ~breakeven (PF 1.01). It is that they cannibalise the kill-zone book,
# which falls +$2742 -> +$581 on the same hours. One position per symbol
# (SYMBOL_DEDUP 856 -> 3656) is the structural channel: an off-session entry
# with a 6h bar timeout can still be open when London Open arrives. Hour 00 is
# the natural control - nothing precedes it, so its trade count is identical
# across arms (77 -> 77) while every downstream kill-zone hour loses trades and
# flips negative.
#
# CORRECTION: this comment previously cited a donor-campaign finding that "the
# Asia block carried 68-73% of trade volume and the largest absolute loss".
# That does not reproduce here. Asia is 43.0% / 40.2% of volume in the two
# windows tested and carries the largest loss in neither; it is the only
# profitable block in the recent window, and TOKYO_OPEN is the single best
# session in the book (109 trades, 51.38% WR, +$1820, PF 1.60). The donor
# figure described a different strategy on 1R geometry. The conclusion below
# survives; that rationale did not. Do not reason from it - it would point at
# cutting TOKYO_OPEN, the most expensive session to remove.
#
# The fallback is therefore opt-in only, via
# SASessionChecker(allow_whole_day=True), and is never part of the default
# window set.
WHOLE_DAY_FALLBACK = SessionWindow(
    "Whole_day", dtime(0, 0), dtime(23, 0),
    "Full-day fallback (demo/testing only — disabled by default)", 9)


@dataclass
class SessionState:
    in_window: bool
    window: Optional[SessionWindow]
    utc_time: datetime
    minutes_remaining: int      # Minutes left in current window (0 if IDLE)
    next_window: Optional[SessionWindow] = None
    minutes_to_next: int = 0

    @property
    def window_name(self) -> str:
        return self.window.name if self.window else "IDLE"

    @property
    def description(self) -> str:
        if self.in_window and self.window:
            return f"{self.window.name} | {self.minutes_remaining}m remaining"
        if self.next_window:
            return f"IDLE | Next: {self.next_window.name} in {self.minutes_to_next}m"
        return "IDLE | No upcoming SA windows today"


class SASessionChecker:
    """
    SA-specific session window checker.
    Completely standalone — does not reference APEX session engine.

    Args:
        allow_whole_day : Include the 00:00-23:00 catch-all window. Defaults to
            False so kill-zone gating actually gates. Set True only for demo
            plumbing runs where signal quality does not matter.
    """

    #: Every window name this checker can return, for validating a whitelist.
    ALL_WINDOWS = tuple(w.name for w in SESSION_WINDOWS)

    def __init__(self, allow_whole_day: bool = False, enabled_sessions=None,
                 vp_window: Optional[tuple] = None):
        self.allow_whole_day = bool(allow_whole_day)
        # Trigger-scoped allowance for VP_LIQUIDITY_REACTION. Deliberately NOT
        # a member of `SESSION_WINDOWS`: that list is walked first-match by
        # `get_state`, so adding a wide window there would hand every existing
        # trigger the extra hours — which is the `Whole_day` mistake that
        # L-010 measured and rejected. This is a separate predicate that only
        # the VP trigger's caller consults.
        self.vp_window = vp_window
        windows = list(SESSION_WINDOWS)
        # Per-session whitelist. Session attribution of the 2026-05-15 ->
        # 08-21 book showed the two London windows losing -$202.68 over 69
        # trades at a 23.19% win rate, negative in all three chronological
        # folds — the only block with a stable sign. Being able to switch a
        # window off is therefore a gate, and lives identically in the live
        # agent and the backtester (invariant #2).
        requested_sessions = (DEFAULT_ENABLED_SESSIONS
                              if enabled_sessions is None else enabled_sessions)
        if requested_sessions is not None:
            requested = {str(x).strip().upper() for x in requested_sessions
                         if str(x).strip()}
            unknown = requested - set(self.ALL_WINDOWS)
            if unknown:
                raise ValueError(
                    f"Unknown session(s) {sorted(unknown)}; "
                    f"valid: {sorted(self.ALL_WINDOWS)}")
            if not requested:
                raise ValueError("enabled_sessions must not be empty")
            windows = [w for w in windows if w.name in requested]
        self.enabled_sessions = {w.name for w in windows}
        self.windows = windows
        if self.allow_whole_day:
            self.windows.append(WHOLE_DAY_FALLBACK)

    def get_state(self, utc_now: Optional[datetime] = None) -> SessionState:
        if utc_now is None:
            utc_now = datetime.now(timezone.utc)
        t = utc_now.time()

        # Check if currently inside a window
        for window in self.windows:
            if window.start <= t < window.end:
                start_dt = utc_now.replace(
                    hour=window.start.hour, minute=window.start.minute,
                    second=0, microsecond=0)
                end_dt = utc_now.replace(
                    hour=window.end.hour, minute=window.end.minute,
                    second=0, microsecond=0)
                mins_remaining = max(0, int((end_dt - utc_now).total_seconds() / 60))
                return SessionState(
                    in_window=True,
                    window=window,
                    utc_time=utc_now,
                    minutes_remaining=mins_remaining,
                )

        # Not in any window — find next one
        next_win = None
        mins_to_next = 9999
        for window in sorted(self.windows, key=lambda w: w.start):
            if t < window.start:
                from datetime import datetime as dt
                now_mins = t.hour * 60 + t.minute
                win_mins = window.start.hour * 60 + window.start.minute
                diff = win_mins - now_mins
                if diff < mins_to_next:
                    mins_to_next = diff
                    next_win = window

        return SessionState(
            in_window=False,
            window=None,
            utc_time=utc_now,
            minutes_remaining=0,
            next_window=next_win,
            minutes_to_next=mins_to_next if mins_to_next < 9999 else 0,
        )

    def is_active(self, utc_now: Optional[datetime] = None) -> bool:
        return self.get_state(utc_now).in_window

    def vp_window_open(self, utc_now: Optional[datetime] = None) -> bool:
        """
        Is the VP_LIQUIDITY_REACTION-only allowance open right now?

        Same clock and same half-open convention as `get_state` — start
        inclusive, end exclusive — so a boundary minute belongs to exactly one
        of the two layers and the two can never disagree about "now".

        Returns False when no VP window is configured, which is what keeps this
        inert for every run that does not enable the trigger.
        """
        if not self.vp_window:
            return False
        if utc_now is None:
            utc_now = datetime.now(timezone.utc)
        start, end = self.vp_window
        return start <= utc_now.time() < end

    def vp_window_name(self, utc_now: Optional[datetime] = None) -> str:
        """Window label for telemetry — the normal session if one is open."""
        state = self.get_state(utc_now)
        if state.in_window:
            return state.window_name
        return "VP_ASIA" if self.vp_window_open(utc_now) else "IDLE"
