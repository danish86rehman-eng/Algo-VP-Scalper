"""
Shared decision parameters — the single source of truth for every value that
must be identical in the live agent and the simulator.

Why this module exists
----------------------
Invariant #2 of this repository says a backtest must run the live decision
path. It was being enforced by hand: `scalper_agent.py` declared a constant,
`backtest_scalper.py` declared its own copy, and a comment asked the next
editor to keep them in step. Four of them had already drifted:

  * the consultation timeframe (live M5 / sim M15) — the two processes were
    classifying regime on different frames, so Gate 1 was a different gate;
  * the trigger-frame window (live 150 bars / sim unbounded);
  * the confirmation-frame window (live 150 bars / sim 200);
  * the forming bar (live included it, sim excluded it).

A comment cannot enforce an invariant. An import can. Both processes now read
these values from here, and a divergence requires deliberately editing this
file rather than forgetting to edit a second one.

Nothing here depends on MT5 being connected; the timeframe values are plain
MT5 enum integers so the module stays importable in unit tests.
"""
from __future__ import annotations

from datetime import time as dtime
from typing import Dict, FrozenSet, List, Tuple

import MetaTrader5 as mt5

# ── Timeframe stack ──────────────────────────────────────────────────────────
#: Structure and entry pattern.
TF_TRIGGER = mt5.TIMEFRAME_M15
#: Imbalance fill and entry timing.
TF_CONFIRM = mt5.TIMEFRAME_M5
#: Directional context — the EMA band and the short-term-bias tiebreaker.
TF_HTF = mt5.TIMEFRAME_H1
#: Slower structural context for the short-term-bias filter.
TF_H4 = mt5.TIMEFRAME_H4

TRIGGER_TF_MINUTES = 15
CONFIRM_TF_MINUTES = 5

# ── Decision windows ─────────────────────────────────────────────────────────
# How many COMPLETED bars each engine sees. The simulator must slice to exactly
# these lengths: an engine handed 6000 bars of history does not produce the
# same structure map as one handed 150, so an unbounded slice is a silent
# strategy change.
TRIGGER_BARS = 150       # ~37h of M15 — swing plus session context
CONFIRM_BARS = 150       # ~12h of M5
HTF_BARS = 120           # ~5 days of H1
H4_BARS = 120            # ~20 days of H4
CONSULT_HTF_BARS = 100   # H1 window the liquidity/manipulation pass reads

# ── Trade lifetime ───────────────────────────────────────────────────────────
#: Hard timeout in TRIGGER-frame bars. 24 x M15 = 6h.
TIMEOUT_BARS = 24
#: The same budget expressed on the confirmation frame, which is what the
#: simulator's exit scan walks. Derived, never typed twice.
TIMEOUT_CONFIRM_BARS = TIMEOUT_BARS * TRIGGER_TF_MINUTES // CONFIRM_TF_MINUTES
#: Forced flat, and the daily-reset boundary.
EOD_HOUR_UTC = 23

# ── Gates ────────────────────────────────────────────────────────────────────
#: Per-trigger regime whitelist. Fading liquidity grabs is the MANIPULATION
#: play, so SWEEP_REJECTION/JUDAS are whitelisted there rather than blocked.
TRIGGER_REGIME_WHITELIST: Dict[str, FrozenSet[str]] = {
    "SESSION_SWEEP": frozenset({"MANIPULATION", "ROTATION"}),
    "HTF_CRT_SWEEP": frozenset({"MANIPULATION", "ROTATION", "EXPANSION"}),
    "SWEEP_REJECTION": frozenset({"MANIPULATION", "ROTATION"}),
    "JUDAS":           frozenset({"MANIPULATION", "ROTATION"}),
    "BOS_RETEST":      frozenset({"EXPANSION"}),
    "FVG_FILL":        frozenset({"EXPANSION", "MANIPULATION"}),
    # Fading a value-area edge is a rotation play. EXPANSION is excluded on
    # purpose: a market leaving its value area in a trend leg is the case
    # where the profile is bimodal and its edges mean nothing (SS12).
    "VALUE_AREA_FADE": frozenset({"ROTATION", "MANIPULATION"}),
    # VP_LIQUIDITY_REACTION is registered at the bottom of this file, where
    # VPLR_REGIMES is defined — Gate 1 defensively denies any trigger with no
    # whitelist entry, so the registration is load-bearing, not decorative.
}

