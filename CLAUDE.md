# CLAUDE.md – SPY Paper Trading Agent

## Project Overview

This is a closed-loop autonomous paper trading agent that trades SPY on Alpaca's Paper Trading API. The core experiment is **not profit maximization** – it is observing whether Claude's reasoning and strategy improve over time through structured self-reflection (daily and strategy reviews).

Claude is the reasoning engine. Python handles all infrastructure: data fetching, feature calculation, order execution, scheduling, logging, and notifications.

---

## Architecture

```
trading-agent/
├── CLAUDE.md                    ← this file
├── Dockerfile
├── docker-compose.yml
├── .env                         ← API keys (never commit)
├── requirements.txt
├── config.py                    ← all constants and parameters
├── main.py                      ← entry point + APScheduler
│
├── data/
│   ├── market.py                ← Alpaca Data API calls (raw bars)
│   └── features.py              ← computes all 22 features from raw data
│
├── execution/
│   └── orders.py                ← bracket orders, position management
│
├── reasoning/
│   ├── prompts.py               ← all Claude prompt templates
│   ├── decision.py              ← trading loop logic (called 3x/day)
│   ├── daily_review.py          ← daily review logic (22:15 MEZ)
│   └── strategy_review.py       ← strategy review (every 20 decisions)
│
├── storage/
│   └── db.py                    ← SQLite interface (journal.db)
│
├── notifications/
│   └── ntfy.py                  ← ntfy.sh push notifications
│
└── journal.db                   ← SQLite database (single file)
```

---

## Schedule (MEZ / Vienna Time)

| Time  | Action |
|-------|--------|
| 16:00 | Decision 1 – 30min after US market open |
| 19:30 | Decision 2 – mid/late session |
| 22:10 | Decision 3 – after market close (full action space, CLOSED flag) |
| 22:15 | Daily Review |

**Strategy Review** is triggered automatically after every 20th decision (not time-based).

US market hours: 15:30–22:00 MEZ. All three decision times are within or immediately after regular hours. The 22:10 decision receives a `market_status: "CLOSED"` flag so Claude can make an informed overnight hold/exit decision.

---

## Trading Parameters

| Parameter | Value |
|-----------|-------|
| Asset | SPY (SPDR S&P 500 ETF) |
| Account | Alpaca Paper Trading |
| Position size | 20% of portfolio per trade |
| Stop loss | -2.5% (bracket order, server-side) |
| Take profit | None – Claude decides exit |
| Max simultaneous positions | 1 |
| Directions | Long + Short |
| Portfolio drawdown stop | None – full experiment visibility desired |

**SL is placed as a bracket order via Alpaca – Python handles this, Claude never calculates SL levels.**

---

## Action Space

Claude must return exactly one of these four actions per decision:

| Action | Condition | Effect |
|--------|-----------|--------|
| `LONG` | No position open | Opens long position with bracket order |
| `LONG` | Short position open | Python closes short, opens long |
| `SHORT` | No position open | Opens short position with bracket order |
| `SHORT` | Long position open | Python closes long, opens short |
| `HOLD` | Any position open | Does nothing, must include `exit_trigger` |
| `CLOSE` | Any position open | Closes current position, goes to cash |

**`HOLD` without an `exit_trigger` field is invalid.** Python must reject and log an error if `exit_trigger` is missing on a HOLD decision.

When `market_status = "CLOSED"` (22:10 decision), all four actions remain available. Claude must explicitly reason about overnight risk in its reasoning field.

---

## Feature Set (Input to Claude)

All features are pre-computed by `data/features.py`. Claude never receives raw OHLCV bars. Features are passed as a structured dict.

### Price & Market Position
```python
"current_price"         # float – current SPY price
"pct_from_52w_high"     # float – e.g. -8.8 (percent below 52w high)
"pct_from_52w_low"      # float – e.g. +16.3 (percent above 52w low)
```

