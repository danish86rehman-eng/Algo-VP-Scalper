"""
SA Trade Logger
================
Logs every SA trade in the exact OUTPUT FORMAT specified in SCALPER_AGENT.md.
Output target: scalper_log.json (append mode, one record per trade).

SA OUTPUT FORMAT:
    [SA TRADE LOG]
    Instrument     :
    Session Window :
    Trigger Type   :
    Entry Price    :
    Stop Loss      :
    TP1 Target     :
    TP2 Target     :
    Position Size  : (SA pool only)
    SA Pool Risk % :
    Main Acct Risk : 0% (isolated)
    Trade Duration :
    Result         :
    SA Pool P&L    :
    SA State After :
"""
from __future__ import annotations
import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("SA.Logger")


@dataclass
class SATradeRecord:
    """One complete SA trade record matching the spec output format."""
    instrument:      str
    session_window:  str
    trigger_type:    str
    entry_price:     float
    stop_loss:       float
    tp1_target:      float
    tp2_target:      float
    position_size:   float          # Lots (SA pool only)
    sa_pool_risk_pct: float
    main_acct_risk:  str = "0% (isolated)"
    open_time:       str = ""
    close_time:      str = ""
    trade_duration:  str = "0m"
    result:          str = "OPEN"   # OPEN | WIN_TP1 | WIN_TP2 | LOSS | TIMEOUT
    sa_pool_pnl:     float = 0.0
    sa_state_after:  str = "ACTIVE"
    notes:           str = ""
    ticket:          Optional[int] = None

    def formatted_log(self) -> str:
        """Returns the spec-matching text format for display/log."""
        return (
            f"\n[SA TRADE LOG]\n"
            f"Instrument     : {self.instrument}\n"
            f"Session Window : {self.session_window}\n"
            f"Trigger Type   : {self.trigger_type}\n"
            f"Entry Price    : {self.entry_price:.5f}\n"
            f"Stop Loss      : {self.stop_loss:.5f}\n"
            f"TP1 Target     : {self.tp1_target:.5f}\n"
            f"TP2 Target     : {self.tp2_target:.5f}\n"
            f"Position Size  : {self.position_size:.2f} lots (SA pool only)\n"
            f"SA Pool Risk % : {self.sa_pool_risk_pct:.2f}%\n"
            f"Main Acct Risk : {self.main_acct_risk}\n"
            f"Trade Duration : {self.trade_duration}\n"
            f"Result         : {self.result}\n"
            f"SA Pool P&L    : ${self.sa_pool_pnl:+.2f}\n"
            f"SA State After : {self.sa_state_after}\n"
        )


