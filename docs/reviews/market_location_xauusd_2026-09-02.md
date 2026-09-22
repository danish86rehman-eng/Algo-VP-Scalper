# XAUUSD Market-Location Diagnostic

This report is generated from completed bars only. Chart prices are not
inputs to the engine; they are visual reference only.

- **As of:** `2026-09-02T12:15:00+00:00`
- **Current price:** `4333.188`
- **ATR:** `8.325`
- **Location:** `ABOVE_VALUE`
- **VP state:** `ABOVE_VALUE`
- **S/R state:** `UNKNOWN_LOCATION`
- **Value-area event:** `VAH_ACCEPTANCE`
- **VP/SR confluence:** `0.000`

## Nearest levels

| Evidence | Price | Distance (ATR) |
|---|---:|---:|
| Support | 4324.880 | 0.998 |
| Resistance | 4371.975 | 4.659 |
| POC | 4327.180 | 0.722 |
| VAH | 4328.793 | 0.528 |
| VAL | 4321.757 | 1.373 |

## Selected profiles

| Status | TF | Anchor | POC | VAH | VAL | Volume source |
|---|---|---|---:|---:|---:|---|
| ACTIVE | M15 | 2026-09-02T10:00:00+00:00 → 2026-09-02T12:00:00+00:00 | 4307.617 | 4326.355 | 4301.851 | TICK_VOLUME |
| ACTIVE | H4 | 2026-09-01T00:00:00+00:00 → 2026-09-02T08:00:00+00:00 | 4327.180 | 4372.028 | 4282.332 | TICK_VOLUME |
| REFERENCE | M15 | 2026-09-02T08:00:00+00:00 → 2026-09-02T10:00:00+00:00 | 4308.050 | 4322.928 | 4305.570 | TICK_VOLUME |
| REFERENCE | M15 | 2026-09-02T06:45:00+00:00 → 2026-09-02T08:00:00+00:00 | 4324.572 | 4328.793 | 4321.757 | TICK_VOLUME |
| REFERENCE | H4 | 2026-08-31T00:00:00+00:00 → 2026-09-01T00:00:00+00:00 | 4443.994 | 4456.676 | 4424.972 | TICK_VOLUME |
| REFERENCE | H4 | 2026-08-28T12:00:00+00:00 → 2026-08-31T00:00:00+00:00 | 4455.428 | 4573.409 | 4435.765 | TICK_VOLUME |

## Structural zones near price

| Type | Zone | Strength | Touches | Rejections | Source |
|---|---:|---:|---:|---:|---|
| SUPPORT | 4323.125–4327.581 | 1.000 | 42 | 8 | H4_STRUCTURAL_HL|H4_IMPULSE_EXTREME|H4_IMPULSE_ORIGIN|H4_STRONG_REJECTION |
| SUPPORT | 4309.460–4314.678 | 1.000 | 24 | 3 | H4_STRUCTURAL_LL|H4_IMPULSE_EXTREME|H4_IMPULSE_ORIGIN|H4_STRUCTURAL_HL |
| FLIP → SUPPORT | 4302.852–4305.766 | 0.900 | 21 | 0 | H4_STRUCTURAL_HH|H4_IMPULSE_EXTREME|H4_IMPULSE_ORIGIN |
| RESISTANCE | 4370.518–4373.432 | 1.000 | 39 | 5 | H4_STRUCTURAL_HH|H4_IMPULSE_EXTREME|H4_IMPULSE_ORIGIN |
| FLIP → RESISTANCE | 4374.864–4378.845 | 0.736 | 20 | 1 | H4_CONSOLIDATION_HIGH|H4_STRONG_REJECTION |
| FLIP → RESISTANCE | 4380.565–4383.479 | 0.716 | 21 | 2 | H4_STRONG_REJECTION |
| SUPPORT | 4280.875–4283.789 | 0.330 | 0 | 0 | H4_CONSOLIDATION_LOW |
| FLIP → RESISTANCE | 4394.981–4397.895 | 0.785 | 3 | 1 | H4_STRUCTURAL_LL|H4_IMPULSE_EXTREME|H4_IMPULSE_ORIGIN |
| RESISTANCE | 4434.956–4437.870 | 1.000 | 39 | 2 | H4_STRUCTURAL_LH|H4_IMPULSE_EXTREME|H4_IMPULSE_ORIGIN |
| SUPPORT | 4221.790–4224.704 | 0.650 | 0 | 0 | H4_STRUCTURAL_HL|H4_IMPULSE_EXTREME|H4_IMPULSE_ORIGIN |

## Interpretation

The snapshot is location evidence for the entry engine. A POC/VAH/VAL touch is not an order by itself; liquidity interaction, displacement/MSS and M5 confirmation remain separate requirements.

**Causality note:** swings are right-side confirmed, profiles use only completed anchored legs, and the runtime/replay frame windows are capped to the same configured lengths.
