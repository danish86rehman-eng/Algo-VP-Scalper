"""Broker-authoritative read-only activation snapshot; fails outside DEMO."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys
import MetaTrader5 as mt5

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/"docs/reviews/2026-09-03-crt-confluence-evidence"
label = sys.argv[1]
assert label in ("before", "after")
assert mt5.initialize(), mt5.last_error()
try:
    account, positions = mt5.account_info(), mt5.positions_get()
    assert account is not None and positions is not None, "Broker state unavailable"
    assert account.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO, "Reload is authorized here only on DEMO"
    paths = ["scalper_agent.py", "trade_guardian_agent.py", "scalper/decision_params.py",
             "scalper/htf_crt.py", "scalper/crt_confluence.py", "backtest_scalper.py"]
    result = dict(at_utc=datetime.now(timezone.utc).isoformat(), demo=True,
                  positions=[dict(ticket=p.ticket, symbol=p.symbol, type=p.type,
                                  volume=p.volume, sl=p.sl, tp=p.tp) for p in positions],
                  source_hashes={p: sha256((ROOT/"apex_ai"/p).read_bytes()).hexdigest() for p in paths})
    (OUT/f"demo-{label}.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
finally:
    mt5.shutdown()
