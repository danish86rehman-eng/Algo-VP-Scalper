"""
SA Behavior State Machine
==========================
Manages the SA agent's operational state.

States (from SCALPER_AGENT.md):
    ACTIVE    : Session window open + triggers available -> trade normally
    IDLE      : Outside session window -> no trades
    PAUSED    : 2 consecutive losses -> wait 15 min before next trade
    HALTED    : Daily loss limit hit -> no more trades today
    PROTECTED : Main system drawdown > 3% -> suspend all SA activity

Transitions are deterministic based on input conditions.
State is logged on every change.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

logger = logging.getLogger("SA.State")


class SAState(str, Enum):
    ACTIVE    = "ACTIVE"
    IDLE      = "IDLE"
    PAUSED    = "PAUSED"
    HALTED    = "HALTED"
    PROTECTED = "PROTECTED"
    #: Outside every normal session window, but inside the trigger-scoped
    #: VP_LIQUIDITY_REACTION allowance. Scanning happens with the trigger set
    #: narrowed to that one detector — see ScalperAgent._scan_symbol.
    VP_ONLY   = "VP_ONLY"


@dataclass
class StateTransition:
    from_state: SAState
    to_state: SAState
    reason: str
    timestamp: str


class SABehaviorStateMachine:
    """
    SA state machine. Determines current operational state based on
    session, drawdown, loss counters, and risk governor output.
    """

    def __init__(self):
        self._state: SAState = SAState.IDLE
        self._history: list = []

    @property
    def state(self) -> SAState:
        return self._state

    @property
    def is_trading_allowed(self) -> bool:
        return self._state in (SAState.ACTIVE, SAState.VP_ONLY)

    @property
    def is_vp_only(self) -> bool:
        """True when scanning must be narrowed to VP_LIQUIDITY_REACTION."""
        return self._state == SAState.VP_ONLY

    def evaluate(self,
                 in_session_window: bool,
                 daily_loss_limit_hit: bool,
                 main_dd_pct: float,
                 consecutive_losses: int,
                 pause_active: bool,
                 vp_window_open: bool = False,
                 ) -> SAState:
        """
        Evaluate inputs and transition to the correct state.

        Priority: PROTECTED > HALTED > PAUSED > ACTIVE > VP_ONLY > IDLE.

        `vp_window_open` is consulted ONLY after every hard protection has
        already been evaluated and cleared, and only when no normal session
        window is open. The trigger-scoped allowance can therefore widen *when*
        the agent looks for one specific setup; it can never override a
        drawdown stop, a daily loss limit, or a consecutive-loss pause. Those
        three return above it and are unreachable from here.

        Defaults to False, so a caller that has not been updated gets exactly
        the previous behaviour.
        """
        # 1. PROTECTED: main system in danger
        if main_dd_pct > 3.0:
            self._set(SAState.PROTECTED,
                      f"Main account DD {main_dd_pct:.1f}% > 3% threshold")
            return self._state

        # 2. HALTED: daily loss limit hit
        if daily_loss_limit_hit:
            self._set(SAState.HALTED, "Daily loss limit hit (2% of SA pool)")
            return self._state

        # 3. PAUSED: 2+ consecutive losses, 15-min cooldown
        if consecutive_losses >= 2 and pause_active:
            self._set(SAState.PAUSED,
                      f"{consecutive_losses} consecutive losses — 15-min pause active")
            return self._state

        # 4. VP_ONLY / IDLE: outside every normal session window.
        if not in_session_window:
            if vp_window_open:
                self._set(SAState.VP_ONLY,
                          "Outside SA session window; VP-only allowance open")
                return self._state
            self._set(SAState.IDLE, "Outside SA session window")
            return self._state

        # 5. ACTIVE: all conditions met
        self._set(SAState.ACTIVE, "Session active, all checks clear")
        return self._state

    def _set(self, new_state: SAState, reason: str):
        if new_state != self._state:
            transition = StateTransition(
                from_state=self._state,
                to_state=new_state,
                reason=reason,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            self._history.append(transition)
            logger.info(f"SA State: {self._state.value} -> {new_state.value} | {reason}")
            self._state = new_state

    def get_history(self) -> list:
        return [
            {"from": t.from_state, "to": t.to_state,
             "reason": t.reason, "at": t.timestamp}
            for t in self._history
        ]

    def daily_reset(self):
        """Reset state to IDLE at start of new day."""
        self._set(SAState.IDLE, "Daily reset")
