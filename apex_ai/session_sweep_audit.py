"""Read-only broker snapshot and replay for SESSION_SWEEP; writes local evidence.

Never launches the scalper/Guardian main loops. Trade and account mutation
APIs are patched to fail before reading broker data or running the simulator.
"""
import argparse
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import MetaTrader5 as mt5
import pandas as pd

from scalper import session_sweep as SS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--out", type=Path, default=Path("session-sweep-audit.json"))
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    # User's already-running terminal; caller verifies its process first.
    if not mt5.initialize():
        raise RuntimeError(mt5.last_error())
    try:
        account = mt5.account_info()
        if not account or account.login != SS.EXPECTED_LOGIN or account.server != SS.EXPECTED_SERVER:
            raise RuntimeError("Wrong MT5 account/server")
        with patch.object(mt5, "order_send", side_effect=AssertionError("orders prohibited")), \
             patch.object(mt5, "login", side_effect=AssertionError("login prohibited")), \
             patch.object(mt5, "symbol_select", return_value=True):
            import backtest_scalper as sim
            logging.disable(logging.CRITICAL)
            tick, info = mt5.symbol_info_tick("XAUUSD"), mt5.symbol_info("XAUUSD")
            rates = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_M5, 1, 150)
            if rates is None or tick is None or info is None:
                raise RuntimeError(f"market data unavailable: {mt5.last_error()}")
            m5 = pd.DataFrame(rates)
            m5["time"] = pd.to_datetime(m5.time, unit="s", utc=True)
            snapshot = dict(as_of=now.isoformat(), login=account.login, server=account.server,
                            connected=mt5.terminal_info().connected, tick_at=datetime.fromtimestamp(tick.time, timezone.utc).isoformat())
            try:
                levels = SS.load_levels(SS.DEFAULT_VAULT, now, account.login, account.server)
                snapshot["ledger_levels"] = len(levels)
                snapshot["plan"] = SS.evaluate(levels, m5, tick.bid, tick.ask, now,
                                                tick_size=info.trade_tick_size).record()
            except (ValueError, OSError) as exc:
                snapshot["ledger_error"] = str(exc)
            print(json.dumps(snapshot), flush=True)
            replay = sim.run_backtest(
                symbols=["XAUUSD"], dt_from=now.replace(hour=0, minute=0, second=0, microsecond=0)-timedelta(days=args.days),
                dt_to=now, sa_pool=1000., risk_pct=.03, max_open_positions=2,
                spread_pips=2.5, enforce_session_windows=True, enforce_news_blackout=True,
                daily_loss_limit_usd=100., enabled_triggers=["SESSION_SWEEP"],
                htf_crt_enabled=False, session_sweep_enabled=True)
            args.out.write_text(json.dumps(dict(snapshot=snapshot, replay=replay), indent=2, default=str), encoding="utf-8")
            print(json.dumps({k:v for k,v in replay.items() if k not in ("trades", "equity_curve", "crt_candidates")}, default=str), flush=True)
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
