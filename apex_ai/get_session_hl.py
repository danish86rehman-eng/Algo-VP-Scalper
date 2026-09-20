import MetaTrader5 as mt5
import os
import pandas as pd

from datetime import datetime, timezone, timedelta, time
from zoneinfo import ZoneInfo
from dotenv import load_dotenv


UTC = ZoneInfo("UTC")
LONDON = ZoneInfo("Europe/London")
NEW_YORK = ZoneInfo("America/New_York")


def get_session_bounds(now_utc, tz, start_time, end_time, completed_only=False):
    """
    Return exact UTC boundaries for the latest relevant session.

    completed_only=False:
        Current session if active, otherwise latest session.

    completed_only=True:
        Most recently completed session only.
    """

    now_local = now_utc.astimezone(tz)
    session_date = now_local.date()

    start_local = datetime.combine(session_date, start_time, tzinfo=tz)
    end_local = datetime.combine(session_date, end_time, tzinfo=tz)

    # Overnight session
    if end_time <= start_time:
        end_local += timedelta(days=1)

    # Today's session has not started yet -> use previous session
    if now_local < start_local:
        start_local -= timedelta(days=1)
        end_local -= timedelta(days=1)

    # If current session is still active but we require completed session,
    # move back one full session day.
    if completed_only and start_local <= now_local < end_local:
        start_local -= timedelta(days=1)
        end_local -= timedelta(days=1)

    return (
        start_local.astimezone(UTC),
        end_local.astimezone(UTC)
    )


def session_hl(df, start_utc, end_utc):
    mask = (df.index >= start_utc) & (df.index < end_utc)
    session_df = df.loc[mask]

    if session_df.empty:
        return "N/A", "N/A"

    return session_df["high"].max(), session_df["low"].min()


def format_utc_window(start_utc, end_utc):
    return f"{start_utc:%H:%M}-{end_utc:%H:%M}"


def main():

    env_path = os.path.join(os.path.dirname(__file__), ".env")
    load_dotenv(env_path)

    login = int(os.getenv("MT5_LOGIN", "0"))
    password = os.getenv("MT5_PASSWORD", "")
    server = os.getenv("MT5_SERVER", "")

    if not mt5.initialize(
        login=login,
        password=password,
        server=server
    ):
        print("MT5 init failed:", mt5.last_error())
        return

    try:
        now = datetime.now(timezone.utc)

        # Fetch enough history to safely cover prior sessions/weekends.
        rates = mt5.copy_rates_from(
            "XAUUSD",
            mt5.TIMEFRAME_M15,
            now,
            800
        )

        if rates is None or len(rates) == 0:
            print("Could not retrieve rates.")
            return

        df = pd.DataFrame(rates)

        df["time"] = pd.to_datetime(
            df["time"],
            unit="s",
            utc=True
        )

        df.set_index("time", inplace=True)

        # --------------------------------------------------
        # ASIA
        # Custom broad Asia session:
        # 22:00 → 08:00 UTC
        # --------------------------------------------------

        asia_start, asia_end = get_session_bounds(
            now,
            UTC,
            time(22, 0),
            time(8, 0)
        )

        # --------------------------------------------------
        # LONDON
        # 08:00 → 17:00 London local time
        # Automatically handles BST/GMT.
        # --------------------------------------------------

        lon_start, lon_end = get_session_bounds(
            now,
            LONDON,
            time(8, 0),
            time(17, 0)
        )

        # --------------------------------------------------
        # NEW YORK
        # 08:00 → 17:00 New York local time
        # Automatically handles EDT/EST.
        # --------------------------------------------------

        ny_start, ny_end = get_session_bounds(
            now,
            NEW_YORK,
            time(8, 0),
            time(17, 0)
        )

        # --------------------------------------------------
        # ICT LONDON KILLZONE
        # 02:00 → 05:00 New York time
        # --------------------------------------------------

        lon_kz_start, lon_kz_end = get_session_bounds(
            now,
            NEW_YORK,
            time(2, 0),
            time(5, 0)
        )

        # --------------------------------------------------
        # ICT NEW YORK KILLZONE
        # 07:00 → 10:00 New York time
        # --------------------------------------------------

        ny_kz_start, ny_kz_end = get_session_bounds(
            now,
            NEW_YORK,
            time(7, 0),
            time(10, 0)
        )

        # --------------------------------------------------
        # SESSION HIGH / LOW
        # --------------------------------------------------

        asia_h, asia_l = session_hl(
            df, asia_start, asia_end
        )

        lon_h, lon_l = session_hl(
            df, lon_start, lon_end
        )

        ny_h, ny_l = session_hl(
            df, ny_start, ny_end
        )

        lon_kz_h, lon_kz_l = session_hl(
            df, lon_kz_start, lon_kz_end
        )

        ny_kz_h, ny_kz_l = session_hl(
            df, ny_kz_start, ny_kz_end
        )

        # --------------------------------------------------
        # PREVIOUS COMPLETED NEW YORK SESSION
        # Important for prior-NY BSL/SSL liquidity logic.
        # --------------------------------------------------

        prev_ny_start, prev_ny_end = get_session_bounds(
            now,
            NEW_YORK,
            time(8, 0),
            time(17, 0),
            completed_only=True
        )

        prev_ny_h, prev_ny_l = session_hl(
            df,
            prev_ny_start,
            prev_ny_end
        )

        # --------------------------------------------------
        # OUTPUT
        # --------------------------------------------------

        print("=== MAJOR SESSIONS ===")

        print(
            f"Asian Session  "
            f"({format_utc_window(asia_start, asia_end)} UTC)"
            f" | High: {asia_h} | Low: {asia_l}"
        )

        print(
            f"London Session "
            f"({format_utc_window(lon_start, lon_end)} UTC)"
            f" | High: {lon_h} | Low: {lon_l}"
        )

        print(
            f"NY Session     "
            f"({format_utc_window(ny_start, ny_end)} UTC)"
            f" | High: {ny_h} | Low: {ny_l}"
        )

        print()

        print("=== APEX KILLZONES ===")

        print(
            f"London KZ      "
            f"({format_utc_window(lon_kz_start, lon_kz_end)} UTC)"
            f" | High: {lon_kz_h} | Low: {lon_kz_l}"
        )

        print(
            f"NY KZ          "
            f"({format_utc_window(ny_kz_start, ny_kz_end)} UTC)"
            f" | High: {ny_kz_h} | Low: {ny_kz_l}"
        )

        print()

        print("=== PRIOR NEW YORK LIQUIDITY ===")

        print(
            f"Previous NY    "
            f"({prev_ny_start:%Y-%m-%d %H:%M} → "
            f"{prev_ny_end:%Y-%m-%d %H:%M} UTC)"
            f" | BSL: {prev_ny_h} | SSL: {prev_ny_l}"
        )

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()



