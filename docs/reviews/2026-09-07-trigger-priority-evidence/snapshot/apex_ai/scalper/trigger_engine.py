"""
SA Trigger Engine
==================
Implements SA's simplified 3-step check logic.
Completely independent of main system engines (LIA, MSA, etc.)

Step 1: Micro Liquidity Identification (equal H/L on M5, session extremes)
Step 2: Trigger Confirmation — ONE of:
    - Sweep + Rejection  : Liquidity swept, immediate wick rejection
    - Imbalance Fill     : M1/M5 FVG fully mitigated, price reverses
    - Breakout Retest    : Clean BOS on M5, retest of broken level
    - Session Open Judas : First 15-min move fades by 50%+
Step 3: Entry Validation (candle close confirmation, spread check)

If all 3 steps pass → EXECUTE
If any step fails  → SKIP (no override allowed)
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Tuple
import pandas as pd
import numpy as np

from scalper.decision_params import (SWEEP_WICK_FILTER_ENABLED,
                                     SWEEP_WICK_RATIO_MIN)

logger = logging.getLogger("SA.Trigger")


# ── Data Classes ──────────────────────────────────────────────────────────────

@dataclass
class MicroLiquidity:
    """Step 1 output: nearby liquidity levels."""
    equal_highs: List[float] = field(default_factory=list)
    equal_lows:  List[float] = field(default_factory=list)
    session_high: Optional[float] = None
    session_low:  Optional[float] = None
    current_price: float = 0.0

    @property
    def nearest_bsl(self) -> Optional[float]:
        """Nearest BSL above current price."""
        candidates = [l for l in self.equal_highs if l > self.current_price]
        if self.session_high and self.session_high > self.current_price:
            candidates.append(self.session_high)
        return min(candidates) if candidates else None

    @property
    def nearest_ssl(self) -> Optional[float]:
        """Nearest SSL below current price."""
        candidates = [l for l in self.equal_lows if l < self.current_price]
        if self.session_low and self.session_low < self.current_price:
            candidates.append(self.session_low)
        return max(candidates) if candidates else None


@dataclass
class SATrigger:
    """Step 2 output: trigger confirmation."""
    detected: bool = False
    trigger_type: str = "NONE"      # SWEEP_REJECTION | FVG_FILL | BOS_RETEST | JUDAS
    direction: str = "NONE"         # BULLISH | BEARISH
    entry_price: float = 0.0
    stop_loss: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    confidence: str = "LOW"         # LOW | MEDIUM | HIGH
    description: str = ""
    swept_level: Optional[float] = None
    requires_displacement: bool = False  # If True, SA Consul must confirm displacement
    #: Every detector that fired on this bar, in priority order. The selected
    #: trigger is `matched_triggers[0]`; the rest are what it out-ranked.
    #: Telemetry only — nothing branches on it (§13.10).
    matched_triggers: List[str] = field(default_factory=list)
    #: The full VP_LIQUIDITY_REACTION evidence chain when that trigger won.
    #: `None` for every other trigger type.
    vplr: object = None
    #: The sweeping candle's rejecting-wick share of its own range (L-016).
    #: Computed on every SWEEP_REJECTION whether or not the filter is enabled,
    #: so a baseline run can answer "how many WINNERS would this have vetoed?"
    #: without a second arm. `None` for every other trigger type.
    wick_ratio: Optional[float] = None
    fvg_entry: object = None  # L-018 M15 zone and structural TP evidence
    htf_crt: object = None  # L-019 native D1/W1/MN1 raid evidence
    # L-017 closed-bar permission, also checked against the final order quote.
    reclaim: object = None


def _wick_ratio(candle, direction: str) -> float:
    """
    The rejecting wick's share of the sweeping candle's total range (L-016).

    `direction` is the direction of the resulting TRADE, so a BULLISH trade
    follows an SSL (sell-side) sweep and is qualified by the LOWER wick.
    A zero-range candle has no wick to measure and scores 0.0, which fails any
    positive threshold — the conservative reading for a bar with no rejection
    in it.
    """
    high  = float(candle['high'])
    low   = float(candle['low'])
    rng   = high - low
    if rng <= 0:
        return 0.0
    body_top = max(float(candle['open']), float(candle['close']))
    body_bot = min(float(candle['open']), float(candle['close']))
    wick = (body_bot - low) if direction == "BULLISH" else (high - body_top)
    return wick / rng


def resolve_enabled_triggers(requested, vplr_enabled: bool, crt_enabled: bool = True) -> List[str]:
    """
    The enabled-trigger set, with VP_LIQUIDITY_REACTION added or removed by its
    own feature switch.

    `SATriggerEngine` defaults to every name in `ALL_TRIGGERS`, and
    VP_LIQUIDITY_REACTION is now one of them — so without this, adding the
    trigger to that tuple would have silently switched it on for every existing
    run and no baseline would reproduce. Membership is decided here, once, and
    both decision paths call it (invariant #2).

    Asking for the trigger by name while its switch is off is a contradiction
    and raises, rather than quietly running a set the operator did not intend.
    """
    if requested is None:
        names = set(SATriggerEngine.ALL_TRIGGERS)
    else:
        names = {str(t).strip().upper() for t in requested if str(t).strip()}
    if vplr_enabled:
        names.add("VP_LIQUIDITY_REACTION")
    else:
        if requested is not None and "VP_LIQUIDITY_REACTION" in names:
            raise ValueError(
                "VP_LIQUIDITY_REACTION requested in the trigger whitelist but "
                "the trigger is disabled; pass --vplr to enable it")
        names.discard("VP_LIQUIDITY_REACTION")
    if not crt_enabled:
        if requested is not None and "HTF_CRT_SWEEP" in names:
            raise ValueError("HTF_CRT_SWEEP requested while --no-htf-crt is set")
        names.discard("HTF_CRT_SWEEP")
    return sorted(names)


# ── Trigger Engine ────────────────────────────────────────────────────────────

class SATriggerEngine:
    """
    SA trigger selection on M15/M5 with optional native HTF context.
    """

    # Max SL in price units per spec (30-50 points Gold, 10-15 pips Forex)
    MAX_SL_PIPS = {
        "XAUUSD": 50,   # points = 0.50 USD
        "GBPUSD": 15,   # pips  = 0.00150
        "EURUSD": 15,
        "US30":   50,   # points
        "NAS100": 50,
        "DEFAULT": 20,
    }

    # P0 spread-ceiling overhaul (replaces the unrealistic 3.0-pip XAUUSD cap
    # that was killing every valid sweep-rejection during London on demo).
    # Two-layer guard:
    #   1. _max_spread()        — absolute outlier kill (broker-error / news)
    #   2. MAX_SPREAD_TO_SL_FRAC — spread ≤ this fraction of SL distance so
    #      transaction cost can never dominate the risk budget on any setup.
    MAX_SPREAD_TO_SL_FRAC = 0.25  # Spread must be ≤ 25% of SL distance

    # P0 minimum SL distance — rejects noise-grade sweeps whose SL is too
    # tight to represent real institutional liquidity. Values are in the
    # code's internal pip convention: sl_pips = (entry - sl) / point / 10.
    # For 5-digit gold/silver and 3-digit oil, this makes 1 "pip" = $0.01.
    # Original values were 10x too small (intended as $/dollar pips) and
    # the guard was effectively disabled. Today's 05:30 XAUUSD trade with
    # a 22-pip ($0.22) SL got through and was instantly stopped.
    MIN_SL_PIPS = {
        "XAUUSD": 350,   # ~$3.50 — Gold sweeps below this are intrabar noise
        "XAGUSD": 120,   # ~$0.12
        "USOIL":  200,   # ~$0.20 — Oil tick is fine
        "GBPUSD":  60,
        "EURUSD":  60,
        "US30":   250,
        "NAS100": 250,
        "BTCUSD": 800,
        "DEFAULT": 100,
    }

    # ── Target geometry (P0 — the core expectancy defect) ────────────────────
    # TP1 was previously fixed at 1.0R. A 1R target needs a >50% win rate to
    # break even BEFORE costs; with spread + commission it needs ~55%+. Live
    # data over 2026-05-06..15 showed 37 wins against 173 losses and 67
    # timeouts on 287 trades — nowhere near that. The published minimum-risk
    # research (MQL5 art. 18991) states the survival condition directly:
    #
    #       RRR  >  (1 - win_rate) / win_rate
    #
    # and art. 19141 tabulates the practical floor: 45% WR needs RRR >= 2.0,
    # 30% WR needs RRR >= 3.5. Both MQL5 SMC reference EAs (art. 20569, 16340)
    # ship a 2.0-2.14 default RR for exactly this reason. TP1 therefore moves
    # to 2.0R and TP2 to 3.0R. This is the single highest-impact change in the
    # scalper: at a 35% win rate, 1R loses money and 2R makes it.
    TP1_R = 2.0
    TP2_R = 3.0

    #: Minimum net R after costs. A setup whose target cannot clear the spread
    #: plus commission by this multiple is not worth taking.
    MIN_NET_R = 1.5

    #: Every trigger this engine can emit. `enabled_triggers` is validated
    #: against this set so a typo silences the engine loudly instead of
    #: quietly disabling every detector.
    #: Order is priority order — `step2_trigger` walks it and returns the first
    #: detector that fires. HTF_CRT_SWEEP is first under L-019's operator
    #: contract; VP_LIQUIDITY_REACTION follows it because its
    #: contract is the most demanding: a raid at a profile level, reclaimed or
    #: displaced, confirmed by a structure break, with confluence. When all of
    #: that holds it is a better description of the bar than a bare sweep
    #: rejection, and when any clause fails it returns nothing and costs the
    #: chain only the time to check.
    #:
    #: The relative order of the PRE-EXISTING five is deliberately unchanged.
    #: Promoting BOS_RETEST above FVG_FILL would alter which trade is taken on
    #: any bar where both fire, and the requirement that a disabled VP trigger
    #: reproduce the baseline exactly is only satisfiable if nothing below the
    #: new head moves.
    ALL_TRIGGERS = ("HTF_CRT_SWEEP", "VP_LIQUIDITY_REACTION", "SWEEP_REJECTION", "FVG_FILL",
                    "BOS_RETEST", "JUDAS", "VALUE_AREA_FADE")

    def __init__(self, equal_hl_tolerance_pct: float = 0.0003,
                 tp1_r: float = None, tp2_r: float = None,
                 enabled_triggers=None,
                 sweep_wick_filter: bool = None,
                 sweep_wick_ratio: float = None):
        self.eq_tol = equal_hl_tolerance_pct
        self.tp1_r = float(tp1_r) if tp1_r is not None else self.TP1_R
        self.tp2_r = float(tp2_r) if tp2_r is not None else self.TP2_R
        # L-016. Both processes construct this class, so the filter living
        # inside the detector satisfies invariant #2 structurally — there is no
        # second copy to keep in step.
        self.sweep_wick_filter = (SWEEP_WICK_FILTER_ENABLED
                                  if sweep_wick_filter is None
                                  else bool(sweep_wick_filter))
        self.sweep_wick_ratio = (SWEEP_WICK_RATIO_MIN
                                 if sweep_wick_ratio is None
                                 else float(sweep_wick_ratio))
        # `step2_trigger` returns the FIRST detector that fires, in a fixed
        # priority order with SWEEP_REJECTION at the head. That makes it
        # greedy: on the 2026-05-15 -> 08-21 window it took 196 of 198 trades
        # and BOS_RETEST never got a chance to be evaluated, so "BOS_RETEST
        # produced zero trades" could not be read as a statement about
        # BOS_RETEST. Restricting the enabled set is how a single concept is
        # isolated for measurement; it is a gate, so it exists identically in
        # the live agent and the backtester.
        if enabled_triggers is None:
            self.enabled_triggers = set(self.ALL_TRIGGERS)
        else:
            requested = {str(t).strip().upper() for t in enabled_triggers if str(t).strip()}
            unknown = requested - set(self.ALL_TRIGGERS)
            if unknown:
                raise ValueError(
                    f"Unknown trigger(s) {sorted(unknown)}; "
                    f"valid: {sorted(self.ALL_TRIGGERS)}")
            if not requested:
                raise ValueError("enabled_triggers must not be empty")
            self.enabled_triggers = requested

    def _targets(self, direction: str, entry: float, sl: float) -> tuple:
        """Return (tp1, tp2) at the configured R multiples of the entry->SL leg."""
        risk = abs(entry - sl)
        if risk <= 0:
            return 0.0, 0.0
        if direction == "BULLISH":
            return entry + risk * self.tp1_r, entry + risk * self.tp2_r
        return entry - risk * self.tp1_r, entry - risk * self.tp2_r

    # ── Public API ────────────────────────────────────────────────────────────

    def step1_liquidity(self, df_m5: pd.DataFrame, symbol: str) -> MicroLiquidity:
        """Identify micro liquidity pools on M5."""
        cp = float(df_m5['close'].iloc[-1])
        liq = MicroLiquidity(current_price=cp)

        highs = df_m5['high'].values[-50:]
        lows  = df_m5['low'].values[-50:]

        # Equal highs: two or more bars that have nearly the same high
        liq.equal_highs = self._find_equals(highs, cp, "high")
        liq.equal_lows  = self._find_equals(lows,  cp, "low")

        # Session extreme (first candle of today's session)
        today_bars = df_m5[df_m5['time'].dt.date == df_m5['time'].dt.date.iloc[-1]] if 'time' in df_m5.columns else df_m5
        if len(today_bars) > 0:
            liq.session_high = float(today_bars['high'].max())
            liq.session_low  = float(today_bars['low'].min())

        return liq

    def step2_trigger(self, df_m5: pd.DataFrame, df_m1: pd.DataFrame,
                      liq: MicroLiquidity, symbol: str,
                      session_open_price: Optional[float] = None,
                      profile=None,
                      edge_tolerance_frac: float = 0.10,
                      poc_band_frac: float = 0.10,
                      vplr_ctx=None, m15_fvg_entry=None, htf_crt=None) -> SATrigger:
        """
        Check every enabled trigger, return the highest-priority one that fired.

        Selection is still first-match by priority, but every detector that
        fires is now recorded on the winner's `matched_triggers`. Two setups
        agreeing on a bar is information — "VP and SWEEP_REJECTION both
        qualified, VP won on priority" is a fact worth being able to count, and
        the old early-return threw it away.

        Cost note: the extra detectors only run while a *higher*-priority one
        has not yet fired, so the common case (nothing fires) is unchanged and
        the expensive case (something fires early) gets cheaper, not dearer.
        """
        winner: Optional[SATrigger] = None
        matched: List[str] = []

        def consider(trig: Optional[SATrigger]) -> None:
            nonlocal winner
            if trig is not None and trig.detected:
                matched.append(trig.trigger_type)
                if winner is None:
                    winner = trig

        # L-019: an executable HTF raid/reclaim/FVG return has first priority.
        if "HTF_CRT_SWEEP" in self.enabled_triggers and htf_crt is not None and htf_crt.allow:
            p = htf_crt
            consider(SATrigger(
                detected=True, trigger_type="HTF_CRT_SWEEP", direction=p.direction,
                entry_price=p.entry, stop_loss=p.stop, tp1=p.target, tp2=p.target,
                confidence="MEDIUM", swept_level=p.swept_level, htf_crt=p,
                description=f"{p.timeframe} CRT raid {p.swept_level:.4f}; "
                            f"M15 reclaim and FVG return {p.fvg_low:.4f}-{p.fvg_high:.4f}"))

        # Needs an anchored H4 profile, which the caller
        # builds from a CLOSED frame — absent one, the detector is skipped
        # entirely rather than run against a degraded profile.
        if "VP_LIQUIDITY_REACTION" in self.enabled_triggers and vplr_ctx is not None:
            consider(self._check_vp_liquidity_reaction(
                df_m5, df_m1, liq, symbol, vplr_ctx))

        # L-018: a qualified M15 zone entry takes precedence over a local
        # sweep. It remains in the FVG_FILL family and respects whitelists.
        if ("FVG_FILL" in self.enabled_triggers and m15_fvg_entry is not None
                and m15_fvg_entry.allow):
            p = m15_fvg_entry
            consider(SATrigger(
                detected=True, trigger_type="FVG_FILL", direction=p.direction,
                entry_price=p.entry, stop_loss=p.stop, tp1=p.target, tp2=p.target,
                confidence="MEDIUM", fvg_entry=p,
                description=f"M15 FVG {p.fvg_low:.4f}-{p.fvg_high:.4f}; "
                            f"target before broken level {p.target_level:.4f}"))

        if "SWEEP_REJECTION" in self.enabled_triggers:
            consider(self._check_sweep_rejection(df_m5, liq, symbol))

        if "FVG_FILL" in self.enabled_triggers and m15_fvg_entry is None:
            consider(self._check_fvg_fill(df_m1, liq, symbol))

        if "BOS_RETEST" in self.enabled_triggers:
            consider(self._check_bos_retest(df_m5, liq, symbol))

        if "JUDAS" in self.enabled_triggers and session_open_price is not None:
            consider(self._check_judas_swing(df_m5, session_open_price, symbol))

        # Last in the first-match order: the value-area fade is the only
        # trigger that needs an H4 profile, and it should not pre-empt a
        # liquidity-anchored setup that fired on the same bar.
        if "VALUE_AREA_FADE" in self.enabled_triggers and profile is not None:
            consider(self._check_value_area_fade(
                df_m5, profile, symbol, edge_tolerance_frac, poc_band_frac))

        if winner is None:
            return SATrigger()
        winner.matched_triggers = matched
        return winner

    def _check_vp_liquidity_reaction(self, df_trigger: pd.DataFrame,
                                     df_confirm: pd.DataFrame,
                                     liq: MicroLiquidity, symbol: str,
                                     ctx) -> SATrigger:
        """
        A liquidity raid at an anchored H4 profile level, confirmed by an MSS.

        Detection lives in `scalper/vp_liquidity_trigger.py`; this wraps it in
        the engine's own geometry so the targets and the cost gate are computed
        exactly as for every other trigger — TP1 = 2R via `_targets`, never the
        POC, for the same reason VALUE_AREA_FADE does not use it (§13.1).
        """
        from scalper import vp_liquidity_trigger as VPLR

        sig = VPLR.detect_with_context(df_trigger, df_confirm, ctx, liq)
        if not sig.detected:
            return SATrigger()

        entry, sl = sig.entry_price, sig.stop_loss
        if (sig.direction == "BEARISH" and sl <= entry) or \
           (sig.direction == "BULLISH" and sl >= entry):
            return SATrigger()
        tp1, tp2 = self._targets(sig.direction, entry, sl)
        return SATrigger(
            detected=True,
            trigger_type="VP_LIQUIDITY_REACTION",
            direction=sig.direction,
            entry_price=entry,
            stop_loss=sl,
            tp1=tp1,
            tp2=tp2,
            confidence=("HIGH" if len(sig.confluences) >= 3 else "MEDIUM"),
            description=sig.summary(),
            swept_level=sig.interacted_level,
            requires_displacement=False,
            vplr=sig,
        )

    def _check_value_area_fade(self, df: pd.DataFrame, profile, symbol: str,
                               edge_tolerance_frac: float,
                               poc_band_frac: float) -> SATrigger:
        """
        Fade the value-area edge: sell rejection at VAH, buy rejection at VAL,
        nothing at the POC. Detection lives in `scalper/va_fade_trigger.py`;
        this wraps it in the engine's own geometry so the stop, targets and
        cost gate are computed exactly as for every other trigger.

        Targets come from `_targets`, i.e. TP1 = 2R (SS13.1). The POC is the
        intuitive fade target and is deliberately NOT used: on a tight value
        area it can sit well inside 1R, which is the sub-2R geometry that made
        this system arithmetically unprofitable at its measured win rate.
        """
        from scalper import va_fade_trigger as VAF

        sig = VAF.detect(df, profile,
                         edge_tolerance_frac=edge_tolerance_frac,
                         poc_band_frac=poc_band_frac)
        if not sig.detected:
            return SATrigger()

        entry, sl = sig.entry, sig.stop_loss
        if (sig.direction == "BEARISH" and sl <= entry) or \
           (sig.direction == "BULLISH" and sl >= entry):
            return SATrigger()
        tp1, tp2 = self._targets(sig.direction, entry, sl)
        return SATrigger(
            detected=True,
            trigger_type="VALUE_AREA_FADE",
            direction=sig.direction,
            entry_price=entry,
            stop_loss=sl,
            tp1=tp1,
            tp2=tp2,
            confidence="HIGH" if sig.wick_frac >= 0.5 else "MEDIUM",
            description=f"{sig.reason} | {sig.levels.summary()}",
            swept_level=profile.vah if sig.direction == "BEARISH" else profile.val,
            requires_displacement=False,
        )

    def step3_validate(self, trigger: SATrigger, spread_pips: float,
                       symbol: str, sl_pips: float = 0.0) -> Tuple[bool, str]:
        """
        Validate entry: confirmed candle close + acceptable spread.

        Spread is checked against two ceilings (both must pass):
          1. Absolute outlier kill — per-symbol _max_spread() — catches
             broker-error / news-spike spreads.
          2. SL-relative ceiling — spread ≤ MAX_SPREAD_TO_SL_FRAC × sl_pips —
             ensures transaction cost can never dominate the risk budget on
             any individual setup, regardless of pip count.

        Args:
            trigger     : Detected SA trigger.
            spread_pips : Current bid-ask spread in pips.
            symbol      : Instrument code.
            sl_pips     : Distance from entry to stop loss in pips. If 0, only
                          the absolute ceiling is enforced (back-compat).

        Returns (is_valid, reason).
        """
        if not trigger.detected:
            return False, "No trigger detected"

        if trigger.entry_price <= 0:
            return False, "Invalid entry price"

        if trigger.stop_loss <= 0:
            return False, "Invalid stop loss"

        # Layer 1 — Absolute outlier kill
        max_spread = self._max_spread(symbol)
        if spread_pips > max_spread:
            return False, (f"Spread {spread_pips:.1f}p exceeds absolute cap "
                           f"{max_spread:.1f}p for {symbol}")

        # Layer 2 — SL-relative ceiling (skipped if sl_pips not supplied)
        if sl_pips > 0:
            frac = spread_pips / sl_pips
            if frac > self.MAX_SPREAD_TO_SL_FRAC:
                return False, (f"Spread {spread_pips:.1f}p is {frac*100:.0f}% "
                               f"of SL {sl_pips:.1f}p (max "
                               f"{int(self.MAX_SPREAD_TO_SL_FRAC*100)}%)")

        # Layer 3 — Minimum SL distance (noise-grade sweep filter)
        if sl_pips > 0:
            min_sl = self.MIN_SL_PIPS.get(symbol, self.MIN_SL_PIPS["DEFAULT"])
            if sl_pips < min_sl:
                return False, (f"SL {sl_pips:.1f}p below institutional floor "
                               f"{min_sl}p for {symbol} (noise-grade sweep)")

        # Layer 4 — Net-R after transaction cost.
        #
        # The gross R multiple is what the geometry promises; the net multiple
        # is what the account receives. Round-turn spread is paid twice (in on
        # the ask, out on the bid) and widens the effective stop while
        # shortening the effective target. A setup that only clears costs at
        # 1.1R is not worth the exposure, so require MIN_NET_R.
        #
        # Published minimum-risk work (MQL5 art. 18991) gives the survival
        # condition as RRR > (1 - win_rate) / win_rate; this layer enforces the
        # cost-adjusted side of that inequality at the point of entry.
        if sl_pips > 0 and trigger.tp1 > 0 and trigger.entry_price > 0:
            reward_pips = abs(trigger.tp1 - trigger.entry_price) / (
                abs(trigger.entry_price - trigger.stop_loss) + 1e-10) * sl_pips
            round_turn = 2.0 * spread_pips
            net_reward = reward_pips - round_turn
            net_risk = sl_pips + round_turn
            net_r = net_reward / (net_risk + 1e-10)
            if net_r < self.MIN_NET_R:
                return False, (
                    f"Net R {net_r:.2f} below floor {self.MIN_NET_R:.2f} "
                    f"(gross target {reward_pips:.1f}p vs SL {sl_pips:.1f}p, "
                    f"round-turn cost {round_turn:.1f}p)"
                )

        return True, "VALID"

    # ── Step 2 Trigger Detectors ──────────────────────────────────────────────

    def _check_sweep_rejection(self, df: pd.DataFrame, liq: MicroLiquidity,
                                symbol: str) -> SATrigger:
        """Liquidity swept, immediate wick rejection (last 3 bars)."""
        trig = SATrigger()
        if len(df) < 4:
            return trig

        recent = df.iloc[-4:]
        cp     = float(df['close'].iloc[-1])
        last   = df.iloc[-1]
        atr    = self._atr(df)

        # Bullish: wick below SSL, close back above → BUY
        ssl = liq.nearest_ssl
        if ssl and recent['low'].min() < ssl:
            if last['close'] > ssl and last['close'] > last['open']:
                ratio = _wick_ratio(recent.loc[recent['low'].idxmin()], "BULLISH")
                trig.wick_ratio = ratio
                if self.sweep_wick_filter and ratio < self.sweep_wick_ratio:
                    return trig
                sl  = float(recent['low'].min()) - atr * 0.2
                tp1, tp2 = self._targets("BULLISH", cp, sl)
                trig.detected     = True
                trig.trigger_type = "SWEEP_REJECTION"
                trig.direction    = "BULLISH"
                trig.entry_price  = cp
                trig.stop_loss    = sl
                trig.tp1          = tp1
                trig.tp2          = tp2
                trig.swept_level  = ssl
                trig.confidence   = "HIGH"
                trig.description  = f"SSL {ssl:.4f} swept, bullish rejection confirmed"
                return trig

        # Bearish: wick above BSL, close back below → SELL
        bsl = liq.nearest_bsl
        if bsl and recent['high'].max() > bsl:
            if last['close'] < bsl and last['close'] < last['open']:
                ratio = _wick_ratio(recent.loc[recent['high'].idxmax()], "BEARISH")
                trig.wick_ratio = ratio
                if self.sweep_wick_filter and ratio < self.sweep_wick_ratio:
                    return trig
                sl  = float(recent['high'].max()) + atr * 0.2
                tp1, tp2 = self._targets("BEARISH", cp, sl)
                trig.detected     = True
                trig.trigger_type = "SWEEP_REJECTION"
                trig.direction    = "BEARISH"
                trig.entry_price  = cp
                trig.stop_loss    = sl
                trig.tp1          = tp1
                trig.tp2          = tp2
                trig.swept_level  = bsl
                trig.confidence   = "HIGH"
                trig.description  = f"BSL {bsl:.4f} swept, bearish rejection confirmed"
                return trig

        return trig

    def _check_fvg_fill(self, df_m1: pd.DataFrame, liq: MicroLiquidity,
                         symbol: str) -> SATrigger:
        """M1/M5 FVG fully mitigated, price reverses."""
        trig = SATrigger()
        if len(df_m1) < 5:
            return trig

        cp   = float(df_m1['close'].iloc[-1])
        last = df_m1.iloc[-1]
        atr  = self._atr(df_m1)

        for i in range(2, min(len(df_m1) - 1, 20)):
            h = df_m1['high'].values
            l = df_m1['low'].values
            c = df_m1['close'].values

            # Bullish FVG: gap between candle[i-2] high and candle[i] low
            if l[i] > h[i-2]:
                fvg_low  = float(h[i-2])
                fvg_high = float(l[i])
                # Price has now filled into this FVG and is closing bullish
                if fvg_low <= cp <= fvg_high and last['close'] > last['open']:
                    sl  = fvg_low - atr * 0.3
                    tp1, tp2 = self._targets("BULLISH", cp, sl)
                    trig.detected     = True
                    trig.trigger_type = "FVG_FILL"
                    trig.direction    = "BULLISH"
                    trig.entry_price  = cp
                    trig.stop_loss    = sl
                    trig.tp1          = tp1
                    trig.tp2          = tp2
                    trig.confidence   = "MEDIUM"
                    trig.description  = f"Bullish FVG {fvg_low:.4f}-{fvg_high:.4f} filled, reversal up"
                    return trig

            # Bearish FVG
            if h[i] < l[i-2]:
                fvg_high = float(l[i-2])
                fvg_low  = float(h[i])
                if fvg_low <= cp <= fvg_high and last['close'] < last['open']:
                    sl  = fvg_high + atr * 0.3
                    tp1, tp2 = self._targets("BEARISH", cp, sl)
                    trig.detected     = True
                    trig.trigger_type = "FVG_FILL"
                    trig.direction    = "BEARISH"
                    trig.entry_price  = cp
                    trig.stop_loss    = sl
                    trig.tp1          = tp1
                    trig.tp2          = tp2
                    trig.confidence   = "MEDIUM"
                    trig.description  = f"Bearish FVG {fvg_low:.4f}-{fvg_high:.4f} filled, reversal down"
                    return trig

        return trig

    def _check_bos_retest(self, df: pd.DataFrame, liq: MicroLiquidity,
                           symbol: str) -> SATrigger:
        """Clean BOS on M5, retest of broken level."""
        trig = SATrigger()
        if len(df) < 10:
            return trig

        cp   = float(df['close'].iloc[-1])
        last = df.iloc[-1]
        atr  = self._atr(df)
        highs = df['high'].values
        lows  = df['low'].values
        closes = df['close'].values

        # Bullish BOS: recent close broke above a recent swing high and now retests
        for i in range(5, min(len(df)-2, 20)):
            swing_high = float(highs[-i])
            # BOS occurred (recent close broke above)
            if closes[-2] > swing_high and cp <= swing_high * 1.001:
                sl  = float(lows[-3:].min()) - atr * 0.2
                tp1, tp2 = self._targets("BULLISH", cp, sl)
                trig.detected     = True
                trig.trigger_type = "BOS_RETEST"
                trig.direction    = "BULLISH"
                trig.entry_price  = cp
                trig.stop_loss    = sl
                trig.tp1          = tp1
                trig.tp2          = tp2
                trig.confidence   = "MEDIUM"
                trig.requires_displacement = True  # Must be validated by SA Consul
                trig.description  = f"Bullish BOS above {swing_high:.4f}, retesting level"
                return trig

        # Bearish BOS: recent close broke below a recent swing low and now
        # retests. This branch did not exist before — BOS_RETEST could only
        # ever fire long, which is why it never appeared once in 287 logged
        # live trades. Mirrors the bullish logic exactly.
        for i in range(5, min(len(df) - 2, 20)):
            swing_low = float(lows[-i])
            if closes[-2] < swing_low and cp >= swing_low * 0.999:
                sl  = float(highs[-3:].max()) + atr * 0.2
                tp1, tp2 = self._targets("BEARISH", cp, sl)
                trig.detected     = True
                trig.trigger_type = "BOS_RETEST"
                trig.direction    = "BEARISH"
                trig.entry_price  = cp
                trig.stop_loss    = sl
                trig.tp1          = tp1
                trig.tp2          = tp2
                trig.confidence   = "MEDIUM"
                trig.requires_displacement = True
                trig.description  = f"Bearish BOS below {swing_low:.4f}, retesting level"
                return trig

        return trig

    def _check_judas_swing(self, df: pd.DataFrame, session_open_price: float,
                            symbol: str) -> SATrigger:
        """Session open Judas: first 15-min move fades by 50%+."""
        trig = SATrigger()
        if len(df) < 5:
            return trig

        cp   = float(df['close'].iloc[-1])
        atr  = self._atr(df)
        # First 3 bars from session open = ~15 minutes on M5
        early = df.iloc[-15:-12] if len(df) >= 15 else df.iloc[:3]
        if early.empty:
            return trig

        session_low  = float(early['low'].min())
        session_high = float(early['high'].max())
        initial_move = session_high - session_low

        # Bearish Judas: price rallied initially but now 50%+ retraced downward
        if session_high > session_open_price * 1.001:
            fade = session_high - cp
            if fade >= initial_move * 0.5 and cp < session_open_price:
                sl  = session_high + atr * 0.3
                tp1, tp2 = self._targets("BEARISH", cp, sl)
                trig.detected     = True
                trig.trigger_type = "JUDAS"
                trig.direction    = "BEARISH"
                trig.entry_price  = cp
                trig.stop_loss    = sl
                trig.tp1          = tp1
                trig.tp2          = tp2
                trig.confidence   = "HIGH"
                trig.description  = f"Judas Swing: fake rally to {session_high:.4f}, now fading"
                return trig

        # Bullish Judas: price dropped initially then 50%+ recovered
        if session_low < session_open_price * 0.999:
            fade = cp - session_low
            if fade >= initial_move * 0.5 and cp > session_open_price:
                sl  = session_low - atr * 0.3
                tp1, tp2 = self._targets("BULLISH", cp, sl)
                trig.detected     = True
                trig.trigger_type = "JUDAS"
                trig.direction    = "BULLISH"
                trig.entry_price  = cp
                trig.stop_loss    = sl
                trig.tp1          = tp1
                trig.tp2          = tp2
                trig.confidence   = "HIGH"
                trig.description  = f"Judas Swing: fake drop to {session_low:.4f}, now recovering"
                return trig

        return trig

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _find_equals(self, values: np.ndarray, current_price: float,
                     side: str) -> List[float]:
        """
        Cluster near-equal highs (or lows) into single liquidity levels.

        Rewritten from an O(n^2) pairwise scan that ignored both of its own
        `current_price` / `side` arguments and emitted one level per matching
        PAIR — so three equal highs produced three nearly-identical levels and
        inflated the pool count. This version sorts once, walks adjacent values
        into clusters within tolerance, and keeps only levels on the relevant
        side of price (highs above, lows below), which is all the caller can
        use anyway.
        """
        if values is None or len(values) < 2:
            return []

        ordered = np.sort(np.asarray(values, dtype=float))
        clusters: List[List[float]] = [[float(ordered[0])]]
        for value in ordered[1:]:
            anchor = clusters[-1][0]
            if abs(value - anchor) / (abs(anchor) + 1e-10) < self.eq_tol:
                clusters[-1].append(float(value))
            else:
                clusters.append([float(value)])

        levels: List[float] = []
        for group in clusters:
            if len(group) < 2:            # a lone extreme is not "equal highs"
                continue
            level = round(sum(group) / len(group), 5)
            if side == "high" and level <= current_price:
                continue
            if side == "low" and level >= current_price:
                continue
            levels.append(level)
        return levels

    def _atr(self, df: pd.DataFrame, period: int = 10) -> float:
        if len(df) < period + 1:
            return float((df['high'] - df['low']).mean())
        hi, lo, cl = df['high'].values, df['low'].values, df['close'].values
        trs = [max(hi[i]-lo[i], abs(hi[i]-cl[i-1]), abs(lo[i]-cl[i-1]))
               for i in range(1, len(df))]
        return float(np.mean(trs[-period:]))

    def _max_spread(self, symbol: str) -> float:
        """
        Absolute spread ceiling per symbol (outlier kill only — the
        SL-relative ceiling in step3_validate provides the adaptive guard).
        Calibrated to Exness demo realities during London/NY volatility.
        """
        spreads = {
            "XAUUSD": 8.0,   # was 3.0 — gold runs 4-8p during London
            "XAGUSD": 5.0,   # was implicit default — silver widens too
            "USOIL":  6.0,   # was implicit default
            "GBPUSD": 3.0,
            "EURUSD": 2.5,
            "US30":   8.0,
            "NAS100": 8.0,
        }
        return spreads.get(symbol, 5.0)
