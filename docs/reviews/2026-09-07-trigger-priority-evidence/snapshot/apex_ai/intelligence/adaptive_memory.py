"""
APEX AI — Adaptive Memory (v6)
SQLite-backed trade journal. Learns from wins/losses to adjust strategy weights.
"""
from __future__ import annotations
import json
import sqlite3
import logging
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("APEX.Memory")


@dataclass
class TradeRecord:
    symbol: str
    direction: str
    model_type: str         # 'RETURN' | 'CONTINUATION'
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float
    lot_size: float
    profit_loss: float      # In account currency
    pips: float
    risk_reward_actual: float
    outcome: str            # 'WIN' | 'LOSS' | 'BREAKEVEN'
    regime: str
    session: str
    confidence: float
    clarity: str
    open_time: str
    close_time: str
    notes: str = ""


@dataclass
class StrategyPerformance:
    symbol: str
    model_type: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    avg_rr: float = 0.0
    total_pnl: float = 0.0
    sharpe_estimate: float = 0.0
    weight: float = 1.0     # Used by SEE to boost/suppress strategies
    status: str = "ACTIVE"  # ACTIVE | OBSERVATION | DEGRADED | RETIRED


class AdaptiveMemory:
    """
    APEX Adaptive Memory — persistent trade journal with strategy weight adaptation.
    Adjusts which strategies get capital allocation based on recent performance.
    """

    DB_VERSION = 1

    def __init__(self, db_path: str = "data/trade_journal.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT, direction TEXT, model_type TEXT,
                    entry_price REAL, exit_price REAL,
                    stop_loss REAL, take_profit REAL,
                    lot_size REAL, profit_loss REAL, pips REAL,
                    risk_reward_actual REAL, outcome TEXT,
                    regime TEXT, session TEXT, confidence REAL, clarity TEXT,
                    open_time TEXT, close_time TEXT, notes TEXT
                )""")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS strategy_weights (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT, model_type TEXT, weight REAL,
                    status TEXT, updated_at TEXT,
                    UNIQUE(symbol, model_type)
                )""")
            conn.commit()

    def log_trade(self, record: TradeRecord):
        """Persist a completed trade to the journal."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO trades (symbol, direction, model_type, entry_price,
                exit_price, stop_loss, take_profit, lot_size, profit_loss, pips,
                risk_reward_actual, outcome, regime, session, confidence, clarity,
                open_time, close_time, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (record.symbol, record.direction, record.model_type,
                 record.entry_price, record.exit_price, record.stop_loss,
                 record.take_profit, record.lot_size, record.profit_loss,
                 record.pips, record.risk_reward_actual, record.outcome,
                 record.regime, record.session, record.confidence,
                 record.clarity, record.open_time, record.close_time, record.notes))
            conn.commit()
        logger.info(f"📝 Trade logged: {record.symbol} {record.direction} {record.outcome} "
                    f"PnL={record.profit_loss:.2f}")
        self._update_strategy_weight(record.symbol, record.model_type)

    def get_performance(self, symbol: str = None, model_type: str = None,
                        lookback: int = 20) -> List[StrategyPerformance]:
        """Get strategy performance stats."""
        where_clauses = []
        params = []
        if symbol:
            where_clauses.append("symbol=?")
            params.append(symbol)
        if model_type:
            where_clauses.append("model_type=?")
            params.append(model_type)
        where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(f"""
                SELECT symbol, model_type, outcome, profit_loss, risk_reward_actual
                FROM (SELECT * FROM trades {where} ORDER BY id DESC LIMIT ?)
            """, params + [lookback]).fetchall()

        # Aggregate by symbol+model_type
        groups: Dict[tuple, list] = {}
        for row in rows:
            key = (row[0], row[1])
            groups.setdefault(key, []).append(row)

        result = []
        for (sym, model), trades in groups.items():
            wins   = sum(1 for t in trades if t[2] == 'WIN')
            losses = sum(1 for t in trades if t[2] == 'LOSS')
            total  = len(trades)
            pnl    = sum(t[3] for t in trades)
            avg_rr = sum(t[4] for t in trades) / (total + 1e-10)
            wr     = wins / (total + 1e-10)

            # Simplified Sharpe: mean PnL / std PnL
            pnls = [t[3] for t in trades]
            import numpy as np
            sharpe = float(np.mean(pnls) / (np.std(pnls) + 1e-10)) if len(pnls) > 1 else 0.0

            status = "ACTIVE"
            if total >= 5 and wr < 0.3:
                status = "DEGRADED"
            elif total >= 10 and wr < 0.2:
                status = "RETIRED"

            result.append(StrategyPerformance(
                symbol=sym, model_type=model, total_trades=total,
                wins=wins, losses=losses, win_rate=round(wr, 3),
                avg_rr=round(avg_rr, 2), total_pnl=round(pnl, 2),
                sharpe_estimate=round(sharpe, 3), status=status))

        return result

    def get_strategy_weight(self, symbol: str, model_type: str) -> float:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT weight FROM strategy_weights WHERE symbol=? AND model_type=?",
                (symbol, model_type)).fetchone()
        return row[0] if row else 1.0

    def get_recent_trades(self, limit: int = 10) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("""
                SELECT symbol, direction, model_type, outcome, profit_loss, close_time
                FROM trades ORDER BY id DESC LIMIT ?""", (limit,)).fetchall()
        return [{"symbol": r[0], "direction": r[1], "model": r[2],
                 "outcome": r[3], "pnl": r[4], "time": r[5]} for r in rows]

    def _update_strategy_weight(self, symbol: str, model_type: str):
        """Adjust strategy weight based on recent performance."""
        perfs = self.get_performance(symbol, model_type, lookback=10)
        if not perfs:
            return
        perf = perfs[0]
        # Weight formula: clamp between 0.2 and 2.0
        new_weight = max(0.2, min(2.0, 0.5 + perf.win_rate * 1.5 + perf.sharpe_estimate * 0.3))
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO strategy_weights (symbol, model_type, weight, status, updated_at)
                VALUES (?, ?, ?, ?, ?)""",
                (symbol, model_type, round(new_weight, 3), perf.status,
                 datetime.now(timezone.utc).isoformat()))
            conn.commit()
        logger.info(f"📊 Strategy weight updated: {symbol}/{model_type} → {new_weight:.3f} ({perf.status})")