### Trend
```python
"trend_daily"           # str – "UPTREND" | "DOWNTREND" | "SIDEWAYS"
                        # rule: UPTREND if close > 20D MA > 50D MA
                        #       DOWNTREND if close < 20D MA < 50D MA
                        #       else SIDEWAYS
"trend_weekly"          # str – same classification over last 5 weeks
"distance_from_20ma"    # float – percent distance from 20-day MA (+ = above)
"distance_from_50ma"    # float – percent distance from 50-day MA (+ = above)
```

### Momentum
```python
"price_change_1d"       # float – percent change vs yesterday close
"price_change_5d"       # float – percent change vs 5 days ago
"price_change_20d"      # float – percent change vs 20 days ago
"gap_today"             # float – today open vs yesterday close (percent)
```

### Volatility
```python
"volatility_regime"     # str – "LOW" | "NORMAL" | "HIGH"
                        # based on 14D realized vol vs 60D average
"weekly_range_pct"      # float – (high - low) / low over last 5 days (percent)
```

### Volume
```python
"volume_ratio_today"    # float – today volume / 20D avg volume (e.g. 1.3 = 30% above avg)
"volume_trend_5d"       # str – "INCREASING" | "DECREASING" | "FLAT"
```

### Intraday
```python
"session_open_move"     # float – price change in first 30min after open (percent)
"price_vs_open"         # float – current price vs today open (percent)
"intraday_range_pct"    # float – (session high - session low) / open so far (percent)
```

### Macro
```python
"vix_current"           # float – current VIX level (via VIXY proxy on Alpaca)
"vix_change_1d"         # float – VIX change vs yesterday
"spy_vs_qqq_5d"         # str – "SPY_OUTPERFORMS" | "QQQ_OUTPERFORMS" | "EQUAL"
                        # based on 5D relative performance
```

### Position & History
```python
"current_position"      # str – "LONG" | "SHORT" | "CASH"
"position_pnl"          # float – current unrealized P&L in percent (0.0 if CASH)
"position_age_decisions" # int – how many decisions this position has been open
"last_3_decisions"      # list – last 3 decisions with outcomes:
                        # [{"action": "LONG", "reasoning_summary": "...",
                        #   "confidence": 0.72, "outcome_pct": +0.4}, ...]
"current_strategy"      # str – free text from last strategy review
                        #       "No strategy defined yet." if first 20 decisions
"market_status"         # str – "OPEN" | "CLOSED"
"decision_number"       # int – total decision count (for strategy review trigger)
```

---

## Claude's Output Format (Decision)

Claude must return a single valid JSON object. No prose before or after.

```json
{
  "action": "LONG",
  "reasoning": "Multi-sentence explanation of why this action was chosen. Must reference specific features from the input. Must acknowledge any contradicting signals.",
  "confidence": 0.72,
  "exit_trigger": "Will close if price_vs_open drops below -0.8% or if VIX spikes above 22.",
  "concerns": "Volume is below average which weakens the signal."
}
```

**Field rules:**
- `action`: exactly one of `LONG` | `SHORT` | `HOLD` | `CLOSE`
- `reasoning`: minimum 3 sentences, must reference at least 2 specific features by name
- `confidence`: float 0.0–1.0, must reflect genuine uncertainty (avoid 0.9+ unless very clear setup)
- `exit_trigger`: **required if action is HOLD**, omit otherwise
- `concerns`: always required, even if minor – forces honest reasoning

**Python rejects and re-prompts (max 1 retry) if:**
- JSON is malformed
- `action` is not one of the four valid values
- `action` is `HOLD` and `exit_trigger` is missing or empty
- `confidence` is outside 0.0–1.0

---

## Daily Review Format

Called at 22:15 MEZ. Claude receives all decisions and outcomes from the current trading day.

Input:
```python
{
  "date": "2026-04-20",
  "decisions_today": [...],   # all decisions with outcomes
  "daily_pnl_pct": -0.3,
  "positions_closed": 2,
  "sl_hits": 0
}
```

Claude returns free text (no JSON required). The review must address:
1. What happened today – which decisions were good/bad and why
2. Which features were most relevant
3. Any patterns noticed in own reasoning
4. What to watch for tomorrow

Stored in `reviews` table with `type = "daily"`.

---

## Strategy Review Format

Triggered automatically when `decision_number % 20 == 0`. This is the core self-improvement mechanism of the experiment.

