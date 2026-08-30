# Design note — `VP_LEG_CONFLUENCE`

**Status:** design, pre-implementation. Written before code, per the workflow.
**Ledger:** L-015 (to be opened).
**Date:** 2026-08-28
**Operator hypothesis:** *"If we use the VP anchor points, we can find better
entries. Quality over quantity."*

---

## 1. What this is, and why it is not L-014 again

L-014 (`VP_LIQUIDITY_REACTION`) made the volume profile a **signal generator**.
It fired 1044–2608 times per window, sat at the head of the priority order, and
displaced `SWEEP_REJECTION` almost entirely (277 taken → 80 in W1). It was
rejected with a consistent negative sign across both disjoint windows.

This is the opposite construction. The profile is a **location filter** on
triggers that already exist. The entry is still a `SWEEP_REJECTION` or a
`BOS_RETEST`; the profile only decides whether that entry is standing somewhere
the market actually transacted.

| | L-005 `VP_GATE` | L-014 `VP_LIQUIDITY_REACTION` | **L-015 `VP_LEG_CONFLUENCE`** |
|---|---|---|---|
| Kind | veto layer | signal | **veto layer** |
| Anchor | rolling 42 H4 bars | one pivot → now, re-derived every bar | **two COMPLETED pivot→pivot legs** |
| Stability | moves every bar | moves every bar | **static once the leg closes** |
| POC rule | dead zone, no trade | bidirectional reaction zone | **evidence of transacted volume — admit** |
| Trade count | unchanged | +1044…+2608 detections | **strictly fewer than baseline** |

**The POC row is the crux, and there is direct evidence for it.** L-005 shipped
"no trade at the POC" and was rejected. §13.11's attribution then showed why:
`AT_POC` was the **best** location bucket in the book — 42 trades, PF 2.10,
+$1038, avg +$24.72 — while `AT_VAL` was the only negative one. The rule was
inverted. MQL5 blog 772228 states the correct orientation explicitly: a zone
"that overlaps the session POC or a value area edge is backed by real
transacted volume", whereas one "inside a low volume node is fragile".

So L-005's failure is *consistent with* this design, not evidence against it.
That is a claim about direction only — §13.11's own warning applies in full:
**a bucket is not a forecast of the arm that isolates it**, and only a full
re-run measures a gate. This has now bitten five times (L-005, L-011, L-014 ×2,
L-014's void campaign).

---

## 2. The anchor — three points, two legs

From the operator's chart: P1 swing low → P2 swing high → P3 swing low.

* **Leg A** = P1 → P2 (the up-leg). Profile A.
* **Leg B** = P2 → P3 (the down-leg). Profile B.

Both are **completed** legs, pivot to pivot. This is the material difference
from `anchored_vp.build_anchored_profile`, which spans pivot → *last closed
bar* and therefore re-derives on every new bar. A completed leg's POC/VAH/VAL
stop moving the moment P3 is confirmed, which is what makes them levels rather
than a running statistic.

**Causality.** `StructureEngine._identify_swings` confirms a pivot at index `i`
only when `i` is the extreme of `[i-n, i+n]`, so a pivot is never reported
until `n` bars have closed after it. P3 — the newest — is therefore already
`swing_lookback` bars old when the leg becomes available. Combined with a frame
that excludes the forming bar, no level here can be known before the live agent
could have known it. Asserted by a look-ahead test, not by argument.

**Acceptance.** The three points must strictly alternate (low-high-low or
high-low-high); a run of same-type pivots is not two legs. Each leg must clear
`MIN_LEG_BARS` and `MIN_LEG_ATR × ATR`, reusing the VPLR floors so a two-bar
wiggle cannot become a "leg". If either leg fails, the filter returns
NO_OPINION and **admits** the trade — a filter that cannot see must not veto.

---

## 3. Levels

Per leg, from the existing `build_profile_auto` (same binning, same value-area
expansion, no re-implementation):

* **POC**, **VAH**, **VAL** — as today.
* **HVN / LVN** — new. Per MQL5 CodeBase 76264: bins more than
  `NODE_STDDEV_MULT` (default **1.0**) standard deviations above / below the
  mean bin volume. Computed from the same histogram `build_volume_profile`
  already accumulates, so the node definition cannot drift from the POC.

**Naked POC** is recorded as telemetry only in this change. Leg A's POC is
"naked" if price has not traded back through it during leg B. It is the natural
structural target and speaks directly to the L-003 finding that 91 trades went
`TP → EARLY_CLOSE` for ≈ −90R against a fixed 2R target — but a target change is
an exit-side change, and those are only now becoming measurable. It gets its
own ledger row, not a smuggled inclusion here.

---

## 4. The decision

A candidate entry price is classified against the union of both legs' levels,
with tolerance `ZONE_ATR × ATR(H4)`:

| label | meaning |
|---|---|
| `CONFLUENCE` | within tolerance of a level from **both** legs |
| `AT_LEVEL` | within tolerance of exactly one leg's POC / VAH / VAL |
| `IN_LVN` | inside a low-volume node, and at no level |
| `NO_LEVEL` | none of the above |

Three modes, because the point is to **measure** which reading is correct
rather than assume one:

* `CONFLUENCE_ONLY` — admit `CONFLUENCE` only. Maximum quality, smallest sample.
* `AT_LEVEL` — admit `CONFLUENCE` or `AT_LEVEL`.
* `LVN_VETO` — admit everything except `IN_LVN`. Weakest filter, largest sample.

`LVN_VETO` is the mode the MQL5 blog actually argues for, and it is the one
whose sample stays closest to the baseline — which matters, because §13.9 says
a gate that changes `LOT_FLOOR` counts is measuring a different book.

**Quality over quantity is testable, not assumed.** Every mode strictly reduces
the trade count. If expectancy per trade does not rise enough to pay for the
trades given up, the hypothesis is falsified — and unlike a trigger, a veto
cannot inflate the count, so the comparison is cleaner than L-014's was.

---

## 5. What this does NOT change

Direction, stop and target come from the underlying trigger, untouched. No
change to `SWEEP_REJECTION`, `BOS_RETEST`, the session windows, the cooldown,
the state machine, or the Guardian. `SESSION_WINDOWS` is not modified. The
rolling 42-bar profile that feeds `vp_gate` / `va_fade_trigger` is untouched;
this is a separate object with its own params.

---

## 6. Files

**New:** `scalper/leg_confluence.py`, `tests/test_leg_confluence.py`
**Edited:** `scalper/decision_params.py`, `scalper_agent.py`,
`backtest_scalper.py`, `tests/test_live_sim_parity.py`
**Untouched:** `volume_profile.py` (read-only), `anchored_vp.py`,
`vp_liquidity_trigger.py`, `vp_gate.py`, `va_fade_trigger.py`

**Default:** `LEG_CONF_ENABLED = False`. Disabled mode must reproduce the
current baseline exactly, asserted by test.

---

## 7. Validation plan

1. Unit: three-point alternation, leg floors, HVN/LVN maths, each mode.
2. Causality: appending future bars cannot change a classification.
3. Parity: every constant identical in both processes; one `classify()`
   imported by the simulator, not re-implemented.
4. Baseline: filter off → identical trade list.
5. Campaign: three modes × two **disjoint** windows, against each window's own
   baseline. Report trades, WR, net $, PF, **sumR** (§13.15 — dollars alone hide
   compounding), `LOT_FLOOR` (§13.9), and the label split.

Promotion requires the **same sign across disjoint windows** (§13.12).
Within-window folds carry no cross-regime information. If the evidence is poor,
`LEG_CONF_ENABLED` stays `False` and it is reported as such.