#: UTC hour ranges (end-exclusive) that demand HIGH short-term-bias confidence.
THIN_LIQUIDITY_HOURS_UTC: List[Tuple[int, int]] = [
    (19, 22),   # NY close -> Asia activation
    (16, 17),   # London close / NY lunch overlap
]
THIN_LIQ_REQUIRED_CONFIDENCE = "HIGH"

#: Concurrent SA positions. Enforced by SACRG and CapitalPool live, and by the
#: simulator's open-trade map.
MAX_OPEN_POSITIONS = 2

CONSULT_MAX_LATENCY_MS = 200.0


def entry_confidence_allowed(trigger_confidence, stb_confidence):
    """Pre-2026-09-07 policy: STB admission is the confidence decision."""
    return True

# ── News blackout ────────────────────────────────────────────────────────────
NEWS_BLACKOUT_BEFORE_MIN = 30
NEWS_BLACKOUT_AFTER_MIN = 15
NEWS_IMPACT_LEVELS = ("HIGH",)

# ── H1 EMA band (18 EMA High / 18 EMA Low) ───────────────────────────────────
#: Period for both bands. Applied to the high series and the low series
#: separately, producing a channel rather than a single line.
EMA_BAND_PERIOD = 18
#: The band is read on H1 regardless of the trigger frame.
EMA_BAND_TF = TF_HTF
#: Completed H1 bars fetched to seed the EMA. MT5 seeds an EMA from an SMA of
#: the first `period` values, so the series needs a run-up before it converges;
#: 200 bars is ~11x the period.
EMA_BAND_BARS = 200
#: Directional mapping. TREND is the specified rule (above the band -> longs).
#: FADE is its inversion, kept selectable so the counter-trend premise of the
#: SWEEP_REJECTION trigger can be measured rather than assumed.
EMA_BAND_MODE = "TREND"

#: Master switch. Both processes default to the same value.
#:
#: Default OFF, and the reason is measured rather than assumed. Walk-forward on
#: XAUUSD 2026-05-15 -> 08-23, 60/20/20, band on vs off with every other gate
#: identical:
#:
#:   risk 3%   off: +2742, PF 1.35, positive in all three folds  (passes §13.5)
#:             TREND: +230, PF 1.06, F3 negative                 (sign flips)
#:             FADE:  -339, PF 0.87, negative in all three folds
#:   risk 2%   off: +1617, PF 1.36, positive in all three folds  (passes §13.5)
#:             TREND: +281, PF 1.11, F2 and F3 negative          (sign flips)
#:
#: The band removes 197 of 280 trades whose profit factor was 1.52 and keeps
#: 83 whose profit factor was 0.94 — it strips out the better half. The cause
#: is structural, not a matter of polarity: 276 of 280 trades come from
#: SWEEP_REJECTION, which fades a liquidity grab, and a trend band vetoes a
#: fade by construction. Inverting the mapping (FADE) does not rescue it.
#:
#: Kept implemented and switchable — `--ema-band` on either process, or flip
#: this — so the rule can be re-measured against a continuation-heavy trigger
#: mix, which is the configuration where it would be expected to help.
#: docs/RESEARCH_NOTES.md §11 carries the full table.
EMA_BAND_ENABLED = False


# ── Short-term-bias relaxation for continuation triggers ─────────────────────
#: Triggers whose premise is *following* a move rather than fading one.
#: The short-term-bias gate was written for the sweep-fade case, and two of its
#: five rules carry justifications that only hold for a fade:
#:
#:   "don't chase"            — blocks a trigger pointing the same way as a
#:                              fresh session sweep. For a continuation setup
#:                              that sweep is the displacement it is trading,
#:                              so the rule inverts the trigger's premise.
#:   "neutral needs direction" — blocks a continuation trigger whenever the
#:                              intraday read is NEUTRAL, while allowing fade
#:                              types through at LOW confidence.
#:
#: The third block — trigger direction *opposes* a clear short-term read — is
#: direction-generic and stays enforced for every trigger: a continuation setup
#: fighting the immediate move contradicts itself.
STB_CONTINUATION_TRIGGERS = frozenset({"BOS_RETEST", "FVG_FILL"})

#: Master switch for the relaxation above.
STB_RELAX_CONTINUATION = False

# ── Short-term-bias trigger classes ──────────────────────────────────────────
# These three sets were hard-coded tuples inside `short_term_bias.py`, repeated
# at three decision points. They are named here because membership IS the gate:
# a trigger absent from the relevant set is blocked, and L-009 is the record of
# what that costs — VALUE_AREA_FADE took zero trades in thirteen months because
# rule 3 admits only SWEEP_REJECTION, and a fade opposes short-term structure by
# construction. That is a definition, not evidence about the setup.
#
# Membership of every PRE-EXISTING trigger is unchanged, so the baseline is
# bit-identical. VP_LIQUIDITY_REACTION is added to the two fade-shaped sets
# because it is a reversal trigger: after a BSL raid, intraday structure reads
# bullish at exactly the moment the short is valid.

