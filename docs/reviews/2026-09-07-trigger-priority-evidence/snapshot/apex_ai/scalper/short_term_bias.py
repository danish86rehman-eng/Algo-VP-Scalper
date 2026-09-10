"""
Short-Term Bias Filter (Phase 1) — replaces HTFBiasFilter as SA's primary gate.

WHY THIS EXISTS
===============
A scalper's edge comes from short-term intraday flow, not from H4/H1
alignment. The prior HTFBiasFilter blocked the most valuable setups
("London just took Asia liquidity → expect bearish reversal") because the
H4 trend was bullish, ignoring the immediate session structure. Today's
real losses came from the bot taking long after London swept Asia highs
in a bullish-H4 context — exactly the textbook short scenario.

HOW IT WORKS
============
Three layers, in priority order:

  1. Session Liquidity Awareness (NEW & most important)
     - Snapshots today's Asia / London / NY high & low per symbol.
     - Detects which have been taken out and by which subsequent session.
     - "Don't chase" rule: block trigger directions matching the direction
       of a recently-swept pool.
     - Reward fading: counter-sweep + SWEEP_REJECTION → HIGH confidence.

  2. Intraday M5 Structure
     - ATR-normalized net move over the last ~1 hour of M5 bars.
     - Returns BULLISH / BEARISH / NEUTRAL — the bot's "human-eye" read.

  3. HTF Context (tiebreaker, NOT gate)
     - H1 / H4 trend via StructureEngine, used only to BOOST confidence
       when it agrees with short-term direction. Disagreement no longer
       blocks the trade.

The filter returns STBResult(allow, confidence, short_term_bias,
htf_trend, recent_sweep, reason). The caller can route confidence to
risk sizing in Phase 2 (currently informational / logged).
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List
import logging

import pandas as pd

from core.structure_engine import StructureEngine
from scalper import decision_params as DP

logger = logging.getLogger("SA.STBias")


# ── Session windows (UTC) ─────────────────────────────────────────────────────
# Overlapping windows intentional: a level can be Asian-formed AND still
# "in London" — the formed_at timestamp distinguishes them.
SESSION_WINDOWS = {
    "ASIA":   (0, 8),     # 00:00-08:00 UTC
    "LONDON": (7, 16),    # 07:00-16:00 UTC
    "NY":     (12, 21),   # 12:00-21:00 UTC
}


# ── Data Classes ──────────────────────────────────────────────────────────────

@dataclass
class SessionPool:
    """A liquidity level formed at a session's high or low."""
    session_name: str       # ASIA / LONDON / NY
    pool_type:    str       # HIGH / LOW
    level:        float
    formed_at:    datetime  # End-of-session timestamp (or last seen bar)
    taken:        bool = False
    taken_at:     Optional[datetime] = None
    taken_by_session: Optional[str] = None

    @property
    def sweep_direction(self) -> str:
        """When taken, which direction did the sweep go?
        HIGH pool taken means price went UP through it = BULLISH sweep.
        LOW pool taken means price went DOWN through it = BEARISH sweep.
        """
        return "BULLISH" if self.pool_type == "HIGH" else "BEARISH"


@dataclass
class STBResult:
    allow: bool
    confidence:      str = "NONE"      # HIGH / MEDIUM / LOW / NONE
    short_term_bias: str = "UNKNOWN"   # BULLISH / BEARISH / NEUTRAL / UNKNOWN
    htf_trend:       str = "UNKNOWN"   # BULLISH / BEARISH / RANGING / UNKNOWN
    recent_sweep:    Optional[str] = None
    reason:          str = ""


# ── Range Detector ────────────────────────────────────────────────────────────

@dataclass
class RangeState:
    in_range:     bool = False
    range_high:   float = 0.0
    range_low:    float = 0.0
    amplitude:    float = 0.0
    atr:          float = 0.0
    position_pct: float = 0.5   # 0 = at range_low, 1 = at range_high

    @property
    def at_upper_extreme(self) -> bool:
        return self.in_range and self.position_pct >= 0.75

    @property
    def at_lower_extreme(self) -> bool:
        return self.in_range and self.position_pct <= 0.25


