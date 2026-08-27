"""
HTF Bias Filter — P0 amendment for SA-V2 wrong-side prevention.

Replaces the M15 EMA-10 bias check with a stacked H1+H4 structure filter
and a Premium/Discount zone gate computed over the last H1 dealing range.

Rules enforced (all must pass; failure of any one rejects the trade):
    1. H4 structural trend MUST equal the trigger direction.
    2. H1 structural trend MUST equal the trigger direction.
    3. Longs only when current price sits in the DISCOUNT half of the last
       H1 dealing range. Shorts only when price sits in PREMIUM.
    4. H1 dealing range must exceed MIN_RANGE_ATR_MULT × ATR(H1, 14) so the
       zone classification is meaningful and not noise.

Any RANGING / UNKNOWN trend on either H1 or H4 blocks the trade — SA only
trades aligned tier-1 institutional flow.

The filter is pure: it consumes dataframes and returns a decision. The
caller is responsible for fetching H1 and H4 OHLCV.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Tuple
import pandas as pd

from core.structure_engine import StructureEngine


@dataclass
class HTFBiasResult:
    allow: bool
    h1_trend: str = "UNKNOWN"
    h4_trend: str = "UNKNOWN"
    zone: str = "UNKNOWN"            # PREMIUM | DISCOUNT | EQUILIBRIUM | UNKNOWN
    equilibrium: float = 0.0
    range_high: float = 0.0
    range_low: float = 0.0
    reason: str = ""


class HTFBiasFilter:
    """
    Stacked HTF directional + premium/discount gate for SA-V2.
    Uses StructureEngine on H1 and H4 for trend; H1 dealing range for zone.
    """

    # H1 dealing range must exceed this multiple of H1 ATR(14) to be valid
    MIN_RANGE_ATR_MULT = 1.0

    def __init__(self):
        self.structure = StructureEngine(swing_lookback=5)

    def check(self, symbol: str, trigger_direction: str,
              current_price: float,
              df_h1: pd.DataFrame, df_h4: pd.DataFrame) -> HTFBiasResult:

        if df_h1 is None or df_h4 is None or len(df_h1) < 30 or len(df_h4) < 30:
            return HTFBiasResult(allow=False, reason="Insufficient H1/H4 data")

        if trigger_direction not in ("BULLISH", "BEARISH"):
            return HTFBiasResult(allow=False,
                                 reason=f"Invalid trigger direction {trigger_direction}")

        h1_state = self.structure.analyze(df_h1, symbol, "H1")
        h4_state = self.structure.analyze(df_h4, symbol, "H4")
        h1_trend = h1_state.trend
        h4_trend = h4_state.trend
        want = trigger_direction  # 'BULLISH' or 'BEARISH'

        # Gate 1 — H4 directional alignment
        if h4_trend != want:
            return HTFBiasResult(
                allow=False, h1_trend=h1_trend, h4_trend=h4_trend,
                reason=f"H4 trend {h4_trend} != trigger {want}",
            )

        # Gate 2 — H1 directional alignment
        if h1_trend != want:
            return HTFBiasResult(
                allow=False, h1_trend=h1_trend, h4_trend=h4_trend,
                reason=f"H1 trend {h1_trend} != trigger {want}",
            )

        # Gate 3 — H1 dealing range validity
        rng_hi, rng_lo = self._last_h1_range(h1_state, df_h1)
        if rng_hi is None or rng_lo is None or rng_hi <= rng_lo:
            return HTFBiasResult(
                allow=False, h1_trend=h1_trend, h4_trend=h4_trend,
                reason="No valid H1 dealing range",
            )

        atr_h1 = self._atr(df_h1, 14)
        if atr_h1 > 0 and (rng_hi - rng_lo) < self.MIN_RANGE_ATR_MULT * atr_h1:
            return HTFBiasResult(
                allow=False, h1_trend=h1_trend, h4_trend=h4_trend,
                range_high=rng_hi, range_low=rng_lo,
                reason="H1 dealing range < 1×ATR (noise)",
            )

        # Gate 4 — Premium/Discount zone vs trigger direction
        equilibrium = (rng_hi + rng_lo) / 2.0
        if current_price > equilibrium:
            zone = "PREMIUM"
        elif current_price < equilibrium:
            zone = "DISCOUNT"
        else:
            zone = "EQUILIBRIUM"

        if want == "BULLISH" and zone != "DISCOUNT":
            return HTFBiasResult(
                allow=False, h1_trend=h1_trend, h4_trend=h4_trend,
                zone=zone, equilibrium=equilibrium,
                range_high=rng_hi, range_low=rng_lo,
                reason=f"Long rejected — price in {zone}, need DISCOUNT",
            )
        if want == "BEARISH" and zone != "PREMIUM":
            return HTFBiasResult(
                allow=False, h1_trend=h1_trend, h4_trend=h4_trend,
                zone=zone, equilibrium=equilibrium,
                range_high=rng_hi, range_low=rng_lo,
                reason=f"Short rejected — price in {zone}, need PREMIUM",
            )

        return HTFBiasResult(
            allow=True, h1_trend=h1_trend, h4_trend=h4_trend,
            zone=zone, equilibrium=equilibrium,
            range_high=rng_hi, range_low=rng_lo,
            reason=f"OK | H1+H4={want} | zone={zone}",
        )

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _last_h1_range(self, h1_state, df_h1: pd.DataFrame
                       ) -> Tuple[Optional[float], Optional[float]]:
        """
        Last completed H1 dealing range. Prefer StructureEngine's most recent
        opposite-swing pair; fall back to last 30 H1 bars if structure is sparse.
        """
        hi = h1_state.recent_hh if h1_state.recent_hh else h1_state.recent_lh
        lo = h1_state.recent_ll if h1_state.recent_ll else h1_state.recent_hl
        if hi and lo:
            return float(hi), float(lo)
        if df_h1 is not None and len(df_h1) >= 30:
            tail = df_h1.iloc[-30:]
            return float(tail['high'].max()), float(tail['low'].min())
        return None, None

    @staticmethod
    def _atr(df: pd.DataFrame, period: int = 14) -> float:
        if df is None or len(df) < period + 1:
            return 0.0
        hi = df['high'].values
        lo = df['low'].values
        cl = df['close'].values
        trs = [max(hi[i]-lo[i], abs(hi[i]-cl[i-1]), abs(lo[i]-cl[i-1]))
               for i in range(1, len(df))]
        return float(sum(trs[-period:]) / period) if trs else 0.0
