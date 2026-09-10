"""
APEX AI — Simulation Engine (v10)
Pre-trade: simulate outcome probability before committing capital.
If weak → reject trade.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import numpy as np
from datetime import datetime, timezone

from core.execution_models import TradeSetup
from intelligence.probabilistic_engine import ProbabilisticBias


@dataclass
class SimulationResult:
    approved: bool
    win_probability: float      # 0.0 – 1.0
    expected_value: float       # Expected $ per $ risked
    worst_case_loss: float      # Worst percentile outcome (in $ risk units)
    best_case_gain: float
    median_outcome: float
    confidence_interval: tuple  # (5th percentile, 95th percentile)
    reason: str = ""
    timestamp: Optional[datetime] = None

    def __repr__(self):
        return (f"Sim({'✅' if self.approved else '❌'} WinProb={self.win_probability:.1%} "
                f"EV={self.expected_value:.2f})")


class SimulationEngine:
    """
    APEX Simulation Engine — Monte Carlo simulation of trade outcomes.
    Uses probabilistic bias + setup RR to estimate expected value.
    If EV is negative or win prob too low → reject.
    """

    def __init__(self, monte_carlo_runs: int = 500, min_win_prob: float = 0.52):
        self.monte_carlo_runs = monte_carlo_runs
        self.min_win_prob = min_win_prob

    def simulate(self, setup: TradeSetup, bias: ProbabilisticBias,
                 dollar_risk: float = 100.0) -> SimulationResult:
        now = datetime.now(timezone.utc)
        rr  = setup.risk_reward

        # Base win probability from probabilistic engine
        if setup.direction == "BUY":
            base_win_prob = bias.bullish_pct / 100
        else:
            base_win_prob = bias.bearish_pct / 100

        # Blend with setup confidence
        blended_win_prob = (base_win_prob * 0.6 + setup.confidence * 0.4)
        blended_win_prob = float(np.clip(blended_win_prob, 0.0, 1.0))

        # Monte Carlo
        results = []
        rng = np.random.default_rng(42)
        for _ in range(self.monte_carlo_runs):
            # Add noise to win probability (±10%)
            noisy_prob = float(np.clip(blended_win_prob + rng.normal(0, 0.05), 0.01, 0.99))
            outcome = rng.random()
            if outcome < noisy_prob:
                # Win: get RR × risk
                pnl = dollar_risk * rr
            else:
                # Loss: lose risk amount (partial loss via SL)
                pnl = -dollar_risk * rng.uniform(0.8, 1.0)
            results.append(pnl)

        results_arr = np.array(results)
        ev = float(np.mean(results_arr))
        worst = float(np.percentile(results_arr, 5))
        best  = float(np.percentile(results_arr, 95))
        med   = float(np.median(results_arr))
        ci    = (float(np.percentile(results_arr, 10)),
                 float(np.percentile(results_arr, 90)))

        approved = (blended_win_prob >= self.min_win_prob and ev > 0)
        reason = (f"WinProb={blended_win_prob:.1%} (min={self.min_win_prob:.1%}), "
                  f"EV={ev:.2f}, RR={rr:.1f}")

        return SimulationResult(
            approved=approved, win_probability=round(blended_win_prob, 3),
            expected_value=round(ev, 2), worst_case_loss=round(worst, 2),
            best_case_gain=round(best, 2), median_outcome=round(med, 2),
            confidence_interval=ci, reason=reason, timestamp=now)
