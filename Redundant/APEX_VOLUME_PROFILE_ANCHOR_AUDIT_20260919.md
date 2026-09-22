# APEX XAUUSD Volume-Profile Anchor Audit

**Audit date:** 2026-09-19  
**Broker feed:** Exness-MT5Trial2, account 40280210 (DEMO)  
**Decision data:** completed bars only; latest completed M15 close = **4380.995** at 2026-09-18 20:45 UTC; last broker tick = 4378.29/4378.34 at 20:57:59 UTC  
**Scope:** read-only audit. No trading, execution, risk, SL/TP, Guardian, session, cooldown, news, trigger-priority, or anchor-selection code was changed.

## Executive verdict

- **The current anchor-selection policy is not suitable for the requested human-like “current active auction” behavior.** It generates candidates across the retained 260-bar history before it establishes current structure.
- **Current W1 anchor: mathematically valid profile, structurally stale as ACTIVE.** The 2025-10-26 low to 2026-01-25 high remains useful as a **REFERENCE** profile, but a more defensible current weekly dealing range is 2026-06-28 **3942.204** to 2026-08-23 **4697.232**.
- **Current H4 anchor: not structurally appropriate as LOCAL.** The selector actually chose 2026-08-25 **4673.729** to 2026-09-18 **4334.102**, not 4235.035. The displayed 4235.035 is an internal profile wick, not the selected end anchor.
- **The reported W1/H4 POC arithmetic is correct for the selected coarse W1/H4 candle ranges.** Independent reimplementation reproduced both values and conserved all tick volume.
- **The histogram input model is too coarse.** Each W1/H4 candle’s tick volume is spread uniformly through its full high-low range. With the same anchors and bins, reconstruction from lower-timeframe bars moved the W1 POC from **4008.676 to 4191.759** and the H4 POC from **4329.041 to 4391.711**.
- **Recommended detailed active POCs (not implemented):** W1 **4052.312** from M15 data over 3942.204→4697.232; H4 **4358.628** from M5 data over 4235.035→4399.826.

All timestamps below are UTC.

## Current algorithm

### Candidate generation

1. Load 260 completed W1 bars and 260 completed H4 bars.
2. Use symmetric fractal pivots (`lookback=2` W1, `lookback=3` H4).
3. Keep only the last 20 alternating pivots.
4. Generate every consecutive opposite-role leg plus many non-consecutive composite legs.
5. Require at least 5 bars and 2 ATR for a consecutive leg; composite legs require at least 10 bars and 3 ATR.

This means the procedure begins with historical candidate enumeration. It does **not** first identify the current BOS, current dealing range, or whether current price belongs to the candidate auction.

### Exact scores

Consecutive leg:

```text
score = 0.60 × min(leg_ATR, 8)
      + 1.25 × displacement
      + 0.35 × min(1, bars / 28)
      + 0.30 × volume_bonus
      + 0.10 × recency
```

Composite leg:

```text
score = 0.60 × min(leg_ATR, 8)
      + 1.00 × displacement
      + 0.25 × min(1, bars / 56)
      + 0.10 × recency
```

`displacement = endpoint move / total absolute close path`. `recency = (right pivot index + 1) / 260`.

Important corrections to the suspected design:

- **Swing prominence is not a score input.** The audit reports topographic prominence independently, but production only requires a fractal pivot.
- **BOS, liquidity raids, current-price containment, and current structural relevance are not score inputs.** HH/HL/LH/LL labels merely determine support/resistance roles.
- Leg size saturates at 8 ATR, causing many large composite legs to receive the same **4.8** size contribution.

### Final W1/H4 choice

- **W1:** compute the highest raw score, retain candidates scoring at least `best × 0.995`, then choose the one with the latest right endpoint.
- **H4:** compute the highest raw score, retain candidates scoring at least `best × 0.70`, then choose the one with the latest right endpoint.
- A persisted prior anchor can subsequently be held unless a newer sufficiently large leg is confirmed.

Thus W1 is almost score-first; H4 is effectively “latest endpoint among a very broad score-qualified set.” Neither implements top-down current-auction discovery.

## Why the current W1 won

