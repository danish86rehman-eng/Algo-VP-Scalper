# XAUUSD Top-Down Market-Location Evidence

Read-only runtime snapshot from the attached Exness MT5 terminal on 2026-09-19.
The observed prices below are diagnostic output, not strategy constants and were
not copied from the supplied screenshots.

## W1 macro profile

- Anchor: confirmed **UP** leg, `2025-10-26T00:00:00Z` → `2026-01-25T00:00:00Z`
- Confirmation: `2026-02-15T00:00:00Z`
- Profile high / low: `5595.401 / 3886.620`
- POC / VAH / VAL: `4008.676 / 4557.927 / 3886.620`
- HVNs: `3947.648, 4008.676, 4069.704, 4191.759, 4313.815`
- LVNs: none classified by the occupied-bin rule
- Selection evidence: score `5.916`, leg size `7.019 ATR`; selected from a near-tie using the narrow W1 recency floor.

## H4 local profile

- Anchor: confirmed composite **DOWN** leg, `2026-08-25T20:00:00Z` → `2026-09-18T00:00:00Z`
- Confirmation: `2026-09-18T16:00:00Z`
- Profile high / low: `4673.729 / 4235.035`
- POC / VAH / VAL: `4329.041 / 4433.492 / 4276.815`
- HVNs: `4318.596, 4329.041, 4339.486, 4349.932, 4360.376, 4391.711, 4402.157, 4412.602, 4423.047`
- LVNs: classified at the low-volume ledges around the profile extremes and upper range.
- Selection evidence: score `5.327`, leg size `8.154 ATR`; a composite leg was required because the local auction contains several internal H4 swings.

## Current location

- Observed price: `4378.315`; M15 ATR used for distance normalization: `6.226`
- State: `INSIDE_VALUE`
- W1 distances: POC `59.368 ATR`, VAH `28.847 ATR`, VAL `78.971 ATR`
- H4 distances: POC `7.914 ATR`, VAH `8.862 ATR`, VAL `16.302 ATR`
- Detected VP/SR confluence: `H4_VAH + H4_STRUCTURAL_RESISTANCE`, center `4434.241`, strength `0.550`
- No W1/H4 VP-level pair was within the configured ATR clustering tolerance at this snapshot; raw W1 and H4 levels remain separate.

## TradingView comparison

The supplied screenshots show the intended visual workflow: a broad macro
profile over the major expansion and a second, wider local profile over the
current range. The live APEX output reproduces that structure qualitatively:
the W1 profile captures the major expansion into the large high, while the H4
composite profile spans the current local high-to-low auction instead of a
short rolling window.

Exact TradingView equality is not claimed. TradingView may use a different
volume source, tick attribution, binning, or manual wick anchors; APEX uses
confirmed causal swings, bar-range volume spreading, dynamic ATR-sized bins,
and a 70% value area. Any numerical difference should therefore be explained
by those methodology differences, not hidden behind a single averaged level.
