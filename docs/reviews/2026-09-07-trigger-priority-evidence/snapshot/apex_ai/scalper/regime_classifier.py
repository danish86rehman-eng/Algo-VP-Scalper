"""
Trending vs. ranging — a deterministic regime classifier.

Why this exists
---------------
A volume profile is only a mean-reversion reference while the market is *in
balance*. Measured on this repository's own data on 2026-08-25: an H4 profile
over 42 bars put the POC at 4641.33, within 1 point of spot, while the same
profile over 60 bars put it at 4394.62 — 247 points away, stranded in the base
the market had already left. The 60-bar window straddled a trend leg, so its
histogram was bimodal and its single POC described nothing. Fading a value area
computed across a trend is not a trade, it is an artefact.

So the profile needs a gate that answers one question: *is price currently
rotating, or going somewhere?* This module answers it.

Method, and why not an HMM by default
--------------------------------------
Two deterministic statistics, and a regime is called RANGING only when both
agree — a deliberately conservative bar, because the cost of trading a fade in
a trend is much higher than the cost of skipping a fade in a range.

1. **Lag-1 autocorrelation of returns.** Transcribed from MQL5 art. 17737
   (*Building a Custom Market Regime Detection System*, Part 1), read in full:

       rho(lag) = sum[(x[i] - mean)(x[i+lag] - mean)] / sum[(x[i] - mean)^2]

   with its published defaults — lookback 100, smoothing 10, trend threshold
   **0.2**, volatility threshold **1.5**. Positive autocorrelation means moves
   persist (trend); near zero means they do not (chop).

2. **Kaufman Efficiency Ratio.** Net displacement divided by total path length
   over the same window:

       ER = |close[n] - close[0]| / sum |close[i] - close[i-1]|

   ER near 1 is a straight line; ER near 0 is a market covering the same ground
   repeatedly. It is the direct measure of the thing being asked about, it has
   no fitted parameters, and it cannot be gamed by a series that oscillates
   with persistent sign.

The two statistics can disagree hard, and `combine` decides who wins. Measured
here: a *smooth* oscillation — 100 + 2*sin(x/5) — reports rho = **+0.971** and
ER = **0.051**. Autocorrelation sees a market whose moves persist, because
inside each half-cycle they do; the efficiency ratio sees a market that ends
where it began. Both are right about different things, and a clean swing range
is exactly the case where they part company.

  combine="ANY"  (default) trending if *either* fires. RANGING therefore needs
                 both statistics to agree the market is going nowhere. The
                 smooth sine above is called TRENDING under this rule.
  combine="BOTH" trending only if both fire, so RANGING is the default reading.

ANY is the default because the gate *abstains* outside RANGING rather than
blocking, so a false "trending" costs a missed veto while a false "ranging"
costs a fade taken into a trend — the more expensive error. Real data sits
nowhere near the boundary: XAUUSD H1 measured rho = +0.112 and M15 rho =
-0.083 on 2026-08-25, both far below the 0.2 threshold. The knob exists so the
choice can be fold-tested rather than assumed.

Art. 17737 and its Part 2 (art. 17781) publish **no win rate, profit factor,
trade count or date range**; Part 2 states its own optimisation "introduces a
risk of overfitting" and that results are "not a definitive proof of future
profitability". Under §13.7 both are design evidence only. The thresholds below
are therefore starting points to be fold-tested, not settled values.

**On HMMs.** `hmmlearn` is installed and an HMM backend is implemented in
`hmm_backend.py`, opt-in and off by default. It is not the default for three
reasons that are properties of the method rather than opinions about it:

  * Expectation-maximisation converges to a local optimum. MQL5 art. 17917
    reports that its variational model "required several training restarts",
    and publishes no performance numbers of any kind.
  * Hidden states carry no inherent meaning — state 0 may be "trend" in one fit
    and "range" in the next. Any mapping from state index to regime is itself
    fitted, and must be re-derived on every refit.
  * Invariant #2 requires the simulator to run the live decision path. A model
    refitted on live data classifies differently from one refitted on the
    simulator's slice, so parity has to be manufactured by freezing the fit —
    at which point the adaptivity that motivated the HMM is gone.

The deterministic path has none of these properties: same input, same answer,
in both processes, forever. The HMM is kept measurable so the question can be
settled by folds rather than by argument.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

# Regime labels. Deliberately distinct from `intelligence/regime_engine.py`'s
# ICT vocabulary (EXPANSION / MANIPULATION / ROTATION / TRANSITION) — that
# engine answers a different question and its labels are consumed by the
# consultation gate. Mixing the two vocabularies would make the whitelist in
# decision_params ambiguous about which classifier it refers to.
REGIME_TRENDING_UP = "TRENDING_UP"
REGIME_TRENDING_DOWN = "TRENDING_DOWN"
REGIME_RANGING = "RANGING"
REGIME_VOLATILE = "VOLATILE"
REGIME_UNKNOWN = "UNKNOWN"

TRENDING = frozenset({REGIME_TRENDING_UP, REGIME_TRENDING_DOWN})


@dataclass(frozen=True)
class RegimeRead:
    regime: str
    autocorr: float = 0.0
    efficiency_ratio: float = 0.0
    volatility: float = 0.0
    vol_ratio: float = 0.0
    bars_used: int = 0
    reason: str = ""

    @property
    def is_ranging(self) -> bool:
        return self.regime == REGIME_RANGING

    @property
    def is_trending(self) -> bool:
        return self.regime in TRENDING

    def summary(self) -> str:
        return (f"{self.regime} (rho={self.autocorr:+.3f} "
                f"ER={self.efficiency_ratio:.3f} "
                f"volratio={self.vol_ratio:.2f})")


def autocorrelation(values: np.ndarray, lag: int = 1) -> float:
    """
    Lag-k autocorrelation, per the formula in art. 17737.

    Returns 0.0 — "no persistence detected" — when the series is too short or
    has zero variance, which is the honest reading of a flat series and keeps
    the caller from having to special-case a None.
    """
    n = len(values)
    if n <= lag + 1:
        return 0.0
    mean = float(np.mean(values))
    dev = values - mean
    denom = float(np.sum(dev * dev))
    if denom <= 0.0:
        return 0.0
    numer = float(np.sum(dev[:-lag] * dev[lag:]))
    return numer / denom


def efficiency_ratio(closes: np.ndarray) -> float:
    """
    Kaufman Efficiency Ratio: net displacement over total path length.

    1.0 is a straight line, 0.0 is a market that ends where it started having
    travelled to get there.
    """
    if len(closes) < 2:
        return 0.0
    path = float(np.sum(np.abs(np.diff(closes))))
    if path <= 0.0:
        return 0.0
    return float(abs(closes[-1] - closes[0])) / path


class RegimeClassifier:
    """
    Pure classifier: consumes a frame of completed bars, returns a reading.

    Fetching the frame — and guaranteeing its last row is a completed bar —
    belongs to the caller, so the live agent and the simulator feed it from
    their own data paths and still run identical arithmetic.
    """

    def __init__(self,
                 lookback: int = 100,
                 smoothing: int = 10,
                 trend_threshold: float = 0.2,
                 vol_threshold: float = 1.5,
                 er_threshold: float = 0.35,
                 vol_window: int = 20,
                 combine: str = "ANY"):
        if lookback < 20:
            # Art. 17737 sets 20 as the minimum for statistical significance.
            raise ValueError(f"lookback must be >= 20, got {lookback}")
        if combine not in ("ANY", "BOTH"):
            raise ValueError(f"combine must be ANY or BOTH, got {combine!r}")
        self.combine = combine
        self.lookback = int(lookback)
        self.smoothing = int(smoothing)
        self.trend_threshold = float(trend_threshold)
        self.vol_threshold = float(vol_threshold)
        self.er_threshold = float(er_threshold)
        self.vol_window = int(vol_window)

    def classify(self, df: pd.DataFrame) -> RegimeRead:
        if df is None or "close" not in df.columns:
            return RegimeRead(REGIME_UNKNOWN, reason="No close series supplied")
        closes_all = df["close"].to_numpy(dtype=np.float64)
        if len(closes_all) < self.lookback:
            return RegimeRead(
                REGIME_UNKNOWN, bars_used=len(closes_all),
                reason=(f"Insufficient history: {len(closes_all)} bars, "
                        f"need {self.lookback}"))

        closes = closes_all[-self.lookback:]
        if np.any(~np.isfinite(closes)) or np.any(closes <= 0):
            return RegimeRead(REGIME_UNKNOWN, reason="Non-finite close values")

        # Percentage returns, as art. 17737 defines them.
        rets = np.diff(closes) / closes[:-1] * 100.0
        rho = autocorrelation(rets, lag=1)
        er = efficiency_ratio(closes)

        # Volatility: rolling standard deviation of returns, latest vs. the
        # average of the window. Art. 17737's hierarchy puts this test first.
        if len(rets) >= self.vol_window * 2:
            roll = pd.Series(rets).rolling(self.vol_window).std().dropna()
            latest_vol = float(roll.iloc[-1])
            avg_vol = float(roll.mean())
        else:
            latest_vol = float(np.std(rets, ddof=1)) if len(rets) > 1 else 0.0
            avg_vol = latest_vol
        vol_ratio = (latest_vol / avg_vol) if avg_vol > 0 else 1.0

        common = dict(autocorr=rho, efficiency_ratio=er,
                      volatility=latest_vol, vol_ratio=vol_ratio,
                      bars_used=self.lookback)

        # Hierarchy, per art. 17737: volatility first, then trend, else range.
        if vol_ratio > self.vol_threshold:
            return RegimeRead(
                REGIME_VOLATILE, **common,
                reason=(f"Volatility {latest_vol:.4f} is {vol_ratio:.2f}x the "
                        f"window average, above the {self.vol_threshold:.2f}x "
                        f"threshold"))

        hot_rho = abs(rho) > self.trend_threshold
        hot_er = er > self.er_threshold
        trending = (hot_rho and hot_er) if self.combine == "BOTH" else (hot_rho or hot_er)
        if trending:
            # Direction from the smoothed change, as art. 17737 does, rather
            # than from the sign of rho — autocorrelation measures persistence,
            # not direction, and a persistent downtrend has positive rho.
            k = max(1, min(self.smoothing, len(closes) // 2))
            recent = float(np.mean(closes[-k:]))
            earlier = float(np.mean(closes[:k]))
            up = recent >= earlier
            why = []
            if abs(rho) > self.trend_threshold:
                why.append(f"|rho|={abs(rho):.3f} > {self.trend_threshold:.2f}")
            if er > self.er_threshold:
                why.append(f"ER={er:.3f} > {self.er_threshold:.2f}")
            return RegimeRead(
                REGIME_TRENDING_UP if up else REGIME_TRENDING_DOWN, **common,
                reason="Trending: " + " and ".join(why))

        return RegimeRead(
            REGIME_RANGING, **common,
            reason=(f"Ranging: |rho|={abs(rho):.3f} <= "
                    f"{self.trend_threshold:.2f} and ER={er:.3f} <= "
                    f"{self.er_threshold:.2f}, volatility {vol_ratio:.2f}x "
                    f"average"))


def classify_regime(df: pd.DataFrame,
                    lookback: int = 100,
                    smoothing: int = 10,
                    trend_threshold: float = 0.2,
                    vol_threshold: float = 1.5,
                    er_threshold: float = 0.35) -> RegimeRead:
    """Module-level convenience wrapper, so callers need not hold an instance."""
    return RegimeClassifier(
        lookback=lookback, smoothing=smoothing,
        trend_threshold=trend_threshold, vol_threshold=vol_threshold,
        er_threshold=er_threshold).classify(df)