Input:
```python
{
  "decisions": [...],          # last 20 decisions with full features + outcomes
  "daily_reviews": [...],      # daily reviews since last strategy review
  "current_strategy": "...",   # current strategy text
  "win_rate": 0.55,            # last 20 decisions
  "avg_pnl_per_decision": 0.12 # percent
}
```

Claude returns free text strategy description. Must address:
1. What patterns emerged in the last 20 decisions
2. Which features proved most predictive
3. Where was confidence miscalibrated (high confidence + bad outcome, or low confidence + good outcome)
4. Explicit strategy adjustments for next 20 decisions
5. What to specifically watch for

This text becomes the new `current_strategy` and is included in every subsequent decision prompt.

---

## Database Schema (journal.db)

```sql
CREATE TABLE decisions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp             TEXT NOT NULL,           -- ISO format, MEZ
    decision_number       INTEGER NOT NULL,
    market_status         TEXT NOT NULL,           -- OPEN | CLOSED
    action                TEXT NOT NULL,           -- LONG | SHORT | HOLD | CLOSE
    reasoning             TEXT NOT NULL,
    confidence            REAL NOT NULL,
    exit_trigger          TEXT,                    -- only on HOLD
    concerns              TEXT,
    features_snapshot     TEXT NOT NULL,           -- JSON string of all 22 features
    order_id              TEXT                     -- Alpaca order ID, null if HOLD/CLOSE failed
);

CREATE TABLE outcomes (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id           INTEGER REFERENCES decisions(id),
    exit_timestamp        TEXT,
    exit_price            REAL,
    pnl_pct               REAL,
    exit_reason           TEXT                     -- SL_HIT | CLAUDE_EXIT | STILL_OPEN
);

CREATE TABLE reviews (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp             TEXT NOT NULL,
    type                  TEXT NOT NULL,           -- daily | strategy
    content               TEXT NOT NULL,
    decisions_covered     INTEGER,
    win_rate              REAL                     -- only for strategy reviews
);

CREATE TABLE strategy (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    updated_at            TEXT NOT NULL,
    content               TEXT NOT NULL,
    decision_count        INTEGER NOT NULL         -- after how many decisions updated
);
```

**Only one active strategy exists at a time.** Always read `SELECT content FROM strategy ORDER BY id DESC LIMIT 1`.

---

## Order Execution Logic (execution/orders.py)

`orders.py` is responsible for all Alpaca interactions. Key behaviors to implement:

- **LONG:** Close any open short position first, then place a bracket buy order with SL at `entry_price * (1 - STOP_LOSS_PCT)`.
- **SHORT:** Close any open long position first, then place a bracket sell order with SL at `entry_price * (1 + STOP_LOSS_PCT)`. Note: for short bracket orders, the stop_loss price must be *above* entry.
- **CLOSE:** Close the current open position via Alpaca's close position endpoint.
- **HOLD:** No order placed.

Position quantity is always `int((portfolio_value * POSITION_SIZE_PCT) / current_price)`.

**Important:** Alpaca does not expose child orders (SL) in standard `get_orders()`. SL status must be queried by parent order ID. Poll every 5 minutes during market hours to detect SL hits and log them to the `outcomes` table with `exit_reason = "SL_HIT"`.

---

## Notifications (ntfy.py)

Every decision and review is sent via ntfy.sh as a push notification. Topic is configured in `.env`.

**Decision notification includes:**
- Action + confidence
- Full reasoning text
- exit_trigger (if HOLD)
- concerns
- Current position P&L

**Daily review notification includes:**
- Full review text

**Strategy review notification includes:**
- Full new strategy text
- Win rate of last 20 decisions

```python
import requests

def notify(title: str, message: str, priority: str = "default"):
    requests.post(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=message.encode("utf-8"),
        headers={
            "Title": title,
            "Priority": priority,   # "urgent" for SL hits
            "Tags": "chart_with_upwards_trend"
        }
    )
```

SL hits (detected via order status polling) should be sent with `priority="urgent"`.

---

## config.py – All Constants

