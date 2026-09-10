from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class NewsEvent:
    time_utc: datetime
    impact: str
    title: str
    symbols: List[str]


class NewsGuard:
    """
    Local-file news blackout guard.
    Reads events from JSON and blocks trading in a configurable window
    before/after high-impact releases.
    """

    def __init__(
        self,
        events_file: str = "news_events.json",
        blackout_before_min: int = 15,
        blackout_after_min: int = 15,
        impact_levels: Optional[List[str]] = None,
    ):
        self.events_file = Path(events_file)
        self.blackout_before = timedelta(minutes=blackout_before_min)
        self.blackout_after = timedelta(minutes=blackout_after_min)
        self.impact_levels = {x.upper() for x in (impact_levels or ["HIGH"])}

    def _load_events(self) -> List[NewsEvent]:
        if not self.events_file.exists():
            return []
        try:
            raw = json.loads(self.events_file.read_text(encoding="utf-8"))
        except Exception:
            return []
        events: List[NewsEvent] = []
        for item in raw:
            try:
                ev_time = datetime.fromisoformat(item["time_utc"].replace("Z", "+00:00")).astimezone(timezone.utc)
                impact = str(item.get("impact", "HIGH")).upper()
                title = str(item.get("title", ""))
                symbols = [str(s).upper() for s in item.get("symbols", [])]
                events.append(NewsEvent(time_utc=ev_time, impact=impact, title=title, symbols=symbols))
            except Exception:
                continue
        return events

    def active_blackout(self, now_utc: datetime, symbol: Optional[str] = None) -> Optional[Dict]:
        symbol_u = (symbol or "").upper()
        for ev in self._load_events():
            if ev.impact not in self.impact_levels:
                continue
            if ev.symbols and symbol_u and symbol_u not in ev.symbols:
                continue
            start = ev.time_utc - self.blackout_before
            end = ev.time_utc + self.blackout_after
            if start <= now_utc <= end:
                return {
                    "event_time_utc": ev.time_utc.isoformat(),
                    "impact": ev.impact,
                    "title": ev.title,
                    "window_start_utc": start.isoformat(),
                    "window_end_utc": end.isoformat(),
                }
        return None

