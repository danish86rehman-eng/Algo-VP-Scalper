# XAUUSD Market-Location Diagnostic

This report is generated from completed bars only. Chart prices are not
inputs to the engine; they are visual reference only.

- **As of:** `2026-09-02T12:15:00+00:00`
- **Current price:** `4333.188`
- **ATR:** `8.325`
- **Location:** `BELOW_VALUE`
- **VP state:** `BELOW_VALUE`
- **S/R state:** `UNKNOWN_LOCATION`
- **Value-area event:** `VAL_ACCEPTANCE`
- **VP/SR confluence:** `0.000`

## Nearest levels

| Evidence | Price | Distance (ATR) |
|---|---:|---:|
| Support | 4323.980 | 1.106 |
| Resistance | 4436.216 | 12.376 |
| POC | 4622.033 | 34.696 |
| VAH | 4557.927 | 26.995 |
| VAL | 4579.063 | 29.534 |

## W1 PROFILE

- **Anchor:** `UP`
- **Start:** `2025-10-26T00:00:00+00:00`
- **End:** `2026-01-25T00:00:00+00:00`
- **Confirmation:** `2026-02-15T00:00:00+00:00`
- **High / Low:** `5595.401` / `3886.620`
- **POC / VAH / VAL:** `4008.676` / `4557.927` / `3886.620`
- **HVNs:** `3947.648, 4008.676, 4069.704, 4191.759, 4313.815`
- **LVNs:** `—`
- **Selection:** score `5.886`, `7.019` ATR, `14` bars; `INITIAL_MEANINGFUL_LEG`

## H4 PROFILE

- **Anchor:** `DOWN`
- **Start:** `2026-08-25T00:00:00+00:00`
- **End:** `2026-08-31T00:00:00+00:00`
- **Confirmation:** `2026-08-31T16:00:00+00:00`
- **High / Low:** `4697.232` / `4396.438`
- **POC / VAH / VAL:** `4622.033` / `4654.261` / `4579.063`
- **HVNs:** `4589.806, 4600.548, 4611.291, 4622.033, 4632.776, 4643.519`
- **LVNs:** `—`
- **Selection:** score `5.111`, `7.018` ATR, `26` bars; `INITIAL_MEANINGFUL_LEG`

## CURRENT PRICE LOCATION

| Level | Price | Distance (ATR) |
|---|---:|---:|
| W1 POC | 4008.676 | 38.980 |
| W1 VAH | 4557.927 | 26.995 |
| W1 VAL | 3886.620 | 53.641 |
| H4 POC | 4622.033 | 34.696 |
| H4 VAH | 4654.261 | 38.567 |
| H4 VAL | 4579.063 | 29.534 |
| Nearest VP/SR zone | — | — |
| Directional location | `BELOW_VALUE` | — |

## CONFLUENCES

| Kind | Components | Structural zones | Strength | Distance (ATR) |
|---|---|---|---:|---:|

## Selected profiles (including supplementary context)

| Status | TF | Anchor | POC | VAH | VAL | Volume source |
|---|---|---|---:|---:|---:|---|
| ACTIVE | W1 | 2025-10-26T00:00:00+00:00 → 2026-01-25T00:00:00+00:00 | 4008.676 | 4557.927 | 3886.620 | TICK_VOLUME |
| ACTIVE | H4 | 2026-08-25T00:00:00+00:00 → 2026-08-31T00:00:00+00:00 | 4622.033 | 4654.261 | 4579.063 | TICK_VOLUME |
| ACTIVE | M15 | 2026-09-01T05:45:00+00:00 → 2026-09-01T13:00:00+00:00 | 4370.028 | 4402.225 | 4346.613 | TICK_VOLUME |
| REFERENCE | W1 | 2025-07-27T00:00:00+00:00 → 2025-10-19T00:00:00+00:00 | 3344.913 | 4036.102 | 3268.114 | TICK_VOLUME |
| REFERENCE | W1 | 2025-06-29T00:00:00+00:00 → 2026-01-25T00:00:00+00:00 | 3308.191 | 4331.416 | 3248.001 | TICK_VOLUME |
| REFERENCE | H4 | 2026-08-03T12:00:00+00:00 → 2026-08-06T00:00:00+00:00 | 4048.573 | 4186.277 | 4019.065 | TICK_VOLUME |
| REFERENCE | H4 | 2026-08-03T12:00:00+00:00 → 2026-08-07T12:00:00+00:00 | 4257.798 | 4361.595 | 4102.103 | TICK_VOLUME |
| REFERENCE | M15 | 2026-09-01T00:15:00+00:00 → 2026-09-01T13:00:00+00:00 | 4370.341 | 4432.246 | 4326.124 | TICK_VOLUME |

## Structural zones near price

| Type | Zone | Strength | Touches | Rejections | Source |
|---|---:|---:|---:|---:|---|
| SUPPORT | 4321.320–4326.039 | 1.000 | 33 | 5 | PDL|H4_STRUCTURAL_HL|H4_IMPULSE_EXTREME|H4_IMPULSE_ORIGIN |
| SUPPORT | 4309.460–4314.678 | 1.000 | 22 | 3 | H4_STRUCTURAL_LL|H4_IMPULSE_EXTREME|H4_IMPULSE_ORIGIN|D1_STRUCTURAL_HL|D1_IMPULSE_EXTREME|D1_IMPULSE_ORIGIN|H4_STRUCTURAL_HL |
| FLIP → SUPPORT | 4302.852–4305.766 | 0.900 | 19 | 0 | H4_STRUCTURAL_HH|H4_IMPULSE_EXTREME|H4_IMPULSE_ORIGIN |
| SUPPORT | 4364.892–4367.806 | 1.000 | 51 | 8 | D1_STRUCTURAL_LL|D1_IMPULSE_EXTREME|D1_IMPULSE_ORIGIN |
| FLIP → SUPPORT | 4370.518–4373.432 | 1.000 | 47 | 6 | H4_STRUCTURAL_HH|H4_IMPULSE_EXTREME|H4_IMPULSE_ORIGIN |
| FLIP → SUPPORT | 4380.240–4384.008 | 1.000 | 59 | 8 | W1_STRUCTURAL_HH|W1_IMPULSE_EXTREME|W1_IMPULSE_ORIGIN|H4_STRONG_REJECTION|D1_STRUCTURAL_LH|D1_IMPULSE_EXTREME|D1_IMPULSE_ORIGIN |
| SUPPORT | 4394.981–4397.895 | 0.665 | 3 | 1 | H4_STRUCTURAL_LL|H4_IMPULSE_EXTREME |
| SUPPORT | 4400.688–4403.602 | 1.000 | 46 | 15 | W1_STRUCTURAL_HL|W1_IMPULSE_EXTREME|W1_IMPULSE_ORIGIN |
| SUPPORT | 4411.463–4414.377 | 0.440 | 2 | 1 | H4_CONSOLIDATION_LOW |
| RESISTANCE | 4434.268–4437.870 | 1.000 | 45 | 3 | PWL|H4_STRUCTURAL_LH|H4_IMPULSE_EXTREME|H4_IMPULSE_ORIGIN |

## Interpretation

The snapshot is location evidence for the entry engine. A POC/VAH/VAL touch is not an order by itself; liquidity interaction, displacement/MSS and M5 confirmation remain separate requirements.

**Causality note:** swings are right-side confirmed, profiles use only completed anchored legs, and the runtime/replay frame windows are capped to the same configured lengths.
