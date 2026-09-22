"""
VP_LIQUIDITY_REACTION — a liquidity reaction AT a volume-profile level.

The premise, and how it differs from the two VP experiments already rejected
---------------------------------------------------------------------------
A volume-profile level is not a signal. It is a place where a lot of business
was done, which means it is a place where orders are resting — and resting
orders are what a raid goes hunting for. So the profile answers *where*, and
never *which way*.

    vp_gate.py         VP as permission        -> REJECTED (L-005)
    va_fade_trigger.py VP as the signal itself -> zero trades (L-009)
    this module        VP as the location, the
                       liquidity raid as the
                       signal, structure as the
                       confirmation

The difference is load-bearing rather than cosmetic. L-005's attribution found
`AT_POC` to be the single best location bucket in the book (42 trades, PF 2.10)
while a POC veto lost money — i.e. the POC is where this strategy's good trades
already happen. That is precisely what you would expect if the POC marks resting
liquidity: a sweep there has the most order flow to reject against. This module
is the first one to trade that reading directly instead of filtering on it.

The sequence, in the order the market produces it
-------------------------------------------------
    1. an H4 impulse leg forms                     (anchored_vp.py)
    2. the leg's POC/VAH/VAL coincides with a
       liquidity level — equal highs, PDH, the
       session high, a swing                       -> a candidate zone
    3. price raids that level                      -> BSL taken
    4. price reclaims it, or displaces away        -> the raid failed
    5. lower-timeframe structure breaks            -> MSS/CHoCH confirms
    6. something else agrees                       -> confluence
    -> SHORT. Mirror for SSL/long.

Steps 3-6 are read on CLOSED M15/M5 bars while the H4 candle that did the raid
is still forming. That is the whole point of the design: waiting for the H4 to
close means entering after the reaction, not into it. Only the profile — step 1
— needs closed H4, which is what keeps the location non-repainting.

None of steps 2-6 alone fires anything. `detect` returns a non-detected signal
until every clause holds, so the bar stays available to SWEEP_REJECTION and the
rest of the priority chain.

POC is bidirectional. VAH is not "sell here" and VAL is not "buy here" — the
direction is decided in step 3 by which side the liquidity sat on, and in step 5
by which way structure actually broke. Selling a VAL raid is legitimate and the
detector will do it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from scalper.anchored_vp import AnchoredProfile, atr

#: Which profile level the reaction happened at.
LEVEL_POC = "POC"
LEVEL_VAH = "VAH"
LEVEL_VAL = "VAL"
VALID_LEVELS = frozenset({LEVEL_POC, LEVEL_VAH, LEVEL_VAL})

#: Where the raided liquidity came from. Ordered most-institutional first; used
#: only for telemetry and for the PD/session confluence test.
SRC_PDH = "PDH"
SRC_PDL = "PDL"
SRC_SESSION_HIGH = "SESSION_HIGH"
SRC_SESSION_LOW = "SESSION_LOW"
SRC_EQUAL_HIGHS = "EQUAL_HIGHS"
SRC_EQUAL_LOWS = "EQUAL_LOWS"
SRC_SWING_HIGH = "SWING_HIGH"
SRC_SWING_LOW = "SWING_LOW"

_EXTERNAL_SOURCES = frozenset({SRC_PDH, SRC_PDL,
                               SRC_SESSION_HIGH, SRC_SESSION_LOW})


def parse_manual_levels(value: str) -> Tuple[Tuple[str, float], ...]:
    """Parse ``POC=4331,VAL=4314`` into validated immutable levels."""
    if not value:
        return ()
    levels = []
    seen = set()
    for item in value.split(","):
        try:
            name, raw_price = item.split("=", 1)
            name = name.strip().upper()
            price = float(raw_price.strip())
        except (TypeError, ValueError):
            raise ValueError(
                "manual VP levels must use NAME=PRICE, e.g. POC=4331,VAL=4314")
        if name not in VALID_LEVELS:
            raise ValueError(f"unknown manual VP level {name!r}; valid: {sorted(VALID_LEVELS)}")
        if not np.isfinite(price) or price <= 0:
            raise ValueError(f"manual VP level {name} must have a finite positive price")
        if name in seen:
            raise ValueError(f"manual VP level {name} was supplied more than once")
        seen.add(name)
        levels.append((name, price))
    return tuple(levels)

#: Confluence labels. Kept as constants so the reject log and the tests agree on
#: spelling and a typo cannot silently drop a confluence.
CONF_DISPLACEMENT = "DISPLACEMENT"
CONF_RECLAIM = "RECLAIM"
CONF_FVG = "FVG"
CONF_ORDER_BLOCK = "OB"
CONF_REJECTION_WICK = "REJECTION_WICK"
CONF_PD_LIQUIDITY = "PD_SESSION_LIQUIDITY"
CONF_HTF_STRUCTURE = "HTF_STRUCTURE"
CONF_ATR_MOMENTUM = "ATR_MOMENTUM"


@dataclass(frozen=True)
class LiquidityLevel:
    """One candidate pool, with where it came from."""
    price: float
    source: str
    is_buy_side: bool          # True = BSL (above), False = SSL (below)


@dataclass(frozen=True)
class VPLRParams:
    """
    Every tunable this trigger has, in one object.

    Exists so the live agent and the simulator cannot drift: both build it with
    `from_decision_params()` and neither restates a number. Passing fifteen
    separate keyword arguments through `step2_trigger` would have reproduced
    exactly the hand-maintained duplication that `decision_params` was created
    to end (§13.4).
    """
    atr_period: int
    zone_atr: float
    zone_va_frac: float
    raid_lookback: int
    min_sweep_atr: float
    raid_max_age_bars: int
    displacement_atr: float
    min_wick_frac: float
    mss_lookback: int
    mss_swing_lookback: int
    swing_lookback: int
    min_confluence: int
    sl_buffer_atr: float
    h4_swing_lookback: int
    min_leg_bars: int
    min_leg_atr: float
    target_bins: int
    value_area_pct: float

    @classmethod
    def from_decision_params(cls, dp) -> "VPLRParams":
        return cls(
            atr_period=dp.VPLR_ATR_PERIOD,
            zone_atr=dp.VPLR_ZONE_ATR,
            zone_va_frac=dp.VPLR_ZONE_VA_FRAC,
            raid_lookback=dp.VPLR_RAID_LOOKBACK,
            min_sweep_atr=dp.VPLR_MIN_SWEEP_ATR,
            raid_max_age_bars=dp.VPLR_RAID_MAX_AGE_BARS,
            displacement_atr=dp.VPLR_DISPLACEMENT_ATR,
            min_wick_frac=dp.VPLR_MIN_WICK_FRAC,
            mss_lookback=dp.VPLR_MSS_LOOKBACK,
            mss_swing_lookback=dp.VPLR_MSS_SWING_LOOKBACK,
            swing_lookback=dp.VPLR_SWING_LOOKBACK,
            min_confluence=dp.VPLR_MIN_CONFLUENCE,
            sl_buffer_atr=dp.VPLR_SL_BUFFER_ATR,
            h4_swing_lookback=dp.VPLR_H4_SWING_LOOKBACK,
            min_leg_bars=dp.VPLR_MIN_LEG_BARS,
            min_leg_atr=dp.VPLR_MIN_LEG_ATR,
            target_bins=dp.VPLR_TARGET_BINS,
            value_area_pct=dp.VPLR_VALUE_AREA_PCT,
        )


@dataclass(frozen=True)
class VPLRContext:
    """
    What the caller must supply for the trigger to be evaluated at all.

    `anchored` is built by the caller from a CLOSED H4 frame, so the trigger
    engine never touches an H4 fetch and cannot accidentally read a forming bar.
    """
    anchored: Optional[AnchoredProfile]
    params: VPLRParams
    df_d1_closed: Optional[pd.DataFrame] = None
    htf_trend: str = ""
    # Operator-supplied profile levels for a time-bounded live watch. These
    # replace the automatically anchored POC/VAH/VAL locations, while leaving
    # the raid, reclaim/displacement, MSS and confluence contract unchanged.
    manual_levels: Tuple[Tuple[str, float], ...] = ()


@dataclass
class VPLRSignal:
    """
    Detection result plus the full evidence chain.

    Every field below is written to telemetry whether or not the setup fired, so
    a near-miss can be read back without re-running the bar. `reject_reason`
    names the first clause that failed, which is what makes the funnel countable
    (`detect` short-circuits, so exactly one clause is named).
    """
    detected: bool = False
    direction: str = "NONE"                 # BULLISH | BEARISH
    entry_price: float = 0.0
    stop_loss: float = 0.0

    # -- evidence ----------------------------------------------------------
    vp_level_name: str = ""                 # POC | VAH | VAL
    vp_level_price: float = 0.0
    interacted_level: float = 0.0
    level_source: str = ""
    sweep_depth_atr: float = 0.0
    reclaimed: bool = False
    displaced: bool = False
    mss_level: float = 0.0
    mss_time: Optional[pd.Timestamp] = None
    raid_time: Optional[pd.Timestamp] = None
    confluences: List[str] = field(default_factory=list)
    profile_type: str = "ANCHORED"
    anchor_time: Optional[pd.Timestamp] = None
    anchor_extreme_time: Optional[pd.Timestamp] = None
    poc: float = 0.0
    vah: float = 0.0
    val: float = 0.0
    reject_reason: str = ""

    def summary(self) -> str:
        if not self.detected:
            return f"VPLR none ({self.reject_reason})"
        return (f"VPLR {self.direction} at {self.vp_level_name} "
                f"{self.vp_level_price:.2f} | raided {self.level_source} "
                f"{self.interacted_level:.2f} by {self.sweep_depth_atr:.2f}xATR | "
                f"MSS {self.mss_level:.2f} | "
                f"confluence {'+'.join(self.confluences)}")


def fractal_swings(df: pd.DataFrame, lookback: int
                   ) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
    """
    Fractal swing highs and lows as (index, price), right-side confirmed.

    Same fractal definition as `va_fade_trigger._swings` and
    `StructureEngine._identify_swings` — an extreme with `lookback` bars either
    side — but returning indices, which the raid and MSS logic need in order to
    say "before" and "after". The loop stops at `len - lookback`, so a swing is
    only ever reported once `lookback` bars have closed after it.
    """
    if df is None or len(df) < 2 * lookback + 1:
        return [], []
    highs = df["high"].to_numpy(dtype=np.float64)
    lows = df["low"].to_numpy(dtype=np.float64)
    sh: List[Tuple[int, float]] = []
    sl: List[Tuple[int, float]] = []
    for i in range(lookback, len(highs) - lookback):
        wh = highs[i - lookback:i + lookback + 1]
        wl = lows[i - lookback:i + lookback + 1]
        if highs[i] == wh.max() and (wh == highs[i]).sum() == 1:
            sh.append((i, float(highs[i])))
        if lows[i] == wl.min() and (wl == lows[i]).sum() == 1:
            sl.append((i, float(lows[i])))
    return sh, sl


def collect_liquidity(df_trigger: pd.DataFrame,
                      liq,
                      df_d1_closed: Optional[pd.DataFrame],
                      swing_lookback: int) -> List[LiquidityLevel]:
    """
    Every pool this trigger is willing to call "liquidity", from existing code.

    `liq` is the `MicroLiquidity` that step 1 already produced — equal highs and
    lows plus the session extremes — so nothing is recomputed. Swings come from
    the trigger frame and PDH/PDL from the previous COMPLETED daily bar, which
    is the level `liquidity_engine._prev_day_hl` publishes at the highest
    strength in the map and which no trigger in this repository has ever been
    able to see (§13.13).
    """
    levels: List[LiquidityLevel] = []

    for price in getattr(liq, "equal_highs", None) or []:
        levels.append(LiquidityLevel(float(price), SRC_EQUAL_HIGHS, True))
    for price in getattr(liq, "equal_lows", None) or []:
        levels.append(LiquidityLevel(float(price), SRC_EQUAL_LOWS, False))

    if getattr(liq, "session_high", None):
        levels.append(LiquidityLevel(float(liq.session_high),
                                     SRC_SESSION_HIGH, True))
    if getattr(liq, "session_low", None):
        levels.append(LiquidityLevel(float(liq.session_low),
                                     SRC_SESSION_LOW, False))

    sh, sl = fractal_swings(df_trigger, swing_lookback)
    for _, price in sh:
        levels.append(LiquidityLevel(price, SRC_SWING_HIGH, True))
    for _, price in sl:
        levels.append(LiquidityLevel(price, SRC_SWING_LOW, False))

    # PDH/PDL. `df_d1_closed` must already exclude today's forming daily bar —
    # its high and low grow through the session, so a level read off it would
    # move under the decision. Callers use _closed_tf(..., 1440) / shift=1.
    if df_d1_closed is not None and len(df_d1_closed) >= 1:
        levels.append(LiquidityLevel(float(df_d1_closed["high"].iloc[-1]),
                                     SRC_PDH, True))
        levels.append(LiquidityLevel(float(df_d1_closed["low"].iloc[-1]),
                                     SRC_PDL, False))

    return [lv for lv in levels if lv.price > 0]


def _has_fvg(df: pd.DataFrame, start: int, bearish: bool) -> bool:
    """
    A three-bar imbalance formed at or after `start`, in the trade direction.

    Same geometry the FVG_FILL detector uses: a bearish gap is
    `high[i] < low[i-2]`, a bullish one `low[i] > high[i-2]`.
    """
    if df is None or len(df) < 3:
        return False
    highs = df["high"].to_numpy(dtype=np.float64)
    lows = df["low"].to_numpy(dtype=np.float64)
    for i in range(max(start, 2), len(df)):
        if bearish and highs[i] < lows[i - 2]:
            return True
        if not bearish and lows[i] > highs[i - 2]:
            return True
    return False


def _has_order_block(df: pd.DataFrame, disp_idx: Optional[int],
                     bearish: bool) -> bool:
    """
    The last opposite-direction candle immediately before the displacement.

    The textbook order block: for a short, the final up-close candle before the
    move down. Absent a displacement bar there is nothing to anchor it to, so
    this returns False rather than guessing.
    """
    if df is None or disp_idx is None or disp_idx <= 0:
        return False
    prev = df.iloc[disp_idx - 1]
    if bearish:
        return float(prev["close"]) > float(prev["open"])
    return float(prev["close"]) < float(prev["open"])


def detect(df_trigger: pd.DataFrame,
           df_confirm: pd.DataFrame,
           anchored: Optional[AnchoredProfile],
           liq,
           *,
           manual_levels: Tuple[Tuple[str, float], ...] = (),
           df_d1_closed: Optional[pd.DataFrame] = None,
           htf_trend: str = "",
           atr_period: int,
           zone_atr: float,
           zone_va_frac: float,
           raid_lookback: int,
           min_sweep_atr: float,
           displacement_atr: float,
           min_wick_frac: float,
           raid_max_age_bars: int,
           mss_lookback: int,
           mss_swing_lookback: int,
           swing_lookback: int,
           min_confluence: int,
           sl_buffer_atr: float) -> VPLRSignal:
    """
    Walk the setup contract. Returns a non-detected signal the moment a clause
    fails, naming that clause — the caller treats that exactly as "no trigger"
    and moves on to the next detector in the priority chain.

    Every frame handed in must contain only CLOSED bars.
    """
    sig = VPLRSignal()
    manual_levels = tuple(manual_levels or ())
    if (not manual_levels and
            (anchored is None or anchored.profile is None or not anchored.profile.valid)):
        sig.reject_reason = "no anchored profile"
        return sig

    if manual_levels:
        sig.profile_type = "MANUAL"
        for name, price in manual_levels:
            if name == LEVEL_POC:
                sig.poc = float(price)
            elif name == LEVEL_VAH:
                sig.vah = float(price)
            elif name == LEVEL_VAL:
                sig.val = float(price)
    else:
        sig.poc, sig.vah, sig.val = anchored.poc, anchored.vah, anchored.val
        sig.anchor_time = anchored.anchor_time
        sig.anchor_extreme_time = anchored.extreme_time

    if df_trigger is None or len(df_trigger) < max(raid_lookback, 20):
        sig.reject_reason = "trigger frame too short"
        return sig
    if df_confirm is None or len(df_confirm) < 2 * mss_swing_lookback + 2:
        sig.reject_reason = "confirm frame too short"
        return sig

    a = atr(df_trigger, atr_period)
    if a <= 0:
        sig.reject_reason = "no ATR"
        return sig

    # Levels from different supplied profiles do not share a meaningful value
    # area width, so their interaction zone is volatility-scaled only.
    zone = (zone_atr * a if manual_levels else
            max(zone_atr * a, zone_va_frac * anchored.profile.value_area_width))
    if zone <= 0:
        sig.reject_reason = "degenerate zone width"
        return sig

    pools = collect_liquidity(df_trigger, liq, df_d1_closed, swing_lookback)
    if not pools:
        sig.reject_reason = "no liquidity pools"
        return sig

    vp_levels = (manual_levels or
                 ((LEVEL_POC, anchored.poc),
                  (LEVEL_VAH, anchored.vah),
                  (LEVEL_VAL, anchored.val)))

    entry = float(df_trigger["close"].iloc[-1])
    window = df_trigger.iloc[-raid_lookback:]
    w_high = window["high"].to_numpy(dtype=np.float64)
    w_low = window["low"].to_numpy(dtype=np.float64)
    w_open = window["open"].to_numpy(dtype=np.float64)
    w_close = window["close"].to_numpy(dtype=np.float64)
    w_time = (window["time"].to_numpy() if "time" in window.columns else None)

    interacted = False
    raided = False
    candidates = []

    # Enumerate every (VP level, pool) coincidence that carries a RECENT raid,
    # then rank. The previous version walked POC/VAH/VAL in fixed order and,
    # within each, pools sorted by distance to the level — returning the first
    # match. Two things went wrong with that, both seen on XAUUSD 2026-08-27:
    #
    #   * proximity decided the side. A swing low 1.02 from the POC out-ranked
    #     the equal highs 3.3 from it, so a stale SSL long was reported on every
    #     bar of a BSL-raid reversal;
    #   * POC-first short-circuiting meant the VAH — which sat on the actual
    #     raid — was never examined at all.
    #
    # Neither distance-to-the-level nor the level's name says anything about
    # which liquidity event is driving price now. Recency and depth do.
    for level_name, level_price in vp_levels:
        if level_price <= 0:
            continue
        near = [lv for lv in pools if abs(lv.price - level_price) <= zone]
        if near:
            interacted = True

        for pool in near:
            bearish = pool.is_buy_side          # BSL raided -> look for a short

            # -- clause 2/3: the raid, and it must be RECENT -----------------
            if bearish:
                pierced = np.where(w_high > pool.price + min_sweep_atr * a)[0]
            else:
                pierced = np.where(w_low < pool.price - min_sweep_atr * a)[0]
            if len(pierced) == 0:
                continue
            raided = True
            # The most recent pierce, not the first. Taking the first made a
            # raid's score grow the older it got, because the extreme was
            # measured from there to the end of the window.
            raid_pos = int(pierced[-1])
            age = (len(window) - 1) - raid_pos
            if age > raid_max_age_bars:
                continue

            if bearish:
                extreme = float(w_high[raid_pos:].max())
                depth = (extreme - pool.price) / a
            else:
                extreme = float(w_low[raid_pos:].min())
                depth = (pool.price - extreme) / a

            candidates.append((age, -depth, level_name, level_price, pool,
                               bearish, raid_pos, extreme, depth))

    # Freshest raid first; deepest breaks the tie. Sorting on an explicit key
    # keeps this deterministic and independent of pool enumeration order.
    for (_age, _negdepth, level_name, level_price, pool, bearish,
         raid_pos, extreme, depth) in sorted(candidates, key=lambda c: c[:2]):

        # -- clause 4: reclaim OR displacement --------------------------
        last_close = float(w_close[-1])
        reclaimed = (last_close < pool.price) if bearish else (last_close > pool.price)

        disp_idx = None
        body = np.abs(w_close - w_open)
        for j in range(raid_pos, len(window)):
            right_way = (w_close[j] < w_open[j]) if bearish else (w_close[j] > w_open[j])
            if right_way and body[j] >= displacement_atr * a:
                disp_idx = j
                break
        displaced = disp_idx is not None

        if not (reclaimed or displaced):
            continue

        # -- clause 5: MSS / CHoCH on the confirmation frame -------------
        raid_time = (pd.Timestamp(w_time[raid_pos])
                     if w_time is not None else None)
        mss_level, mss_time = _find_mss(
            df_confirm, raid_time, bearish,
            mss_lookback, mss_swing_lookback)
        if mss_level is None:
            continue

        # -- clause 6: confluence ---------------------------------------
        conf: List[str] = []
        if displaced:
            conf.append(CONF_DISPLACEMENT)
        if reclaimed:
            conf.append(CONF_RECLAIM)
        if _has_fvg(window, raid_pos, bearish):
            conf.append(CONF_FVG)
        if _has_order_block(window, disp_idx, bearish):
            conf.append(CONF_ORDER_BLOCK)
        rng = float(w_high[raid_pos] - w_low[raid_pos])
        if rng > 0:
            wick = ((w_high[raid_pos] - max(w_open[raid_pos], w_close[raid_pos]))
                    if bearish else
                    (min(w_open[raid_pos], w_close[raid_pos]) - w_low[raid_pos]))
            if wick / rng >= min_wick_frac:
                conf.append(CONF_REJECTION_WICK)
        if pool.source in _EXTERNAL_SOURCES:
            conf.append(CONF_PD_LIQUIDITY)
        want = "BEARISH" if bearish else "BULLISH"
        if htf_trend and htf_trend == want:
            conf.append(CONF_HTF_STRUCTURE)
        if len(window) >= 2 * atr_period:
            recent = atr(window.iloc[-atr_period:], atr_period)
            prior = atr(window.iloc[-2 * atr_period:-atr_period], atr_period)
            if prior > 0 and recent > prior:
                conf.append(CONF_ATR_MOMENTUM)

        if len(conf) < min_confluence:
            continue

        # -- geometry ----------------------------------------------------
        sl = (extreme + sl_buffer_atr * a) if bearish else (extreme - sl_buffer_atr * a)
        if bearish and sl <= entry:
            continue
        if not bearish and sl >= entry:
            continue

        sig.detected = True
        sig.direction = want
        sig.entry_price = entry
        sig.stop_loss = float(sl)
        sig.vp_level_name = level_name
        sig.vp_level_price = float(level_price)
        sig.interacted_level = pool.price
        sig.level_source = pool.source
        sig.sweep_depth_atr = float(depth)
        sig.reclaimed = reclaimed
        sig.displaced = displaced
        sig.mss_level = float(mss_level)
        sig.mss_time = mss_time
        sig.raid_time = raid_time
        sig.confluences = conf
        return sig

    if not interacted:
        sig.reject_reason = "no VP/liquidity coincidence"
    elif not raided:
        sig.reject_reason = "VP zone touched but no liquidity raid"
    else:
        sig.reject_reason = "raid without reclaim/MSS/confluence"
    return sig


def detect_with_context(df_trigger: pd.DataFrame,
                        df_confirm: pd.DataFrame,
                        ctx: VPLRContext,
                        liq) -> VPLRSignal:
    """
    `detect` with the parameter bundle unpacked. This is what both decision
    paths call, so neither of them enumerates the arguments and they cannot
    fall out of step.
    """
    if ctx is None or (ctx.anchored is None and not ctx.manual_levels):
        sig = VPLRSignal()
        sig.reject_reason = "no anchored profile"
        return sig
    p = ctx.params
    return detect(
        df_trigger, df_confirm, ctx.anchored, liq,
        manual_levels=ctx.manual_levels,
        df_d1_closed=ctx.df_d1_closed,
        htf_trend=ctx.htf_trend,
        atr_period=p.atr_period,
        zone_atr=p.zone_atr,
        zone_va_frac=p.zone_va_frac,
        raid_lookback=p.raid_lookback,
        min_sweep_atr=p.min_sweep_atr,
        raid_max_age_bars=p.raid_max_age_bars,
        displacement_atr=p.displacement_atr,
        min_wick_frac=p.min_wick_frac,
        mss_lookback=p.mss_lookback,
        mss_swing_lookback=p.mss_swing_lookback,
        swing_lookback=p.swing_lookback,
        min_confluence=p.min_confluence,
        sl_buffer_atr=p.sl_buffer_atr,
    )


def build_context(df_h4_closed: pd.DataFrame,
                  symbol: str,
                  params: VPLRParams,
                  df_d1_closed: Optional[pd.DataFrame] = None,
                  htf_trend: str = "") -> VPLRContext:
    """
    Build the anchored profile and wrap it, from one CLOSED H4 frame.

    Both decision paths call this with their own closed-H4 slice — live from
    `copy_rates_from_pos(..., H4, 1, n)`, the simulator from
    `_closed_tf(..., 240)`. Neither re-implements the anchoring.
    """
    from scalper.anchored_vp import build_anchored_profile
    anchored = build_anchored_profile(
        df_h4_closed, symbol,
        swing_lookback=params.h4_swing_lookback,
        min_leg_bars=params.min_leg_bars,
        min_leg_atr=params.min_leg_atr,
        atr_period=params.atr_period,
        target_bins=params.target_bins,
        value_area_pct=params.value_area_pct,
    )
    return VPLRContext(anchored=anchored, params=params,
                       df_d1_closed=df_d1_closed, htf_trend=htf_trend)


def _find_mss(df_confirm: pd.DataFrame,
              raid_time: Optional[pd.Timestamp],
              bearish: bool,
              lookback_bars: int,
              swing_lookback: int) -> Tuple[Optional[float], Optional[pd.Timestamp]]:
    """
    The market-structure shift that confirms the reaction, on closed M5 bars.

    For a short: after the raid, price must CLOSE below the last swing low that
    formed before the reaction high. That is a CHoCH — the first lower low after
    a higher high — and it is the earliest point at which "the raid failed" is a
    statement about structure rather than about one candle.

    Restricting the search to bars at or after `raid_time` is what keeps this
    from matching a break that happened before the liquidity event.
    """
    if df_confirm is None or len(df_confirm) < 2 * swing_lookback + 2:
        return None, None

    df = df_confirm.iloc[-lookback_bars:] if lookback_bars > 0 else df_confirm
    df = df.reset_index(drop=True)

    start = 0
    if raid_time is not None and "time" in df.columns:
        # Normalise BOTH sides to tz-aware UTC before comparing. MT5 frames
        # come back tz-aware and fixtures are often naive; a raw numpy
        # comparison between the two raises rather than returning False, so an
        # unfixed version of this would abort the scan on a live bar.
        times = pd.to_datetime(pd.Series(df["time"].to_numpy()), utc=True)
        cutoff = pd.Timestamp(raid_time)
        cutoff = (cutoff.tz_localize("UTC") if cutoff.tzinfo is None
                  else cutoff.tz_convert("UTC"))
        after = np.where((times >= cutoff).to_numpy())[0]
        if len(after) == 0:
            return None, None
        start = int(after[0])

    highs = df["high"].to_numpy(dtype=np.float64)
    lows = df["low"].to_numpy(dtype=np.float64)
    closes = df["close"].to_numpy(dtype=np.float64)
    if start >= len(df) - 1:
        return None, None

    # The reaction extreme: the high the raid reached (short) on this frame.
    seg = slice(start, len(df))
    pivot = (start + int(np.argmax(highs[seg]))) if bearish \
        else (start + int(np.argmin(lows[seg])))
    if pivot >= len(df) - 1:
        return None, None

    sh, sl = fractal_swings(df.iloc[:pivot + 1], swing_lookback)
    candidates = sl if bearish else sh
    if not candidates:
        return None, None
    level = candidates[-1][1]

    for j in range(pivot + 1, len(df)):
        broke = (closes[j] < level) if bearish else (closes[j] > level)
        if broke:
            t = pd.Timestamp(df["time"].iloc[j]) if "time" in df.columns else None
            return float(level), t
    return None, None
