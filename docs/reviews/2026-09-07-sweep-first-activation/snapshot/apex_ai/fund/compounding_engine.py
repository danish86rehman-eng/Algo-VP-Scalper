"""
APEX AI — Compounding Engine (v10)
Tracks equity curve, optimizes for smooth growth with low drawdown.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime, timezone
import numpy as np


@dataclass
class EquitySnapshot:
    balance: float
    equity: float
    timestamp: datetime
    drawdown_pct: float = 0.0


@dataclass
class CompoundingState:
    current_balance: float
    peak_balance: float
    max_drawdown_pct: float
    current_drawdown_pct: float
    daily_pnl: float
    total_pnl: float
    growth_pct: float
    equity_curve: List[float] = field(default_factory=list)
    suggested_risk_pct: float = 1.0    # Kelly-adjusted risk suggestion
    timestamp: Optional[datetime] = None

    def __repr__(self):
        return (f"Equity(bal={self.current_balance:.2f} DD={self.current_drawdown_pct:.2f}% "
                f"growth={self.growth_pct:.2f}%)")


class CompoundingEngine:
    """
    APEX Compounding Engine — monitors equity curve health and
    adjusts suggested risk using a fractional Kelly criterion.
    Goal: smooth equity growth, low drawdown, stable returns.
    """

    def __init__(self, initial_balance: float = 10000.0,
                 kelly_fraction: float = 0.25,
                 max_risk_pct: float = 2.0):
        self.initial_balance = initial_balance
        self.kelly_fraction  = kelly_fraction   # Fractional Kelly (25%)
        self.max_risk_pct    = max_risk_pct
        self._peak_balance   = initial_balance
        self._start_balance  = initial_balance
        self._daily_start    = initial_balance
        self._snapshots: List[EquitySnapshot] = []

    def update(self, balance: float, equity: float,
               win_rate: float = 0.55, avg_rr: float = 2.0) -> CompoundingState:
        now = datetime.now(timezone.utc)

        # Update peak
        self._peak_balance = max(self._peak_balance, balance)

        # Calculate drawdowns
        dd_from_peak = ((self._peak_balance - balance) / self._peak_balance * 100
                        if self._peak_balance > 0 else 0.0)
        max_dd = max((s.drawdown_pct for s in self._snapshots), default=0.0)
        max_dd = max(max_dd, dd_from_peak)

        # Store snapshot
        snap = EquitySnapshot(balance=balance, equity=equity,
                              timestamp=now, drawdown_pct=dd_from_peak)
        self._snapshots.append(snap)

        # Keep last 500 snapshots
        if len(self._snapshots) > 500:
            self._snapshots = self._snapshots[-500:]

        # Kelly criterion: f* = (W - (1-W)/R) where W=winrate, R=avg_rr
        # Fractional Kelly = f* × kelly_fraction
        kelly_full = max(0.0, win_rate - (1 - win_rate) / (avg_rr + 1e-10))
        kelly_pct  = min(self.max_risk_pct, kelly_full * self.kelly_fraction * 100)

        # Drawdown adjustment to Kelly
        if dd_from_peak > 5.0:
            kelly_pct *= 0.5    # Half Kelly when DD > 5%
        elif dd_from_peak > 3.0:
            kelly_pct *= 0.75

        equity_curve = [s.balance for s in self._snapshots[-50:]]

        return CompoundingState(
            current_balance=round(balance, 2),
            peak_balance=round(self._peak_balance, 2),
            max_drawdown_pct=round(max_dd, 3),
            current_drawdown_pct=round(dd_from_peak, 3),
            daily_pnl=round(balance - self._daily_start, 2),
            total_pnl=round(balance - self._start_balance, 2),
            growth_pct=round((balance - self._start_balance) / self._start_balance * 100, 2),
            equity_curve=equity_curve,
            suggested_risk_pct=round(max(0.1, kelly_pct), 2),
            timestamp=now)

    def reset_daily(self, balance: float):
        """Call at start of each trading day."""
        self._daily_start = balance
