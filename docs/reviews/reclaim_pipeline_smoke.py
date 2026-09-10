"""Bounded L-017 integration smoke; no order submission and no alpha claim.

Run from apex_ai with py -3.14 -E ../docs/reviews/reclaim_pipeline_smoke.py.
Reads broker bars/contract metadata; forbids all order_send calls.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apex_ai"))
import MetaTrader5 as mt5
from backtest_scalper import run_backtest

logging.disable(logging.CRITICAL)
assert mt5.initialize(), mt5.last_error()
try:
    with patch.object(mt5, "order_send", side_effect=AssertionError("smoke must never submit orders")):
        result = run_backtest(
            symbols=["XAUUSD"],
            dt_from=datetime(2026, 9, 1, 0, tzinfo=timezone.utc),
            dt_to=datetime(2026, 9, 2, 12, tzinfo=timezone.utc),
            sa_pool=1000., risk_pct=.03, max_open_positions=1,
            spread_pips=2.5, enforce_session_windows=True,
            enforce_news_blackout=True, daily_loss_limit_usd=100.,
            commission_per_lot=11., tga_exits=True, reclaim_fvg_enabled=True)
    result["purpose"] = (
        "Single integration smoke on development data, not an efficacy experiment. "
        "Uses historical broker OHLC with bar-level fills and existing news cache; "
        "not tick-perfect reproduction of the account. No baseline comparison or promotion.")
    destination = ROOT / "docs/reviews/reclaim-pipeline-smoke.json"
    destination.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"artifact": str(destination), "keys": list(result),
                      "summary": result.get("summary"), "rejections": result.get("rejections")}, default=str))
finally:
    mt5.shutdown()
