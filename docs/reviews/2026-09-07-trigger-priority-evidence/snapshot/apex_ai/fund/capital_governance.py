"""
APEX AI — Capital Governance (v8)
Modes: Aggressive / Balanced / Defensive / Preservation.
Switches based on drawdown levels.
"""
from __future__ import annotations
from enum import Enum
from dataclasses import dataclass
from typing import Optional
from datetime import datetime, timezone
import logging

logger = logging.getLogger("APEX.Governance")


class GovernanceMode(str, Enum):
    AGGRESSIVE   = "AGGRESSIVE"
    BALANCED     = "BALANCED"
    DEFENSIVE    = "DEFENSIVE"
    PRESERVATION = "PRESERVATION"


@dataclass
class GovernanceState:
    mode: GovernanceMode
    daily_dd_pct: float
    max_risk_per_trade: float
    max_total_exposure: float
    description: str = ""
    timestamp: Optional[datetime] = None


class CapitalGovernance:
    """
    APEX Capital Governance — dynamically switches risk mode based on drawdown.
    Capital protection ALWAYS takes priority.
    """

    THRESHOLDS = {
        GovernanceMode.AGGRESSIVE:   (0.0,  2.0),   # DD 0-2%
        GovernanceMode.BALANCED:     (2.0,  5.0),   # DD 2-5%
        GovernanceMode.DEFENSIVE:    (5.0,  7.0),   # DD 5-7%
        GovernanceMode.PRESERVATION: (7.0, 100.0),  # DD 7%+
    }

    RISK_CAPS = {
        GovernanceMode.AGGRESSIVE:   (2.5, 8.0),
        GovernanceMode.BALANCED:     (2.0, 6.0),
        GovernanceMode.DEFENSIVE:    (1.0, 3.0),
        GovernanceMode.PRESERVATION: (0.5, 1.5),
    }

    def __init__(self, default_mode: str = "BALANCED",
                 aggressive_dd: float = 2.0, balanced_dd: float = 5.0,
                 defensive_dd: float = 7.0, preservation_dd: float = 10.0):
        self.default_mode = GovernanceMode(default_mode)
        self._custom_thresholds = {
            GovernanceMode.AGGRESSIVE:   (0.0, aggressive_dd),
            GovernanceMode.BALANCED:     (aggressive_dd, balanced_dd),
            GovernanceMode.DEFENSIVE:    (balanced_dd, defensive_dd),
            GovernanceMode.PRESERVATION: (defensive_dd, 100.0),
        }
        self._current_mode = self.default_mode

    def assess(self, account_balance: float, peak_balance: float) -> GovernanceState:
        """Calculate current drawdown and select appropriate governance mode."""
        dd_pct = 0.0
        if peak_balance > 0:
            dd_pct = max(0.0, (peak_balance - account_balance) / peak_balance * 100)

        mode = self._select_mode(dd_pct)

        if mode != self._current_mode:
            logger.warning(f"⚖️ Governance mode changed: {self._current_mode} → {mode} "
                           f"(DD={dd_pct:.2f}%)")
            self._current_mode = mode

        max_risk, max_exp = self.RISK_CAPS[mode]

        descs = {
            GovernanceMode.AGGRESSIVE:   "Full power — all strategies active",
            GovernanceMode.BALANCED:     "Standard operation — normal risk sizing",
            GovernanceMode.DEFENSIVE:    "Drawdown elevated — reducing position sizes",
            GovernanceMode.PRESERVATION: "Capital protection mode — minimal exposure only",
        }

        return GovernanceState(
            mode=mode, daily_dd_pct=round(dd_pct, 3),
            max_risk_per_trade=max_risk, max_total_exposure=max_exp,
            description=descs[mode], timestamp=datetime.now(timezone.utc))

    def _select_mode(self, dd_pct: float) -> GovernanceMode:
        for mode, (low, high) in self._custom_thresholds.items():
            if low <= dd_pct < high:
                return mode
        return GovernanceMode.PRESERVATION

    @property
    def current_mode(self) -> GovernanceMode:
        return self._current_mode