class SATradeLogger:
    """
    Persistent trade logger. Appends one JSON record per trade to scalper_log.json.
    Also writes the spec-format text block to the Python logger.
    """

    def __init__(self, log_path: str = "logs/scalper_log.json"):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        # Initialize file if empty
        if not self.log_path.exists() or self.log_path.stat().st_size == 0:
            self.log_path.write_text("[]", encoding="utf-8")

    def log_open(self, record: SATradeRecord):
        """Call immediately when a trade is opened."""
        record.open_time = datetime.now(timezone.utc).isoformat()
        record.result    = "OPEN"
        self._append(record)
        logger.info(record.formatted_log())

    def log_close(self, ticket: int, result: str, pnl_usd: float,
                  sa_state_after: str, close_time: str = None):
        """
        Record a close: update this ticket's open record, correct the value
        already booked for it, or write a fresh close-only row if it has none.

        Args:
            ticket       : MT5 ticket number.
            result       : WIN_TP1 | WIN_TP2 | LOSS | TIMEOUT | EOD_CLOSE |
                           EOD_RESET | UNRECONCILED. The last one means the
                           broker never priced the position and `pnl_usd` is a
                           $0.00 placeholder rather than a measurement — see
                           `core.constants.OUTCOME_UNRECONCILED`.
            pnl_usd      : Realized P&L in USD, including commission and swap.
            sa_state_after: SA state after close.
            close_time   : ISO timestamp (defaults to now).
        """
        close_time = close_time or datetime.now(timezone.utc).isoformat()
        records = self._load()
        rec = self._find_for_close(records, ticket)

        if rec is None:
            # No open record to update. Every position adopted from MT5 after a
            # restart is in this state — `_adopt_open_positions_from_mt5`
            # repopulates `_open_trades` but never calls `log_open` — and this
            # branch used to drop the close entirely, so the pool ledger booked
            # a trade the log had no row for. Record what is known rather than
            # discarding a real P&L.
            logger.warning(f"SA Logger: no open record for ticket {ticket} — "
                           f"writing a close-only row (adopted position?)")
            rec = asdict(SATradeRecord(
                instrument="UNKNOWN", session_window="UNKNOWN",
                trigger_type="UNKNOWN", entry_price=0.0, stop_loss=0.0,
                tp1_target=0.0, tp2_target=0.0, position_size=0.0,
                sa_pool_risk_pct=0.0, open_time=close_time, ticket=ticket,
                notes="reconstructed at close — no open record"))
            records.append(rec)

        was, was_pnl = rec.get("result"), rec.get("sa_pool_pnl")
        open_dt  = datetime.fromisoformat(rec.get("open_time") or close_time)
        close_dt = datetime.fromisoformat(close_time)
        duration_mins = int((close_dt - open_dt).total_seconds() / 60)
        rec["result"]         = result
        rec["sa_pool_pnl"]    = round(pnl_usd, 2)
        rec["close_time"]     = close_time
        rec["trade_duration"] = f"{duration_mins}m"
        rec["sa_state_after"] = sa_state_after
        self._save(records)

        if was not in (None, "OPEN"):
            logger.info(f"SA Trade CORRECTED | Ticket={ticket} | "
                        f"was {was} ${float(was_pnl or 0.0):+.2f} "
                        f"-> now {result} ${pnl_usd:+.2f}")
        else:
            logger.info(f"SA Trade CLOSED | Ticket={ticket} | "
                        f"Result={result} | PnL=${pnl_usd:+.2f} | "
                        f"Duration={duration_mins}m | State->{sa_state_after}")

    @staticmethod
    def _find_for_close(records: list, ticket: int) -> Optional[dict]:
        """
        The record a close should write into: the open one for this ticket, or
        failing that the most recent one for it.

        The fallback is the reconciliation path. This used to match only on
        `result == "OPEN"`, which made every booking final — so when the agent
        booked a settlement failure as `LOSS` at $0.00, nothing could ever
        repair it. Against the broker, 131 of the 187 repriceable records in
        `scalper_log.json` disagreed with deal history and not one of them
        could be corrected in place (REMEDY_LEDGER L-004).
        """
        for rec in records:
            if rec.get("ticket") == ticket and rec.get("result") == "OPEN":
                return rec
        for rec in reversed(records):
            if rec.get("ticket") == ticket:
                return rec
        return None

    def get_today_summary(self) -> dict:
        """Returns today's trade stats from the log."""
        from datetime import date
        today = date.today().isoformat()
        records = self._load()
        today_recs = [r for r in records if r.get("open_time", "").startswith(today)]
        wins   = sum(1 for r in today_recs if r.get("result", "").startswith("WIN"))
        losses = sum(1 for r in today_recs if r.get("result") == "LOSS")
        total_pnl = sum(r.get("sa_pool_pnl", 0) for r in today_recs)
        return {
            "trades_today": len(today_recs),
            "wins": wins,
            "losses": losses,
            "total_pnl": round(total_pnl, 2),
        }

    # ── Private ───────────────────────────────────────────────────────────────

    def _append(self, record: SATradeRecord):
        records = self._load()
        records.append(asdict(record))
        self._save(records)

    def _load(self) -> list:
        try:
            return json.loads(self.log_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def _save(self, records: list):
        self.log_path.write_text(
            json.dumps(records, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
