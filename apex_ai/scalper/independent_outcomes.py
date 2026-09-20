"""Research-only, portfolio-independent sweep outcome evaluation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from scalper.decision_params import TIMEOUT_CONFIRM_BARS


@dataclass(frozen=True)
class IndependentOutcome:
    outcome: str
    exit_price: Optional[float]
    realised_R: Optional[float]
    mfe_R: Optional[float]
    mae_R: Optional[float]
    bars_to_exit: Optional[int]
    exit_time: Optional[str]
    cost_price: Optional[float]
    status: str = "RESEARCH_ONLY"

    def record(self) -> dict:
        return {
            "outcome": self.outcome, "exit_price": self.exit_price,
            "realised_R": self.realised_R, "mfe_R": self.mfe_R,
            "mae_R": self.mae_R, "bars_to_exit": self.bars_to_exit,
            "exit_time": self.exit_time, "cost_price": self.cost_price,
            "status": self.status,
        }


def evaluate_sweep(*, trigger: Any, df_m5, entry_idx: int,
                   fill_at_next_bar_open: bool, spread_price: float = 0.0,
                   round_trip_cost_price: Optional[float] = None,
                   commission_price: float = 0.0) -> IndependentOutcome:
    """Evaluate one signal using the project's stop-first M5 convention.

    This function has no portfolio, cooldown, risk, or position-state inputs.
    It is deliberately unsuitable for execution and is only a per-candidate
    outcome labeler.
    """
    if df_m5 is None or entry_idx >= len(df_m5):
        return IndependentOutcome("NO_DATA", None, None, None, None, None, None, None)
    entry = float(df_m5.iloc[entry_idx]["open"] if fill_at_next_bar_open else trigger.entry_price)
    risk = abs(float(trigger.entry_price) - float(trigger.stop_loss))
    if risk <= 0:
        return IndependentOutcome("NO_DATA", entry, None, None, None, None, None, None)
    cost = (float(round_trip_cost_price) if round_trip_cost_price is not None
            else float(spread_price) + float(commission_price))
    max_idx = min(entry_idx + TIMEOUT_CONFIRM_BARS, len(df_m5) - 1)
    direction = str(trigger.direction)
    mfe = 0.0
    mae = 0.0
    exit_price = float(df_m5.iloc[max_idx]["close"])
    outcome = "TIMEOUT"
    exit_time = df_m5.iloc[max_idx]["time"]
    exit_idx = max_idx
    for j in range(entry_idx, max_idx + 1):
        bar = df_m5.iloc[j]
        high, low = float(bar["high"]), float(bar["low"])
        if direction == "BULLISH":
            mfe = max(mfe, high - entry); mae = max(mae, entry - low)
            if low <= float(trigger.stop_loss):
                outcome, exit_price, exit_idx, exit_time = "SL", float(trigger.stop_loss), j, bar["time"]; break
            if high >= float(trigger.tp1):
                outcome, exit_price, exit_idx, exit_time = "TP", float(trigger.tp1), j, bar["time"]; break
        else:
            mfe = max(mfe, entry - low); mae = max(mae, high - entry)
            if high >= float(trigger.stop_loss):
                outcome, exit_price, exit_idx, exit_time = "SL", float(trigger.stop_loss), j, bar["time"]; break
            if low <= float(trigger.tp1):
                outcome, exit_price, exit_idx, exit_time = "TP", float(trigger.tp1), j, bar["time"]; break
        if hasattr(bar["time"], "to_pydatetime") and bar["time"].to_pydatetime().hour == 23:
            outcome, exit_price, exit_idx, exit_time = "TIMEOUT", float(bar["close"]), j, bar["time"]; break
    signed = (exit_price - entry) if direction == "BULLISH" else (entry - exit_price)
    return IndependentOutcome(
        outcome=outcome, exit_price=exit_price,
        realised_R=(signed - cost) / risk,
        mfe_R=mfe / risk, mae_R=mae / risk,
        bars_to_exit=exit_idx - entry_idx,
        exit_time=(exit_time.isoformat() if hasattr(exit_time, "isoformat") else str(exit_time)),
        cost_price=cost)