#: Layer 0 — the range whipsaw guard applies to these fade types only.
#: VP_LIQUIDITY_REACTION is deliberately NOT here: its own contract already
#: requires a raid beyond a liquidity level, which is a stronger statement about
#: location than "is price at the range extreme".
STB_RANGE_GUARD_TRIGGERS = frozenset({"SWEEP_REJECTION", "JUDAS", "FVG_FILL"})

#: Layer 3, NEUTRAL branch — triggers allowed through with no clear short-term
#: read. A setup that does not need a directional bias to be valid.
STB_NEUTRAL_OK_TRIGGERS = frozenset({"SWEEP_REJECTION", "JUDAS", "FVG_FILL",
                                     "VP_LIQUIDITY_REACTION", "HTF_CRT_SWEEP", "SESSION_SWEEP"})

#: Layer 3, OPPOSING branch — triggers that are MEANT to trade against the
#: prevailing short-term read. This is the set L-009 named as the blocker.
STB_COUNTER_TREND_TRIGGERS = frozenset({"SWEEP_REJECTION",
                                        "VP_LIQUIDITY_REACTION", "HTF_CRT_SWEEP", "SESSION_SWEEP"})


# ── H4 volume profile / value-area location gate ─────────────────────────────
# Sell from VAH, buy from VAL, nothing at the POC. See scalper/vp_gate.py for
# the rule and scalper/volume_profile.py for the arithmetic.

#: Frame the profile is built on. H4 per the operator's specification: it is
#: slow enough that the value area survives a session, fast enough to track a
#: balance that formed this week.
VP_PROFILE_TF = TF_H4
#: COMPLETED H4 bars fetched. The profile itself uses the last
#: VP_PROFILE_BARS of these; the surplus exists so the frame can also seed the
#: regime classifier without a second fetch.
VP_PROFILE_FETCH_BARS = 260
#: Bars entering the histogram. 42 x H4 = 7 days.
#:
#: Measured on XAUUSD 2026-08-25: at 30 and 42 bars the POC tracked spot
#: (4641.33 against a 4642 market); at 60 bars it fell to 4394.62 — 247 points
#: adrift, because that window straddled the rally and the histogram went
#: bimodal. A profile is a statement about one balance area, so the window has
#: to be short enough to contain one.
VP_PROFILE_BARS = 42
#: Histogram resolution in rows. Art. 23169 fixes a 10-point bin, which does
#: not travel across instruments; a row count holds resolution constant where
#: it matters. 60 rows over a 370-point range is ~6 points per row on gold.
VP_TARGET_BINS = 60
#: Fraction of volume enclosed by the value area. The universal convention and
#: art. 23169's own default.
VP_VALUE_AREA_PCT = 0.70

#: Half-width of the POC dead zone, as a fraction of value-area width. At 0.10
#: on a 248-wide area the zone is +/-24.8, i.e. 20% of the value area is
#: no-trade. The 2026-08-25 16:45 loss entered 0.93 from the POC and is caught
#: by any band wider than ~0.004; the two winning shorts that day sat 2.55 and
#: 4.61 away and are caught by anything wider than ~0.019. There is no band
#: that keeps those winners and rejects that loser, so this number decides how
#: much of the book the rule removes and must be swept, not guessed.
VP_POC_BAND_FRAC = 0.10
#: How near VAH/VAL counts as "at" the edge, as a fraction of value-area width.
VP_EDGE_TOLERANCE_FRAC = 0.10

#: RANGING_ONLY | ALWAYS | POC_ONLY — see scalper/vp_gate.py.
VP_GATE_MODE = "RANGING_ONLY"

#: Frame the regime classifier reads when the gate runs in RANGING_ONLY.
#: H1: slow enough not to flip on a single M15 impulse, fast enough that a
#: balance forming this session is visible before the session ends.
VP_REGIME_TF = TF_HTF
VP_REGIME_BARS = 400

#: Regime classifier thresholds. Autocorrelation and volatility defaults are
#: art. 17737's published values (trend 0.2, volatility 1.5, lookback 100,
#: smoothing 10). The efficiency-ratio threshold has no source — Kaufman's
#: ratio is standard but the cut is ours, and it is the parameter most likely
#: to need moving.
VP_REGIME_LOOKBACK = 100
VP_REGIME_SMOOTHING = 10
VP_REGIME_TREND_THRESHOLD = 0.2
VP_REGIME_VOL_THRESHOLD = 1.5
VP_REGIME_ER_THRESHOLD = 0.35

