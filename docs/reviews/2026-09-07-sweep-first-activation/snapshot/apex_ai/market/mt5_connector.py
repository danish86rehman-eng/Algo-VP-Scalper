"""
APEX AI — MT5 Connector
Handles all MetaTrader 5 communication: connection, data fetch, order management.
"""
from __future__ import annotations
import os
import time
import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone
import pandas as pd
import numpy as np

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

from dotenv import load_dotenv
from core.constants import MAGIC_MAIN
load_dotenv()

logger = logging.getLogger("APEX.MT5")

# Timeframe mapping
TF_MAP = {
    "M1":  1,  "M5":  5,  "M15": 15, "M30": 30,
    "H1":  60, "H4":  240, "D1": 1440, "W1": 10080,
}

if MT5_AVAILABLE:
    TF_MT5 = {
        "M1":  mt5.TIMEFRAME_M1,  "M5":  mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30,
        "H1":  mt5.TIMEFRAME_H1,  "H4":  mt5.TIMEFRAME_H4,
        "D1":  mt5.TIMEFRAME_D1,  "W1":  mt5.TIMEFRAME_W1,
    }
else:
    TF_MT5 = {}


class MT5Connector:
    """
    Robust MT5 connection manager with retry logic and synthetic data fallback
    for dry-run/testing mode.
    """

    def __init__(self, dry_run: bool = False, max_retries: int = 3):
        self.dry_run = dry_run
        self.max_retries = max_retries
        self._connected = False
        self._login   = int(os.getenv("MT5_LOGIN", "0"))
        self._password = os.getenv("MT5_PASSWORD", "")
        self._server   = os.getenv("MT5_SERVER", "")

    # ── Connection ─────────────────────────────────────────────────────────

    def _initialize(self) -> bool:
        """
        Attach to an already-authorised terminal when it is already on the
        expected account; only force a credentialed login otherwise.

        Passing credentials unconditionally re-logs the terminal on every
        connect. With a stale password in `.env` that does not merely fail —
        it drops the terminal's working session and leaves it logged out, so
        one bad env value takes down a terminal that was running fine.

        Attaching is only safe if the terminal is on the account we were told
        to trade, so the login is verified before the attach is accepted;
        a mismatch falls through to an explicit login rather than silently
        trading whichever account happens to be open.
        """
        if mt5.initialize():
            info = mt5.account_info()
            if info and (self._login in (0, info.login)):
                logger.info(f"MT5 attached to running terminal — account {info.login}")
                return True
            found = info.login if info else None
            logger.warning(
                f"Terminal is on account {found}, expected {self._login} — "
                f"performing explicit login instead of attaching."
            )
            mt5.shutdown()

        return bool(mt5.initialize(login=self._login,
                                   password=self._password,
                                   server=self._server))

    def connect(self) -> bool:
        if self.dry_run:
            logger.info("DRY RUN mode — MT5 connection skipped.")
            self._connected = True
            return True
        if not MT5_AVAILABLE:
            logger.warning("MetaTrader5 package not installed. Running in dry-run mode.")
            self.dry_run = True
            self._connected = True
            return True
        for attempt in range(1, self.max_retries + 1):
            try:
                if not self._initialize():
                    logger.warning(f"MT5 init failed (attempt {attempt}): {mt5.last_error()}")
                    time.sleep(2 ** attempt)
                    continue
                self._connected = True
                info = mt5.account_info()
                logger.info(f"✅ MT5 connected — Account: {info.login} | "
                            f"Balance: {info.balance:.2f} {info.currency}")
                return True
            except Exception as e:
                logger.error(f"MT5 connect error: {e}")
                time.sleep(2 ** attempt)
        return False

    def disconnect(self):
        if not self.dry_run and MT5_AVAILABLE and self._connected:
            mt5.shutdown()
        self._connected = False
        logger.info("MT5 disconnected.")

    def is_connected(self) -> bool:
        if self.dry_run:
            return True
        if not MT5_AVAILABLE or not self._connected:
            return False
        try:
            return mt5.account_info() is not None
        except Exception:
            return False

    def ensure_connected(self) -> bool:
        if not self.is_connected():
            logger.warning("MT5 not connected — attempting reconnect...")
            return self.connect()
        return True

    # ── Market Data ────────────────────────────────────────────────────────

    def get_ohlcv(self, symbol: str, timeframe: str, bars: int = 300) -> Optional[pd.DataFrame]:
        """Fetch OHLCV data. Returns synthetic data in dry-run mode."""
        if self.dry_run:
            return self._synthetic_ohlcv(symbol, timeframe, bars)
        if not self.ensure_connected():
            return None
        if not MT5_AVAILABLE:
            return self._synthetic_ohlcv(symbol, timeframe, bars)
        tf = TF_MT5.get(timeframe)
        if tf is None:
            logger.error(f"Unknown timeframe: {timeframe}")
            return None
        try:
            rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)
            if rates is None or len(rates) == 0:
                logger.warning(f"No data for {symbol} {timeframe}: {mt5.last_error()}")
                return None
            df = pd.DataFrame(rates)
            df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
            df = df.rename(columns={'tick_volume': 'volume'})
            return df[['time', 'open', 'high', 'low', 'close', 'volume']].copy()
        except Exception as e:
            logger.error(f"get_ohlcv error {symbol} {timeframe}: {e}")
            return None

    def get_current_price(self, symbol: str) -> Optional[Tuple[float, float]]:
        """Returns (bid, ask) for symbol."""
        if self.dry_run:
            return (1.0, 1.0)
        if not self.ensure_connected() or not MT5_AVAILABLE:
            return None
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return None
        return (tick.bid, tick.ask)

    def get_account_info(self) -> Dict:
        if self.dry_run:
            return {"balance": 10000.0, "equity": 10000.0, "margin_free": 9000.0,
                    "profit": 0.0, "currency": "USD", "login": 0}
        if not self.ensure_connected() or not MT5_AVAILABLE:
            return {}
        info = mt5.account_info()
        if info is None:
            return {}
        return {"balance": info.balance, "equity": info.equity,
                "margin_free": info.margin_free, "profit": info.profit,
                "currency": info.currency, "login": info.login}

    def get_open_positions(self) -> List[Dict]:
        if self.dry_run or not MT5_AVAILABLE:
            return []
        if not self.ensure_connected():
            return []
        positions = mt5.positions_get()
        if positions is None:
            return []
        result = []
        for p in positions:
            result.append({
                "ticket": p.ticket, "symbol": p.symbol,
                "type": "BUY" if p.type == 0 else "SELL",
                "volume": p.volume, "open_price": p.price_open,
                "current_price": p.price_current, "profit": p.profit,
                "sl": p.sl, "tp": p.tp, "comment": p.comment,
                "open_time": datetime.fromtimestamp(p.time, tz=timezone.utc)
            })
        return result

    def get_symbol_info(self, symbol: str) -> Optional[Dict]:
        if self.dry_run or not MT5_AVAILABLE:
            return {"point": 0.01, "digits": 2, "trade_contract_size": 100.0,
                    "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01}
        if not self.ensure_connected():
            return None
        info = mt5.symbol_info(symbol)
        if info is None:
            return None
        return {"point": info.point, "digits": info.digits,
                "trade_contract_size": info.trade_contract_size,
                "volume_min": info.volume_min, "volume_max": info.volume_max,
                "volume_step": info.volume_step}

    # ── Order Management ───────────────────────────────────────────────────

    def place_order(self, symbol: str, order_type: str, volume: float,
                    price: float, sl: float, tp: float,
                    comment: str = "APEX AI") -> Optional[Dict]:
        """Place a market order. Returns result dict or None."""
        if self.dry_run:
            logger.info(f"[DRY RUN] {order_type} {volume} {symbol} @ {price:.4f} "
                        f"SL={sl:.4f} TP={tp:.4f}")
            return {"retcode": 10009, "order": 0, "volume": volume, "price": price}

        if not self.ensure_connected() or not MT5_AVAILABLE:
            return None

        mt5_type = mt5.ORDER_TYPE_BUY if order_type == "BUY" else mt5.ORDER_TYPE_SELL

        request = {
            "action":   mt5.TRADE_ACTION_DEAL,
            "symbol":   symbol,
            "volume":   float(volume),
            "type":     mt5_type,
            "price":    price,
            "sl":       sl,
            "tp":       tp,
            "deviation": 20,
            "magic":    MAGIC_MAIN,
            "comment":  comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result is None:
            logger.error(f"Order send failed: {mt5.last_error()}")
            return None
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Order rejected: retcode={result.retcode} comment={result.comment}")
            return None
        logger.info(f"✅ Order placed: {order_type} {volume} {symbol} ticket={result.order}")
        return {"retcode": result.retcode, "order": result.order,
                "volume": result.volume, "price": result.price}

    def close_position(self, ticket: int) -> bool:
        if self.dry_run or not MT5_AVAILABLE:
            logger.info(f"[DRY RUN] Close position {ticket}")
            return True
        if not self.ensure_connected():
            return False
        pos = mt5.positions_get(ticket=ticket)
        if not pos:
            return False
        p = pos[0]
        close_type = mt5.ORDER_TYPE_SELL if p.type == 0 else mt5.ORDER_TYPE_BUY
        price = mt5.symbol_info_tick(p.symbol).bid if p.type == 0 else mt5.symbol_info_tick(p.symbol).ask
        request = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol,
            "volume": p.volume, "type": close_type, "position": ticket,
            "price": price, "deviation": 20, "magic": MAGIC_MAIN,
            "comment": "APEX AI Close", "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        return result is not None and result.retcode == mt5.TRADE_RETCODE_DONE

    # ── Synthetic Data (dry-run) ───────────────────────────────────────────

    def _synthetic_ohlcv(self, symbol: str, timeframe: str, bars: int) -> pd.DataFrame:
        """Generate realistic synthetic OHLCV data for testing."""
        np.random.seed(hash(symbol + timeframe) % (2**31))
        prices_map = {"XAUUSD": 4710.0, "XAGUSD": 75.5, "USOil": 94.4, "BTCUSD": 78100.0}
        base = prices_map.get(symbol, 1.0)
        vol_scale = base * 0.001

        times, opens, highs, lows, closes, volumes = [], [], [], [], [], []
        price = base
        now = pd.Timestamp.now(tz='UTC')
        minutes = TF_MAP.get(timeframe, 60)

        for i in range(bars, 0, -1):
            t = now - pd.Timedelta(minutes=minutes * i)
            o = price
            c = o + np.random.normal(0, vol_scale)
            h = max(o, c) + abs(np.random.normal(0, vol_scale * 0.5))
            l = min(o, c) - abs(np.random.normal(0, vol_scale * 0.5))
            v = int(np.random.randint(100, 5000))
            times.append(t); opens.append(o); highs.append(h)
            lows.append(l); closes.append(c); volumes.append(v)
            price = c

        return pd.DataFrame({
            'time': times, 'open': opens, 'high': highs,
            'low': lows, 'close': closes, 'volume': volumes
        })
