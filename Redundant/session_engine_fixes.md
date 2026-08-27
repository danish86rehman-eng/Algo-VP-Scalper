# APEX AI — Session Engine: Required Fixes

## Context
This file describes all bugs found in `SessionEngine` (session_engine.py) that need to be corrected for accurate ICT-based trading session classification.

---

## Bug 1 — London Silver Bullet is 1 hour late

**Current code:**
```python
"LONDON_SB": (dtime(8, 0), dtime(9, 0), 1.2, True, True),
```

**Problem:**
The London Silver Bullet runs 07:00–08:00 UTC (03:00–04:00 NY time). The code has it at 08:00–09:00 UTC, which is 1 hour too late.

**Fix:**
```python
("LONDON_SB", dtime(7, 0), dtime(8, 0), 1.2, True, True),
```

---

## Bug 2 — NY AM Silver Bullet is completely missing

**Current code:**
No entry for NY AM Silver Bullet.

**Problem:**
The most important Silver Bullet window — NY AM at 14:00–15:00 UTC (10:00–11:00 NY time) — is entirely absent from the session list. This is the highest-probability ICT setup window of the day.

**Fix — add this entry:**
```python
("NY_AM_SB", dtime(14, 0), dtime(15, 0), 1.2, True, True),
```

---

## Bug 3 — NY_SB is the wrong session entirely

**Current code:**
```python
"NY_SB": (dtime(15, 0), dtime(16, 0), 1.2, True, True),
```

**Problem:**
15:00–16:00 UTC (10:00–11:00 NY time) is NOT a Silver Bullet. It is the **London Close Kill Zone**. The actual NY PM Silver Bullet is at 18:00–19:00 UTC (14:00–15:00 NY time).

**Fix — replace NY_SB with:**
```python
("LONDON_CLOSE_KZ", dtime(15, 0), dtime(17, 0), 0.8, True, False),  # London Close KZ
("NY_PM_SB",        dtime(18, 0), dtime(19, 0), 1.2, True, True),   # Actual NY PM Silver Bullet
```

---

## Bug 4 — Dict must be replaced with ordered list

**Current code:**
```python
SESSIONS = { ... }  # plain dict
```

**Problem:**
Priority ordering (Silver Bullet checked before Kill Zone) depends on dict insertion order, which is fragile. If anyone reorders entries, the logic silently breaks.

**Fix — replace the dict with a list of tuples:**
```python
SESSION_PRIORITY = [
    ("LONDON_SB",        dtime(7, 0),  dtime(8, 0),   1.2, True,  True),
    ("LONDON_KZ",        dtime(7, 0),  dtime(10, 0),  1.0, True,  False),
    ("NY_AM_SB",         dtime(14, 0), dtime(15, 0),  1.2, True,  True),
    ("LONDON_CLOSE_KZ",  dtime(15, 0), dtime(17, 0),  0.8, True,  False),
    ("NY_KZ",            dtime(12, 0), dtime(15, 0),  1.0, True,  False),
    ("NY_PM_SB",         dtime(18, 0), dtime(19, 0),  1.2, True,  True),
    ("ASIA",             dtime(0, 0),  dtime(6, 0),   0.4, False, False),
]
```

Update `get_state()` to iterate over `SESSION_PRIORITY` instead of `self.SESSIONS.items()`.

---

## Bug 5 — next_kill_zone() schedule is incomplete and has wrong times

**Current code:**
```python
schedule = [
    (dtime(7, 0),  "London KZ opens"),
    (dtime(8, 0),  "London Silver Bullet"),
    (dtime(12, 0), "NY KZ opens"),
    (dtime(15, 0), "NY Silver Bullet"),   # ← wrong, this is London Close KZ
]
```

**Fix:**
```python
schedule = [
    (dtime(7, 0),  "London KZ / Silver Bullet opens"),
    (dtime(12, 0), "NY KZ opens"),
    (dtime(14, 0), "NY AM Silver Bullet"),   # most important window
    (dtime(15, 0), "London Close KZ"),
    (dtime(18, 0), "NY PM Silver Bullet"),
]
```

---

## Bug 6 — _describe() is missing new sessions

**Current code:**
No entries for `NY_AM_SB`, `NY_PM_SB`, or `LONDON_CLOSE_KZ`.

**Fix — update the descs dict:**
```python
descs = {
    "LONDON_SB":       "🎯 London Silver Bullet — PRIME precision window (07:00–08:00 UTC)",
    "LONDON_KZ":       "⚡ London Kill Zone — institutional session active (07:00–10:00 UTC)",
    "NY_AM_SB":        "🎯 NY AM Silver Bullet — PRIME precision window (14:00–15:00 UTC)",
    "LONDON_CLOSE_KZ": "🔔 London Close KZ — position squaring, late reversals (15:00–17:00 UTC)",
    "NY_KZ":           "⚡ NY Kill Zone — institutional session active (12:00–15:00 UTC)",
    "NY_PM_SB":        "🎯 NY PM Silver Bullet — PRIME precision window (18:00–19:00 UTC)",
    "ASIA":            "🌙 Asia session — low volatility, avoid new positions",
}
```

---

## Summary Table — Correct UTC Session Map

| Session            | UTC Start | UTC End | NY Time (EST) | Type          | Weight |
|--------------------|-----------|---------|----------------|---------------|--------|
| LONDON_SB          | 07:00     | 08:00   | 02:00–03:00    | Silver Bullet | 1.2    |
| LONDON_KZ          | 07:00     | 10:00   | 02:00–05:00    | Kill Zone     | 1.0    |
| NY_KZ              | 12:00     | 15:00   | 07:00–10:00    | Kill Zone     | 1.0    |
| NY_AM_SB ⭐        | 14:00     | 15:00   | 10:00–11:00    | Silver Bullet | 1.2    |
| LONDON_CLOSE_KZ    | 15:00     | 17:00   | 10:00–12:00    | Kill Zone     | 0.8    |
| NY_PM_SB           | 18:00     | 19:00   | 14:00–15:00    | Silver Bullet | 1.2    |
| ASIA               | 00:00     | 06:00   | 19:00–01:00    | Low activity  | 0.4    |

⭐ = most important Silver Bullet window, was missing entirely from original code.

---

## DST Warning (important for live trading)

The code uses **fixed UTC times** with no DST handling. ICT kill zone times are defined in **NY local time**, which shifts by 1 hour between EST (UTC-5, Nov–Mar) and EDT (UTC-4, Mar–Nov). This means the UTC offsets above are only correct during EST (winter). During EDT (summer), subtract 1 hour from all NY times to get UTC equivalents.

Consider adding a DST-aware conversion or a note in the docstring warning users about this.
