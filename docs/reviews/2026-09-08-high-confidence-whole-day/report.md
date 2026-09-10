# HIGH-only scalper: whole-day mode (00:00-23:00 UTC)

XAUUSD, real-server cached history; W1 March–May and W2 June–August 2026. Each window starts independently at $1,000 with 3% compounding risk and a $100 daily loss limit. Both trigger and STB confidence must be HIGH. Guardian exit simulation is enabled.

| Entry session (UTC) | Trades | Winning net P&L $ | Losing net P&L $ | Net $ | W1 net $ | W2 net $ |
|---|---:|---:|---:|---:|---:|---:|
| TOKYO_OPEN | 35 | 569.01 | -542.52 | 26.49 | -143.13 | 169.62 |
| PRE_LONDON | 5 | 39.14 | -88.15 | -49.01 | 0.00 | -49.01 |
| LONDON_OPEN | 16 | 193.53 | -248.37 | -54.84 | -92.81 | 37.97 |
| LONDON_NY | 18 | 218.12 | -319.32 | -101.20 | 37.32 | -138.52 |
| NY_LUNCH_REV | 6 | 51.82 | -99.57 | -47.75 | -99.57 | 51.82 |
| Whole_day | 213 | 2736.97 | -2442.36 | 294.61 | 426.69 | -132.08 |

Window totals:
- W1: {"trades": 129, "wins": 65, "losses": 64, "win_rate_pct": 50.39, "net_pnl": 128.5, "gross_pnl": 236.65, "total_costs": 108.15, "ending_balance": 1128.5, "by_symbol": {"XAUUSD": {"trades": 129, "pnl": 128.5}}}; CONFIDENCE rejections: 1978
  Closed-trade balance drawdown: $224.65 (19.97%); profit factor: 1.080; sum R: 6.1227. Drawdown excludes floating losses.
- W2: {"trades": 164, "wins": 78, "losses": 86, "win_rate_pct": 47.56, "net_pnl": -60.2, "gross_pnl": 105.0, "total_costs": 165.2, "ending_balance": 939.8, "by_symbol": {"XAUUSD": {"trades": 164, "pnl": -60.2}}}; CONFIDENCE rejections: 2111
  Closed-trade balance drawdown: $397.09 (31.92%); profit factor: 0.972; sum R: 0.7855. Drawdown excludes floating losses.

Combined descriptive total across the two independent starts: 293 trades, 143 wins, 150 losses, 48.81% win rate, $341.65 gross, -$273.35 costs, $68.30 net, PF 1.018, sum R 6.9082.

All sessions were enabled together. Session attribution uses entry time, not close time; these are not independent single-session strategies. Summing the windows combines two separate $1,000 starts.

Costs: $0.35/oz total round trip, replacing spread/commission debits; archived spread still gates entries with a 6-pip fallback. This cost estimate was measured on demo, not calibrated live slippage. Current news calendar does not reconstruct historical news blackouts. M5 fill/Guardian approximation, no broker rejection or other account activity replay. HIGH is a rule label, not a calibrated win probability. No live session settings were changed.

Reproduction: run high_confidence_sessions.py W1 and W2, then summarize_high_confidence_sessions.py. Frozen source hashes and data provenance are in scope.json.

For reproduction append --whole-day to each command. Whole_day labels entries outside the five named windows, not an independent strategy. No entries at 23:00-24:00 UTC; existing EOD handling and all other gates remain. No order-flow filter added.

Compared with the previous all-five-session replay:
- W1: prior $-109.94; whole-day $128.50; change $238.44.
- W2: prior $-15.16; whole-day $-60.20; change $-45.04.