#: Master switch. Default OFF, and it stays off until it clears §13.5 —
#: chronological 60/20/20 with the same sign in all three folds. On the only
#: four trades measured so far the POC veto is net -$74.79, because it removes
#: two winners along with the loss it was designed to prevent. That is not a
#: verdict; four trades cannot produce one. It is the reason the switch is off
#: while the folds are run.
VP_GATE_ENABLED = False

#: Regime backend: DETERMINISTIC | HMM. The HMM path needs `hmmlearn`, which is
#: not in requirements.txt, and costs ~142 ms against ~0.9 ms for the
#: deterministic classifier — measured on 400 H1 bars of XAUUSD, where the two
#: agreed on the answer. See scalper/hmm_backend.py for why a fitted model is
#: the harder thing to keep parity-safe.
VP_REGIME_BACKEND = "DETERMINISTIC"
VP_REGIME_BACKENDS = ("DETERMINISTIC", "HMM")


# -- Regime-direction gate (do not fade a classified trend) -------------------
# Ledger L-006. See scalper/regime_direction_gate.py.

#: SYMMETRIC | COUNTER_TREND_LONGS. SYMMETRIC states the general rule;
#: COUNTER_TREND_LONGS reproduces the literal in-sample observation (longs into
#: TRENDING_DOWN) and exists only so the two can be compared.
RD_GATE_MODE = "SYMMETRIC"

#: Frame and window the regime is read on. Same values the VP gate uses, so a
#: run cannot end up classifying regime two different ways in one decision.
RD_REGIME_TF = VP_REGIME_TF
RD_REGIME_BARS = VP_REGIME_BARS

#: Master switch. Default OFF.
#:
#: The hypothesis behind it was read off 8 trades in a single window, which is
#: below anything §13.5 can act on, and a trend filter has already been
#: rejected once on this book (§13.8, the H1 EMA band) for a structural reason
#: that applies here too: ~99% of trades come from SWEEP_REJECTION, which fades
#: by construction. This stays off until it is measured somewhere the
#: hypothesis has never been.
RD_GATE_ENABLED = False


# -- VALUE_AREA_FADE entry model ---------------------------------------------
# The operator's specified rule as a SIGNAL, not a filter: sell a rejection at
# VAH, buy a rejection at VAL, nothing at the POC. See
# scalper/va_fade_trigger.py. The veto-shaped version of this rule was measured
# first and rejected (RESEARCH_NOTES SS12); that result constrains filters, not
# signal generators, so this is a separate claim needing its own measurement.

#: RSI confluence. Art. 17781 pairs RSI(14) 30/70 with a ranging regime.
VA_FADE_RSI_PERIOD = 14
VA_FADE_RSI_OVERBOUGHT = 70.0
VA_FADE_RSI_OVERSOLD = 30.0
#: Minimum rejecting-wick share of the bar range.
VA_FADE_MIN_WICK_FRAC = 0.33
#: Stop buffer beyond the range marker, as a fraction of value-area width.
VA_FADE_SL_BUFFER_FRAC = 0.05

#: Master switch. Default OFF pending the fold test.
VA_FADE_ENABLED = False


# -- Previous-day-range location gate ----------------------------------------
# The decision path had no daily level in it at all: `_prev_day_hl` computes
# PDH/PDL, `sa_consultant.analyse()` runs the engine that owns it, and the
# result object drops both before any gate sees them. See scalper/pdr_gate.py
# and ledger L-011.

#: Daily frame the gate reads. Its last CLOSED bar is the previous day.
PDR_TF = mt5.TIMEFRAME_D1
#: Daily bars fetched. Only the last closed one is used; the rest are lead-in
#: so a short history is visibly short rather than silently wrong.
PDR_BARS = 10

#: SHORT_DISCOUNT | LONG_PREMIUM | SYMMETRIC.
#: SHORT_DISCOUNT is the operator's literal hypothesis (and the bucket the
#: 2026-08-26 13:00 UTC live loss falls in). LONG_PREMIUM is the only cell that
#: held its sign across both L-008 windows. SYMMETRIC states the general rule.
PDR_GATE_MODE = "SYMMETRIC"

#: Range fractions bounding "discount" and "premium".
PDR_DISCOUNT_FRAC = 0.25
PDR_PREMIUM_FRAC = 0.75

