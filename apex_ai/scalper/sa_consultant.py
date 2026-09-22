"""
SA Consultant — ReadOnly-Consultation Layer
============================================
Provides the Scalper Agent with read-only access to the Main System's
core intelligence engines WITHOUT joining the AI Council.

Isolation Guarantee:
    - This module NEVER opens, modifies, or closes trades.
    - It reads market data and computes institutional context.
    - All engine failures return a safe neutral default result.
    - Magic Number (88880) is never referenced here.

Consultation Pipeline (per symbol):
    1. Fetch M5 data  -> StructureEngine + DisplacementEngine
    2. Fetch H1 data  -> LiquidityEngine + ManipulationEngine
    3. Feed all into  -> RegimeEngine
    4. Return         -> SAConsultResult
"""
from __future__ import annotations
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Tuple
import pandas as pd
import numpy as np

logger = logging.getLogger("SA.Consul")

from scalper import decision_params as DP
from scalper.regime_classifier import RegimeClassifier

#: Momentum floor for "institutional displacement". Gate 2 (BOS_RETEST) needs
#: displacement, a momentum score at or above this, and a mapped FVG.
DISPLACEMENT_MIN_MOMENTUM = 0.65


# ── Safe import of core engines ───────────────────────────────────────────────
# If any import fails (e.g., running SA standalone without main system),
# the consultant defaults to a neutral/permissive state.
try:
    import MetaTrader5 as mt5
    from core.structure_engine import StructureEngine, StructureState
    from core.manipulation_engine import ManipulationEngine, ManipulationSignal
    from core.displacement_engine import DisplacementEngine, DisplacementMap
    from core.liquidity_engine import LiquidityEngine, LiquidityMap
    from intelligence.regime_engine import RegimeEngine, RegimeState
    _ENGINES_AVAILABLE = True
except ImportError as e:
    logger.warning(f"SA Consul: Core engines unavailable — {e}. Defaulting to neutral.")
    _ENGINES_AVAILABLE = False


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class SAConsultResult:
    """
    The output of a single SA consultation.
    All fields have safe defaults that allow trading to proceed normally
    if the consultation fails or engines are unavailable.
    """
    # Regime
    regime: str = "UNKNOWN"                     # EXPANSION | MANIPULATION | ROTATION | TRANSITION
    regime_behavior: str = "OBSERVE"            # EXPLOIT | OBSERVE | ADAPT | REDUCE | DISENGAGE
    regime_confidence: float = 0.0

    # Displacement
    is_displaced: bool = False
    displacement_direction: str = "NONE"        # BULLISH | BEARISH | NONE
    displacement_momentum: float = 0.0

    # Liquidity targets (H1 macro pools)
    nearest_bsl: Optional[float] = None         # Best TP2 for BUY trades
    nearest_ssl: Optional[float] = None         # Best TP2 for SELL trades
    price_zone: str = "UNKNOWN"                 # PREMIUM | DISCOUNT | UNKNOWN

    # Gate decisions
    regime_blocks_trade: bool = False           # True if regime says skip
    displacement_confirmed: bool = False        # True if displacement exists
    lia_tp2_override: Optional[float] = None   # Macro TP2 if a better target found

    # Meta
    success: bool = False                       # False = engine failure, use defaults
    reason: str = ""
    snapshot_ts: Optional[datetime] = None
    latency_ms: float = 0.0
    stale_data: bool = False
    # Observation-only regime components; existing decision fields remain the
    # sole source of gate behavior.
    atr_ratio: float = 0.0
    displacement_momentum_score: float = 0.0
    structure_trend: str = "RANGING"
    manipulation_detected: bool = False
    manipulation_confidence: float = 0.0
    statistical_regime: str = "UNKNOWN"
    statistical_autocorrelation: float = 0.0
    statistical_efficiency_ratio: float = 0.0
    statistical_volatility_ratio: float = 0.0

    def summary(self) -> str:
        return (
            f"Regime={self.regime}({self.regime_behavior}/{self.regime_confidence:.2f}) | "
            f"Displaced={self.is_displaced}({self.displacement_direction}) | "
            f"BSL={self.nearest_bsl} SSL={self.nearest_ssl} | "
            f"Block={self.regime_blocks_trade}"
        )


