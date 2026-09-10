"""
News Fetcher
============
Auto-populates news_events.json from Forex Factory's weekly calendar.

Source:
    https://nfs.faireconomy.media/ff_calendar_thisweek.json
    (FairEconomy public mirror of Forex Factory — stable JSON feed,
     no API key, no scraping HTML)

Behavior:
    - Filters HIGH-impact events only
    - Maps each event's currency code -> list of MT5 instruments affected
    - Writes apex_ai/news_events.json sorted by event time
    - Preserves existing file on any failure (network, parse, empty)

Usage:
    Standalone:  python -m core.news_fetcher
    Embedded:    fetch_and_save() called by scalper_agent.py
                 (startup + every 6 hours)
"""
from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("SA.News")

FF_JSON_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) APEX-SA-NewsFetcher/1.0"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "news_events.json"

# Map FF currency codes to MT5 instruments potentially traded by SA.
# A HIGH-impact event for a given currency blacks out every instrument it touches.
CURRENCY_TO_INSTRUMENTS: Dict[str, List[str]] = {
    "USD": [
        "XAUUSD", "XAGUSD", "USOIL", "BTCUSD",
        "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD",
        "AUDUSD", "NZDUSD",
        "US30", "NAS100", "SPX500",
    ],
    "EUR": ["EURUSD", "EURGBP", "EURJPY", "EURCHF", "EURAUD", "XAUUSD"],
    "GBP": ["GBPUSD", "EURGBP", "GBPJPY", "GBPCHF", "XAUUSD"],
    "JPY": ["USDJPY", "EURJPY", "GBPJPY", "AUDJPY", "CHFJPY", "XAUUSD"],
    "CHF": ["USDCHF", "EURCHF", "CHFJPY", "XAUUSD"],
    "AUD": ["AUDUSD", "EURAUD", "AUDJPY"],
    "CAD": ["USDCAD", "USOIL"],   # CAD events strongly affect oil
    "NZD": ["NZDUSD"],
    "CNY": ["XAUUSD", "BTCUSD", "USOIL"],   # China data moves gold/oil/crypto
    "ALL": [],   # Empty list = blocks all symbols (used for global events)
}


def _fetch_raw_calendar(timeout: int = 10) -> Optional[list]:
    """Download FF JSON calendar. Returns None on any failure."""
    req = urllib.request.Request(FF_JSON_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8")
        return json.loads(data)
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        logger.error(f"News fetch network error: {e}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"News fetch JSON parse error: {e}")
        return None
    except Exception as e:
        logger.error(f"News fetch unexpected error: {e}")
        return None


def _normalize_event(raw: dict) -> Optional[dict]:
    """Convert one FF event to news_events.json schema. Returns None to skip."""
    impact = str(raw.get("impact", "")).strip().upper()
    if impact != "HIGH":
        return None

    country = str(raw.get("country", "")).strip().upper()
    if not country:
        return None

    date_str = raw.get("date")
    if not date_str:
        return None

    try:
        if date_str.endswith("Z"):
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
    except Exception:
        return None

    title = str(raw.get("title", "Unknown Event")).strip()
    instruments = CURRENCY_TO_INSTRUMENTS.get(country, [])

    return {
        "time_utc": dt.isoformat().replace("+00:00", "Z"),
        "impact": "HIGH",
        "title": f"{country} {title}",
        "symbols": instruments,
    }


def fetch_and_save(output_path: Path = DEFAULT_OUTPUT) -> int:
    """
    Fetch FF calendar, filter HIGH-impact events, save to news_events.json.
    Returns event count on success; -1 on failure (existing file preserved).
    """
    raw_events = _fetch_raw_calendar()
    if raw_events is None:
        logger.warning("News fetch failed — existing news_events.json preserved")
        return -1

    if not isinstance(raw_events, list):
        logger.warning(f"Unexpected calendar format ({type(raw_events).__name__})")
        return -1

    normalized = []
    for raw in raw_events:
        if not isinstance(raw, dict):
            continue
        evt = _normalize_event(raw)
        if evt:
            normalized.append(evt)

    if not normalized:
        logger.warning("News fetch returned 0 HIGH-impact events — file preserved")
        return -1

    normalized.sort(key=lambda x: x["time_utc"])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(normalized, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info(
        f"News events updated: {len(normalized)} HIGH-impact events "
        f"written to {output_path.name}"
    )
    return len(normalized)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    count = fetch_and_save()
    if count >= 0:
        print(f"OK: Wrote {count} HIGH-impact events to {DEFAULT_OUTPUT}")
    else:
        import sys
        print("FAILED: see logs above")
        sys.exit(1)