#: Master switch. Default OFF.
#:
#: The stable attribution cell behind this (PD_PREMIUM/BULLISH, PF 0.60 recent
#: and 0.86 prior) is an attribution bucket, not a filter forecast — vetoing a
#: trade frees a position slot and skips a cooldown, so the gated book is made
#: of different trades than the bucket (§13.11). Stays off until a full re-run
#: clears §13.5 in both disjoint windows.
PDR_GATE_ENABLED = False


# -- VP_LIQUIDITY_REACTION trigger -------------------------------------------
# A liquidity reaction AT an anchored H4 volume-profile level. Ledger L-014, and
# a different claim from both prior VP experiments: L-005 tested VP as a veto,
# L-009 tested the value-area edge as a fade signal. This treats the profile as
# a LOCATION and takes its direction from the liquidity raid and the structure
# break. See scalper/vp_liquidity_trigger.py and docs/DESIGN_VP_LIQUIDITY_REACTION.md.

#: Frame the anchored profile is built on, and how many COMPLETED bars are
#: fetched to find the leg. Separate from VP_PROFILE_* so the rolling 42-bar
#: profile feeding vp_gate / va_fade_trigger is untouched.
VPLR_PROFILE_TF = TF_H4
VPLR_PROFILE_FETCH_BARS = 180

#: Right-side confirmation for the H4 pivot that anchors the leg. A pivot is not
#: reported until this many bars have CLOSED after it, which is what makes the
#: anchor causal — and what costs the trigger this much latency in exchange.
VPLR_H4_SWING_LOOKBACK = 3

#: A leg must be big enough to have a meaningful volume distribution. Below
#: these it is a wiggle, and its "POC" is one noisy bar.
VPLR_MIN_LEG_BARS = 6
VPLR_MIN_LEG_ATR = 1.5

#: Histogram resolution and value-area fraction for the anchored profile. Same
#: conventions as the rolling profile; a leg covers less range, so fewer rows
#: hold the same resolution per row.
VPLR_TARGET_BINS = 40
VPLR_VALUE_AREA_PCT = 0.70

#: ATR period used for every normalisation in this trigger, on the trigger frame.
VPLR_ATR_PERIOD = 14

#: How close a liquidity pool must sit to POC/VAH/VAL to count as "at" it. The
#: larger of an ATR multiple and a value-area fraction, so the zone stays
#: sensible both when the leg is tight and when volatility expands.
VPLR_ZONE_ATR = 0.5
VPLR_ZONE_VA_FRAC = 0.10

#: Trigger-frame bars searched for the raid. 32 x M15 = 8h, enough to contain
#: the developing H4 candle and the one before it.
VPLR_RAID_LOOKBACK = 32

#: How far past the pool price counts as a raid rather than a touch. Below this
#: the "sweep" is inside the spread and the noise.
VPLR_MIN_SWEEP_ATR = 0.15

#: How recent the raid must be, in trigger-frame bars. 8 x M15 = 2h.
#:
#: Without this the detector treats any pierce inside the whole
#: VPLR_RAID_LOOKBACK window as current, so a level breached eight hours ago
#: still reads as "price just raided it" — and because depth is measured to the
#: running extreme, the staler the event the DEEPER it scores. Traced on
#: XAUUSD 2026-08-27: a swing low breached hours earlier scored 4.43xATR and
#: won every bar of the 02:00-06:00 reversal. A sweep is an event, and an event
#: has to be recent to be the reason for an entry.
VPLR_RAID_MAX_AGE_BARS = 8

#: Body size that counts as displacement away from the raided level.
VPLR_DISPLACEMENT_ATR = 0.8

#: Rejecting-wick share of the raid bar's range, for the wick confluence.
VPLR_MIN_WICK_FRAC = 0.33

#: Confirmation-frame window searched for the MSS, and the fractal lookback used
#: to locate the swing that must break. 120 x M5 = 10h.
VPLR_MSS_LOOKBACK = 120
VPLR_MSS_SWING_LOOKBACK = 2

#: Fractal lookback for trigger-frame swing pools.
VPLR_SWING_LOOKBACK = 3

#: Minimum additional confluences beyond the mandatory raid + reclaim/displace +
#: MSS chain. The brief specifies "at least one".
VPLR_MIN_CONFLUENCE = 1

#: Stop buffer beyond the raid extreme, in ATR.
VPLR_SL_BUFFER_ATR = 0.25