class RangeDetector:
    """
    Detects tight intraday ranges and price position within them.

    A market is "in range" when over the last LOOKBACK M5 bars:
      1. Amplitude < RANGE_AMPLITUDE_MULT × ATR(M5,14)
      2. Price has touched BOTH the upper edge (≥ EDGE_TOUCHES times)
         AND the lower edge (≥ EDGE_TOUCHES times)

    The first rule catches tight ranges. The second rule confirms the
    extremes are real S/R (touched multiple times), not artifacts of a
    single impulse + retrace.

    When in_range, position_pct tells the caller where current price sits
    inside the range — fade trades should only fire near the extremes.
    """
    LOOKBACK            = 20    # ~1.5 hours of M5
    ATR_PERIOD          = 14
    RANGE_AMPLITUDE_MULT = 4.0  # Range ≤ 4×ATR = tight enough to whipsaw
    EDGE_TOLERANCE      = 0.20  # × ATR — "near the edge"
    EDGE_TOUCHES        = 2     # Min touches on each side to confirm range

    def analyze(self, df_m5: pd.DataFrame, current_price: float) -> RangeState:
        st = RangeState()
        if df_m5 is None or len(df_m5) < self.LOOKBACK + self.ATR_PERIOD + 1:
            return st
        recent = df_m5.iloc[-self.LOOKBACK:]
        rng_hi = float(recent['high'].max())
        rng_lo = float(recent['low'].min())
        amp = rng_hi - rng_lo
        atr = self._atr(df_m5, self.ATR_PERIOD)
        if atr <= 0 or amp <= 0:
            return st
        st.range_high = rng_hi
        st.range_low  = rng_lo
        st.amplitude  = amp
        st.atr        = atr

        # Rule 1 — amplitude vs ATR
        if amp > self.RANGE_AMPLITUDE_MULT * atr:
            return st  # Trending or wide-volatile, not a tight range

        # Rule 2 — multiple touches on each edge
        edge_tol = self.EDGE_TOLERANCE * atr
        touches_hi = int((recent['high'] >= rng_hi - edge_tol).sum())
        touches_lo = int((recent['low']  <= rng_lo + edge_tol).sum())
        if touches_hi < self.EDGE_TOUCHES or touches_lo < self.EDGE_TOUCHES:
            return st  # Single-sided test isn't a real range

        st.in_range = True
        st.position_pct = max(0.0, min(1.0, (current_price - rng_lo) / amp))
        return st

    @staticmethod
    def _atr(df: pd.DataFrame, period: int) -> float:
        hi = df['high'].values
        lo = df['low'].values
        cl = df['close'].values
        trs = [max(hi[i] - lo[i], abs(hi[i] - cl[i-1]), abs(lo[i] - cl[i-1]))
               for i in range(1, len(df))]
        if len(trs) < period:
            return 0.0
        return float(sum(trs[-period:]) / period)


# ── Intraday Structure ────────────────────────────────────────────────────────

class IntradayStructure:
    """
    Computes short-term M5 directional bias from net displacement over the
    last LOOKBACK_BARS, normalized by ATR. Responsive — flips within ~1 hour
    of a meaningful intraday move, unlike a slow EMA cross.
    """

    LOOKBACK_BARS       = 12     # ~1 hour of M5
    ATR_PERIOD          = 14
    DIRECTION_THRESHOLD = 0.5    # × ATR

    def analyze(self, df_m5: pd.DataFrame) -> str:
        if df_m5 is None or len(df_m5) < self.LOOKBACK_BARS + self.ATR_PERIOD + 1:
            return "UNKNOWN"
        atr = self._atr(df_m5, self.ATR_PERIOD)
        if atr <= 0:
            return "UNKNOWN"
        close_now  = float(df_m5['close'].iloc[-1])
        close_then = float(df_m5['close'].iloc[-self.LOOKBACK_BARS - 1])
        atr_norm   = (close_now - close_then) / atr
        if atr_norm >= self.DIRECTION_THRESHOLD:
            return "BULLISH"
        if atr_norm <= -self.DIRECTION_THRESHOLD:
            return "BEARISH"
        return "NEUTRAL"

    @staticmethod
    def _atr(df: pd.DataFrame, period: int) -> float:
        hi = df['high'].values
        lo = df['low'].values
        cl = df['close'].values
        trs = [max(hi[i] - lo[i], abs(hi[i] - cl[i-1]), abs(lo[i] - cl[i-1]))
               for i in range(1, len(df))]
        if len(trs) < period:
            return 0.0
        return float(sum(trs[-period:]) / period)


