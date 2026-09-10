"""
APEX AI — EA: Execution Agent
Final execution gatekeeper — sends orders only after all gates pass.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, List
from agents.agent_base import BaseAgent, AgentVote
from core.execution_models import TradeSetup
from market.mt5_connector import MT5Connector
from intelligence.risk_engine import RiskAllocation
import logging

logger = logging.getLogger("APEX.EA")


@dataclass
class EAContext:
    setup: TradeSetup
    risk: RiskAllocation
    connector: MT5Connector
    dry_run: bool = False


class ExecutionAgent(BaseAgent):
    """
    EA — validates the final trade setup, calculates execution parameters,
    and sends the order to MT5 if all conditions are met.
    """

    def __init__(self):
        super().__init__("EA")

    def analyze(self, context: EAContext) -> AgentVote:
        setup = context.setup
        risk  = context.risk

        if not risk.approved:
            return self._make_vote("ABSTAIN", 0.9,
                f"Risk not approved: {risk.reason}")

        if risk.lot_size <= 0:
            return self._make_vote("ABSTAIN", 0.9, "Lot size = 0, cannot execute")

        if setup.risk_reward < 2.0:
            return self._make_vote("ABSTAIN", 0.8,
                f"RR={setup.risk_reward:.1f} below minimum 2.0")

        # Validate price levels
        if setup.direction == "BUY":
            if not (setup.stop_loss < setup.entry_price < setup.take_profit):
                return self._make_vote("ABSTAIN", 0.9,
                    f"Invalid BUY levels: SL={setup.stop_loss:.4f} "
                    f"Entry={setup.entry_price:.4f} TP={setup.take_profit:.4f}")
        else:
            if not (setup.take_profit < setup.entry_price < setup.stop_loss):
                return self._make_vote("ABSTAIN", 0.9,
                    f"Invalid SELL levels: TP={setup.take_profit:.4f} "
                    f"Entry={setup.entry_price:.4f} SL={setup.stop_loss:.4f}")

        conf = min(1.0, 0.6 + setup.confidence * 0.3 + risk.risk_pct / 4 * 0.1)
        return self._make_vote("NEUTRAL", conf,
            f"Execution ready: {setup.direction} {setup.symbol} "
            f"@ {setup.entry_price:.4f} lot={risk.lot_size} "
            f"SL={setup.stop_loss:.4f} TP={setup.take_profit:.4f} "
            f"RR={setup.risk_reward:.1f}")

    def execute(self, context: EAContext) -> Optional[dict]:
        """Actually place the order through MT5."""
        setup = context.setup
        risk  = context.risk

        vote = self.analyze(context)
        if vote.vote == "ABSTAIN":
            logger.warning(f"EA blocked execution: {vote.reasoning}")
            return None

        result = context.connector.place_order(
            symbol=setup.symbol,
            order_type=setup.direction,
            volume=risk.lot_size,
            price=setup.entry_price,
            sl=setup.stop_loss,
            tp=setup.take_profit,
            comment=f"APEX {setup.model_type}"
        )

        if result:
            logger.info(f"✅ EA executed: {setup.symbol} {setup.direction} "
                       f"lot={risk.lot_size} ticket={result.get('order')}")
        else:
            logger.error("EA execution failed")

        return result