Raw best score was **5.923251**. The 99.5% W1 floor was **5.893634**. Only candidates 1 and 2 cleared that floor. Candidate 2 ended later, so it replaced candidate 1 despite having the second-highest raw score.

Current selected W1, 3886.620→5595.401:

| Component | Weighted contribution |
|---|---:|
| ATR size: 7.019 ATR | 4.211473 |
| Displacement: 1.000 | 1.250000 |
| Duration: 14 bars | 0.175000 |
| Volume bonus | 0.192215 |
| Recency | 0.087692 |
| **Total** | **5.916380** |

It defeated candidate 1 only at the post-score recency tie-break. Candidates 3–5 failed the 99.5% floor. No test asked whether Jan 2026 still controls Sep 2026 price.

## Top five W1 candidates from the current scorer

Prominence is shown as `start/end` in price units and ATR units. Distance is absolute distance from the completed-bar price 4380.995.

| # | Anchor and direction | Move / ATR / bars | Prominence price; ATR | Structure / liquidity | Inside? | POC / VAH / VAL | Distance to POC / VAH / VAL | Weighted score components: size + disp + dur + vol + rec = total | Why ranked |
|---:|---|---|---|---|---|---|---|---|---|
| 1 | 2025-07-27 3268.114 → 2025-10-19 4381.697, LOW→HIGH | 1113.583 / 7.372 / 13 | 56.894/350.971; 0.436/2.430 | HL→HH, bullish BOS-like expansion; no explicit liquidity test | Yes | 3344.913 / 4036.102 / 3268.114 | 1036.082 / 344.893 / 1112.881 | 4.423364 + 1.207924 + .162500 + .047156 + .082308 = **5.923251** | Raw #1: large and almost straight |
| 2 | 2025-10-26 3886.620 → 2026-01-25 5595.401, LOW→HIGH | 1708.781 / 7.019 / 14 | 122.105/1492.046; 0.801/6.159 | HL→HH, bullish BOS-like expansion; no explicit liquidity test | Yes | 4008.676 / 4557.927 / 3886.620 | 372.319 / 176.932 / 494.375 | 4.211473 + 1.250000 + .175000 + .192215 + .087692 = **5.916380** | Selected: within 99.5% of #1 and later endpoint |
| 3 | 2024-12-15 2583.399 → 2025-04-20 3500.113, LOW→HIGH | 916.714 / 7.198 / 19 | 51.234/207.422; 0.597/1.629 | HL→HH; old bullish expansion | **No** | 2867.896 / 3057.561 / 2615.010 | 1513.099 / 1323.434 / 1765.985 | 4.319068 + 1.186383 + .237500 + .074719 + .072308 = **5.889977** | Missed W1 floor; price is above entire auction |
| 4 | 2025-06-29 3248.001 → 2026-01-25 5595.401, LOW→HIGH | 2347.400 / 9.690 / 31 | 92.424/1492.046; 0.560/6.159 | Composite HL→HH; spans internal auctions | Yes | 3308.191 / 4331.416 / 3248.001 | 1072.804 / 49.579 / 1132.994 | 4.800000 + .811623 + .138393 + 0 + .087692 = **5.837709** | Size capped; composite path less efficient |
| 5 | 2025-07-27 3268.114 → 2026-01-25 5595.401, LOW→HIGH | 2327.287 / 9.607 / 27 | 56.894/1492.046; 0.436/6.159 | Composite HL→HH; spans internal auctions | Yes | 4003.047 / 4554.246 / 3635.580 | 377.948 / 173.251 / 745.415 | 4.800000 + .823030 + .120536 + 0 + .087692 = **5.831258** | Size capped; below near-tie floor |

These are the scorer’s top five, not the five most plausible active auctions. The absence of the latest weekly range from this list is itself the defect: 2026-06-28→2026-08-23 scores only **3.182664** because it is 3.222 ATR, even though it is the latest confirmed weekly dealing range containing price.

## Weekly current structure

The recent W1 sequence is:

```text
2026-01-25 HH 5595.401
2026-02-01 HL 4402.145
2026-03-01 LH 5419.133
2026-03-22 LL 4098.470
2026-04-12 LH 4889.592
2026-06-28 LL 3942.204   <- most recent important bearish continuation / BOS-like LL
2026-08-23 LH 4697.232   <- latest confirmed major high
current 4380.995         <- inside 3942.204–4697.232
```

