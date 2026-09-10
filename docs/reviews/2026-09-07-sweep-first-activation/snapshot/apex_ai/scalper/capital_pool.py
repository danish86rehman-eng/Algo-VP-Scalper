"""
SA Capital Pool Tracker
========================
Tracks SA-only capital completely separate from main account.
SA pool is a fixed allocation (% of total account) drawn once at startup.
All P&L, daily loss limits, and consecutive loss counters live here.

Rules (from SCALPER_AGENT.md):
- Max SA Risk Per Trade: 0.25% – 0.5% of SA pool only
- Max SA Daily Loss: 2% of SA pool -> auto-shutdown
- Max Open SA Positions: 2 simultaneously
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, date
from typing import List

logger = logging.getLogger("SA.Pool")


@dataclass
class PoolSnapshot:
    timestamp: str
    event: str
    pnl: float
    pool_balance: float
    daily_pnl: float


class SACapitalPool:
    """
    Isolated SA capital pool.
    Main account is NEVER touched — all sizing is done against this pool only.
    """

    def __init__(self, sa_pool_usd: float, risk_pct_per_trade: float = 0.003,
                 max_daily_loss_usd: float = None):
        """
        Args:
            sa_pool_usd         : Fixed dollar amount allocated to SA pool.
            risk_pct_per_trade  : % of SA pool risked per trade (default 0.3%).
            max_daily_loss_usd  : Hard dollar limit for daily loss (optional).
        """
        self.initial_pool   = sa_pool_usd
        self.current_pool   = sa_pool_usd
        self.risk_pct       = risk_pct_per_trade    # 0.0025 – 0.005 per spec
        self._max_daily_loss_usd = max_daily_loss_usd
        self.max_daily_loss_pct = 0.02              # 2% default per spec

        # State
        self._daily_pnl: float = 0.0
        self._daily_date: date  = datetime.now(timezone.utc).date()
        self._consecutive_losses: int = 0
        self._trade_count_today: int = 0
        self._open_positions: int = 0
        self._history: List[PoolSnapshot] = []

        logger.info(f"SA Capital Pool initialized: ${sa_pool_usd:.2f} | "
                    f"Max daily loss: ${self.max_daily_loss:.2f}")

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def max_daily_loss(self) -> float:
        if self._max_daily_loss_usd is not None:
            return self._max_daily_loss_usd
        return self.initial_pool * self.max_daily_loss_pct

    @property
    def risk_per_trade_usd(self) -> float:
        """Dollar risk per trade based on current pool size."""
        return self.current_pool * self.risk_pct

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    @property
    def daily_loss_limit_hit(self) -> bool:
        return self._daily_pnl <= -self.max_daily_loss

    @property
    def consecutive_losses(self) -> int:
        return self._consecutive_losses

    @property
    def open_positions(self) -> int:
        return self._open_positions

    @property
    def can_open_position(self) -> bool:
        """SA spec: max 2 open positions simultaneously."""
        return self._open_positions < 2 and not self.daily_loss_limit_hit

    # ── Position Lifecycle ────────────────────────────────────────────────────

    def register_open(self):
        """Call when SA opens a new position."""
        self._open_positions += 1
        self._trade_count_today += 1
        logger.info(f"SA position opened. Open count: {self._open_positions}")

    def adopt_open_positions(self, count: int):
        """
        Seed _open_positions from existing MT5 positions after a restart.
        Does NOT increment trades_today (those were already counted on the
        original open and restored via the deal-history path).
        """
        self._open_positions = max(0, int(count))
        logger.info(f"SA adopted {self._open_positions} pre-existing open position(s)")

    def register_close(self, pnl_usd: float):
        """
        Call when SA closes a position.
        Args:
            pnl_usd: P&L in USD (negative = loss).
        """
        self._check_date_rollover()
        self._open_positions = max(0, self._open_positions - 1)
        self.current_pool += pnl_usd
        self._daily_pnl   += pnl_usd

        if pnl_usd < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

        self._history.append(PoolSnapshot(
            timestamp=datetime.now(timezone.utc).isoformat(),
            event="CLOSE",
            pnl=pnl_usd,
            pool_balance=self.current_pool,
            daily_pnl=self._daily_pnl,
        ))

        logger.info(f"SA position closed: PnL=${pnl_usd:+.2f} | "
                    f"Pool=${self.current_pool:.2f} | Daily=${self._daily_pnl:+.2f} | "
                    f"Consecutive losses={self._consecutive_losses}")

        if self.daily_loss_limit_hit:
            logger.warning(f"SA DAILY LOSS LIMIT HIT (${-self._daily_pnl:.2f}). "
                           f"Auto-shutdown triggered.")

    # ── Restore from Persisted State (e.g., after a restart) ──────────────────

    def restore(self, daily_pnl: float, cumulative_pnl: float = 0.0,
                trades_today: int = 0, consecutive_losses: int = 0,
                apply_to_pool: bool = True):
        """
        Seed pool state from external history (e.g., MT5 closed deals) after a
        process restart so dashboard PnL is not zeroed mid-day.

        Args:
            daily_pnl          : Sum of realized P&L for today (UTC).
            cumulative_pnl     : Sum of realized P&L since the pool was first
                                 allocated. Adjusts current_pool from initial_pool.
                                 Pass 0.0 if you only want today's PnL applied.
            trades_today       : Count of trades already opened today.
            consecutive_losses : Current consecutive-loss streak.
            apply_to_pool      : When False, restore the daily counters and the
                                 loss streak but leave `current_pool` at the
                                 allocation the operator asked for (pool-mode
                                 FRESH). The daily counters are restored either
                                 way — they feed the daily-loss shutdown, and
                                 dropping them would let a restart reset a
                                 breached limit.
        """
        self._daily_pnl          = float(daily_pnl)
        self._trade_count_today  = int(trades_today)
        self._consecutive_losses = int(consecutive_losses)
        if apply_to_pool:
            # Move current_pool by realized PnL since inception. If caller passes
            # only today's PnL, that becomes the adjustment (good enough for a
            # same-day restart; the dashboard stays internally consistent).
            adj = float(cumulative_pnl) if cumulative_pnl else float(daily_pnl)
            self.current_pool = self.initial_pool + adj
        self._daily_date  = datetime.now(timezone.utc).date()
        logger.info(
            f"SA Capital Pool restored: daily_pnl=${self._daily_pnl:+.2f} | "
            f"pool=${self.current_pool:.2f} | trades_today={self._trade_count_today} | "
            f"consecutive_losses={self._consecutive_losses}"
        )

    # ── Daily Reset ───────────────────────────────────────────────────────────

    def daily_reset(self, new_pool_usd: float = None):
        """
        Reset daily counters at start of new trading day.
        From spec: 3+ consecutive losing days → pool reduced 50%;
                   5+ winning days → pool increased 10%.
        This method handles the reset; pool_adjustment is caller's responsibility.
        """
        self._daily_pnl = 0.0
        self._trade_count_today = 0
        self._daily_date = datetime.now(timezone.utc).date()
        if new_pool_usd is not None:
            self.current_pool = new_pool_usd
            self.initial_pool = new_pool_usd
        logger.info(f"SA daily reset. New pool: ${self.current_pool:.2f}")

    # ── Reporting ─────────────────────────────────────────────────────────────

    def summary(self) -> dict:
        return {
            "sa_pool_initial":  round(self.initial_pool, 2),
            "sa_pool_current":  round(self.current_pool, 2),
            "daily_pnl":        round(self._daily_pnl, 2),
            "max_daily_loss":   round(-self.max_daily_loss, 2),
            "daily_limit_hit":  self.daily_loss_limit_hit,
            "open_positions":   self._open_positions,
            "consecutive_losses": self._consecutive_losses,
            "trades_today":     self._trade_count_today,
        }

    # ── Private ───────────────────────────────────────────────────────────────

    def _check_date_rollover(self):
        today = datetime.now(timezone.utc).date()
        if today != self._daily_date:
            logger.info("SA date rollover detected — resetting daily counters.")
            self._daily_pnl = 0.0
            self._trade_count_today = 0
            self._daily_date = today
