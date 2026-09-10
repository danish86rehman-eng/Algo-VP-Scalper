"""
scalper_agent.py — SA Main Entry Point
========================================
Standalone Scalper Agent (SA) as specified in SCALPER_AGENT.md.

ISOLATION GUARANTEE:
    - SA capital pool is completely separate from main account capital.
    - SA never communicates with or overrides LIA / MSA / RDA / CAIA / SEE / EA / CRG.
    - SA can be started / stopped independently of maingpt.py.
    - All output is logged to logs/scalper_log.json.

Usage:
    python scalper_agent.py [--pool 1000] [--risk 0.03] [--symbols XAUUSD]

Args:
    --pool      : SA pool size in USD (default: 1000.0).
    --risk      : Risk per trade as decimal fraction of pool (default: 0.03 = 3%).
    --symbols   : Comma-separated SA instruments (default: XAUUSD). XAGUSD and
                  USOIL were dropped 2026-08-22 — they produced 0 of 198 trades
                  in the fresh-window walk-forward (lot floor).
    --interval  : Analysis loop interval in seconds (default: 30).
    --loss-limit: Hard daily loss limit in USD (default: 100.0).
    --dry-run   : Log signals without executing trades.
    --no-cooldown / --win-cooldown-min / --loss-cooldown-policy :
                  Post-trade cooldown switch. A loss holds entries until the
                  next UTC hour; a win holds for --win-cooldown-min minutes.

TIMEFRAMES: triggers on M15, confirms on M5, biases on H1/H4.
"""
from __future__ import annotations
import os
import sys
import time
import json
import logging
import argparse
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

# ── Path ──────────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

# ── Logging Setup ─────────────────────────────────────────────────────────────
Path("logs").mkdir(exist_ok=True)
from logging.handlers import RotatingFileHandler

# Redirected stdout on Windows defaults to cp1252, so any non-cp1252 character
# raises UnicodeEncodeError inside the handler. logging swallows it, so the
# process survives but the console record is lost. Force UTF-8 with a lossy
# fallback before any handler is attached.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

# Rotating rather than plain FileHandler: the previous append-only log reached
# 13 MB in nine days with no ceiling.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        RotatingFileHandler("logs/scalper_agent.log", encoding="utf-8",
                            maxBytes=10 * 1024 * 1024, backupCount=5),
        logging.StreamHandler(sys.stdout),
    ]
)
# Every gate in this system is UTC — sessions, cooldown, news blackout, EOD.
# Local-time stamps put the reader hours away from the decision they are
# trying to explain (the 00:00 UTC daily reset was logged as 05:00).
logging.Formatter.converter = time.gmtime
for _h in logging.getLogger().handlers:
    _h.setFormatter(logging.Formatter(
        "%(asctime)sZ [%(name)s] %(levelname)s: %(message)s"))
logger = logging.getLogger("SA")

# ── SA Module Imports ─────────────────────────────────────────────────────────
from scalper.capital_pool    import SACapitalPool
from scalper.session_checker import SASessionChecker
from scalper.vp_liquidity_trigger import (
    VPLRParams,
    build_context as vplr_context,
    detect_with_context as VPLR_DETECT,
)


def _vplr_record(trigger) -> Optional[dict]:
    """
    The VP evidence chain as a plain dict for the trade journal, or None.

    Flat and JSON-safe by construction: the journal is read back by research
    tooling, and a dataclass or a Timestamp in there would force every consumer
    to know about this module.
    """
    sig = getattr(trigger, "vplr", None)
    if sig is None:
        return None

    def _t(value):
        return value.isoformat() if hasattr(value, "isoformat") else None

    return {
        "profile_type": sig.profile_type,
        "vp_level": sig.vp_level_name,
        "vp_level_price": sig.vp_level_price,
        "poc": sig.poc, "vah": sig.vah, "val": sig.val,
        "anchor_time": _t(sig.anchor_time),
        "anchor_extreme_time": _t(sig.anchor_extreme_time),
        "interacted_level": sig.interacted_level,
        "level_source": sig.level_source,
        "sweep_depth_atr": sig.sweep_depth_atr,
        "reclaimed": sig.reclaimed,
        "displaced": sig.displaced,
        "mss_level": sig.mss_level,
        "mss_time": _t(sig.mss_time),
        "raid_time": _t(sig.raid_time),
        "confluences": list(sig.confluences),
    }


from scalper.trigger_engine  import SATriggerEngine, resolve_enabled_triggers
from scalper.sa_crg          import SACRG
from scalper.behavior_state  import SABehaviorStateMachine, SAState
from scalper.trade_logger    import SATradeLogger, SATradeRecord
from scalper.daily_reset     import SADailyReset
from scalper.sa_consultant   import SAConsultant  # ReadOnly-Consultation layer
from scalper.short_term_bias import ShortTermBiasFilter  # Task 6: short-term bias gate
from scalper.cooldown        import SACooldown
from scalper.reclaim_fvg     import (evaluate_reclaim, entry_in_reclaim_zone,
                                     close_barriers)
from scalper.m15_fvg_entry   import find_fvg_entry, entry_quote_allowed
from scalper.htf_crt         import watch_crt, select_crt, used_setups, crt_quote_allowed
from scalper.ema_filter      import EMABandFilter  # H1 EMA(18) high/low band
from scalper.leg_confluence  import (MODES as LEG_CONF_MODES, build_leg_pair,
                                     evaluate as leg_conf_evaluate)
from scalper.vp_gate         import MODES as VP_MODES, VolumeProfileGate
from scalper.regime_classifier import RegimeClassifier  # trending vs ranging
from scalper.regime_direction_gate import (
    MODES as RD_MODES, RegimeDirectionGate)  # do not fade a trend
from scalper.pdr_gate       import (
    MODES as PDR_MODES, PDRGate)              # previous-day-range location
from scalper.reject_log      import RejectionLog   # structured gate telemetry
from scalper import postmortem as PMORTEM          # closed-trade forensics
from core.news_guard         import NewsGuard
from core.news_fetcher       import fetch_and_save as fetch_news_events
from core.constants          import (MAGIC_SCALPER, LOG_MAX_BYTES,
                                     LOG_BACKUP_COUNT, OUTCOME_UNRECONCILED)
# Every value below is shared with backtest_scalper.py. See the module docstring
# for why these stopped being per-file constants.
from scalper import decision_params as DP

# ── MT5 ───────────────────────────────────────────────────────────────────────
import MetaTrader5 as mt5
import pandas as pd
from dotenv import load_dotenv


# ═════════════════════════════════════════════════════════════════════════════
# SA Core
# ═════════════════════════════════════════════════════════════════════════════