This establishes a current bearish macro context and a current dealing range bounded by the latest confirmed LL and LH. The old Oct-Jan markup remains historically important but is not the most recent structure governing price.

## Why the displayed/current H4 won

Raw H4 best score was **5.945893**; the permissive 70% floor was only **4.162125**. The selector then chose the qualifying candidate with the latest right endpoint.

The actual selected leg is:

```text
2026-08-25 20:00  4673.729 LH
→ 2026-09-18 00:00 4334.102 HL
```

Its composite score is:

| Component | Weighted contribution |
|---|---:|
| ATR size: 8.154 ATR, capped | 4.800000 |
| Displacement: 0.178 | 0.178055 |
| Duration: 107 bars, capped | 0.250000 |
| Volume bonus | 0.000000 |
| Recency | 0.098462 |
| **Total** | **5.326517** |

It is not raw top 10, but it clears the low floor and ends later than every higher-scoring candidate. This is why it won.

The reported **4235.035** is not the anchor endpoint. It is the profile minimum produced by the 2026-09-16 16:00 candle inside the 107-bar segment. The record itself stores `anchor_low=4334.102`, `profile_low=4235.035`. Any output describing 4673.729→4235.035 as the chosen swing conflates anchor geometry with histogram extrema.

## Top ten H4 candidates from the current scorer

All ten are composite legs. Consequently volume bonus is zero and ATR size is capped at 4.8. `LL` endpoints are BOS-like/previous-low raids, but production does not validate a close-confirmed BOS or use the raid in scoring.

| # | Anchor, direction | ATR / bars | Prominence price; ATR | Structure / liquidity | POC / VAH / VAL | Distance to POC / VAH / VAL | Components: size + disp + dur + rec = total | Ranking reason |
|---:|---|---|---|---|---|---|---|---|
| 1 | Aug-28 12:00 4632.400 → Sep-02 00:00 4282.332, HIGH→LOW | 8.827 / 17 | 29.712/190.324; .789/4.799 | LH→LL; broke/raided Aug-31 low | 4442.363 / 4612.396 / 4392.353 | 61.368 / 231.401 / 11.358 | 4.8 + 1.000000 + .075893 + .070000 = **5.945893** | Raw #1; perfectly efficient close path |
| 2 | Aug-27 00:00 4643.186 → Sep-02 00:00 4282.332, HIGH→LOW | 9.099 / 26 | 38.336/190.324; 1.034/4.799 | LH→LL; same downside break | 4442.712 / 4603.091 / 4392.593 | 61.717 / 222.096 / 11.598 | 4.8 + .675727 + .116071 + .070000 = **5.661799** | Large, less efficient composite |
| 3 | Aug-25 20:00 4673.729 → Sep-02 00:00 4282.332, HIGH→LOW | 9.869 / 33 | 26.088/190.324; .688/4.799 | LH→LL; same downside break | 4593.442 / 4663.693 / 4432.869 | 212.447 / 282.698 / 51.874 | 4.8 + .625620 + .147321 + .070000 = **5.642941** | Larger span, lower efficiency |
| 4 | Aug-28 12:00 4632.400 → Sep-14 12:00 4253.500, HIGH→LOW | 9.410 / 70 | 29.712/70.790; .789/1.758 | LH→LL; later lower-low continuation | 4393.095 / 4442.950 / 4323.297 | 12.100 / 61.955 / 57.698 | 4.8 + .300087 + .250000 + .090385 = **5.440471** | Duration capped; weak path efficiency |
| 5 | Aug-25 20:00 4673.729 → Sep-04 12:00 4365.309, HIGH→LOW | 8.120 / 48 | 26.088/63.318; .688/1.667 | LH→HL; **not** bearish BOS | 4435.072 / 4559.174 / 4282.332 | 54.077 / 178.179 / 98.663 | 4.8 + .331462 + .214286 + .075769 = **5.421517** | Size cap overwhelms weaker structure |
| 6 | Aug-28 12:00 4632.400 → Sep-09 00:00 4341.021, HIGH→LOW | 9.325 / 48 | 29.712/63.051; .789/2.018 | LH→LL; raids 4365.309 | 4422.359 / 4476.814 / 4360.125 | 41.364 / 95.819 / 20.870 | 4.8 + .317289 + .214286 + .081923 = **5.413498** | Size cap plus later LL |
| 7 | Aug-25 20:00 4673.729 → Sep-14 12:00 4253.500, HIGH→LOW | 10.436 / 86 | 26.088/70.790; .688/1.758 | LH→LL; later downside continuation | 4393.576 / 4463.615 / 4303.527 | 12.581 / 82.620 / 77.468 | 4.8 + .272470 + .250000 + .090385 = **5.412855** | Long range; low efficiency |
| 8 | Aug-28 12:00 4632.400 → Sep-16 16:00 4235.035, HIGH→LOW | 10.080 / 83 | 29.712/137.606; .789/3.490 | LH→LL; raids 4253.500 | 4324.442 / 4423.783 / 4284.706 | 56.553 / 42.788 / 96.289 | 4.8 + .266306 + .250000 + .095385 = **5.411690** | Newer LL offsets poor efficiency |
| 9 | Aug-25 20:00 4673.729 → Sep-09 00:00 4341.021, HIGH→LOW | 10.648 / 64 | 26.088/63.051; .688/2.018 | LH→LL; raids 4365.309 | 4423.235 / 4493.686 / 4297.988 | 42.240 / 112.691 / 83.007 | 4.8 + .277720 + .250000 + .081923 = **5.409643** | Size capped; poor efficiency |
| 10 | Aug-27 00:00 4643.186 → Sep-14 12:00 4253.500, HIGH→LOW | 9.677 / 79 | 38.336/70.790; 1.034/1.758 | LH→LL; later downside continuation | 4393.387 / 4443.347 / 4303.460 | 12.392 / 62.352 / 77.535 | 4.8 + .268619 + .250000 + .090385 = **5.409004** | Size capped; poor efficiency |

