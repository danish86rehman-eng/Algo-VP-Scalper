# Candidate funnel recorder — implementation report

Date: 2026-09-17  
Scope: observation-only SA telemetry; no MT5 orders, gate changes, threshold changes, whitelist changes, or priority changes.

## Implemented

- Added `apex_ai/scalper/candidate_funnel.py`.
- Candidate IDs are SHA-256 hashes of symbol, trigger, direction, originating event timestamp, reference level/setup ID, and formation timestamp. Evaluation/wall-clock time is excluded.
- Events are append-only JSONL at `apex_ai/logs/sa_candidate_funnel.jsonl` when the agent is next started.
- Repeated rescans are idempotent; WAIT can resolve to PASS/FAIL; terminal states cannot reopen; terminal candidates receive explicit NOT_APPLICABLE records for missing stages.
- Added an observer-only hook to `SATriggerEngine.step2_trigger`. Its return value is ignored by selection. Same-cycle lower-priority detections are marked PREEMPTED with the winner ID and trigger.
- Added 12 focused tests covering identity, WAIT, terminal locking, pre-emption, disabled mode, deterministic expiry/lifecycle, live/replay stage parity, and completed-bar integrity.

## Current coverage and counts

The running scalper process was not restarted, so the newly added recorder has not yet emitted live events. Current recorder count is therefore 0; this is expected and avoids changing a live process during an instrumentation-only change. Existing historical artifacts were not rewritten.

The recorder schema supports the requested stage names, statuses, source timeframes, numeric values, completed-bar timestamps, and terminal outcomes. Detailed BOS/STB, JUDAS/regime, M5 confirmation, M15 FVG lifecycle, M15 OB observation, shadow outcomes, fold reports, and bottleneck ranking require producer-specific adapters to populate their evidence fields; this patch does not infer those facts or alter those producers.

## Integrity result

Focused suite: `12/12 passed`.  `py -3.14 -E -m compileall -q scalper scalper_agent.py` passed.

## One recommendation

Restart the scalper at the next safe demo window and collect 3–5 complete sessions with this recorder enabled, then rank the actual funnel bottleneck before changing any strategy rule.
