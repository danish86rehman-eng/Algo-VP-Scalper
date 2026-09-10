# 2 September 2026 XAUUSD support/retest evidence

Captured by read-only broker queries at 12:33:02 UTC. No trading actions.

- `broker_deals.json`: broker-authoritative deals from 31 August through capture; demo account mode, timestamps, fills and all cost components. Opening-position attribution includes Guardian exits.
- `XAUUSD_M15.csv`, `XAUUSD_M5.csv`, `XAUUSD_H1.csv`, `XAUUSD_H4.csv`: broker history through 2 September 12:00. Later bars are included for examination, **not** fed to earlier decisions; replay slices by bar close time.
- `decision_reconstruction.json`: exact trigger/STB reproduction at the two application decision timestamps. Not a full simulator run.
- `application_trades.json`, `application_incidents.json`: application records, retained as fallible source evidence. Incident outcome/entry/R/P&L fields are not broker truth.
- `*.excerpt.txt`: relevant Guardian and scalper text logs with source line numbers at capture.
- `manifest.json`: provenance, reported-but-unverified levels and production code hashes.
- `replay_incident.py`: offline check of the recorded predicates, costs, wick-filter behavior and stale M15 confirmation. Uses saved data; no MetaTrader connection or order submission.

Run with `py -3.14 -E docs/reviews/2026-09-02-support-retest-evidence/replay_incident.py` from the repository root. Requires the project's pandas/numpy environment. Expected: both original signals match; 45% wick filter removes the winner and retains the loser; pair net -$11.45; second signal lacks a post-exit M15 close.

Report: `../../../APEX_SUPPORT_RETEST_ENHANCEMENT_RECOMMENDATIONS.md`. Vault counterpart: `journal/reviews/2026-09-02-xauusd-support-retest-review.md` in the configured AI-Trading-Strategies vault.
