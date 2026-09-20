"""
Historical backtest for ScalperAgent (SA-V2).

The simulator replays the *live decision path*. Invariant #2 of this repository
says any gate present in `ScalperAgent._scan_symbol` must also be present here:
a backtest that does not run the live decision path is not evidence. One full
research campaign in the donor repository was invalidated by exactly that
defect, so the gate chain below is written in the same order as the live one
and each rejection is counted so the two can be reconciled.

Structure: a single chronological event loop over the merged M15 timeline of
every symbol. It is not a per-symbol loop, because the cooldown, the pool
balance, the daily loss limit and the open-position cap are all *global* state
in the live agent — a loss on XAUUSD pauses USOIL too, and per-symbol loops
cannot express that.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
from types import SimpleNamespace
from collections import Counter
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import MetaTrader5 as mt5
import pandas as pd
from dotenv import load_dotenv

from core.displacement_engine import DisplacementEngine
from core.liquidity_engine import LiquidityEngine
from core.manipulation_engine import ManipulationEngine
from core.structure_engine import StructureEngine
from intelligence.regime_engine import RegimeEngine
from core.news_guard import NewsGuard
from scalper.cooldown import SACooldown
from scalper.ema_filter import EMABandFilter
from scalper.sa_consultant import analyse as consult_analyse, apply_lia_override
from scalper.sa_crg import SACRG
from scalper.session_checker import SASessionChecker
from scalper.short_term_bias import ShortTermBiasFilter
from scalper.trigger_engine import (
    SATrigger, SATriggerEngine, resolve_enabled_triggers)
from scalper.leg_confluence import (
    MODES as LEG_CONF_MODES, build_leg_pair,
    evaluate as leg_conf_evaluate)
from scalper.market_location import MarketLocationConfig, MarketLocationEngine
from scalper.location_permission import (build_location_permission,
                                          evaluate_sweep_reaction)
from scalper.vp_gate import MODES as VP_MODES, VolumeProfileGate
from scalper.regime_classifier import RegimeClassifier
from scalper.regime_direction_gate import (
    MODES as RD_MODES, RegimeDirectionGate)
from scalper.pdr_gate import MODES as PDR_MODES, PDRGate
from scalper.execution_telemetry import build_entry_observation
from scalper.sweep_location import (SweepLocationPolicy,
                                     build_sweep_location_context,
                                     load_sweep_location_policy)
from scalper.candidate_funnel import (CandidateFunnelRecorder,
                                       candidate_id_for_trigger,
                                       make_candidate_id)
from scalper.independent_outcomes import evaluate_sweep
from scalper.vp_liquidity_trigger import (
    VPLRParams,
    build_context as vplr_context,
    detect_with_context as vplr_detect,
)
import scalper.decision_params as DP

# Every decision constant is imported, not restated. This block used to hold
# hand-copied duplicates of the live agent's values with a comment asking the
# next editor to keep them in step; three of them had already drifted. See
# scalper/decision_params.py.
from scalper.decision_params import (
    CONFIRM_BARS,
    EMA_BAND_PERIOD,
    CONSULT_HTF_BARS,
    EMA_BAND_BARS,
    EMA_BAND_ENABLED,
    EMA_BAND_MODE,
    EMA_BAND_TF,
    EOD_HOUR_UTC,
    PDR_BARS,
    PDR_DISCOUNT_FRAC,
    PDR_GATE_ENABLED,
    PDR_GATE_MODE,
    PDR_PREMIUM_FRAC,
    RD_GATE_ENABLED,
    VA_FADE_ENABLED,
    RD_GATE_MODE,
    RD_REGIME_BARS,
    RD_REGIME_TF,
    VP_EDGE_TOLERANCE_FRAC,
    LEG_CONF_ATR_PERIOD,
    LEG_CONF_ENABLED,
    LEG_CONF_LVN_ATR,
    LEG_CONF_MIN_LEG_ATR,
    LEG_CONF_MIN_LEG_BARS,
    LEG_CONF_MODE,
    LEG_CONF_NODE_STDDEV_MULT,
    LEG_CONF_SWING_LOOKBACK,
    LEG_CONF_TARGET_BINS,
    LEG_CONF_VALUE_AREA_PCT,
    LEG_CONF_ZONE_ATR,
    SWEEP_WICK_FILTER_ENABLED,
    SWEEP_WICK_RATIO_MIN,
    VP_GATE_ENABLED,
    VP_GATE_MODE,
    VP_POC_BAND_FRAC,
    VP_PROFILE_BARS,
    VP_PROFILE_FETCH_BARS,
    VP_PROFILE_TF,
    VP_REGIME_BARS,
    VP_REGIME_ER_THRESHOLD,
    VP_REGIME_LOOKBACK,
    VP_REGIME_SMOOTHING,
    VP_REGIME_TF,
    VP_REGIME_TREND_THRESHOLD,
    VP_REGIME_VOL_THRESHOLD,
    VP_TARGET_BINS,
    VP_VALUE_AREA_PCT,
    VPLR_ENABLED,
    VPLR_PROFILE_FETCH_BARS,
    VPLR_SCOPE,
    VPLR_SCOPES,
    VPLR_SESSION_OVERRIDE_ENABLED,
    VPLR_SESSION_WINDOW_UTC,
    H4_BARS,
    HTF_BARS,
    MAX_OPEN_POSITIONS,
    NEWS_BLACKOUT_AFTER_MIN,
    NEWS_BLACKOUT_BEFORE_MIN,
    NEWS_IMPACT_LEVELS,
    STB_RELAX_CONTINUATION,
    THIN_LIQ_REQUIRED_CONFIDENCE,
    THIN_LIQUIDITY_HOURS_UTC,
    CONFIRM_TF_MINUTES,
    TIMEOUT_CONFIRM_BARS,
    TGA_EXITS_IN_SIM,
    TRIGGER_BARS,
    TRIGGER_REGIME_WHITELIST,
)


#: Calendar days of history fetched before the requested start so the gates
#: with the longest lookback (H4 bias 120 bars, EMA band 200 H1 bars) are fully
#: seeded on the first tradable bar. Calendar rather than trading days because
#: weekends carry no bars.
WARMUP_LEAD_DAYS = 45


@dataclass
class SimTrade:
    symbol: str
    open_time: str
    close_time: str
    trigger_type: str
    direction: str
    entry: float         # actual fill (L-013 S1); == signal_price when disabled
    signal_price: float  # price that produced the trigger
    sl: float
    tp1: float
    tp2: float
    exit_price: float
    result: str          # WIN_TP1 | LOSS | TIMEOUT | EOD  (legacy label)
    exit_reason: str     # TP | SL | TIMEOUT | EOD         (exit-profile bucket)
    pnl: float           # net of round-turn spread and commission
    gross_pnl: float
    cost_usd: float
    volume: float
    risk_usd: float      # realised risk at the rounded lot size
    r_multiple: float
    duration_min: int
    # -- attribution ------------------------------------------------------
    #: The session window the entry was taken in, or VP_ASIA when it came from
    #: the trigger-scoped allowance. Needed to measure Asia against non-Asia
    #: without inferring the session from the timestamp.
    session: str = ""
    #: Every detector that fired on the entry bar, selected one first.
    matched_triggers: str = ""
    #: VP_LIQUIDITY_REACTION evidence. Empty for every other trigger.
    # -- Guardian replay telemetry (blank unless --tga-exits) --------------
    tga_managed: bool = False
    tga_sl_stage: int = 0
    tga_peak_r: float = 0.0
    tga_mae_r: float = 0.0
    tga_tp_extended: bool = False
    tga_legs: int = 1
    # -- Gate state at entry, mirroring the live agent's incident record.
    #: Not required by invariant #2 (the postmortem pass is post-processing,
    #: not a gate) but required for the two books to be poolable at all: a
    #: sim row with UNKNOWN htf cannot be compared against a live row.
    stb_confidence: str = "UNKNOWN"
    short_term_bias: str = "UNKNOWN"
    htf_trend: str = "UNKNOWN"
    recent_sweep: str = ""
    vp_level: str = ""           # POC | VAH | VAL
    vp_level_source: str = ""    # PDH | SESSION_HIGH | EQUAL_HIGHS | SWING_* …
    vp_sweep_depth_atr: float = 0.0
    vp_confluences: str = ""
    #: L-016. The sweeping candle's rejecting-wick share of its own range,
    #: stamped whether or not the filter is enabled — a baseline run has to be
    #: able to answer "how many WINNERS does this veto?" without a second arm.
    #: -1.0 for non-SWEEP_REJECTION trades, which the filter never sees.
    wick_ratio: float = -1.0
    reclaim: Optional[dict] = None  # closed-bar permission used for this fill
    m15_fvg: Optional[dict] = None
    htf_crt: Optional[dict] = None
    session_sweep: Optional[dict] = None
    entry_setup_telemetry: Optional[dict] = None
    market_location: Optional[dict] = None
    location_permission: Optional[dict] = None
    sweep_runtime_contract: Optional[dict] = None


def _sweep_runtime_record(trigger) -> dict:
    signal_entry = getattr(trigger, "entry_price", None)
    signal_stop = getattr(trigger, "stop_loss", None)
    signal_tp1 = getattr(trigger, "tp1", None)
    risk = (abs(float(signal_entry) - float(signal_stop))
            if signal_entry is not None and signal_stop is not None else 0.0)
    signal_r = (abs(float(signal_tp1) - float(signal_entry)) / (risk + 1e-10)
                if signal_entry is not None and signal_tp1 is not None and risk > 0
                else None)
    quote = getattr(trigger, "final_quote", None) or {}
    context = getattr(trigger, "location_context", None)
    permission = getattr(trigger, "location_permission", None)
    final_context = getattr(trigger, "final_location_context", None)
    final_permission = getattr(trigger, "final_location_permission", None)
    return {
        "candidate_id": getattr(trigger, "candidate_id", None),
        "decision_time": getattr(trigger, "decision_time", None),
        "sweep_time": getattr(trigger, "sweep_time", None),
        "direction": getattr(trigger, "direction", None),
        "swept_level": getattr(trigger, "swept_level", None),
        "signal_entry": signal_entry,
        "signal_stop": signal_stop,
        "signal_tp1": signal_tp1,
        "signal_R": signal_r,
        "signal_spread_pips": getattr(trigger, "signal_spread_pips", None),
        "signal_net_R": getattr(trigger, "signal_net_R", None),
        "named_liquidity": context.record() if context is not None else None,
        "market_location": (getattr(trigger, "market_location", None).record()
                            if getattr(trigger, "market_location", None) is not None else None),
        "location_permission": permission.record() if permission is not None else None,
        "final_location_context": final_context.record() if final_context is not None else None,
        "final_location_permission": final_permission.record() if final_permission is not None else None,
        "reaction_state": getattr(trigger, "reaction_state", "NO_REACTION"),
        "confirmation_state": getattr(trigger, "confirmation_state", "NONE"),
        "final_executable_bid": quote.get("bid"),
        "final_executable_ask": quote.get("ask"),
        "selected_executable_price": quote.get("executable_price"),
        "final_stop_distance_pips": quote.get("sl_pips"),
        "final_spread_pips": quote.get("spread_pips"),
        "final_net_R": quote.get("net_R"),
        "final_permission": quote.get("allowed"),
        "first_blocker": quote.get("blocker"),
        "arrival_to_execution_ms": quote.get("arrival_to_execution_ms"),
        "price_drift_points": quote.get("price_drift_points"),
        "price_drift_R": quote.get("price_drift_R"),
    }


def emit_incidents(all_trades, data, session_checker, path: str,
                   lookahead_bars: int = 24) -> int:
    """
    Run the live agent's forensic pass over every simulated trade.

    This is what makes the self-diagnosis loop usable: the shipping
    configuration has almost no live history, so the simulator is the only
    place that produces closed trades at volume under the CURRENT rules. The
    same `postmortem.analyse` runs here as in `scalper_agent`, so a diagnosis
    of a simulated book and of a live book use one definition of every mode.

    Note this is post-processing, not a gate — it reads trades that have
    already been decided and written. It therefore does not engage invariant
    #2's mirroring rule, and must not grow a branch that changes a decision.
    """
    from scalper import postmortem as PM

    journal = PM.IncidentJournal(path)
    written = 0
    for t in all_trades:
        frames = data.get(t.symbol)
        if not frames:
            continue
        opened = datetime.fromisoformat(t.open_time)
        closed_at = datetime.fromisoformat(t.close_time)
        sess = session_checker.get_state(opened)
        sl_pips = abs(t.entry - t.sl) / _pip_size(t.symbol) if t.entry else 0.0
        ctx = PM.TradeContext(
            ticket=0,
            symbol=t.symbol,
            direction=t.direction,
            entry=t.entry,
            stop_loss=t.sl,
            tp1=t.tp1,
            outcome="TIMEOUT" if t.exit_reason in ("TIMEOUT", "EOD")
                    else ("WIN_TP1" if t.pnl > 0 else "LOSS"),
            pnl_usd=t.pnl,
            open_time=opened,
            close_time=closed_at,
            trigger_type=t.trigger_type,
            session=sess.window_name,
            regime="SIM",
            confidence="SIM",
            stb_confidence=t.stb_confidence,
            short_term_bias=t.short_term_bias,
            htf_trend=t.htf_trend,
            recent_sweep=t.recent_sweep,
            config_era=DP.CONFIG_ERA,
            lots=t.volume,
            risk_usd=t.risk_usd,
            spread_pips=0.0,
            sl_pips=sl_pips,
        )
        journal.record(PM.analyse(ctx, frames["M5"],
                                  bar_minutes=CONFIRM_TF_MINUTES,
                                  lookahead_bars=lookahead_bars))
        written += 1
    return written


def _pip_size(symbol: str) -> float:
    """Price move of one pip, used only to express the stop in pips."""
    info = mt5.symbol_info(symbol)
    return (info.point * 10) if info and info.point else 1.0


def _load_env_and_connect() -> None:
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    load_dotenv(env_path)
    login = int(os.getenv("MT5_LOGIN", "0"))
    password = os.getenv("MT5_PASSWORD", "")
    server = os.getenv("MT5_SERVER", "")
    # Attach to an already-authorised terminal first. Forcing a re-login with
    # stale credentials both fails *and* logs the running terminal out, so the
    # attach path is tried before the credentialed one — and only accepted when
    # the terminal is on the account we were told to read.
    if mt5.initialize():
        info = mt5.account_info()
        if info and login in (0, info.login):
            print(f"MT5 attached to running terminal — account {info.login} "
                  f"({info.server}), balance {info.balance:.2f} {info.currency}")
            return
        mt5.shutdown()
    if not mt5.initialize(login=login, password=password, server=server):
        raise RuntimeError(
            f"MT5 initialize failed: {mt5.last_error()}. "
            f"Either log the terminal into account {login} ({server}) by hand, "
            f"or correct MT5_PASSWORD in apex_ai/.env."
        )


def _fetch(symbol: str, timeframe: int, dt_from: datetime, dt_to: datetime) -> pd.DataFrame:
    rates = mt5.copy_rates_range(symbol, timeframe, dt_from, dt_to)
    if rates is None or len(rates) == 0:
        return pd.DataFrame()
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df


# ── entry fill model (ledger L-013 stage S1) ─────────────────────────────────

# The simulator used to book P&L against `trigger.entry_price` — the price that
# produced the signal — while `_schedule_exit` already scanned from the entry
# bar "because the position is opened at that bar's open". The two disagreed
# about when the fill happened: exits were timed off the bar, P&L off the
# signal. This closes that, and in doing so stops every backtest in the repo
# assuming a perfect fill (L-012 measured 8 of 11 live fills adverse, median
# realized 1.852R against an intended 2.000R).
#
# SL/TP stay anchored to the signal price on purpose. That is what the live
# agent does (`scalper_agent.py` l.920-921) and re-anchoring them is L-012's
# remedy, which is exit-side and blocked by L-003. So is position sizing, which
# `_calc_volume` takes off the signal price exactly as live does.
#
# There is no "off" position here that is more faithful. The flag exists only
# so the pre-S1 tables can be regenerated for comparison.
FILL_AT_NEXT_BAR_OPEN = True


def _entry_fill_price(df_m5: "pd.DataFrame", entry_idx: int,
                      trigger: SATrigger, model_fill: bool) -> float:
    """
    Price the position actually opens at.

    With the model on this is the open of the first M5 bar at or after the
    decision instant — the earliest price the live agent's market order could
    touch. With it off it is the signal price, reproducing the pre-L-013
    perfect fill.
    """
    if not model_fill:
        return float(trigger.entry_price)
    return float(df_m5.iloc[entry_idx]["open"])


from scalper.exit_manager import ExitOutcome, SimulatedGuardian
from scalper.reclaim_fvg import evaluate_reclaim, entry_in_reclaim_zone
from scalper.m15_fvg_entry import find_fvg_entry, entry_quote_allowed
from scalper.htf_crt import watch_crt, select_crt, crt_quote_allowed
from scalper import session_sweep as SS
import math


def _execution_cost(symbol, volume, bar_spread, pip_value, commission_per_lot,
                    round_trip_cost_price=None, symbol_info=None):
    if round_trip_cost_price is None:
        return bar_spread*pip_value*volume + commission_per_lot*volume
    # Total measured USD/oz cost already includes spread, commission and
    # slippage. This is a debit model, not a tick-level execution simulator.
    info = symbol_info if symbol_info is not None else mt5.symbol_info(symbol)
    if (symbol != "XAUUSD" or info is None or info.trade_contract_size <= 0
            or not math.isfinite(round_trip_cost_price) or round_trip_cost_price < 0):
        raise ValueError("Invalid measured XAUUSD execution-cost inputs")
    return round_trip_cost_price*info.trade_contract_size*volume


def _order_pnl(symbol: str, direction: str, volume: float, entry: float, exit_price: float) -> float:
    order_type = mt5.ORDER_TYPE_BUY if direction == "BULLISH" else mt5.ORDER_TYPE_SELL
    pnl = mt5.order_calc_profit(order_type, symbol, volume, entry, exit_price)
    return float(pnl) if pnl is not None else 0.0


def _pip_value(symbol: str, symbol_info=None) -> float:
    info = symbol_info if symbol_info is not None else mt5.symbol_info(symbol)
    if not info or info.point <= 0:
        return 0.0
    return info.trade_tick_value * 10.0


def _calc_volume(symbol: str, risk_usd: float, entry: float, sl: float,
                 symbol_info=None) -> float:
    info = symbol_info if symbol_info is not None else mt5.symbol_info(symbol)
    if not info or info.point <= 0:
        return 0.0
    sl_pips = abs(entry - sl) / info.point / 10.0
    pip_value = _pip_value(symbol, info)
    if sl_pips <= 0 or pip_value <= 0:
        return 0.0
    volume = risk_usd / (sl_pips * pip_value)
    if volume < info.volume_min:
        return 0.0
    volume = min(volume, info.volume_max)
    volume = round(volume / info.volume_step) * info.volume_step
    return round(volume, 2)


def _trading_day(moment: datetime) -> str:
    """
    Key for the 23:00->23:00 UTC trading day used by the daily reset, so the
    daily loss limit and the consecutive-loss streak roll over when the live
    agent flattens rather than at midnight.
    """
    return (moment - timedelta(hours=EOD_HOUR_UTC)).date().isoformat()


def _eod_cutoff(moment: datetime) -> datetime:
    """The next 23:00 UTC boundary at or after `moment`."""
    same_day = moment.replace(hour=EOD_HOUR_UTC, minute=0, second=0, microsecond=0)
    return same_day if moment <= same_day else same_day + timedelta(days=1)


def _stb_bucket(reason: str) -> str:
    """
    Collapse an STB rejection reason to the branch that produced it.

    A single `STB` counter says a gate is expensive but not which of its five
    independent rules is expensive, and they have opposite justifications: the
    "don't chase" rule is premised on fading, while a continuation trigger's
    whole premise is following. Attribution is what tells the two apart.
    """
    if reason.startswith("Don't chase"):
        return "STB/dont_chase"
    if reason.startswith("Range whipsaw"):
        return "STB/range_guard"
    if "requires direction" in reason:
        return "STB/neutral_needs_direction"
    if "is not a fade type" in reason:
        return "STB/opposes_short_term"
    if reason.startswith("Invalid trigger direction"):
        return "STB/bad_direction"
    return "STB/other"


def _closed(df: pd.DataFrame, now: datetime, bars: int) -> pd.DataFrame:
    """
    The last `bars` COMPLETED rows of `df` as of `now`.

    A bar stamped `now` opens at `now` and is still forming, so the comparison
    is strict. This mirrors `ScalperAgent._get_ohlcv`, which reads from
    position 1 rather than 0 for the same reason.
    """
    return df[df["time"] < pd.Timestamp(now)].iloc[-bars:]


def _closed_tf(df: pd.DataFrame, now: datetime, bars: int,
               tf_minutes: int) -> pd.DataFrame:
    """
    The last `bars` rows of `df` that have genuinely CLOSED as of `now`.

    `_closed` above compares the bar's opening stamp against `now`, which is
    correct only when the bar is one unit long. A bar stamped T on an
    `tf_minutes` frame covers [T, T + tf_minutes) and is not closed until
    T + tf_minutes <= now, so on H4 at 18:00 the bar stamped 16:00 is still
    forming — `_closed` would hand it over as settled.

    Live has no equivalent hole: `_get_ohlcv` reads from `copy_rates_from_pos`
    position 1, which is the last fully closed bar on whatever frame it asks
    for. This helper is what makes the simulator agree with it on frames slower
    than the trigger.

    NOTE: `_closed` is deliberately left in place for the H1/H4 frames feeding
    the short-term-bias and consultation gates. Those gates' behaviour is what
    every existing walk-forward number in docs/RESEARCH_NOTES.md was measured
    against, and silently re-timing them would invalidate that baseline in the
    same change that adds a new gate. The discrepancy is real and is recorded
    as its own ledger row rather than fixed in passing.
    """
    if df is None or len(df) == 0:
        return df
    cutoff = pd.Timestamp(now) - pd.Timedelta(minutes=tf_minutes)
    return df[df["time"] <= cutoff].iloc[-bars:]


def _spread_series(df_m15: pd.DataFrame, floor_pips: float) -> pd.Series:
    """
    Per-bar round-turn spread in pips, taken from the broker's own record.

    MT5 stamps the spread in points on every bar it returns, so the simulator
    does not have to assume one. It previously used a single `--spread-pips`
    constant for the whole run: the default was 2.5 while XAUUSD actually
    averaged 4.78 pips over 2026-05-15 -> 08-23 (median 5.0, p90 6.0). That
    understated the dominant cost by roughly half, and — because spread also
    enters `step3_validate`'s net-R test — admitted setups the live agent
    would have rejected. Both errors push reported results the same way:
    optimistic.

    Some symbols come back with the field zeroed. Where that happens the
    supplied floor is used instead, so a missing feed cannot silently price
    the run at zero cost.
    """
    pips = df_m15["spread"].astype(float) / 10.0
    return pips.where(pips > 0.0, floor_pips)


def _apply_v1_council_gates(
    symbol: str,
    trigger: SATrigger,
    current_price: float,
    df_m15_upto: pd.DataFrame,
    df_h1_upto: pd.DataFrame,
    regime_engine: RegimeEngine,
    structure_engine: StructureEngine,
    manipulation_engine: ManipulationEngine,
    displacement_engine: DisplacementEngine,
    liquidity_engine: LiquidityEngine,
) -> Tuple[Optional[SATrigger], str]:
    if len(df_m15_upto) < 30 or len(df_h1_upto) < 30:
        return None, "WARMUP"

    # The analysis itself is the live agent's own code, imported rather than
    # restated. The re-implementation this replaced ran structure, displacement
    # and regime on M15 while the live consultant ran them on M5, so Gate 1 was
    # applying the same whitelist to a differently-classified regime.
    consult = consult_analyse(
        symbol, df_m15_upto, df_h1_upto,
        structure_engine    = structure_engine,
        displacement_engine = displacement_engine,
        liquidity_engine    = liquidity_engine,
        manipulation_engine = manipulation_engine,
        regime_engine       = regime_engine,
    )

    # Gate 1 — per-trigger regime whitelist.
    if consult.regime == "STRESS":
        return None, "CONSUL_GATE1_STRESS"
    allowed = TRIGGER_REGIME_WHITELIST.get(trigger.trigger_type)
    if allowed is None or consult.regime not in allowed:
        return None, "CONSUL_GATE1_REGIME"

    # Gate 2 — BOS_RETEST requires confirmed institutional displacement.
    if trigger.requires_displacement and not consult.displacement_confirmed:
        return None, "CONSUL_GATE2_DISPLACEMENT"

    # Gate 3 — LIA macro TP2 realignment. Same helper the live agent calls.
    consult = apply_lia_override(
        consult, trigger.direction, trigger.tp1, trigger.tp2, current_price)
    if consult.lia_tp2_override and trigger.fvg_entry is None and trigger.htf_crt is None and trigger.session_sweep is None:
        trigger.tp2 = consult.lia_tp2_override

    return trigger, ""


def _schedule_exit(
    df_m5: pd.DataFrame,
    entry_idx: int,
    trigger: SATrigger,
    entry_time: datetime,
    guardian: Optional[SimulatedGuardian] = None,
    volume: float = 0.0,
) -> Tuple[float, str, str, datetime, Optional[ExitOutcome]]:
    """
    Walk the M5 confirmation frame forward from the entry bar and return the
    first exit the live agent would have taken: stop, TP1, the 6h bar timeout,
    or the 23:00 UTC daily flatten — whichever comes first.

    Within a bar the stop is checked before the target, which is the
    conservative resolution of the intrabar-ambiguity problem.

    When `guardian` is supplied (``--tga-exits``) the walk is delegated to
    `scalper.exit_manager`, which replays the live Trade Guardian's trailing,
    early close, no-progress kill and TP extension over the same bars. That is
    ledger L-003: without it the simulator measures a static-stop strategy the
    account does not run. Default OFF so every stored baseline stays
    byte-comparable — the unmanaged branch below is untouched.
    """
    max_idx = min(entry_idx + TIMEOUT_CONFIRM_BARS, len(df_m5) - 1)
    eod = _eod_cutoff(entry_time)

    if guardian is not None:
        outcome = guardian.run(
            df_m5=df_m5, entry_idx=entry_idx, direction=trigger.direction,
            entry_price=trigger.entry_price, stop_loss=trigger.stop_loss,
            tp1=trigger.tp1, entry_time=entry_time, volume=volume,
            max_idx=max_idx, eod=eod,
            tp_extend_blocked=(trigger.fvg_entry is not None or trigger.htf_crt is not None
                               or trigger.session_sweep is not None))
        return (outcome.exit_price, outcome.result, outcome.exit_reason,
                outcome.close_time, outcome)

    exit_price = trigger.entry_price
    result, reason = "TIMEOUT", "TIMEOUT"
    close_time = df_m5.iloc[max_idx]["time"].to_pydatetime()

    # Starts *at* the entry bar, not after it: the position is opened at that
    # bar's open, so that bar's own range can take the stop or the target.
    for j in range(entry_idx, max_idx + 1):
        bar = df_m5.iloc[j]
        bar_time = bar["time"].to_pydatetime()
        high = float(bar["high"])
        low = float(bar["low"])

        if trigger.direction == "BULLISH":
            if low <= trigger.stop_loss:
                return trigger.stop_loss, "LOSS", "SL", bar_time, None
            if high >= trigger.tp1:
                return trigger.tp1, "WIN_TP1", "TP", bar_time, None
        else:
            if high >= trigger.stop_loss:
                return trigger.stop_loss, "LOSS", "SL", bar_time, None
            if low <= trigger.tp1:
                return trigger.tp1, "WIN_TP1", "TP", bar_time, None

        exit_price = float(bar["close"])
        close_time = bar_time
        if bar_time >= eod:
            return exit_price, "EOD", "EOD", bar_time, None

    return exit_price, result, reason, close_time, None


def run_backtest(
    symbols: List[str],
    dt_from: datetime,
    dt_to: datetime,
    sa_pool: float,
    risk_pct: float,
    max_open_positions: int,
    spread_pips: float,
    enforce_session_windows: bool,
    enforce_news_blackout: bool,
    daily_loss_limit_usd: float = 50.0,
    commission_per_lot: float = 0.0,
    cooldown_enabled: bool = True,
    win_cooldown_minutes: float = 5.0,
    loss_cooldown_policy: str = "NEXT_UTC_HOUR",
    allow_whole_day: bool = False,
    enabled_triggers: Optional[List[str]] = None,
    tga_exits: bool = TGA_EXITS_IN_SIM,
    enabled_sessions: Optional[List[str]] = None,
    ema_band_enabled: bool = EMA_BAND_ENABLED,
    ema_band_mode: str = EMA_BAND_MODE,
    stb_relax_continuation: bool = STB_RELAX_CONTINUATION,
    leg_conf_enabled: bool = LEG_CONF_ENABLED,
    leg_conf_mode: str = LEG_CONF_MODE,
    sweep_wick_filter: bool = SWEEP_WICK_FILTER_ENABLED,
    sweep_wick_ratio: float = SWEEP_WICK_RATIO_MIN,
    reclaim_fvg_enabled: bool = DP.RECLAIM_FVG_ENABLED,
    m15_fvg_entry_enabled: bool = DP.M15_FVG_ENTRY_ENABLED,
    htf_crt_enabled: bool = DP.CRT_ENABLED,
    vp_gate_enabled: bool = VP_GATE_ENABLED,
    vp_gate_mode: str = VP_GATE_MODE,
    vp_poc_band_frac: float = VP_POC_BAND_FRAC,
    va_fade_enabled: bool = VA_FADE_ENABLED,
    rd_gate_enabled: bool = RD_GATE_ENABLED,
    rd_gate_mode: str = RD_GATE_MODE,
    pdr_gate_enabled: bool = PDR_GATE_ENABLED,
    pdr_gate_mode: str = PDR_GATE_MODE,
    vplr_enabled: bool = VPLR_ENABLED,
    vplr_session_override: bool = VPLR_SESSION_OVERRIDE_ENABLED,
    vplr_scope: str = VPLR_SCOPE,
    fill_at_next_bar_open: bool = FILL_AT_NEXT_BAR_OPEN,
    incidents_path: Optional[str] = None,
    crt_confluence_mode: str = DP.CRT_CONFLUENCE_MODE,
    round_trip_cost_price: Optional[float] = None,
    watch_inactive_crt: bool = True,
    session_sweep_enabled: bool = False,
    historical_data: Optional[Dict[str, Dict[str, pd.DataFrame]]] = None,
    historical_symbol_info: Optional[Dict[str, object]] = None,
    candidate_funnel: Optional[CandidateFunnelRecorder] = None,
    sweep_location_policy: Optional[SweepLocationPolicy] = None,
    sweep_research_path: Optional[str] = None,
    market_location_mode: str = DP.MARKET_LOCATION_MODE,
) -> Dict:
    from scalper.crt_confluence import validate_mode
    validate_mode(crt_confluence_mode)
    market_location_mode = str(market_location_mode).upper()
    if market_location_mode not in DP.MARKET_LOCATION_MODES:
        raise ValueError("market_location_mode must be OFF or ACTIVE")
    sweep_location_policy = sweep_location_policy or SweepLocationPolicy()
    if round_trip_cost_price is not None:
        if (not math.isfinite(round_trip_cost_price) or round_trip_cost_price < 0
                or any(s != "XAUUSD" for s in symbols)):
            raise ValueError("Measured price-cost override requires XAUUSD and a finite nonnegative USD/oz cost")
    # Mirrors ScalperAgent's --triggers whitelist (invariant #2). Restricting
    # the set is the only way to measure a trigger that sits behind
    # SWEEP_REJECTION in step2_trigger's first-match priority order.
    trigger_engine = SATriggerEngine(
        enabled_triggers=resolve_enabled_triggers(enabled_triggers,
                                                  vplr_enabled, htf_crt_enabled, session_sweep_enabled),
        sweep_wick_filter=sweep_wick_filter,
        sweep_wick_ratio=sweep_wick_ratio)
    if "SWEEP_REJECTION" in trigger_engine.enabled_triggers:
        if market_location_mode != "ACTIVE":
            raise ValueError(
                "SWEEP_REJECTION replay requires market_location_mode='ACTIVE'")
        if not sweep_location_policy.active:
            raise ValueError(
                "SWEEP_REJECTION replay requires an ACTIVE sweep-location policy; "
                "load config.json explicitly")
    # Mirrors ScalperAgent's VP_LIQUIDITY_REACTION construction (invariant #2):
    # same parameter object, same source, and the Asia allowance handed over
    # only when both the trigger and the override are on.
    # L-003. One Guardian for the whole run: it holds no cross-trade state,
    # only the shared config, so a single instance is correct and avoids
    # rebuilding three engines per position. Its INFO chatter (a "BE deferred"
    # line per unconfirmed bar) would be thousands of lines in a backtest, so
    # the shared TGA logger is quieted here rather than in the module, which
    # the live Guardian also imports.
    guardian = None
    if tga_exits:
        logging.getLogger("TGA").setLevel(logging.WARNING)
        guardian = SimulatedGuardian()
    vplr_params = VPLRParams.from_decision_params(DP)
    vp_window = (VPLR_SESSION_WINDOW_UTC
                 if (vplr_enabled and vplr_session_override) else None)
    # Mirrors ScalperAgent's --sessions whitelist (invariant #2).
    session_checker = SASessionChecker(allow_whole_day=allow_whole_day,
                                       enabled_sessions=enabled_sessions,
                                       vp_window=vp_window)
    regime_engine = RegimeEngine()
    structure_engine = StructureEngine(swing_lookback=5)
    manipulation_engine = ManipulationEngine()
    displacement_engine = DisplacementEngine()
    liquidity_engine = LiquidityEngine(swing_lookback=5)
    stb_filter = ShortTermBiasFilter(relax_continuation=stb_relax_continuation)
    ema_filter = EMABandFilter(mode=ema_band_mode)
    # Mirrors ScalperAgent's previous-day-range gate (invariant #2).
    pdr_gate = PDRGate(
        mode          = pdr_gate_mode,
        discount_frac = PDR_DISCOUNT_FRAC,
        premium_frac  = PDR_PREMIUM_FRAC,
    )
    # Mirrors ScalperAgent's regime-direction gate (invariant #2).
    rd_gate = RegimeDirectionGate(
        mode              = rd_gate_mode,
        regime_classifier = RegimeClassifier(
            lookback        = VP_REGIME_LOOKBACK,
            smoothing       = VP_REGIME_SMOOTHING,
            trend_threshold = VP_REGIME_TREND_THRESHOLD,
            vol_threshold   = VP_REGIME_VOL_THRESHOLD,
            er_threshold    = VP_REGIME_ER_THRESHOLD,
        ),
    )
    # Mirrors ScalperAgent's VP gate (invariant #2). Every parameter is
    # imported from decision_params, so the two constructions cannot drift.
    # Observation only (§13.10): counted, never branched on.
    leg_conf_labels: Counter = Counter()
    leg_conf_admitted: Counter = Counter()
    vp_gate = VolumeProfileGate(
        mode                = vp_gate_mode,
        profile_bars        = VP_PROFILE_BARS,
        target_bins         = VP_TARGET_BINS,
        value_area_pct      = VP_VALUE_AREA_PCT,
        poc_band_frac       = vp_poc_band_frac,
        edge_tolerance_frac = VP_EDGE_TOLERANCE_FRAC,
        regime_classifier   = RegimeClassifier(
            lookback        = VP_REGIME_LOOKBACK,
            smoothing       = VP_REGIME_SMOOTHING,
            trend_threshold = VP_REGIME_TREND_THRESHOLD,
            vol_threshold   = VP_REGIME_VOL_THRESHOLD,
            er_threshold    = VP_REGIME_ER_THRESHOLD,
        ),
    )
    crg = SACRG()
    cooldown = SACooldown(
        win_minutes=win_cooldown_minutes,
        loss_policy=loss_cooldown_policy,
        enabled=cooldown_enabled,
    )
    # Live uses 30 minutes before / 15 after; this previously said 15/15.
    news_guard = NewsGuard(
        events_file=str(Path(__file__).parent / "news_events.json"),
        blackout_before_min=NEWS_BLACKOUT_BEFORE_MIN,
        blackout_after_min=NEWS_BLACKOUT_AFTER_MIN,
        impact_levels=list(NEWS_IMPACT_LEVELS),
    )

    # ── Prefetch every frame for every symbol ────────────────────────────────
    data: Dict[str, Dict[str, pd.DataFrame]] = {}
    m5_index: Dict[str, pd.Index] = {}
    spread_by_symbol: Dict[str, pd.Series] = {}
    symbol_info_by_symbol = {}
    events: List[Tuple[datetime, str, int]] = []
    session_indexes = {}
    session_used = set()
    session_funnel = Counter()
    sweep_research_rows = {}

    # Every decision frame is fetched with a lead-in *before* the requested
    # start so that the first tradable bar already has the same history behind
    # it that the live agent would have had. Without it the run would open with
    # every long-lookback gate failing on insufficient data — the H4 leg of the
    # bias filter needs 120 bars (~20 trading days) and the EMA band needs 200
    # H1 bars (~8 days) — which silently suppresses the opening weeks of the
    # window rather than reporting them as no-trade.
    warmup_from = dt_from - timedelta(days=WARMUP_LEAD_DAYS)

    for symbol in symbols:
        if historical_data is not None:
            info = (historical_symbol_info or {}).get(symbol)
            if info is None and symbol == "XAUUSD":
                info = SimpleNamespace(point=0.01, trade_tick_size=0.01,
                    trade_tick_value=1.0, volume_min=0.01, volume_step=0.01,
                    volume_max=100.0, trade_contract_size=100.0)
            frames_in = historical_data.get(symbol, {})
            df_m5 = frames_in.get("M5", pd.DataFrame()).copy()
            df_m15 = frames_in.get("M15", pd.DataFrame()).copy()
            df_h1 = frames_in.get("H1", pd.DataFrame()).copy()
            df_h4 = frames_in.get("H4", pd.DataFrame()).copy()
            df_d1 = frames_in.get("D1", pd.DataFrame()).copy()
            df_w1 = frames_in.get("W1", pd.DataFrame()).copy()
        else:
            mt5.symbol_select(symbol, True)
            info = mt5.symbol_info(symbol)
            df_m5 = _fetch(symbol, mt5.TIMEFRAME_M5, warmup_from, dt_to)
            df_m15 = _fetch(symbol, mt5.TIMEFRAME_M15, warmup_from, dt_to)
            df_h1 = _fetch(symbol, mt5.TIMEFRAME_H1, warmup_from, dt_to)
            df_h4 = _fetch(symbol, mt5.TIMEFRAME_H4, warmup_from, dt_to)
            df_d1 = _fetch(symbol, mt5.TIMEFRAME_D1, warmup_from, dt_to)
            # W1 is the macro anchor, so it needs the same long history that
            # live ``copy_rates_from_pos`` supplies; the generic 45-day gate
            # warmup would otherwise make replay unable to form a W1 profile.
            w1_warmup_from = dt_from - timedelta(
                days=max(WARMUP_LEAD_DAYS, DP.MARKET_LOCATION_W1_BARS * 7 + 14))
            df_w1 = _fetch(symbol, mt5.TIMEFRAME_W1, w1_warmup_from, dt_to)
        if (info is None or info.point <= 0 or info.trade_tick_value <= 0
                or info.volume_min <= 0 or info.volume_step <= 0
                or info.volume_max < info.volume_min
                or (session_sweep_enabled and info.trade_tick_size <= 0)
                or (round_trip_cost_price is not None
                    and getattr(info, "trade_contract_size", 0) <= 0)):
            raise RuntimeError(
                f"Missing or invalid MT5 symbol metadata for {symbol} before replay: "
                f"{mt5.last_error()}")
        # MT5 metadata is contract data, not historical state. Snapshot it once:
        # long event loops must not become invalid because a later terminal API
        # call transiently returns None.
        symbol_info_by_symbol[symbol] = info
        if df_m5.empty or df_m15.empty or df_h1.empty:
            continue
        data[symbol] = {"M5": df_m5, "M15": df_m15, "H1": df_h1, "H4": df_h4,
                        "D1": df_d1, "W1": df_w1}
        if session_sweep_enabled and symbol == "XAUUSD" and historical_data is None:
            session_indexes[symbol] = SS.reconstruct_levels(
                _fetch(symbol, mt5.TIMEFRAME_M1, warmup_from, dt_to))
        if htf_crt_enabled:
            # Native calendar bars; warm the ten-bar monthly window without
            # fetching twelve months of M5 data.
            for tf, code in DP.CRT_TIMEFRAMES.items():
                data[symbol][tf] = _fetch(symbol, code,
                    dt_from - timedelta(days=400), dt_to)
        m5_index[symbol] = pd.Index(df_m5["time"])
        spread_by_symbol[symbol] = _spread_series(df_m15, spread_pips)

        # Decisions are only scored inside the requested window; the lead-in
        # exists to feed the gates, not to generate trades.
        start_ts = pd.Timestamp(dt_from)
        if reclaim_fvg_enabled or m15_fvg_entry_enabled or htf_crt_enabled or session_sweep_enabled:
            # A return confirmed at :05/:10 must be observable, not postponed
            # to the next M15 bar when it is stale. The trigger frame remains
            # M15; only the decision clock follows completed M5 confirmations.
            trigger_times = pd.Index(df_m15["time"])
            for bar_time in df_m5.loc[df_m5["time"] >= start_ts, "time"]:
                i = int(trigger_times.searchsorted(bar_time, side="right"))-1
                if i >= TRIGGER_BARS:
                    events.append((bar_time.to_pydatetime(), symbol, i))
        else:
            for i in range(TRIGGER_BARS, len(df_m15)):
                bar_time = df_m15.iloc[i]["time"]
                if bar_time < start_ts:
                    continue
                events.append((bar_time.to_pydatetime(), symbol, i))

    # Single chronological timeline across all symbols — the live agent's
    # cooldown, balance and position cap are global, so the replay must be too.
    events.sort(key=lambda e: (e[0], e[1]))

    # One ledger per symbol keeps profile lifecycle history isolated while the
    # pure snapshot builder remains identical to the live path.
    market_location_engines = ({
        symbol: MarketLocationEngine(config=MarketLocationConfig(
            profile_target_bins=DP.MARKET_LOCATION_VP_ROWS))
        for symbol in data}
                               if market_location_mode == "ACTIVE" else {})
    all_trades: List[SimTrade] = []
    rejects: Counter = Counter()
    # Per-trigger instrumentation. A zero-trade result is not a negative
    # result — it can mean the detector never fired, or that it fired and one
    # specific gate consumed every candidate. `detected` counts what step2
    # emitted before any post-detection gate; `rejects_by_trigger` attributes
    # each downstream rejection to the trigger type that produced it.
    detected: Counter = Counter()
    rejects_by_trigger: Counter = Counter()
    balance = sa_pool
    equity_curve: List[Tuple[str, float]] = []

    open_trades: Dict[str, dict] = {}       # symbol -> scheduled trade
    pending_exits: List[dict] = []          # sorted by exit_time
    session_open_prices: Dict[str, float] = {}
    day_key = None
    daily_pnl = 0.0
    consecutive_losses = 0
    reclaim_last_closes = {}
    crt_used = set()
    crt_watch = Counter()
    crt_candidates = []

    def flush_exits(upto: datetime) -> None:
        nonlocal balance, daily_pnl, consecutive_losses
        while pending_exits and pending_exits[0]["close_time"] <= upto:
            done = pending_exits.pop(0)
            trade: SimTrade = done["trade"]
            balance += trade.pnl
            daily_pnl += trade.pnl
            if trade.pnl > 0:
                consecutive_losses = 0
            else:
                consecutive_losses += 1
            cooldown.record_trade_result(trade.pnl, now=done["close_time"])
            reclaim_last_closes[(trade.symbol, trade.direction)] = done["close_time"]
            open_trades.pop(trade.symbol, None)
            all_trades.append(trade)
            equity_curve.append((trade.close_time, round(balance, 2)))

    for now, symbol, i in events:
        flush_exits(now)
        # Same first watch as live, including IDLE/cooldown periods. Future
        # HTF rows are filtered by calendar close inside the shared evaluator.
        frames = data[symbol]
        info = symbol_info_by_symbol[symbol]
        crt_plan = None
        if htf_crt_enabled and (watch_inactive_crt or not enforce_session_windows
                or session_checker.get_state(now).in_window
                or (vplr_enabled and vplr_session_override)):
            qi = int(m5_index[symbol].searchsorted(pd.Timestamp(now), side="left"))
            if qi < len(frames["M5"]):
                q = float(frames["M5"].iloc[qi]["open"])
                plans = watch_crt(
                    frames["M15"].iloc[max(0, i-TRIGGER_BARS):i],
                    frames["M5"].iloc[max(0, qi-CONFIRM_BARS):qi],
                    frames, q, q, now, crt_used, reclaim_last_closes, symbol,
                    crt_confluence_mode)
                crt_plan = select_crt(plans)
                for p in plans:
                    crt_watch[f"{p.timeframe}:{p.direction}:{p.reason}"] += 1
                    if p.confluence and p.confluence["baseline_ready"]:
                        crt_candidates.append(dict(decision_at=now.isoformat(), symbol=symbol, **p.record()))

        # Daily reset — the 23:00 UTC flatten also clears the pool's daily P&L,
        # the streak counter and the cooldown.
        key = _trading_day(now)
        if key != day_key:
            day_key = key
            daily_pnl = 0.0
            consecutive_losses = 0
            cooldown.reset()
            session_open_prices.clear()

        # Session gating. Mirrors the live state machine: normally IDLE outside
        # a window, but when the trigger-scoped VP allowance is open the bar is
        # scanned with the trigger set narrowed to VP_LIQUIDITY_REACTION.
        # Live's equivalent is SAState.VP_ONLY (behavior_state.py).
        vp_only_bar = False
        if enforce_session_windows and not session_checker.get_state(now).in_window:
            if vplr_enabled and session_checker.vp_window_open(now):
                vp_only_bar = True
            else:
                rejects["SESSION"] += 1
                continue

        blackout = bool(news_guard.active_blackout(now_utc=now, symbol=symbol)) \
            if enforce_news_blackout else False
        if blackout:
            rejects["NEWS"] += 1
            continue

        if daily_loss_limit_usd > 0 and -daily_pnl >= daily_loss_limit_usd:
            rejects["DAILY_LOSS_LIMIT"] += 1
            continue

        if not cooldown.can_enter(now):
            rejects["COOLDOWN"] += 1
            continue

        if symbol in open_trades:
            rejects["SYMBOL_DEDUP"] += 1
            continue

        if len(open_trades) >= max_open_positions:
            rejects["MAX_OPEN"] += 1
            continue

        # Decision frames hold only *completed* bars. `now` is the open time of
        # M15 bar `i`, so bar i is still forming and bars 0..i-1 are the most
        # recent settled history. Slicing to `i + 1` — as this file used to —
        # handed the simulator that bar's final high/low/close, which is
        # look-ahead: the live agent sees the same bar half-built.
        frames = data[symbol]
        # Slice to exactly the window the live agent fetches. This used to be
        # `iloc[:i]` — the entire history to date, growing to thousands of bars
        # — while live passed 150. The structure and displacement engines scan
        # what they are given, so the two were analysing different data.
        df_m15_upto = frames["M15"].iloc[max(0, i - TRIGGER_BARS):i]
        df_m5_upto = (frames["M5"][frames["M5"]["time"] < now]
                      .iloc[-CONFIRM_BARS:])
        if len(df_m15_upto) < 100 or len(df_m5_upto) < 100:
            rejects["WARMUP"] += 1
            continue

        if symbol not in session_open_prices:
            session_open_prices[symbol] = float(df_m15_upto["open"].iloc[-1])

        liq = trigger_engine.step1_liquidity(df_m15_upto, symbol)
        vp_profile = None
        if va_fade_enabled:
            vp_profile = vp_gate.build_profile(
                _closed_tf(frames['H4'], now, VP_PROFILE_FETCH_BARS, 240))

        # Shared causal VP + structural S/R snapshot.  The trigger engine
        # consumes it through the same permission layer as live; all later
        # risk, geometry, and execution gates remain unchanged.
        market_location = None
        if market_location_mode == "ACTIVE":
            location_m15 = frames["M15"].iloc[
                max(0, i - DP.MARKET_LOCATION_M15_DETAIL_BARS):i]
            location_h1 = (frames["H1"][frames["H1"]["time"] < now]
                           .iloc[-DP.MARKET_LOCATION_H1_DETAIL_BARS:])
            location_m5 = (frames["M5"][frames["M5"]["time"] < now]
                           .iloc[-DP.MARKET_LOCATION_M5_DETAIL_BARS:])
            market_location = market_location_engines[symbol].snapshot(
                symbol=symbol,
                current_price=float(df_m15_upto["close"].iloc[-1]),
                df_w1=frames.get("W1"), df_d1=frames.get("D1"),
                df_h4=frames.get("H4"), df_m15=location_m15,
                df_h1=location_h1, df_m5=location_m5, as_of=now)

        # ── VP_LEG_CONFLUENCE legs — mirrors _scan_symbol (invariant #2) ────
        # Built once per symbol per bar, before any trigger is selected, so the
        # same two legs judge every candidate on this bar. `_closed_tf` for the
        # same reason as every other H4 read: an opening stamp in the past does
        # not mean the bar has closed, and a profile over a forming bar
        # repaints.
        leg_pair = None
        if leg_conf_enabled:
            leg_pair = build_leg_pair(
                _closed_tf(frames['H4'], now, VP_PROFILE_FETCH_BARS, 240),
                symbol,
                swing_lookback=LEG_CONF_SWING_LOOKBACK,
                min_leg_bars=LEG_CONF_MIN_LEG_BARS,
                min_leg_atr=LEG_CONF_MIN_LEG_ATR,
                atr_period=LEG_CONF_ATR_PERIOD,
                target_bins=LEG_CONF_TARGET_BINS,
                value_area_pct=LEG_CONF_VALUE_AREA_PCT,
                node_stddev_mult=LEG_CONF_NODE_STDDEV_MULT)

        # ── VP_LIQUIDITY_REACTION context — mirrors _scan_symbol ─────────────
        # `_closed_tf`, not `_closed`, on both the H4 and D1 frames: a bar whose
        # opening stamp is in the past has not necessarily closed, and an
        # anchored profile built over a forming H4 bar would repaint (L-007).
        vplr_ctx = None
        # Mirrors _scan_symbol's scope test exactly (invariant #2).
        if vplr_enabled and (vplr_scope == "ALL_SESSIONS" or vp_only_bar):
            vplr_ctx = vplr_context(
                _closed_tf(frames['H4'], now, VPLR_PROFILE_FETCH_BARS, 240),
                symbol, vplr_params,
                df_d1_closed=_closed_tf(frames.get('D1'), now, PDR_BARS, 1440),
                htf_trend=stb_filter._htf_trend(
                    symbol, _closed_tf(frames['H1'], now, HTF_BARS, 60), None),
            )

        session_plan = None
        if session_sweep_enabled and not vp_only_bar and symbol == "XAUUSD":
            quote_idx = int(m5_index[symbol].searchsorted(pd.Timestamp(now), side="left"))
            if quote_idx < len(frames["M5"]):
                quote = float(frames["M5"].iloc[quote_idx]["open"])
                sspread = float(spread_by_symbol[symbol].iloc[i]) * info.point * 10.
                session_plan = SS.evaluate(SS.as_of(session_indexes.get(symbol, []), now),
                    df_m5_upto, quote, quote+sspread, now, session_used,
                    info.trade_tick_size)
                session_funnel[session_plan.reason] += 1

        m15_fvg_plan = None
        if m15_fvg_entry_enabled and not vp_only_bar:
            quote_idx = int(m5_index[symbol].searchsorted(pd.Timestamp(now), side="left"))
            if quote_idx < len(frames["M5"]):
                quote = float(frames["M5"].iloc[quote_idx]["open"])
                m15_fvg_plan = find_fvg_entry(
                    df_m15_upto, df_m5_upto, quote, quote, now,
                    reclaim_last_closes, symbol)
                if not m15_fvg_plan.allow and m15_fvg_plan.reason != "M15_FVG_WAIT_PRICE":
                    rejects[m15_fvg_plan.reason] += 1
        if vp_only_bar:
            saved_triggers = trigger_engine.enabled_triggers
            trigger_engine.enabled_triggers = {"VP_LIQUIDITY_REACTION"}
        funnel_seen = []
        try:
            trigger = trigger_engine.step2_trigger(
                df_m15_upto, df_m5_upto, liq, symbol,
                session_open_prices[symbol],
                profile=vp_profile,
                edge_tolerance_frac=VP_EDGE_TOLERANCE_FRAC,
                poc_band_frac=VP_POC_BAND_FRAC,
                vplr_ctx=vplr_ctx, m15_fvg_entry=m15_fvg_plan,
                htf_crt=crt_plan if not vp_only_bar else None,
                session_sweep=session_plan,
                candidate_observer=funnel_seen.append,
                market_location=market_location,
                market_location_mode=market_location_mode,
                sweep_location_active=sweep_location_policy.active,
                location_context_builder=(lambda t: build_sweep_location_context(
                    symbol=symbol, trigger=t, df_trigger=df_m15_upto, liquidity=liq,
                    now=now, df_d1=_closed_tf(frames.get("D1"), now, PDR_BARS, 1440),
                    df_h1=_closed_tf(frames["H1"], now, HTF_BARS, 60),
                    df_h4=_closed_tf(frames["H4"], now, H4_BARS, 240),
                    session_tracker=stb_filter.liq_tracker,
                    vp_profile=vp_profile, m15_fvg=m15_fvg_plan,
                    policy=sweep_location_policy, point=info.point)
                    if (candidate_funnel or sweep_location_policy.active or sweep_research_path) else None))
        finally:
            if vp_only_bar:
                trigger_engine.enabled_triggers = saved_triggers

        if not trigger.detected:
            if candidate_funnel is not None and funnel_seen:
                completed_bar = df_m15_upto["time"].iloc[-1]
                funnel_ids = [(t, candidate_funnel.observe_trigger(
                    t, symbol, completed_bar, now)) for t in funnel_seen]
                for observed, cid in funnel_ids:
                    if cid:
                        candidate_funnel.record_location_result(
                            cid, observed, completed_bar, now)
                candidate_funnel.record_selection(
                    funnel_ids, trigger, completed_bar_ts=completed_bar,
                    evaluation_ts=now)
            rejects["NO_TRIGGER"] += 1
            if vplr_ctx is not None:
                probe = vplr_detect(df_m15_upto, df_m5_upto, vplr_ctx, liq)
                rejects[f"VPLR_FUNNEL:{probe.reject_reason}"] += 1
            continue
        if sweep_research_path and funnel_seen:
            completed_bar = df_m15_upto["time"].iloc[-1]
            entry_idx = int(m5_index[symbol].searchsorted(pd.Timestamp(now), side="left"))
            spread_price = (float(spread_by_symbol[symbol].iloc[i - 1])
                            * info.point * 10.0)
            for observed in funnel_seen:
                if observed.trigger_type != "SWEEP_REJECTION":
                    continue
                cid = candidate_id_for_trigger(symbol, observed, completed_bar)
                if cid in sweep_research_rows:
                    continue
                outcome = evaluate_sweep(
                    trigger=observed, df_m5=frames["M5"], entry_idx=entry_idx,
                    fill_at_next_bar_open=fill_at_next_bar_open,
                    spread_price=spread_price,
                    round_trip_cost_price=round_trip_cost_price)
                ctx = getattr(observed, "location_context", None)
                sweep_research_rows[cid] = {
                    "candidate_id": cid, "symbol": symbol,
                    "decision_time": completed_bar.isoformat(),
                    "direction": observed.direction,
                    "swept_level": observed.swept_level,
                    "production_winner": trigger.trigger_type,
                    "matched_triggers": "|".join(observed.matched_triggers),
                    "location_context": (json.dumps(ctx.record(), sort_keys=True)
                                          if ctx is not None else ""),
                    **outcome.record(),
                }
        selected_id = None
        if candidate_funnel and funnel_seen:
            completed_bar = df_m15_upto["time"].iloc[-1]
            funnel_ids = [(t, candidate_funnel.observe_trigger(
                t, symbol, completed_bar, now)) for t in funnel_seen]
            candidate_funnel.record_selection(
                funnel_ids, trigger, completed_bar_ts=completed_bar,
                evaluation_ts=now)
            for observed, cid in funnel_ids:
                if cid:
                    candidate_funnel.record_location_result(
                        cid, observed, completed_bar, now)
            selected_id = next((cid for observed, cid in funnel_ids
                                if observed is trigger), None)
            if selected_id:
                for observed, cid in funnel_ids:
                    if cid and observed is not trigger:
                        permission = getattr(observed, "location_permission", None)
                        if permission is not None and not getattr(permission, "executable", False):
                            continue
                        candidate_funnel.preempt(
                            cid, selected_id, trigger.trigger_type)
        detected[trigger.trigger_type] += 1
        ttype = trigger.trigger_type

        # Live reads this off the trigger frame's last bar (`df_m5` there is
        # the M15 stack under its historical variable name), so mirror that.
        current_price = (trigger.entry_price if (trigger.fvg_entry or trigger.htf_crt or trigger.session_sweep) else
                         float(df_m15_upto["close"].iloc[-1]))

        # The broker's own spread for the bar being decided on, rather than one
        # constant for the whole run. Bar i is forming, so i-1 is the latest
        # settled quote — the same bar `current_price` comes from.
        bar_spread = float(spread_by_symbol[symbol].iloc[i - 1])

        if reclaim_fvg_enabled and trigger.session_sweep is None:
            gate_idx = int(m5_index[symbol].searchsorted(pd.Timestamp(now), side="left"))
            if gate_idx >= len(frames["M5"]):
                rejects["NO_M5_BAR"] += 1
                continue
            gate_price = _entry_fill_price(frames["M5"], gate_idx, trigger,
                                           fill_at_next_bar_open)
            trigger.reclaim = evaluate_reclaim(
                trigger.direction, df_m15_upto, df_m5_upto, gate_price,
                trigger.stop_loss, trigger.tp1, now,
                reclaim_last_closes.get((symbol, trigger.direction)))
            if not trigger.reclaim.allow:
                rejects[trigger.reclaim.reason] += 1
                rejects_by_trigger[f"{trigger.reclaim.reason}:{ttype}"] += 1
                continue

        # ── H1 EMA(18) band — mirrors _scan_symbol's first post-detection gate ─
        if ema_band_enabled:
            band = ema_filter.check(
                trigger_direction = trigger.direction,
                price             = current_price,
                df_h1_closed      = _closed(frames["H1"], now, EMA_BAND_BARS),
            )
            if not band.allow:
                rejects["EMA_BAND"] += 1
                rejects_by_trigger[f"EMA_BAND:{ttype}"] += 1
                continue

        # ── Previous-day-range gate — mirrors _scan_symbol ───────────────
        # `_closed_tf` with tf_minutes=1440, not `_closed`: a daily bar stamped
        # today 00:00 has not closed until tomorrow 00:00, so `_closed` would
        # hand over today's forming bar as if it were yesterday's settled one
        # and PDH/PDL would repaint through the session. Live avoids the same
        # hole by reading `copy_rates_from_pos(..., D1, 1, n)`.
        if pdr_gate_enabled:
            pdr = pdr_gate.check(
                trigger_direction = trigger.direction,
                price             = current_price,
                df_d1_closed      = _closed_tf(frames.get('D1'), now,
                                               PDR_BARS, 1440),
            )
            if not pdr.allow:
                rejects["PDR_GATE"] += 1
                rejects_by_trigger[f"PDR_GATE:{ttype}"] += 1
                continue

        # ── Regime-direction gate — mirrors _scan_symbol ─────────────────
        if rd_gate_enabled:
            rd = rd_gate.check(
                trigger.direction,
                _closed_tf(frames['H1'], now, RD_REGIME_BARS, 60))
            if not rd.allow:
                rejects["RD_GATE"] += 1
                rejects_by_trigger[f"RD_GATE:{ttype}"] += 1
                continue

        # ── H4 value-area location gate — mirrors _scan_symbol ───────────
        # Both frames use _closed_tf, not _closed: on H4 and H1 a bar's
        # opening stamp being in the past does not mean the bar has closed,
        # and a profile built over a forming bar repaints.
        if vp_gate_enabled:
            vp = vp_gate.check(
                trigger_direction = trigger.direction,
                price             = current_price,
                df_h4_closed      = _closed_tf(frames['H4'], now,
                                               VP_PROFILE_FETCH_BARS, 240),
                df_regime_closed  = _closed_tf(frames['H1'], now,
                                               VP_REGIME_BARS, 60),
            )
            if not vp.allow:
                rejects["VP_GATE"] += 1
                rejects_by_trigger[f"VP_GATE:{ttype}"] += 1
                continue

        # ── VP_LEG_CONFLUENCE location filter — mirrors _scan_symbol ────────
        # A veto only: direction, stop and target stay with the trigger. It is
        # placed after the existing location gates and before STB so it sees
        # the same candidate set they do.
        if leg_conf_enabled:
            lc = leg_conf_evaluate(current_price, leg_pair, leg_conf_mode,
                                   LEG_CONF_ZONE_ATR, LEG_CONF_LVN_ATR)
            leg_conf_labels[lc.label] += 1
            if not lc.admit:
                rejects[lc.reason()] += 1
                rejects_by_trigger[f"{lc.reason()}:{ttype}"] += 1
                continue
            leg_conf_admitted[lc.label] += 1

        # ── Short-term bias gate — mirrors _scan_symbol's STB block ──────────
        stb = stb_filter.check(
            symbol            = symbol,
            trigger_direction = trigger.direction,
            trigger_type      = trigger.trigger_type,
            current_price     = current_price,
            df_m5             = df_m15_upto,
            df_h1             = _closed(frames["H1"], now, HTF_BARS),
            df_h4             = _closed(frames["H4"], now, H4_BARS),
            now               = now,
        )
        if not stb.allow:
            rejects["STB"] += 1
            bucket = _stb_bucket(stb.reason)
            rejects[bucket] += 1
            rejects_by_trigger[f"{bucket}:{ttype}"] += 1
            continue

        if not DP.entry_confidence_allowed(trigger.confidence, stb.confidence):
            rejects["CONFIDENCE"] += 1
            rejects_by_trigger[f"CONFIDENCE:{ttype}"] += 1
            continue

        # ── Thin-liquidity filter ────────────────────────────────────────────
        in_thin = any(start <= now.hour < end for (start, end) in THIN_LIQUIDITY_HOURS_UTC)
        if in_thin and stb.confidence != THIN_LIQ_REQUIRED_CONFIDENCE:
            rejects["THIN_LIQ"] += 1
            rejects_by_trigger[f"THIN_LIQ:{ttype}"] += 1
            continue

        trigger, why = _apply_v1_council_gates(
            symbol, trigger, current_price, df_m15_upto,
            _closed(frames["H1"], now, CONSULT_HTF_BARS),
            regime_engine, structure_engine, manipulation_engine,
            displacement_engine, liquidity_engine,
        )
        if trigger is None:
            rejects[why] += 1
            rejects_by_trigger[f"{why}:{ttype}"] += 1
            continue

        sl_pips = (abs(trigger.entry_price - trigger.stop_loss)
                   / info.point / 10.0)
        valid, _ = trigger_engine.step3_validate(
            trigger, bar_spread, symbol, sl_pips=sl_pips)
        if not valid:
            rejects["STEP3"] += 1
            rejects_by_trigger[f"STEP3:{ttype}"] += 1
            continue

        crg_decision = crg.check(
            sa_daily_loss_usd     = -daily_pnl if daily_pnl < 0 else 0.0,
            sa_max_daily_loss_usd = daily_loss_limit_usd,
            sa_open_positions     = len(open_trades),
            sa_consecutive_losses = consecutive_losses,
            current_spread_pips   = bar_spread,
            max_spread_pips       = trigger_engine._max_spread(symbol),
            main_account_dd_pct   = 0.0,
            news_blackout_active  = blackout,
            now                   = now,
        )
        if not crg_decision.approved:
            rejects["CRG"] += 1
            rejects_by_trigger[f"CRG:{ttype}"] += 1
            continue

        risk_usd = balance * risk_pct
        volume = _calc_volume(symbol, risk_usd, trigger.entry_price,
                              trigger.stop_loss, info)
        if volume <= 0:
            rejects["LOT_FLOOR"] += 1
            rejects_by_trigger[f"LOT_FLOOR:{ttype}"] += 1
            continue

        # Fill on the first M5 bar at or after the decision instant.
        entry_idx = int(m5_index[symbol].searchsorted(pd.Timestamp(now), side="left"))
        if entry_idx >= len(frames["M5"]):
            rejects["NO_M5_BAR"] += 1
            rejects_by_trigger[f"NO_M5_BAR:{ttype}"] += 1
            continue

        fill_price = _entry_fill_price(frames["M5"], entry_idx, trigger,
                                       fill_at_next_bar_open)
        if trigger.session_sweep:
            # Generic replay books bid-price P&L and debits spread later.
            # Validate and size this trigger on the executable side of quote.
            execution_price = fill_price + (bar_spread*info.point*10.
                              if trigger.direction == "BULLISH" else 0.)
            if not SS.quote_allowed(trigger.session_sweep, execution_price, now):
                rejects["SESSION_SWEEP_QUOTE_MOVED"] += 1
                continue
            quoted = replace(trigger, entry_price=execution_price)
            valid, _ = trigger_engine.step3_validate(quoted, bar_spread, symbol,
                sl_pips=abs(execution_price-trigger.stop_loss)/info.point/10.)
            if not valid:
                rejects["SESSION_SWEEP_FINAL_COST"] += 1
                continue
            volume = min(volume, _calc_volume(symbol, risk_usd, execution_price,
                                              trigger.stop_loss, info))
            if volume <= 0:
                rejects["SESSION_SWEEP_LOT_FLOOR"] += 1
                continue
        fvg_plan = trigger.htf_crt or trigger.fvg_entry
        quote_guard = ((lambda p, q: crt_quote_allowed(p, q, now))
                       if trigger.htf_crt else entry_quote_allowed)
        family = "CRT" if trigger.htf_crt else "M15_FVG"
        if fvg_plan is not None and not quote_guard(fvg_plan, fill_price):
            rejects[f"{family}_QUOTE_MOVED"] += 1
            continue
        if fvg_plan is not None:
            valid, _ = trigger_engine.step3_validate(
                replace(trigger, entry_price=fill_price), bar_spread, symbol,
                sl_pips=abs(fill_price-trigger.stop_loss)/info.point/10.)
            if not valid:
                rejects[f"{family}_FINAL_COST_GATE"] += 1
                continue
        if (reclaim_fvg_enabled and trigger.session_sweep is None
                and not entry_in_reclaim_zone(trigger.reclaim, fill_price)):
            rejects["RECLAIM_QUOTE_MOVED"] += 1
            rejects_by_trigger[f"RECLAIM_QUOTE_MOVED:{ttype}"] += 1
            continue

        # Replay the same final sweep contract as live. The M5 open is the
        # simulated bid; BUYs execute on ask and SELLs on bid.
        if trigger.trigger_type == "SWEEP_REJECTION":
            trigger.decision_time = now
            trigger.candidate_id = candidate_id_for_trigger(
                symbol, trigger, df_m15_upto["time"].iloc[-1])
            signal_spread_price = float(bar_spread) * info.point * 10.0
            trigger.signal_bid = float(trigger.entry_price)
            trigger.signal_ask = float(trigger.entry_price + signal_spread_price)
            trigger.signal_spread_pips = float(bar_spread)
            signal_sl_pips = abs(trigger.entry_price - trigger.stop_loss) / info.point / 10.0
            signal_reward_pips = abs(trigger.tp1 - trigger.entry_price) / info.point / 10.0
            trigger.signal_net_R = ((signal_reward_pips - 2.0 * bar_spread) /
                                    (signal_sl_pips + 2.0 * bar_spread + 1e-10)
                                    if signal_sl_pips > 0 else None)
            original_context = getattr(trigger, "location_context", None)
            original_permission = getattr(trigger, "location_permission", None)
            location_blocker = None
            if (original_context is None or original_permission is None or
                    not getattr(original_permission, "frozen", None) or
                    not getattr(original_context, "liquidity_pool_id", None)):
                location_blocker = "FINAL_LOCATION_NOT_FROZEN"
            else:
                fill_time = frames["M5"].iloc[entry_idx]["time"]
                final_context = build_sweep_location_context(
                    symbol=symbol, trigger=trigger,
                    df_trigger=frames["M15"].iloc[:i], now=fill_time,
                    df_d1=frames.get("D1"), df_h1=frames.get("H1"),
                    df_h4=frames.get("H4"), df_w1=frames.get("W1"),
                    policy=sweep_location_policy, point=info.point)
                final_permission = build_location_permission(
                    market_location, direction=trigger.direction,
                    trigger_type="SWEEP_REJECTION",
                    swept_level=trigger.swept_level,
                    triggered_at=original_permission.triggered_at,
                    frozen_location=original_permission.frozen)
                final_permission = evaluate_sweep_reaction(
                    final_permission,
                    frames["M5"][frames["M5"]["time"] < fill_time],
                    sweep_time=getattr(trigger, "sweep_time", None))
                trigger.final_location_context = final_context
                trigger.final_location_permission = final_permission
                frozen = original_permission.frozen
                profiles_changed = any(old is not None and old != new for old, new in (
                    (frozen.w1_profile_id, getattr(market_location, "w1_profile_id", None)),
                    (frozen.h4_profile_id, getattr(market_location, "h4_profile_id", None)),
                    (frozen.profile_id, getattr(market_location, "active_profile_id", None)),
                ))
                if profiles_changed:
                    location_blocker = "FINAL_PROFILE_MIGRATION"
                elif final_context.liquidity_pool_id != original_context.liquidity_pool_id:
                    location_blocker = "FINAL_POOL_MIGRATION"
                elif final_context.liquidity_type == "UNKNOWN_LOCAL":
                    location_blocker = "FINAL_UNKNOWN_LOCAL"
                elif final_context.already_consumed:
                    location_blocker = "FINAL_CONSUMED_POOL"
                elif not final_context.allowed:
                    location_blocker = f"FINAL_{final_context.permission_reason or 'LOCATION_BLOCK'}"
                elif final_permission.reason == "SETUP_EXPIRED":
                    location_blocker = "FINAL_SETUP_EXPIRED"
                elif (final_permission.reaction_state == "ACCEPTANCE"
                      or "ACCEPTANCE" in str(final_permission.reason)):
                    location_blocker = "FINAL_ACCEPTANCE"
                elif final_permission.reason == "LOCATION_INVALIDATED":
                    location_blocker = "FINAL_LOCATION_INVALIDATED"
                elif not final_permission.executable:
                    location_blocker = f"FINAL_{final_permission.rejection_code or final_permission.reason}"
                elif final_permission.frozen.location_id != frozen.location_id:
                    location_blocker = "FINAL_LOCATION_MIGRATION"
            if location_blocker:
                rejects[location_blocker] += 1
                rejects_by_trigger[f"{location_blocker}:{ttype}"] += 1
                trigger.final_quote = {"allowed": False, "blocker": location_blocker}
                continue
            spread_price = float(bar_spread) * info.point * 10.0
            bid = float(fill_price)
            ask = bid + spread_price
            execution_price = ask if trigger.direction == "BULLISH" else bid
            final_quote = trigger_engine.final_quote_check(
                trigger, bid=bid, ask=ask, point=info.point, symbol=symbol)
            drift_price = execution_price - float(trigger.entry_price)
            final_quote.update(
                arrival_to_execution_ms=max(
                    0.0, (pd.Timestamp(fill_time).to_pydatetime() - now).total_seconds() * 1000.0),
                price_drift_points=drift_price / info.point,
                price_drift_R=(abs(drift_price) /
                               abs(float(trigger.entry_price) - float(trigger.stop_loss))
                               if abs(float(trigger.entry_price) - float(trigger.stop_loss)) > 0
                               else None),
            )
            trigger.final_quote = final_quote
            if not final_quote["allowed"]:
                blocker = final_quote.get("blocker", "FINAL_GEOMETRY_FAIL")
                rejects[blocker] += 1
                rejects_by_trigger[f"{blocker}:{ttype}"] += 1
                continue
            volume = min(volume, _calc_volume(
                symbol, risk_usd, execution_price, trigger.stop_loss, info))
            if volume <= 0:
                rejects["FINAL_LOT_FLOOR"] += 1
                rejects_by_trigger[f"FINAL_LOT_FLOOR:{ttype}"] += 1
                continue

        if guardian is not None:
            # Lot geometry decides whether a TP-extension partial can fill at
            # all; a 0.01-lot position cannot be halved and lives sets
            # `tp_extend_blocked`. Read per symbol, not assumed.
            guardian.volume_min = info.volume_min
            guardian.volume_step = info.volume_step
            guardian.min_sl_move = info.point * 10.0

        exit_price, result, reason, close_time, outcome = _schedule_exit(
            frames["M5"], entry_idx, trigger, now, guardian, volume)

        # Booked against the fill, not the signal. `realised_risk` below stays
        # on the signal-anchored `sl_pips` because that is the risk the sizing
        # actually assumed — so an adverse fill now shows up as an r_multiple
        # short of the intended 2.0 instead of being invisible.
        if outcome is not None and len(outcome.legs) > 1:
            # A TP extension banks half the position before the rest runs, so
            # one (price, volume) pair cannot express the result. Total volume
            # across legs equals the original, so the cost line is unchanged.
            gross = sum(_order_pnl(symbol, trigger.direction, leg.volume,
                                   fill_price, leg.price)
                        for leg in outcome.legs)
        else:
            gross = _order_pnl(symbol, trigger.direction, volume,
                               fill_price, exit_price)
        pip_val = _pip_value(symbol, info)
        # Round-turn spread is paid once (buy the ask, sell the bid); commission
        # is per round-turn lot. Article 19141's model excludes both, so the
        # simulator applies them explicitly rather than reporting gross R.
        cost = _execution_cost(symbol, volume, bar_spread, pip_val,
                               commission_per_lot, round_trip_cost_price, info)
        net = gross - cost
        realised_risk = sl_pips * pip_val * volume

        trade = SimTrade(
            symbol=symbol,
            open_time=now.isoformat(),
            close_time=close_time.isoformat(),
            trigger_type=trigger.trigger_type,
            direction=trigger.direction,
            entry=fill_price,
            signal_price=trigger.entry_price,
            sl=trigger.stop_loss,
            tp1=trigger.tp1,
            tp2=trigger.tp2,
            exit_price=exit_price,
            result=result,
            exit_reason=reason,
            pnl=round(net, 2),
            gross_pnl=round(gross, 2),
            cost_usd=round(cost, 2),
            session=session_checker.vp_window_name(now),
            matched_triggers="+".join(trigger.matched_triggers),
            wick_ratio=(round(trigger.wick_ratio, 4)
                        if trigger.wick_ratio is not None else -1.0),
            reclaim=trigger.reclaim.record() if trigger.reclaim else None,
            m15_fvg=trigger.fvg_entry.record() if trigger.fvg_entry else None,
            htf_crt=trigger.htf_crt.record() if trigger.htf_crt else None,
            session_sweep=trigger.session_sweep.record() if trigger.session_sweep else None,
            entry_setup_telemetry=build_entry_observation(
                symbol=symbol, trigger=trigger, legacy_decision="ALLOWED"),
            market_location=(trigger.market_location.record()
                             if getattr(trigger, "market_location", None) is not None
                             else None),
            location_permission=(trigger.location_permission.record()
                                 if getattr(trigger, "location_permission", None) is not None
                                 else None),
            sweep_runtime_contract=(_sweep_runtime_record(trigger)
                                    if trigger.trigger_type == "SWEEP_REJECTION"
                                    else None),
            tga_managed=outcome is not None,
            tga_sl_stage=(outcome.sl_stage if outcome else 0),
            tga_peak_r=round(outcome.peak_r, 4) if outcome else 0.0,
            tga_mae_r=round(outcome.adverse_r, 4) if outcome else 0.0,
            tga_tp_extended=(outcome.tp_extended if outcome else False),
            tga_legs=(len(outcome.legs) if outcome else 1),
            # `stb` is bound on every path that reaches here: it is assigned at
            # the loop-body indent, and every construct between that assignment
            # and this one is a guard that `continue`s, which re-enters the loop
            # and re-executes it. Mirrors the live registration site's four
            # values exactly, including `or ""` for the Optional sweep string,
            # so the two books compare field-for-field. Observation-only.
            stb_confidence=stb.confidence,
            short_term_bias=stb.short_term_bias,
            htf_trend=stb.htf_trend,
            recent_sweep=stb.recent_sweep or "",
            vp_level=(trigger.vplr.vp_level_name if trigger.vplr else ""),
            vp_level_source=(trigger.vplr.level_source if trigger.vplr else ""),
            vp_sweep_depth_atr=round(
                trigger.vplr.sweep_depth_atr if trigger.vplr else 0.0, 3),
            vp_confluences=("+".join(trigger.vplr.confluences)
                            if trigger.vplr else ""),
            volume=volume,
            risk_usd=round(realised_risk, 2),
            r_multiple=round(net / realised_risk, 4) if realised_risk > 0 else 0.0,
            duration_min=int((close_time - now).total_seconds() / 60),
        )
        open_trades[symbol] = {"trade": trade}
        if trigger.htf_crt:
            crt_used.add(trigger.htf_crt.setup_id)
        if trigger.session_sweep:
            session_used.add(trigger.session_sweep.setup_id)
        pending_exits.append({"close_time": close_time, "trade": trade})
        pending_exits.sort(key=lambda p: p["close_time"])
        if candidate_funnel is not None and selected_id:
            candidate_funnel.terminal(
                selected_id, "SIMULATED_ENTRY", "simulated order accepted",
                values=(_sweep_runtime_record(trigger)
                        if trigger.trigger_type == "SWEEP_REJECTION" else {}))

    flush_exits(datetime.max.replace(tzinfo=timezone.utc))
    all_trades.sort(key=lambda t: t.open_time)

    wins = sum(1 for t in all_trades if t.pnl > 0)
    losses = len(all_trades) - wins
    total_pnl = round(sum(t.pnl for t in all_trades), 2)

    incidents_written = 0
    if incidents_path and all_trades:
        incidents_written = emit_incidents(all_trades, data, session_checker,
                                           incidents_path)
        print(f"forensics: {incidents_written} incidents -> {incidents_path}")

    if sweep_research_path:
        fields = ["candidate_id", "symbol", "decision_time", "direction",
                  "swept_level", "production_winner", "matched_triggers",
                  "location_context", "outcome", "exit_price", "realised_R",
                  "mfe_R", "mae_R", "bars_to_exit", "exit_time",
                  "cost_price", "status"]
        path = Path(sweep_research_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(sweep_research_rows.values())

    by_symbol: Dict[str, Dict[str, float]] = {}
    for t in all_trades:
        b = by_symbol.setdefault(t.symbol, {"trades": 0, "pnl": 0.0})
        b["trades"] += 1
        b["pnl"] = round(b["pnl"] + t.pnl, 2)

    return {
        "config": {
            "symbols": symbols,
            "from": dt_from.isoformat(),
            "to": dt_to.isoformat(),
            "sa_pool": sa_pool,
            "risk_pct": risk_pct,
            "max_open_positions": max_open_positions,
            "spread_pips_fallback": spread_pips,
            "spread_source": "per-bar MT5 broker spread (fallback where zero)",
            "spread_pips_observed": {
                sym: {
                    "mean": round(float(ser.mean()), 2),
                    "median": round(float(ser.median()), 2),
                    "p90": round(float(ser.quantile(0.90)), 2),
                }
                for sym, ser in spread_by_symbol.items()
            },
            "va_fade_enabled": va_fade_enabled,
            "rd_gate_enabled": rd_gate_enabled,
            "rd_gate_mode": rd_gate_mode,
            "pdr_gate_enabled": pdr_gate_enabled,
            "pdr_gate_mode": pdr_gate_mode,
            "vp_gate_enabled": vp_gate_enabled,
            "vp_gate_mode": vp_gate_mode,
            "tga_exits": tga_exits,
            "leg_conf_enabled": leg_conf_enabled,
            "leg_conf_mode": leg_conf_mode,
            "sweep_wick_filter": sweep_wick_filter,
            "sweep_wick_ratio": sweep_wick_ratio,
            "reclaim_fvg_enabled": reclaim_fvg_enabled,
            "m15_fvg_entry_enabled": m15_fvg_entry_enabled,
            "htf_crt_enabled": htf_crt_enabled,
            "session_sweep_enabled": session_sweep_enabled,
            "session_sweep_funnel": dict(session_funnel),
            "crt_confluence_mode": crt_confluence_mode,
            "watch_inactive_crt": watch_inactive_crt,
            "round_trip_cost_price": round_trip_cost_price,
            "cost_model": "measured_total_replaces_spread_commission" if round_trip_cost_price is not None else "spread_plus_commission",
            "crt_watch_observations": dict(crt_watch),
            "crt_candidate_observations": crt_candidates,
            "vplr_enabled": vplr_enabled,
            "vplr_session_override": vplr_session_override,
            "vplr_scope": vplr_scope,
            "vplr_session_window_utc": [str(VPLR_SESSION_WINDOW_UTC[0]),
                                        str(VPLR_SESSION_WINDOW_UTC[1])],
            "vp_poc_band_frac": vp_poc_band_frac,
            "vp_profile_bars": VP_PROFILE_BARS,
            "vp_target_bins": VP_TARGET_BINS,
            "ema_band_enabled": ema_band_enabled,
            "ema_band_mode": ema_band_mode,
            "stb_relax_continuation": stb_relax_continuation,
            "ema_band_period": EMA_BAND_PERIOD,
            "ema_band_timeframe": "H1",
            "warmup_lead_days": WARMUP_LEAD_DAYS,
            "commission_per_lot": commission_per_lot,
            "daily_loss_limit_usd": daily_loss_limit_usd,
            "cooldown_enabled": cooldown_enabled,
            "win_cooldown_minutes": win_cooldown_minutes,
            "loss_cooldown_policy": loss_cooldown_policy,
            "allow_whole_day": allow_whole_day,
            "enabled_triggers": sorted(trigger_engine.enabled_triggers),
            "enabled_sessions": sorted(session_checker.enabled_sessions),
            "enforce_session_windows": enforce_session_windows,
            "enforce_news_blackout": enforce_news_blackout,
            "fill_at_next_bar_open": fill_at_next_bar_open,
            "incidents_path": incidents_path,
            "incidents_written": incidents_written,
        },
        "summary": {
            "trades": len(all_trades),
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round((wins / len(all_trades) * 100.0), 2) if all_trades else 0.0,
            "net_pnl": total_pnl,
            "gross_pnl": round(sum(t.gross_pnl for t in all_trades), 2),
            "total_costs": round(sum(t.cost_usd for t in all_trades), 2),
            "ending_balance": round(balance, 2),
            "by_symbol": by_symbol,
        },
        "rejections": dict(sorted(rejects.items(), key=lambda kv: -kv[1])),
        "triggers_detected": dict(sorted(detected.items(), key=lambda kv: -kv[1])),
        "leg_conf_labels": dict(leg_conf_labels),
        "leg_conf_admitted": dict(leg_conf_admitted),
        "rejections_by_trigger": dict(sorted(rejects_by_trigger.items(), key=lambda kv: -kv[1])),
        "equity_curve": equity_curve,
        "trades": [asdict(t) for t in all_trades],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Historical backtest for ScalperAgent with the live gate chain")
    parser.add_argument("--symbols", type=str, default="XAUUSD")
    parser.add_argument("--from", dest="date_from", type=str, required=True, help="UTC start, e.g. 2026-05-15T00:00:00")
    parser.add_argument("--to", dest="date_to", type=str, required=True, help="UTC end, e.g. 2026-08-23T00:00:00")
    parser.add_argument("--pool", type=float, default=1000.0)
    parser.add_argument("--risk", type=float, default=0.03)
    parser.add_argument("--max-open", type=int, default=MAX_OPEN_POSITIONS)
    parser.add_argument("--spread-pips", type=float, default=2.5,
                        help="Fallback spread in pips, used only for bars where "
                             "the broker reports no spread. The simulator "
                             "otherwise uses the per-bar spread MT5 records.")
    parser.add_argument("--va-fade", dest="va_fade",
                        action=argparse.BooleanOptionalAction,
                        default=VA_FADE_ENABLED,
                        help="VALUE_AREA_FADE entry model. Mirrors scalper_agent.py.")
    parser.add_argument("--rd-gate", dest="rd_gate",
                        action=argparse.BooleanOptionalAction,
                        default=RD_GATE_ENABLED,
                        help="Regime-direction gate. Mirrors scalper_agent.py.")
    parser.add_argument("--rd-gate-mode", type=str, default=RD_GATE_MODE,
                        choices=list(RD_MODES),
                        help="SYMMETRIC | COUNTER_TREND_LONGS.")
    parser.add_argument("--pdr-gate", dest="pdr_gate",
                        action=argparse.BooleanOptionalAction,
                        default=PDR_GATE_ENABLED,
                        help="Previous-day-range gate: veto buying the premium "
                             "or selling the discount of yesterday's range. "
                             "Default off (ledger L-011).")
    parser.add_argument("--pdr-gate-mode", type=str, default=PDR_GATE_MODE,
                        choices=list(PDR_MODES),
                        help="SYMMETRIC | LONG_PREMIUM | SHORT_DISCOUNT. "
                             "Mirrors scalper_agent.py.")
    parser.add_argument("--vp-gate", dest="vp_gate",
                        action=argparse.BooleanOptionalAction,
                        default=VP_GATE_ENABLED,
                        help="H4 value-area location gate: sell from VAH, "
                             "buy from VAL, no trade at the POC. "
                             "Mirrors scalper_agent.py.")
    parser.add_argument("--vp-gate-mode", type=str, default=VP_GATE_MODE,
                        choices=list(VP_MODES),
                        help="RANGING_ONLY | ALWAYS | POC_ONLY. "
                             "Mirrors scalper_agent.py.")
    parser.add_argument("--vp-poc-band", type=float,
                        default=VP_POC_BAND_FRAC,
                        help="Half-width of the POC dead zone as a fraction "
                             "of value-area width.")
    parser.add_argument("--leg-conf", dest="leg_conf",
                        action=argparse.BooleanOptionalAction,
                        default=LEG_CONF_ENABLED,
                        help="VP_LEG_CONFLUENCE: filter entries by their "
                             "location against two completed H4 swing legs "
                             "(ledger L-015)")
    parser.add_argument("--leg-conf-mode", type=str, default=LEG_CONF_MODE,
                        choices=list(LEG_CONF_MODES),
                        help="CONFLUENCE_ONLY (both legs agree) | AT_LEVEL "
                             "(either leg) | LVN_VETO (block low-volume nodes)")
    parser.add_argument("--sweep-wick-filter", dest="sweep_wick_filter",
                        action=argparse.BooleanOptionalAction,
                        default=SWEEP_WICK_FILTER_ENABLED,
                        help="L-016: require the sweeping candle's wick to be "
                             "at least --sweep-wick-ratio of its own range "
                             "before SWEEP_REJECTION is admitted")
    parser.add_argument("--reclaim-fvg", action=argparse.BooleanOptionalAction,
                        default=DP.RECLAIM_FVG_ENABLED,
                        help="Require displacement reclaim plus FVG return at broken "
                             "levels; --no-reclaim-fvg reproduces the prior entry contract")
    parser.add_argument("--htf-crt", action=argparse.BooleanOptionalAction,
                        default=DP.CRT_ENABLED, help="Priority D1/W1/MN1 CRT sweep trigger")
    parser.add_argument("--crt-confluence-mode", choices=DP.CRT_CONFLUENCE_MODES,
                        default=DP.CRT_CONFLUENCE_MODE)
    parser.add_argument("--round-trip-cost-price", type=float, default=None,
                        help="XAUUSD total USD/oz cost replacing spread/commission debit")
    parser.add_argument("--watch-inactive-crt", action=argparse.BooleanOptionalAction,
                        default=True, help="Record stateless CRT watches outside entry sessions")
    parser.add_argument("--m15-fvg-entry", action=argparse.BooleanOptionalAction,
                        default=DP.M15_FVG_ENTRY_ENABLED,
                        help="Enter confirmed M15 FVGs with structural TP (L-018)")
    parser.add_argument("--sweep-wick-ratio", type=float,
                        default=SWEEP_WICK_RATIO_MIN,
                        help="Minimum rejecting-wick share of the sweeping "
                             "candle's range. Swept 0.35 / 0.45 / 0.55.")
    parser.add_argument("--tga-exits", dest="tga_exits",
                        action="store_true", default=TGA_EXITS_IN_SIM,
                        help="replay the live Trade Guardian's trailing, early "
                             "close, no-progress kill and TP extension instead "
                             "of a static stop and target (ledger L-003)")
    parser.add_argument("--no-tga-exits", dest="tga_exits",
                        action="store_false",
                        help="force the unmanaged static-exit walk")
    parser.add_argument("--vplr", dest="vplr",
                        action=argparse.BooleanOptionalAction,
                        default=VPLR_ENABLED,
                        help="VP_LIQUIDITY_REACTION: a liquidity raid at an "
                             "anchored H4 volume-profile level confirmed by an "
                             "M5 MSS. Highest trigger priority. "
                             "Mirrors scalper_agent.py.")
    parser.add_argument("--vplr-session-override", dest="vplr_session_override",
                        action=argparse.BooleanOptionalAction,
                        default=VPLR_SESSION_OVERRIDE_ENABLED,
                        help="Let VP_LIQUIDITY_REACTION — and only it — scan "
                             "during the Asia allowance. Mirrors "
                             "scalper_agent.py.")
    parser.add_argument("--vplr-scope", type=str, default=VPLR_SCOPE,
                        choices=list(VPLR_SCOPES),
                        help="ASIA_ONLY: evaluate VP_LIQUIDITY_REACTION only "
                             "inside the VP allowance. ALL_SESSIONS: evaluate "
                             "it on every scanned bar. Mirrors scalper_agent.py.")
    parser.add_argument("--ema-band-mode", type=str, default=EMA_BAND_MODE,
                        choices=["TREND", "FADE"],
                        help="TREND: above the band permits longs (the "
                             "specified rule). FADE: the inversion.")
    parser.add_argument("--stb-relax-continuation", dest="stb_relax",
                        action=argparse.BooleanOptionalAction,
                        default=STB_RELAX_CONTINUATION,
                        help="Relax the two fade-specific short-term-bias rules "
                             "for continuation triggers. Mirrors "
                             "scalper_agent.py.")
    parser.add_argument("--ema-band", dest="ema_band",
                        action=argparse.BooleanOptionalAction,
                        default=EMA_BAND_ENABLED,
                        help="H1 EMA(18) high/low directional band. Mirrors "
                             "scalper_agent.py --ema-band, and takes its "
                             "default from decision_params so the two cannot "
                             "disagree.")
    parser.add_argument("--commission-per-lot", type=float, default=0.0)
    parser.add_argument("--loss-limit", type=float, default=100.0)
    parser.add_argument("--no-cooldown", action="store_true")
    parser.add_argument("--win-cooldown-min", type=float, default=5.0)
    parser.add_argument("--loss-cooldown-policy", type=str, default="NEXT_UTC_HOUR",
                        choices=["NEXT_UTC_HOUR", "FIXED_MINUTES"])
    parser.add_argument("--allow-whole-day", action="store_true")
    parser.add_argument("--session-sweep", action="store_true",
                        help="Replay session-sweep trigger from historical M1, never the current vault CSV")
    parser.add_argument("--triggers", type=str, default=None,
                        help="Comma-separated trigger whitelist "
                             "(SWEEP_REJECTION,FVG_FILL,BOS_RETEST,JUDAS,SESSION_SWEEP,VP_LIQUIDITY_REACTION,VALUE_AREA_FADE). "
                             "Mirrors scalper_agent.py --triggers.")
    parser.add_argument("--market-location-mode", choices=DP.MARKET_LOCATION_MODES,
                        default=DP.MARKET_LOCATION_MODE,
                        help="OFF legacy behavior; ACTIVE binds SWEEP_REJECTION")
    parser.add_argument("--sweep-location-config", default=None,
                        help="JSON named-liquidity policy. Required and must be "
                             "ACTIVE whenever SWEEP_REJECTION is enabled.")
    parser.add_argument("--sessions", type=str, default=None,
                        help="Comma-separated session whitelist (LONDON_OPEN,"
                             "PRE_LONDON,LONDON_NY,TOKYO_OPEN,NY_LUNCH_REV). "
                             "Default: TOKYO_OPEN,LONDON_NY; mirrors "
                             "scalper_agent.py --sessions.")
    parser.add_argument("--fill-signal-price", dest="fill_signal_price",
                        action="store_true",
                        help="Disable the L-013 S1 entry fill model and book "
                             "P&L at the signal price, as every table written "
                             "before 2026-08-27 did. For regenerating those "
                             "comparisons only \u2014 it is strictly less faithful.")
    parser.add_argument("--ignore-session-windows", action="store_true")
    parser.add_argument("--ignore-news-blackout", action="store_true")
    parser.add_argument("--out", type=str, default="logs/scalper_backtest_results.json")
    parser.add_argument("--incidents", type=str, default=None,
                        help="Write a forensic incident per simulated trade to "
                             "this JSONL path, for analyze_incidents.py. Uses "
                             "the same postmortem module the live agent uses.")
    args = parser.parse_args()

    requested_triggers = ([t.strip().upper() for t in args.triggers.split(",") if t.strip()]
                          if args.triggers else [])
    sweep_required = "SWEEP_REJECTION" in requested_triggers
    if sweep_required and args.market_location_mode != "ACTIVE":
        parser.error("SWEEP_REJECTION requires --market-location-mode ACTIVE")
    try:
        sweep_location_policy = load_sweep_location_policy(
            args.sweep_location_config, require_active=sweep_required)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(f"SWEEP_REJECTION named-liquidity policy failed to load: {exc}")

    dt_from = datetime.fromisoformat(args.date_from).replace(tzinfo=timezone.utc)
    dt_to = datetime.fromisoformat(args.date_to).replace(tzinfo=timezone.utc)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    _load_env_and_connect()
    try:
        results = run_backtest(
            symbols=symbols,
            dt_from=dt_from,
            dt_to=dt_to,
            sa_pool=args.pool,
            risk_pct=args.risk,
            max_open_positions=args.max_open,
            spread_pips=args.spread_pips,
            ema_band_enabled=args.ema_band,
            ema_band_mode=args.ema_band_mode,
            va_fade_enabled=args.va_fade,
            rd_gate_enabled=args.rd_gate,
            rd_gate_mode=args.rd_gate_mode,
            pdr_gate_enabled=args.pdr_gate,
            pdr_gate_mode=args.pdr_gate_mode,
            fill_at_next_bar_open=not args.fill_signal_price,
            vp_gate_enabled=args.vp_gate,
            vp_gate_mode=args.vp_gate_mode,
            tga_exits=args.tga_exits,
            leg_conf_enabled=args.leg_conf,
            leg_conf_mode=args.leg_conf_mode,
            sweep_wick_filter=args.sweep_wick_filter,
            sweep_wick_ratio=args.sweep_wick_ratio,
            reclaim_fvg_enabled=args.reclaim_fvg,
            m15_fvg_entry_enabled=args.m15_fvg_entry,
            htf_crt_enabled=args.htf_crt,
            session_sweep_enabled=args.session_sweep,
            crt_confluence_mode=args.crt_confluence_mode,
            round_trip_cost_price=args.round_trip_cost_price,
            watch_inactive_crt=args.watch_inactive_crt,
            vplr_enabled=args.vplr,
            vplr_session_override=args.vplr_session_override,
            vplr_scope=args.vplr_scope,
            vp_poc_band_frac=args.vp_poc_band,
            stb_relax_continuation=args.stb_relax,
            commission_per_lot=args.commission_per_lot,
            daily_loss_limit_usd=args.loss_limit,
            cooldown_enabled=not args.no_cooldown,
            win_cooldown_minutes=args.win_cooldown_min,
            loss_cooldown_policy=args.loss_cooldown_policy,
            allow_whole_day=args.allow_whole_day,
            enabled_triggers=(requested_triggers if args.triggers else None),
            enabled_sessions=([t.strip() for t in args.sessions.split(",") if t.strip()]
                              if args.sessions else None),
            enforce_session_windows=not args.ignore_session_windows,
            enforce_news_blackout=not args.ignore_news_blackout,
            incidents_path=args.incidents,
            market_location_mode=args.market_location_mode,
            sweep_location_policy=sweep_location_policy,
        )
    finally:
        mt5.shutdown()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    summary = results["summary"]
    print("Scalper Backtest Complete")
    print(f"Trades        : {summary['trades']}")
    print(f"Win Rate      : {summary['win_rate_pct']}%")
    print(f"Net PnL       : {summary['net_pnl']}  (gross {summary['gross_pnl']}, costs {summary['total_costs']})")
    print(f"Ending Balance: {summary['ending_balance']}")
    print(f"Output        : {out_path}")
    print("Top rejections:")
    for gate, n in list(results["rejections"].items())[:10]:
        print(f"  {gate:<26} {n}")
    print("Triggers detected (before post-detection gates):")
    for name, n in results["triggers_detected"].items():
        print(f"  {name:<26} {n}")
    if results["rejections_by_trigger"]:
        print("Rejections attributed to trigger:")
        for key, n in list(results["rejections_by_trigger"].items())[:15]:
            print(f"  {key:<40} {n}")


if __name__ == "__main__":
    main()