# ── The shared analysis pass ─────────────────────────────────────────────────

def analyse(symbol: str,
            df_trigger: pd.DataFrame,
            df_h1: pd.DataFrame,
            *,
            structure_engine,
            displacement_engine,
            liquidity_engine,
            manipulation_engine,
            regime_engine) -> SAConsultResult:
    """
    Compute the institutional context for one symbol from supplied frames.

    Pure with respect to data: it fetches nothing and reads no clock, so the
    live agent (which passes freshly fetched frames) and the simulator (which
    passes historical slices) run the *same* code rather than two
    implementations that are supposed to agree.

    They previously did not agree. `backtest_scalper._apply_v1_council_gates`
    was a parallel re-implementation running structure, displacement and regime
    on M15, while this module ran them on M5 — a leftover from before the
    M15/M5 migration, which `_run_consultation` was never updated for. Gate 1
    is a regime whitelist, so the live agent and the simulator were applying
    the same whitelist to regimes classified on different timeframes. That is
    exactly the defect invariant #2 exists to prevent, and it invalidated any
    comparison between live and simulated results.

    `df_trigger` must be the trigger frame (M15) and `df_h1` the H1 frame, both
    containing completed bars only.
    """
    result = SAConsultResult(success=True)

    # Structure + displacement on the trigger frame — both feed the regime.
    structure = structure_engine.analyze(df_trigger, symbol, "M15")
    displacement = displacement_engine.analyze(df_trigger, symbol, "M15")

    result.is_displaced = displacement.is_displaced
    result.displacement_direction = displacement.displacement_direction
    result.displacement_momentum = displacement.momentum_score
    has_fvg = bool(
        displacement.nearest_bullish_fvg is not None or
        displacement.nearest_bearish_fvg is not None
    )
    result.displacement_confirmed = (
        displacement.is_displaced and
        displacement.momentum_score >= DISPLACEMENT_MIN_MOMENTUM and
        has_fvg
    )

    # Liquidity on H1 — macro pools, used for the Gate 3 TP2 realignment.
    liq_map = liquidity_engine.analyze(df_h1, symbol, "H1")
    result.price_zone = liq_map.premium_or_discount()
    result.nearest_bsl = liq_map.nearest_bsl.level if liq_map.nearest_bsl else None
    result.nearest_ssl = liq_map.nearest_ssl.level if liq_map.nearest_ssl else None

    # Manipulation on H1 — feeds the regime.
    manip_signal = manipulation_engine.analyze(df_h1, liq_map)

    regime = regime_engine.analyze(
        df_trigger, structure, manip_signal, displacement, daily_dd_pct=0.0)
    result.regime = regime.regime
    result.regime_behavior = regime.behavior_state
    result.regime_confidence = regime.confidence
    result.regime_blocks_trade = regime.regime in ("TRANSITION", "ROTATION")
    result.atr_ratio = regime.atr_ratio
    result.displacement_momentum_score = displacement.momentum_score
    result.structure_trend = structure.trend
    result.manipulation_detected = manip_signal.detected
    result.manipulation_confidence = manip_signal.confidence
    # Observation only.  This classifier is not consulted by any live gate.
    stat = RegimeClassifier(
        lookback=DP.VP_REGIME_LOOKBACK,
        smoothing=DP.VP_REGIME_SMOOTHING,
        trend_threshold=DP.VP_REGIME_TREND_THRESHOLD,
        vol_threshold=DP.VP_REGIME_VOL_THRESHOLD,
        er_threshold=DP.VP_REGIME_ER_THRESHOLD,
    ).classify(df_h1)
    result.statistical_regime = stat.regime
    result.statistical_autocorrelation = stat.autocorr
    result.statistical_efficiency_ratio = stat.efficiency_ratio
    result.statistical_volatility_ratio = stat.vol_ratio
    return result


