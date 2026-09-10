"""
Previous-day-range location gate — refuse to enter against the daily range.

The rule
--------
Read the previous day's high and low. Express the entry price as a fraction of
that range:

    loc = (price - PDL) / (PDH - PDL)

Then veto by mode:

    SHORT_DISCOUNT  ->  BEARISH vetoed at loc <= 0.25   (selling the low)
    LONG_PREMIUM    ->  BULLISH vetoed at loc >= 0.75   (buying the high)
    SYMMETRIC       ->  both

This is a veto layer like `ema_filter.py`, `vp_gate.py` and
`regime_direction_gate.py`: it never creates a trade, and it never moves a
stop or a target.

Why this module exists at all
-----------------------------
The decision path had no daily level in it. `core/liquidity_engine._prev_day_hl`
computes PDH/PDL and `sa_consultant.analyse()` runs that engine, but the result
object keeps only `price_zone`, `nearest_bsl` and `nearest_ssl`, and those two
pools feed the Gate 3 TP2 realignment — never admission, never direction. The
trigger's own liquidity pass (`trigger_engine.step1_liquidity`) reads **M5** and
derives `session_high/low` from **today's bars only**, so a daily level could
not reach a trigger even in principle. PDH/PDL was computed and discarded on
every scan. Ledger L-011.

Only applies INSIDE the previous day's range
--------------------------------------------
When price is beyond PDH or below PDL the gate abstains. That is a deliberate
restriction to where the evidence actually is, not an oversight. Attribution of
905 baseline trades across both L-008 windows:

    PD_PREMIUM  BULLISH   PF 0.60 recent / 0.86 prior    both negative
    PD_DISCOUNT BULLISH   PF 1.73 recent / 1.53 prior    both positive
    ABOVE_PDH   BULLISH   PF 0.83 recent / 1.67 prior    FLIPS
    BELOW_PDL   BULLISH   PF 0.57 recent / 1.12 prior    FLIPS

Outside the range is a breakout context, structurally different from a
rotation inside it, and its attribution has no stable sign. Extending the veto
there would import an unstable cell into a rule justified by a stable one.

Why three modes, and which one the evidence backs
-------------------------------------------------
`SHORT_DISCOUNT` is the operator's literal hypothesis and the bucket the live
loss of 2026-08-26 13:00 UTC falls in (SELL @ 4611.38 with an unswept PDL at
4605.33, loc = 0.066). It is stated first because it is the claim that prompted
the work, and it deserves its own measurement rather than being folded into a
rule that happens to be easier to defend.

`LONG_PREMIUM` is the only cell that held its sign in both disjoint windows —
and it is the opposite side from the trade that prompted the hypothesis. A
correct diagnosis does not imply a correct remedy (§13.11), and here the
diagnosis and the surviving remedy point at different halves of the book.

`SYMMETRIC` states the general rule ("do not enter against the daily range")
and is the version that can be wrong in a way that shows up.

Two things the attribution above does NOT establish, kept here so the next
reader does not have to re-derive them:

  - **A bucket is not a filter.** Vetoing a trade frees a position slot and
    skips a cooldown, so gating changes which trades come *later*. The 28-trade
    PD_PREMIUM/BULLISH cell and the book a `LONG_PREMIUM` arm actually produces
    are different trades. Only a full re-run measures this gate (§13.11).
  - **Two windows are two windows.** L-008 is still open.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

#: Veto BEARISH triggers in the bottom quartile of the previous day's range.
#: The operator's literal hypothesis.
MODE_SHORT_DISCOUNT = "SHORT_DISCOUNT"
#: Veto BULLISH triggers in the top quartile. The attribution-backed cell.
MODE_LONG_PREMIUM = "LONG_PREMIUM"
#: Both of the above.
MODE_SYMMETRIC = "SYMMETRIC"

MODES = (MODE_SYMMETRIC, MODE_LONG_PREMIUM, MODE_SHORT_DISCOUNT)


@dataclass(frozen=True)
class PDRResult:
    allow: bool
    reason: str = ""
    loc: Optional[float] = None
    pdh: Optional[float] = None
    pdl: Optional[float] = None
    abstained: bool = False

    def summary(self) -> str:
        if self.loc is None:
            return "PDR unreadable"
        return (f"loc={self.loc:.2f} of PDL={self.pdl:.2f}..PDH={self.pdh:.2f}")


class PDRGate:
    """
    Pure gate: consumes a frame of completed D1 bars, returns a decision.

    Fetching the frame — and guaranteeing its last row is a **completed** daily
    bar — belongs to the caller, exactly as in `RegimeDirectionGate`. Live reads
    `copy_rates_from_pos(symbol, TIMEFRAME_D1, 1, n)`, whose position 1 is the
    last closed daily bar; the simulator slices with `_closed_tf(..., 1440)`.
    Both therefore hand this gate *yesterday* as the final row, which is what
    makes PDH/PDL mean the same thing in both processes.
    """

    def __init__(self, mode: str,
                 discount_frac: float = 0.25,
                 premium_frac: float = 0.75):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
        if not 0.0 < discount_frac < premium_frac < 1.0:
            raise ValueError(
                f"need 0 < discount_frac < premium_frac < 1, got "
                f"{discount_frac} / {premium_frac}")
        self.mode = mode
        self.discount_frac = discount_frac
        self.premium_frac = premium_frac

    def check(self, trigger_direction: str, price: float,
              df_d1_closed: pd.DataFrame) -> PDRResult:
        if trigger_direction not in ("BULLISH", "BEARISH"):
            return PDRResult(
                allow=True, abstained=True,
                reason=f"Unknown direction {trigger_direction!r}; gate abstains")
        if df_d1_closed is None or len(df_d1_closed) == 0:
            return PDRResult(allow=True, abstained=True,
                             reason="No daily frame supplied; gate abstains")
        if not price or price <= 0:
            return PDRResult(allow=True, abstained=True,
                             reason="No price supplied; gate abstains")

        prev = df_d1_closed.iloc[-1]
        pdh = float(prev["high"])
        pdl = float(prev["low"])
        rng = pdh - pdl
        if rng <= 0:
            return PDRResult(allow=True, abstained=True, pdh=pdh, pdl=pdl,
                             reason="Previous day has no range; gate abstains")

        loc = (price - pdl) / rng

        # Outside the previous day's range is a breakout context whose
        # attribution flips sign between windows. Abstain rather than guess.
        if loc < 0.0 or loc > 1.0:
            where = "above PDH" if loc > 1.0 else "below PDL"
            return PDRResult(
                allow=True, abstained=True, loc=loc, pdh=pdh, pdl=pdl,
                reason=(f"price is {where} — outside the previous day's range; "
                        f"gate abstains"))

        veto_long = (self.mode in (MODE_LONG_PREMIUM, MODE_SYMMETRIC)
                     and trigger_direction == "BULLISH"
                     and loc >= self.premium_frac)
        veto_short = (self.mode in (MODE_SHORT_DISCOUNT, MODE_SYMMETRIC)
                      and trigger_direction == "BEARISH"
                      and loc <= self.discount_frac)

        if veto_long:
            return PDRResult(
                allow=False, loc=loc, pdh=pdh, pdl=pdl,
                reason=(f"BULLISH vetoed — buying the premium of the previous "
                        f"day's range (loc={loc:.2f} >= {self.premium_frac}, "
                        f"PDL={pdl:.2f} PDH={pdh:.2f})"))
        if veto_short:
            return PDRResult(
                allow=False, loc=loc, pdh=pdh, pdl=pdl,
                reason=(f"BEARISH vetoed — selling the discount of the previous "
                        f"day's range (loc={loc:.2f} <= {self.discount_frac}, "
                        f"PDL={pdl:.2f} PDH={pdh:.2f})"))

        return PDRResult(
            allow=True, loc=loc, pdh=pdh, pdl=pdl,
            reason=f"{trigger_direction} allowed at loc={loc:.2f}")
