"""
SA Daily Reset Protocol
========================
Handles end-of-day cleanup and capital pool adjustment per SCALPER_AGENT.md:

1. Close all open SA positions
2. Record SA P&L separately from main system
3. Reset SA daily loss counter
4. Evaluate: 3+ consecutive losing days -> reduce SA pool by 50%
5. Evaluate: 5+ winning days -> consider increasing SA pool by 10%
"""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import MetaTrader5 as mt5

if TYPE_CHECKING:
    from scalper.capital_pool import SACapitalPool

logger = logging.getLogger("SA.Reset")

DAILY_JOURNAL_PATH = Path("logs/sa_daily_journal.json")


class SADailyReset:
    """
    Manages daily SA protocol: close positions, log P&L, adjust pool.
    """

    def __init__(self):
        DAILY_JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not DAILY_JOURNAL_PATH.exists():
            DAILY_JOURNAL_PATH.write_text("[]", encoding="utf-8")

    def execute(self, pool: "SACapitalPool", mt5_connector,
                sa_tickets: list, closer=None) -> dict:
        """
        Execute end-of-day protocol.
        Args:
            pool          : SACapitalPool instance.
            mt5_connector : MT5 connector for closing positions.
            sa_tickets    : List of currently open SA ticket numbers.
            closer        : Callable(ticket, symbol, pos_type, volume, reason)
                            -> bool, returning True only on a broker-confirmed
                            close. The agent passes its own `_close_at_market`
                            so there is exactly one close implementation in the
                            system. Omitting it falls back to `mt5.Close`.
        Returns:
            dict with day_summary and new pool size. `positions_closed` lists
            only tickets the broker confirmed closed — the caller must keep
            tracking anything absent from it.
        """
        logger.info("SA Daily Reset: Starting end-of-day protocol")

        # Step 1: Close all open SA positions.
        #
        # This loop previously called `mt5.Close(ticket)`. That has never been
        # a valid call: the installed MetaTrader5 signature is
        # `Close(symbol, *, comment=None, ticket=None)`, so the ticket landed
        # in `symbol` and raised TypeError; and `Close` returns a bool, so
        # `result.retcode` would have raised AttributeError even with the
        # arguments the right way round. The bare `except` swallowed both, so
        # the last-resort midnight net logged a failure and moved on — while
        # the caller cleared its open-trade map regardless.
        closed = []
        for ticket in sa_tickets:
            try:
                pos = mt5.positions_get(ticket=ticket)
                if not pos:
                    continue
                p = pos[0]
                if closer is not None:
                    ok = bool(closer(int(p.ticket), p.symbol, p.type,
                                     p.volume, "EOD_RESET"))
                else:
                    ok = bool(mt5.Close(p.symbol, ticket=int(p.ticket)))
                if ok:
                    closed.append(ticket)
                    logger.info(f"SA Reset: Closed ticket {ticket}")
                else:
                    logger.error(
                        f"SA Reset: FAILED to close {ticket} — it remains open "
                        f"at the broker and must stay tracked"
                    )
            except Exception as e:
                logger.error(f"SA Reset: Error closing {ticket}: {e}")

        # Step 2: Record daily P&L
        daily_pnl = pool.daily_pnl
        day_entry = {
            "date": datetime.now(timezone.utc).date().isoformat(),
            "daily_pnl": round(daily_pnl, 2),
            "pool_end": round(pool.current_pool, 2),
            "trades": pool._trade_count_today,
            "result": "WIN" if daily_pnl > 0 else ("LOSS" if daily_pnl < 0 else "FLAT"),
            "positions_closed": closed,
        }
        self._append_journal(day_entry)
        logger.info(f"SA Daily P&L: ${daily_pnl:+.2f} ({day_entry['result']})")

        # Step 3: Calculate adjustment based on recent history
        history = self._load_journal()
        new_pool = self._adjust_pool(pool, history)

        # Step 4 & 5: Reset pool
        pool.daily_reset(new_pool_usd=new_pool)

        return {
            "date": day_entry["date"],
            "daily_pnl": daily_pnl,
            "result": day_entry["result"],
            "positions_closed": closed,
            "new_pool_size": new_pool,
        }

    # ── Private ───────────────────────────────────────────────────────────────

    def _adjust_pool(self, pool: "SACapitalPool", history: list) -> float:
        """
        Spec rules:
            3+ consecutive losing days → reduce SA pool by 50%
            5+ winning days consecutively → increase SA pool by 10%
        """
        if len(history) < 3:
            return pool.current_pool

        recent = history[-5:]  # Last 5 days
        recent_results = [d["result"] for d in recent]

        # Check consecutive losing days (last 3)
        last3 = [d["result"] for d in history[-3:]]
        if all(r == "LOSS" for r in last3):
            new_pool = pool.current_pool * 0.50
            logger.warning(f"SA Reset: 3 consecutive losing days -> "
                           f"SA pool reduced 50% to ${new_pool:.2f}")
            return round(new_pool, 2)

        # Check 5 consecutive winning days
        last5 = [d["result"] for d in history[-5:]]
        if len(last5) == 5 and all(r == "WIN" for r in last5):
            new_pool = pool.current_pool * 1.10
            logger.info(f"SA Reset: 5 consecutive winning days -> "
                        f"SA pool increased 10% to ${new_pool:.2f}")
            return round(new_pool, 2)

        return pool.current_pool

    def _append_journal(self, entry: dict):
        records = self._load_journal()
        records.append(entry)
        DAILY_JOURNAL_PATH.write_text(
            json.dumps(records, indent=2), encoding="utf-8")

    def _load_journal(self) -> list:
        try:
            return json.loads(DAILY_JOURNAL_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            return []
