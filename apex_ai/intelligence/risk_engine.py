"""
APEX AI — Risk Allocation Engine (v5)
Dynamic risk % based on clarity score. High clarity → trade; Low → stay out.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import math
from datetime import datetime, timezone

from intelligence.probabilistic_engine import ProbabilisticBias
from intelligence.regime_engine import RegimeState
from market.session_engine import SessionState


@dataclass
class RiskAllocation:
    risk_pct: float             # % of account to risk on this trade
    lot_size: float             # Calculated lot size
    sl_pips: float              # Stop loss in pips/points
    sl_price: float             # SL as price level
    tp_price: float             # TP as price level
    dollar_risk: float          # $ amount at risk
    approved: bool = True
    reason: str = ""
    timestamp: Optional[datetime] = None

    def __repr__(self):
        return (f"Risk({self.risk_pct:.2f}% | lot={self.lot_size:.2f} | "
                f"$risk={self.dollar_risk:.2f} | {'✅' if self.approved else '❌'})")


class RiskEngine:
    """
    APEX Risk Engine — sizes positions based on clarity, regime, and governance mode.
    High clarity → up to max_risk%
    Medium → half risk
    Low → no trade
    """

    def __init__(self, high_risk_pct: float = 2.0, medium_risk_pct: float = 1.0,
                 low_risk_pct: float = 0.0, min_rr: float = 2.0,
                 single_trade_dollar_cap_pct: float = 5.0):
        """
        Args:
            high_risk_pct / medium_risk_pct / low_risk_pct : Base risk per
                clarity tier (from config["risk"]).
            min_rr : Minimum reward/risk required for the setup.
            single_trade_dollar_cap_pct : Absolute hard cap — a single trade
                can never risk more than this percent of current account
                equity in dollar terms, regardless of what the clarity/regime
                multipliers produce. Defends small accounts from over-sizing
                when governance limits are wide.
        """
        self.high_risk_pct   = high_risk_pct
        self.medium_risk_pct = medium_risk_pct
        self.low_risk_pct    = low_risk_pct
        self.min_rr = min_rr
        self.single_trade_dollar_cap_pct = float(single_trade_dollar_cap_pct)

    def calculate(self, account_balance: float, entry_price: float,
                  stop_loss: float, take_profit: float,
                  point_value: float, contract_size: float,
                  bias: ProbabilisticBias, regime: RegimeState,
                  session: SessionState, governance_mode: str = "BALANCED") -> RiskAllocation:

        now = datetime.now(timezone.utc)

        # RR check
        sl_dist = abs(entry_price - stop_loss)
        tp_dist = abs(take_profit - entry_price)
        rr = tp_dist / sl_dist if sl_dist > 0 else 0
        if rr < self.min_rr:
            return RiskAllocation(
                risk_pct=0, lot_size=0, sl_pips=0,
                sl_price=stop_loss, tp_price=take_profit, dollar_risk=0,
                approved=False, reason=f"RR={rr:.1f} below minimum {self.min_rr}",
                timestamp=now)

        # Base risk % from clarity
        base_risk = {
            "HIGH":   self.high_risk_pct,
            "MEDIUM": self.medium_risk_pct,
            "LOW":    self.low_risk_pct,
        }.get(bias.clarity, 0.0)

        if base_risk == 0:
            return RiskAllocation(
                risk_pct=0, lot_size=0, sl_pips=0,
                sl_price=stop_loss, tp_price=take_profit, dollar_risk=0,
                approved=False, reason=f"Clarity={bias.clarity} — no trade",
                timestamp=now)

        # Governance mode multiplier
        gov_mult = {"AGGRESSIVE": 1.2, "BALANCED": 1.0,
                    "DEFENSIVE": 0.6, "PRESERVATION": 0.2}.get(governance_mode, 1.0)

        # Session multiplier
        sess_mult = session.session_weight

        # Regime multiplier
        reg_mult = 1.0
        if regime.regime == "MANIPULATION":
            reg_mult = 1.1
        elif regime.regime == "ROTATION":
            reg_mult = 0.5
        elif regime.regime == "TRANSITION":
            reg_mult = 0.4

        # Confidence multiplier
        conf_mult = 0.7 + bias.confidence * 0.5

        final_risk = min(self.high_risk_pct * 1.5,
                         base_risk * gov_mult * sess_mult * reg_mult * conf_mult)
        final_risk = round(final_risk, 2)

        # Absolute dollar safety cap — single trade cannot risk more than
        # single_trade_dollar_cap_pct of account equity, no matter what the
        # percent calc produced. Apply by clamping final_risk downward.
        dollar_cap_pct = self.single_trade_dollar_cap_pct
        if final_risk > dollar_cap_pct:
            final_risk = round(dollar_cap_pct, 2)

        # Dollar risk
        dollar_risk = account_balance * (final_risk / 100)

        # Lot size calculation
        sl_pips = sl_dist / (point_value + 1e-10)
        pip_value = point_value * contract_size
        lot_size = dollar_risk / (sl_pips * pip_value + 1e-10) if sl_pips > 0 else 0
        lot_size = max(0.01, round(lot_size, 2))

        return RiskAllocation(
            risk_pct=final_risk, lot_size=lot_size,
            sl_pips=round(sl_pips, 1),
            sl_price=stop_loss, tp_price=take_profit,
            dollar_risk=round(dollar_risk, 2),
            approved=True,
            reason=(f"Clarity={bias.clarity} × Gov={governance_mode} × "
                    f"Session={session.session_weight} × Regime={regime.regime}"),
            timestamp=now)
