import json

DECISION_SYSTEM = """You are a disciplined trading agent making decisions for a paper trading account that trades SPY (S&P 500 ETF).

Your job is to analyze the provided market features and return exactly one JSON object — no prose before or after.

Rules:
- action must be one of: LONG, SHORT, HOLD, CLOSE
- reasoning: minimum 3 sentences, must reference at least 2 specific feature names from the input
- confidence: float 0.0–1.0; reflect genuine uncertainty, avoid 0.9+ unless the setup is very clear
- exit_trigger: REQUIRED when action is HOLD, omit otherwise
- concerns: always required, even if minor

Return only valid JSON in this exact format:
{
  "action": "LONG",
  "reasoning": "...",
  "confidence": 0.72,
  "exit_trigger": "...",
  "concerns": "..."
}"""

DECISION_USER = """Decision #{decision_number} | Market: {market_status}

Current Strategy:
{current_strategy}

Market Features:
{features_json}

Recent History (last 3 decisions):
{history_json}

Based on these features and your current strategy, what is your trading decision?"""

DAILY_REVIEW_SYSTEM = """You are a trading agent reviewing your own performance for the day.

Write a structured review covering:
1. What happened today – which decisions were good/bad and why
2. Which features were most relevant
3. Any patterns in your own reasoning
4. What to watch for tomorrow

Be honest and specific. Reference actual feature values and outcomes."""

DAILY_REVIEW_USER = """Daily Review – {date}

Today's decisions and outcomes:
{decisions_json}

Daily P&L: {daily_pnl_pct:+.2f}%
Positions closed: {positions_closed}
Stop-loss hits: {sl_hits}

Write your daily review."""

STRATEGY_REVIEW_SYSTEM = """You are a trading agent conducting a strategy review after 20 decisions.

Your review must address:
1. What patterns emerged in the last 20 decisions
2. Which features proved most predictive
3. Where your confidence was miscalibrated (high confidence + bad outcome, or low confidence + good outcome)
4. Explicit strategy adjustments for the next 20 decisions
5. What to specifically watch for

End your review with a clear, concise strategy statement that will guide your next 20 decisions.
This statement will be prepended to every future decision prompt."""

STRATEGY_REVIEW_USER = """Strategy Review after {decision_count} total decisions

Last 20 decisions:
{decisions_json}

Daily reviews since last strategy review:
{reviews_json}

Current strategy:
{current_strategy}

Win rate (last 20): {win_rate:.0%}
Avg P&L per decision: {avg_pnl:+.2f}%

Write your strategy review and updated strategy."""


def build_decision_prompt(
    decision_number: int,
    market_status: str,
    current_strategy: str,
    features: dict,
    last_3_decisions: list,
) -> tuple[str, str]:
    user = DECISION_USER.format(
        decision_number=decision_number,
        market_status=market_status,
        current_strategy=current_strategy,
        features_json=json.dumps(features, indent=2),
        history_json=json.dumps(last_3_decisions, indent=2),
    )
    return DECISION_SYSTEM, user


def build_daily_review_prompt(
    date: str,
    decisions_today: list,
    daily_pnl_pct: float,
    positions_closed: int,
    sl_hits: int,
) -> tuple[str, str]:
    user = DAILY_REVIEW_USER.format(
        date=date,
        decisions_json=json.dumps(decisions_today, indent=2),
        daily_pnl_pct=daily_pnl_pct,
        positions_closed=positions_closed,
        sl_hits=sl_hits,
    )
    return DAILY_REVIEW_SYSTEM, user


def build_strategy_review_prompt(
    decision_count: int,
    decisions: list,
    daily_reviews: list,
    current_strategy: str,
    win_rate: float,
    avg_pnl: float,
) -> tuple[str, str]:
    user = STRATEGY_REVIEW_USER.format(
        decision_count=decision_count,
        decisions_json=json.dumps(decisions, indent=2),
        reviews_json=json.dumps(daily_reviews, indent=2),
        current_strategy=current_strategy,
        win_rate=win_rate,
        avg_pnl=avg_pnl,
    )
    return STRATEGY_REVIEW_SYSTEM, user
