"""Read saved replay records; classify only blockers supported by recorded evidence."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from scalper.session_checker import SASessionChecker


def timestamp(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def audit(data):
    checker = SASessionChecker()
    rows = []
    for setup in data['config']['tracked_pullback_setups']:
        if setup['state'] != 'MISSED_ENTRY':
            continue
        confirmed = timestamp(setup['confirmed_at'])
        session = checker.get_state(confirmed)
        overlaps = [t for t in data['trades'] if t['symbol'] == setup['symbol']
                    and timestamp(t['open_time']) <= confirmed < timestamp(t['close_time'])]
        reason = ('OUTSIDE_SESSION' if not session.in_window else
                  'POSITION_OPEN' if overlaps else 'UNRESOLVED_DOWNSTREAM')
        rows.append(dict(setup_id=setup['setup_id'], confirmed_at=confirmed.isoformat(),
                         classification=reason, overlapping_positions=len(overlaps)))
    groups = {}
    for name, tracked in [('tracked_entries', True), ('native_entries', False)]:
        trades = [t for t in data['trades'] if bool(t.get('pullback')) == tracked]
        groups[name] = dict(trades=len(trades), net_usd=round(sum(t['pnl'] for t in trades), 2),
                           sum_r=round(sum(t['r_multiple'] for t in trades), 4))
    return dict(counts=dict(Counter(r['classification'] for r in rows)), missed=rows,
                performance=groups, limitations=[
                    'Uses current session checker; original runtime session-code hash was not recorded.',
                    'In-session unresolved records do not establish a timing defect or an allowed entry.',
                    'Historical per-candidate downstream rejection records are absent.',
                    'Grouping executed trades is descriptive and does not simulate an independent strategy.'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    raw = args.input.read_bytes()
    result = audit(json.loads(raw))
    result['source_sha256'] = hashlib.sha256(raw).hexdigest()
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(dict(counts=result['counts'], performance=result['performance'])))
