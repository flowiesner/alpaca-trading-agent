import logging
from datetime import datetime, timezone

import anthropic

import config
from notifications.ntfy import notify_strategy_review, notify_error
from reasoning.prompts import build_strategy_review_prompt
from storage import db

logger = logging.getLogger(__name__)

_anthropic = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def run_strategy_review(decision_count: int):
    logger.info("--- Running strategy review after decision #%d ---", decision_count)

    decisions = db.get_decisions_since_last_strategy_review()
    daily_reviews = db.get_reviews_since_last_strategy()
    current_strategy = db.get_current_strategy()

    closed = [d for d in decisions if d.get("pnl_pct") is not None]
    if closed:
        wins = sum(1 for d in closed if d["pnl_pct"] > 0)
        win_rate = wins / len(closed)
        avg_pnl = sum(d["pnl_pct"] for d in closed) / len(closed)
    else:
        win_rate = 0.0
        avg_pnl = 0.0

    system, user = build_strategy_review_prompt(
        decision_count=decision_count,
        decisions=decisions,
        daily_reviews=daily_reviews,
        current_strategy=current_strategy,
        win_rate=win_rate,
        avg_pnl=avg_pnl,
    )

    try:
        response = _anthropic.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=3000,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        strategy_text = response.content[0].text.strip()
    except Exception as e:
        logger.error("Strategy review API call failed: %s", e)
        notify_error("Strategy review failed", str(e))
        return

    timestamp = datetime.now(timezone.utc).isoformat()
    db.save_strategy(timestamp, strategy_text, decision_count)
    db.log_review(
        timestamp=timestamp,
        review_type="strategy",
        content=strategy_text,
        decisions_covered=len(decisions),
        win_rate=win_rate,
    )

    notify_strategy_review(strategy_text, win_rate)
    logger.info("Strategy review logged after decision #%d (win_rate=%.0f%%)", decision_count, win_rate * 100)
