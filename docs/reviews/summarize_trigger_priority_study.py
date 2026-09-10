"""Validate and report the separate 2026-09-07 experiment; no live access."""
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from trigger_priority_study import ACTIVE, OPTIONAL, OUT, ROOT, metrics, save


def load(window, arm):
    return json.loads((OUT / f"results/{window}__{arm}.json").read_text())


def cash(value):
    return f"{value:+,.2f}"


def number(value):
    return "n/a" if value is None else f"{value:.2f}"


def main():
    manifest = json.loads((OUT / "manifest.json").read_text())
    assert sha256((OUT/"cost_reference.json").read_bytes()).hexdigest() == manifest["cost_reference_sha256"]
    summaries = {}
    validated = []
    for path in sorted((OUT / "results").glob("*.json")):
        result = json.loads(path.read_text())
        cfg, trades = result["config"], result["trades"]
        arm = path.stem.split("__")[1].removesuffix("_FULLWATCH")
        assert cfg["tga_exits"] and cfg["enforce_news_blackout"] and cfg["enforce_session_windows"]
        assert cfg["enabled_sessions"] == ["LONDON_NY", "TOKYO_OPEN"] and not cfg["allow_whole_day"]
        assert cfg["sa_pool"] == 1000 and cfg["risk_pct"] == .03 and cfg["daily_loss_limit_usd"] == 100
        assert cfg["round_trip_cost_price"] == .35 and cfg["cooldown_enabled"]
        assert cfg["reclaim_fvg_enabled"] and cfg["m15_fvg_entry_enabled"] and cfg["htf_crt_enabled"]
        assert cfg["vplr_enabled"] == (arm == "VP_LIQUIDITY_REACTION")
        assert cfg["va_fade_enabled"] == (arm == "VALUE_AREA_FADE")
        allowed_sessions = cfg["enabled_sessions"] + (["VP_ASIA"] if cfg["vplr_enabled"] else [])
        assert all(t["session"] in allowed_sessions and t["tga_managed"] for t in trades)
        assert all(abs(t["pnl"] - (t["gross_pnl"] - 35*t["volume"])) <= .011 for t in trades)
        assert abs(sum(t["pnl"] for t in trades)-result["summary"]["net_pnl"]) < .011
        if arm in (*ACTIVE, *OPTIONAL):
            assert all(t["trigger_type"] == arm for t in trades)
        validated.append(path.stem)
    changed = [rel for rel, value in manifest["source_hashes"].items()
               if Path(rel).suffix == ".py" and sha256((ROOT/"apex_ai"/rel).read_bytes()).hexdigest() != value]
    assert not changed, f"Production Python changed since capture: {changed}"
    for arm in (*ACTIVE, *OPTIONAL, "BASELINE"):
        runs = {w: load(w, arm) for w in ("W1", "W2", "RECENT")}
        trades = runs["W1"]["trades"] + runs["W2"]["trades"]
        combined = metrics(trades)
        # Windows reset independently: a synthetic concatenated DD/balance
        # would pretend this was a continuous compounding equity curve.
        for field in ("ending_balance", "max_closed_balance_drawdown", "max_closed_balance_drawdown_pct"):
            combined.pop(field)
        summary = dict(pooled=combined, windows={w: metrics(r["trades"]) for w,r in runs.items()},
                       detections={w: r["triggers_detected"] for w,r in runs.items()},
                       gate_rejections={w: r["rejections_by_trigger"] for w,r in runs.items()},
                       folds={w: json.loads((OUT/f"summaries/{w}__{arm}.json").read_text())["folds"] for w in runs})
        summaries[arm] = summary
    ranked = sorted(ACTIVE, key=lambda a: summaries[a]["pooled"]["pnl"], reverse=True)
    selected = [a for a in ranked if summaries[a]["pooled"]["trades"] > 0][:2]
    priority = {}
    for arm in selected:
        priority[arm] = {}
        for window in ("W1", "W2"):
            baseline = load(window, "BASELINE")
            base_summary = json.loads((OUT/f"summaries/{window}__BASELINE.json").read_text())
            possible = base_summary["possible_promotions"].get(arm, 0)
            if possible == 0:
                priority[arm][window] = dict(status="ARBITRATION_EQUIVALENT", potentially_changed_scans=0,
                     pnl=baseline["summary"]["net_pnl"], delta=0., trades=len(baseline["trades"]))
            else:
                promoted = load(window, "TOP_"+arm)
                promoted_summary = json.loads((OUT/f"summaries/{window}__TOP_{arm}.json").read_text())
                priority[arm][window] = dict(status="REPLAYED", potentially_changed_scans=possible,
                     arbitration_switches=promoted_summary["arbitration_switches"],
                     pnl=promoted["summary"]["net_pnl"],
                     delta=round(promoted["summary"]["net_pnl"]-baseline["summary"]["net_pnl"], 2),
                     trades=len(promoted["trades"]), metrics=metrics(promoted["trades"]))
    vp_fast=load("SMOKE", "VP_LIQUIDITY_REACTION")
    vp_full=load("SMOKE", "VP_LIQUIDITY_REACTION_FULLWATCH")
    parity_fields=["trades", "summary", "rejections", "triggers_detected", "rejections_by_trigger", "equity_curve"]
    assert all(vp_fast[k] == vp_full[k] for k in parity_fields)
    save(OUT/"comparison.json", dict(created_utc=datetime.now(timezone.utc), ranked=ranked,
         selected_priority_arms=selected, summaries=summaries, priority=priority,
         validation=dict(results_checked=validated, production_python_changed=changed,
                         vp_watch_parity=dict(passed=True, fields=parity_fields, trades=len(vp_full["trades"])),
                         harness_sha256=sha256((ROOT/"docs/reviews/trigger_priority_study.py").read_bytes()).hexdigest())))
    lines = ["# Scalper trigger isolation and priority — fresh current-code experiment",
             "", f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}. No production code or live strategy settings changed; no live orders submitted by this study.",
             "", "## Interpretation", "",
             "SWEEP_REJECTION has the highest standalone net among the five active families: +$295.82, versus JUDAS +$292.58. That $3.24 advantage is small and reverses under the higher-cost fixed-trade overlay. JUDAS has only 18 trades, versus Sweep's 128; its apparent standalone quality does not establish a better combined priority.",
             "", "Of the tested combined orders, Sweep-first has the highest two-window net: +$418.58, versus +$259.95 for current precedence and +$320.59 for Judas-first. However, Sweep-first changes the baseline by -$7.39 / +$166.02 across W1/W2, while Judas-first changes it by -$126.12 / +$186.76. Neither promotion improves both periods. Sweep-first is the leading further-testing candidate, not a validated live promotion. Leave live precedence unchanged on this evidence.",
             "", "CRT has no executed sample. BOS has one trade; FVG has seven. Those are insufficient for a robust profitability ranking. The optional VP arm uses different hours and is not a same-session priority alternative.",
             "", "## Scope and fidelity", "",
             "Frozen current working copy, including the operator's existing uncommitted changes; 490 tests pass and all snapshot Python compiles. Attached Exness-MT5Real2 XAUUSD history, not the old demo CSVs. Source/data hashes, symbol specification and six broker-vs-offline P&L calculator checks are in `manifest.json`.",
             "", "Each arm/window starts at $1,000, 3% compounding risk, $100 daily loss limit, current cooldown and position controls. Tokyo 00:00–02:00 and London/NY 12:00–13:30 UTC only. Shared Guardian enabled. M15 triggers, M5 confirmations/decision clock, H1/H4 context and native D1/W1/MN1 CRT ranges. The current reclaim/FVG gates remain on; no legacy M5 FVG substitution.",
             "", "Net uses the existing measured-total-cost option at $0.35/oz round trip. It replaces, not duplicates, spread and commission debits. Archived spread still drives entry gates; zero spread falls back to 6 pips. The reference was measured on DEMO. Ten current live deals showed commission-per-lot values $0/$11 and zero swap, but do not establish live slippage.",
             "", "**Not tick-exact or a live-profit forecast:** the shipped simulator uses M5 fills and approximate Guardian timing; news stays enabled but the frozen calendar covers only September 10–11, so historical news blackouts cannot be reconstructed. No broker rejection, partial-fill or restart simulation. Historical dates have been used before; the experiment/configuration/feed comparison is new, not an untouched OOS claim.",
             "", "The simulator also passes `main_account_dd_pct=0.0` to the shared risk governor (`backtest_scalper.py:1149`) and does not replay other account activity or broker margin rejection. These are isolated virtual-pool results, not a reconstruction of the live account's available margin or account-wide drawdown gate.",
             "", "## Isolated trigger results", "",
             "W1 = March 1–May 31; W2 = June 1–August 31, 2026. Dollar totals sum two independent $1,000 starts, not a continuously compounded six-month account. PF is pooled winning net P&L / absolute losing net P&L. Zero trades is unmeasured, not profitable.",
             "", "| Trigger | W1 trades / net $ | W2 trades / net $ | Total net $ | Pooled PF | Net R |",
             "|---|---:|---:|---:|---:|---:|"]
    for arm in ranked:
        s=summaries[arm]; a=s["windows"]["W1"]; b=s["windows"]["W2"]; p=s["pooled"]
        lines.append(f"| {arm} | {a['trades']} / {cash(a['pnl'])} | {b['trades']} / {cash(b['pnl'])} | {cash(p['pnl'])} | {number(p['profit_factor'])} | {cash(p['sum_net_r'])} |")
    lines += ["", "The two dormant families are excluded from this production-configuration ranking and tested separately below; their feature switches remain false in production.",
              "", "## Combined priority tests", "", "Changing `--triggers` list order does not change precedence: it becomes a set. Actual active precedence is HTF_CRT_SWEEP → M15 FVG_FILL → SWEEP_REJECTION → BOS_RETEST → JUDAS. The research wrapper calls the unchanged detectors, changing only which detected candidate wins in its own process.",
              "", "| Combined configuration | W1 net $ | W2 net $ | Delta vs baseline W1 / W2 $ |",
              "|---|---:|---:|---:|"]
    base=summaries["BASELINE"]["windows"]
    lines.append(f"| Current precedence | {cash(base['W1']['pnl'])} | {cash(base['W2']['pnl'])} | — |")
    for arm, windows in priority.items():
        a,b=windows["W1"],windows["W2"]
        lines.append(f"| {arm} first | {cash(a['pnl'])} | {cash(b['pnl'])} | {cash(a['delta'])} / {cash(b['delta'])} |")
    lines += ["", "ARBITRATION_EQUIVALENT in comparison.json means the promoted family never lost a first-match decision on the baseline path. Under this deterministic replay, no state changes and its promotion is equivalent. It is not labelled as a separately executed replay.",
              "", "## Stability, drawdown and recent check", "",
              "60/20/20 splits below are chronological splits of each long window; they do not reset the arm's balance. Drawdown is realised closed-balance drawdown within the individual window, not intratrade equity drawdown.",
              "", "| Arm | W1 fold net $ | W2 fold net $ | W1 / W2 max DD % | Sep 1–6 trades / net $ |",
              "|---|---:|---:|---:|---:|"]
    for arm in (*ranked,"BASELINE"):
        s=summaries[arm]; w=s["windows"]; f=s["folds"]
        lines.append(f"| {arm} | {' / '.join(cash(x['pnl']) for x in f['W1'])} | {' / '.join(cash(x['pnl']) for x in f['W2'])} | {w['W1']['max_closed_balance_drawdown_pct']:.2f} / {w['W2']['max_closed_balance_drawdown_pct']:.2f} | {w['RECENT']['trades']} / {cash(w['RECENT']['pnl'])} |")
    lines += ["", "## What stopped the other triggers?", "", "Counts below are selected detection scans, not unique setups. Gate counts show the actual first post-detection rejection and can recur on successive M5 decisions."]
    for arm in (*ACTIVE, *OPTIONAL):
        s=summaries[arm]; counts=Counter()
        for w in ("W1","W2"):
            counts.update(s["gate_rejections"][w])
        detected=sum(sum(s["detections"][w].values()) for w in ("W1","W2"))
        lines += ["",f"- {arm}: {detected} detections, {s['pooled']['trades']} trades. Top rejections: " + "; ".join(f"{k}={v}" for k,v in counts.most_common(5)) + "."]
    lines += ["", "## Cost sensitivity (fixed-trade overlay only)", "",
              "| Trigger | $0.17/oz net $ | $0.35/oz net $ | $0.75/oz net $ |", "|---|---:|---:|---:|"]
    for arm in ranked:
        c=summaries[arm]["pooled"]["cost_overlay_fixed_trades"]
        lines.append(f"| {arm} | {cash(c['0.17'])} | {cash(c['0.35'])} | {cash(c['0.75'])} |")
    lines += ["", "These overlays hold entries and volumes fixed. They do not re-run compounding, lot floors, cooldowns or loss limits under the changed cost, and are not alternative strategy backtests.",
              "", "## Remaining dormant triggers — research-only activations", "",
              "| Optional trigger | W1 trades / net $ | W2 trades / net $ | Total net $ | PF | Sep 1–6 trades / net $ |",
              "|---|---:|---:|---:|---:|---:|"]
    for arm in OPTIONAL:
        s=summaries[arm]; a=s["windows"]["W1"]; b=s["windows"]["W2"]; c=s["windows"]["RECENT"]; p=s["pooled"]
        lines.append(f"| {arm} | {a['trades']} / {cash(a['pnl'])} | {b['trades']} / {cash(b['pnl'])} | {cash(p['pnl'])} | {number(p['profit_factor'])} | {c['trades']} / {cash(c['pnl'])} |")
    lines += ["", "VALUE_AREA_FADE enables only its implemented profile trigger under the existing two sessions. VP_LIQUIDITY_REACTION preserves the shipped ASIA_ONLY scope and trigger-specific 00:00–06:30 allowance: in this configuration VP detections are evaluated on VP-only bars outside the normal sessions (02:00–06:30). Therefore its profit is not an apples-to-apples current-session priority comparison. No risk, gate, target or detector retuning was performed.",
              "", "The VP-only arm omits unused pure CRT observations, whose plans cannot be selected by its whitelist. A paired September 4 replay with the full watcher produced the identical trade, P&L, equity, detections and rejection counts. All decision TFs and data fetches remain unchanged. This skips non-decision work, not an entry or exit filter."]
    lines += ["", "## Reproduction and evidence", "",
              "- `docs/reviews/trigger_priority_study.py`: prepare and isolated/offline replay commands.",
              "- `docs/reviews/2026-09-07-trigger-priority-protocol.md`: prespecified windows, settings and selection rule.",
              "- Evidence folder: `manifest.json`, `comparison.json`, `results/` (every trade), `summaries/` (metrics/arbitration), `data/` (frozen server candles), `snapshot/` (unchanged production source), and test logs.",
              "- Initial snapshot test attempt omitted two repository-relative historical fixtures: 490 ran, two FileNotFound errors. Copying those existing fixtures into the snapshot produced 490/OK. No tests or production code were edited to obtain a pass.",
              "", "Source locations: `apex_ai/scalper/trigger_engine.py:299` (actual selection); `:266` (whitelist is stored as a set); `apex_ai/scalper/decision_params.py:34` (TFs); `apex_ai/scalper/session_checker.py:54` (default sessions); `apex_ai/backtest_scalper.py:600` (replay); `:781` (M5 decision clock); `apex_ai/scalper/exit_manager.py:27` (bar/tick limitations).",
              "", "No automatic promotion. Sparse samples, sign changes, multiple comparisons, retrospective windows and missing historical news/live execution calibration must qualify any ranking."]
    (ROOT/"docs/reviews/2026-09-07-trigger-priority-results.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps(dict(ranked=ranked, selected=selected, priority=priority),indent=2))


if __name__ == "__main__":
    main()
