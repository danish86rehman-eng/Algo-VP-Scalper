"""
Volume-profile location gate — where in the auction is this entry?

The rule, as specified by the operator
--------------------------------------
Build a volume profile on H4. Then, on the trigger frame:

    sell from VAH        price at the upper edge is above value; fade it down
    buy  from VAL        price at the lower edge is below value; fade it up
    no action at POC     price at fair value has no edge in either direction

This module is a **veto layer**, not a signal generator, exactly like
`ema_filter.py`. It never creates a trade. It removes trades whose location in
the auction contradicts the direction they want to take.

Why the POC rule is the one that matters
----------------------------------------
The POC is where the most volume traded, which is another way of saying it is
the price neither side could reject. An entry there is a coin-flip that pays
the spread for the privilege, and its stop must sit outside a band of heavy
two-way business, so the stop gets taken by noise rather than by being wrong.

Concretely, on 2026-08-25 16:45:25 UTC the agent bought XAUUSD at 4642.26 while
the H4 POC (42-bar profile) sat at 4641.33 — a distance of **0.93** on a value
area **248.43** wide. The stop was hit for **-$23.29**. That trade is what this
gate exists to refuse.

What the same measurement also showed, and why nothing here ships enabled
------------------------------------------------------------------------
The two winning shorts earlier that day, at 11:59 and 12:15, were **also** at
the POC — 4.61 and 2.55 away from it. A blanket POC veto would have removed
+$30.38 and +$59.42 along with the -$23.29, for a net of **-$74.79** across
those four trades. n=4 is not evidence of anything, in either direction, which
is the entire point: the rule is well-motivated and unmeasured, so it goes
through the 60/20/20 fold test in `docs/REMEDY_LEDGER.md` like every other
proposal. §13.5 does not have an exception for rules that sound obviously
correct — those are the ones it exists to catch.

Regime dependence
-----------------
A value area is a mean-reversion reference only while the market is rotating.
Across a trend leg the histogram goes bimodal and the single POC it reports
describes nothing: measured on the same instrument the same day, a 60-bar H4
profile placed the POC at 4394.62 while spot was 4642 — 247 points of pure
artefact, stranded in a base the market had already left. So the location rules
are applied only in the regime where they mean something, and the gate abstains
elsewhere rather than guessing. `VP_GATE_MODE` selects that behaviour.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from scalper.regime_classifier import (
    REGIME_RANGING, RegimeClassifier, RegimeRead,
)
from scalper.volume_profile import (
    LOC_ABOVE_VAH, LOC_AT_POC, LOC_AT_VAH, LOC_AT_VAL, LOC_BELOW_VAL,
    LOC_LOWER_VALUE, LOC_UNKNOWN, LOC_UPPER_VALUE, VolumeProfile,
    build_profile_auto, classify_location,
)

#: Locations from which a long may be taken: at or below the lower edge, or in
#: the lower half of value. "Buy from VAL" generalised to "buy in the discount
#: half", so a trigger that fires two bins above VAL is not discarded on a
#: technicality while one exactly at VAL is kept.
LONG_LOCATIONS = frozenset({LOC_AT_VAL, LOC_BELOW_VAL, LOC_LOWER_VALUE})
#: The mirror image for shorts.
SHORT_LOCATIONS = frozenset({LOC_AT_VAH, LOC_ABOVE_VAH, LOC_UPPER_VALUE})

#: Apply the location rules only when the regime classifier says RANGING;
#: abstain (allow) otherwise.
MODE_RANGING_ONLY = "RANGING_ONLY"
#: Apply the location rules in every regime.
MODE_ALWAYS = "ALWAYS"
#: Apply nothing but the POC dead zone, in every regime. The narrowest possible
#: expression of the operator's diagnosis, and the cheapest arm to measure.
MODE_POC_ONLY = "POC_ONLY"
#: The INVERSE of POC_ONLY: admit only entries inside the POC band, veto
#: everything else. Added because attribution of the 280-trade baseline book
#: found AT_POC to be the single best location bucket — 42 trades, PF 2.10,
#: +$1038 — i.e. the opposite of the rule this module was built to enforce.
#: Selected from the same window it would be measured on, so a fold result here
#: is contaminated by construction and can only ever disconfirm, never promote.
MODE_POC_REQUIRE = "POC_REQUIRE"

MODES = (MODE_RANGING_ONLY, MODE_ALWAYS, MODE_POC_ONLY, MODE_POC_REQUIRE)


@dataclass(frozen=True)
class VPGateResult:
    allow: bool
    location: str = LOC_UNKNOWN
    regime: str = ""
    poc: float = 0.0
    vah: float = 0.0
    val: float = 0.0
    poc_band: float = 0.0
    price: float = 0.0
    reason: str = ""
    #: True when the gate declined to form an opinion (no profile, wrong
    #: regime for the mode). Distinguished from an allow-on-merit so the
    #: reject log can tell "passed" from "not consulted".
    abstained: bool = False

    @property
    def at_poc(self) -> bool:
        return self.location == LOC_AT_POC


class VolumeProfileGate:
    """
    Pure gate: consumes closed frames, returns a decision.

    Fetching the H4 profile frame and the regime frame — and guaranteeing both
    end on a COMPLETED bar — belongs to the caller, so the live agent and the
    simulator feed it from their own data paths and run identical arithmetic.
    """

    def __init__(self,
                 mode: str,
                 profile_bars: int,
                 target_bins: int,
                 value_area_pct: float,
                 poc_band_frac: float,
                 edge_tolerance_frac: float,
                 regime_classifier: Optional[RegimeClassifier] = None):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
        self.mode = mode
        self.profile_bars = int(profile_bars)
        self.target_bins = int(target_bins)
        self.value_area_pct = float(value_area_pct)
        self.poc_band_frac = float(poc_band_frac)
        self.edge_tolerance_frac = float(edge_tolerance_frac)
        self.regime = regime_classifier or RegimeClassifier()

    def build_profile(self, df_h4_closed: pd.DataFrame) -> Optional[VolumeProfile]:
        """Profile over the last `profile_bars` COMPLETED H4 bars."""
        if df_h4_closed is None or len(df_h4_closed) < 2:
            return None
        window = df_h4_closed.tail(self.profile_bars)
        return build_profile_auto(window, target_bins=self.target_bins,
                                  va_pct=self.value_area_pct)

    def check(self,
              trigger_direction: str,
              price: float,
              df_h4_closed: pd.DataFrame,
              df_regime_closed: Optional[pd.DataFrame] = None) -> VPGateResult:
        """
        `df_regime_closed` may be None in POC_ONLY / ALWAYS mode, where the
        regime is not consulted. In RANGING_ONLY it is required, and its
        absence is an abstention rather than a veto — a gate that cannot see
        the regime has no business blocking a trade the rest of the stack
        approved.
        """
        if price <= 0:
            return VPGateResult(allow=True, abstained=True,
                                reason="Invalid reference price; gate abstains")

        profile = self.build_profile(df_h4_closed)
        if profile is None or not profile.valid:
            return VPGateResult(
                allow=True, abstained=True,
                reason="No usable H4 volume profile; gate abstains")

        regime_read: Optional[RegimeRead] = None
        if self.mode == MODE_RANGING_ONLY:
            if df_regime_closed is None:
                return VPGateResult(
                    allow=True, abstained=True, poc=profile.poc,
                    vah=profile.vah, val=profile.val, price=price,
                    reason="No regime frame supplied; gate abstains")
            regime_read = self.regime.classify(df_regime_closed)
            if regime_read.regime != REGIME_RANGING:
                return VPGateResult(
                    allow=True, abstained=True, regime=regime_read.regime,
                    poc=profile.poc, vah=profile.vah, val=profile.val,
                    price=price,
                    reason=(f"Regime is {regime_read.regime}, not "
                            f"{REGIME_RANGING} — value area is not a "
                            f"mean-reversion reference here; gate abstains"))

        width = profile.value_area_width
        poc_band = self.poc_band_frac * width
        edge_tol = self.edge_tolerance_frac * width
        location = classify_location(price, profile, edge_tol, poc_band)

        common = dict(
            location=location,
            regime=regime_read.regime if regime_read else "",
            poc=profile.poc, vah=profile.vah, val=profile.val,
            poc_band=poc_band, price=price,
        )

        if self.mode == MODE_POC_REQUIRE:
            if location == LOC_AT_POC:
                return VPGateResult(
                    allow=True, **common,
                    reason=f"AT_POC — admitted (POC_REQUIRE)")
            return VPGateResult(
                allow=False, **common,
                reason=(f"{location} — POC_REQUIRE admits only entries within "
                        f"+/-{poc_band:.2f} of POC {profile.poc:.2f}"))

        # The POC dead zone. Applies in every remaining mode — it is the rule
        # the gate was built for, and it is direction-independent.
        if location == LOC_AT_POC:
            return VPGateResult(
                allow=False, **common,
                reason=(f"AT_POC — price {price:.2f} is "
                        f"{abs(price - profile.poc):.2f} from POC "
                        f"{profile.poc:.2f} (dead zone +/-{poc_band:.2f}); "
                        f"no edge in either direction"))

        if self.mode == MODE_POC_ONLY:
            return VPGateResult(
                allow=True, **common,
                reason=f"Outside POC dead zone ({location})")

        if location == LOC_UNKNOWN:
            return VPGateResult(allow=True, abstained=True, **common,
                                reason="Location unknown; gate abstains")

        permitted = LONG_LOCATIONS if trigger_direction == "BULLISH" else SHORT_LOCATIONS
        if trigger_direction not in ("BULLISH", "BEARISH"):
            return VPGateResult(allow=True, abstained=True, **common,
                                reason=f"Unknown direction {trigger_direction!r}; "
                                       f"gate abstains")

        if location not in permitted:
            side = "buy" if trigger_direction == "BULLISH" else "sell"
            edge = "VAL" if trigger_direction == "BULLISH" else "VAH"
            return VPGateResult(
                allow=False, **common,
                reason=(f"{trigger_direction} vetoed at {location} — a {side} "
                        f"belongs at {edge}, not here "
                        f"(VAL={profile.val:.2f} POC={profile.poc:.2f} "
                        f"VAH={profile.vah:.2f}, price {price:.2f})"))

        return VPGateResult(
            allow=True, **common,
            reason=(f"OK — {trigger_direction} at {location} "
                    f"(VAL={profile.val:.2f} POC={profile.poc:.2f} "
                    f"VAH={profile.vah:.2f}, price {price:.2f})"))
