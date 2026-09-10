"""
APEX AI — Session Engine
Classifies current UTC time into trading sessions and applies session weights.
Outside kill zones → reduce activity.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone, time as dtime
from typing import Optional


@dataclass
class SessionState:
    active_session: str         # 'LONDON_KZ' | 'LONDON_SB' | 'NY_KZ' | 'NY_SB' | 'OFF'
    is_kill_zone: bool
    is_silver_bullet: bool
    session_weight: float       # Multiplier: 0.3 – 1.2
    utc_time: datetime
    description: str = ""

    def is_active(self) -> bool:
        return self.session_weight >= 0.8

    def is_prime(self) -> bool:
        """Silver Bullet = highest precision window."""
        return self.is_silver_bullet


class SessionEngine:
    """
    APEX Session Engine — UTC-based session classifier.
    Aligned with ICT trading sessions (fixed UTC offsets).
    
    Session Map:
    - LONDON_SB:       07:00–08:00 UTC
    - LONDON_KZ:       07:00–10:00 UTC
    - NY_KZ:           12:00–15:00 UTC
    - NY_AM_SB:        14:00–15:00 UTC
    - LONDON_CLOSE_KZ: 15:00–17:00 UTC
    - NY_PM_SB:        18:00–19:00 UTC
    - ASIA:            00:00–06:00 UTC
    """

    # Fix #4: Replace SESSIONS dict with ordered SESSION_PRIORITY list of tuples
    # Each tuple: (name, start, end, weight, is_kill_zone, is_silver_bullet)
    SESSION_PRIORITY = [
        ("LONDON_SB",        dtime(7, 0),  dtime(8, 0),   1.2, True,  True),
        ("LONDON_KZ",        dtime(7, 0),  dtime(10, 0),  1.0, True,  False),
        ("NY_AM_SB",         dtime(14, 0), dtime(15, 0),  1.2, True,  True),
        ("LONDON_CLOSE_KZ",  dtime(15, 0), dtime(17, 0),  0.8, True,  False),
        ("NY_KZ",            dtime(12, 0), dtime(15, 0),  1.0, True,  False),
        ("NY_PM_SB",         dtime(18, 0), dtime(19, 0),  1.2, True,  True),
        ("ASIA",             dtime(0, 0),  dtime(6, 0),   0.4, False, False),
    ]

    def get_state(self, utc_now: Optional[datetime] = None) -> SessionState:
        if utc_now is None:
            utc_now = datetime.now(timezone.utc)
        t = utc_now.time()

        # Check in priority order (more specific first)
        for (session, start, end, weight, is_kz, is_sb) in self.SESSION_PRIORITY:
            if start <= t < end:
                return SessionState(
                    active_session=session,
                    is_kill_zone=is_kz,
                    is_silver_bullet=is_sb,
                    session_weight=weight,
                    utc_time=utc_now,
                    description=self._describe(session, t))

        return SessionState(
            active_session="OFF", is_kill_zone=False,
            is_silver_bullet=False, session_weight=0.3,
            utc_time=utc_now,
            description=f"Off-session ({t.strftime('%H:%M')} UTC) — reduce activity")

    def next_kill_zone(self, utc_now: Optional[datetime] = None) -> str:
        if utc_now is None:
            utc_now = datetime.now(timezone.utc)
        t = utc_now.time()
        
        # Fix #5: Updated schedule with correct times and all sessions
        schedule = [
            (dtime(7, 0),  "London KZ / Silver Bullet opens"),
            (dtime(12, 0), "NY KZ opens"),
            (dtime(14, 0), "NY AM Silver Bullet"),
            (dtime(15, 0), "London Close KZ"),
            (dtime(18, 0), "NY PM Silver Bullet"),
        ]
        for start, label in schedule:
            if t < start:
                delta = datetime.combine(utc_now.date(), start) - datetime.combine(utc_now.date(), t)
                h, m = divmod(int(delta.total_seconds() / 60), 60)
                return f"{label} in {h}h {m}m"
        return "London KZ tomorrow at 07:00 UTC"

    def _describe(self, session: str, t: dtime) -> str:
        # Fix #6: Updated descs to include all new session names and UTC times
        descs = {
            "LONDON_SB":       "🎯 London Silver Bullet — PRIME precision window (07:00–08:00 UTC)",
            "LONDON_KZ":       "⚡ London Kill Zone — institutional session active (07:00–10:00 UTC)",
            "NY_AM_SB":        "🎯 NY AM Silver Bullet — PRIME precision window (14:00–15:00 UTC)",
            "LONDON_CLOSE_KZ": "🔔 London Close KZ — position squaring, late reversals (15:00–17:00 UTC)",
            "NY_KZ":           "⚡ NY Kill Zone — institutional session active (12:00–15:00 UTC)",
            "NY_PM_SB":        "🎯 NY PM Silver Bullet — PRIME precision window (18:00–19:00 UTC)",
            "ASIA":            "🌙 Asia session — low volatility, avoid new positions (00:00–06:00 UTC)",
        }
        return descs.get(session, "Off session")
