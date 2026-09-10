"""
`VP_LEG_CONFLUENCE` — two completed swing legs as a location filter (L-015).
===========================================================================

The operator's hypothesis, stated on 2026-08-28: *"If we use the VP anchor
points, we can find better entries. Quality over quantity."* Three confirmed H4
swing points P1 (low) -> P2 (high) -> P3 (low) define two legs; each leg gets
its own volume profile; a trigger that fires at a level shared by both legs is
standing where the market actually transacted twice.

Why this is not L-014 wearing a different hat
---------------------------------------------
`VP_LIQUIDITY_REACTION` made the profile a SIGNAL: it detected 1044-2608 times
per window, sat first in the priority order, and displaced `SWEEP_REJECTION`
almost entirely (277 taken -> 80 in W1). It was rejected with a consistent
negative sign in both disjoint windows.

This module cannot generate a trade. It only ever removes one. Direction, stop
and target stay with the underlying trigger. A veto cannot inflate the trade
count, so the "fewer but better" claim is directly falsifiable: if expectancy
per trade does not rise enough to pay for the trades given up, the hypothesis
is dead.

Why the POC is treated as evidence rather than a dead zone
----------------------------------------------------------
L-005 shipped "no trade at the POC" and was rejected. §13.11's attribution then
showed the rule was inverted — `AT_POC` was the book's BEST bucket (42 trades,
PF 2.10, +$1038) and `AT_VAL` its only negative one. MQL5 blog 772228 states
the correct orientation: a zone "that overlaps the session POC or a value area
edge is backed by real transacted volume", while one "inside a low volume node
is fragile" and gets traded through.

That is Grade C design evidence (§13.7) — the blog publishes no win rate, no
profit factor and no sample size, and says so itself. It fixes the DIRECTION of
the rule. It is not a reason to expect the gate to earn money, and §13.11's
standing warning applies: a bucket is not a forecast of the arm that isolates
it. Only a full re-run measures a gate.

Causality
---------
`StructureEngine._identify_swings` confirms a pivot at `i` only when `i` is the
extreme of `[i-n, i+n]`, so no pivot is reported until `n` bars have closed
after it. P3 is already `swing_lookback` bars old when the pair becomes usable,
and the caller hands in closed bars only. `tests/test_leg_confluence.py`
asserts this by appending future bars and requiring an identical verdict.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from core.structure_engine import StructureEngine, StructurePoint
from scalper.anchored_vp import atr
from scalper.volume_profile import (
    VolumeProfile,
    _accumulate,
    bin_size_for_range,
    build_profile_auto,
)

# -- Labels ------------------------------------------------------------------
#: Both legs put a level here. The operator's "anchor point" in its strongest
#: form: two independent auctions agreed this price mattered.
LOC_CONFLUENCE = "CONFLUENCE"
#: Exactly one leg puts a POC / VAH / VAL here.
LOC_AT_LEVEL = "AT_LEVEL"
#: Inside a low-volume node and at no level — the "fragile" case.
LOC_IN_LVN = "IN_LVN"
#: Neither. Ordinary price.
LOC_NO_LEVEL = "NO_LEVEL"
#: The legs could not be built. A filter that cannot see must not veto.
LOC_NO_OPINION = "NO_OPINION"

#: Decision modes, from strictest to loosest.
MODE_CONFLUENCE_ONLY = "CONFLUENCE_ONLY"
MODE_AT_LEVEL = "AT_LEVEL"
MODE_LVN_VETO = "LVN_VETO"
MODES = (MODE_CONFLUENCE_ONLY, MODE_AT_LEVEL, MODE_LVN_VETO)

LEG_UP = "LEG_UP"
LEG_DOWN = "LEG_DOWN"

#: `StructureEngine` labels. HH/LH mark swing HIGHS, HL/LL swing LOWS.
_HIGH_LABELS = ("HH", "LH")
_LOW_LABELS = ("HL", "LL")


def _is_high(point: StructurePoint) -> bool:
    return point.point_type in _HIGH_LABELS


@dataclass(frozen=True)
class LegProfile:
    """One completed pivot-to-pivot leg and its profile."""
    profile: VolumeProfile
    direction: str                 # LEG_UP | LEG_DOWN
    start_index: int
    end_index: int
    start_price: float
    end_price: float
    start_time: Optional[pd.Timestamp]
    end_time: Optional[pd.Timestamp]
    bars: int
    #: Bin centres classified as high / low volume nodes.
    hvn_prices: Tuple[float, ...] = ()
    lvn_prices: Tuple[float, ...] = ()

    @property
    def levels(self) -> Tuple[float, float, float]:
        return (self.profile.poc, self.profile.vah, self.profile.val)

    def summary(self) -> str:
        span = ""
        if self.start_time is not None and self.end_time is not None:
            span = f" {self.start_time:%m-%dT%H:%M}->{self.end_time:%m-%dT%H:%M}"
        return (f"{self.direction}{span} ({self.bars}b) "
                f"POC={self.profile.poc:.2f} VAH={self.profile.vah:.2f} "
                f"VAL={self.profile.val:.2f} "
                f"HVN={len(self.hvn_prices)} LVN={len(self.lvn_prices)}")


@dataclass(frozen=True)
class LegPair:
    """Two consecutive completed legs sharing the middle pivot."""
    leg_a: LegProfile
    leg_b: LegProfile
    atr_h4: float

    def summary(self) -> str:
        return f"A[{self.leg_a.summary()}] B[{self.leg_b.summary()}]"


@dataclass
class ConfluenceVerdict:
    """Observation-only record of one classification. §13.10: nothing here
    feeds back into gating; the gate reads `admit` and nothing else."""
    label: str = LOC_NO_OPINION
    admit: bool = True
    matched_levels: List[str] = field(default_factory=list)
    matched_prices: List[float] = field(default_factory=list)
    tolerance: float = 0.0
    #: Leg A's POC when price has not traded back through it during leg B.
    #: Telemetry for a future target study (L-003 found 91 trades exiting
    #: TP -> EARLY_CLOSE against a fixed 2R); it is NOT used as a target here.
    naked_poc: Optional[float] = None
    pair_summary: str = ""

    def reason(self) -> str:
        return f"LEGCONF/{self.label}"


def _node_prices(df: pd.DataFrame,
                 profile: VolumeProfile,
                 stddev_mult: float) -> Tuple[Tuple[float, ...],
                                              Tuple[float, ...]]:
    """High- and low-volume node bin centres.

    Definition from MQL5 CodeBase 76264: bins more than `stddev_mult` standard
    deviations above / below the mean bin volume. The histogram is rebuilt with
    `volume_profile._accumulate` using the profile's own geometry, so the node
    definition cannot drift from the POC that `build_volume_profile` derived
    from the identical accumulation.

    Empty bins are excluded from the mean. A profile spanning a wide range has
    many untouched bins at its edges, and counting those as "low volume" would
    label the whole periphery an LVN and make the veto fire on everything.
    """
    if profile is None or not profile.valid or profile.bin_count <= 0:
        return (), ()
    vol_col = "tick_volume" if "tick_volume" in df.columns else None
    vols = (df[vol_col].to_numpy(dtype=np.float64) if vol_col
            else np.ones(len(df), dtype=np.float64))
    hist = _accumulate(df["high"].to_numpy(dtype=np.float64),
                       df["low"].to_numpy(dtype=np.float64),
                       vols, profile.profile_low, profile.bin_size,
                       profile.bin_count)
    occupied = hist[hist > 0]
    if occupied.size < 3:
        return (), ()
    mean, sd = float(occupied.mean()), float(occupied.std())
    if sd <= 0:
        return (), ()
    centres = (profile.profile_low
               + (np.arange(profile.bin_count) + 0.5) * profile.bin_size)
    hvn = centres[hist > mean + stddev_mult * sd]
    # An empty bin is not a low-volume node; it is a bin the leg never visited.
    lvn = centres[(hist > 0) & (hist < mean - stddev_mult * sd)]
    return tuple(float(x) for x in hvn), tuple(float(x) for x in lvn)


def _ordered_pivots(points: Sequence[StructurePoint]) -> List[StructurePoint]:
    """Confirmed pivots oldest-first, de-duplicated by index.

    `StructureEngine` can emit a high and a low label for the same bar; keeping
    the first occurrence makes the walk deterministic, exactly as
    `anchored_vp._candidate_pivots` does.
    """
    seen = set()
    out: List[StructurePoint] = []
    for p in points or []:
        if p.index in seen:
            continue
        seen.add(p.index)
        out.append(p)
    return sorted(out, key=lambda p: p.index)


def _pick_three(points: Sequence[StructurePoint]
                ) -> Optional[Tuple[StructurePoint, StructurePoint,
                                    StructurePoint]]:
    """The newest three STRICTLY ALTERNATING pivots, oldest first.

    Alternation is the whole contract: a run of same-type pivots is one leg
    with noise in it, not two legs. Walking back from the newest finds the most
    recent genuine pair rather than the first one in the frame.
    """
    ordered = _ordered_pivots(points)
    for k in range(len(ordered) - 1, 1, -1):
        p3, p2, p1 = ordered[k], ordered[k - 1], ordered[k - 2]
        if _is_high(p1) == _is_high(p2) or _is_high(p2) == _is_high(p3):
            continue
        return p1, p2, p3
    return None


def _build_leg(frame: pd.DataFrame,
               start: StructurePoint,
               end: StructurePoint,
               min_leg_bars: int,
               min_leg_atr: float,
               atr_h4: float,
               target_bins: int,
               value_area_pct: float,
               node_stddev_mult: float) -> Optional[LegProfile]:
    """Profile one completed pivot-to-pivot leg, or None if it is not a leg.

    Returns None rather than a degraded profile: a short or shallow span is
    chop, and its "levels" are an artefact of the binning.
    """
    lo, hi = int(start.index), int(end.index)
    if lo < 0 or hi <= lo or hi > len(frame) - 1:
        return None
    # Inclusive of the terminal pivot bar: the leg ends AT the extreme, and the
    # volume transacted in that bar belongs to the auction that made it.
    leg = frame.iloc[lo:hi + 1]
    bars = len(leg)
    if bars < min_leg_bars:
        return None
    leg_range = float(leg["high"].max() - leg["low"].min())
    if atr_h4 > 0 and leg_range < min_leg_atr * atr_h4:
        return None
    profile = build_profile_auto(leg, target_bins, va_pct=value_area_pct)
    if profile is None or not profile.valid:
        return None
    hvn, lvn = _node_prices(leg, profile, node_stddev_mult)
    return LegProfile(
        profile=profile,
        direction=LEG_DOWN if _is_high(start) else LEG_UP,
        start_index=lo, end_index=hi,
        start_price=float(start.level), end_price=float(end.level),
        start_time=leg["time"].iloc[0] if "time" in leg.columns else None,
        end_time=leg["time"].iloc[-1] if "time" in leg.columns else None,
        bars=bars, hvn_prices=hvn, lvn_prices=lvn)


def build_leg_pair(df_h4_closed: pd.DataFrame,
                   symbol: str,
                   swing_lookback: int,
                   min_leg_bars: int,
                   min_leg_atr: float,
                   atr_period: int,
                   target_bins: int,
                   value_area_pct: float,
                   node_stddev_mult: float) -> Optional[LegPair]:
    """Two completed legs from the newest three alternating H4 pivots.

    `df_h4_closed` MUST contain only completed bars — the caller owns that,
    as it does for every other profile in this repository.
    """
    if df_h4_closed is None or len(df_h4_closed) < max(swing_lookback * 2 + 1,
                                                       atr_period + 1):
        return None
    if not {"high", "low"}.issubset(df_h4_closed.columns):
        return None

    state = StructureEngine(swing_lookback=swing_lookback).analyze(
        df_h4_closed, symbol, "H4")
    picked = _pick_three(state.structure_points)
    if picked is None:
        return None
    p1, p2, p3 = picked

    a_h4 = atr(df_h4_closed, atr_period)
    leg_a = _build_leg(df_h4_closed, p1, p2, min_leg_bars, min_leg_atr, a_h4,
                       target_bins, value_area_pct, node_stddev_mult)
    leg_b = _build_leg(df_h4_closed, p2, p3, min_leg_bars, min_leg_atr, a_h4,
                       target_bins, value_area_pct, node_stddev_mult)
    if leg_a is None or leg_b is None:
        return None
    return LegPair(leg_a=leg_a, leg_b=leg_b, atr_h4=a_h4)


def _near(price: float, level: float, tol: float) -> bool:
    return tol > 0 and abs(price - level) <= tol


def _leg_hits(price: float, leg: LegProfile, tol: float,
              tag: str) -> List[Tuple[str, float]]:
    hits = []
    for name, level in (("POC", leg.profile.poc),
                        ("VAH", leg.profile.vah),
                        ("VAL", leg.profile.val)):
        if _near(price, level, tol):
            hits.append((f"{tag}_{name}", float(level)))
    return hits


def classify(price: float,
             pair: Optional[LegPair],
             zone_atr: float,
             lvn_atr: float) -> ConfluenceVerdict:
    """Where does `price` sit against the two legs?

    ONE implementation, imported by both the live agent and the simulator
    (invariant #2 / §13.4). The consultation timeframe, the trigger window and
    the forming bar all drifted once because a comment asked editors to keep
    two copies in step; this is not restated anywhere.
    """
    v = ConfluenceVerdict()
    if pair is None or pair.atr_h4 <= 0:
        return v                      # NO_OPINION, admit=True

    v.pair_summary = pair.summary()
    tol = zone_atr * pair.atr_h4
    v.tolerance = tol

    hits_a = _leg_hits(price, pair.leg_a, tol, "A")
    hits_b = _leg_hits(price, pair.leg_b, tol, "B")
    hits = hits_a + hits_b
    v.matched_levels = [n for n, _ in hits]
    v.matched_prices = [p for _, p in hits]

    # Leg A's POC is "naked" when leg B never traded back through it. Recorded
    # for a later target study only — see ConfluenceVerdict.naked_poc.
    poc_a = pair.leg_a.profile.poc
    b_lo, b_hi = pair.leg_b.profile.profile_low, pair.leg_b.profile.profile_high
    if not (b_lo <= poc_a <= b_hi):
        v.naked_poc = float(poc_a)

    if hits_a and hits_b:
        v.label = LOC_CONFLUENCE
    elif hits:
        v.label = LOC_AT_LEVEL
    else:
        lvn_tol = lvn_atr * pair.atr_h4
        in_lvn = any(_near(price, x, lvn_tol)
                     for x in pair.leg_a.lvn_prices + pair.leg_b.lvn_prices)
        v.label = LOC_IN_LVN if in_lvn else LOC_NO_LEVEL
    return v


def admits(verdict: ConfluenceVerdict, mode: str) -> bool:
    """Does `mode` let this location through?

    NO_OPINION always admits. A filter that could not build its legs has not
    judged the trade, and silently vetoing on missing data would make the gate
    look selective while actually measuring warmup coverage.
    """
    label = verdict.label
    if label == LOC_NO_OPINION:
        return True
    if mode == MODE_CONFLUENCE_ONLY:
        return label == LOC_CONFLUENCE
    if mode == MODE_AT_LEVEL:
        return label in (LOC_CONFLUENCE, LOC_AT_LEVEL)
    if mode == MODE_LVN_VETO:
        return label != LOC_IN_LVN
    raise ValueError(f"unknown leg-confluence mode: {mode!r}")


def evaluate(price: float,
             pair: Optional[LegPair],
             mode: str,
             zone_atr: float,
             lvn_atr: float) -> ConfluenceVerdict:
    """Classify and decide in one call — what both decision paths invoke."""
    v = classify(price, pair, zone_atr, lvn_atr)
    v.admit = admits(v, mode)
    return v
