"""
Trade forensics — what actually happened to a trade, in R.

Why this module exists
----------------------
Every exit in this system used to be recorded as one of `WIN_TP1`, `LOSS` or
`TIMEOUT` plus a dollar figure. That is not enough information to fix anything.
Three trades that all book `LOSS -$30` can be three unrelated defects:

  * the stop was clipped by a wick and price then ran to TP1  -> stop geometry
  * price never went onside at all                            -> signal quality
  * the trade reached +1.8R and gave every cent back          -> exit management

They have different remedies, and averaging them together hides all three.
`analyse()` replays the confirmation-frame bars the trade lived through,
measures the excursions in R, looks a bounded distance PAST the exit to see
whether the target printed anyway, and assigns one label from a fixed taxonomy.

Scope — read this before extending
----------------------------------
This module has **zero decision authority**. It observes closed trades and
returns a record. It never reads or writes a parameter, never vetoes a setup,
and is never consulted before an entry. A running agent that retunes itself
from a handful of losses is precisely the overfitting machine that CLAUDE.md
13.5 exists to prevent: remedies leave here as evidence for a walk-forward
campaign, not as live configuration.

That property is what lets the same code run in `scalper_agent.py` and in
`backtest_scalper.py` without engaging invariant #2's gate-parity rule — there
is no gate here to keep in step. It is imported by both so the diagnosis of a
simulated book and a live book use one definition of "stop too tight".

Thresholds live here rather than in `decision_params` because they classify
history; they do not decide trades. There is exactly one copy, which is the
property that matters.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger("SA.Postmortem")


# -- Classification thresholds (in R) -----------------------------------------
#: A win that never drew down past this is "clean" — the entry was located
#: well. Beyond it the trade was right but early, which is a different lesson.
WIN_CLEAN_MAE_R = 0.5

#: A loss that first reached this much profit was a winner that was given
#: back. The defect is in exit management, not in the signal.
GAVE_BACK_MFE_R = 1.0

#: A loss whose adverse excursion barely exceeded the stop. Combined with the
#: target printing afterwards, this is stop geometry rather than a bad read.
#: 1.25R allows for the wick that took the stop plus ordinary slippage.
STOP_CLIP_MAE_R = 1.25

#: A loss that never got this far onside never worked at all.
FALSE_SIGNAL_MFE_R = 0.3

#: A timeout that got this close to the target was a budget problem, not a
#: bad trade — CLAUDE.md 13.3 requires the timeout profile to be measured
#: separately, and this is the half of it that argues for a longer budget.
TIMEOUT_NEAR_MISS_MFE_R = 1.5

#: A timeout that moved less than this in EITHER direction never developed.
#: The lesson is about the window it was taken in, not the setup.
TIMEOUT_STALL_R = 0.5

#: Round-turn spread above this fraction of the stop makes cost a material
#: share of the outcome. Recorded as an overlay flag on any mode, because it
#: co-occurs with them rather than replacing them.
COST_HEAVY_SPREAD_TO_SL = 0.10


def htf_alignment(direction: str, htf_trend: str) -> str:
    """
    Whether a trade agreed with the higher-timeframe read at entry.

    Defined here, once, for the same reason the R thresholds above are: the
    live agent and the simulator must not develop two ideas of what
    "counter-HTF" means. Purely descriptive -- nothing consults this before an
    entry, and nothing may branch on it. See CLAUDE.md 13.10.
    """
    if direction not in ("BULLISH", "BEARISH"):
        return "UNKNOWN"
    if htf_trend == "RANGING":
        return "NEUTRAL"
    if htf_trend not in ("BULLISH", "BEARISH"):
        return "UNKNOWN"
    return "AGREE" if direction == htf_trend else "OPPOSE"


# -- Failure taxonomy ---------------------------------------------------------
#: Every label `classify()` can return, with the remedy family each points at.
#: `docs/REMEDY_KB.md` is keyed on exactly these strings.
FAILURE_MODES: Dict[str, str] = {
    "WIN_CLEAN":         "Target reached with shallow drawdown — entry well located.",
    "WIN_SURVIVED_DD":   "Target reached only after deep adverse excursion — entry early.",
    "STOP_TOO_TIGHT":    "Stopped near the extreme, then the target printed anyway.",
    "SIGNAL_FALSE":      "Never went meaningfully onside — the read was wrong.",
    "GAVE_BACK_WINNER":  "Reached >=1R profit and closed at a loss — exit management.",
    "LOSS_ORDINARY":     "Went onside, failed, stopped — the cost of doing business.",
    "TIMEOUT_NEAR_MISS": "Timed out close to target — the time budget was the binding limit.",
    "TIMEOUT_STALLED":   "Timed out having barely moved — taken in a dead window.",
    "TIMEOUT_CHOPPED":   "Timed out after two-sided movement — no directional delivery.",
    "UNCLASSIFIED":      "Insufficient data to classify (missing bars or zero risk).",
}

#: Modes that cost money and therefore have remedies worth researching.
ACTIONABLE_MODES = (
    "STOP_TOO_TIGHT", "SIGNAL_FALSE", "GAVE_BACK_WINNER",
    "TIMEOUT_NEAR_MISS", "TIMEOUT_STALLED", "TIMEOUT_CHOPPED",
)


@dataclass(frozen=True)
class TradeContext:
    """Everything known about a closed trade at the moment it closed."""
    ticket: int
    symbol: str
    direction: str            # BULLISH | BEARISH
    entry: float
    stop_loss: float
    tp1: float
    outcome: str              # WIN_TP1 | WIN_TP2 | LOSS | TIMEOUT
    pnl_usd: float
    open_time: datetime
    close_time: datetime
    trigger_type: str = "UNKNOWN"
    session: str = "UNKNOWN"
    regime: str = "UNKNOWN"
    confidence: str = "UNKNOWN"
    lots: float = 0.0
    risk_usd: float = 0.0     # Intended dollar risk; 0 when unknown
    spread_pips: float = 0.0
    sl_pips: float = 0.0

    # -- Gate state at entry (observation-only; see module docstring) --------
    # Computed by `short_term_bias` at decision time and logged to
    # scalper_agent.log, then discarded before this record was written. That
    # is why `sa_incidents.jsonl` cannot answer any question about gate
    # behaviour. Persisted here as data only: no gate reads these.
    stb_confidence:  str = "UNKNOWN"   # HIGH | MEDIUM | LOW | NONE
    short_term_bias: str = "UNKNOWN"   # BULLISH | BEARISH | NEUTRAL | UNKNOWN
    htf_trend:       str = "UNKNOWN"   # BULLISH | BEARISH | RANGING | UNKNOWN
    recent_sweep:    str = ""
    #: Shipped gate configuration, so aggregates cannot silently mix eras.
    config_era:      str = "UNKNOWN"

    @property
    def risk_price(self) -> float:
        return abs(self.entry - self.stop_loss)

    @property
    def is_long(self) -> bool:
        return self.direction == "BULLISH"


@dataclass
class Postmortem:
    """The forensic record for one closed trade."""
    ticket: int
    symbol: str
    direction: str
    trigger_type: str
    session: str
    regime: str
    confidence: str
    outcome: str
    failure_mode: str
    pnl_usd: float
    exit_r: float
    mfe_r: float
    mae_r: float
    bars_to_mfe: int
    bars_to_mae: int
    bars_held: int
    tp1_after_exit: bool
    bars_to_tp1_after_exit: int
    cost_heavy: bool
    spread_to_sl: float
    open_time: str
    close_time: str
    notes: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)

    # -- Gate state carried through from TradeContext (observation-only) -----
    stb_confidence:  str = "UNKNOWN"
    short_term_bias: str = "UNKNOWN"
    htf_trend:       str = "UNKNOWN"
    recent_sweep:    str = ""
    config_era:      str = "UNKNOWN"
    #: AGREE | OPPOSE | NEUTRAL | UNKNOWN, from `htf_alignment()`.
    htf_alignment:   str = "UNKNOWN"

    def to_record(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def headline(self) -> str:
        return (f"{self.symbol} {self.direction} {self.trigger_type} "
                f"[{self.session}] -> {self.failure_mode} "
                f"(exit {self.exit_r:+.2f}R, MFE {self.mfe_r:+.2f}R, "
                f"MAE {self.mae_r:.2f}R, ${self.pnl_usd:+.2f})")


# -- Analysis -----------------------------------------------------------------

def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def analyse(ctx: TradeContext,
            df_confirm: Optional[pd.DataFrame],
            bar_minutes: int = 5,
            lookahead_bars: int = 24) -> Postmortem:
    """
    Replay a closed trade against confirmation-frame bars.

    Args:
        ctx            : the trade's entry context and booked result.
        df_confirm     : OHLC bars with a `time` column, covering at least
                         open_time -> close_time and ideally `lookahead_bars`
                         beyond it. `None` yields an UNCLASSIFIED record
                         rather than an exception.
        bar_minutes    : minutes per bar in `df_confirm`.
        lookahead_bars : how far past the exit to look for the target. Bounded
                         so "the target printed eventually" cannot be claimed
                         from a move that arrived days later.

    Never raises. A forensic pass must not be able to break a trading loop.
    """
    try:
        return _analyse_inner(ctx, df_confirm, bar_minutes, lookahead_bars)
    except Exception as exc:                      # pragma: no cover - guard
        logger.warning(f"postmortem failed for ticket {ctx.ticket}: {exc}")
        return _unclassified(ctx, f"analysis error: {exc}")


def _unclassified(ctx: TradeContext, note: str) -> Postmortem:
    exit_r = (ctx.pnl_usd / ctx.risk_usd) if ctx.risk_usd else 0.0
    return Postmortem(
        ticket=ctx.ticket, symbol=ctx.symbol, direction=ctx.direction,
        trigger_type=ctx.trigger_type, session=ctx.session, regime=ctx.regime,
        confidence=ctx.confidence, outcome=ctx.outcome,
        failure_mode="UNCLASSIFIED", pnl_usd=round(ctx.pnl_usd, 2),
        stb_confidence=ctx.stb_confidence,
        short_term_bias=ctx.short_term_bias,
        htf_trend=ctx.htf_trend, recent_sweep=ctx.recent_sweep,
        config_era=ctx.config_era,
        htf_alignment=htf_alignment(ctx.direction, ctx.htf_trend),
        exit_r=round(exit_r, 3), mfe_r=0.0, mae_r=0.0,
        bars_to_mfe=0, bars_to_mae=0, bars_held=0,
        tp1_after_exit=False, bars_to_tp1_after_exit=0,
        cost_heavy=False, spread_to_sl=0.0,
        open_time=_as_utc(ctx.open_time).isoformat(),
        close_time=_as_utc(ctx.close_time).isoformat(),
        notes=note,
    )


def _analyse_inner(ctx: TradeContext,
                   df_confirm: Optional[pd.DataFrame],
                   bar_minutes: int,
                   lookahead_bars: int) -> Postmortem:
    if df_confirm is None or len(df_confirm) == 0:
        return _unclassified(ctx, "no confirmation bars supplied")

    risk = ctx.risk_price
    if risk <= 0:
        return _unclassified(ctx, "entry equals stop — zero risk, R undefined")

    df = df_confirm.copy().reset_index(drop=True)
    df["time"] = pd.to_datetime(df["time"], utc=True)

    opened = _as_utc(ctx.open_time)
    closed = _as_utc(ctx.close_time)

    # Include the bar the entry fell inside: a trade opened at 07:03 lives on
    # the 07:00 bar, and excluding it loses the excursion that took the stop.
    window_start = opened - timedelta(minutes=bar_minutes)
    held = df[(df["time"] >= window_start) & (df["time"] <= closed)]
    if held.empty:
        return _unclassified(ctx, "no bars cover the holding period")

    highs = held["high"].to_numpy(dtype=float)
    lows = held["low"].to_numpy(dtype=float)

    if ctx.is_long:
        fav_series = highs - ctx.entry
        adv_series = ctx.entry - lows
    else:
        fav_series = ctx.entry - lows
        adv_series = highs - ctx.entry

    bars_to_mfe = int(fav_series.argmax())
    bars_to_mae = int(adv_series.argmax())

    # Excursions are one-sided measures. A trade that only ever went against
    # you has a negative "best favourable move", which is not progress of
    # -0.4R but simply zero progress. Clamp so the taxonomy reads the way its
    # thresholds are written.
    mfe_r = max(0.0, float(fav_series.max()) / risk)
    mae_r = max(0.0, float(adv_series.max()) / risk)

    # Did the target print after we were out? Bounded look-ahead only.
    ahead = df[df["time"] > closed].head(lookahead_bars).reset_index(drop=True)
    tp1_after_exit = False
    bars_to_tp1_after = 0
    if not ahead.empty and ctx.tp1:
        if ctx.is_long:
            hit = ahead.index[ahead["high"] >= ctx.tp1]
        else:
            hit = ahead.index[ahead["low"] <= ctx.tp1]
        if len(hit) > 0:
            tp1_after_exit = True
            bars_to_tp1_after = int(hit[0]) + 1

    spread_to_sl = (ctx.spread_pips / ctx.sl_pips) if ctx.sl_pips > 0 else 0.0
    cost_heavy = spread_to_sl >= COST_HEAVY_SPREAD_TO_SL

    exit_r = (ctx.pnl_usd / ctx.risk_usd) if ctx.risk_usd else 0.0

    mode = classify(outcome=ctx.outcome, mfe_r=mfe_r, mae_r=mae_r,
                    tp1_after_exit=tp1_after_exit)

    return Postmortem(
        ticket=ctx.ticket, symbol=ctx.symbol, direction=ctx.direction,
        trigger_type=ctx.trigger_type, session=ctx.session, regime=ctx.regime,
        confidence=ctx.confidence, outcome=ctx.outcome, failure_mode=mode,
        stb_confidence=ctx.stb_confidence,
        short_term_bias=ctx.short_term_bias,
        htf_trend=ctx.htf_trend, recent_sweep=ctx.recent_sweep,
        config_era=ctx.config_era,
        htf_alignment=htf_alignment(ctx.direction, ctx.htf_trend),
        pnl_usd=round(ctx.pnl_usd, 2), exit_r=round(exit_r, 3),
        mfe_r=round(mfe_r, 3), mae_r=round(mae_r, 3),
        bars_to_mfe=bars_to_mfe, bars_to_mae=bars_to_mae,
        bars_held=int(len(held)),
        tp1_after_exit=tp1_after_exit,
        bars_to_tp1_after_exit=bars_to_tp1_after,
        cost_heavy=cost_heavy, spread_to_sl=round(spread_to_sl, 4),
        open_time=opened.isoformat(), close_time=closed.isoformat(),
        notes=FAILURE_MODES.get(mode, ""),
        evidence={
            "entry": ctx.entry, "stop_loss": ctx.stop_loss, "tp1": ctx.tp1,
            "risk_price": round(risk, 6), "risk_usd": round(ctx.risk_usd, 2),
            "lots": ctx.lots, "spread_pips": ctx.spread_pips,
            "sl_pips": ctx.sl_pips, "lookahead_bars": lookahead_bars,
            # How much of the look-ahead budget actually existed when this
            # ran. A pass forced early — at shutdown, say — sees fewer bars,
            # and `tp1_after_exit=False` then means "not yet" rather than
            # "no". Without this field the two are indistinguishable and
            # STOP_TOO_TIGHT gets silently under-counted.
            "lookahead_available": int(len(ahead)),
        },
    )


def classify(outcome: str, mfe_r: float, mae_r: float,
             tp1_after_exit: bool) -> str:
    """
    Assign one failure mode. Order is deliberate — the first matching rule is
    the most actionable explanation, not merely the first true statement.
    """
    outcome = (outcome or "").upper()

    if outcome.startswith("WIN"):
        return "WIN_CLEAN" if mae_r <= WIN_CLEAN_MAE_R else "WIN_SURVIVED_DD"

    if outcome == "TIMEOUT":
        if mfe_r >= TIMEOUT_NEAR_MISS_MFE_R:
            return "TIMEOUT_NEAR_MISS"
        if max(mfe_r, mae_r) < TIMEOUT_STALL_R:
            return "TIMEOUT_STALLED"
        return "TIMEOUT_CHOPPED"

    # Losses. "Gave back a winner" outranks everything: a trade that reached
    # 1R and lost is an exit defect regardless of how the stop was placed.
    if mfe_r >= GAVE_BACK_MFE_R:
        return "GAVE_BACK_WINNER"
    if tp1_after_exit and mae_r <= STOP_CLIP_MAE_R:
        return "STOP_TOO_TIGHT"
    if mfe_r < FALSE_SIGNAL_MFE_R:
        return "SIGNAL_FALSE"
    return "LOSS_ORDINARY"


# -- Persistence --------------------------------------------------------------

class IncidentJournal:
    """
    Append-only forensic stream.

    JSON Lines rather than the JSON array the other logs use: this file is
    written by a live trading process and read by an offline analyser, and an
    array requires a read-modify-write of the whole file on every append. One
    line per incident makes an append a single write, so a reader can never
    observe a half-rewritten array and a crash costs at most the last record.
    """

    def __init__(self, path: str = "logs/sa_incidents.jsonl"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, pm: Postmortem) -> None:
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(pm.to_record(), ensure_ascii=False) + "\n")
        except Exception as exc:                  # pragma: no cover - guard
            logger.warning(f"incident write failed for {pm.ticket}: {exc}")

    def load(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        out: List[Dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("skipping malformed incident line")
        return out
