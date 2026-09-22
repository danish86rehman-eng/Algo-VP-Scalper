"""Offline, observation-only regime evidence replay over preserved XAUUSD bars."""
from __future__ import annotations

import argparse
import json
import pickle
from datetime import datetime, timezone
from pathlib import Path

from backtest_scalper import run_backtest
from analyze_regime_evidence import build_report
from scalper.regime_evidence import RegimeEvidenceRecorder, load_snapshots


TIMEFRAMES = ("M5", "M15", "H1", "H4", "D1")


def load_frames(data_dir: Path) -> dict:
    frames = {}
    for timeframe in TIMEFRAMES:
        path = data_dir / f"XAUUSD_{timeframe}.pkl"
        if not path.exists():
            raise FileNotFoundError(path)
        with path.open("rb") as handle:
            frames[timeframe] = pickle.load(handle)
    return {"XAUUSD": frames}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--from", dest="date_from", required=True)
    parser.add_argument("--to", dest="date_to", required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    dt_from = datetime.fromisoformat(args.date_from).replace(tzinfo=timezone.utc)
    dt_to = datetime.fromisoformat(args.date_to).replace(tzinfo=timezone.utc)
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    if args.evidence.exists():
        args.evidence.unlink()

    recorder = RegimeEvidenceRecorder(args.evidence)
    result = run_backtest(
        symbols=["XAUUSD"], dt_from=dt_from, dt_to=dt_to,
        sa_pool=500.0, risk_pct=0.02, max_open_positions=2,
        spread_pips=2.5, enforce_session_windows=False,
        enforce_news_blackout=False, allow_whole_day=True,
        tga_exits=False, historical_data=load_frames(args.data_dir),
        evidence_recorder=recorder,
        reclaim_fvg_enabled=False, m15_fvg_entry_enabled=False,
        htf_crt_enabled=False, session_sweep_enabled=False,
        tracked_pullback_enabled=False, vplr_enabled=False,
    )
    report = build_report(load_snapshots(args.evidence))
    report["replay"] = {
        "data_dir": str(args.data_dir), "from": dt_from.isoformat(),
        "to": dt_to.isoformat(), "historical_source": "preserved_pickle",
        "backtest_summary": result["summary"],
        "backtest_rejections": result["rejections"],
    }
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(args.report),
                      "evidence": str(args.evidence),
                      "bars": report["market_bar_statistics"]["observations"],
                      "candidates": report["integrity"]["candidate_records"],
                      "trades": result["summary"]["trades"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