# ── SA Consultant ─────────────────────────────────────────────────────────────

class SAConsultant:
    """
    ReadOnly-Consultation wrapper that gives the Scalper Agent
    institutional-grade market context without joining the AI Council.
    """

    CACHE_SECONDS = 30  # Refresh engine results every 30 seconds per symbol
    STALE_AFTER_SECONDS = 60

    def __init__(self):
        self._cache: Dict[str, Tuple[SAConsultResult, float]] = {}
        self.available = _ENGINES_AVAILABLE

        if self.available:
            self._structure   = StructureEngine(swing_lookback=5)
            self._manipulation= ManipulationEngine()
            self._displacement= DisplacementEngine()
            self._liquidity   = LiquidityEngine(swing_lookback=5)
            self._regime      = RegimeEngine()
            logger.info("SA Consul: All core engines loaded. Institutional consultation active.")
        else:
            logger.warning("SA Consul: Running in PASSIVE mode — no engine consultation.")

    # ── Public API ─────────────────────────────────────────────────────────────

    def consult(self, symbol: str, trigger_type: str = "NONE",
                trigger_direction: str = "NONE",
                original_tp1: float = 0.0,
                original_tp2: float = 0.0,
                current_price: float = 0.0) -> SAConsultResult:
        """
        Run a full institutional consultation for one symbol.
        Returns cached result if fresh (< 30s old).
        Falls back to permissive neutral result on any failure.
        """
        started = time.perf_counter()
        if not self.available:
            result = SAConsultResult(success=False, reason="Engines not available")
            result.latency_ms = (time.perf_counter() - started) * 1000.0
            return result

        # Cache check
        cache_key = symbol
        now = time.time()
        if cache_key in self._cache:
            cached_result, cached_at = self._cache[cache_key]
            if now - cached_at < self.CACHE_SECONDS:
                # Still recalculate LIA TP2 override as it's trigger-specific
                fresh_copy = SAConsultResult(**vars(cached_result))
                fresh_copy = self._apply_lia_override(
                    fresh_copy, trigger_direction, original_tp1,
                    original_tp2, current_price)
                fresh_copy.latency_ms = (time.perf_counter() - started) * 1000.0
                age = now - cached_at
                fresh_copy.stale_data = age > self.STALE_AFTER_SECONDS
                return fresh_copy

        try:
            result = self._run_consultation(symbol)
            result.snapshot_ts = datetime.now(timezone.utc)
            self._cache[cache_key] = (result, now)
            result = self._apply_lia_override(
                result, trigger_direction, original_tp1, original_tp2, current_price)
            result.latency_ms = (time.perf_counter() - started) * 1000.0
            result.stale_data = False
            return result
        except Exception as e:
            logger.error(f"SA Consul [{symbol}]: Consultation failed — {e}", exc_info=False)
            result = SAConsultResult(success=False, reason=str(e))
            result.latency_ms = (time.perf_counter() - started) * 1000.0
            return result

    # ── Internal Pipeline ──────────────────────────────────────────────────────

    def _run_consultation(self, symbol: str) -> SAConsultResult:
        # Fetch closed bars only — position 0 is the forming bar, whose values
        # still move. The simulator cannot see inside a forming bar, so reading
        # one here would make the two processes analyse different data.
        df_trigger = self._fetch(symbol, DP.TF_TRIGGER, bars=DP.TRIGGER_BARS)
        df_h1 = self._fetch(symbol, DP.TF_HTF, bars=DP.CONSULT_HTF_BARS)

        if df_trigger is None or df_h1 is None:
            return SAConsultResult(success=False,
                                   reason="Could not fetch OHLCV data")

        result = analyse(
            symbol, df_trigger, df_h1,
            structure_engine    = self._structure,
            displacement_engine = self._displacement,
            liquidity_engine    = self._liquidity,
            manipulation_engine = self._manipulation,
            regime_engine       = self._regime,
        )

        # `regime_blocks_trade` is set inside analyse(). It is advisory only —
        # the binding decision is the per-trigger regime whitelist applied in
        # scalper_agent._scan_symbol, which allows sweep fades in MANIPULATION
        # and ROTATION rather than blanket-blocking them.
        logger.debug(f"SA Consul [{symbol}]: {result.summary()}")
        return result

    def observe_frames(self, symbol: str, df_trigger: pd.DataFrame,
                       df_h1: pd.DataFrame) -> SAConsultResult:
        """Run the existing context pass on supplied closed frames only.

        Observation API: callers must not use this result to make decisions.
        """
        return analyse(
            symbol, df_trigger, df_h1,
            structure_engine=self._structure,
            displacement_engine=self._displacement,
            liquidity_engine=self._liquidity,
            manipulation_engine=self._manipulation,
            regime_engine=self._regime,
        )

    def _apply_lia_override(self, result: SAConsultResult,
                            trigger_direction: str,
                            original_tp1: float,
                            original_tp2: float,
                            current_price: float) -> SAConsultResult:
        """Instance-level shim; the logic lives in the module function."""
        return apply_lia_override(result, trigger_direction, original_tp1,
                                  original_tp2, current_price)


    def _fetch(self, symbol: str, timeframe: int, bars: int = 100) -> Optional[pd.DataFrame]:
        """Fetch OHLCV from MT5 and return as DataFrame."""
        try:
            # Start at 1: position 0 is the forming bar. See _get_ohlcv in
            # scalper_agent.py for why every decision frame excludes it.
            rates = mt5.copy_rates_from_pos(symbol, timeframe, 1, bars)
            if rates is None or len(rates) < 20:
                return None
            df = pd.DataFrame(rates)
            df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
            return df
        except Exception:
            return None