#: Trigger-scoped session allowance. SESSION_WINDOWS is NOT modified — existing
#: triggers keep their exact windows. This opens Asia for THIS trigger only.
#:
#: Measured cause, from logs/scalper_agent.log on 2026-08-27: the agent went
#: `ACTIVE -> IDLE | Outside SA session window` at 02:00:12Z and stayed IDLE
#: until 06:30:18Z. TOKYO_OPEN ends at 02:00 and PRE_LONDON starts at 06:30, so
#: that 4.5h gap is structural. `_scan_symbol` is never called there, which is
#: why the fix has to live in the state machine rather than in a gate.
VPLR_SESSION_OVERRIDE_ENABLED = True
VPLR_SESSION_WINDOW_UTC = (dtime(0, 0), dtime(6, 30))

#: Where the trigger is allowed to compete.
#:
#:   ASIA_ONLY    evaluate it only inside the VP allowance, i.e. only in hours
#:                no other trigger is awake for.
#:   ALL_SESSIONS evaluate it on every scanned bar, at the head of the priority
#:                order.
#:
#: Default ASIA_ONLY, and the reason is measured. On XAUUSD at 3% risk,
#: ALL_SESSIONS detects the setup ~1100-1750 times per window and displaces
#: SWEEP_REJECTION almost entirely (277 taken trades -> 23 on 2026-05-15..08-23),
#: turning +$2758 into +$421. The trigger is not better than what it pre-empts
#: during hours the book already trades. In the hours the book does NOT trade,
#: it has nothing to displace — and that block is positive in both disjoint
#: windows tested (+$336 PF 2.80 and +$675 PF 1.45).
#:
#: This is the cannibalisation mechanism L-010 measured for `Whole_day`, seen
#: from the other side: one position per symbol means an extra entry does not
#: add to the book, it replaces a later one.
VPLR_SCOPE = "ASIA_ONLY"
VPLR_SCOPES = ("ASIA_ONLY", "ALL_SESSIONS")

#: Master switch. Default OFF until the campaign in
#: docs/DESIGN_VP_LIQUIDITY_REACTION.md §9 clears disjoint-window folds.
#: With this False the trigger is not constructed, not evaluated, and the
#: VP_ONLY state is unreachable — asserted by test, not by inspection.
VPLR_ENABLED = False

#: Regimes this trigger may fire in. A raid-and-reverse is the MANIPULATION play
#: and lives naturally in ROTATION; EXPANSION is included because the raid that
#: ends an impulse leg happens inside one, and the MSS clause — not the regime —
#: is what distinguishes a failed raid from a genuine breakout. TRANSITION is
#: excluded (no clear regime) and STRESS is denied for every trigger upstream.
VPLR_REGIMES = frozenset({"MANIPULATION", "ROTATION", "EXPANSION"})

#: Register with Gate 1. Done here rather than inline above because the value
#: lives with the rest of the trigger's parameters; done at all because Gate 1
#: blocks any trigger type absent from the map.
TRIGGER_REGIME_WHITELIST["VP_LIQUIDITY_REACTION"] = VPLR_REGIMES


# ── Trade Guardian exit management ────────────────────────────────────────────
# L-003: the simulator modelled no trailing while the live Guardian trails,
# closes early and extends targets, so every exit-side result was measuring a
# strategy nobody runs. These are the Guardian's thresholds, hoisted here so
# `trade_guardian_agent.TGAConfig` and `scalper/exit_manager.py` read ONE set
# of numbers. A drift between them would silently re-open the gap this change
# closes; `tests/test_live_sim_parity.py` asserts identity.

#: Stage ladder, in R-multiples of the ORIGINAL entry->SL distance, keyed off
#: peak R rather than current R. Raised from {0.5/1.0/2.0} after three trades
#: stopped at breakeven on routine 0.5R retraces — healthy ICT continuations
#: frequently dip 50-70% of the initial leg.
TGA_BREAKEVEN_TRIGGER_R = 1.0
TGA_STAGE2_TRIGGER_R = 1.5
TGA_STAGE3_TRIGGER_R = 2.5

#: Stage 1 will not fire on a wick: this many CLOSED confirmation-frame candles
#: must have held the trade side of entry first.
TGA_BREAKEVEN_MIN_CLOSE_CANDLES = 2

#: Trail distances from current price, in units of the entry ATR.
TGA_STAGE2_TRAIL_ATR = 1.0
TGA_STAGE3_TRAIL_ATR = 0.6

#: A swing within this many ATR of the computed trail pulls the stop to just
#: beyond that swing instead.
TGA_STRUCTURE_LOCK_ATR = 0.5

#: Early close arms only once the trade has been this far in profit.
TGA_EARLY_CLOSE_MIN_PROFIT_R = 1.0

