"""
APEX AI — MT5 Quick Connect Setup
Run this script once to create your .env file with MT5 credentials.
Usage: python setup_mt5.py
"""
import os
from pathlib import Path

print("\n" + "="*55)
print("   APEX AI — MT5 Connection Setup")
print("="*55)
print()
print("Your broker (from previous session): Exness-MT5Trial2")
print()

login    = input("  MT5 Account Login (number): ").strip()
password = input("  MT5 Account Password:       ").strip()
server   = input(f"  MT5 Server [Exness-MT5Trial2]: ").strip() or "Exness-MT5Trial2"

env_path = Path(".env")
env_content = f"MT5_LOGIN={login}\nMT5_PASSWORD={password}\nMT5_SERVER={server}\n"
env_path.write_text(env_content)

print()
print(f"  .env file written: {env_path.absolute()}")
print()

# Quick connection test
print("  Testing MT5 connection...")
try:
    import MetaTrader5 as mt5
    if mt5.initialize(login=int(login), password=password, server=server):
        info = mt5.account_info()
        print(f"\n  CONNECTED!")
        print(f"  Account : {info.login}")
        print(f"  Balance : ${info.balance:,.2f} {info.currency}")
        print(f"  Leverage: 1:{info.leverage}")
        print(f"  Server  : {info.server}")
        mt5.shutdown()
        print("\n  Run:  python maingpt.py")
    else:
        err = mt5.last_error()
        mt5.shutdown()
        print(f"\n  Connection FAILED: {err}")
        print("  Check login/password/server and try again.")
except ImportError:
    print("\n  MetaTrader5 package not found.")
    print("  Run: pip install MetaTrader5")
    print("  Then run: python maingpt.py")
print()
