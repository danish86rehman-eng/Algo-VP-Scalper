"""
Regime-direction gate — refuse to fade a trend.

The rule
--------
Read the regime on H1. If it is trending, veto a trigger pointing against it:

    TRENDING_DOWN  ->  BULLISH triggers vetoed
    TRENDING_UP    ->  BEARISH triggers vetoed   (SYMMETRIC mode only)

RANGING and VOLATILE are not trends, so nothing is vetoed there. This is a veto
layer like `ema_filter.py` and `vp_gate.py`: it never creates a trade.

Where the hypothesis came from, and why that matters
----------------------------------------------------
Attribution of the 280-trade baseline book (docs/RESEARCH_NOTES.md §12.6) found
exactly one materially negative regime x direction cell:

    TRENDING_DOWN / BULLISH   8 trades, 37.5% WR, -$214.91, PF 0.35

Every other cell was positive. It is mechanically coherent — a long into a
downtrend — and it is the same shape as the live loss of 2026-08-25 16:45 UTC.

It is also **n=8, selected in-sample**, which is why this module exists as a
switch rather than as a default. §13.10: a diagnosis points research at a
defect, it never authorises a change. A rule read off the same data it will be
measured on is worth nothing; the measurement has to happen somewhere the
hypothesis has never been.

Why SYMMETRIC is the honest default mode
-----------------------------------------
The observed cell is one-sided: longs into downtrends lost, while
TRENDING_UP / BEARISH made +$124.74 at PF 1.73 over 5 trades. Vetoing only the
side that happened to lose is fitting the direction of the sample — with n=8
against n=5, the asymmetry is indistinguishable from noise. `SYMMETRIC` states
the general rule ("do not fade a trend") and is the version that can be wrong
in a way that shows up. `COUNTER_TREND_LONGS` reproduces the literal
observation and is kept so the two can be compared rather than argued about.

Note the tension with §13.8: the EMA band — also a trend filter — was rejected
because 276 of 280 trades come from SWEEP_REJECTION, which fades by
construction, and a trend filter vetoes a fade by definition. This gate is a
weaker version of the same idea: it only vetoes when the classifier calls an
actual trend, rather than whenever price sits on the wrong side of a band. If
it fails the same way, that is confirmation the book cannot tolerate any trend
filter, which is itself worth knowing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from scalper.regime_classifier import (
    REGIME_TRENDING_DOWN, REGIME_TRENDING_UP, REGIME_UNKNOWN,
    RegimeClassifier,
)

#: Veto BULLISH triggers in a downtrend only — the literal L-006 observation.
MODE_COUNTER_TREND_LONGS = "COUNTER_TREND_LONGS"
#: Veto any trigger that opposes a classified trend, both directions.
MODE_SYMMETRIC = "SYMMETRIC"

MODES = (MODE_SYMMETRIC, MODE_COUNTER_TREND_LONGS)


@dataclass(frozen=True)
class RegimeDirectionResult:
    allow: bool
    regime: str = REGIME_UNKNOWN
    reason: str = ""
    abstained: bool = False


class RegimeDirectionGate:
    """
    Pure gate: consumes a frame of completed H1 bars, returns a decision.

    Fetching the frame — and guaranteeing its last row is a completed bar —
    belongs to the caller, so the live agent and the simulator feed it from
    their own data paths and run identical arithmetic.
    """

    def __init__(self, mode: str,
                 regime_classifier: Optional[RegimeClassifier] = None):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
        self.mode = mode
        self.regime = regime_classifier or RegimeClassifier()

    def check(self, trigger_direction: str,
              df_regime_closed: pd.DataFrame) -> RegimeDirectionResult:
        if trigger_direction not in ("BULLISH", "BEARISH"):
            return RegimeDirectionResult(
                allow=True, abstained=True,
                reason=f"Unknown direction {trigger_direction!r}; gate abstains")
        if df_regime_closed is None or len(df_regime_closed) == 0:
            return RegimeDirectionResult(
                allow=True, abstained=True,
                reason="No regime frame supplied; gate abstains")

        read = self.regime.classify(df_regime_closed)
        if read.regime == REGIME_UNKNOWN:
            return RegimeDirectionResult(
                allow=True, abstained=True, regime=read.regime,
                reason=f"Regime unreadable ({read.reason}); gate abstains")

        # Only a classified trend can be faded. RANGING and VOLATILE pass.
        if not read.is_trending:
            return RegimeDirectionResult(
                allow=True, regime=read.regime,
                reason=f"{read.regime} is not a trend — nothing to fade")

        opposes = (
            (read.regime == REGIME_TRENDING_DOWN and trigger_direction == "BULLISH")
            or (read.regime == REGIME_TRENDING_UP and trigger_direction == "BEARISH")
        )
        if not opposes:
            return RegimeDirectionResult(
                allow=True, regime=read.regime,
                reason=f"{trigger_direction} agrees with {read.regime}")

        if (self.mode == MODE_COUNTER_TREND_LONGS
                and trigger_direction != "BULLISH"):
            return RegimeDirectionResult(
                allow=True, regime=read.regime,
                reason=(f"{trigger_direction} opposes {read.regime} but this "
                        f"mode only vetoes longs"))

        return RegimeDirectionResult(
            allow=False, regime=read.regime,
            reason=(f"{trigger_direction} vetoed — H1 regime is {read.regime} "
                    f"({read.summary()})"))
