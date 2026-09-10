"""Offline current-code HIGH-only replay of all five named market sessions."""
import sys
import os
import json
import shutil
import logging
from pathlib import Path
from hashlib import sha256
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from contextlib import ExitStack
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'docs/reviews/2026-09-07-high-confidence-sessions'
whole_day = '--whole-day' in sys.argv[2:]
OUT = ROOT / 'docs/reviews/2026-09-08-high-confidence-whole-day' if whole_day else BASE
OLD = ROOT / 'docs/reviews/2026-09-07-trigger-priority-evidence'
OUT.mkdir(exist_ok=True)
snapshot = BASE / 'snapshot'
if not snapshot.exists():
    shutil.copytree(ROOT / 'apex_ai', snapshot,
                    ignore=shutil.ignore_patterns('.env', 'logs', 'data', '__pycache__', '*.pyc', '.venv'))
sys.path.insert(0, str(snapshot))
os.chdir(snapshot)
import pandas as pd
import MetaTrader5 as mt5
import backtest_scalper as sim
from scalper import decision_params as DP
assert DP.CONFIG_ERA == '2026-09-07-high-confidence-only'
logging.disable(logging.CRITICAL)
manifest = json.loads((OLD / 'manifest.json').read_text())
info = SimpleNamespace(**manifest['symbol_info'])
frames = {}
for name, code in {'M5': DP.TF_CONFIRM, 'M15': DP.TF_TRIGGER,
                   'H1': DP.TF_HTF, 'H4': DP.TF_H4, **DP.CRT_TIMEFRAMES}.items():
    path = OLD / f'data/XAUUSD_{name}.pkl'
    assert sha256(path.read_bytes()).hexdigest() == manifest['data'][name]['sha256']
    frames[code] = pd.read_pickle(path)
sessions = ['TOKYO_OPEN', 'PRE_LONDON', 'LONDON_OPEN', 'LONDON_NY', 'NY_LUNCH_REV']
def fetch(symbol, code, begin, end):
    f = frames[code]
    return f[(f.time >= begin) & (f.time <= end)].copy()
def profit(side, symbol, volume, entry, close):
    return round((close-entry)*volume*info.trade_contract_size*(1 if side == mt5.ORDER_TYPE_BUY else -1), 2)
def save(path, obj):
    path.write_text(json.dumps(obj, indent=2, default=str), encoding='utf-8')
save(OUT / 'scope.json', dict(era=DP.CONFIG_ERA, sessions=sessions, allow_whole_day=whole_day,
     entry_hours_utc='00:00-23:00' if whole_day else 'five named windows', pool=1000, risk=.03,
     cost_per_oz=.35, spread_fallback_pips=6, data_manifest=str(OLD / 'manifest.json'),
     source_hashes={str(p.relative_to(snapshot)):sha256(p.read_bytes()).hexdigest() for p in snapshot.rglob('*.py')}))
window = sys.argv[1]
begin, end = {'W1': ('2026-03-01','2026-06-01'), 'W2': ('2026-06-01','2026-09-01')}[window]
begin, end = [datetime.fromisoformat(x).replace(tzinfo=timezone.utc) for x in (begin,end)]
with ExitStack() as stack:
    for name in ('initialize','login','order_send','copy_rates_range','copy_rates_from_pos','copy_ticks_range'):
        stack.enter_context(patch.object(mt5,name,side_effect=AssertionError('Offline replay only')))
    stack.enter_context(patch.object(mt5,'symbol_info',return_value=info))
    stack.enter_context(patch.object(mt5,'symbol_select',return_value=True))
    stack.enter_context(patch.object(mt5,'order_calc_profit',side_effect=profit))
    stack.enter_context(patch.object(sim,'_fetch',side_effect=fetch))
    print('START', window, flush=True)
    result=sim.run_backtest(['XAUUSD'],begin,end-timedelta(microseconds=1),1000.,.03,
        DP.MAX_OPEN_POSITIONS,6.,True,True,daily_loss_limit_usd=100.,commission_per_lot=11.,
        enabled_sessions=sessions,allow_whole_day=whole_day,tga_exits=True,
        round_trip_cost_price=.35,watch_inactive_crt=False)
save(OUT / f'{window}.json',result)
print('DONE',window,json.dumps(result['summary']),flush=True)
