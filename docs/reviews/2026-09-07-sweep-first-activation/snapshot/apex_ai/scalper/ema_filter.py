"""
H1 EMA band filter — EMA(18) on High and EMA(18) on Low.

The rule
--------
Two exponential moving averages of the same period are taken on H1: one over
the high series, one over the low series. Because every high is above its own
low, EMA(high) sits above EMA(low) and the pair forms a channel.

    price above BOTH bands  ->  longs permitted, shorts vetoed
    price below BOTH bands  ->  shorts permitted, longs vetoed
    price inside the band   ->  no directional permission, everything vetoed

"Above both" reduces to `price > ema_high` and "below both" to
`price < ema_low`, since the band is ordered — but the checks are written out
in full because the ordering is a property of the data, not a guarantee, and a
degenerate flat series can collapse them.

This is a veto layer, not a signal generator. It never creates a trade; it only
removes trades whose direction disagrees with H1 context.

Closed bars only
----------------
The EMA is computed from completed H1 bars and read at the last of them —
MQL5's shift=1 convention. MQL5 art. 21133, which adds exactly this kind of
trend filter to a liquidity strategy, states that a shift of 1 "ensures the
value comes from a fully closed candle"; art. 20851, which builds the same
EMA-high/EMA-low channel, evaluates once per closed bar. Reading a forming bar
would make the filter repaint: the value that permitted an entry can change
before that bar closes, so the decision could never be reproduced — by a
backtest or by the operator looking at the chart afterwards.

Caller contract: pass a frame whose final row is a COMPLETED H1 bar.

Provenance
----------
The EMA recursion matches MetaTrader's `iMA`: the series is seeded with a
simple average of the first `period` values and then advanced with
alpha = 2 / (period + 1). Using pandas' default `ewm` instead would seed
differently and print numbers that disagree with the operator's own chart.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import pandas as pd

from scalper.decision_params import EMA_BAND_PERIOD


@dataclass(frozen=True)
class EMABandResult:
    allow: bool
    #: BULLISH | BEARISH | NEUTRAL — the only direction H1 context permits.
    permitted_direction: str = "NEUTRAL"
    ema_high: float = 0.0
    ema_low: float = 0.0
    price: float = 0.0
    #: ABOVE | BELOW | INSIDE | UNKNOWN
    position: str = "UNKNOWN"
    reason: str = ""


def ema_mt5(values: Sequence[float], period: int) -> Optional[float]:
    """
    Final EMA value over `values`, seeded and advanced the way MetaTrader does.

    Returns None when there is not enough history to seed the average, which
    the caller must treat as "no opinion" rather than as a zero.
    """
    n = len(values)
    if period <= 0 or n < period:
        return None
    ema = sum(values[:period]) / float(period)
    alpha = 2.0 / (period + 1.0)
    for i in range(period, n):
        ema = values[i] * alpha + ema * (1.0 - alpha)
    return float(ema)


class EMABandFilter:
    """
    H1 EMA(18)-high / EMA(18)-low directional gate.

    Pure: it consumes a dataframe and a price and returns a decision. Fetching
    the H1 frame — and guaranteeing its last row is a completed bar — belongs
    to the caller, so the live agent and the simulator can feed it from their
    own data paths and still run identical arithmetic.
    """

    #: TREND — the specified rule: above the band permits longs.
    #: FADE  — the inversion: above the band permits shorts.
    #:
    #: FADE exists because it is the arithmetically obvious question to ask of
    #: this particular book, not because it is recommended. Roughly 99% of the
    #: scalper's trades come from SWEEP_REJECTION, which is a counter-trend
    #: fade, so a trend band vetoes the trigger's own premise. Measuring the
    #: inversion is how that suspicion is tested rather than argued about.
    MODES = ("TREND", "FADE")

    def __init__(self, period: int = EMA_BAND_PERIOD, mode: str = "TREND"):
        self.period = int(period)
        if mode not in self.MODES:
            raise ValueError(f"mode must be one of {self.MODES}, got {mode!r}")
        self.mode = mode

    def check(self, trigger_direction: str, price: float,
              df_h1_closed: pd.DataFrame) -> EMABandResult:
        if df_h1_closed is None or len(df_h1_closed) < self.period:
            have = 0 if df_h1_closed is None else len(df_h1_closed)
            return EMABandResult(
                allow=False,
                reason=f"Insufficient H1 history for EMA{self.period} "
                       f"({have} closed bars, need {self.period})",
            )
        if price <= 0:
            return EMABandResult(allow=False, reason="Invalid reference price")

        highs = df_h1_closed["high"].astype(float).tolist()
        lows = df_h1_closed["low"].astype(float).tolist()
        ema_high = ema_mt5(highs, self.period)
        ema_low = ema_mt5(lows, self.period)
        if ema_high is None or ema_low is None:
            return EMABandResult(allow=False, reason="EMA could not be seeded")

        above_both = price > ema_high and price > ema_low
        below_both = price < ema_high and price < ema_low

        if above_both:
            position = "ABOVE"
            permitted = "BULLISH" if self.mode == "TREND" else "BEARISH"
        elif below_both:
            position = "BELOW"
            permitted = "BEARISH" if self.mode == "TREND" else "BULLISH"
        else:
            position, permitted = "INSIDE", "NEUTRAL"

        common = dict(ema_high=ema_high, ema_low=ema_low,
                      price=price, position=position)

        if permitted == "NEUTRAL":
            return EMABandResult(
                allow=False, permitted_direction="NEUTRAL", **common,
                reason=(f"Price {price:.5f} inside H1 EMA{self.period} band "
                        f"[{ema_low:.5f}, {ema_high:.5f}] — no directional bias"),
            )

        if trigger_direction != permitted:
            return EMABandResult(
                allow=False, permitted_direction=permitted, **common,
                reason=(f"{trigger_direction} vetoed — price {price:.5f} is "
                        f"{position} the H1 EMA{self.period} band "
                        f"[{ema_low:.5f}, {ema_high:.5f}], "
                        f"so only {permitted} is permitted"),
            )

        return EMABandResult(
            allow=True, permitted_direction=permitted, **common,
            reason=(f"OK — price {price:.5f} {position} H1 EMA{self.period} band "
                    f"[{ema_low:.5f}, {ema_high:.5f}]"),
        )
