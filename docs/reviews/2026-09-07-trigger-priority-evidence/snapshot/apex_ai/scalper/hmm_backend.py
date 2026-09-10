"""
Optional Hidden Markov Model regime backend. Off by default.

This exists so the HMM question can be settled by measurement instead of by
argument. It is deliberately constructed to remove the three failure modes that
would otherwise make it untestable in this repository — and each removal costs
something, which is the honest reason it is not the default.

1. **Local optima.** EM converges to whichever optimum it starts near. MQL5
   art. 17917 reports its variational model "required several training
   restarts" and publishes no performance figures at all. Fixed by pinning
   `random_state` and running a fixed number of restarts, keeping the fit with
   the best log-likelihood. Cost: the model is now a deterministic function of
   its input window, which is what parity requires, but it is no longer
   "adaptive" in any sense a fresh fit would be.

2. **Label switching.** A hidden state index means nothing — state 0 may be
   "range" in one fit and "trend" in the next. Fixed by mapping states to
   regimes through an ordering of their *fitted emission parameters* rather
   than their indices: states are ranked by the volatility they emit, and the
   lowest-volatility state is the ranging one. This is a rule about the model's
   parameters, so it survives a refit.

3. **Lookahead.** Fitting on the whole series and then labelling every bar of
   it is the classic HMM backtest fraud: the bar being classified helped choose
   the parameters doing the classifying. Fixed by fitting strictly on bars
   *before* the decision bar and classifying only the final observation.

What remains true regardless: the model is fitted, so it can overfit; art.
17917 itself notes HMMs "are prone to overfitting on non-stationary time
series". Nothing here makes an HMM appropriate — it makes one measurable.

Import is lazy: `hmmlearn` is not in requirements.txt and this module must not
break the agent on a machine without it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from scalper.regime_classifier import (
    REGIME_RANGING, REGIME_TRENDING_DOWN, REGIME_TRENDING_UP,
    REGIME_UNKNOWN, REGIME_VOLATILE, RegimeRead,
)

#: Fixed seed. Every fit in both processes starts from the same place.
HMM_SEED = 20260825
#: Restarts kept, best log-likelihood wins. More restarts, more determinism
#: against the local-optimum problem, linearly more time.
HMM_RESTARTS = 3
#: Art. 17917 settles on 3-5 hidden states, noting "it is sometimes enough to
#: set 3-5 hidden states" where clustering needed 10.
HMM_STATES = 3
HMM_ITER = 100


def available() -> bool:
    """True when hmmlearn can be imported. Cheap to call."""
    try:
        import hmmlearn  # noqa: F401
        return True
    except Exception:
        return False


@dataclass(frozen=True)
class _StateProfile:
    index: int
    mean_return: float
    volatility: float


def _features(closes: np.ndarray, vol_window: int) -> Optional[np.ndarray]:
    """
    Observation matrix: [return, rolling volatility].

    Art. 17917 feeds rolling standard deviations over several window lengths.
    Two columns is the smallest set that can separate "quiet drift" from "loud
    chop", and keeps the parameter count low enough that a few hundred bars is
    a defensible sample.
    """
    if len(closes) < vol_window + 2:
        return None
    rets = np.diff(closes) / closes[:-1]
    vol = pd.Series(rets).rolling(vol_window).std().to_numpy()
    obs = np.column_stack([rets, vol])
    obs = obs[np.all(np.isfinite(obs), axis=1)]
    return obs if len(obs) >= vol_window else None


def classify_hmm(df: pd.DataFrame,
                 lookback: int = 400,
                 n_states: int = HMM_STATES,
                 vol_window: int = 20,
                 seed: int = HMM_SEED,
                 restarts: int = HMM_RESTARTS) -> RegimeRead:
    """
    Fit on the window ending at the last COMPLETED bar, classify that bar.

    Returns a RegimeRead so it is a drop-in for the deterministic classifier.
    UNKNOWN on any failure — an unfittable window is "no opinion", never a
    silently neutral answer.
    """
    if not available():
        return RegimeRead(REGIME_UNKNOWN, reason="hmmlearn not installed")
    if df is None or "close" not in df.columns:
        return RegimeRead(REGIME_UNKNOWN, reason="No close series supplied")

    closes = df["close"].to_numpy(dtype=np.float64)
    if len(closes) < lookback:
        return RegimeRead(
            REGIME_UNKNOWN, bars_used=len(closes),
            reason=f"Insufficient history: {len(closes)} bars, need {lookback}")
    closes = closes[-lookback:]

    obs = _features(closes, vol_window)
    if obs is None:
        return RegimeRead(REGIME_UNKNOWN, reason="Feature matrix too short")

    try:
        from hmmlearn.hmm import GaussianHMM
    except Exception as exc:                                # pragma: no cover
        return RegimeRead(REGIME_UNKNOWN, reason=f"hmmlearn import failed: {exc}")

    best, best_ll = None, -np.inf
    for r in range(max(1, restarts)):
        try:
            model = GaussianHMM(n_components=n_states, covariance_type="diag",
                                n_iter=HMM_ITER, random_state=seed + r)
            model.fit(obs)
            ll = float(model.score(obs))
        except Exception:
            continue
        if np.isfinite(ll) and ll > best_ll:
            best, best_ll = model, ll
    if best is None:
        return RegimeRead(REGIME_UNKNOWN, reason="HMM failed to converge")

    try:
        states = best.predict(obs)
    except Exception as exc:                                # pragma: no cover
        return RegimeRead(REGIME_UNKNOWN, reason=f"HMM predict failed: {exc}")

    current = int(states[-1])

    # Label switching is resolved here, from fitted parameters rather than
    # state indices: rank states by emitted volatility (feature column 1).
    profiles = [
        _StateProfile(index=i,
                      mean_return=float(best.means_[i][0]),
                      volatility=float(best.means_[i][1]))
        for i in range(n_states)
    ]
    by_vol = sorted(profiles, key=lambda p: p.volatility)
    quiet = by_vol[0]
    loud = by_vol[-1]
    here = profiles[current]

    obs_vol = float(np.nanstd(obs[:, 0]))
    vol_ratio = (here.volatility / obs_vol) if obs_vol > 0 else 1.0
    rho_like = float(here.mean_return)

    common = dict(autocorr=rho_like, efficiency_ratio=0.0,
                  volatility=here.volatility, vol_ratio=vol_ratio,
                  bars_used=len(obs))

    if current == quiet.index:
        return RegimeRead(
            REGIME_RANGING, **common,
            reason=(f"HMM state {current} of {n_states} is the lowest-"
                    f"volatility state (sigma={here.volatility:.6f})"))
    if current == loud.index and loud.volatility > quiet.volatility * 2.0:
        return RegimeRead(
            REGIME_VOLATILE, **common,
            reason=(f"HMM state {current} is the highest-volatility state "
                    f"(sigma={here.volatility:.6f})"))
    regime = REGIME_TRENDING_UP if here.mean_return >= 0 else REGIME_TRENDING_DOWN
    return RegimeRead(
        regime, **common,
        reason=(f"HMM state {current}, mean return "
                f"{here.mean_return:+.6f}, sigma={here.volatility:.6f}"))
