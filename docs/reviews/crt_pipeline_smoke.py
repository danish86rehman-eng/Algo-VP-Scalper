"""Read-only CRT development replay; no expectancy claim and no orders."""
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"apex_ai"))
import MetaTrader5 as mt5
import pandas as pd
import backtest_scalper as sim
from scalper import decision_params as DP
from scalper.htf_crt import watch_crt

logging.disable(logging.CRITICAL)
OUT = ROOT/"docs/reviews/2026-09-03-htf-crt-evidence"
OUT.mkdir(exist_ok=True)
assert mt5.initialize(), mt5.last_error()
try:
    captured = datetime.now(timezone.utc)
    acct = mt5.account_info()
    positions = mt5.positions_get()
    assert acct is not None and positions is not None
    (OUT/"broker-state.json").write_text(json.dumps({"captured_utc": captured.isoformat(),
        "demo": acct.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO,
        "open_positions": len(positions)}, indent=2))
    start, end = datetime(2026, 9, 2, 11, tzinfo=timezone.utc), datetime(2026, 9, 2, 15, tzinfo=timezone.utc)
    frames = {}
    mapping = {"M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
               "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4, **DP.CRT_TIMEFRAMES}
    for tf, code in mapping.items():
        saved = OUT/f"XAUUSD_{tf}.csv"
        frame = (pd.read_csv(saved, parse_dates=["time"]) if saved.exists() else
                 sim._fetch("XAUUSD", code, start-timedelta(days=400 if tf in DP.CRT_TIMEFRAMES else 40), end))
        assert not frame.empty, tf
        if not saved.exists():
            frame.to_csv(saved, index=False)
        frames[tf] = frame
    observations = []
    for bar in frames["M5"].itertuples():
        if not start <= bar.time <= end:
            continue
        plans = watch_crt(frames["M15"][frames["M15"].time+pd.Timedelta(minutes=15) <= bar.time].tail(DP.TRIGGER_BARS),
                          frames["M5"][frames["M5"].time < bar.time].tail(DP.CONFIRM_BARS),
                          frames, bar.open, bar.open, bar.time)
        for p in plans:
            if p.raid_at:
                observations.append({"decision_at": bar.time.isoformat(), **p.record()})
    (OUT/"crt-watch-replay.json").write_text(json.dumps(observations, indent=2))
    def fetch_cached(symbol, tf, dt_from, dt_to):
        name = next(k for k, v in mapping.items() if v == tf)
        f = frames[name]
        return f[(f.time >= dt_from) & (f.time <= dt_to)].copy()
    with patch.object(mt5, "order_send", side_effect=AssertionError("No orders in replay")), \
         patch.object(sim, "_fetch", side_effect=fetch_cached):
        result = sim.run_backtest(["XAUUSD"], start, end, 1000., .03, 1, 2.5, True, True,
            daily_loss_limit_usd=100., commission_per_lot=11., tga_exits=True,
            reclaim_fvg_enabled=True, m15_fvg_entry_enabled=True, htf_crt_enabled=True)
    result["purpose"] = "Development integration only. M5 opening bid observations approximate executable quotes; no tick fills or out-of-sample evidence."
    (OUT/"pipeline-replay.json").write_text(json.dumps(result, indent=2, default=str))
    print(json.dumps({"trades": len(result["trades"]),
        "crt_trades": sum(t.get("htf_crt") is not None for t in result["trades"]),
        "watch_states": sorted({p["reason"] for p in observations}), "rejections": result["rejections"]}))
finally:
    mt5.shutdown()
