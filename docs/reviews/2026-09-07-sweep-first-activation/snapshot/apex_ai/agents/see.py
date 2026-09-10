"""
APEX AI — SEE: Strategy Evolution Engine (v6-v7)
Reads adaptive memory, strengthens working models, weakens failing ones.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List
from agents.agent_base import BaseAgent, AgentVote
from intelligence.adaptive_memory import AdaptiveMemory, StrategyPerformance


@dataclass
class SEEContext:
    symbol: str
    proposed_model: str     # 'RETURN' | 'CONTINUATION'
    memory: AdaptiveMemory


class StrategyEvolutionEngine(BaseAgent):
    """
    SEE — determines if the proposed strategy model should be trusted
    based on its recent performance history.
    """

    def __init__(self):
        super().__init__("SEE")

    def analyze(self, context: SEEContext) -> AgentVote:
        perfs = context.memory.get_performance(
            symbol=context.symbol, model_type=context.proposed_model, lookback=15)

        if not perfs:
            # No history → trust with moderate confidence
            return self._make_vote("NEUTRAL", 0.55,
                f"No history for {context.symbol}/{context.proposed_model} — neutral trust")

        perf = perfs[0]

        if perf.status == "RETIRED":
            return self._make_vote("ABSTAIN", 0.9,
                f"Strategy {context.proposed_model} on {context.symbol} is RETIRED. "
                f"WR={perf.win_rate:.0%} over {perf.total_trades} trades.")

        if perf.status == "DEGRADED":
            return self._make_vote("NEUTRAL", 0.4,
                f"Strategy DEGRADED: WR={perf.win_rate:.0%}, "
                f"Sharpe={perf.sharpe_estimate:.2f}. Reduce allocation.")

        if perf.win_rate >= 0.60 and perf.sharpe_estimate > 0.5:
            conf = min(1.0, 0.6 + perf.win_rate * 0.3 + perf.sharpe_estimate * 0.1)
            return self._make_vote("NEUTRAL", conf,
                f"Strategy STRONG: WR={perf.win_rate:.0%}, "
                f"Sharpe={perf.sharpe_estimate:.2f}, PnL={perf.total_pnl:.2f}")

        # Adequate performance
        return self._make_vote("NEUTRAL", 0.5 + perf.win_rate * 0.2,
            f"Strategy OK: WR={perf.win_rate:.0%} over {perf.total_trades} trades.")