#: TP extension: fraction banked at TP1 before the remainder is allowed to run.
TGA_TP_EXTENSION_PARTIAL_PCT = 0.50
TGA_MOMENTUM_CANDLES_CHECK = 3
TGA_ENTRY_BREATHING_CANDLES = 2

#: No-progress kill. A trade open this long whose peak never cleared this R is
#: dead; close it at market rather than let it ride into the original stop.
TGA_NO_PROGRESS_MINUTES = 60
TGA_NO_PROGRESS_MAX_PEAK_R = 0.3

#: Master switch for replaying the Guardian inside the simulator.
#: Default OFF so every stored baseline stays byte-comparable; the flag is
#: `--tga-exits`. See docs/RESEARCH_NOTES.md §19 for the measured gap.
TGA_EXITS_IN_SIM = False


# ── VP_LEG_CONFLUENCE (L-015) ─────────────────────────────────────────────────
# Two COMPLETED H4 swing legs (P1->P2, P2->P3) as a location filter on the
# triggers that already exist. Not a signal: it can only remove a trade, never
# add one — which is what makes "quality over quantity" falsifiable rather than
# assumed. Design: docs/DESIGN_VP_LEG_CONFLUENCE.md.

#: Pivot confirmation depth on H4. Shared with VPLR deliberately: a pivot is
#: either confirmed at this repository's standard or it is not.
LEG_CONF_SWING_LOOKBACK = VPLR_H4_SWING_LOOKBACK

#: Leg size floors. A short or shallow span is chop and its "levels" are an
#: artefact of the binning, so the pair is refused rather than degraded.
LEG_CONF_MIN_LEG_BARS = VPLR_MIN_LEG_BARS
LEG_CONF_MIN_LEG_ATR = VPLR_MIN_LEG_ATR

#: Profile geometry, matching the rest of the repo's VP work.
LEG_CONF_ATR_PERIOD = VPLR_ATR_PERIOD
LEG_CONF_TARGET_BINS = VPLR_TARGET_BINS
LEG_CONF_VALUE_AREA_PCT = VPLR_VALUE_AREA_PCT

#: How close to a POC / VAH / VAL counts as "at" it, in H4 ATR.
LEG_CONF_ZONE_ATR = 0.25

#: How close to a low-volume-node bin centre counts as inside it.
LEG_CONF_LVN_ATR = 0.15

#: HVN / LVN threshold in standard deviations of mean OCCUPIED bin volume.
#: 1.0 is the default in MQL5 CodeBase 76264, which is where this definition
#: comes from. Empty bins are excluded from the mean — see `_node_prices`.
LEG_CONF_NODE_STDDEV_MULT = 1.0

#: CONFLUENCE_ONLY | AT_LEVEL | LVN_VETO — see scalper/leg_confluence.py.
#: LVN_VETO is the mode MQL5 blog 772228 actually argues for and the one whose
#: sample stays closest to the baseline, which matters under §13.9.
LEG_CONF_MODE = "LVN_VETO"

#: Master switch. Default OFF; disabled mode must reproduce the baseline trade
#: list exactly, and that is asserted by test rather than by inspection.
LEG_CONF_ENABLED = False


# ── Authoritative VP + structural market-location permission -----------------
# There are deliberately only two modes.  ACTIVE is binding only for the
# trigger families listed below; OFF reproduces their legacy behaviour.
MARKET_LOCATION_MODES = ("OFF", "ACTIVE")
MARKET_LOCATION_MODE = "ACTIVE"
MARKET_LOCATION_ACTIVE_TRIGGERS = frozenset({"SWEEP_REJECTION"})
# Compatibility for older operational code; decision paths use MODE directly.
MARKET_LOCATION_ENABLED = MARKET_LOCATION_MODE == "ACTIVE"
MARKET_LOCATION_D1_BARS = 260
MARKET_LOCATION_W1_BARS = 260
MARKET_LOCATION_H4_BARS = 260
MARKET_LOCATION_M15_BARS = 180
# Detailed tick-volume reconstruction windows.  ACTIVE W1 normally fits in
# the M15 window; H1 is the long-history fallback.  ACTIVE/REFERENCE H4 uses
# M5 when complete and M15 otherwise.
MARKET_LOCATION_VP_ROWS = 48
MARKET_LOCATION_M15_DETAIL_BARS = 12_000
MARKET_LOCATION_H1_DETAIL_BARS = 12_000
MARKET_LOCATION_M5_DETAIL_BARS = 12_000
MARKET_LOCATION_HISTORY_PATH = "logs/market_location_profiles.jsonl"
MARKET_LOCATION_STATE_PATH = "logs/market_location_state.json"

