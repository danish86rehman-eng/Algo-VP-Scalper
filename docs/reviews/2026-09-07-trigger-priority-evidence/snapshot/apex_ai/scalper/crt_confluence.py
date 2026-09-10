"""L-020: causal, observable CRT confirmations; no fitted indicators.

Inputs are the already-validated completed frames from htf_crt.watch_crt.
OBSERVE never changes eligibility. Strict modes are explicit research arms.
"""
from dataclasses import replace

import pandas as pd

from scalper import decision_params as DP


def validate_mode(mode):
    if mode not in DP.CRT_CONFLUENCE_MODES:
        raise ValueError(f"Unknown CRT confluence mode: {mode}")
    return mode


def assess_confluence(plan, m15, m5, now, mode):
    validate_mode(mode)
    evidence = dict(mode=mode, baseline_ready=plan.allow, mss=False,
                    mss_level=None, swing_at=None, swing_known_at=None,
                    mss_at=None, retest=False, retest_at=None, expires_at=None)
    if plan.raid_at and plan.formed_at:
        side = 1 if plan.direction == "BULLISH" else -1
        # A pivot confirmed by the raid candle would be future information at
        # the raid open. Freeze only swings whose right-hand bars closed first.
        before = m15[m15.time+pd.Timedelta(minutes=15) <= pd.Timestamp(plan.raid_at)]
        highs = side*(before.high if side == 1 else before.low).to_numpy()
        width = DP.RECLAIM_SWING_BARS
        pivots = [k for k in range(width, len(before)-width)
                  if all(highs[k] > highs[j] for j in range(k-width, k+width+1) if j != k)]
        if pivots:
            k = pivots[-1]
            level = float(highs[k])
            evidence.update(mss_level=side*level, swing_at=before.time.iloc[k].isoformat(),
                            swing_known_at=(before.time.iloc[k+width]+pd.Timedelta(minutes=15)).isoformat())
            displacement = m15[m15.time+pd.Timedelta(minutes=15) == pd.Timestamp(plan.reclaimed_at)]
            if (not displacement.empty
                    and displacement.time.iloc[0] >= pd.Timestamp(plan.raid_at)
                    and side*float(displacement.close.iloc[0]) > level+plan.level_buffer):
                evidence.update(mss=True, mss_at=plan.reclaimed_at)
        after = m5[m5.time >= pd.Timestamp(plan.formed_at)]
        for row in after.itertuples():
            closed_at = row.time+pd.Timedelta(minutes=5)
            if (row.low <= plan.fvg_high and row.high >= plan.fvg_low
                    and plan.fvg_low <= row.close <= plan.fvg_high
                    and side*(row.close-row.open) > 0
                    and side*(row.close-plan.swept_level) >= plan.level_buffer
                    and evidence["mss_at"] is not None
                    and closed_at > pd.Timestamp(evidence["mss_at"])):
                expires = closed_at+pd.Timedelta(minutes=5)
                evidence.update(retest=(closed_at <= now < expires),
                                retest_at=closed_at.isoformat(), expires_at=expires.isoformat())
    deny = None
    if mode != "OBSERVE" and not evidence["mss"]:
        deny = "CRT_WAIT_MSS"
    elif mode == "MSS_RETEST" and not evidence["retest"]:
        deny = "CRT_WAIT_RETEST_CONFIRMATION"
    return replace(plan, confluence=evidence,
                   allow=plan.allow and deny is None,
                   reason=deny if plan.allow and deny else plan.reason)


def confirmation_current(plan, now):
    evidence = plan.confluence or {}
    if evidence.get("mode") != "MSS_RETEST":
        return True
    if now is None or not evidence.get("mss") or not evidence.get("retest"):
        return False
    return (pd.Timestamp(evidence["retest_at"]) <= pd.Timestamp(now)
            < pd.Timestamp(evidence["expires_at"]))