```python
# Alpaca
ALPACA_API_KEY        = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY     = os.getenv("ALPACA_SECRET_KEY")
ALPACA_PAPER          = True

# Anthropic
ANTHROPIC_API_KEY     = os.getenv("ANTHROPIC_API_KEY")
CLAUDE_MODEL          = "claude-opus-4-7"

# ntfy
NTFY_TOPIC            = os.getenv("NTFY_TOPIC")

# Trading
SYMBOL                = "SPY"
POSITION_SIZE_PCT     = 0.20
STOP_LOSS_PCT         = 0.025      # 2.5%, always positive – logic handles direction

# Lookback periods for feature calculation
LOOKBACK_DAILY        = 60         # days
LOOKBACK_4H           = 20         # days
LOOKBACK_1H           = 5          # days

# Schedule (MEZ / Europe/Vienna)
DECISION_TIMES        = [
    {"hour": 16, "minute": 0},
    {"hour": 19, "minute": 30},
    {"hour": 22, "minute": 10},
]
DAILY_REVIEW_TIME     = {"hour": 22, "minute": 15}

# Strategy review
STRATEGY_REVIEW_EVERY = 20         # decisions
```

---

## Environment Variables (.env)

```
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
ANTHROPIC_API_KEY=...
NTFY_TOPIC=spy-trading-agent       # choose a unique topic name
```

Never commit `.env`. Add to `.gitignore`.

---

## Docker

```dockerfile
FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "main.py"]
```

```yaml
# docker-compose.yml
services:
  trading-bot:
    build: .
    restart: unless-stopped
    env_file: .env
    volumes:
      - ./journal.db:/app/journal.db
    timezone: Europe/Vienna
```

`journal.db` is mounted as a volume so it persists across container rebuilds.

---

## Key Implementation Notes for Claude Code

1. **Never hardcode API keys.** Always use `os.getenv()` via config.py.

2. **Feature calculation is Python's job, not Claude's.** Claude receives finished features only. Never pass raw bars to the Claude prompt.

3. **Timezone is always Europe/Vienna (MEZ/MESZ).** Use `pytz` or `zoneinfo` consistently. APScheduler must be initialized with `timezone="Europe/Vienna"`.

4. **SQLite writes must be atomic.** Use transactions. A failed order execution must not result in a logged decision without an order_id – log the error and skip.

5. **Claude API calls use `claude-opus-4-7`.** Opus 4.7 is used for all decisions, daily reviews, and strategy reviews – reasoning quality is central to the experiment. Never downgrade to Sonnet or Haiku for cost reasons given the low token volume (~180k tokens/month).

6. **Retry logic:** If Claude returns malformed JSON or invalid action, retry exactly once with an appended instruction: `"Your previous response was invalid because: {reason}. Return only valid JSON."` If second attempt also fails, skip the decision, log the error, send ntfy notification.

7. **Market hours check:** Before every decision, verify market status via Alpaca. If market is unexpectedly closed (holiday), skip the decision and notify.

8. **Short selling on Alpaca Paper requires margin account.** Verify the paper account has margin enabled. If short order is rejected, log and notify.

9. **SL hit detection via reconciliation, not polling.** At every decision call, compare Alpaca's current position against `journal.db`. If Alpaca shows CASH but the database shows an open position, the SL was hit. Log the outcome to the `outcomes` table with `exit_reason = "SL_HIT"` and send an urgent ntfy notification. No background polling loop needed.

10. **No hardcoded logic in prompts.py.** Prompt templates must be pure strings with placeholders. All feature formatting, JSON serialization, and context assembly happens in `decision.py` before the prompt is built.

---

## Experiment Goals (for context)

This system is designed to answer: **does Claude's reasoning and calibration improve over time through self-reflection?**

Key metrics to track:
- Win rate per 20-decision block (improving over time?)
- Confidence calibration: does 70% confidence = ~70% win rate?
- Strategy drift: how much does `current_strategy` change between reviews?
- exit_trigger quality: do stated exit triggers match actual exit decisions?

The system is intentionally simple. Resist the urge to add complexity (more features, multiple tickers, ML preprocessing) until at least 60 decisions have been logged.