"""
import MetaTrader5 as mt5
import os
import pandas as pd
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

def get_session_hl(df, start_hour, end_hour):
    if start_hour > end_hour:
        mask = (df.index.hour >= start_hour) | (df.index.hour < end_hour)
    else:
        mask = (df.index.hour >= start_hour) & (df.index.hour < end_hour)
    
    session_df = df[mask]
    if session_df.empty:
        return "N/A", "N/A"
    return session_df['high'].max(), session_df['low'].min()

def main():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    load_dotenv(env_path)
    login = int(os.getenv("MT5_LOGIN", "0"))
    password = os.getenv("MT5_PASSWORD", "")
    server = os.getenv("MT5_SERVER", "")
    
    if not mt5.initialize(login=login, password=password, server=server):
        print("MT5 init failed")
        return

    now = datetime.now(timezone.utc)
    rates = mt5.copy_rates_from('XAUUSD', mt5.TIMEFRAME_M15, now, 100)
    if rates is None or len(rates) == 0:
        print("Could not retrieve rates.")
        mt5.shutdown()
        return

    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    df.set_index('time', inplace=True)
    
    last_24h = now - timedelta(hours=24)
    df = df[df.index >= last_24h]

    asia_h, asia_l = get_session_hl(df, 22, 8)
    lon_h, lon_l = get_session_hl(df, 7, 16)
    ny_h, ny_l = get_session_hl(df, 12, 21)
    
    lon_kz_h, lon_kz_l = get_session_hl(df, 7, 10)
    ny_kz_h, ny_kz_l = get_session_hl(df, 12, 15)
    
    print("=== MAJOR SESSIONS (Last 24h UTC) ===")
    print(f"Asian Session  (22:00-08:00) | High: {asia_h} | Low: {asia_l}")
    print(f"London Session (07:00-16:00) | High: {lon_h} | Low: {lon_l}")
    print(f"NY Session     (12:00-21:00) | High: {ny_h} | Low: {ny_l}")
    print("")
    print("=== APEX KILLZONES ===")
    print(f"London KZ      (07:00-10:00) | High: {lon_kz_h} | Low: {lon_kz_l}")
    print(f"NY KZ          (12:00-15:00) | High: {ny_kz_h} | Low: {ny_kz_l}")

    mt5.shutdown()

if __name__ == "__main__":
    main()

    """
