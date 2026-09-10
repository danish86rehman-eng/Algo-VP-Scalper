"""
APEX AI — Order Flow & Pressure Model (v5)
Interprets who is in control: buyers vs sellers.
Detects absorption, aggressive vs engineered moves.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import pandas as pd
import numpy as np
from datetime import datetime, timezone


@dataclass
class OrderFlowState:
    control: str = "NEUTRAL"            # 'BUYERS' | 'SELLERS' | 'NEUTRAL'
    pressure: float = 0.0               # -1.0 (max sell) to +1.0 (max buy)
    absorption_detected: bool = False
    absorption_side: str = "NONE"       # 'DEMAND' | 'SUPPLY'
    move_type: str = "UNKNOWN"          # 'AGGRESSIVE' | 'ENGINEERED' | 'RANDOM'
    delta_score: float = 0.0
    description: str = ""
    timestamp: Optional[datetime] = None

    def __repr__(self):
        return f"OF({self.control} pressure={self.pressure:+.2f} {self.move_type})"


class OrderFlowEngine:
    """
    APEX Order Flow Engine — reads candle anatomy to infer who controls price.
    Uses body/wick ratios, consecutive candle analysis, and volume (if available).
    """

    def analyze(self, df: pd.DataFrame) -> OrderFlowState:
        state = OrderFlowState(timestamp=datetime.now(timezone.utc))
        if df is None or len(df) < 10:
            state.control = "NEUTRAL"
            return state

        last10 = df.iloc[-10:].copy()
        o = last10['open'].values
        h = last10['high'].values
        l = last10['low'].values
        c = last10['close'].values
        v = last10['volume'].values if 'volume' in last10.columns else np.ones(10)

        # ── Delta (buy/sell pressure from candle anatomy) ──────────────────
        deltas = []
        for i in range(len(last10)):
            body     = c[i] - o[i]
            full_rng = h[i] - l[i] + 1e-10
            # Positive delta = bullish candle dominance
            delta = body / full_rng  # -1 to +1
            deltas.append(delta)

        delta_score = float(np.mean(deltas))
        state.delta_score = round(delta_score, 3)

        # ── Pressure score weighted by recency ────────────────────────────
        weights = np.linspace(0.5, 1.0, len(deltas))
        weighted_delta = float(np.average(deltas, weights=weights))
        state.pressure = round(float(np.clip(weighted_delta, -1.0, 1.0)), 3)

        state.control = ("BUYERS" if state.pressure > 0.15 else
                         "SELLERS" if state.pressure < -0.15 else "NEUTRAL")

        # ── Absorption detection ────────────────────────────────────────────
        # Large candle body + high volume but price doesn't extend → absorption
        last3 = df.iloc[-3:]
        for i in range(1, len(last3)):
            bar = last3.iloc[i]
            body_size = abs(bar['close'] - bar['open'])
            wick_size  = (bar['high'] - bar['low'])
            if wick_size > 0 and body_size / wick_size < 0.3:
                # Long wick, small body = absorption candle
                if bar['close'] > bar['open']:
                    state.absorption_detected = True
                    state.absorption_side = "DEMAND"
                else:
                    state.absorption_detected = True
                    state.absorption_side = "SUPPLY"
                break

        # ── Move type classification ───────────────────────────────────────
        # Aggressive: all candles same direction, high volume
        same_dir = all(c[i] > o[i] for i in range(-4, 0)) or \
                   all(c[i] < o[i] for i in range(-4, 0))
        if same_dir and abs(state.pressure) > 0.5:
            state.move_type = "AGGRESSIVE"
        elif state.absorption_detected:
            state.move_type = "ENGINEERED"
        else:
            state.move_type = "RANDOM"

        state.description = (
            f"Control={state.control} | Pressure={state.pressure:+.2f} | "
            f"Move={state.move_type} | "
            f"{'Absorption on ' + state.absorption_side if state.absorption_detected else 'No absorption'}"
        )
        return state
