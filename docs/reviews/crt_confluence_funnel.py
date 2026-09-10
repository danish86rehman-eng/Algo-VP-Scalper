"""Read-only localization of L-020's three near-entry R rejections.

Uses saved bars, the shared detector, and the already-recorded baseline close
barriers. No new strategy arm, no cost/parameter changes and no broker access.
"""
import json
from pathlib import Path
import sys
from collections import Counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"apex_ai"))
import pandas as pd
from scalper import decision_params as DP
from scalper.htf_crt import watch_crt
from scalper.session_checker import SASessionChecker

OUT = ROOT/"docs/reviews/2026-09-03-crt-confluence-evidence"
frames = {tf: pd.read_csv(OUT/f"XAUUSD_{tf}.csv", parse_dates=["time"])
          for tf in ("M5", "M15", *DP.CRT_TIMEFRAMES)}
for f in frames.values():
    f.time = pd.to_datetime(f.time, utc=True)
baseline = json.loads((OUT/"OBSERVE.json").read_text())
closes = sorted(baseline["trades"], key=lambda t: t["close_time"])
assert not any(t["trigger_type"] == "HTF_CRT_SWEEP" for t in closes)
cursor, barriers, near, states, seen = 0, {}, [], Counter(), set()
times = pd.Index(frames["M15"].time)
session = SASessionChecker()
for qi, row in enumerate(frames["M5"].itertuples()):
    now = row.time
    if not pd.Timestamp("2026-06-01T00:00Z") <= now < pd.Timestamp("2026-09-01T00:00Z"):
        continue
    while cursor < len(closes) and pd.Timestamp(closes[cursor]["close_time"]) <= now:
        t = closes[cursor]
        barriers[(t["symbol"], t["direction"])] = pd.Timestamp(t["close_time"])
        cursor += 1
    if not session.get_state(now).in_window:
        continue
    i = int(times.searchsorted(now, side="right"))-1
    plans = watch_crt(frames["M15"].iloc[max(0, i-DP.TRIGGER_BARS):i],
                      frames["M5"].iloc[max(0, qi-DP.CONFIRM_BARS):qi], frames,
                      row.open, row.open, now, last_closes=barriers, symbol="XAUUSD")
    for p in plans:
        if p.raid_at:
            seen.add(p.setup_id)
        states[p.reason] += 1
        if p.reason == "CRT_TARGET_R_TOO_SMALL":
            record = dict(at=now.isoformat(), **p.record())
            record["available_gross_r"] = abs(p.target-p.entry)/abs(p.entry-p.stop)
            near.append(record)
result = dict(near_entries=near, distinct_raid_setups=len(seen), watch_states=dict(states),
              scope="Localization only, exactly the fixed baseline's sessions/close barriers and saved M5 opening quotes")
(OUT/"funnel.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