# ── Session Liquidity Tracker ─────────────────────────────────────────────────

class SessionLiquidityTracker:
    """
    Rebuilds today's session pool state from M5 bars on each scan.
    Stateless across calls (other than caching last computed pools per
    symbol) — no persistence concerns, no restart bugs.

    A pool is "swept" when a later same-day bar pierces its level. The
    session that produced the breaching bar is recorded as taken_by_session.
    "Recent" = swept within RECENT_WINDOW_MINUTES.
    """

    RECENT_WINDOW_MINUTES = 60

    def __init__(self):
        self.pools_by_symbol: Dict[str, List[SessionPool]] = {}

    def update(self, symbol: str, df_m5: pd.DataFrame, now_utc: datetime) -> None:
        if df_m5 is None or len(df_m5) < 1:
            return
        today = now_utc.date()
        today_bars = df_m5[df_m5['time'].dt.date == today]
        if today_bars.empty:
            self.pools_by_symbol[symbol] = []
            return

        pools: List[SessionPool] = []
        for sess_name, (start_h, end_h) in SESSION_WINDOWS.items():
            sb = today_bars[
                (today_bars['time'].dt.hour >= start_h) &
                (today_bars['time'].dt.hour <  end_h)
            ]
            if sb.empty:
                continue
            high      = float(sb['high'].max())
            low       = float(sb['low'].min())
            formed_at = sb['time'].iloc[-1].to_pydatetime()
            pools.append(SessionPool(sess_name, "HIGH", high, formed_at))
            pools.append(SessionPool(sess_name, "LOW",  low,  formed_at))

        # Mark which pools are taken by later bars
        for pool in pools:
            later = today_bars[today_bars['time'] > pool.formed_at]
            if later.empty:
                continue
            if pool.pool_type == "HIGH":
                breach = later[later['high'] > pool.level]
            else:
                breach = later[later['low']  < pool.level]
            if breach.empty:
                continue
            first_breach = breach.iloc[0]
            pool.taken    = True
            pool.taken_at = first_breach['time'].to_pydatetime()
            hr = first_breach['time'].hour
            for s_name, (s_start, s_end) in SESSION_WINDOWS.items():
                if s_start <= hr < s_end:
                    pool.taken_by_session = s_name
                    break

        self.pools_by_symbol[symbol] = pools

    def recent_sweep(self, symbol: str, now_utc: datetime
                     ) -> Optional[SessionPool]:
        """Most-recently-swept pool within RECENT_WINDOW_MINUTES, or None."""
        pools = self.pools_by_symbol.get(symbol, [])
        if not pools:
            return None
        cutoff = now_utc - timedelta(minutes=self.RECENT_WINDOW_MINUTES)
        recent = [p for p in pools
                  if p.taken and p.taken_at and p.taken_at >= cutoff]
        if not recent:
            return None
        return max(recent, key=lambda p: p.taken_at)


# ── Short-Term Bias Filter (composite) ────────────────────────────────────────

