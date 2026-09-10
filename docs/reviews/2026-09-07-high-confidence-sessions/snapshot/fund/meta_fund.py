"""
APEX AI — Meta-Fund Evolution Engine (v11)
Three specialist sub-funds. Capital flows to the best performer.
Strong strategies replicate, weak strategies die, new ones emerge.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import numpy as np
from datetime import datetime, timezone
import logging

from intelligence.adaptive_memory import AdaptiveMemory, StrategyPerformance

logger = logging.getLogger("APEX.MetaFund")


@dataclass
class SubFund:
    name: str               # 'EXPANSION' | 'MANIPULATION' | 'RANGE'
    speciality: str         # Which regime it trades
    capital_weight: float   # 0.0 – 1.0 (proportion of capital allocated)
    win_rate: float = 0.0
    sharpe: float = 0.0
    total_trades: int = 0
    status: str = "ACTIVE"  # ACTIVE | OBSERVATION | DEGRADED | RETIRED
    last_updated: Optional[datetime] = None

    def __repr__(self):
        return (f"SubFund({self.name} weight={self.capital_weight:.0%} "
                f"WR={self.win_rate:.0%} Sharpe={self.sharpe:.2f} [{self.status}])")


@dataclass
class MetaFundState:
    sub_funds: Dict[str, SubFund] = field(default_factory=dict)
    active_fund: str = "MANIPULATION"      # Which fund is currently dominant
    rebalance_count: int = 0
    total_trades_seen: int = 0
    timestamp: Optional[datetime] = None

    def get_weight(self, fund_name: str) -> float:
        return self.sub_funds.get(fund_name, SubFund(fund_name, "", 1/3)).capital_weight


class MetaFundEngine:
    """
    APEX Meta-Fund Evolution — manages capital allocation across three specialist sub-funds.
    v11: Capital flows to the best performer. Weak funds get starved; strong ones replicate.
    """

    FUND_REGIME_MAP = {
        "EXPANSION":    ["EXPANSION"],
        "MANIPULATION": ["MANIPULATION"],
        "RANGE":        ["ROTATION", "TRANSITION"],
    }

    def __init__(self, memory: AdaptiveMemory,
                 rebalance_interval: int = 10,
                 lookback_trades: int = 20,
                 min_sharpe: float = 0.5):
        self.memory = memory
        self.rebalance_interval = rebalance_interval
        self.lookback_trades = lookback_trades
        self.min_sharpe = min_sharpe

        # Initialize sub-funds with equal weight
        self._state = MetaFundState(
            sub_funds={
                "EXPANSION":    SubFund("EXPANSION",    "CONTINUATION", 1/3),
                "MANIPULATION": SubFund("MANIPULATION", "RETURN",       1/3),
                "RANGE":        SubFund("RANGE",        "MEAN_REVERSION", 1/3),
            },
            timestamp=datetime.now(timezone.utc))

    def get_state(self) -> MetaFundState:
        return self._state

    def get_capital_weight(self, regime: str) -> float:
        """Returns capital allocation weight for the given regime."""
        for fund_name, regimes in self.FUND_REGIME_MAP.items():
            if regime in regimes:
                fund = self._state.sub_funds.get(fund_name)
                return fund.capital_weight if fund else 1/3
        return 1/3

    def update(self, trade_count: int) -> MetaFundState:
        """Update sub-fund performance and rebalance if needed."""
        self._state.total_trades_seen = trade_count

        if trade_count > 0 and trade_count % self.rebalance_interval == 0:
            self._rebalance()

        return self._state

    def _rebalance(self):
        """Rebalance capital weights based on recent performance."""
        logger.info("♻️ Meta-Fund rebalancing...")
        now = datetime.now(timezone.utc)

        # Map fund speciality to model type for memory lookup
        fund_model_map = {
            "EXPANSION":    "CONTINUATION",
            "MANIPULATION": "RETURN",
            "RANGE":        "MEAN_REVERSION",
        }

        scores = {}
        for fund_name, model in fund_model_map.items():
            perfs = self.memory.get_performance(model_type=model, lookback=self.lookback_trades)
            if perfs:
                perf = perfs[0]
                # Score = win_rate * 0.5 + normalized_sharpe * 0.3 + trades_factor * 0.2
                trade_factor = min(1.0, perf.total_trades / 10)
                score = (perf.win_rate * 0.5 + min(1.0, max(0, perf.sharpe_estimate)) * 0.3 +
                         trade_factor * 0.2)
                scores[fund_name] = max(0.01, score)
                fund = self._state.sub_funds[fund_name]
                fund.win_rate     = perf.win_rate
                fund.sharpe       = perf.sharpe_estimate
                fund.total_trades = perf.total_trades
                fund.status       = perf.status
            else:
                scores[fund_name] = 0.33   # Default equal weight if no data

        # Normalize to weights
        total_score = sum(scores.values()) + 1e-10
        for fund_name, score in scores.items():
            weight = score / total_score
            # Clamp: no fund below 10% or above 70%
            weight = max(0.1, min(0.7, weight))
            self._state.sub_funds[fund_name].capital_weight = round(weight, 3)
            self._state.sub_funds[fund_name].last_updated   = now

        # Renormalize after clamping
        total_w = sum(f.capital_weight for f in self._state.sub_funds.values())
        for fund in self._state.sub_funds.values():
            fund.capital_weight = round(fund.capital_weight / total_w, 3)

        # Active fund = highest weight
        self._state.active_fund = max(self._state.sub_funds,
                                       key=lambda k: self._state.sub_funds[k].capital_weight)
        self._state.rebalance_count += 1
        self._state.timestamp = now

        for name, fund in self._state.sub_funds.items():
            logger.info(f"  {name}: {fund.capital_weight:.0%} weight | {fund.status}")
