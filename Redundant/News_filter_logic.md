# News Filter Logic Documentation

This document extracts and explains the logic used by the `CalendarFilter` module in the current agent, so it can be ported to other trading bots freely.

## Overview
The News Filter operates by fetching economic calendar events and automatically pausing trading during "high-impact" (or "red-folder") events. It also scales down risk on the first few trades executed immediately following a blackout period.

## Core Mechanics

### 1. Blackout Windows
Trading is paused (or blocked) around high-impact events for specific currencies.
- **Pre-news pause:** 30 minutes before the event.
- **Post-news pause:** 15 minutes after the event.
During this `[-30m, +15m]` window, the bot should reject new trades.

### 2. Currency-to-Symbol Mapping
The filter only blocks trading if the news event affects the traded symbol. For example:
- **XAUUSD** is blocked by **USD** and **XAU** events.
- **XAGUSD** is blocked by **USD** and **XAG** events.
- **USOIL** is blocked by **USD** and **OIL** events.
- **BTCUSD** is blocked by **BTC** events.

### 3. Risk Reduction Post-News
When the 15-minute post-news blackout ends, the market might still be highly volatile. To manage this risk:
- The **first 2 trades** taken after the news window resumes are sized at **0.5x (50%)** of the normal position size.
- After 2 trades, the sizing multiplier returns to **1.0x (100%)**.

### 4. Data Source and Caching
- **Source:** The bot fetches data from a public JSON calendar compatible with ForexFactory (e.g., `https://nfs.faireconomy.media/ff_calendar_thisweek.json`).
- **Filtering:** Only events marked with `"impact": "high"` or `"red"` are processed.
- **Caching:** The event list is cached for **1 hour** to avoid rate limits or unnecessary API calls.
- **Failsafe:** If the API is unreachable, the system fails open (it returns an empty event list and does not block trading).

## Implementation Steps for Another Bot

To implement this in a different bot, follow this structure:

1. **State Management:**
   - Keep a variable for the last fetch time and the cached events array.
   - Keep a counter mapping `Symbol -> PostNewsTradeCount` to handle the 0.5x risk multiplier.

2. **Fetching Logic (Runs Periodically or on Trade Request):**
   - Check if 1 hour has passed since the last fetch.
   - If yes, fetch the JSON data. Parse the `datetime_utc`, `country` (currency), and `impact`. Store only high-impact events.

3. **Trade Validation (`is_safe_to_trade`):**
   - Before taking a trade on a `Symbol`, determine its mapped currencies (e.g., EURUSD -> EUR, USD).
   - Get the current UTC time.
   - Iterate over cached events matching the mapped currencies.
   - Calculate time delta: `MinutesDelta = (EventTime - CurrentTime)`.
   - If `MinutesDelta` is between `-15` and `+30`, return `False` (Do not trade).

4. **Risk Multiplier (`get_position_size_multiplier`):**
   - If `is_safe_to_trade` returns `True`, check the `PostNewsTradeCount` for that symbol.
   - If `Count < 2`, return `0.5` and increment the `Count`.
   - Otherwise, return `1.0`.

5. **Resetting Counters:**
   - Ensure the `PostNewsTradeCount` is properly reset to 0 whenever a new high-impact event window begins, so the next resumption is caught.

## Advantages of this Approach
- **Lightweight:** Uses a single public API endpoint and minimal memory.
- **Safe:** Fails open so trading is never permanently halted due to an API outage.
- **Dynamic Risk:** Doesn't just binary block trading, but intelligently scales risk down right after volatility.
