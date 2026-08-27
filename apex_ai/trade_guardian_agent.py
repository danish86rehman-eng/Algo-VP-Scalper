"""
Trade Guardian Agent (TGA)
==========================
Universal Position Manager. Watches all open positions across all agents.
It trails SL through 4 stages, manages dynamic TP, and monitors for early close reversals.
It NEVER opens positions.
"""

import os
import sys
import time
import json
import logging
import argparse
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))

from core.constants import agent_for_magic, MAGIC_TGA

# ── Logging Setup ─────────────────────────────────────────────────────────────
Path("logs").mkdir(exist_ok=True)

# Redirected stdout on Windows defaults to cp1252. The banner below contains a
# shield emoji, which raised UnicodeEncodeError inside the handler on every
# emit — logging swallows handler errors, so the daemon survived but the
# console record was lost and stderr filled with tracebacks.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler("logs/tga_log.txt", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
)
# The Guardian's stage ladder is driven off UTC bar times; local stamps put the
# reader hours away from the event they are reading about.
logging.Formatter.converter = time.gmtime
for _h in logging.getLogger().handlers:
    _h.setFormatter(logging.Formatter(
        "%(asctime)sZ [%(name)s] %(levelname)s: %(message)s"))
logger = logging.getLogger("TGA")


# The decision engines live in `scalper/tga_engines.py` so the simulator
# executes the same code rather than a copy of it (CLAUDE.md 13.4).
# TGADataFeed and TradeGuardianAgent below stay here: MT5 I/O and process
# orchestration are exactly what the simulator replaces.
from scalper.tga_engines import (   # noqa: F401  (re-exported)
    TGAConfig,
    TGAPositionRecord,
    SLEngine,
    EarlyCloseEngine,
    TPEngine,
    StructureFeed,
    compute_atr,
    recent_structure,
)


# ── Data Feed ─────────────────────────────────────────────────────────────────
class TGADataFeed:
    """Fetches MT5 data, caches it briefly, calculates ATR and Structure."""
    def __init__(self, cache_seconds=15):
        self.cache_seconds = cache_seconds
        self._cache = {}

    def get_data(self, symbol: str, timeframe: int, bars: int = 100) -> Optional[pd.DataFrame]:
        cache_key = f"{symbol}_{timeframe}"
        now = time.time()
        
        if cache_key in self._cache:
            df, tstamp = self._cache[cache_key]
            if now - tstamp < self.cache_seconds:
                return df
        
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
        if rates is None or len(rates) < 10:
            return None
        
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
        self._cache[cache_key] = (df, now)
        return df

    def get_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        return compute_atr(df, period)

    def get_historical_atr(self, symbol: str, target_time: datetime, timeframe: int = mt5.TIMEFRAME_M5) -> float:
        """Fetch ATR at the time the trade was opened."""
        rates = mt5.copy_rates_from(symbol, timeframe, target_time, 20)
        if rates is None or len(rates) < 14:
            # Fallback to current ATR
            df = self.get_data(symbol, timeframe)
            return self.get_atr(df)
        df = pd.DataFrame(rates)
        return self.get_atr(df)

    def get_recent_structure(self, df: pd.DataFrame, window: int = 5) -> Tuple[float, float]:
        """Finds recent swing high and swing low."""
        return recent_structure(df, window)


