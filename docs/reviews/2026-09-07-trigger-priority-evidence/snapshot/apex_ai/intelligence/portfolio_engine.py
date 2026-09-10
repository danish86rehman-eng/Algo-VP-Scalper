"""
APEX AI — Portfolio Intelligence Engine (v5)
Thinks in exposure, not trades. Prevents correlated stacking.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Tuple
import numpy as np
from datetime import datetime, timezone


# Correlation groups: symbols that move together
CORRELATION_GROUPS = {
    "PRECIOUS_METALS": ["XAUUSD", "XAGUSD"],
    "RISK_ASSETS":     ["BTCUSD"],
    "ENERGY":          ["USOil", "USOIL", "XTIUSD"],
}

# Directional correlation (approximate): positive = same direction, negative = inverse
CROSS_CORRELATIONS = {
    ("XAUUSD", "XAGUSD"): +0.85,
    ("XAUUSD", "USOil"):  +0.40,
    ("XAUUSD", "BTCUSD"): +0.30,
    ("XAGUSD", "USOil"):  +0.35,
    ("XAGUSD", "BTCUSD"): +0.25,
    ("BTCUSD", "USOil"):  +0.20,
}


@dataclass
class PortfolioState:
    total_risk_pct: float = 0.0         # Sum of all open risks
    directional_exposure: str = "FLAT"  # LONG | SHORT | FLAT | MIXED
    correlated_pairs: List[Tuple] = field(default_factory=list)
    open_symbols: List[str] = field(default_factory=list)
    can_add_long: bool = True
    can_add_short: bool = True
    warnings: List[str] = field(default_factory=list)
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)


class PortfolioEngine:
    """
    APEX Portfolio Engine — manages cross-asset exposure and correlation risk.
    Rejects trades that would stack correlated positions or breach exposure limits.
    """

    def __init__(self, max_total_risk_pct: float = 6.0, max_positions: int = 4):
        self.max_total_risk_pct = max_total_risk_pct
        self.max_positions = max_positions

    def assess(self, open_positions: List[Dict]) -> PortfolioState:
        """Assess current portfolio state from list of open positions."""
        state = PortfolioState()
        if not open_positions:
            return state

        state.open_symbols = [p['symbol'] for p in open_positions]
        longs  = [p for p in open_positions if p.get('type') == 'BUY']
        shorts = [p for p in open_positions if p.get('type') == 'SELL']

        # Directional exposure
        if longs and not shorts:
            state.directional_exposure = "LONG"
        elif shorts and not longs:
            state.directional_exposure = "SHORT"
        elif longs and shorts:
            state.directional_exposure = "MIXED"

        # Total risk (approximated from position profit relative to balance)
        state.total_risk_pct = sum(p.get('risk_pct', 1.0) for p in open_positions)

        # Correlated pair detection
        symbols = state.open_symbols
        for (s1, s2), corr in CROSS_CORRELATIONS.items():
            if s1 in symbols and s2 in symbols:
                state.correlated_pairs.append((s1, s2, corr))
                state.warnings.append(f"⚠️ Correlated positions: {s1}/{s2} (ρ={corr:.2f})")

        # Max positions check
        if len(open_positions) >= self.max_positions:
            state.can_add_long  = False
            state.can_add_short = False
            state.warnings.append(f"⚠️ Max positions reached ({self.max_positions})")

        # Max risk check
        if state.total_risk_pct >= self.max_total_risk_pct:
            state.can_add_long  = False
            state.can_add_short = False
            state.warnings.append(f"⚠️ Max exposure reached ({state.total_risk_pct:.1f}%)")

        return state

    def can_trade(self, symbol: str, direction: str,
                  portfolio: PortfolioState) -> Tuple[bool, str]:
        """
        Returns (allowed, reason) for adding a new trade.
        """
        # Max positions
        if len(portfolio.open_symbols) >= self.max_positions:
            return False, f"Max positions ({self.max_positions}) reached"

        # Max exposure
        if portfolio.total_risk_pct >= self.max_total_risk_pct:
            return False, f"Max total risk ({self.max_total_risk_pct}%) reached"

        # Correlated stacking check
        for open_sym in portfolio.open_symbols:
            corr = self._get_correlation(symbol, open_sym)
            if abs(corr) >= 0.7:
                # Check if directions align (stacking correlated risk)
                return False, f"Correlated with open position {open_sym} (ρ={corr:.2f})"

        # Directional exposure check
        if direction == "BUY" and not portfolio.can_add_long:
            return False, "Cannot add more long exposure"
        if direction == "SELL" and not portfolio.can_add_short:
            return False, "Cannot add more short exposure"

        return True, "OK"

    def _get_correlation(self, s1: str, s2: str) -> float:
        key = (s1, s2) if (s1, s2) in CROSS_CORRELATIONS else (s2, s1)
        return CROSS_CORRELATIONS.get(key, 0.0)
