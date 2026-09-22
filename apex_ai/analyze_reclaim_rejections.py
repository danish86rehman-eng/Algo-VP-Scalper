"""Classify executed reclaim-OFF trades against the legacy gate, read-only."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from unittest.mock import patch

import MetaTrader5 as mt5
import pandas as pd

from scalper.reclaim_fvg import evaluate_reclaim


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--off',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    source=json.loads(args.off.read_text(encoding='utf-8'))
    terminal=Path(r'C:\Users\nauman.afzal\AppData\Roaming\MetaTrader 5\terminal64.exe')
    if not mt5.initialize(str(terminal)):
        raise RuntimeError(mt5.last_error())
    try:
        account=mt5.account_info()
        if not account or account.login != 172783529 or account.server != 'Exness-MT5Real2':
            raise RuntimeError('Wrong account/server')
        with patch.object(mt5,'order_send',side_effect=AssertionError('orders prohibited')), \
             patch.object(mt5,'login',side_effect=AssertionError('login prohibited')), \
             patch.object(mt5,'symbol_select',return_value=True):
            import backtest_scalper as sim
            start=datetime.fromisoformat(source['config']['from'])-timedelta(days=45)
            end=datetime.fromisoformat(source['config']['to'])
            m15=sim._fetch('XAUUSD',mt5.TIMEFRAME_M15,start,end)
            m5=sim._fetch('XAUUSD',mt5.TIMEFRAME_M5,start,end)
            if m15.empty or m5.empty:
                raise RuntimeError(f'Historical bars unavailable: {mt5.last_error()}')
            trades=sorted(source['trades'],key=lambda t:t['open_time'])
            closed=[]
            rows=[]
            reasons=Counter()
            for trade in trades:
                now=pd.Timestamp(trade['open_time'])
                closed.extend(t for t in trades if t not in closed
                              and pd.Timestamp(t['close_time']) <= now)
                prior=[pd.Timestamp(t['close_time']) for t in closed
                       if t['symbol']==trade['symbol'] and t['direction']==trade['direction']]
                result=evaluate_reclaim(trade['direction'],
                    m15[m15.time+pd.Timedelta(minutes=15)<=now].tail(150),
                    m5[m5.time+pd.Timedelta(minutes=5)<=now].tail(150),
                    trade['entry'],trade['sl'],trade['tp1'],now,max(prior) if prior else None)
                reasons[result.reason]+=1
                rows.append(dict(open_time=trade['open_time'],direction=trade['direction'],
                    trigger_type=trade['trigger_type'],pnl=trade['pnl'],r_multiple=trade['r_multiple'],
                    reclaim_allow=result.allow,reclaim_reason=result.reason))
            rejected=[r for r in rows if not r['reclaim_allow']]
            allowed=[r for r in rows if r['reclaim_allow']]
            def stats(items):
                return dict(setups=len(items),winners=sum(r['pnl']>0 for r in items),
                    net_pnl=round(sum(r['pnl'] for r in items),2),
                    expectancy_usd=round(sum(r['pnl'] for r in items)/len(items),4) if items else None,
                    expectancy_r=round(sum(r['r_multiple'] for r in items)/len(items),4) if items else None)
            output=dict(as_of_utc=datetime.now(timezone.utc).isoformat(),
                definition='Unique trades admitted and executed in reclaim-OFF, classified at their original decision time using the legacy reclaim gate and the OFF arm close barrier.',
                rejected_valid_off_arm_setups=stats(rejected),allowed_by_reclaim=stats(allowed),
                reasons=dict(reasons),rows=rows,
                caveat='These are valid under the OFF portfolio path. Off-only winners do not prove a reclaim rejection was erroneous because cooldown, balance and later opportunity paths differ.')
            args.out.parent.mkdir(parents=True,exist_ok=True)
            args.out.write_text(json.dumps(output,indent=2),encoding='utf-8')
            print(json.dumps({k:v for k,v in output.items() if k!='rows'},indent=2))
    finally:
        mt5.shutdown()


if __name__=='__main__':
    main()
