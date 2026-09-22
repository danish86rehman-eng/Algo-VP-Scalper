"""Read-only research/test runner. Never invokes a live agent main loop."""
import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent


def forbidden(*args, **kwargs):
    raise RuntimeError('Broker mutation prohibited in research')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tests', action='store_true')
    parser.add_argument('--audit-ready', action='store_true',
                        help='Record rejection counters for READY setups without changing decisions')
    parser.add_argument('--from', dest='start', default='2026-06-01T00:00:00+00:00')
    parser.add_argument('--to', dest='end', default='2026-09-12T00:00:00+00:00')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    os.chdir(out.parent)  # Imported agent log handlers cannot touch live logs.
    import MetaTrader5 as mt5
    with patch.object(mt5, 'order_send', side_effect=forbidden), \
         patch.object(mt5, 'login', side_effect=forbidden), \
         patch.object(mt5, 'symbol_select', side_effect=forbidden):
        if args.tests:
            sys.path.insert(0, str(ROOT/'tests'))
            suite = unittest.defaultTestLoader.discover(str(ROOT/'tests'))
            with out.open('w', encoding='utf-8') as stream:
                result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
            print(json.dumps(dict(tests=result.testsRun,
                failures=[str(t) for t,e in result.failures], errors=[str(t) for t,e in result.errors])))
            return
        terminal = Path(r'C:\Users\nauman.afzal\AppData\Roaming\MetaTrader 5\terminal64.exe')
        # Do not guess a terminal or fall back to credentials. Already-running
        # terminal must be verified by the caller before this attachment.
        if not terminal.exists():
            raise RuntimeError('Verified terminal path required')
        if not mt5.initialize(str(terminal)):
            raise RuntimeError(mt5.last_error())
        try:
            account = mt5.account_info()
            if not account or account.login != 172783529 or account.server != 'Exness-MT5Real2':
                raise RuntimeError('Wrong account/server')
            info = mt5.symbol_info('XAUUSD')
            if not info or not info.visible:
                raise RuntimeError('XAUUSD must already be selected')
            import backtest_scalper as sim
            from research_ready_audit import rejection_counter
            ready_records = {}
            logging.disable(logging.CRITICAL)
            # Snapshot metadata once: late terminal metadata failure must not
            # change geometry during a long replay. Profit calls fail closed.
            original_info = mt5.symbol_info
            with patch.object(sim, 'Counter',
                    rejection_counter(ready_records) if args.audit_ready else sim.Counter), \
                 patch.object(mt5, 'symbol_info', side_effect=lambda symbol:
                    info if symbol == 'XAUUSD' else original_info(symbol)), \
                 patch.object(mt5, 'symbol_select', side_effect=lambda symbol, selected:
                    True if symbol == 'XAUUSD' and selected else forbidden()):
                result = sim.run_backtest(symbols=['XAUUSD'],
                    dt_from=datetime.fromisoformat(args.start), dt_to=datetime.fromisoformat(args.end),
                    sa_pool=1000., risk_pct=.03, max_open_positions=2, spread_pips=6.,
                    enforce_session_windows=True, enforce_news_blackout=True,
                    daily_loss_limit_usd=100., tga_exits=True, round_trip_cost_price=.35,
                    reclaim_fvg_enabled=False, tracked_pullback_enabled=True)
            result['research_caveats'] = [
                'M5 replay observes one quote per bar, not 30-second live scans or tick spreads.',
                'Current news cache is not a historical blackout archive.',
                '0.35 USD/oz total costs are a sensitivity assumption, not calibrated live fills.',
                'Chronological partitions of this already-inspected interval are diagnostic, not pristine OOS.']
            result['completed_at_utc'] = datetime.now(timezone.utc).isoformat()
            if args.audit_ready:
                result['ready_rejection_audit'] = list(ready_records.values())
            out.write_text(json.dumps(result, indent=2, default=str), encoding='utf-8')
            print(json.dumps(result['summary']), flush=True)
        finally:
            mt5.shutdown()  # Disconnect this Python client, not the terminal.


if __name__ == '__main__':
    main()