# ── Main Agent ────────────────────────────────────────────────────────────────
class TradeGuardianAgent:
    def __init__(self, config: TGAConfig = TGAConfig()):
        self.cfg = config
        self.data = TGADataFeed()
        self.sl_engine = SLEngine(config)
        self.tp_engine = TPEngine(config)
        self.early_engine = EarlyCloseEngine(config)
        
        self.registry: Dict[int, TGAPositionRecord] = {}
        self._running = False
        self._action_log = "logs/tga_action_log.json"
        
        if not Path(self._action_log).exists():
            Path(self._action_log).write_text("[]", encoding="utf-8")

        logger.info(
            f"🛡️ TGA Initialized | "
            f"Stage ladder: BE@{self.cfg.breakeven_trigger_r}R (+ "
            f"{self.cfg.breakeven_min_close_candles}-candle struct confirm) | "
            f"Trail@{self.cfg.stage2_trigger_r}R | "
            f"AggrTrail@{self.cfg.stage3_trigger_r}R | "
            f"EarlyCloseArm={self.cfg.early_close_min_profit_r}R"
        )

    def _log_action(self, rec: TGAPositionRecord, action_type: str, reason: str, 
                    prev_sl=None, new_sl=None, prev_tp=None, new_tp=None):
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "position_id": rec.ticket,
            "origin_agent": rec.origin_agent,
            "action_type": action_type,
            "previous_sl": prev_sl,
            "new_sl": new_sl,
            "previous_tp": prev_tp,
            "new_tp": new_tp,
            "current_profit_r": rec.current_r(mt5.symbol_info_tick(rec.symbol).bid if rec.direction=="BUY" else mt5.symbol_info_tick(rec.symbol).ask),
            "peak_profit_r": rec.peak_profit_r,
            "peak_adverse_r": rec.peak_adverse_r,
            "sl_stage": rec.sl_stage,
            "trigger": reason,
            "candle_confirmed": "Yes"
        }
        
        try:
            with open(self._action_log, 'r', encoding='utf-8') as f:
                records = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            records = []
            
        records.append(log_entry)
        with open(self._action_log, 'w', encoding='utf-8') as f:
            json.dump(records, f, indent=2)
            
        logger.info(
            f"\n[TGA ACTION LOG]\n"
            f"Timestamp         : {log_entry['timestamp']}\n"
            f"Position ID       : {log_entry['position_id']}\n"
            f"Origin Agent      : {log_entry['origin_agent']}\n"
            f"Action Type       : {log_entry['action_type']}\n"
            f"Previous SL       : {log_entry['previous_sl']}\n"
            f"New SL            : {log_entry['new_sl']}\n"
            f"Previous TP       : {log_entry['previous_tp']}\n"
            f"New TP            : {log_entry['new_tp']}\n"
            f"Current Profit (R): {log_entry['current_profit_r']:.2f}\n"
            f"Peak Profit (R)   : {log_entry['peak_profit_r']:.2f}\n"
            f"Peak Adverse (R)  : {log_entry['peak_adverse_r']:.2f}\n"
            f"SL Stage          : {log_entry['sl_stage']}\n"
            f"Trigger           : {log_entry['trigger']}\n"
        )

    def _sync_registry(self):
        """Discover new trades and remove closed ones."""
        open_positions = mt5.positions_get()
        if open_positions is None:
            return
            
        active_tickets = {p.ticket for p in open_positions}
        
        # Remove closed
        for ticket in list(self.registry.keys()):
            if ticket not in active_tickets:
                logger.info(f"TGA: Position {ticket} closed. Removing from registry.")
                del self.registry[ticket]
                
        # Add new
        for p in open_positions:
            if p.ticket not in self.registry:
                # Was a hard-coded 12345/88880/77770 ladder that did not
                # include the magic MT5Connector actually stamps (20260426),
                # so every Pool A position was registered as UNKNOWN.
                origin = agent_for_magic(p.magic)
                
                # Infer entry time
                entry_time = datetime.fromtimestamp(p.time, tz=timezone.utc)
                entry_atr = self.data.get_historical_atr(p.symbol, entry_time)
                if entry_atr == 0:
                    entry_atr = 0.0001 # fallback safe non-zero
                
                rec = TGAPositionRecord(
                    ticket=p.ticket,
                    symbol=p.symbol,
                    origin_agent=origin,
                    direction="BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
                    entry_price=p.price_open,
                    original_sl=p.sl,
                    original_tp=p.tp,
                    volume=p.volume,
                    entry_atr=entry_atr,
                    entry_time=entry_time
                )
                self.registry[p.ticket] = rec
                logger.info(rec.print_registry())

    def _modify_mt5_sl_tp(self, rec: TGAPositionRecord, new_sl: float, new_tp: float) -> bool:
        tick = mt5.symbol_info_tick(rec.symbol)
        if not tick: return False
        
        info = mt5.symbol_info(rec.symbol)
        sl = round(new_sl, info.digits) if new_sl else rec.original_sl
        tp = round(new_tp, info.digits) if new_tp else rec.original_tp
        
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": rec.ticket,
            "sl": sl,
            "tp": tp,
            "magic": MAGIC_TGA,  # TGA modifier signature
        }
        res = mt5.order_send(request)
        return res and res.retcode == mt5.TRADE_RETCODE_DONE

    def _close_mt5_position(self, rec: TGAPositionRecord):
        tick = mt5.symbol_info_tick(rec.symbol)
        if not tick: return
        pos_type = mt5.ORDER_TYPE_BUY if rec.direction == "BUY" else mt5.ORDER_TYPE_SELL
        close_type = mt5.ORDER_TYPE_SELL if pos_type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price = tick.bid if pos_type == mt5.ORDER_TYPE_BUY else tick.ask
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": rec.symbol,
            "volume": rec.volume,
            "type": close_type,
            "price": price,
            "position": rec.ticket,
            "deviation": 20,
            "magic": MAGIC_TGA,
            "comment": "TGA_EARLY_CLOSE",
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        res = mt5.order_send(request)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"TGA closed position {rec.ticket} at market.")
        else:
            logger.error(f"TGA failed to close {rec.ticket}: {res.comment if res else 'Unknown'}")

    def _partial_close_mt5(self, rec: TGAPositionRecord, pct: float) -> bool:
        """Close `pct` of the position. True only when a partial actually filled.

        Two ways this used to lie:

        * `round(0.01 * 0.50, 2)` is `0.01`, and volume_min/step are both 0.01
          — so the "partial" close took 100% of a minimum-lot position. The
          guard only tested `< volume_min`, never `>= rec.volume`. Volume is
          now floored to a whole broker step and checked against both ends.
        * Every path returned None and a rejected `order_send` logged nothing,
          while the caller latched `tp_extended` and wrote a TP_EXTEND row
          regardless. Same class of fabricated journal entry as L-004.
        """
        info = mt5.symbol_info(rec.symbol)
        if info is None:
            logger.error(f"TGA partial close {rec.ticket}: no symbol_info")
            return False

        # Floor to a whole volume step; +1e-9 absorbs the float repr of
        # exact multiples (0.05 / 0.01 == 5.000000000000001).
        step = info.volume_step or 0.01
        close_vol = round(int((rec.volume * pct) / step + 1e-9) * step, 2)

        if close_vol < info.volume_min or close_vol >= rec.volume:
            logger.info(
                f"TGA {rec.ticket}: {rec.volume} lots cannot be split "
                f"{pct:.0%} (min={info.volume_min}, step={step}) — skipping "
                f"partial close and leaving TP at its original target.")
            rec.tp_extend_blocked = True
            return False

        tick = mt5.symbol_info_tick(rec.symbol)
        pos_type = mt5.ORDER_TYPE_BUY if rec.direction == "BUY" else mt5.ORDER_TYPE_SELL
        close_type = mt5.ORDER_TYPE_SELL if pos_type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price = tick.bid if pos_type == mt5.ORDER_TYPE_BUY else tick.ask
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": rec.symbol,
            "volume": close_vol,
            "type": close_type,
            "price": price,
            "position": rec.ticket,
            "deviation": 20,
            "magic": MAGIC_TGA,
            "comment": "TGA_PARTIAL_TP1",
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        res = mt5.order_send(request)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"TGA partially closed {close_vol} lots on {rec.ticket}")
            rec.volume = round(rec.volume - close_vol, 2)
            return True

        logger.error(
            f"TGA failed to partially close {close_vol} lots on {rec.ticket}: "
            f"{res.comment if res else 'Unknown'}")
        return False

    def _process_position(self, rec: TGAPositionRecord, p):
        current_price = p.price_current
        r = rec.current_r(current_price)
        
        # 1. Update Peak Profit AND Maximum Adverse Excursion
        if r > rec.peak_profit_r:
            rec.peak_profit_r = r
        if r < rec.peak_adverse_r:
            rec.peak_adverse_r = r

        if not rec.reached_1r and rec.peak_profit_r >= 1.0:
            rec.reached_1r = True
            rec.early_close_armed = True
        if not rec.reached_2r and rec.peak_profit_r >= 2.0:
            rec.reached_2r = True
            
        df_m5 = self.data.get_data(rec.symbol, mt5.TIMEFRAME_M5)
        
        # 2 & 3. Determine SL Stage & Update SL
        current_mt5_sl = p.sl
        # `p` is a snapshot taken before this tick's trailing move. Anything
        # downstream that writes SL must use the effective value, not p.sl,
        # or a TP write reverts the stop we just trailed.
        effective_sl = p.sl
        target_stage, new_sl = self.sl_engine.evaluate(rec, current_price, df_m5, self.data)

        if new_sl and abs(new_sl - current_mt5_sl) > (mt5.symbol_info(rec.symbol).point * 10):
            # Move SL only in favorable direction
            valid_move = False
            if rec.direction == "BUY" and new_sl > current_mt5_sl: valid_move = True
            if rec.direction == "SELL" and new_sl < current_mt5_sl: valid_move = True
            # Special case: initial SL move
            if current_mt5_sl == 0: valid_move = True
            
            if valid_move:
                success = self._modify_mt5_sl_tp(rec, new_sl, p.tp)
                if success:
                    self._log_action(rec, "SL_MOVE" if target_stage == rec.sl_stage else "STAGE_TRANSITION",
                                     f"Trailing SL Stage {target_stage}", current_mt5_sl, new_sl, p.tp, p.tp)
                    rec.sl_stage = target_stage
                    effective_sl = new_sl
                    if target_stage >= 1: rec.breakeven_reached = True

        # A trade inside the TP-extension band with confirmed momentum is not
        # a stalling trade — it is precisely the case the extension stage
        # exists to handle (bank 50%, run the rest). Computed once here
        # because the early-close suppression below and the extension stage
        # at step 7 key off the same answer, and check_momentum is not free
        # on a 2-second poll.
        #
        # Before TP1 moved 1.0R -> 2.0R (CLAUDE.md 13.1) these two never
        # raced: the proximity band sat at ~0.8R, below the 1.0R arming
        # threshold, so extension always won. At 2R the band sits at ~1.8R,
        # early close armed first and returned before step 7 could run, and
        # partial closes silently stopped happening — 13 TP_EXTENDs in May
        # against 0 in August.
        running_into_tp = (
            not rec.tp_extended
            and not rec.tp_extend_blocked
            and rec.original_tp > 0
            and abs(current_price - rec.original_tp) < (0.5 * rec.entry_atr)
            and self.tp_engine.check_momentum(rec, df_m5)
        )

        # 4 & 5. Early Close Check
        if rec.early_close_armed and not running_into_tp:
            should_close, reason = self.early_engine.check_triggers(rec, df_m5, current_price)
            if should_close:
                self._log_action(rec, "EARLY_CLOSE", reason, current_mt5_sl, current_mt5_sl, p.tp, p.tp)
                self._close_mt5_position(rec)
                return # Stop processing this closed trade

        # 6. NO-PROGRESS check — kill dead trades before they hit full SL.
        # Fires for trades that have been open beyond no_progress_minutes
        # with peak_profit_r still ≤ no_progress_max_peak_r. Different from
        # the early-close engine, which only fires after +1R has been reached.
        elapsed_min = (datetime.now(timezone.utc) - rec.entry_time).total_seconds() / 60.0
        if (elapsed_min >= self.cfg.no_progress_minutes and
            rec.peak_profit_r <= self.cfg.no_progress_max_peak_r):
            reason = (f"No-progress: {elapsed_min:.0f}m open, "
                      f"peak={rec.peak_profit_r:.2f}R ≤ "
                      f"{self.cfg.no_progress_max_peak_r}R")
            logger.warning(f"TGA {rec.ticket} {rec.symbol}: {reason}")
            self._log_action(rec, "NO_PROGRESS_CLOSE", reason,
                             current_mt5_sl, current_mt5_sl, p.tp, p.tp)
            self._close_mt5_position(rec)
            return

        # 7. TP extension — bank part of the position at TP1 and run the
        #    remainder to the next M15 structure target. The extension only
        #    happens if the partial actually filled: without profit banked,
        #    pushing TP further out is strictly more risk than leaving it.
        if running_into_tp:
            df_m15 = self.data.get_data(rec.symbol, mt5.TIMEFRAME_M15)
            new_tp = self.tp_engine.get_next_structure_target(rec, df_m15)
            if new_tp and self._partial_close_mt5(rec, self.cfg.tp_extension_partial_pct):
                if self._modify_mt5_sl_tp(rec, effective_sl, new_tp):
                    rec.tp_extended = True
                    self._log_action(rec, "TP_EXTEND", "Strong momentum near TP1",
                                     effective_sl, effective_sl, rec.original_tp, new_tp)
                else:
                    logger.error(
                        f"TGA {rec.ticket}: partial close filled but TP extend "
                        f"write failed — position now {rec.volume} lots at the "
                        f"original TP.")

    def run(self):
        self._running = True
        logger.info("🛡️ TGA Main Loop Started")
        
        while self._running:
            try:
                self._sync_registry()
                
                open_positions = mt5.positions_get()
                if open_positions:
                    for p in open_positions:
                        if p.ticket in self.registry:
                            rec = self.registry[p.ticket]
                            # Entry breathing rule: skip if trade is very new (approx 10 mins / 2 M5 candles)
                            mins_alive = (datetime.now(timezone.utc) - rec.entry_time).total_seconds() / 60
                            if mins_alive < 10:
                                continue
                                
                            self._process_position(rec, p)
                
            except KeyboardInterrupt:
                logger.info("TGA Stopping...")
                self._running = False
            except Exception as e:
                logger.error(f"TGA Loop Error: {e}", exc_info=True)
                
            # Tick emulation (fast poll)
            time.sleep(2.0)

