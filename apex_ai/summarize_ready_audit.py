"""Join the completed diagnostic replay to prior unresolved setup IDs."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def summarize(prior, current, window_audit):
    wanted = {r['setup_id'] for r in window_audit['missed']
              if r['classification'] == 'UNRESOLVED_DOWNSTREAM'}
    rows = []
    for sid in sorted(wanted):
        observations = [r for r in current.get('ready_rejection_audit', [])
                        if r['setup_id'] == sid]
        observations.sort(key=lambda r: r['decision_at'])
        first = next((r for r in observations if r['rejection_counters']), None)
        rows.append(dict(setup_id=sid, first_blocker=(first['rejection_counters'][0]
                        if first else 'UNRESOLVED_NO_RECORDED_GATE'), observations=observations))
    fields = ('symbol', 'open_time', 'close_time', 'trigger_type', 'direction',
              'entry', 'sl', 'tp1', 'pnl', 'volume', 'r_multiple')
    def canonical(data):
        return sorted([tuple(str(t.get(k)) for k in fields) for t in data['trades']])
    return dict(executed_trade_fields_identical=canonical(prior) == canonical(current),
                comparison_fields=fields,
                note='Parity check covers executed trades, not all rejected candidates or input data.',
                blockers=dict(Counter(r['first_blocker'] for r in rows)), cases=rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for name in ('prior', 'current', 'window_audit', 'output'):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    paths = (args.prior, args.current, args.window_audit)
    inputs = [p.read_bytes() for p in paths]
    result = summarize(*(json.loads(b) for b in inputs))
    result['input_sha256'] = {str(p): hashlib.sha256(b).hexdigest() for p, b in zip(paths, inputs)}
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('executed_trade_fields_identical', 'blockers')}))
