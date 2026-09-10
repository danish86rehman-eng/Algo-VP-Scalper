"""Summarize entry-session attribution in the two combined-session replays."""
import json
import sys
from pathlib import Path
from collections import defaultdict

whole_day = '--whole-day' in sys.argv[1:]
out=Path(__file__).resolve().parent / ('2026-09-08-high-confidence-whole-day' if whole_day else '2026-09-07-high-confidence-sessions')
results={w:json.loads((out/f'{w}.json').read_text()) for w in ('W1','W2')}
sessions=['TOKYO_OPEN','PRE_LONDON','LONDON_OPEN','LONDON_NY','NY_LUNCH_REV']
if whole_day:
    sessions.append('Whole_day')
rows=[]
for session in sessions:
    trades=[t for r in results.values() for t in r['trades'] if t['session']==session]
    wins=sum(t['pnl'] for t in trades if t['pnl']>0)
    losses=sum(t['pnl'] for t in trades if t['pnl']<0)
    net=[sum(t['pnl'] for t in results[w]['trades'] if t['session']==session) for w in results]
    rows.append(dict(session=session,trades=len(trades),profit=round(wins,2),loss=round(losses,2),
                     net=round(wins+losses,2),W1=round(net[0],2),W2=round(net[1],2)))
assert sum(r['trades'] for r in rows)==sum(len(r['trades']) for r in results.values())
for w,r in results.items():
    assert abs(sum(t['pnl'] for t in r['trades'])-r['summary']['net_pnl'])<.02
report=['# HIGH-only scalper: ' + ('whole-day mode (00:00-23:00 UTC)' if whole_day else 'all five named sessions'), '',
    'XAUUSD, real-server cached history; W1 March–May and W2 June–August 2026. Each window starts independently at $1,000 with 3% compounding risk and a $100 daily loss limit. Both trigger and STB confidence must be HIGH. Guardian exit simulation is enabled.', '',
    '| Entry session (UTC) | Trades | Winning net P&L $ | Losing net P&L $ | Net $ | W1 net $ | W2 net $ |',
    '|---|---:|---:|---:|---:|---:|---:|']
for r in rows:
    report.append(f"| {r['session']} | {r['trades']} | {r['profit']:.2f} | {r['loss']:.2f} | {r['net']:.2f} | {r['W1']:.2f} | {r['W2']:.2f} |")
report+=['','Window totals:']
for w,r in results.items():
    report.append(f"- {w}: {json.dumps(r['summary'])}; CONFIDENCE rejections: {r['rejections'].get('CONFIDENCE',0)}")
    assert r['config']['allow_whole_day'] == whole_day
    assert all(t['stb_confidence'] == 'HIGH' for t in r['trades'])
    peak = balance = 1000.0
    max_dd = max_dd_pct = 0.0
    for t in sorted(r['trades'], key=lambda t: t['close_time']):
        balance += t['pnl']
        peak = max(peak, balance)
        max_dd = max(max_dd, peak-balance)
        max_dd_pct = max(max_dd_pct, (peak-balance)/peak*100)
    profit = sum(t['pnl'] for t in r['trades'] if t['pnl'] > 0)
    loss = -sum(t['pnl'] for t in r['trades'] if t['pnl'] < 0)
    report.append(f"  Closed-trade balance drawdown: ${max_dd:.2f} ({max_dd_pct:.2f}%); profit factor: {profit/loss if loss else float('inf'):.3f}; sum R: {sum(t['r_multiple'] for t in r['trades']):.4f}. Drawdown excludes floating losses.")
all_trades=[t for r in results.values() for t in r['trades']]
all_profit=sum(t['pnl'] for t in all_trades if t['pnl'] > 0)
all_loss=-sum(t['pnl'] for t in all_trades if t['pnl'] < 0)
report += ['', f"Combined descriptive total across the two independent starts: {len(all_trades)} trades, "
           f"{sum(r['summary']['wins'] for r in results.values())} wins, "
           f"{sum(r['summary']['losses'] for r in results.values())} losses, "
           f"{100*sum(r['summary']['wins'] for r in results.values())/len(all_trades):.2f}% win rate, "
           f"${sum(t['gross_pnl'] for t in all_trades):.2f} gross, "
           f"-${sum(t['cost_usd'] for t in all_trades):.2f} costs, "
           f"${sum(t['pnl'] for t in all_trades):.2f} net, "
           f"PF {all_profit/all_loss:.3f}, sum R {sum(t['r_multiple'] for t in all_trades):.4f}."]
report+=['', 'All sessions were enabled together. Session attribution uses entry time, not close time; these are not independent single-session strategies. Summing the windows combines two separate $1,000 starts.', '',
    'Costs: $0.35/oz total round trip, replacing spread/commission debits; archived spread still gates entries with a 6-pip fallback. This cost estimate was measured on demo, not calibrated live slippage. Current news calendar does not reconstruct historical news blackouts. M5 fill/Guardian approximation, no broker rejection or other account activity replay. HIGH is a rule label, not a calibrated win probability. No live session settings were changed.', '',
    'Reproduction: run high_confidence_sessions.py W1 and W2, then summarize_high_confidence_sessions.py. Frozen source hashes and data provenance are in scope.json.']
if whole_day:
    report += ['', 'For reproduction append --whole-day to each command. Whole_day labels entries outside the five named windows, not an independent strategy. No entries at 23:00-24:00 UTC; existing EOD handling and all other gates remain. No order-flow filter added.', '', 'Compared with the previous all-five-session replay:']
    for w,r in results.items():
        prior=json.loads((out.parent/'2026-09-07-high-confidence-sessions'/f'{w}.json').read_text())
        report.append(f"- {w}: prior ${prior['summary']['net_pnl']:.2f}; whole-day ${r['summary']['net_pnl']:.2f}; change ${r['summary']['net_pnl']-prior['summary']['net_pnl']:.2f}.")
(out/'report.md').write_text('\n'.join(report)+'\n',encoding='utf-8')
(out/'session_summary.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
print('\n'.join(report))