def _connect_mt5(login: int, password: str, server: str) -> bool:
    """
    Connect to MT5, preferring an already-authenticated terminal session.
    See the identical helper in scalper_agent.py — a bad credentialed
    initialize() logs the running terminal out rather than simply failing.
    """
    if mt5.initialize():
        info = mt5.account_info()
        if info and (not login or info.login == login):
            logger.info(f"TGA: attached to running MT5 session | "
                        f"Account={info.login}")
            return True
        logger.info(
            f"TGA: attached terminal is account "
            f"{getattr(info, 'login', 'unknown')}, need {login} — "
            f"re-initializing with credentials"
        )
        mt5.shutdown()

    if not login or not password or not server:
        logger.error("TGA: no running MT5 session and MT5_LOGIN / MT5_PASSWORD "
                     "/ MT5_SERVER are not all set in .env")
        return False

    return bool(mt5.initialize(login=login, password=password, server=server))


def main():
    parser = argparse.ArgumentParser()
    parser.parse_args()
    
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    load_dotenv(env_path)

    login = int(os.getenv("MT5_LOGIN", "0"))
    password = os.getenv("MT5_PASSWORD", "")
    server = os.getenv("MT5_SERVER", "")

    # Single instance lock
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 55555))
    except socket.error:
        logger.info("TGA is already running in another process.")
        sys.exit(0)

    # Attach to an already-signed-in terminal before trying a credentialed
    # login: a failed credentialed initialize() signs the running terminal OUT,
    # and nothing here can sign it back in.
    if not _connect_mt5(login, password, server):
        logger.error(f"TGA: MT5 connection failed: {mt5.last_error()}")
        sys.exit(1)

    tga = TradeGuardianAgent()
    try:
        tga.run()
    finally:
        mt5.shutdown()

if __name__ == "__main__":
    main()
