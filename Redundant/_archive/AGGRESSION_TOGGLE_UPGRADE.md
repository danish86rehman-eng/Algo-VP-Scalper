# ⚡ APEX UPGRADE V2: DYNAMIC AGGRESSION TOGGLE (THE SNIPER PROTOCOL)

## 🎯 The Concept
Currently, the APEX AI system (both Main and Scalper) uses a **Static Risk Model** (e.g., risking exactly 0.3% of the pool per trade). This is extremely safe, but it treats a "perfect" setup the same as an "average" setup.

The **Aggression Toggle** introduces **Dynamic Risk Scaling**. When the core intelligence engines reach a state of 100% confluence (the "Golden Setup"), the AI automatically toggles into an aggressive state, increasing the lot size to capitalize heavily on the high-probability opportunity.

---

## 🔷 THE "GOLDEN SETUP" CONDITIONS

To flip the Aggression Toggle from `NORMAL` to `SNIPER_MODE`, all of the following core engines must align with high clarity:

1.  **Regime Detection Agent (RDA): `EXPANSION`**
    *   The market is in a clear, high-momentum directional state. (No ranges, no transition noise).
2.  **Market Structure Agent (MSA): `TREND ALIGNMENT`**
    *   M15, H1, and H4 trends must all point in the exact same direction (e.g., all Bullish).
3.  **Liquidity Intelligence Agent (LIA): `MACRO VOID`**
    *   There must be a massive, untapped Liquidity Pool (BSL/SSL) in the direction of the trend, acting as a high-probability "Magnet."
4.  **Displacement Engine: `INSTITUTIONAL MOMENTUM`**
    *   The setup must be triggered by an institutional displacement candle (momentum score > 0.85).

---

## 🔷 DYNAMIC RISK SCALING LOGIC

When the **Golden Setup** is detected, the Capital Risk Governor (CRG) dynamically adjusts the parameters for that specific trade:

*   **Normal Trade Risk:** 0.3% of capital pool.
*   **Sniper Mode Risk:** **1.0% to 1.5%** of capital pool.

### **The Math (Example on a $10,000 Pool):**
*   **Average Setup (Normal Risk):** Risking $30 to make $60.
*   **Golden Setup (Sniper Mode):** Risking $150 to make $300+.

This ensures that the system punches hard only when the mathematical probability of winning is overwhelmingly in its favor.

---

## 🔷 SAFETY RAILS (PREVENTING BLOWOUTS)

Increasing risk requires increasing safety. The Aggression Toggle comes with strict hardware-level constraints:

1.  **The "One Shot" Rule:** Only ONE `SNIPER_MODE` trade can be active at any given time across the entire APEX system.
2.  **Drawdown Veto:** If the account is in a Daily Drawdown of more than 2%, the Aggression Toggle is hard-locked to `OFF`. The system must earn the right to be aggressive by being in profit.
3.  **TGA Hyper-Trailing:** Trades executed under `SNIPER_MODE` use a tighter trailing stop sequence via the Trade Guardian Agent (TGA) to lock in profit faster, ensuring a high-risk trade never turns into a full loss once it goes into profit.

---

## 🔷 IMPLEMENTATION ROADMAP (FOR THE AI)

If you decide to proceed with this upgrade in the future, instruct the AI to:

1.  **Modify `CapitalRiskGovernor (CRG)`:** Add a `calculate_dynamic_risk()` method that accepts a `confluence_score` (0.0 to 1.0).
2.  **Modify `Main Agent` / `Scalper Agent`:** Create a confluence checker before trade execution. If `confluence_score == 1.0`, request the upgraded risk percentage from CRG.
3.  **Modify `TGA`:** Add a flag for `is_sniper_trade`. If true, initiate break-even protocol at 0.5R instead of 1.0R.
