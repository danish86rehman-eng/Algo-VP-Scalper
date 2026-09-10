"""L-018 bounded integration smoke, not an expectancy experiment.

Run from apex_ai: py -3.14 -E ../docs/reviews/m15_fvg_pipeline_smoke.py
Broker reads only; any order_send call raises. Fills use the existing M5
opening-quote observation model, never the eventual low/high of that candle.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"apex_ai"))
import MetaTrader5 as mt5
from backtest_scalper import run_backtest

logging.disable(logging.CRITICAL)
assert mt5.initialize(), mt5.last_error()
try:
    with patch.object(mt5, "order_send", side_effect=AssertionError("No orders in smoke")):
        result = run_backtest(
            ["XAUUSD"], datetime(2026, 9, 1, tzinfo=timezone.utc),
            datetime(2026, 9, 2, 12, tzinfo=timezone.utc),
            1000., .03, 1, 2.5, True, True,
            daily_loss_limit_usd=100., commission_per_lot=11.,
            tga_exits=True, reclaim_fvg_enabled=True, m15_fvg_entry_enabled=True)
    result["purpose"] = ("Integration only on development data; no baseline/OOS comparison. "
                         "M5 open observations approximate live scans and may miss intrabar "
                         "touches. Neither an intrabar low nor a screenshot is a fill.")
    out = ROOT/"docs/reviews/m15-fvg-pipeline-smoke.json"
    out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"completed": True, "trades": len(result["trades"]),
                      "m15_fvg_trades": sum(t.get("m15_fvg") is not None for t in result["trades"]),
                      "rejections": result["rejections"], "artifact": str(out)}))
finally:
    mt5.shutdown()
