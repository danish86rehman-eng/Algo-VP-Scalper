"""Optional replay diagnostics. Does not change any entry or risk decision."""
from collections import Counter
import sys


def rejection_counter(records):
    class RecordedCounter(Counter):
        def __setitem__(self, key, value):
            previous = self.get(key, 0)
            super().__setitem__(key, value)
            if value <= previous:
                return
            frame = sys._getframe(1)
            state = frame.f_locals
            if state.get('rejects') is not self:
                return
            book = state.get('pullbacks')
            now = state.get('now')
            if book is None or now is None:
                return
            for setup in book.setups.values():
                if setup.state != 'READY' or setup.symbol != state.get('symbol'):
                    continue
                # Several diagnostic counters may describe one gate. Preserve
                # their order, and never pretend the last counter was first.
                identity = (setup.setup_id, now.isoformat())
                row = records.setdefault(identity, dict(
                    setup_id=setup.setup_id, decision_at=now.isoformat(),
                    confirmed_at=setup.confirmed_at, rejection_counters=[]))
                row['rejection_counters'].append(str(key))
    return RecordedCounter