class ScalperAgent:
    """
    SA — Autonomous Intra-Session Scalper.
    Operates on M15/M5 with no dependency on main system agents.
    """

    SYMBOLS_DEFAULT = ["XAUUSD"]    # Spec primary instrument

    TF_M5  = mt5.TIMEFRAME_M5
    TF_M1  = mt5.TIMEFRAME_M1
    TF_M15 = mt5.TIMEFRAME_M15
    TF_H1  = mt5.TIMEFRAME_H1
    TF_H4  = mt5.TIMEFRAME_H4

    # ── Timeframe stack (M15/M5) ─────────────────────────────────────────────
    # SA now triggers on M15 and confirms on M5. It previously triggered on M5
    # and confirmed on M1.
    #
    # Why the move up:
    #   1. The M5/M1 stack fired ~11 trades/day/symbol on SWEEP_REJECTION and
    #      was uniformly unprofitable across every walk-forward fold — the
    #      signal was dominated by intrabar noise, not by structure.
    #   2. Round-turn spread is a fixed cost per trade. Halving trade frequency
    #      halves the cost drag; widening the stop (M15 swings are wider than
    #      M5 swings) raises the reward-to-cost ratio on the trades that remain.
    #   3. The MIN_SL_PIPS institutional floor was rejecting a large share of
    #      M5 setups as noise-grade. On M15 the natural stop distance clears
    #      that floor instead of fighting it.
    #
    # TF_TRIGGER carries the structure and the entry pattern; TF_CONFIRM is the
    # finer frame used for the imbalance-fill trigger and for entry timing.
    #
    # These now come from scalper/decision_params.py so the simulator reads the
    # identical values. They stay exposed as class attributes because callers
    # and tests reference ScalperAgent.TF_TRIGGER by name.
    TF_TRIGGER = DP.TF_TRIGGER
    TF_CONFIRM = DP.TF_CONFIRM
    TRIGGER_TF_MINUTES = DP.TRIGGER_TF_MINUTES
    CONFIRM_TF_MINUTES = DP.CONFIRM_TF_MINUTES
    TRIGGER_BARS = DP.TRIGGER_BARS
    CONFIRM_BARS = DP.CONFIRM_BARS

    #: Hard timeout expressed in TRIGGER-timeframe bars rather than wall-clock
    #: minutes, so it scales automatically with the stack. 24 M15 bars = 6h.
    #: The old fixed 120 minutes was 24 M5 bars; keeping the bar count constant
    #: preserves the intent ("give the setup two dozen bars to work") while
    #: matching the new frame. A too-short timeout is a forced random exit, and
    #: random exits with negative expectancy erode the whole system
    #: (MQL5 art. 19211) — 23% of live trades were closing on timeout.
    TIMEOUT_BARS = DP.TIMEOUT_BARS

    CONSULT_MAX_LATENCY_MS = DP.CONSULT_MAX_LATENCY_MS
    NEWS_REFRESH_HOURS = 6
    #: Backoff between retries when a news fetch fails. Without it the failure
    #: path never stamps _last_news_fetch, so the 6-hour throttle never engages
    #: and the agent re-attempts the download on every 30s cycle — 643 attempts
    #: in one observed session.
    NEWS_RETRY_MINUTES = 15
    #: A calendar older than this cannot be trusted to cover today's events.
    #: Logged loudly; it does not block trading (that would be a new gate, and
    #: gates must be mirrored into the simulator — invariant #2).
    NEWS_STALE_HOURS = 24

    #: Confirmation bars the forensic pass looks PAST the exit, to answer
    #: "was the stop clipped, or was the read simply wrong?". 24 M5 bars = 2h.
    #: Bounded on purpose: a target that printed the next day is not evidence
    #: that the stop was too tight. The pass is deferred by this many bars,
    #: because at the moment of close those bars do not exist yet.
    PM_LOOKAHEAD_BARS = 24

    #: Broker-side confirmation that a close actually removed the position.
    CLOSE_CONFIRM_ATTEMPTS = 4
    CLOSE_CONFIRM_DELAY_S = 0.25
    #: Deal history lags order_send. Poll for the closing (DEAL_ENTRY_OUT) leg
    #: rather than booking whatever is visible the instant the order returns.
    PNL_SETTLE_ATTEMPTS = 5
    PNL_SETTLE_DELAY_S = 0.3
    #: How long a vanished position may go unreconciled before the agent stops
    #: waiting for its closing deal. Within this window the close is simply not
    #: booked and the next cycle retries it; past it the trade is booked as
    #: OUTCOME_UNRECONCILED so a never-settling ticket cannot hold a position
    #: slot for the lifetime of the process. Accounting plumbing, not strategy:
    #: it changes when the books are written, never which trades are taken.
    PNL_RECONCILE_GRACE_MIN = 10

    @property
    def timeout_minutes(self) -> int:
        return self.TIMEOUT_BARS * self.TRIGGER_TF_MINUTES

    # P1 — Per-trigger regime whitelist (inverts the prior blanket
    # MANIPULATION/STRESS block, which was upside-down from ICT reality).
    #   SWEEP_REJECTION / JUDAS : fade liquidity grabs → MANIPULATION + ROTATION
    #   BOS_RETEST              : trend continuation   → EXPANSION only
    #   FVG_FILL                : mitigation entry     → EXPANSION + MANIPULATION
    # STRESS regime is always rejected (handled separately).
    TRIGGER_REGIME_WHITELIST = DP.TRIGGER_REGIME_WHITELIST

    # P1 — Thin-liquidity hour windows (UTC hour ranges, end-exclusive).
    # During these windows we require STB confidence == HIGH so only the
    # strongest setups fire. Today's loss data: 5 of 9 losses occurred in
    # the 19:00-22:00 UTC band (NY close → Asia activation). Not blocking
    # outright — allows clear HIGH-confidence trades through.
    THIN_LIQUIDITY_HOURS_UTC = DP.THIN_LIQUIDITY_HOURS_UTC
    THIN_LIQ_REQUIRED_CONFIDENCE = DP.THIN_LIQ_REQUIRED_CONFIDENCE

    def __init__(self, sa_pool_usd: float, risk_pct: float,
                 symbols: List[str], loss_limit_usd: float = None,
                 interval: int = 30, dry_run: bool = False,
                 cooldown_enabled: bool = True,
                 win_cooldown_minutes: float = 5.0,
                 loss_cooldown_policy: str = "NEXT_UTC_HOUR",
                 allow_whole_day: bool = False,
                 enabled_triggers=None,
                 enabled_sessions=None,
                 ema_band_enabled: bool = DP.EMA_BAND_ENABLED,
                 ema_band_mode: str = DP.EMA_BAND_MODE,
                 stb_relax_continuation: bool = DP.STB_RELAX_CONTINUATION,
                 leg_conf_enabled: bool = DP.LEG_CONF_ENABLED,
                 leg_conf_mode: str = DP.LEG_CONF_MODE,
                 sweep_wick_filter: bool = DP.SWEEP_WICK_FILTER_ENABLED,
                 sweep_wick_ratio: float = DP.SWEEP_WICK_RATIO_MIN,
                 reclaim_fvg_enabled: bool = DP.RECLAIM_FVG_ENABLED,
                 m15_fvg_entry_enabled: bool = DP.M15_FVG_ENTRY_ENABLED,
                 htf_crt_enabled: bool = DP.CRT_ENABLED,
                 crt_confluence_mode: str = DP.CRT_CONFLUENCE_MODE,
                 vp_gate_enabled: bool = DP.VP_GATE_ENABLED,
                 vp_gate_mode: str = DP.VP_GATE_MODE,
                 vp_poc_band_frac: float = DP.VP_POC_BAND_FRAC,
                 va_fade_enabled: bool = DP.VA_FADE_ENABLED,
                 rd_gate_enabled: bool = DP.RD_GATE_ENABLED,
                 rd_gate_mode: str = DP.RD_GATE_MODE,
                 pdr_gate_enabled: bool = DP.PDR_GATE_ENABLED,
                 pdr_gate_mode: str = DP.PDR_GATE_MODE,
                 vplr_enabled: bool = DP.VPLR_ENABLED,
                 vplr_session_override: bool = DP.VPLR_SESSION_OVERRIDE_ENABLED,
                 pool_mode: str = "RESUME"):
        self.symbols    = symbols
        self.interval   = interval
        self.dry_run    = dry_run
        self.reclaim_fvg_enabled = reclaim_fvg_enabled
        self.m15_fvg_entry_enabled = m15_fvg_entry_enabled
        self.htf_crt_enabled = htf_crt_enabled
        from scalper.crt_confluence import validate_mode
        self.crt_confluence_mode = validate_mode(crt_confluence_mode)
        self._crt_used = set()
        self._crt_frames_cache = {}
        self._crt_watch_bar = {}
        self._reclaim_last_closes = {}
        self._reclaim_history_ready = False
        # RESUME: current_pool = --pool + all realized P&L since inception
        #         (the pool compounds; a losing history shrinks the risk unit).
        # FRESH:  current_pool = --pool exactly. Use this when the run is meant
        #         to measure the strategy at a stated risk budget.
        # Set before _restore_pool_from_mt5(), which reads it.
        self.pool_mode  = str(pool_mode).upper()
        if self.pool_mode not in ("RESUME", "FRESH"):
            raise ValueError(f"pool_mode must be RESUME or FRESH, got {pool_mode!r}")
        self._running   = False
        self._cycle     = 0
        self._start_time = datetime.now(timezone.utc)
        # Last closed trade, used to re-arm the cooldown across restarts.
        self._last_close_time: Optional[datetime] = None
        self._last_close_pnl: Optional[float] = None

        # SA state
        self._open_trades: Dict[int, dict] = {}    # ticket → trade_info
        self._session_open_prices: Dict[str, float] = {}
        # Tracks which session window was active in the previous cycle.
        # When the window name changes (e.g. LONDON_OPEN -> LONDON_NY), we
        # clear _session_open_prices so each session's Judas detector fades
        # against THAT session's open candle — not yesterday's first
        # observation as the previous build did.
        self._last_session_name: Optional[str] = None
        self._last_reset_date = datetime.now(timezone.utc).date()
        self._last_news_fetch: Optional[datetime] = None
        # Distinct from _last_news_fetch: an *attempt*, successful or not.
        # Drives the retry backoff so a failing feed cannot be hammered once
        # per cycle.
        self._last_news_attempt: Optional[datetime] = None

        # SA components (all isolated). Note: SA-CRG must be constructed
        # BEFORE _restore_pool_from_mt5() so that prime_from_history() can
        # arm the post-restart loss-pause from the actual last-loss timestamp.
        self.pool        = SACapitalPool(sa_pool_usd=sa_pool_usd,
                                         risk_pct_per_trade=risk_pct,
                                         max_daily_loss_usd=loss_limit_usd)
        self.crg         = SACRG()
        # Restore today's realized PnL from MT5 history so a mid-day restart
        # does not wipe the dashboard daily PnL back to $0. Also restores the
        # lifetime cumulative PnL via a persisted inception-date state file.
        self._restore_pool_from_mt5()
        # Adopt any SA-magic positions that were still open at restart so the
        # 120-min timeout and EOD enforcement keep working on them.
        self._adopt_open_positions_from_mt5()
        # VP_LIQUIDITY_REACTION. The trigger-scoped Asia allowance is only
        # handed to the session checker when BOTH the trigger and the override
        # are on — with the trigger off, `vp_window` stays None, `vp_window_open`
        # is always False, the VP_ONLY state is unreachable and the agent's
        # decision path is exactly what it was before this change.
        self.vplr_enabled = bool(vplr_enabled)
        self.vplr_params = VPLRParams.from_decision_params(DP)
        vp_window = (DP.VPLR_SESSION_WINDOW_UTC
                     if (self.vplr_enabled and vplr_session_override) else None)
        self.session     = SASessionChecker(allow_whole_day=allow_whole_day,
                                            enabled_sessions=enabled_sessions,
                                            vp_window=vp_window)
        # Restricting the enabled trigger set isolates one concept for
        # measurement. `step2_trigger` returns the first detector that fires,
        # so SWEEP_REJECTION at the head of the priority order masks every
        # trigger behind it. Mirrored in backtest_scalper.py (invariant #2).
        self.trigger_eng = SATriggerEngine(
            enabled_triggers=resolve_enabled_triggers(enabled_triggers,
                                                      self.vplr_enabled, htf_crt_enabled),
            sweep_wick_filter=sweep_wick_filter,
            sweep_wick_ratio=sweep_wick_ratio)
        self.state_mach  = SABehaviorStateMachine()
        self.trade_log   = SATradeLogger("logs/scalper_log.json")
        self.daily_reset = SADailyReset()
        self.consultant  = SAConsultant()  # Institutional ReadOnly-Consultation
        self.stb_filter  = ShortTermBiasFilter(
            relax_continuation=stb_relax_continuation)  # PRIMARY bias gate
        self.ema_filter  = EMABandFilter(mode=ema_band_mode)  # H1 EMA(18) band
        self.ema_band_enabled = ema_band_enabled
        self.va_fade_enabled = va_fade_enabled
        # Regime-direction gate: refuse to fade a classified H1 trend.
        self.rd_gate_enabled = rd_gate_enabled
        self.rd_gate = RegimeDirectionGate(
            mode              = rd_gate_mode,
            regime_classifier = RegimeClassifier(
                lookback        = DP.VP_REGIME_LOOKBACK,
                smoothing       = DP.VP_REGIME_SMOOTHING,
                trend_threshold = DP.VP_REGIME_TREND_THRESHOLD,
                vol_threshold   = DP.VP_REGIME_VOL_THRESHOLD,
                er_threshold    = DP.VP_REGIME_ER_THRESHOLD,
            ),
        )
        # Previous-day-range gate: refuse to buy the premium / sell the
        # discount of yesterday's range. Ledger L-011.
        self.pdr_gate_enabled = pdr_gate_enabled
        self.pdr_gate = PDRGate(
            mode          = pdr_gate_mode,
            discount_frac = DP.PDR_DISCOUNT_FRAC,
            premium_frac  = DP.PDR_PREMIUM_FRAC,
        )
        # H4 value-area location gate: sell from VAH, buy from VAL, nothing at
        # the POC. Every parameter comes from decision_params so the simulator
        # constructs an identical gate (invariant #2).
        self.leg_conf_enabled = leg_conf_enabled
        self.leg_conf_mode = leg_conf_mode
        self.vp_gate_enabled = vp_gate_enabled
        self.vp_gate = VolumeProfileGate(
            mode                = vp_gate_mode,
            profile_bars        = DP.VP_PROFILE_BARS,
            target_bins         = DP.VP_TARGET_BINS,
            value_area_pct      = DP.VP_VALUE_AREA_PCT,
            poc_band_frac       = vp_poc_band_frac,
            edge_tolerance_frac = DP.VP_EDGE_TOLERANCE_FRAC,
            regime_classifier   = RegimeClassifier(
                lookback        = DP.VP_REGIME_LOOKBACK,
                smoothing       = DP.VP_REGIME_SMOOTHING,
                trend_threshold = DP.VP_REGIME_TREND_THRESHOLD,
                vol_threshold   = DP.VP_REGIME_VOL_THRESHOLD,
                er_threshold    = DP.VP_REGIME_ER_THRESHOLD,
            ),
        )
        self.cooldown    = SACooldown(
            win_minutes=win_cooldown_minutes,
            loss_policy=loss_cooldown_policy,
            enabled=cooldown_enabled,
        )
        # Re-arm the cooldown from the last closed trade so a restart cannot be
        # used to escape an active pause.
        self.cooldown.restore(self._last_close_time, self._last_close_pnl)
        # ── Self-diagnosis layer (observation only, zero decision authority) ──
        # Neither of these is consulted before a trade. They record what the
        # gates rejected and what happened to the trades that got through, so
        # the offline remedy loop has evidence to work from instead of a grep
        # of a rotating log. See scalper/postmortem.py for why this must stay
        # non-decisional.
        self.rejects = RejectionLog("logs/sa_rejections.json")
        self.incidents = PMORTEM.IncidentJournal("logs/sa_incidents.jsonl")
        # Closed trades awaiting their look-ahead window. A forensic pass run
        # at the instant of close cannot see whether the target printed
        # afterwards — the bars do not exist yet — so the pass is deferred
        # until PM_LOOKAHEAD_BARS of confirmation bars have formed.
        self._pending_postmortems: List[dict] = []

        self.news_guard  = NewsGuard(
            events_file=str(Path(__file__).parent / "news_events.json"),
            blackout_before_min=30,
            blackout_after_min=15,
            impact_levels=["HIGH"],
        )

        # Initial news calendar fetch (Forex Factory / FairEconomy mirror)
        try:
            count = fetch_news_events()
            if count >= 0:
                self._last_news_fetch = datetime.now(timezone.utc)
                logger.info(f"SA News: {count} HIGH-impact events loaded on startup")
            else:
                logger.warning("SA News: Startup fetch failed — using existing file")
        except Exception as e:
            logger.error(f"SA News: Startup fetch error — {e}")

        priority = [name for name in self.trigger_eng.ALL_TRIGGERS
                    if name in self.trigger_eng.enabled_triggers
                    and (name != "VALUE_AREA_FADE" or self.va_fade_enabled)]
        logger.info("SA TRIGGER PRIORITY: %s | VP_LIQUIDITY_REACTION=%s | "
                    "VALUE_AREA_FADE=%s | Era=%s",
                    " > ".join(priority), self.vplr_enabled,
                    self.va_fade_enabled, DP.CONFIG_ERA)
        logger.info(
            f"\n"
            f"{'='*60}\n"
            f"  SCALPER AGENT (SA) — INITIALIZING\n"
            f"  SA Pool      : ${sa_pool_usd:.2f} USD (isolated)\n"
            f"  Risk/Trade   : {risk_pct*100:.2f}% of SA pool\n"
            f"  Max Daily Loss: ${self.pool.max_daily_loss:.2f} (2% of pool)\n"
            f"  Symbols      : {symbols}\n"
            f"  Dry Run      : {dry_run}\n"
            f"  Interval     : {interval}s\n"
            f"{'='*60}"
        )

    # ── Main Loop ─────────────────────────────────────────────────────────────

    def run(self):
        self._running = True
        logger.info("SA: Starting main analysis loop")

        while self._running:
            try:
                self._cycle += 1
                now = datetime.now(timezone.utc)

                # Daily reset check
                if now.date() != self._last_reset_date:
                    self._perform_daily_reset()
                    self._last_reset_date = now.date()

                # EOD enforcement: close all open trades 1h before next day
                self._enforce_eod_close(now)

                # Periodic news calendar refresh
                self._refresh_news_if_due(now)

                # Monitor open trades (120-min timeout)
                self._monitor_open_trades()

                # Run any forensic pass whose look-ahead window has filled.
                # Observation only — nothing downstream reads the result.
                self._drain_postmortems(now)

                # First watch, including outside entry sessions. This observes
                # closed bars only; execution still requires ACTIVE below.
                if self.htf_crt_enabled:
                    for symbol in self.symbols:
                        self._watch_htf_crt(symbol, now)

                # Session and state evaluation
                sess = self.session.get_state(now)
                acct = mt5.account_info()
                main_dd = self._get_main_dd(acct)

                # Per-session reset: when we enter a NEW session window, clear
                # the session-open anchors so each kill zone's Judas detector
                # fades against its own opening candle, not a prior session's.
                # Only triggers on transitions INTO an active window — we
                # don't reset on transitions to IDLE (no scanning anyway).
                current_session = sess.window_name if sess.in_window else None
                if current_session and current_session != self._last_session_name:
                    if self._last_session_name is not None:
                        logger.info(
                            f"SA session transition: {self._last_session_name} -> "
                            f"{current_session} | clearing session-open anchors "
                            f"({len(self._session_open_prices)} symbols re-anchor "
                            f"on next scan)"
                        )
                    self._session_open_prices.clear()
                    self._last_session_name = current_session

                # `vp_window_open` is consulted by the state machine only after
                # PROTECTED / HALTED / PAUSED have been evaluated and cleared,
                # so the VP allowance can never override a hard protection.
                new_state = self.state_mach.evaluate(
                    in_session_window   = sess.in_window,
                    daily_loss_limit_hit= self.pool.daily_loss_limit_hit,
                    main_dd_pct         = main_dd,
                    consecutive_losses  = self.pool.consecutive_losses,
                    pause_active        = self.crg._pause_until is not None
                                         and now < (self.crg._pause_until or now),
                    vp_window_open      = self.session.vp_window_open(now),
                )

                cd_state = self.cooldown.state(now)
                cd_txt = (f" | COOLDOWN {cd_state.remaining_seconds}s"
                          if cd_state.active else "")
                logger.info(
                    f"SA Cycle #{self._cycle} | State={new_state.value} | "
                    f"Session={self.session.vp_window_name(now)} | "
                    f"Pool=${self.pool.current_pool:.2f} | "
                    f"Daily PnL=${self.pool.daily_pnl:+.2f} | "
                    f"Open={self.pool.open_positions}{cd_txt}"
                )

                # Scan when ACTIVE, or when the VP-only allowance is open. In
                # VP_ONLY the trigger set is narrowed to VP_LIQUIDITY_REACTION
                # inside _scan_symbol, so no other detector is even evaluated
                # and none of them can consume the bar.
                if new_state in (SAState.ACTIVE, SAState.VP_ONLY):
                    vp_only = (new_state == SAState.VP_ONLY)
                    for symbol in self.symbols:
                        self._scan_symbol(symbol, sess, main_dd, now,
                                          vp_only=vp_only)

            except KeyboardInterrupt:
                logger.info("SA: Keyboard interrupt received — stopping")
                self._running = False
            except Exception as e:
                logger.error(f"SA Cycle error: {e}", exc_info=True)

            time.sleep(self.interval)

        # Shutdown: seal the telemetry. A partial look-ahead on the last few
        # trades beats losing their records, and the pass stamps how many bars
        # it actually saw so a truncated one is identifiable.
        try:
            self._drain_postmortems(datetime.now(timezone.utc), force=True)
            self.rejects.flush()
            logger.info(f"SA: {self.rejects.summary_line()}")
        except Exception as e:
            logger.warning(f"SA: telemetry flush on shutdown failed: {e}")

        logger.info("SA: Agent stopped.")

    # ── Symbol Scanning ───────────────────────────────────────────────────────

    def _scan_symbol(self, symbol: str, sess, main_dd: float, now: datetime,
                     vp_only: bool = False):
        """
        Run the full 3-step SA check for one symbol.

        `vp_only` narrows the trigger set to VP_LIQUIDITY_REACTION for this
        scan. It affects DETECTION only: every gate below — dedup, cooldown,
        STB, thin-liquidity, consultation, spread, net-R, news, risk and
        exposure — runs exactly as it does in a normal session.
        """

        # Fetch the M15/M5 stack. `df_m5` / `df_m1` keep their local names
        # because they flow into positional trigger-engine arguments meaning
        # "trigger frame" and "confirmation frame"; they now carry M15 and M5.
        df_m5 = self._get_ohlcv(symbol, self.TF_TRIGGER, bars=self.TRIGGER_BARS)
        df_m1 = self._get_ohlcv(symbol, self.TF_CONFIRM, bars=self.CONFIRM_BARS)
        if df_m5 is None or df_m1 is None:
            self.rejects.record("NO_DATA", symbol, "OHLCV fetch returned None", now)
            return

        # Global post-trade cooldown (win = short break, loss = until the next
        # UTC hour). Checked before any analysis so a paused agent does no work.
        if not self.cooldown.can_enter(now):
            cd = self.cooldown.state(now)
            logger.debug(
                f"SA {symbol}: COOLDOWN active — {cd.reason} "
                f"({cd.remaining_seconds}s remaining)"
            )
            self.rejects.record("COOLDOWN", symbol, cd.reason, now)
            return

        # P0 trigger dedup — every loss cluster today shows 2-3 identical
        # trades opened on the same setup within minutes of each other.
        # If SA already has an open position on this symbol, do not scan
        # for a new entry — wait for the existing trade to close. This
        # single check prevents the double-risking pattern that produced
        # 7/7 of today's loss clusters.
        for ot in self._open_trades.values():
            if ot.get("symbol") == symbol:
                logger.debug(
                    f"SA {symbol}: scan skipped — open position already exists "
                    f"({ot.get('direction')} ticket may not be in dict yet)"
                )
                self.rejects.record("DEDUP", symbol, "open position exists", now)
                return

        # Step 1: Micro Liquidity
        liq = self.trigger_eng.step1_liquidity(df_m5, symbol)

        # Track session open price
        if symbol not in self._session_open_prices:
            self._session_open_prices[symbol] = float(df_m5['open'].iloc[-1])
        session_open = self._session_open_prices.get(symbol)

        # Step 2: Trigger Detection
        # The H4 profile is built once per scan when any consumer needs it:
        # the VALUE_AREA_FADE trigger below, and the VP gate further down.
        vp_profile = None
        if self.va_fade_enabled or self.vp_gate_enabled:
            df_h4_vp = self._get_ohlcv(symbol, DP.VP_PROFILE_TF,
                                       bars=DP.VP_PROFILE_FETCH_BARS)
            vp_profile = self.vp_gate.build_profile(df_h4_vp)

        # ── VP_LEG_CONFLUENCE legs (L-015) ──────────────────────────────────
        # Two COMPLETED H4 swing legs, built once per scan before any trigger
        # is chosen, so the same pair judges every candidate on this bar.
        # `_get_ohlcv` reads from position 1, so no leg can include a forming
        # H4 bar. Mirrored verbatim in backtest_scalper (invariant #2).
        leg_pair = None
        if self.leg_conf_enabled:
            df_h4_legs = self._get_ohlcv(symbol, DP.VP_PROFILE_TF,
                                         bars=DP.VP_PROFILE_FETCH_BARS)
            if df_h4_legs is not None:
                leg_pair = build_leg_pair(
                    df_h4_legs, symbol,
                    swing_lookback=DP.LEG_CONF_SWING_LOOKBACK,
                    min_leg_bars=DP.LEG_CONF_MIN_LEG_BARS,
                    min_leg_atr=DP.LEG_CONF_MIN_LEG_ATR,
                    atr_period=DP.LEG_CONF_ATR_PERIOD,
                    target_bins=DP.LEG_CONF_TARGET_BINS,
                    value_area_pct=DP.LEG_CONF_VALUE_AREA_PCT,
                    node_stddev_mult=DP.LEG_CONF_NODE_STDDEV_MULT)

        # ── VP_LIQUIDITY_REACTION context ────────────────────────────────────
        # The anchored H4 profile, plus the previous COMPLETED daily bar for
        # PDH/PDL. Both frames come from `_get_ohlcv`, which reads from
        # position 1 — so neither the H4 leg nor the daily level can be taken
        # from a bar that is still forming (L-007).
        vplr_ctx = None
        # In ASIA_ONLY the trigger is not evaluated during normal sessions at
        # all, so it cannot pre-empt a detector that was already trading those
        # hours profitably. Skipping the build also avoids two OHLCV fetches
        # per scan for a trigger that could not fire.
        vplr_in_scope = (self.vplr_enabled
                         and (DP.VPLR_SCOPE == "ALL_SESSIONS" or vp_only))
        if vplr_in_scope:
            df_h4_vplr = self._get_ohlcv(symbol, DP.VPLR_PROFILE_TF,
                                         bars=DP.VPLR_PROFILE_FETCH_BARS)
            df_d1_vplr = self._get_ohlcv(symbol, DP.PDR_TF, bars=DP.PDR_BARS)
            if df_h4_vplr is not None:
                vplr_ctx = vplr_context(
                    df_h4_vplr, symbol, self.vplr_params,
                    df_d1_closed=df_d1_vplr,
                    htf_trend=self.stb_filter._htf_trend(
                        symbol, self._get_ohlcv(symbol, self.TF_H1, bars=120),
                        None),
                )

        m15_fvg_plan = None
        if self.m15_fvg_entry_enabled and not vp_only:
            if not self._reclaim_history_ready:
                self.rejects.record("M15_FVG_HISTORY_UNAVAILABLE", symbol, "prior close unknown", now)
                return
            quote_tick = mt5.symbol_info_tick(symbol)
            if quote_tick is None:
                self.rejects.record("M15_FVG_NO_QUOTE", symbol, "no executable quote", now)
                return
            m15_fvg_plan = find_fvg_entry(
                df_m5, df_m1, quote_tick.bid, quote_tick.ask, now,
                self._reclaim_last_closes, symbol)
            if m15_fvg_plan.allow:
                logger.info(f"SA {symbol}: M15 FVG candidate | {m15_fvg_plan.record()}")
            elif m15_fvg_plan.reason != "M15_FVG_WAIT_PRICE":
                self.rejects.record(m15_fvg_plan.reason, symbol, str(m15_fvg_plan.record()), now)

        # In VP_ONLY the engine is asked for one detector. This is a narrowing
        # of what may fire, never a widening: the trigger's own contract still
        # has to hold in full.
        if vp_only:
            saved = self.trigger_eng.enabled_triggers
            self.trigger_eng.enabled_triggers = {"VP_LIQUIDITY_REACTION"}
        try:
            trigger = self.trigger_eng.step2_trigger(
                df_m5, df_m1, liq, symbol, session_open,
                profile=vp_profile if self.va_fade_enabled else None,
                edge_tolerance_frac=DP.VP_EDGE_TOLERANCE_FRAC,
                poc_band_frac=DP.VP_POC_BAND_FRAC,
                vplr_ctx=vplr_ctx, m15_fvg_entry=m15_fvg_plan,
                htf_crt=self._crt_candidate(symbol, df_m5, df_m1, now) if not vp_only else None)
        finally:
            if vp_only:
                self.trigger_eng.enabled_triggers = saved

        if not trigger.detected:
            reason = ""
            if vplr_ctx is not None and getattr(trigger, "vplr", None) is None:
                # Record why the VP chain stopped, so the funnel is countable
                # without re-running the bar. Observation only (§13.10).
                probe = VPLR_DETECT(df_m5, df_m1, vplr_ctx, liq)
                reason = f"VPLR: {probe.reject_reason}"
            self.rejects.record("NO_TRIGGER", symbol, reason, now)
            return

        analysis_price = (trigger.entry_price if (trigger.fvg_entry or trigger.htf_crt) else
                          float(df_m5['close'].iloc[-1]))
        if trigger.matched_triggers and len(trigger.matched_triggers) > 1:
            logger.info(
                f"SA {symbol}: {trigger.trigger_type} selected on priority over "
                f"{', '.join(trigger.matched_triggers[1:])}"
            )
        if getattr(trigger, "vplr", None) is not None:
            logger.info(f"SA {symbol}: {trigger.vplr.summary()} | "
                        f"{getattr(vplr_ctx.anchored, 'summary', lambda: '')()}")

        # L-017 is a mandatory sequence check for every entry type. A local
        # sweep never substitutes for reclaiming broken support/resistance.
        if self.reclaim_fvg_enabled:
            if not self._reclaim_history_ready:
                self.rejects.record("RECLAIM_HISTORY_UNAVAILABLE", symbol,
                                    "cannot restore prior-entry barrier", now)
                return
            gate_tick = mt5.symbol_info_tick(symbol)
            if gate_tick is None:
                self.rejects.record("RECLAIM_NO_QUOTE", symbol, "no quote", now)
                return
            quote = gate_tick.ask if trigger.direction == "BULLISH" else gate_tick.bid
            trigger.reclaim = evaluate_reclaim(
                trigger.direction, df_m5, df_m1, quote,
                trigger.stop_loss, trigger.tp1, now,
                self._reclaim_last_closes.get((symbol, trigger.direction)))
            decision = trigger.reclaim
            logger.info(f"SA {symbol}: RECLAIM {'passed' if decision.allow else 'BLOCKED'} "
                        f"{trigger.direction} {trigger.trigger_type} | {decision.record()}")
            if not decision.allow:
                self.rejects.record(decision.reason, symbol,
                                    f"level={decision.level} {decision.record()}", now)
                return

        # ── H1 EMA(18) high/low band — directional permission ────────────────
        # Longs only while price is above both bands, shorts only while below
        # both, nothing while inside. Placed first among the post-detection
        # gates because it is the cheapest veto and because a trigger that
        # disagrees with H1 context should not consume the downstream budget.
        #
        # The frame is fetched closed-only, so the band is read on the last
        # completed H1 bar (MQL5 shift=1) and cannot repaint.
        if self.ema_band_enabled:
            df_h1_band = self._get_ohlcv(symbol, DP.EMA_BAND_TF,
                                         bars=DP.EMA_BAND_BARS)
            band = self.ema_filter.check(
                trigger_direction = trigger.direction,
                price             = analysis_price,
                df_h1_closed      = df_h1_band,
            )
            if not band.allow:
                logger.info(
                    f"SA {symbol}: EMA BAND BLOCKED {trigger.direction} "
                    f"{trigger.trigger_type} — {band.reason}"
                )
                self.rejects.record("EMA_BAND", symbol, band.reason, now)
                return
            logger.info(f"SA {symbol}: EMA BAND passed — {band.reason}")

        # ── Previous-day-range location gate ─────────────────────────────────
        # Refuse to buy the top quartile or sell the bottom quartile of the
        # previous day's range. Placed among the cheap vetoes: one D1 fetch of
        # 10 bars and two subtractions, less than the EMA band.
        #
        # The frame is fetched closed-only, so its last row is *yesterday's*
        # completed daily bar. Reading position 0 would hand the gate today's
        # still-forming daily bar, whose high and low grow through the session
        # — the level that permitted an entry could move before the day closed
        # and the decision could never be reproduced.
        if self.pdr_gate_enabled:
            df_pdr = self._get_ohlcv(symbol, DP.PDR_TF, bars=DP.PDR_BARS)
            pdr = self.pdr_gate.check(
                trigger_direction = trigger.direction,
                price             = float(df_m5['close'].iloc[-1]),
                df_d1_closed      = df_pdr,
            )
            if not pdr.allow:
                logger.info(
                    f"SA {symbol}: PDR GATE blocked {trigger.direction} "
                    f"{trigger.trigger_type} — {pdr.reason}"
                )
                self.rejects.record("PDR_GATE", symbol, pdr.reason, now)
                return
            logger.info(f"SA {symbol}: PDR GATE passed — {pdr.reason}")

        # ── Regime-direction gate ────────────────────────────────────────────
        # Refuse to fade a classified H1 trend. Cheap (one OHLCV fetch and a
        # 100-bar statistic), so it sits ahead of the profile build.
        if self.rd_gate_enabled:
            df_rd = self._get_ohlcv(symbol, DP.RD_REGIME_TF,
                                    bars=DP.RD_REGIME_BARS)
            rd = self.rd_gate.check(trigger.direction, df_rd)
            if not rd.allow:
                logger.info(
                    f"SA {symbol}: RD GATE blocked {trigger.direction} "
                    f"{trigger.trigger_type} — {rd.reason}"
                )
                self.rejects.record("RD_GATE", symbol, rd.reason, now)
                return

        # ── H4 value-area location gate ──────────────────────────────────────
        # Sell from VAH, buy from VAL, nothing at the POC. Placed here, after
        # the cheap directional band and before the short-term-bias gate,
        # because it costs two OHLCV fetches and a histogram build — more than
        # the EMA check, far less than the consultation.
        #
        # Both frames are fetched closed-only. A profile that included the
        # forming H4 bar would repaint: that bar's high and low are still
        # growing, so the VAH which permitted a short can move before the bar
        # closes and the decision could never be reproduced.
        if self.vp_gate_enabled:
            df_regime = self._get_ohlcv(symbol, DP.VP_REGIME_TF,
                                        bars=DP.VP_REGIME_BARS)
            vp = self.vp_gate.check(
                trigger_direction = trigger.direction,
                price             = float(df_m5['close'].iloc[-1]),
                df_h4_closed      = df_h4_vp,
                df_regime_closed  = df_regime,
            )
            if not vp.allow:
                logger.info(
                    f"SA {symbol}: VP GATE blocked {trigger.direction} "
                    f"{trigger.trigger_type} — {vp.reason}"
                )
                self.rejects.record("VP_GATE", symbol, vp.reason, now)
                return
            if vp.abstained:
                logger.debug(f"SA {symbol}: VP GATE abstained — {vp.reason}")
            else:
                logger.info(f"SA {symbol}: VP GATE passed — {vp.reason}")

        # ── VP_LEG_CONFLUENCE location filter (L-015) ───────────────────────
        # A veto only — direction, stop and target stay with the trigger.
        # Placed after the existing location gates and before STB, exactly as
        # in backtest_scalper, so both paths judge the same candidate set.
        if self.leg_conf_enabled:
            lc = leg_conf_evaluate(
                float(df_m5['close'].iloc[-1]), leg_pair, self.leg_conf_mode,
                DP.LEG_CONF_ZONE_ATR, DP.LEG_CONF_LVN_ATR)
            if not lc.admit:
                logger.info(
                    f"SA {symbol}: LEG CONFLUENCE blocked {trigger.direction} "
                    f"{trigger.trigger_type} — {lc.label} [{lc.pair_summary}]")
                self.rejects.record(lc.reason(), symbol, lc.label, now)
                return
            logger.info(
                f"SA {symbol}: LEG CONFLUENCE passed — {lc.label} "
                f"{'+'.join(lc.matched_levels) or 'no-level'}")

        # ── Task 6: Short-Term Bias Gate (replaces HTFBiasFilter) ─────────────
        # Three layers:
        #   1. Session liquidity awareness — "don't chase the sweep" rule
        #   2. Intraday M5 structure (the bot's short-term bias)
        #   3. HTF (tiebreaker for confidence, not a hard gate)
        # Specifically fixes the "London took Asia liquidity → bot went long"
        # scenario that produced today's losses.
        df_h1 = self._get_ohlcv(symbol, self.TF_H1, bars=120)
        df_h4 = self._get_ohlcv(symbol, self.TF_H4, bars=120)
        stb = self.stb_filter.check(
            symbol            = symbol,
            trigger_direction = trigger.direction,
            trigger_type      = trigger.trigger_type,
            current_price     = analysis_price,
            df_m5             = df_m5,
            df_h1             = df_h1,
            df_h4             = df_h4,
            now               = now,
        )
        if not stb.allow:
            sweep_str = f" | sweep={stb.recent_sweep}" if stb.recent_sweep else ""
            logger.info(
                f"SA {symbol}: STB GATE blocked {trigger.direction} "
                f"{trigger.trigger_type} — {stb.reason} | "
                f"short_term={stb.short_term_bias} htf={stb.htf_trend}{sweep_str}"
            )
            self.rejects.record("STB", symbol, stb.reason, now)
            return
        sweep_str = f" | sweep={stb.recent_sweep}" if stb.recent_sweep else ""
        logger.info(
            f"SA {symbol}: STB GATE passed [{stb.confidence}] — {stb.reason} | "
            f"short_term={stb.short_term_bias} htf={stb.htf_trend}{sweep_str}"
        )

        # Thin-liquidity filter — require HIGH STB confidence during
        # historically-low-liquidity UTC windows.
        utc_hour = now.hour
        in_thin = any(start <= utc_hour < end
                      for (start, end) in self.THIN_LIQUIDITY_HOURS_UTC)
        if in_thin and stb.confidence != self.THIN_LIQ_REQUIRED_CONFIDENCE:
            logger.info(
                f"SA {symbol}: THIN-LIQ BLOCKED — UTC hour {utc_hour:02d} "
                f"is thin-liquidity, requires {self.THIN_LIQ_REQUIRED_CONFIDENCE} "
                f"STB confidence, got {stb.confidence}."
            )
            self.rejects.record(
                "THIN_LIQ", symbol,
                f"hour {utc_hour:02d} needs {self.THIN_LIQ_REQUIRED_CONFIDENCE}, "
                f"got {stb.confidence}", now)
            return

        # ── SA Consultant Gates (Institutional Intelligence) ──────────────────
        tick_now = mt5.symbol_info_tick(symbol)
        c_price  = tick_now.bid if trigger.direction == "BULLISH" else tick_now.ask if tick_now else 0.0

        consult = self.consultant.consult(
            symbol          = symbol,
            trigger_type    = trigger.trigger_type,
            trigger_direction = trigger.direction,
            original_tp1    = trigger.tp1,
            original_tp2    = trigger.tp2,
            current_price   = c_price,
        )

        if not consult.success:
            logger.info(
                f"SA {symbol}: CONSUL BLOCKED — consultation unavailable ({consult.reason}). "
                f"Safe fallback = skip trade."
            )
            self.rejects.record("CONSULT_UNAVAILABLE", symbol, consult.reason, now)
            return

        if consult.stale_data:
            logger.info(
                f"SA {symbol}: CONSUL BLOCKED — stale council snapshot (>60s). "
                f"Safe fallback = skip trade."
            )
            self.rejects.record("CONSULT_STALE", symbol, "snapshot >60s", now)
            return

        if consult.latency_ms > self.CONSULT_MAX_LATENCY_MS:
            logger.info(
                f"SA {symbol}: CONSUL BLOCKED — latency {consult.latency_ms:.1f}ms "
                f"> {self.CONSULT_MAX_LATENCY_MS:.0f}ms."
            )
            self.rejects.record(
                "CONSULT_LATENCY", symbol,
                f"{consult.latency_ms:.0f}ms over budget", now)
            return

        if consult.success:
            # Gate 1 — Per-trigger regime whitelist (P1 inversion)
            #
            # Previous logic blanket-blocked MANIPULATION/STRESS for every
            # trigger and then allow-listed SWEEP_REJECTION via the
            # regime_blocks_trade flag — which was inverted from ICT reality
            # (fading sweeps is precisely the manipulation play). New logic
            # uses an explicit per-trigger whitelist defined as a class
            # constant; STRESS regime is always rejected.
            if consult.regime == "STRESS":
                logger.info(
                    f"SA {symbol}: CONSUL Gate 1 BLOCKED — STRESS regime rejects "
                    f"all triggers"
                )
                self.rejects.record("GATE1_REGIME", symbol,
                                    "STRESS rejects all triggers", now)
                return

            allowed = self.TRIGGER_REGIME_WHITELIST.get(trigger.trigger_type)
            if allowed is None:
                logger.info(
                    f"SA {symbol}: CONSUL Gate 1 BLOCKED — trigger "
                    f"{trigger.trigger_type} has no whitelisted regime (defensive deny)"
                )
                self.rejects.record(
                    "GATE1_REGIME", symbol,
                    f"{trigger.trigger_type} has no whitelist", now)
                return
            if consult.regime not in allowed:
                logger.info(
                    f"SA {symbol}: CONSUL Gate 1 BLOCKED — Regime={consult.regime} "
                    f"unsuitable for {trigger.trigger_type} (allowed: {sorted(allowed)})"
                )
                self.rejects.record(
                    "GATE1_REGIME", symbol,
                    f"{consult.regime} unsuitable for {trigger.trigger_type}", now)
                return
            logger.info(
                f"SA {symbol}: CONSUL Gate 1 passed | Regime={consult.regime} "
                f"matches {trigger.trigger_type}"
            )

            # Gate 2 — Displacement Validation (BOS_RETEST only)
            if trigger.requires_displacement and not consult.displacement_confirmed:
                logger.info(
                    f"SA {symbol}: CONSUL Gate 2 BLOCKED — BOS_RETEST has no institutional "
                    f"displacement (momentum_score too low). Skipping fakeout."
                )
                self.rejects.record("GATE2_DISPLACEMENT", symbol,
                                    "no institutional displacement", now)
                return

            # Gate 3 — LIA TP2 Override
            if consult.lia_tp2_override and trigger.fvg_entry is None and trigger.htf_crt is None:
                trigger.tp2 = consult.lia_tp2_override
                logger.info(
                    f"SA {symbol}: CONSUL Gate 3 — TP2 realigned to macro H1 pool "
                    f"{trigger.tp2:.5f} (was fixed R:R)"
                )
        # Step 3: Spread validation
        tick = mt5.symbol_info_tick(symbol)
        info = mt5.symbol_info(symbol)
        if not tick or not info:
            return
        spread_pips = (tick.ask - tick.bid) / info.point / 10
        sl_pips_for_check = abs(trigger.entry_price - trigger.stop_loss) / info.point / 10

        valid, reason = self.trigger_eng.step3_validate(
            trigger, spread_pips, symbol, sl_pips=sl_pips_for_check)
        if not valid:
            logger.info(
                f"SA {symbol}: STEP 3 BLOCKED — {reason} | "
                f"spread={spread_pips:.2f} pips trigger={trigger.trigger_type} "
                f"dir={trigger.direction}"
            )
            self.rejects.record("STEP3_VALIDATE", symbol, reason, now)
            return

        blackout = self.news_guard.active_blackout(now_utc=now, symbol=symbol)
        if blackout:
            logger.info(
                f"SA {symbol}: News blackout active for {blackout['impact']} event "
                f"'{blackout['title']}' ({blackout['window_start_utc']} -> {blackout['window_end_utc']})"
            )

        # SA-CRG check
        crg_decision = self.crg.check(
            sa_daily_loss_usd    = -self.pool.daily_pnl if self.pool.daily_pnl < 0 else 0,
            sa_max_daily_loss_usd= self.pool.max_daily_loss,
            sa_open_positions    = self.pool.open_positions,
            sa_consecutive_losses= self.pool.consecutive_losses,
            current_spread_pips  = spread_pips,
            max_spread_pips      = self.trigger_eng._max_spread(symbol),
            main_account_dd_pct  = main_dd,
            news_blackout_active = bool(blackout),
            now                  = now,
        )

        if not crg_decision.approved:
            logger.info(f"SA {symbol}: CRG BLOCKED — {crg_decision.blocked_reason}")
            self.rejects.record("CRG", symbol, crg_decision.blocked_reason, now)
            return

        # Execute (or dry-run)
        lot_size = self._calculate_lots(symbol, trigger)
        if lot_size <= 0:
            logger.info(
                f"SA {symbol}: LOT SIZE BLOCKED — calculated lots={lot_size} "
                f"(see prior warnings for cause: undercap / bad pip value / missing symbol_info)"
            )
            self.rejects.record("LOT_FLOOR", symbol,
                                f"lots={lot_size} below broker minimum", now)
            return

        logger.info(
            f"SA SETUP: {symbol} | {trigger.direction} {trigger.trigger_type} | "
            f"Entry={trigger.entry_price:.4f} | SL={trigger.stop_loss:.4f} | "
            f"TP1={trigger.tp1:.4f} | TP2={trigger.tp2:.4f} | Lots={lot_size:.2f}"
        )

        if self.dry_run:
            logger.info("SA DRY RUN: Signal generated but not executed.")
            self._log_trade(symbol, trigger, sess, lot_size, ticket=None)
            return

        ticket = self._execute_trade(symbol, trigger, lot_size)
        if not ticket:
            self.rejects.record("EXECUTE_FAIL", symbol,
                                "broker did not return a ticket", now)
        if ticket:
            self.pool.register_open()
            self._open_trades[ticket] = {
                "symbol": symbol,
                "direction": trigger.direction,
                "entry": trigger.entry_price,
                "sl": trigger.stop_loss,
                "tp1": trigger.tp1,
                "tp2": trigger.tp2,
                "open_time": now,
                "tp1_hit": False,
                # Context captured at entry so the close handler can journal a
                # complete record without re-deriving market state.
                "trigger_type": trigger.trigger_type,
                "matched_triggers": list(trigger.matched_triggers),
                "confidence": trigger.confidence,
                # Gate state as it stood at entry. `stb` is already in scope
                # here -- these values were computed by the STB gate above,
                # printed to the log, and then dropped. Persisting them is what
                # makes "did this trade agree with the HTF read?" answerable.
                # Observation-only: nothing reads these back. CLAUDE.md 13.10.
                "stb_confidence":  stb.confidence,
                "short_term_bias": stb.short_term_bias,
                "htf_trend":       stb.htf_trend,
                "recent_sweep":    stb.recent_sweep or "",
                "config_era":      DP.CONFIG_ERA,
                # `vp_window_name` resolves to VP_ASIA when the trade was taken
                # under the VP-only allowance; `sess.window_name` would say
                # IDLE there, which is true of the normal windows and useless
                # for attributing the trade.
                "session": self.session.vp_window_name(now),
                "vplr": _vplr_record(trigger),
                "reclaim": trigger.reclaim.record() if trigger.reclaim else None,
                "m15_fvg": trigger.fvg_entry.record() if trigger.fvg_entry else None,
                "htf_crt": trigger.htf_crt.record() if trigger.htf_crt else None,
                "regime": consult.regime,
                "lots": lot_size,
                # Cost and risk as they stood at entry. The forensic pass
                # cannot reconstruct these after the fact — the spread that
                # mattered is the one that was quoted here, not the one
                # quoted two hours later when the trade is analysed.
                "spread_pips": spread_pips,
                "sl_pips": sl_pips_for_check,
                "risk_usd": self.pool.risk_per_trade_usd,
            }
            self._log_trade(symbol, trigger, sess, lot_size, ticket)

    # ── Trade Execution ───────────────────────────────────────────────────────

    def _execute_trade(self, symbol: str, trigger, lot_size: float) -> Optional[int]:
        """Place market order via MT5."""
        try:
            tick = mt5.symbol_info_tick(symbol)
            info = mt5.symbol_info(symbol)
            if not tick or not info:
                return None

            order_type = mt5.ORDER_TYPE_BUY if trigger.direction == "BULLISH" else mt5.ORDER_TYPE_SELL
            price      = tick.ask if trigger.direction == "BULLISH" else tick.bid
            point      = info.point

            crt_plan = getattr(trigger, "htf_crt", None)
            fvg_plan = crt_plan or getattr(trigger, "fvg_entry", None)
            quote_guard = ((lambda p, q: crt_quote_allowed(p, q, datetime.now(timezone.utc)))
                           if crt_plan else entry_quote_allowed)
            family = "CRT" if crt_plan else "M15_FVG"
            if fvg_plan is not None and not quote_guard(fvg_plan, price):
                logger.warning(f"SA {symbol}: {family}_QUOTE_MOVED — skip entry at {price}")
                return None
            if fvg_plan is not None:
                quoted_trigger = replace(trigger, entry_price=price)
                valid, why = self.trigger_eng.step3_validate(
                    quoted_trigger, (tick.ask-tick.bid)/point/10., symbol,
                    sl_pips=abs(price-trigger.stop_loss)/point/10.)
                if not valid:
                    logger.warning(f"SA {symbol}: {family}_FINAL_COST_GATE — {why}")
                    return None

            if getattr(self, "reclaim_fvg_enabled", False):
                permission = getattr(trigger, "reclaim", None)
                if permission is None or not entry_in_reclaim_zone(permission, price):
                    logger.warning(f"SA {symbol}: RECLAIM_QUOTE_MOVED — skip entry at {price}")
                    return None

            # Round SL/TP to symbol digits
            digits = info.digits
            sl = round(trigger.stop_loss, digits)
            tp = round(trigger.tp1, digits)

            request = {
                "action":    mt5.TRADE_ACTION_DEAL,
                "symbol":    symbol,
                "volume":    lot_size,
                "type":      order_type,
                "price":     price,
                "sl":        sl,
                "tp":        tp,
                "deviation": 10,
                "magic":     MAGIC_SCALPER,
                "comment":   (DP.CRT_ORDER_PREFIX + crt_plan.setup_id if crt_plan else
                              DP.M15_FVG_ORDER_COMMENT if fvg_plan else
                              f"SA_{trigger.trigger_type[:4]}"),
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }

            result = mt5.order_send(request)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                if crt_plan:
                    self._crt_used.add(crt_plan.setup_id)
                logger.info(f"SA Trade executed: {symbol} {trigger.direction} "
                            f"Lot={lot_size:.2f} Ticket={result.order}")
                return result.order
            else:
                logger.error(f"SA Trade failed: {result.comment if result else 'No result'}")
                return None
        except Exception as e:
            logger.error(f"SA Execute error: {e}")
            return None

    def _monitor_open_trades(self):
        """Check open SA trades for 120-minute timeout and TP1 partial close."""
        now = datetime.now(timezone.utc)
        closed_tickets = []

        for ticket, info in self._open_trades.items():
            pos = mt5.positions_get(ticket=ticket)
            if not pos:
                # Position already closed (by SL/TP hit, or by the Guardian).
                # Book it only against settled deal history; an unsettled one
                # stays tracked and this loop retries it next cycle.
                if self._book_settled_close(ticket, info, now):
                    closed_tickets.append(ticket)
                continue

            pos = pos[0]
            elapsed = (now - info["open_time"]).total_seconds() / 60

            # Hard timeout, expressed in trigger-timeframe bars (see TIMEOUT_BARS).
            if elapsed >= self.timeout_minutes:
                logger.warning(
                    f"SA: Ticket {ticket} timeout after {elapsed:.0f}m "
                    f"({self.TIMEOUT_BARS} x {self.TRIGGER_TF_MINUTES}m bars) "
                    f"— closing at market"
                )
                if not self._close_at_market(ticket, pos.symbol, pos.type,
                                             pos.volume, reason="TIMEOUT"):
                    # Still open at the broker. Leave it tracked — the timeout
                    # condition holds, so the next cycle retries the close.
                    continue
                # Reconcile from deal history rather than trusting pos.profit,
                # which excludes commission and swap and disagrees with the
                # figure the normal close path books for the same event. The
                # position is gone at the broker now, so an unsettled read just
                # defers to the next cycle rather than booking an estimate.
                if self._book_settled_close(ticket, info, now,
                                            outcome="TIMEOUT"):
                    closed_tickets.append(ticket)

        for t in closed_tickets:
            self._open_trades.pop(t, None)

    def _book_settled_close(self, ticket: int, info: dict, now: datetime,
                            outcome: Optional[str] = None,
                            force: bool = False) -> bool:
        """
        Book a vanished position against the broker's settled deal history.

        Returns True when the trade has been booked and the ticket may be
        untracked; False means "not priced yet, keep it and try again".

        This applies to the P&L the rule `_close_at_market` already applies to
        the close itself: what the broker has not confirmed is not booked. The
        three call sites used to fabricate a figure instead — `0.0`, or the
        floating `pos.profit` — and then derive the WIN/LOSS label from the
        fabrication. One such $0.00 was never neutral:

          * `pool.register_close(0.0)` leaves the pool balance wrong, which
            sizes every later trade, and *resets* the consecutive-loss counter
            (`0.0 < 0` is False).
          * `cooldown.record_trade_result(0.0)` arms the LOSS cooldown
            (`0.0 > 0` is False) — up to an hour of held trading on a trade
            that may have won.
          * the trade log recorded `LOSS` at $0.00, and `log_close` only wrote
            into records still marked OPEN, so the verdict was permanent.

        Against the broker, 140 of 282 logged closes carried $0.00 and 131 of
        the 187 that could be repriced disagreed with the log — +$947.93 /
        52.78% WR actual against +$61.80 / 27.66% as booked (REMEDY_LEDGER
        L-004). `force` is for the daily reset, where there is no next cycle.
        """
        # A deferred close keeps the exit profile it was closed under. Without
        # this, a TIMEOUT or EOD_CLOSE that waited a cycle for its deal would
        # return through the "position is gone" branch carrying no outcome and
        # be relabelled WIN/LOSS — and CLAUDE.md §13.3 requires every exit
        # profile to stay separately measurable, because a profile that cannot
        # be measured cannot be eliminated.
        outcome = outcome or info.get("close_outcome")

        pnl = self._settled_pnl(ticket)
        if pnl is not None:
            self._on_trade_closed(
                ticket, outcome or ("WIN_TP1" if pnl > 0 else "LOSS"),
                pnl, now)
            return True

        waiting_since = info.setdefault("settle_wait_since", now)
        waited_min = (now - waiting_since).total_seconds() / 60
        if not force and waited_min < self.PNL_RECONCILE_GRACE_MIN:
            if outcome:
                info["close_outcome"] = outcome
            logger.warning(
                f"SA: ticket {ticket} is gone but its closing deal has not "
                f"settled ({waited_min:.1f}m of "
                f"{self.PNL_RECONCILE_GRACE_MIN}m grace) — not booked; "
                f"retrying next cycle."
            )
            return False

        logger.error(
            f"SA: ticket {ticket} closed with no closing deal in history after "
            f"{waited_min:.1f}m — booking {OUTCOME_UNRECONCILED} at $0.00 to "
            f"release the position slot. The pool balance is now understated "
            f"by this trade's true P&L; reconcile with get_agent_pnl.py."
        )
        self._on_trade_closed(ticket, OUTCOME_UNRECONCILED, 0.0, now,
                              settled=False)
        return True

    def _on_trade_closed(self, ticket: int, outcome: str, pnl: float,
                         now: Optional[datetime] = None,
                         settled: bool = True):
        """
        Single place where a closed SA trade updates every piece of state.

        Previously the pool ledger, the trade log and (nothing else) were
        updated at four separate call sites with two different P&L definitions.
        Cooldown arming and journal persistence now happen here too, so no exit
        path can silently skip them.

        `settled=False` means the P&L is a placeholder, not a measurement. The
        ledger and the trade log still see it — the position slot has to be
        released and an operator has to be able to find the trade — but the two
        consumers that would convert it into a false finding are skipped:
        `postmortem.classify` has no branch for the label and would fall
        through to its loss rules, and the AdaptiveMemory journal is what SEE
        reads for strategy trust. The cooldown *is* armed: an unpriced close is
        the one case where pausing is the conservative reading.
        """
        now = now or datetime.now(timezone.utc)
        if (getattr(self, "reclaim_fvg_enabled", False)
                or getattr(self, "m15_fvg_entry_enabled", False)
                or getattr(self, "htf_crt_enabled", False)):
            info = self._open_trades.get(ticket)
            if info:
                self._reclaim_last_closes[(info["symbol"], info["direction"])] = now
        self.pool.register_close(pnl)
        self.trade_log.log_close(ticket, outcome, pnl,
                                 self.state_mach.state.value)
        self.cooldown.record_trade_result(pnl, now)
        self._last_close_time = now
        self._last_close_pnl = pnl
        if not settled:
            return
        self._journal_closed_trade(ticket, outcome, pnl, now)
        self._queue_postmortem(ticket, outcome, pnl, now)

    # ── Self-diagnosis: closed-trade forensics ────────────────────────────────

    def _queue_postmortem(self, ticket: int, outcome: str, pnl: float,
                          now: datetime):
        """
        Park a closed trade for forensic analysis once its look-ahead window
        exists. Deliberately deferred: the question "was the stop clipped or
        was the read wrong?" is answered by bars that have not printed at the
        moment of close.
        """
        try:
            info = self._open_trades.get(ticket, {})
            if not info:
                return
            self._pending_postmortems.append({
                "ctx": PMORTEM.TradeContext(
                    ticket=ticket,
                    symbol=info.get("symbol", "UNKNOWN"),
                    direction=info.get("direction", "UNKNOWN"),
                    entry=float(info.get("entry", 0.0) or 0.0),
                    stop_loss=float(info.get("sl", 0.0) or 0.0),
                    tp1=float(info.get("tp1", 0.0) or 0.0),
                    outcome=outcome,
                    pnl_usd=float(pnl),
                    open_time=info.get("open_time", now),
                    close_time=now,
                    trigger_type=info.get("trigger_type", "UNKNOWN"),
                    session=info.get("session", "UNKNOWN"),
                    regime=info.get("regime", "UNKNOWN"),
                    confidence=str(info.get("confidence", "UNKNOWN")),
                    lots=float(info.get("lots", 0.0) or 0.0),
                    risk_usd=float(info.get("risk_usd", 0.0) or 0.0),
                    spread_pips=float(info.get("spread_pips", 0.0) or 0.0),
                    sl_pips=float(info.get("sl_pips", 0.0) or 0.0),
                    # Absent on adopted positions -- correctly reports UNKNOWN
                    # rather than fabricating a value.
                    stb_confidence=str(info.get("stb_confidence", "UNKNOWN")),
                    short_term_bias=str(info.get("short_term_bias", "UNKNOWN")),
                    htf_trend=str(info.get("htf_trend", "UNKNOWN")),
                    recent_sweep=str(info.get("recent_sweep", "") or ""),
                    config_era=str(info.get("config_era", "UNKNOWN")),
                ),
                "due": now + timedelta(
                    minutes=self.PM_LOOKAHEAD_BARS * self.CONFIRM_TF_MINUTES),
            })
        except Exception as e:
            logger.warning(f"SA: could not queue postmortem for {ticket}: {e}")

    def _drain_postmortems(self, now: datetime, force: bool = False):
        """
        Run every forensic pass whose look-ahead window has filled.

        `force` runs the remainder early — used at shutdown, where a partial
        look-ahead beats losing the record entirely. The pass stamps how many
        bars it actually saw (`evidence.lookahead_available`), so a truncated
        analysis is identifiable rather than silently under-counting
        STOP_TOO_TIGHT.
        """
        if not self._pending_postmortems:
            return
        still_pending = []
        for item in self._pending_postmortems:
            if not force and now < item["due"]:
                still_pending.append(item)
                continue
            ctx = item["ctx"]
            try:
                held_bars = int(
                    (ctx.close_time - ctx.open_time).total_seconds() / 60
                    / self.CONFIRM_TF_MINUTES)
                need = held_bars + self.PM_LOOKAHEAD_BARS + 10
                df = self._get_ohlcv(ctx.symbol, self.TF_CONFIRM,
                                     bars=max(60, need))
                pm = PMORTEM.analyse(ctx, df,
                                     bar_minutes=self.CONFIRM_TF_MINUTES,
                                     lookahead_bars=self.PM_LOOKAHEAD_BARS)
                self.incidents.record(pm)
                logger.info(f"SA POSTMORTEM: {pm.headline}")
            except Exception as e:
                logger.warning(
                    f"SA: postmortem failed for ticket {ctx.ticket}: {e}")
        self._pending_postmortems = still_pending

    def _refresh_news_if_due(self, now: datetime):
        """
        Re-fetch the FF calendar every NEWS_REFRESH_HOURS, backing off
        NEWS_RETRY_MINUTES after a failure.

        The failure path used to leave `_last_news_fetch` unset, so the 6-hour
        throttle never engaged: one download attempt per 30s cycle, 643 of them
        and ~1900 log lines in a single observed session.
        """
        if self._last_news_attempt is not None:
            since_attempt = (now - self._last_news_attempt).total_seconds() / 60
            if since_attempt < self.NEWS_RETRY_MINUTES:
                return
        if self._last_news_fetch is not None:
            elapsed_hours = (now - self._last_news_fetch).total_seconds() / 3600
            if elapsed_hours < self.NEWS_REFRESH_HOURS:
                return

        self._last_news_attempt = now
        try:
            count = fetch_news_events()
            if count >= 0:
                self._last_news_fetch = now
                logger.info(f"SA News refreshed: {count} HIGH-impact events")
                return
            logger.warning(f"SA News refresh failed — keeping existing file; "
                           f"next attempt in {self.NEWS_RETRY_MINUTES} min")
        except Exception as e:
            logger.error(f"SA News refresh error: {e}")
        self._warn_if_news_stale(now)

    def _warn_if_news_stale(self, now: datetime):
        """
        Say out loud when the blackout gate is running on an old calendar.

        This warns rather than blocks. Refusing to trade on a stale file would
        be a new gate in the live decision path, and every such gate has to be
        mirrored into backtest_scalper.py (invariant #2) — that is a separate,
        measurable change, not a bug fix.
        """
        try:
            path = Path(__file__).parent / "news_events.json"
            if not path.exists():
                logger.error("SA News: news_events.json is MISSING — the HIGH-"
                             "impact blackout gate is blind.")
                return
            age_h = (now.timestamp() - path.stat().st_mtime) / 3600
            if age_h >= self.NEWS_STALE_HOURS:
                logger.error(
                    f"SA News: calendar is {age_h:.1f}h old (ceiling "
                    f"{self.NEWS_STALE_HOURS}h). The blackout gate may not "
                    f"cover today's events — trading continues UNGUARDED "
                    f"against news."
                )
        except OSError as e:
            logger.warning(f"SA News: could not stat calendar file: {e}")

    def _enforce_eod_close(self, now: datetime):
        """Close all open SA trades after 23:00 UTC so nothing carries to next day."""
        if now.hour < 23:
            return
        if not self._open_trades:
            return

        logger.warning(
            f"SA: EOD enforcement at {now.strftime('%H:%M')} UTC — "
            f"closing {len(self._open_trades)} open trade(s)"
        )
        self._force_close_all(now, "EOD_CLOSE")

    def _force_close_all(self, now: datetime, reason: str,
                         force_settle: bool = False) -> int:
        """
        Close and book every tracked position. Returns how many are still open.

        The one implementation shared by the 23:00 EOD sweep and the midnight
        reset. Only broker-confirmed closes are booked and untracked; anything
        that fails to close stays in `_open_trades` so the next cycle retries
        it and the position never becomes invisible to the agent. The same rule
        governs the P&L: a close the broker has not yet priced is left unbooked
        for the next cycle. `force_settle` switches that off for the midnight
        reset, where there is no next cycle to carry the trade into.
        """
        closed_tickets = []
        for ticket, info in list(self._open_trades.items()):
            pos = mt5.positions_get(ticket=ticket)
            if not pos:
                if self._book_settled_close(ticket, info, now, outcome=reason,
                                            force=force_settle):
                    closed_tickets.append(ticket)
                continue

            pos = pos[0]
            if not self._close_at_market(ticket, pos.symbol, pos.type,
                                         pos.volume, reason=reason):
                # Leave it tracked and booked as open. The most likely cause is
                # a market that has already shut for the week — gold stops
                # trading before 23:00 UTC on a Friday — in which case the
                # position genuinely carries, and pretending otherwise would
                # hide weekend gap exposure.
                logger.error(
                    f"SA {reason}: ticket {ticket} could NOT be closed and "
                    f"remains open. It stays tracked; the next cycle retries."
                )
                continue

            if self._book_settled_close(ticket, info, now, outcome=reason,
                                        force=force_settle):
                closed_tickets.append(ticket)
                logger.info(f"SA {reason}: Ticket {ticket} closed at market")

        for t in closed_tickets:
            self._open_trades.pop(t, None)
        return len(self._open_trades)

    def _close_at_market(self, ticket: int, symbol: str, pos_type: int,
                         volume: float, reason: str = "TIMEOUT") -> bool:
        """
        Close a position at market. Returns True only when the broker has
        confirmed the position is gone.

        This used to discard the `order_send` result entirely, and both callers
        booked the trade regardless. A rejection — market closed on a Friday
        23:00 UTC EOD sweep, a requote, insufficient margin — therefore wrote a
        fabricated P&L into the pool ledger, armed a cooldown, and deleted the
        only in-process record of a position that was still open at the broker.
        Returning a verified bool lets the caller leave the ticket tracked and
        retry on the next cycle.

        `reason` becomes the broker-side deal comment. Every close previously
        said "SA_TIMEOUT" regardless of why it fired, which made the exit
        profiles indistinguishable in deal history — and per RESEARCH_NOTES
        §13.3 an exit profile that cannot be measured cannot be eliminated.
        """
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            logger.error(f"SA close FAILED ticket={ticket}: no tick for {symbol} "
                         f"— position left open and still tracked")
            return False

        close_type = mt5.ORDER_TYPE_SELL if pos_type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price      = tick.bid if pos_type == mt5.ORDER_TYPE_BUY else tick.ask
        request = {
            "action":   mt5.TRADE_ACTION_DEAL,
            "symbol":   symbol,
            "volume":   volume,
            "type":     close_type,
            "price":    price,
            "position": ticket,
            "deviation": 20,
            "magic":    MAGIC_SCALPER,
            "comment":  f"SA_{reason}"[:31],
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result  = mt5.order_send(request)
        retcode = getattr(result, "retcode", None)
        if retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(
                f"SA close FAILED ticket={ticket} {symbol} reason={reason} "
                f"retcode={retcode} "
                f"comment={getattr(result, 'comment', 'no result object')} "
                f"— position remains open and stays tracked"
            )
            return False

        # A DONE retcode is not proof the position is gone: an IOC close can
        # fill partially and leave a residual position behind. Confirm against
        # the broker, allowing a moment for the terminal to settle.
        for attempt in range(self.CLOSE_CONFIRM_ATTEMPTS):
            if not mt5.positions_get(ticket=ticket):
                logger.info(f"SA close confirmed: ticket={ticket} {symbol} "
                            f"reason={reason}")
                return True
            if attempt < self.CLOSE_CONFIRM_ATTEMPTS - 1:
                time.sleep(self.CLOSE_CONFIRM_DELAY_S)

        logger.error(
            f"SA close UNCONFIRMED ticket={ticket} {symbol} reason={reason}: "
            f"order accepted but position still open (partial fill?) "
            f"— stays tracked, retrying next cycle"
        )
        return False

    # ── Lot Sizing ────────────────────────────────────────────────────────────

    def _calculate_lots(self, symbol: str, trigger) -> float:
        """Size based on SA pool risk, not main account."""
        info = mt5.symbol_info(symbol)
        if not info:
            return 0.0

        risk_usd  = self.pool.risk_per_trade_usd
        sl_pips   = abs(trigger.entry_price - trigger.stop_loss) / info.point / 10
        pip_value = info.trade_tick_value * 10    # Value of 1 pip per lot

        if sl_pips <= 0 or pip_value <= 0:
            logger.warning(f"SA {symbol}: Invalid SL distance or pip value. Trade rejected.")
            return 0.0

        lots = risk_usd / (sl_pips * pip_value)
        
        # FIX: Reject trade if mathematically required lot size is below broker minimum
        if lots < info.volume_min:
            logger.warning(
                f"SA {symbol}: Trade REJECTED (Undercapitalized). "
                f"Required lots ({lots:.5f}) < Broker Min ({info.volume_min}). "
                f"Forcing minimum would risk more than ${risk_usd:.2f}."
            )
            return 0.0

        lots = min(info.volume_max, lots)
        lots = round(lots / info.volume_step) * info.volume_step
        return round(lots, 2)

    # ── Logging ───────────────────────────────────────────────────────────────

    def _log_trade(self, symbol: str, trigger, sess, lot_size: float,
                   ticket: Optional[int]):
        """Create and log a SATradeRecord."""
        record = SATradeRecord(
            instrument       = symbol,
            session_window   = sess.window_name,
            trigger_type     = trigger.trigger_type,
            entry_price      = trigger.entry_price,
            stop_loss        = trigger.stop_loss,
            tp1_target       = trigger.tp1,
            tp2_target       = trigger.tp2,
            position_size    = lot_size,
            sa_pool_risk_pct = round(self.pool.risk_pct * 100, 2),
            sa_state_after   = self.state_mach.state.value,
            ticket           = ticket,
            notes            = ("HTF_CRT_SWEEP " + json.dumps(trigger.htf_crt.record())
                                if getattr(trigger, "htf_crt", None) else
                                "M15_FVG_ENTRY " + json.dumps(trigger.fvg_entry.record())
                                if getattr(trigger, "fvg_entry", None) else ""),
        )
        self.trade_log.log_open(record)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _crt_frames(self, symbol, now):
        stamp = pd.Timestamp(now).floor("15min")
        cached = self._crt_frames_cache.get(symbol)
        if cached is None or cached[0] != stamp:
            frames = {tf: self._get_ohlcv(symbol, code, bars=DP.CRT_RANGE_BARS)
                      for tf, code in DP.CRT_TIMEFRAMES.items()}
            self._crt_frames_cache[symbol] = (stamp, frames)
        return self._crt_frames_cache[symbol][1]

    def _crt_plans(self, symbol, m15, m5, now):
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return []
        return watch_crt(m15, m5, self._crt_frames(symbol, now), tick.bid, tick.ask,
                         now, self._crt_used, self._reclaim_last_closes, symbol,
                         getattr(self, "crt_confluence_mode", DP.CRT_CONFLUENCE_MODE))

    def _watch_htf_crt(self, symbol, now):
        stamp = pd.Timestamp(now).floor("15min")
        if self._crt_watch_bar.get(symbol) == stamp:
            return
        plans = self._crt_plans(
            symbol, self._get_ohlcv(symbol, self.TF_TRIGGER, bars=DP.TRIGGER_BARS),
            self._get_ohlcv(symbol, self.TF_CONFIRM, bars=DP.CONFIRM_BARS), now)
        # A missing first tick/bar is transient; retry on the next loop rather
        # than suppressing observation for the entire quarter-hour.
        if plans and all(p.reason != "CRT_LTF_DATA_UNAVAILABLE" for p in plans):
            self._crt_watch_bar[symbol] = stamp
        for p in plans:
            logger.info(f"SA {symbol}: HTF CRT WATCH | {p.record()}")

    def _crt_candidate(self, symbol, m15, m5, now):
        if not getattr(self, "htf_crt_enabled", False):
            return None
        if not self._reclaim_history_ready:
            self.rejects.record("CRT_HISTORY_UNAVAILABLE", symbol, "entry history unavailable", now)
            return None
        plans = self._crt_plans(symbol, m15, m5, now)
        for plan in plans:
            if plan.confluence and plan.confluence["baseline_ready"]:
                logger.info(f"SA {symbol}: CRT CONFLUENCE | {plan.record()}")
                if not plan.allow:
                    self.rejects.record(plan.reason, symbol, str(plan.confluence), now)
        p = select_crt(plans)
        if p.allow:
            logger.info(f"SA {symbol}: HTF CRT candidate | {p.record()}")
        elif p.reason == "CRT_DIRECTION_CONFLICT":
            self.rejects.record(p.reason, symbol, "opposing ready HTF setups", now)
        return p

    def _journal_closed_trade(self, ticket: int, outcome: str, pnl: float,
                              now: datetime):
        """
        Persist the closed trade to the shared AdaptiveMemory journal.

        Until now `AdaptiveMemory.log_trade()` had no callers anywhere in the
        codebase and `data/trade_journal.db` held zero rows, which meant SEE
        always voted "no history" and the meta-fund never rebalanced. The
        scalper is the highest-volume producer of closed trades, so it feeds
        the journal here. Failures are swallowed: journalling must never be
        able to break the trading loop.
        """
        info = self._open_trades.get(ticket, {})
        try:
            from intelligence.adaptive_memory import AdaptiveMemory, TradeRecord

            if getattr(self, "_memory", None) is None:
                self._memory = AdaptiveMemory()

            entry = float(info.get("entry", 0.0) or 0.0)
            sl = float(info.get("sl", 0.0) or 0.0)
            risk = abs(entry - sl)
            outcome_label = ("WIN" if pnl > 0 else
                             ("BREAKEVEN" if pnl == 0 else "LOSS"))
            open_time = info.get("open_time", now)

            self._memory.log_trade(TradeRecord(
                symbol=info.get("symbol", "UNKNOWN"),
                direction="BUY" if info.get("direction") == "BULLISH" else "SELL",
                model_type=info.get("trigger_type", "SCALP"),
                entry_price=entry,
                exit_price=0.0,
                stop_loss=sl,
                take_profit=float(info.get("tp1", 0.0) or 0.0),
                lot_size=float(info.get("lots", 0.0) or 0.0),
                profit_loss=float(pnl),
                pips=0.0,
                risk_reward_actual=(float(pnl) / risk) if risk else 0.0,
                outcome=outcome_label,
                regime=info.get("regime", "UNKNOWN"),
                session=info.get("session", "UNKNOWN"),
                confidence=0.0,
                clarity=info.get("confidence", "UNKNOWN"),
                open_time=open_time.isoformat() if hasattr(open_time, "isoformat")
                          else str(open_time),
                close_time=now.isoformat(),
                notes=f"SA {outcome} ticket={ticket}",
            ))
        except Exception as e:
            logger.warning(f"SA: journal write failed for ticket {ticket}: {e}")

    def _get_ohlcv(self, symbol: str, tf: int, bars: int = 100,
                   closed_only: bool = True) -> Optional[pd.DataFrame]:
        """
        Completed bars for `symbol` on timeframe `tf`.

        Position 0 of `copy_rates_from_pos` is the bar currently forming, whose
        high, low and close still move. Every decision frame therefore starts
        at position 1 — MQL5's shift=1 convention.

        This used to start at position 0, which was the largest single reason
        live results and backtest results disagreed: the simulator deliberately
        excludes the forming bar (it cannot see inside one), so the live agent
        was reading a half-built candle that the simulator never showed its own
        gates. It also made live entries unreproducible — a trigger detected on
        a forming bar can vanish before that bar closes, so neither a backtest
        nor the operator's chart could confirm the decision afterwards.

        Pass closed_only=False only for price display, never for a decision.
        """
        mt5.symbol_select(symbol, True)
        start = 1 if closed_only else 0
        rates = mt5.copy_rates_from_pos(symbol, tf, start, bars)
        if rates is None or len(rates) < 10:
            return None
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
        return df

    def _get_main_dd(self, acct) -> float:
        """Get main account drawdown % (read-only, no write back to main system)."""
        if acct and acct.balance > 0:
            return max(0.0, (acct.balance - acct.equity) / acct.balance * 100)
        return 0.0

    SA_STATE_FILE = Path(__file__).parent / "data" / "sa_pool_state.json"
    SA_MAGIC      = MAGIC_SCALPER

    def _load_or_init_inception(self) -> datetime:
        """
        Persistent inception timestamp for the SA pool. Used as the lower
        bound of the cumulative-PnL history query so current_pool reflects
        every SA trade since the pool was first allocated.
        """
        try:
            self.SA_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            if self.SA_STATE_FILE.exists():
                state = json.loads(self.SA_STATE_FILE.read_text(encoding="utf-8"))
                iso = state.get("inception_utc")
                if iso:
                    return datetime.fromisoformat(iso)
            inception = datetime.now(timezone.utc)
            self.SA_STATE_FILE.write_text(
                json.dumps({"inception_utc": inception.isoformat()}, indent=2),
                encoding="utf-8",
            )
            logger.info(f"SA pool inception recorded: {inception.isoformat()}")
            return inception
        except Exception as e:
            logger.warning(f"SA: inception state read/write failed ({e}); using now()")
            return datetime.now(timezone.utc)

    @staticmethod
    def _deal_pnl(d) -> float:
        return (getattr(d, "profit", 0.0)
                + getattr(d, "commission", 0.0)
                + getattr(d, "swap", 0.0))

    def _restore_pool_from_mt5(self):
        """
        Seed pool state from MT5 deal history for magic 88880:
          - daily_pnl       : sum from today's 00:00 UTC to now
          - cumulative_pnl  : sum from pool inception (persisted) to now
          - trades_today    : count of DEAL_ENTRY_IN deals today
          - consecutive_losses : streak from chronological close deals today
        Without this, the dashboard shows $0 daily PnL on every restart and
        current_pool snaps back to initial_pool.
        """
        try:
            now = datetime.now(timezone.utc)
            start_today = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
            inception   = self._load_or_init_inception()

            # Cumulative deals (inception → now)
            all_deals = mt5.history_deals_get(inception, now)
            if all_deals is None:
                raise RuntimeError("broker history unavailable; entry barrier cannot be restored")
            self._reclaim_last_closes = close_barriers(all_deals, self.SA_MAGIC)
            self._crt_used = used_setups(all_deals, self.SA_MAGIC)
            logger.info(f"SA HTF CRT={'ON' if self.htf_crt_enabled else 'OFF'} | "
                        f"confluence={self.crt_confluence_mode} | "
                        f"priority=MN1/W1/D1 | restored {len(self._crt_used)} used setups")
            self._reclaim_history_ready = True
            logger.info(f"SA RECLAIM gate={'ON' if self.reclaim_fvg_enabled else 'OFF'} | "
                        f"restored {len(self._reclaim_last_closes)} directional close barriers")
            logger.info(f"SA M15 FVG entry={'ON' if self.m15_fvg_entry_enabled else 'OFF'} | "
                        "target before broken structure; Guardian TP extension disabled for this model")
            sa_all = [d for d in all_deals if getattr(d, "magic", 0) == self.SA_MAGIC]
            cumulative_pnl = sum(self._deal_pnl(d) for d in sa_all)

            # Today's slice
            sa_today = [d for d in sa_all
                        if getattr(d, "time", 0) >= start_today.timestamp()]
            daily_pnl = sum(self._deal_pnl(d) for d in sa_today)

            try:
                entry_in = mt5.DEAL_ENTRY_IN
                trades_today = sum(1 for d in sa_today
                                   if getattr(d, "entry", None) == entry_in)
            except Exception:
                trades_today = 0

            last_loss_close_time = None
            try:
                entry_out = mt5.DEAL_ENTRY_OUT
                closes = sorted(
                    [d for d in sa_today if getattr(d, "entry", None) == entry_out],
                    key=lambda d: getattr(d, "time", 0),
                )
                if closes:
                    newest = closes[-1]
                    self._last_close_time = datetime.fromtimestamp(
                        getattr(newest, "time", 0), tz=timezone.utc)
                    self._last_close_pnl = self._deal_pnl(newest)
                streak = 0
                last_loss_ts = 0
                for d in closes:
                    if self._deal_pnl(d) < 0:
                        streak += 1
                        last_loss_ts = getattr(d, "time", 0)
                    else:
                        streak = 0
                        last_loss_ts = 0
                consecutive_losses = streak
                if last_loss_ts:
                    last_loss_close_time = datetime.fromtimestamp(
                        last_loss_ts, tz=timezone.utc)
            except Exception:
                consecutive_losses = 0

            requested = self.pool.initial_pool
            self.pool.restore(
                daily_pnl          = daily_pnl,
                cumulative_pnl     = cumulative_pnl,
                trades_today       = trades_today,
                consecutive_losses = consecutive_losses,
                apply_to_pool      = (self.pool_mode == "RESUME"),
            )

            # Position sizing decides what a run measures (CLAUDE.md §13.9): a
            # smaller risk budget takes more LOT_FLOOR rejections and therefore
            # samples a different, degraded set of signals. Silently sizing at
            # 42% of the requested pool — as `--pool 1000` restoring to $423.77
            # did — means the session measured the residue, not the strategy.
            # Say so loudly whichever mode is in force.
            drift = abs(self.pool.current_pool - requested)
            if drift > max(1.0, requested * 0.01):
                if self.pool_mode == "RESUME":
                    logger.warning(
                        f"SA POOL OVERRIDE: --pool ${requested:.2f} was adjusted "
                        f"to ${self.pool.current_pool:.2f} by ${cumulative_pnl:+.2f} "
                        f"of realized P&L since inception {inception.date()}. "
                        f"Risk/trade is ${self.pool.risk_per_trade_usd:.2f}, not "
                        f"${requested * self.pool.risk_pct:.2f}. "
                        f"Use --pool-mode FRESH to size at the requested pool."
                    )
                else:
                    logger.warning(
                        f"SA POOL FRESH: holding the requested ${requested:.2f} "
                        f"and ignoring ${cumulative_pnl:+.2f} of realized P&L "
                        f"since inception {inception.date()}. Risk/trade "
                        f"${self.pool.risk_per_trade_usd:.2f}."
                    )

            # Prime SA-CRG so the 15-min pause runs from the actual last
            # loss timestamp, not from "now". Prevents the post-restart bug
            # where a restored streak armed a fresh 15-min lockout.
            self.crg.prime_from_history(
                last_loss_close_time = last_loss_close_time,
                consecutive_losses   = consecutive_losses,
            )
        except Exception as e:
            logger.warning(f"SA: Failed to restore pool from MT5 history: {e}")

    def _adopt_open_positions_from_mt5(self):
        """
        Repopulate self._open_trades and pool._open_positions from MT5 for
        any positions with the SA magic that survived the restart. Without
        this, the 120-min timeout and EOD enforcer never fire on them.
        """
        try:
            positions = mt5.positions_get() or []
            sa_positions = [p for p in positions
                            if getattr(p, "magic", 0) == self.SA_MAGIC]
            if not sa_positions:
                return

            adopted = 0
            for p in sa_positions:
                ticket = int(p.ticket)
                if ticket in self._open_trades:
                    continue
                direction = "BULLISH" if p.type == mt5.ORDER_TYPE_BUY else "BEARISH"
                open_time = datetime.fromtimestamp(p.time, tz=timezone.utc)
                tp1 = float(p.tp) if p.tp else 0.0
                self._open_trades[ticket] = {
                    "symbol":    p.symbol,
                    "direction": direction,
                    "entry":     float(p.price_open),
                    "sl":        float(p.sl) if p.sl else 0.0,
                    "tp1":       tp1,
                    "tp2":       tp1,   # tp2 not stored on broker side — reuse tp1
                    "open_time": open_time,
                    "tp1_hit":   False,
                    "adopted":   True,
                }
                adopted += 1
                logger.info(
                    f"SA adopted open position: ticket={ticket} {p.symbol} "
                    f"{direction} entry={p.price_open:.4f} sl={p.sl:.4f} tp={p.tp:.4f} "
                    f"opened_at={open_time.isoformat()}"
                )

            if adopted:
                self.pool.adopt_open_positions(adopted)
                # One-shot timeout sweep: freshly-adopted positions that
                # already exceed the 120-min cap are closed now, not 30s
                # later. Protects against the gap when SA was offline long
                # enough for trades to age past the timeout window.
                try:
                    self._monitor_open_trades()
                except Exception as e:
                    logger.warning(
                        f"SA: immediate timeout sweep after adoption failed: {e}"
                    )
        except Exception as e:
            logger.warning(f"SA: Failed to adopt open positions from MT5: {e}")

    def _position_deals(self, ticket: int) -> list:
        """
        Every deal belonging to one position.

        An earlier implementation used `history_deals_get(ticket=N)`, which
        filters by DEAL ticket rather than position ticket — passing the SA's
        position ID matched nothing and returned $0 for every closed trade, so
        pool counters never decremented and losses were invisible. The
        `position` filter keys on position_id; the time-range scan is a
        fallback for brokers where the primary call comes back empty.
        """
        deals = mt5.history_deals_get(position=ticket)
        if not deals:
            now  = datetime.now(timezone.utc)
            wide = mt5.history_deals_get(now - timedelta(hours=6), now)
            if wide:
                deals = [d for d in wide
                         if getattr(d, "position_id", 0) == ticket]
        return list(deals or [])

    def _get_closed_pnl(self, ticket: int) -> Optional[float]:
        """
        Realized P&L of a position (profit + commission + swap across all its
        deals), or None when the broker reports no deals for it at all.

        The None is load-bearing. This used to return 0.0 in that case and
        every caller wrote `self._get_closed_pnl(t) or pos.profit`, so a
        genuinely flat trade silently fell through to floating P&L. Callers
        now have to say what they want when history is unavailable.
        """
        deals = self._position_deals(ticket)
        if not deals:
            return None
        return sum(
            getattr(d, "profit", 0.0)
            + getattr(d, "commission", 0.0)
            + getattr(d, "swap", 0.0)
            for d in deals
        )

    def _settled_pnl(self, ticket: int) -> Optional[float]:
        """
        Realized P&L once the *closing* leg has landed in deal history.

        Deal history lags `order_send`. The previous code read P&L back the
        instant the close was fired, so only the ENTRY deal was visible: either
        it summed to 0.0 and the `or pos.profit` fallback booked the floating
        P&L of a position that might still be open, or the entry commission
        made it non-zero, the fallback was bypassed, and a commission-sized
        "loss" was booked — arming a loss cooldown and advancing the
        consecutive-loss counter toward the SA-CRG pause.

        Returns None if no closing deal appears within the polling budget.
        """
        entry_out = getattr(mt5, "DEAL_ENTRY_OUT", 1)
        for attempt in range(self.PNL_SETTLE_ATTEMPTS):
            deals = self._position_deals(ticket)
            if deals and any(getattr(d, "entry", None) == entry_out
                             for d in deals):
                return sum(
                    getattr(d, "profit", 0.0)
                    + getattr(d, "commission", 0.0)
                    + getattr(d, "swap", 0.0)
                    for d in deals
                )
            if attempt < self.PNL_SETTLE_ATTEMPTS - 1:
                time.sleep(self.PNL_SETTLE_DELAY_S)
        return None

    def _perform_daily_reset(self):
        logger.info("SA: Performing daily reset protocol")
        now = datetime.now(timezone.utc)

        # Close and BOOK anything still open *before* the day's ledger is
        # snapshotted, so the P&L lands in the day it belongs to. The reset
        # module used to do its own closing and never routed the result
        # through _on_trade_closed, so the pool ledger, trade log, cooldown
        # and journal never saw those exits at all.
        # force_settle: the day's ledger is snapshotted below and this process
        # may not be alive for the next cycle, so an unpriced close is resolved
        # now rather than carried into a new day unbooked.
        still_open = self._force_close_all(now, "EOD_RESET", force_settle=True)

        open_tickets = list(self._open_trades.keys())
        if still_open:
            logger.error(
                f"SA Daily Reset: {still_open} position(s) could not be closed "
                f"and remain open at the broker: {open_tickets}. They stay "
                f"tracked across the reset and will be retried."
            )
        result = self.daily_reset.execute(self.pool, mt5, open_tickets,
                                          closer=self._close_at_market)
        # Only forget tickets the broker confirmed closed. Clearing the whole
        # map unconditionally is exactly how a failed close turned into an
        # untracked, unmanaged position.
        for t in result.get("positions_closed", []):
            self._open_trades.pop(t, None)
        self._session_open_prices.clear()
        self._last_session_name = None      # Force a fresh transition log
        self.state_mach.daily_reset()
        self.crg.reset_pause()
        self.cooldown.reset()

        # Seal the day's telemetry. The EOD close above routes through
        # _on_trade_closed, so those trades are queued for a forensic pass —
        # forced here because the agent may not still be running in two
        # hours' time when their look-ahead window would naturally fill.
        try:
            self._drain_postmortems(now, force=True)
            self.rejects.flush()
            logger.info(f"SA Daily Reset: {self.rejects.summary_line()}")
        except Exception as e:
            logger.warning(f"SA Daily Reset: telemetry seal failed: {e}")

        logger.info(f"SA Daily Reset complete: {result}")


# ═════════════════════════════════════════════════════════════════════════════
# Entry Point
# ═════════════════════════════════════════════════════════════════════════════

#: TCP port held for the lifetime of the process to enforce a single scalper.
#: The Guardian uses 55555 for the same purpose. Two scalper instances would
#: both trade magic 88880 and both restore their pool from the same shared deal
#: history, double-counting every close.
SA_SINGLE_INSTANCE_PORT = 55556
_instance_lock = None


def _acquire_single_instance_lock() -> bool:
    """Bind the SA instance port. False if another scalper already holds it."""
    global _instance_lock
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", SA_SINGLE_INSTANCE_PORT))
        s.listen(1)
    except OSError:
        s.close()
        return False
    _instance_lock = s          # keep referenced; closing would free the port
    return True


def _connect_mt5(login: int, password: str, server: str) -> bool:
    """
    Connect to MT5, preferring an already-authenticated terminal session.

    `mt5.initialize(login=..., password=...)` with bad credentials does not
    merely fail — it signs the running terminal out, and nothing in this
    codebase can sign it back in. Attaching first means a stale .env password
    degrades to "kept the working session" instead of "killed it".
    """
    if mt5.initialize():
        info = mt5.account_info()
        if info and (not login or info.login == login):
            logger.info(f"SA: attached to running MT5 session | "
                        f"Account={info.login}")
            return True
        logger.info(
            f"SA: attached terminal is account "
            f"{getattr(info, 'login', 'unknown')}, need {login} — "
            f"re-initializing with credentials"
        )
        mt5.shutdown()

    if not login or not password or not server:
        logger.error("SA: no running MT5 session and MT5_LOGIN / MT5_PASSWORD "
                     "/ MT5_SERVER are not all set in .env")
        return False

    return bool(mt5.initialize(login=login, password=password, server=server))


def main():
    # TGA auto-spawn disabled — launch trade_guardian_agent.py separately
    # to avoid duplicate instances when managing agents independently.
    # import subprocess
    # import sys
    # try:
    #     subprocess.Popen([sys.executable, "trade_guardian_agent.py"],
    #                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # except Exception as e:
    #     logger.warning(f"Failed to start TGA: {e}")

    parser = argparse.ArgumentParser(description="APEX Scalper Agent (SA)")
    parser.add_argument("--pool",     type=float, default=1000.0,
                        help="SA pool size in USD (default: 1000)")
    parser.add_argument("--risk",     type=float, default=0.03,
                        help="Risk per trade as decimal of pool (default: 0.03 = 3%%)")
    parser.add_argument("--symbols",  type=str,   default="XAUUSD",
                        help="Comma-separated instrument list (default: XAUUSD)")
    parser.add_argument("--interval", type=int,   default=30,
                        help="Analysis interval in seconds (default: 30)")
    parser.add_argument("--loss-limit", type=float, default=100.0,
                        help="Hard dollar daily loss limit for SA pool (default: 100.0)")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Log signals only, do not execute trades")
    parser.add_argument("--stb-relax-continuation", dest="stb_relax",
                        action=argparse.BooleanOptionalAction,
                        default=DP.STB_RELAX_CONTINUATION,
                        help="Relax the two fade-specific short-term-bias rules "
                             "for continuation triggers (BOS_RETEST, FVG_FILL).")
    parser.add_argument("--ema-band", dest="ema_band",
                        action=argparse.BooleanOptionalAction,
                        default=DP.EMA_BAND_ENABLED,
                        help="H1 EMA(18) high/low directional band: longs only "
                             "above both bands, shorts only below. Default off "
                             "— it failed the walk-forward fold test on the "
                             "current sweep-fade trigger mix "
                             "(scalper/decision_params.py).")
    parser.add_argument("--ema-band-mode", type=str, default=DP.EMA_BAND_MODE,
                        choices=["TREND", "FADE"],
                        help="TREND: above the band permits longs. FADE: the "
                             "inversion. Mirrors backtest_scalper.py.")
    parser.add_argument("--va-fade", dest="va_fade",
                        action=argparse.BooleanOptionalAction,
                        default=DP.VA_FADE_ENABLED,
                        help="Enable the VALUE_AREA_FADE entry model: sell a "
                             "rejection at VAH, buy one at VAL, nothing at the "
                             "POC. Default off pending the fold test.")
    parser.add_argument("--rd-gate", dest="rd_gate",
                        action=argparse.BooleanOptionalAction,
                        default=DP.RD_GATE_ENABLED,
                        help="Regime-direction gate: veto a trigger that fades "
                             "a classified H1 trend. Default off (ledger L-006).")
    parser.add_argument("--rd-gate-mode", type=str, default=DP.RD_GATE_MODE,
                        choices=list(RD_MODES),
                        help="SYMMETRIC: veto any counter-trend trigger. "
                             "COUNTER_TREND_LONGS: vetoes longs only. "
                             "Mirrors backtest_scalper.py.")
    parser.add_argument("--pdr-gate", dest="pdr_gate",
                        action=argparse.BooleanOptionalAction,
                        default=DP.PDR_GATE_ENABLED,
                        help="Previous-day-range gate: veto buying the premium "
                             "or selling the discount of yesterday's range. "
                             "Default off (ledger L-011).")
    parser.add_argument("--pdr-gate-mode", type=str, default=DP.PDR_GATE_MODE,
                        choices=list(PDR_MODES),
                        help="SYMMETRIC: both vetoes. LONG_PREMIUM: vetoes "
                             "longs in the top quartile only. SHORT_DISCOUNT: "
                             "vetoes shorts in the bottom quartile only. "
                             "Mirrors backtest_scalper.py.")
    parser.add_argument("--vp-gate", dest="vp_gate",
                        action=argparse.BooleanOptionalAction,
                        default=DP.VP_GATE_ENABLED,
                        help="H4 value-area location gate: sell from VAH, buy "
                             "from VAL, no trade at the POC. Default off "
                             "pending the 60/20/20 fold test "
                             "(scalper/decision_params.py).")
    parser.add_argument("--leg-conf", dest="leg_conf",
                        action=argparse.BooleanOptionalAction,
                        default=DP.LEG_CONF_ENABLED,
                        help="VP_LEG_CONFLUENCE: filter entries by "
                             "location against two completed H4 swing "
                             "legs (ledger L-015)")
    parser.add_argument("--leg-conf-mode", type=str,
                        default=DP.LEG_CONF_MODE,
                        choices=list(LEG_CONF_MODES),
                        help="CONFLUENCE_ONLY | AT_LEVEL | LVN_VETO")
    parser.add_argument("--sweep-wick-filter", dest="sweep_wick_filter",
                        action=argparse.BooleanOptionalAction,
                        default=DP.SWEEP_WICK_FILTER_ENABLED,
                        help="L-016: require the sweeping candle's wick to be "
                             "at least --sweep-wick-ratio of its own range "
                             "before SWEEP_REJECTION is admitted")
    parser.add_argument("--reclaim-fvg", action=argparse.BooleanOptionalAction,
                        default=DP.RECLAIM_FVG_ENABLED,
                        help="Require displacement reclaim and a subsequent FVG return "
                             "at broken support/resistance (L-017; default on)")
    parser.add_argument("--htf-crt", action=argparse.BooleanOptionalAction,
                        default=DP.CRT_ENABLED, help="Priority D1/W1/MN1 sweep, M15 reclaim and FVG return")
    parser.add_argument("--crt-confluence-mode", choices=DP.CRT_CONFLUENCE_MODES,
                        default=DP.CRT_CONFLUENCE_MODE,
                        help="OBSERVE records MSS/retest; strict modes are unvalidated research arms")
    parser.add_argument("--m15-fvg-entry", action=argparse.BooleanOptionalAction,
                        default=DP.M15_FVG_ENTRY_ENABLED,
                        help="Enter confirmed M15 FVGs with TP before broken structure (L-018)")
    parser.add_argument("--sweep-wick-ratio", type=float,
                        default=DP.SWEEP_WICK_RATIO_MIN,
                        help="Minimum rejecting-wick share of the sweeping "
                             "candle's range (ledger L-016)")
    parser.add_argument("--vp-gate-mode", type=str, default=DP.VP_GATE_MODE,
                        choices=list(VP_MODES),
                        help="RANGING_ONLY: apply only when the regime "
                             "classifier says RANGING. ALWAYS: apply in every "
                             "regime. POC_ONLY: enforce just the POC dead "
                             "zone. Mirrors backtest_scalper.py.")
    parser.add_argument("--vp-poc-band", type=float,
                        default=DP.VP_POC_BAND_FRAC,
                        help="Half-width of the POC dead zone as a fraction of "
                             "value-area width (default "
                             f"{DP.VP_POC_BAND_FRAC}).")
    parser.add_argument("--vplr", dest="vplr",
                        action=argparse.BooleanOptionalAction,
                        default=DP.VPLR_ENABLED,
                        help="VP_LIQUIDITY_REACTION: a liquidity raid at an "
                             "anchored H4 volume-profile level, confirmed by an "
                             "M5 MSS. Highest trigger priority when on. "
                             "Mirrors backtest_scalper.py.")
    parser.add_argument("--vplr-session-override", dest="vplr_session_override",
                        action=argparse.BooleanOptionalAction,
                        default=DP.VPLR_SESSION_OVERRIDE_ENABLED,
                        help="Allow VP_LIQUIDITY_REACTION — and only it — to "
                             f"scan during {DP.VPLR_SESSION_WINDOW_UTC[0]}-"
                             f"{DP.VPLR_SESSION_WINDOW_UTC[1]} UTC. Existing "
                             "triggers keep their own windows unchanged.")
    parser.add_argument("--allow-whole-day", action="store_true",
                        help="Enable the 00:00-23:00 catch-all session window "
                             "(demo plumbing only; disables kill-zone gating)")
    parser.add_argument("--no-cooldown", action="store_true",
                        help="Disable the post-trade cooldown switch entirely")
    parser.add_argument("--win-cooldown-min", type=float, default=5.0,
                        help="Break after a winning trade, in minutes (default: 5)")
    parser.add_argument("--triggers", type=str, default=None,
                        help="Comma-separated trigger whitelist "
                             "(SWEEP_REJECTION,FVG_FILL,BOS_RETEST,JUDAS). "
                             "Default: all four.")
    parser.add_argument("--sessions", type=str, default=None,
                        help="Comma-separated session whitelist (LONDON_OPEN,"
                             "PRE_LONDON,LONDON_NY,TOKYO_OPEN,NY_LUNCH_REV). "
                             "Default: TOKYO_OPEN,LONDON_NY.")
    parser.add_argument("--loss-cooldown-policy", type=str,
                        default="NEXT_UTC_HOUR",
                        choices=["NEXT_UTC_HOUR", "FIXED_MINUTES"],
                        help="After a loss: hold until the next UTC hour "
                             "(default) or for a fixed period")
    parser.add_argument("--pool-mode", type=str, default="RESUME",
                        choices=["RESUME", "FRESH"],
                        help="RESUME (default): --pool is the original "
                             "allocation and the live pool carries all "
                             "realized P&L since inception. FRESH: size at "
                             "exactly --pool, ignoring that history. Use FRESH "
                             "when the run is meant to measure the strategy at "
                             "a stated risk budget (CLAUDE.md §13.9).")
    args = parser.parse_args()

    if not _acquire_single_instance_lock():
        logger.error(
            f"SA: another scalper instance already holds port "
            f"{SA_SINGLE_INSTANCE_PORT}. Two instances would both trade magic "
            f"{MAGIC_SCALPER} and double-count the shared deal history. Exiting."
        )
        sys.exit(1)

    symbols = [s.strip() for s in args.symbols.split(",")]

    # ── MT5 Connection ────────────────────────────────────────────────────────
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    load_dotenv(env_path)

    login    = int(os.getenv("MT5_LOGIN", "0"))
    password = os.getenv("MT5_PASSWORD", "")
    server   = os.getenv("MT5_SERVER", "")

    # Attach to an already-signed-in terminal before attempting a credentialed
    # login. A *failed* credentialed initialize() signs the running terminal
    # OUT — which is how one stale .env password took a whole audit session
    # down and could not be recovered without the MT5 GUI.
    if not _connect_mt5(login, password, server):
        logger.error(f"SA: MT5 connection failed: {mt5.last_error()}")
        sys.exit(1)

    acct = mt5.account_info()
    logger.info(f"SA: MT5 connected | Account={acct.login} | Balance=${acct.balance:.2f}")

    # Guard: SA pool must not exceed account balance
    if args.pool > acct.balance:
        logger.error(f"SA Pool ${args.pool:.2f} exceeds account balance ${acct.balance:.2f}. Adjust --pool.")
        mt5.shutdown()
        sys.exit(1)

    # ── Launch SA ─────────────────────────────────────────────────────────────
    agent = ScalperAgent(
        sa_pool_usd = args.pool,
        risk_pct    = args.risk,
        symbols     = symbols,
        loss_limit_usd = args.loss_limit,
        interval    = args.interval,
        dry_run     = args.dry_run,
        cooldown_enabled     = not args.no_cooldown,
        win_cooldown_minutes = args.win_cooldown_min,
        loss_cooldown_policy = args.loss_cooldown_policy,
        allow_whole_day      = args.allow_whole_day,
        enabled_triggers     = ([t.strip() for t in args.triggers.split(",") if t.strip()]
                                if args.triggers else None),
        enabled_sessions     = ([t.strip() for t in args.sessions.split(",") if t.strip()]
                                if args.sessions else None),
        ema_band_enabled     = args.ema_band,
        va_fade_enabled      = args.va_fade,
        rd_gate_enabled      = args.rd_gate,
        rd_gate_mode         = args.rd_gate_mode,
        pdr_gate_enabled     = args.pdr_gate,
        pdr_gate_mode        = args.pdr_gate_mode,
        vplr_enabled         = args.vplr,
        vplr_session_override= args.vplr_session_override,
        leg_conf_enabled     = args.leg_conf,
        leg_conf_mode        = args.leg_conf_mode,
        sweep_wick_filter    = args.sweep_wick_filter,
        sweep_wick_ratio     = args.sweep_wick_ratio,
        reclaim_fvg_enabled  = args.reclaim_fvg,
        m15_fvg_entry_enabled= args.m15_fvg_entry,
        htf_crt_enabled      = args.htf_crt,
        crt_confluence_mode  = args.crt_confluence_mode,
        vp_gate_enabled      = args.vp_gate,
        vp_gate_mode         = args.vp_gate_mode,
        vp_poc_band_frac     = args.vp_poc_band,
        ema_band_mode        = args.ema_band_mode,
        stb_relax_continuation = args.stb_relax,
        pool_mode            = args.pool_mode,
    )

    try:
        agent.run()
    finally:
        mt5.shutdown()
        logger.info("SA: MT5 disconnected. Goodbye.")


if __name__ == "__main__":
    main()