# Directional location permission.  All distances are ATR-normalised so the
# same contract scales with XAUUSD volatility; live and replay import the same
# values.
MARKET_LOCATION_PROXIMITY_ATR = 0.35
MARKET_LOCATION_ACCEPTANCE_BUFFER_ATR = 0.10
MARKET_LOCATION_M5_DISPLACEMENT_ATR = 0.80
MARKET_LOCATION_M5_MSS_LOOKBACK = 120
MARKET_LOCATION_M5_MSS_SWING_LOOKBACK = 2
MARKET_LOCATION_ACCEPTANCE_BARS = 2
MARKET_LOCATION_SWEEP_EXPIRY_MINUTES = 120


# ── Sweep-candle wick qualification (L-016) ──────────────────────────────────
# `_check_sweep_rejection` admits a setup on two conditions only: any extreme in
# the last 4 bars pierced the level, and the LAST bar closes back through it.
# Nothing requires the piercing candle to have REJECTED anything, so a shallow
# two-pip tag is admitted on identical terms to a violent rejection wick.
#
# The remedy requires the sweeping candle's wick beyond the level to be at least
# this fraction of that candle's total range:
#
#     (high - max(open,close)) / range   for a BSL (buy-side) sweep
#     (min(open,close) - low)  / range   for an SSL (sell-side) sweep
#
#: Grade C — design only (§13.7). MQL5 art. 22140 ships a detector around
#: MIN_WICK_RATIO = 45 and publishes no win rate, profit factor, trade count or
#: date range. 45 is a starting value, not a validated one, which is why L-016
#: sweeps 0.35 / 0.45 / 0.55 rather than adopting it.
SWEEP_WICK_RATIO_MIN = 0.45

#: Master switch. Default OFF — with this False the ratio is still COMPUTED and
#: stamped on the trigger for attribution, but nothing branches on it, so the
#: baseline trade list is reproduced exactly. Ledger L-016, PROPOSED.
SWEEP_WICK_FILTER_ENABLED = False


# ── Configuration era ────────────────────────────────────────────────────────
#: Stamped onto every incident row so aggregates cannot silently pool trades
#: taken under different gate configurations. Bump this whenever a gate flag
#: changes (EMA_BAND, VP_GATE, PDR_GATE, VPLR, allow_whole_day, ...).
#:
#: Why it exists: `logs/sa_incidents.jsonl` currently holds 204 `Whole_day`
#: rows from 2026-05-06..05-15, taken before 13.6 closed that window. Every
#: naive query over that file pools them with current-config trades.
CONFIG_ERA = "2026-09-09-pre-high-confidence-restored"

# L-019 operator-requested demo trigger. 'MN1' means monthly, never M1.
CRT_ENABLED = True
CRT_TIMEFRAMES = {"MN1": mt5.TIMEFRAME_MN1, "W1": mt5.TIMEFRAME_W1,
                  "D1": mt5.TIMEFRAME_D1}
CRT_RANGE_BARS = 10
CRT_LEVEL_ATR = 0.10
CRT_STOP_ATR = 0.30
CRT_MAX_ENTRY_BARS = 24
CRT_MIN_R = 2.0
CRT_ORDER_PREFIX = "SA_CRT_"
CRT_CONFLUENCE_MODE = "OBSERVE"
CRT_CONFLUENCE_MODES = ("OBSERVE", "MSS", "MSS_RETEST")

# L-017: operator-requested entry contract. Initial mechanical definitions,
# not optimized thresholds or a claim of positive trading expectancy.
RECLAIM_FVG_ENABLED = True
RECLAIM_SWING_BARS = 3
RECLAIM_ATR_PERIOD = 14
RECLAIM_ZONE_ATR = 0.10
RECLAIM_BODY_ATR = 0.80
RECLAIM_BODY_FRACTION = 0.60
RECLAIM_CLOSE_LOCATION = 0.75
RECLAIM_MIN_FVG_ATR = 0.05
RECLAIM_MAX_FVG_BARS = 24

# L-018: enter a confirmed M15 FVG, targeting before broken structure.
# Reuses displacement/age definitions above and the existing FVG 0.3 ATR stop
# buffer. The 2R and cost floors stay binding; never tighten a stop to fit TP.
M15_FVG_ENTRY_ENABLED = True
M15_FVG_STOP_ATR = 0.30
M15_FVG_MIN_R = 2.0
M15_FVG_ORDER_COMMENT = "SA_FVG_M15"
