# APEX AI — VP-Gated XAUUSD Scalper

APEX AI is a Python/MetaTrader 5 trading system with an isolated scalper pool and a separate main-system pool. The current scalper implementation evaluates XAUUSD on M15, confirms on M5, and applies higher-timeframe context before a trade can be considered.

The repository contains the completed top-down volume-profile and `SWEEP_REJECTION` location-gating work. It is an execution and research codebase, not investment advice. Trading carries substantial risk; use a DEMO account until the complete runtime and operational process have been independently validated.

## Current preferred DEMO profile

Run from `apex_ai` with the verified Python 3.14 interpreter:

```powershell
cd apex_ai
py -3.14 -E scalper_agent.py `
  --pool 900 `
  --risk 0.03 `
  --symbols XAUUSD `
  --interval 30 `
  --loss-limit 100.0 `
  --pool-mode FRESH `
  --sweep-location-config config.json `
  --market-location-mode ACTIVE
```

The equivalent explicit interpreter command is:

```powershell
& "C:\Users\nauman.afzal.ORIENTPET\AppData\Local\Python\pythoncore-3.14-64\python.exe" -E scalper_agent.py --pool 900 --risk 0.03 --symbols XAUUSD --interval 30 --loss-limit 100.0 --pool-mode FRESH --sweep-location-config config.json --market-location-mode ACTIVE
```

This profile is non-dry-run by default. Add `--dry-run` only when paper execution is explicitly intended. `SWEEP_REJECTION` requires `--market-location-mode ACTIVE` and an active named-liquidity policy in `config.json`; it cannot silently fall back to an unrestricted mode.

## Runtime pipeline

```text
M15 trigger frame
    ↓
M5 reaction and confirmation
    ↓
W1/H4 active-auction selection
    ↓
Detailed lower-timeframe volume profile and structural S/R
    ↓
Named-liquidity and location permission
    ↓
Session, news, cooldown, risk, and geometry gates
    ↓
Final executable quote validation
    ↓
MT5 DEMO order submission
```

The scalper uses an isolated logical capital pool and magic number `88880`. The Trade Guardian manages existing positions but never opens trades. The main system (`maingpt.py`) is independent and uses its own pool and controls.

## Repository layout

| Path | Purpose |
|---|---|
| `apex_ai/scalper_agent.py` | Live scalper entry point and runtime loop |
| `apex_ai/scalper/market_location.py` | Top-down active-auction and VP/SR snapshots |
| `apex_ai/scalper/location_permission.py` | Directional location permission and reaction gates |
| `apex_ai/scalper/sweep_location.py` | Named-liquidity policy and frozen-location contract |
| `apex_ai/scalper/trigger_engine.py` | Trigger selection, geometry, and final quote checks |
| `apex_ai/backtest_scalper.py` | Historical replay using the shared decision path |
| `apex_ai/trade_guardian_agent.py` | Position management and trailing controls |
| `apex_ai/config.json` | Runtime location-policy configuration |
| `apex_ai/tests/` | Unit and parity tests |
| `APEX_SWEEP_REJECTION_TECHNICAL_DOCUMENT.md` | Detailed implementation and evidence record |

## Setup

1. Install Python 3.14 and MetaTrader 5.
2. Install dependencies:

   ```powershell
   cd apex_ai
   py -3.14 -E -m pip install -r requirements.txt
   ```

3. Copy `.env.template` to `.env` and provide the DEMO credentials:

   ```text
   MT5_LOGIN=your_demo_login
   MT5_PASSWORD=your_demo_password
   MT5_SERVER=your_demo_server
   MT5_MODE=DEMO
   ```

Never commit `.env` or credentials.

## Verification

Run the complete test suite from `apex_ai`:

```powershell
py -3.14 -E -m unittest discover -s tests
```

Compile the package from the repository root:

```powershell
py -3.14 -E -m compileall -q apex_ai
```

Before committing changes, also run:

```powershell
git diff --check
```

## Evidence and limitations

The implementation records candidate-funnel, location, quote, and execution telemetry for later analysis. Current repository evidence is not a profitability guarantee: raw sweep detections and stable candidates must not be presented as executed-trade performance. Review the technical document and research notes before changing strategy parameters or enabling additional trigger families.

## GitHub status

The completed VP-gated `SWEEP_REJECTION` implementation is on branch `l015-leg-confluence`. The preferred DEMO profile is documented in this README and the operational runbooks.