Current price lies inside every H4 range in this table; containment therefore does not discriminate among them and is not evaluated by production anyway.

## H4 structural defect and current auction

The 2026-09-16 16:00 H4 candle had high **4368.559**, low **4235.035**, and close **4272.462**. The fractal detector emitted both a resistance pivot and a support pivot at the same index. This is possible because highs and lows are tested independently and the alternation pass does not resolve same-index dual pivots. Candidate generation can therefore form structurally ambiguous paths.

Subsequent completed structure was nevertheless clear enough for a local auction:

```text
Sep-14 12:00 LL 4253.500
Sep-16 16:00 LL 4235.035  <- liquidity sweep/new low
Sep-17 12:00 HH 4381.321
Sep-18 00:00 HL 4334.102
Sep-18 04:00 HH 4399.826  <- confirmed at 20:00
current 4380.995           <- inside 4235.035–4399.826
```

The Sep-16 low swept 4253.500 by 18.465, then price displaced upward and printed HH-HL-HH. That is the current local auction; the month-long Aug-25 profile is useful background, not LOCAL.

## Independent VP arithmetic verification

I independently reimplemented bin overlap allocation and greedy value-area expansion instead of calling the production accumulator.

| Check | Current W1 | Current H4 |
|---|---:|---:|
| Anchor interval actually profiled | Oct-26→Jan-25 | Aug-25 20:00→Sep-18 00:00 |
| Bars | 14 W1 | 107 H4 |
| Profile low / high | 3886.620 / 5595.401 | 4235.035 / 4673.729 |
| Bins / bin size | 29 / 61.027893 | 43 / 10.445095 |
| Volume source | Exness tick volume | Exness tick volume |
| Real volume | 0 | 0 |
| Total distributed volume | 33,238,260 | 5,436,282 |
| Maximum-volume bin index (zero-based) | 2 | 9 |
| Max-bin volume | 3,110,366.761 | 315,431.051 |
| POC | **4008.675786** | **4329.040857** |
| VAH / VAL | 4557.926821 / 3886.620000 | 4433.491810 / 4276.815381 |
| Actual value-area volume | **72.523846%** | **70.021631%** |
| Volume conservation error | ~0 | 0 |