# ── Gate 3 helper (module level so the simulator shares it) ──────────────────
def apply_lia_override(result: SAConsultResult,
                       trigger_direction: str,
                       original_tp1: float,
                       original_tp2: float,
                       current_price: float) -> SAConsultResult:
    """
    Gate 3: use a macro H1 liquidity pool as TP2 when it is a better target.
    Rule: if the pool sits within 1.5x the TP1 distance and beyond the current
    TP2, it becomes TP2.

    Module-level so the simulator can call it directly. It previously existed
    only as a method, which left the simulator to re-derive the same rule
    inline — a second place for it to drift.
    """
    if current_price <= 0 or original_tp1 <= 0:
        return result

    tp1_dist = abs(original_tp1 - current_price)
    if tp1_dist == 0:
        return result

    proximity_threshold = tp1_dist * 1.5

    if trigger_direction == "BULLISH" and result.nearest_bsl:
        bsl_dist = abs(result.nearest_bsl - current_price)
        if bsl_dist <= proximity_threshold and result.nearest_bsl > original_tp2:
            result.lia_tp2_override = result.nearest_bsl
            logger.info(
                f"SA Consul: LIA TP2 Override → {result.nearest_bsl:.5f} "
                f"(Macro BSL better than fixed TP2={original_tp2:.5f})"
            )
    elif trigger_direction == "BEARISH" and result.nearest_ssl:
        ssl_dist = abs(result.nearest_ssl - current_price)
        if ssl_dist <= proximity_threshold and result.nearest_ssl < original_tp2:
            result.lia_tp2_override = result.nearest_ssl
            logger.info(
                f"SA Consul: LIA TP2 Override → {result.nearest_ssl:.5f} "
                f"(Macro SSL better than fixed TP2={original_tp2:.5f})"
            )

    return result
