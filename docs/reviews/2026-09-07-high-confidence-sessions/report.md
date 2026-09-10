# HIGH-only scalper: all five named sessions

XAUUSD, real-server cached history; W1 March–May and W2 June–August 2026. Each window starts independently at $1,000 with 3% compounding risk and a $100 daily loss limit. Both trigger and STB confidence must be HIGH. Guardian exit simulation is enabled.

| Entry session (UTC) | Trades | Winning net P&L $ | Losing net P&L $ | Net $ | W1 net $ | W2 net $ |
|---|---:|---:|---:|---:|---:|---:|
| TOKYO_OPEN | 35 | 608.41 | -510.06 | 98.35 | -37.87 | 136.22 |
| PRE_LONDON | 11 | 99.51 | -150.59 | -51.08 | -25.79 | -25.29 |
| LONDON_OPEN | 22 | 173.43 | -354.76 | -181.33 | -122.99 | -58.34 |
| LONDON_NY | 19 | 195.53 | -347.02 | -151.49 | 82.06 | -233.55 |
| NY_LUNCH_REV | 13 | 268.75 | -108.30 | 160.45 | -5.35 | 165.80 |

Window totals:
- W1: {"trades": 39, "wins": 15, "losses": 24, "win_rate_pct": 38.46, "net_pnl": -109.94, "gross_pnl": -85.44, "total_costs": 24.5, "ending_balance": 890.06, "by_symbol": {"XAUUSD": {"trades": 39, "pnl": -109.94}}}; CONFIDENCE rejections: 542
- W2: {"trades": 61, "wins": 28, "losses": 33, "win_rate_pct": 45.9, "net_pnl": -15.16, "gross_pnl": 37.69, "total_costs": 52.85, "ending_balance": 984.84, "by_symbol": {"XAUUSD": {"trades": 61, "pnl": -15.16}}}; CONFIDENCE rejections: 492

All sessions were enabled together. Session attribution uses entry time, not close time; these are not independent single-session strategies. Summing the windows combines two separate $1,000 starts.

Costs: $0.35/oz total round trip, replacing spread/commission debits; archived spread still gates entries with a 6-pip fallback. This cost estimate was measured on demo, not calibrated live slippage. Current news calendar does not reconstruct historical news blackouts. M5 fill/Guardian approximation, no broker rejection or other account activity replay. HIGH is a rule label, not a calibrated win probability. No live session settings were changed.

Reproduction: run high_confidence_sessions.py W1 and W2, then summarize_high_confidence_sessions.py. Frozen source hashes and data provenance are in scope.json.