Verdict:

- Current W1 POC mathematically correct for those W1 bars: **YES**.
- Current H4 POC mathematically correct for those H4 bars: **YES**.
- The actual percentages exceed 70% because the greedy algorithm adds whole bins until the threshold is crossed.

## Volume-data check

Production uses `tick_volume`; Exness `real_volume` is zero for these XAUUSD bars. For every source candle, all tick volume is spread uniformly across the candle’s complete high-low range in proportion to bin overlap. It does not know where inside that range ticks traded.

Lower-timeframe reconstruction uses the same total tick volume—the broker’s lower-bar volumes aggregate exactly to the higher-bar total—but locates it more precisely in price:

| Fixed anchor and same bin geometry | Native TF POC | Detailed TF POC | POC shift |
|---|---:|---:|---:|
| Current W1 Oct-26→Jan-25 | W1 4008.676 | H1/M15 **4191.759** | **+183.084** |
| Current H4 Aug-25→Sep-18 | H4 4329.041 | M15/M5 **4391.711** | **+62.671** |

Using 48-row detailed profiles instead of retaining the coarse native bin geometry produced W1 4313.815 and H4 M5 4326.430, demonstrating that both data granularity **and bin resolution** matter. A production design should specify a stable row-count/tick-size policy and test sensitivity; it should not silently let higher-timeframe ATR choose a very coarse histogram.

Conclusion: lower-timeframe reconstruction materially improves fidelity and materially changes levels. Preferred hierarchy given available broker history:

- W1 structure chooses the anchor; H1 or M15 bars build the histogram.
- H4 structure chooses the anchor; M15 or preferably M5 bars build the histogram.
- Clip strictly to the anchor period; do not include later confirmation bars or future data.

## Recommended active and reference profiles (not implemented)

### W1 ACTIVE

**2026-06-28 3942.204 LOW → 2026-08-23 4697.232 HIGH**

Reason: this is the latest confirmed W1 LL→LH dealing range after the bearish sequence produced a lower low. Current price remains inside it. It answers “what weekly auction currently contains price?” better than the Jan expansion.

- Native W1 coarse profile: POC 4036.583, VAH 4351.178, VAL 3942.204.
- Recommended M15 48-row detailed profile: **POC 4052.312, VAH 4351.178, VAL 3942.204**.
- Actual detailed VA: 70.203%.

### W1 REFERENCE

Retain **2025-10-26 3886.620 → 2026-01-25 5595.401** as reference context. Do not average it with ACTIVE and do not let it drive the active-location gate merely because its historical expansion score is larger.

### H4 ACTIVE

**2026-09-16 16:00 4235.035 LOW → 2026-09-18 04:00 4399.826 HIGH**

Reason: the low swept the prior 4253.500 LL, price displaced upward through local highs, formed 4381.321 HH, 4334.102 HL, and 4399.826 HH, and current price remains inside the auction.

- Native H4 coarse profile: POC 4344.896, VAH 4372.361, VAL 4289.965.
- Recommended M5 48-row detailed profile: **POC 4358.628, VAH 4372.361, VAL 4286.532**.
- Actual detailed VA: 70.676%.

At 4380.995, price is about 8.634 above the recommended H4 VAH and 22.367 above its POC, which is materially different location evidence from the stale month-long profile.

## Required conceptual correction before implementation

The future selector should separate discovery from ranking:

1. Establish current W1/H4 structural state from current price backward.
2. Identify latest confirmed BOS/MSS, controlling high/low, and any liquidity raid.
3. Build a small set of structurally valid auctions that contain or directly govern current price.
4. Rank within that set by structural relevance, price membership, confirmation recency, BOS/displacement, pivot significance, then size.
5. Maintain `W1_ACTIVE_PROFILE` separately from optional `W1_REFERENCE_PROFILE`.
6. Resolve same-candle high/low pivots before constructing legs.
7. Keep anchor endpoints distinct from profile high/low in telemetry and UI.
8. Construct the histogram from lower-timeframe bars and lock a stable bin-resolution policy.

No implementation is included in this audit.
