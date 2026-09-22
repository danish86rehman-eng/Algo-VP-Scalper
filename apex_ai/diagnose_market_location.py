"""Produce a causal XAUUSD VP/SR diagnostic report from exported MT5 bars."""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from scalper.market_location import MarketLocationEngine


def _load(path: str | None) -> pd.DataFrame:
    if not path:
        return pd.DataFrame()
    frame = pd.read_csv(path)
    if "time" in frame.columns:
        frame["time"] = pd.to_datetime(frame["time"], utc=True)
    return frame


def _fmt(value):
    return "—" if value is None else f"{float(value):.3f}"


def render(snapshot) -> str:
    by_tf = {profile.timeframe: profile for profile in snapshot.profiles
             if profile.status == "ACTIVE"}
    references = {profile.timeframe: profile for profile in snapshot.profiles
                  if profile.status == "REFERENCE"
                  and "REFERENCE_PRIMARY_" in profile.source}

    def profile_block(label: str, profile):
        if profile is None:
            return [f"## {label}", "", "No causal active profile was available.", ""]
        return [
            f"## {label}", "",
            f"- **Anchor:** `{profile.direction}`",
            f"- **Anchor start:** `{profile.anchor_start_time}` at `{profile.anchor_start_price:.3f}`",
            f"- **Anchor end:** `{profile.anchor_end_time}` at `{profile.anchor_end_price:.3f}`",
            f"- **Confirmation:** `{profile.confirmation_time}`",
            f"- **Profile high / low:** `{profile.profile_high:.3f}` / `{profile.profile_low:.3f}`",
            f"- **POC / VAH / VAL:** `{profile.poc:.3f}` / `{profile.vah:.3f}` / `{profile.val:.3f}`",
            f"- **Construction:** `{profile.source_timeframe}` tick volume, `{profile.row_count}` rows, bin `{profile.profile.bin_size:.6f}`, actual VA `{profile.actual_value_area_percentage:.3f}%`, total volume `{profile.total_volume:.0f}`",
            f"- **HVNs:** `{', '.join(f'{x:.3f}' for x in profile.high_volume_nodes) or '—'}`",
            f"- **LVNs:** `{', '.join(f'{x:.3f}' for x in profile.low_volume_nodes) or '—'}`",
            f"- **Selection:** score `{profile.selection_score:.3f}`, `{profile.leg_size_atr:.3f}` ATR, `{profile.anchor_bars}` bars; `{profile.replacement_reason}`",
            "",
        ]

    lines = [
        "# XAUUSD Market-Location Diagnostic",
        "",
        "This report is generated from completed bars only. Chart prices are not",
        "inputs to the engine; they are visual reference only.",
        "",
        f"- **As of:** `{snapshot.as_of}`",
        f"- **Current price:** `{snapshot.current_price:.3f}`",
        f"- **ATR:** `{snapshot.atr:.3f}`",
        f"- **Location:** `{snapshot.location_type}`",
        f"- **VP state:** `{snapshot.vp_state}`",
        f"- **S/R state:** `{snapshot.sr_state}`",
        f"- **Value-area event:** `{snapshot.value_area_event}`",
        f"- **VP/SR confluence:** `{snapshot.vp_sr_confluence_score:.3f}`",
        "",
        "## Nearest levels",
        "",
        "| Evidence | Price | Distance (ATR) |",
        "|---|---:|---:|",
        f"| Support | {_fmt(snapshot.nearest_support)} | {_fmt(snapshot.distance_to_support_atr)} |",
        f"| Resistance | {_fmt(snapshot.nearest_resistance)} | {_fmt(snapshot.distance_to_resistance_atr)} |",
        f"| POC | {_fmt(snapshot.nearest_poc)} | {_fmt(snapshot.distance_to_poc_atr)} |",
        f"| VAH | {_fmt(snapshot.nearest_vah)} | {_fmt(snapshot.distance_to_vah_atr)} |",
        f"| VAL | {_fmt(snapshot.nearest_val)} | {_fmt(snapshot.distance_to_val_atr)} |",
        "",
    ]
    lines.extend(profile_block("W1 ACTIVE PROFILE", by_tf.get("W1")))
    lines.extend(profile_block("W1 REFERENCE PROFILE", references.get("W1")))
    lines.extend(profile_block("H4 ACTIVE PROFILE", by_tf.get("H4")))
    lines.extend(profile_block("H4 REFERENCE PROFILE", references.get("H4")))
    lines.extend([
        "## CURRENT PRICE LOCATION",
        "",
        "| Level | Price | Distance (ATR) |",
        "|---|---:|---:|",
        f"| W1 POC | {_fmt(snapshot.w1_poc)} | {_fmt(None if snapshot.w1_poc is None else abs(snapshot.current_price - snapshot.w1_poc) / snapshot.atr if snapshot.atr else None)} |",
        f"| W1 VAH | {_fmt(snapshot.w1_vah)} | {_fmt(None if snapshot.w1_vah is None else abs(snapshot.current_price - snapshot.w1_vah) / snapshot.atr if snapshot.atr else None)} |",
        f"| W1 VAL | {_fmt(snapshot.w1_val)} | {_fmt(None if snapshot.w1_val is None else abs(snapshot.current_price - snapshot.w1_val) / snapshot.atr if snapshot.atr else None)} |",
        f"| H4 POC | {_fmt(snapshot.h4_poc)} | {_fmt(None if snapshot.h4_poc is None else abs(snapshot.current_price - snapshot.h4_poc) / snapshot.atr if snapshot.atr else None)} |",
        f"| H4 VAH | {_fmt(snapshot.h4_vah)} | {_fmt(None if snapshot.h4_vah is None else abs(snapshot.current_price - snapshot.h4_vah) / snapshot.atr if snapshot.atr else None)} |",
        f"| H4 VAL | {_fmt(snapshot.h4_val)} | {_fmt(None if snapshot.h4_val is None else abs(snapshot.current_price - snapshot.h4_val) / snapshot.atr if snapshot.atr else None)} |",
        f"| Nearest VP/SR zone | {_fmt(min((c.price for c in snapshot.vp_confluences), key=lambda x: abs(x - snapshot.current_price), default=None))} | — |",
        f"| Directional location | `{snapshot.location_type}` | — |",
        "",
        "## CONFLUENCES",
        "",
        "| Kind | Components | Structural zones | Strength | Distance (ATR) |",
        "|---|---|---|---:|---:|",
    ])
    for confluence in snapshot.vp_confluences:
        lines.append(
            f"| {confluence.kind} | {' + '.join(confluence.components)} | "
            f"{', '.join(confluence.structural_zone_ids) or '—'} | "
            f"{confluence.strength_score:.3f} | {_fmt(confluence.distance_from_price_atr)} |"
        )
    lines.extend([
        "",
        "## Selected profiles (including supplementary context)",
        "",
        "| Status | TF | Anchor endpoints | Profile extremes | POC | VAH | VAL | Source |",
        "|---|---|---|---|---:|---:|---:|---|",
    ])
    for profile in snapshot.profiles:
        lines.append(
            f"| {profile.status} | {profile.timeframe} | "
            f"{profile.anchor_start_time} @ {profile.anchor_start_price:.3f} → "
            f"{profile.anchor_end_time} @ {profile.anchor_end_price:.3f} | "
            f"{profile.profile_low:.3f}–{profile.profile_high:.3f} | "
            f"{profile.poc:.3f} | {profile.vah:.3f} | {profile.val:.3f} | "
            f"{profile.source_timeframe} {profile.volume_source} |"
        )
    lines.extend([
        "",
        "## Structural zones near price",
        "",
        "| Type | Zone | Strength | Touches | Rejections | Source |",
        "|---|---:|---:|---:|---:|---|",
    ])
    nearby = sorted(snapshot.zones, key=lambda z: z.distance_from_price)[:10]
    for zone in nearby:
        lines.append(
            f"| {zone.type}{' → ' + zone.flip_to if zone.flip_to else ''} | "
            f"{zone.zone_low:.3f}–{zone.zone_high:.3f} | {zone.strength_score:.3f} | "
            f"{zone.touch_count} | {zone.rejection_count} | {zone.source} |"
        )
    lines.extend([
        "",
        "## Interpretation",
        "",
        "The snapshot is location evidence for the entry engine. A POC/VAH/VAL "
        "touch is not an order by itself; liquidity interaction, displacement/MSS "
        "and M5 confirmation remain separate requirements.",
        "",
        "**Causality note:** swings are right-side confirmed, profiles use only "
        "completed anchored legs, and the runtime/replay frame windows are capped "
        "to the same configured lengths.",
    ])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--m15", required=True)
    parser.add_argument("--h4", required=True)
    parser.add_argument("--w1", required=True)
    parser.add_argument("--d1", default=None)
    parser.add_argument("--h1", default=None)
    parser.add_argument("--m5", default=None)
    parser.add_argument("--as-of", default=None,
                        help="UTC decision timestamp; defaults to last M15 bar close")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    m15 = _load(args.m15)
    h4 = _load(args.h4)
    w1 = _load(args.w1)
    d1 = _load(args.d1)
    h1 = _load(args.h1)
    m5 = _load(args.m5)
    if m15.empty or h4.empty:
        raise SystemExit("M15 and H4 data are required")
    as_of = (pd.Timestamp(args.as_of).tz_localize("UTC")
             if args.as_of and pd.Timestamp(args.as_of).tzinfo is None
             else pd.Timestamp(args.as_of) if args.as_of
             else m15["time"].iloc[-1] + pd.Timedelta(minutes=15))
    closed = m15.loc[m15["time"] + pd.Timedelta(minutes=15) <= as_of]
    if closed.empty:
        raise SystemExit("No completed M15 bar at --as-of")
    snapshot = MarketLocationEngine().snapshot(
        symbol="XAUUSD", current_price=float(closed["close"].iloc[-1]),
        df_w1=w1, df_d1=d1, df_h4=h4, df_m15=m15,
        df_h1=h1, df_m5=m5, as_of=as_of)
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(snapshot), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
