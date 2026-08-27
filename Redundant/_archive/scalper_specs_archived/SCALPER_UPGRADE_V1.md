# ⚡ SCALPER AGENT UPGRADE: V1 (INSTITUTIONAL INTELLIGENCE)

This document outlines the proposed upgrades for the Scalper Agent (SA) to transition from a tactical micro-trader to a council-integrated institutional execution module.

---

## 🔷 UPGRADE 1: RDA REGIME AWARENESS (THE FILTER)

**Goal:** Prevent the Scalper from trading during high-risk "Stop Hunt" environments that the Main Agent successfully avoids.

### **Logic Integration:**
Before the Scalper Agent validates a Step 2 Trigger, it must perform a **Regime Consultation**:
1.  **Query `intelligence/regime_engine.py`**: Get the current market regime for the target symbol.
2.  **Manipulation Filter:** If the current regime is `MANIPULATION` or `STRESS` → **ABORT TRADE**.
3.  **Expansion Synergy:** If the regime is `EXPANSION` and the setup is trend-aligned -> **INCREASE CONFIDENCE** (risk may increase up to 1.25x, never above SA max risk cap).
4.  **Contraction Logic:** If the regime is `CONTRACTION`, only allow `SWEEP_REJECTION` setups (mean reversion).

### **Expected Benefit:**
*   Eliminates "Slow Bleed" losses where price drifts against the scalp without momentum.
*   Ensures SA only operates when the "Market Weather" is favorable for its specific strategy.

---

## 🔷 UPGRADE 2: LIA MACRO MAGNETS (THE TARGETS)

**Goal:** Improve Take Profit (TP) accuracy by aligning micro-scalps with larger institutional "Liquidity Magnets."

### **Logic Integration:**
When a setup is identified, the Scalper Agent will query the **`core/liquidity_engine.py`**:
1.  **Identify Macro Pools:** Fetch H1 and H4 Buy-Side Liquidity (BSL) and Sell-Side Liquidity (SSL) levels.
2.  **Proximity Check:** If a Macro Pool is within 1.5x of the Scalper's TP1 distance and within session-valid range -> **RE-ALIGN TP2**.
3.  **Target Alignment:** Instead of a fixed 1:2 RR for TP2, the Scalper will set TP2 exactly at the **Macro Liquidity Pool** to capture the full institutional "draw on liquidity."

### **Expected Benefit:**
*   Significantly increases the "Runner" (TP2) success rate.
*   Prevents closing trades prematurely right before a massive institutional move occurs.

---

## 🔷 UPGRADE 3: DISPLACEMENT CONFIRMATION (THE TRIGGER)

**Goal:** Ensure that "Breakout" scalps have actual force behind them.

### **Logic Integration:**
For `BOS_RETEST` setups, the Scalper will consult the **`core/displacement_engine.py`**:
1.  **Displacement Check:** Verify that the initial Break of Structure (BOS) was created by a **Displacement Candle** (body >= 65% of candle range and >= 1.2x M5 ATR baseline).
2.  **Fair Value Gap (FVG) Synergy:** Ensure a FVG was left behind during the breakout.
3.  **Validation:** If no displacement is detected → **SKIP RETEST**. The breakout is likely a "fakeout" or lacks institutional backing.

---

## 🔷 TECHNICAL ARCHITECTURE (COUNCIL-GATED CONSULTATION MODEL)

The Scalper is no longer fully isolated. It uses a **ReadOnly-Consultation + Council-Gate** model:

1.  **Execution Autonomy:** SA runs in its own process and manages its own MT5 connection and order timing.
2.  **Read-Only Council Access:** SA reads state from core engines updated by `maingpt.py` (no write-back).
3.  **Gating Rules:** RDA/Displacement can veto entries; LIA can modify TP2 targeting.
4.  **Freshness/Latency Guardrails:** Council snapshot must be <= 60s old and consultation latency <= 200ms.
5.  **Failure Policy:** If council data is stale/unavailable, SA skips trade (safe default).

---

## 🔷 IMPLEMENTATION PRIORITY

1.  **High:** RDA Integration (Regime Awareness + hard veto).
2.  **High:** Displacement Confirmation (Trigger Quality + anti-fakeout gate).
3.  **Medium:** LIA Macro Magnets (TP2 optimization).

---

## 🔷 ACCEPTANCE CRITERIA (IMPLEMENTATION DONE WHEN)

1.  Trades are blocked when regime is `MANIPULATION` or `STRESS`.
2.  `BOS_RETEST` is rejected if displacement thresholds are not met.
3.  TP2 is re-aligned to macro pool when proximity rule qualifies.
4.  Consultation freshness/latency checks are enforced before entry.
5.  Missing or stale council data causes a safe trade skip.

> **Instruction for AI:** When asked to "Implement Scalper Upgrade V1," refer to this document to modify `scalper_agent.py` and its trigger/logic sub-modules.
