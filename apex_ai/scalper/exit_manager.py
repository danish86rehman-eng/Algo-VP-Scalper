"""
Simulated Trade Guardian — replaying live exit management on closed bars.
=========================================================================

Why this exists (ledger L-003)
------------------------------
The simulator modelled a position as "static SL, static TP, walk forward until
one is touched". The live account does not work that way: `trade_guardian_agent`
polls every 2 seconds and trails the stop through three stages, arms an early
close at +1R, kills no-progress trades at 60 minutes, and banks half a position
into an extended target. Every exit-side number this repository has ever
produced therefore described a strategy nobody runs, and L-003 recorded that
entry-side remedies were measurable while exit-side ones were not.

`docs/RESEARCH_NOTES.md` §13.10 has the tell: 36 of 115 reconciled live losses
closed WITHOUT price ever reaching the original stop, median MAE 0.54R. A
breakeven stop cannot produce that. The no-progress kill — 60 minutes open with
peak <= 0.3R, close at market — produces exactly that, and it was the one live
exit path with no simulator counterpart at all.

What this module does NOT do
----------------------------
It does not re-implement the Guardian's decisions. `SLEngine`,
`EarlyCloseEngine` and `TPEngine` are imported from `scalper.tga_engines` and
executed as-is, which is the same discipline `sa_consultant.analyse()` is held
to (CLAUDE.md §13.4). This module supplies only what the live process gets from
MT5: a price, a candle frame, and the clock.

The approximation, stated plainly
---------------------------------
The Guardian sees ticks; the simulator sees M5 bars. Three consequences, all
deliberate and all conservative:

1. **Peak/adverse R is updated from the bar's extremes**, because a tick would
   have reached them — but the stop that results only becomes effective on the
   NEXT bar. Trailing from an extreme within the same bar that made it would be
   look-ahead.
2. **A stop and a target inside the same bar resolve to the stop.** This is the
   pre-existing convention in `_schedule_exit` and it is kept, so the two paths
   differ only in the management, not in the tie-break.
3. **Market closes (early close, no-progress, the partial leg of a TP
   extension) fill at the bar close**, which is where the Guardian's next poll
   would have seen price.

None of these can see the future: the frame handed to the engines ends at the
bar being evaluated, and the "forming" row is synthesised from that bar's own
close rather than taken from the next bar. `tests/test_tga_exit_sim.py` asserts
this by appending violent future bars and requiring the decision to be
unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import pandas as pd

from scalper.tga_engines import (
    EarlyCloseEngine,
    SLEngine,
    StructureFeed,
    TGAConfig,
    TGAPositionRecord,
    TPEngine,
    compute_atr,
)

#: Bars ending at the entry that the Guardian's `get_historical_atr` reads,
#: and the period it smooths over. Both mirror `TGADataFeed.get_historical_atr`.
ATR_LOOKBACK_BARS = 20
ATR_PERIOD = 14


@dataclass
class ExitLeg:
    """One fill that closed part or all of the position."""
    volume: float
    price: float
    reason: str
    time: datetime


@dataclass
class ExitOutcome:
    """Everything the simulator needs to book the trade, plus telemetry.

    `legs` is the account of record for P&L: a TP extension closes half the
    position early, so a single (price, volume) pair cannot express the result.
    `exit_price` / `exit_reason` describe the FINAL leg, which is what the
    existing trade record shows.
    """
    legs: List[ExitLeg] = field(default_factory=list)
    exit_price: float = 0.0
    result: str = "TIMEOUT"
    exit_reason: str = "TIMEOUT"
    close_time: Optional[datetime] = None
    # -- telemetry (observation only, per §13.10) --------------------------
    peak_r: float = 0.0
    adverse_r: float = 0.0
    sl_stage: int = 0
    early_closed: bool = False
    no_progress_closed: bool = False
    tp_extended: bool = False
    final_sl: float = 0.0
    final_tp: float = 0.0


def entry_atr_at(df_m5: pd.DataFrame, entry_idx: int) -> float:
    """ATR the Guardian would have stamped on the position at registration.

    Mirrors `TGADataFeed.get_historical_atr`: 20 M5 bars ending at the entry,
    smoothed over 14. Returns the same 0.0001 floor the live path uses, so a
    thin warmup cannot make a trail distance zero and pin the stop to price.
    """
    lo = max(0, entry_idx + 1 - ATR_LOOKBACK_BARS)
    window = df_m5.iloc[lo:entry_idx + 1]
    a = compute_atr(window, ATR_PERIOD)
    if a is None or a != a or a <= 0:      # NaN or non-positive
        return 0.0001
    return float(a)


def _causal_frame(df_m5: pd.DataFrame, j: int) -> pd.DataFrame:
    """The frame the Guardian holds at the instant bar `j` closes.

    Its engines all treat `iloc[-1]` as the in-flight candle and read from
    `iloc[-2]` backwards. At the moment bar `j` closes, the in-flight candle is
    bar `j+1`, which has only just opened — so it carries no information beyond
    bar `j`'s close. Synthesising that row from bar `j`'s own close reproduces
    the Guardian's view exactly while making look-ahead structurally impossible:
    no value from bar `j+1` is ever read, because bar `j+1` is never present.
    """
    hist = df_m5.iloc[:j + 1]
    last = hist.iloc[-1]
    c = float(last["close"])
    forming = {col: last[col] for col in hist.columns}
    forming.update({"open": c, "high": c, "low": c, "close": c})
    return pd.concat(
        [hist, pd.DataFrame([forming], columns=hist.columns)],
        ignore_index=True)


class SimulatedGuardian:
    """Drives the real Guardian engines over a closed-bar frame.

    One instance per simulated position. `run()` returns the exit the live
    Guardian would have produced, or as close to it as M5 resolution allows.
    """

    def __init__(self,
                 config: Optional[TGAConfig] = None,
                 volume_min: float = 0.01,
                 volume_step: float = 0.01,
                 point: float = 0.001):
        self.cfg = config or TGAConfig()
        self.sl_engine = SLEngine(self.cfg)
        self.early_engine = EarlyCloseEngine(self.cfg)
        self.tp_engine = TPEngine(self.cfg)
        self.feed = StructureFeed()
        self.volume_min = volume_min
        self.volume_step = volume_step
        #: `_process_position` ignores an SL move smaller than this, so a
        #: sub-tick recomputation does not count as a stage transition.
        self.min_sl_move = point * 10.0

    # -- helpers -----------------------------------------------------------
    def _round_volume(self, v: float) -> float:
        if self.volume_step <= 0:
            return round(v, 2)
        return round(round(v / self.volume_step) * self.volume_step, 2)

    def _splittable(self, volume: float) -> Optional[Tuple[float, float]]:
        """(banked, remainder) if the Guardian's partial close could fill.

        A 0.01-lot position cannot be halved, which is what sets
        `tp_extend_blocked` live. Returning None here reproduces that rather
        than inventing a fill the broker would reject.
        """
        banked = self._round_volume(volume * self.cfg.tp_extension_partial_pct)
        remainder = self._round_volume(volume - banked)
        if banked < self.volume_min or remainder < self.volume_min:
            return None
        return banked, remainder

    # -- main loop ---------------------------------------------------------
    def run(self,
            df_m5: pd.DataFrame,
            entry_idx: int,
            direction: str,
            entry_price: float,
            stop_loss: float,
            tp1: float,
            entry_time: datetime,
            volume: float,
            max_idx: int,
            eod: datetime,
            df_m15: Optional[pd.DataFrame] = None) -> ExitOutcome:
        """Walk bars [entry_idx, max_idx] and return the managed exit.

        `direction` is the trigger's BULLISH/BEARISH; the Guardian's records
        speak BUY/SELL, so it is translated once, here.
        """
        is_long = direction == "BULLISH"
        rec = TGAPositionRecord(
            ticket=0,
            symbol="",
            origin_agent="SA",
            direction="BUY" if is_long else "SELL",
            entry_price=entry_price,
            original_sl=stop_loss,
            original_tp=tp1,
            volume=volume,
            entry_atr=entry_atr_at(df_m5, entry_idx),
            entry_time=entry_time,
        )
        out = ExitOutcome()
        cur_sl, cur_tp = stop_loss, tp1
        live_volume = volume

        def finish(price: float, result: str, reason: str,
                   when: datetime) -> ExitOutcome:
            out.legs.append(ExitLeg(live_volume, price, reason, when))
            out.exit_price = price
            out.result = result
            out.exit_reason = reason
            out.close_time = when
            out.peak_r = rec.peak_profit_r
            out.adverse_r = rec.peak_adverse_r
            out.sl_stage = rec.sl_stage
            out.tp_extended = rec.tp_extended
            out.final_sl, out.final_tp = cur_sl, cur_tp
            return out

        for j in range(entry_idx, max_idx + 1):
            bar = df_m5.iloc[j]
            bar_time = bar["time"].to_pydatetime()
            high, low, close = (float(bar["high"]), float(bar["low"]),
                                float(bar["close"]))

            # 1. The stop and target IN FORCE were set at the previous bar's
            #    close. Stop first — the conservative intrabar tie-break, kept
            #    identical to the unmanaged path so the two differ only in
            #    management.
            if is_long:
                if low <= cur_sl:
                    res = "LOSS" if cur_sl < entry_price else "WIN_TP1"
                    return finish(cur_sl, res, _stop_reason(rec), bar_time)
                if cur_tp > 0 and high >= cur_tp:
                    return finish(cur_tp, "WIN_TP1", "TP", bar_time)
            else:
                if high >= cur_sl:
                    res = "LOSS" if cur_sl > entry_price else "WIN_TP1"
                    return finish(cur_sl, res, _stop_reason(rec), bar_time)
                if cur_tp > 0 and low <= cur_tp:
                    return finish(cur_tp, "WIN_TP1", "TP", bar_time)

            # 2. Peak and adverse excursion from the bar's extremes — the tick
            #    price reached them even though we only see the bar.
            fav, adv = (high, low) if is_long else (low, high)
            r_fav = rec.current_r(fav)
            r_adv = rec.current_r(adv)
            if r_fav > rec.peak_profit_r:
                rec.peak_profit_r = r_fav
            if r_adv < rec.peak_adverse_r:
                rec.peak_adverse_r = r_adv
            if not rec.reached_1r and rec.peak_profit_r >= 1.0:
                rec.reached_1r = True
                rec.early_close_armed = True
            if not rec.reached_2r and rec.peak_profit_r >= 2.0:
                rec.reached_2r = True

            # 3. The Guardian's state machine, evaluated at the bar close with
            #    a frame that cannot see past it.
            frame = _causal_frame(df_m5, j)

            target_stage, new_sl = self.sl_engine.evaluate(
                rec, close, frame, self.feed)
            if new_sl and abs(new_sl - cur_sl) > self.min_sl_move:
                favourable = ((is_long and new_sl > cur_sl)
                              or (not is_long and new_sl < cur_sl))
                if favourable:
                    cur_sl = float(new_sl)
                    rec.sl_stage = target_stage
                    if target_stage >= 1:
                        rec.breakeven_reached = True

            # `running_into_tp` gates BOTH the early-close suppression and the
            # extension itself, exactly as live. Dropping the suppression would
            # reproduce the bug §8 records: at 2R geometry the proximity band
            # sits above the 1R arming threshold, so early close fires first and
            # the extension stage becomes unreachable.
            running_into_tp = (
                not rec.tp_extended
                and not rec.tp_extend_blocked
                and rec.original_tp > 0
                and abs(close - rec.original_tp) < (0.5 * rec.entry_atr)
                and self.tp_engine.check_momentum(rec, frame)
            )

            if rec.early_close_armed and not running_into_tp:
                should_close, _reason = self.early_engine.check_triggers(
                    rec, frame, close)
                if should_close:
                    out.early_closed = True
                    res = "WIN_TP1" if _pnl_sign(is_long, entry_price,
                                                 close) > 0 else "LOSS"
                    return finish(close, res, "EARLY_CLOSE", bar_time)

            elapsed_min = (bar_time - rec.entry_time).total_seconds() / 60.0
            if (elapsed_min >= self.cfg.no_progress_minutes
                    and rec.peak_profit_r <= self.cfg.no_progress_max_peak_r):
                out.no_progress_closed = True
                res = "WIN_TP1" if _pnl_sign(is_long, entry_price,
                                             close) > 0 else "LOSS"
                return finish(close, res, "NO_PROGRESS", bar_time)

            if running_into_tp:
                split = self._splittable(live_volume)
                if split is None:
                    # Too small to halve — live sets this flag and stops
                    # retrying on every poll. The original TP stands.
                    rec.tp_extend_blocked = True
                else:
                    new_tp = self.tp_engine.get_next_structure_target(
                        rec, df_m15 if df_m15 is not None else frame)
                    if new_tp:
                        banked, remainder = split
                        out.legs.append(
                            ExitLeg(banked, close, "TP_EXTEND_PARTIAL",
                                    bar_time))
                        live_volume = remainder
                        rec.volume = remainder
                        cur_tp = float(new_tp)
                        rec.tp_extended = True
                        out.tp_extended = True

            # 4. Daily flatten, then the bar-count timeout.
            if bar_time >= eod:
                return finish(close, "EOD", "EOD", bar_time)

        last = df_m5.iloc[max_idx]
        return finish(float(last["close"]), "TIMEOUT", "TIMEOUT",
                      last["time"].to_pydatetime())


def _stop_reason(rec: TGAPositionRecord) -> str:
    """Distinguish a trailed stop from the original one.

    They are different exit profiles — §13.3 — and collapsing them into `SL`
    is what made the trailing invisible in the first place.
    """
    if rec.sl_stage >= 2:
        return "TRAIL_SL"
    if rec.sl_stage == 1:
        return "BREAKEVEN_SL"
    return "SL"


def _pnl_sign(is_long: bool, entry: float, exit_price: float) -> float:
    return (exit_price - entry) if is_long else (entry - exit_price)
