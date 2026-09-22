"""Descriptive three-arm report; never interprets off-only winners as mistakes."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path


def metrics(trades, capital=1000.):
    ordered = sorted(trades, key=lambda t:t['close_time'])
    pnl = [float(t['pnl']) for t in ordered]
    equity = peak = capital
    dd = dd_pct = 0.
    streak = longest = clusters = 0
    for value in pnl:
        equity += value
        peak = max(peak,equity)
        dd = max(dd,peak-equity)
        dd_pct = max(dd_pct,100*(peak-equity)/peak) if peak > 0 else dd_pct
        streak = streak+1 if value < 0 else 0
        clusters += streak == 2
        longest = max(longest,streak)
    gains = sum(p for p in pnl if p > 0)
    losses = -sum(p for p in pnl if p < 0)
    return dict(trades=len(pnl), buys=sum(t['direction']=='BULLISH' for t in trades),
                sells=sum(t['direction']=='BEARISH' for t in trades),
                net_pnl=round(sum(pnl),2), expectancy_usd=round(sum(pnl)/len(pnl),4) if pnl else None,
                expectancy_r=round(sum(t['r_multiple'] for t in trades)/len(trades),4) if trades else None,
                profit_factor=round(gains/losses,4) if losses else None,
                closed_equity_drawdown_usd=round(dd,2), closed_equity_drawdown_pct=round(dd_pct,2),
                longest_loss_streak=longest, loss_clusters_at_least_two=clusters)


def summarize(data):
    cfg = data['config']
    start,end = datetime.fromisoformat(cfg['from']),datetime.fromisoformat(cfg['to'])
    cuts = [start,start+(end-start)*.6,start+(end-start)*.8,end]
    result = dict(overall=metrics(data['trades'],cfg['sa_pool']), folds={})
    for i,name in enumerate(('training','validation','test')):
        trades = [t for t in data['trades'] if cuts[i] <= datetime.fromisoformat(t['open_time']) < cuts[i+1]]
        by_group = {}
        for t in trades:
            key = t['trigger_type']+':'+t['direction']
            by_group.setdefault(key,[]).append(t)
        setup_rows = [s for s in cfg.get('tracked_pullback_setups',[]) if cuts[i] <= datetime.fromisoformat(s['break_at']) < cuts[i+1]]
        result['folds'][name] = dict(start=cuts[i].isoformat(),end=cuts[i+1].isoformat(),
            **metrics(trades,cfg['sa_pool']), by_trigger_direction={k:metrics(v) for k,v in by_group.items()},
            tracked_setups=len([s for s in setup_rows if s['zone_kind']]),
            tracked_states=dict(Counter(s['state'] for s in setup_rows)),
            tracked_terminal_funnel=dict(Counter(s['reason'] for s in setup_rows)),
            rejection_funnel_note='Legacy files contain aggregate rejection counts only, not per-fold candidate records.')
    result['reclaim_rejection_observations'] = sum(v for k,v in data['rejections'].items() if k.startswith('RECLAIM_'))
    result['rejections'] = data['rejections']
    result['tracked_funnel'] = cfg.get('tracked_pullback_funnel',{})
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--on', type=Path, required=True)
    parser.add_argument('--off', type=Path, required=True)
    parser.add_argument('--tracked', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    arms = {'reclaim_on':args.on,'reclaim_off':args.off}
    if args.tracked:
        arms['tracked_pullback'] = args.tracked
    output = dict(as_of_utc=datetime.now(timezone.utc).isoformat(), arms={})
    for name,path in arms.items():
        output['arms'][name] = summarize(json.loads(path.read_text(encoding='utf-8')))
    output['limitations'] = [
        'No activation recommendation. M5 OHLC does not measure 30-second quote retries or intra-bar spread spikes.',
        'Folds partition a continuous compounded portfolio; they are not independently initialized validation runs.',
        'Drawdown is realized closed-equity only. Fold drawdown rebases to initial capital and excludes intratrade drawdown.',
        'Legacy rejection observations are not unique valid setups; raw rejected candidate IDs were not retained.',
        'Off-only winners do not establish erroneous rejections: the portfolio and cooldown path changes.',
        'Current news cache and assumed costs limit live realism; these are diagnostic partitions of already-inspected dates.']
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(output,indent=2),encoding='utf-8')
    print(json.dumps({k:v['overall'] for k,v in output['arms'].items()},indent=2))


if __name__ == '__main__':
    main()