class ShortTermBiasFilter:
    """
    Replaces HTFBiasFilter.

    Decision order:
      1. Recent session-pool sweep   → don't-chase rule
      2. Intraday M5 structure       → primary direction
      3. HTF trend                   → confidence tiebreaker only
    """

    def __init__(self, relax_continuation: bool = DP.STB_RELAX_CONTINUATION):
        self.structure   = StructureEngine(swing_lookback=5)
        self.intraday    = IntradayStructure()
        self.liq_tracker = SessionLiquidityTracker()
        self.range_det   = RangeDetector()
        self.relax_continuation = bool(relax_continuation)

    def _relaxed(self, trigger_type: str) -> bool:
        """True when this trigger is a continuation type and relaxation is on."""
        return (self.relax_continuation
                and trigger_type in DP.STB_CONTINUATION_TRIGGERS)

    @staticmethod
    def _as_utc(moment: Optional[datetime]) -> datetime:
        """Wall clock when `moment` is None, else `moment` normalised to UTC."""
        if moment is None:
            return datetime.now(timezone.utc)
        if moment.tzinfo is None:
            return moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc)

    def check(self,
              symbol:            str,
              trigger_direction: str,
              trigger_type:      str,
              current_price:     float,
              df_m5:             pd.DataFrame,
              df_h1: Optional[pd.DataFrame] = None,
              df_h4: Optional[pd.DataFrame] = None,
              now:   Optional[datetime] = None) -> STBResult:

        if trigger_direction not in ("BULLISH", "BEARISH"):
            return STBResult(allow=False,
                             reason=f"Invalid trigger direction {trigger_direction}")

        # `now` is injected so the simulator can replay a historical bar's
        # timestamp. Left to the wall clock the session-liquidity tracker would
        # compare May bars against today's date, `recent_sweep` would never
        # fire, and the backtest would run a different gate from the live one.
        now = self._as_utc(now)

        # ── Layer 0 — Range whipsaw protection ──────────────────────────────
        # In a tight intraday range, SWEEP_REJECTION fires on internal noise
        # and fades the wrong side of S/R. Block unless price is genuinely
        # at the range extreme matching the trigger direction:
        #   BULLISH (buy)  → only at lower extreme (≤ 25% into range)
        #   BEARISH (sell) → only at upper extreme (≥ 75% into range)
        # BOS_RETEST is allowed through — it targets range breaks, not fades.
        if trigger_type in DP.STB_RANGE_GUARD_TRIGGERS:
            rng = self.range_det.analyze(df_m5, current_price)
            if rng.in_range:
                pos_pct = rng.position_pct
                if trigger_direction == "BULLISH" and not rng.at_lower_extreme:
                    return STBResult(
                        allow=False,
                        reason=(f"Range whipsaw guard — BULLISH fade rejected "
                                f"at {pos_pct*100:.0f}% of range "
                                f"[{rng.range_low:.4f}-{rng.range_high:.4f}, "
                                f"amp={rng.amplitude:.4f} = "
                                f"{rng.amplitude/rng.atr:.1f}×ATR]. "
                                f"Need ≤25% (near lower extreme)."),
                    )
                if trigger_direction == "BEARISH" and not rng.at_upper_extreme:
                    return STBResult(
                        allow=False,
                        reason=(f"Range whipsaw guard — BEARISH fade rejected "
                                f"at {pos_pct*100:.0f}% of range "
                                f"[{rng.range_low:.4f}-{rng.range_high:.4f}, "
                                f"amp={rng.amplitude:.4f} = "
                                f"{rng.amplitude/rng.atr:.1f}×ATR]. "
                                f"Need ≥75% (near upper extreme)."),
                    )
                # In range AND at the correct extreme — allow but log it
                logger.info(
                    f"STB range-extreme {trigger_direction}: "
                    f"pos={pos_pct*100:.0f}% in "
                    f"[{rng.range_low:.4f}-{rng.range_high:.4f}]"
                )

        # ── Layer 1 — Session Liquidity Awareness ───────────────────────────
        self.liq_tracker.update(symbol, df_m5, now)
        recent = self.liq_tracker.recent_sweep(symbol, now)
        recent_desc = None
        if recent:
            mins_ago = int((now - recent.taken_at).total_seconds() / 60)
            recent_desc = (f"{recent.session_name}_{recent.pool_type}"
                           f"@{recent.level:.4f} taken by "
                           f"{recent.taken_by_session} {mins_ago}m ago")

            # "Don't chase" — block when trigger direction matches sweep
            # direction. For a continuation trigger the sweep IS the
            # displacement being traded, so the rule contradicts the setup's
            # premise rather than protecting it.
            if trigger_direction == recent.sweep_direction:
                if self._relaxed(trigger_type):
                    return STBResult(
                        allow=True, confidence="MEDIUM",
                        short_term_bias=trigger_direction,
                        recent_sweep=recent_desc,
                        reason=(f"Continuation {trigger_type} following fresh "
                                f"sweep ({recent_desc}) — don't-chase relaxed"),
                    )
                return STBResult(
                    allow=False, recent_sweep=recent_desc,
                    reason=(f"Don't chase — trigger {trigger_direction} "
                            f"matches fresh sweep ({recent_desc})"),
                )

            # Premium edge: fading a fresh session sweep with SWEEP_REJECTION
            if trigger_type == "SWEEP_REJECTION":
                return STBResult(
                    allow=True, confidence="HIGH",
                    short_term_bias=trigger_direction,
                    recent_sweep=recent_desc,
                    reason=f"Fading fresh sweep ({recent_desc})",
                )

            # Counter-sweep but not a fade-type trigger — allow at modest confidence
            short_dir = self.intraday.analyze(df_m5)
            conf = "MEDIUM" if short_dir == trigger_direction else "LOW"
            return STBResult(
                allow=True, confidence=conf,
                short_term_bias=short_dir, recent_sweep=recent_desc,
                reason=(f"Counter to {recent_desc}; trigger={trigger_type} "
                        f"short_term={short_dir}"),
            )

        # ── Layer 2 — Intraday M5 Structure ─────────────────────────────────
        short_dir = self.intraday.analyze(df_m5)

        # ── Layer 3 — HTF Tiebreaker (confidence only) ──────────────────────
        htf_trend = self._htf_trend(symbol, df_h1, df_h4)

        if short_dir == trigger_direction:
            conf = "HIGH" if htf_trend == trigger_direction else "MEDIUM"
            return STBResult(
                allow=True, confidence=conf,
                short_term_bias=short_dir, htf_trend=htf_trend,
                reason=(f"Short-term {short_dir} matches trigger; "
                        f"HTF={htf_trend}"),
            )

        if short_dir in ("NEUTRAL", "UNKNOWN"):
            # No clear short-term bias — allow fade-type setups only
            if trigger_type in DP.STB_NEUTRAL_OK_TRIGGERS:
                return STBResult(
                    allow=True, confidence="LOW",
                    short_term_bias=short_dir, htf_trend=htf_trend,
                    reason=(f"Short-term {short_dir}; fade-type trigger "
                            f"{trigger_type} acceptable at LOW conf"),
                )
            if self._relaxed(trigger_type):
                return STBResult(
                    allow=True, confidence="LOW",
                    short_term_bias=short_dir, htf_trend=htf_trend,
                    reason=(f"Short-term {short_dir}; continuation trigger "
                            f"{trigger_type} allowed at LOW — relaxed"),
                )
            return STBResult(
                allow=False, short_term_bias=short_dir, htf_trend=htf_trend,
                reason=(f"Short-term {short_dir}; continuation trigger "
                        f"{trigger_type} requires direction"),
            )

        # short_dir clearly OPPOSES trigger_direction
        # Allow only fade-types (these are *meant* to be counter-trend)
        if trigger_type in DP.STB_COUNTER_TREND_TRIGGERS:
            return STBResult(
                allow=True, confidence="MEDIUM",
                short_term_bias=short_dir, htf_trend=htf_trend,
                reason=(f"Fading short-term {short_dir} with {trigger_type}; "
                        f"HTF={htf_trend}"),
            )
        return STBResult(
            allow=False, short_term_bias=short_dir, htf_trend=htf_trend,
            reason=(f"Trigger {trigger_direction} opposes short-term "
                    f"{short_dir} and {trigger_type} is not a fade type"),
        )

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _htf_trend(self, symbol: str,
                   df_h1: Optional[pd.DataFrame],
                   df_h4: Optional[pd.DataFrame]) -> str:
        """Best-effort HTF trend — confidence tiebreaker only."""
        for df, name in [(df_h1, "H1"), (df_h4, "H4")]:
            if df is None or len(df) < 30:
                continue
            state = self.structure.analyze(df, symbol, name)
            if state.trend in ("BULLISH", "BEARISH"):
                return state.trend
        return "RANGING"
