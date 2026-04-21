import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import anthropic

import config
from notifications.ntfy import notify_daily_review, notify_error
from reasoning.prompts import build_daily_review_prompt
from storage import db

logger = logging.getLogger(__name__)

_anthropic = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

VIENNA = ZoneInfo("Europe/Vienna")


def run_daily_review():
    try:
        _run_daily_review_inner()
    except Exception:
        logger.exception("Unhandled exception in run_daily_review")
        notify_error("run_daily_review crashed", "See trading.log for full traceback")


def _run_daily_review_inner():
    logger.info("--- Running daily review ---")
    now_vienna = datetime.now(VIENNA)
    date_str = now_vienna.strftime("%Y-%m-%d")

    decisions_today = db.get_decisions_today(date_str)
    if not decisions_today:
        logger.info("No decisions today – skipping daily review")
        return

    # Compute daily stats
    closed = [d for d in decisions_today if d.get("pnl_pct") is not None]
    daily_pnl_pct = sum(d["pnl_pct"] for d in closed) if closed else 0.0
    positions_closed = len(closed)
    sl_hits = sum(1 for d in closed if d.get("exit_reason") == "SL_HIT")

    system, user = build_daily_review_prompt(
        date=date_str,
        decisions_today=decisions_today,
        daily_pnl_pct=daily_pnl_pct,
        positions_closed=positions_closed,
        sl_hits=sl_hits,
    )

    try:
        response = _anthropic.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        review_text = response.content[0].text.strip()
    except Exception as e:
        logger.error("Daily review API call failed: %s", e)
        notify_error("Daily review failed", str(e))
        return

    timestamp = datetime.now(timezone.utc).isoformat()
    db.log_review(
        timestamp=timestamp,
        review_type="daily",
        content=review_text,
        decisions_covered=len(decisions_today),
    )

    notify_daily_review(review_text, date_str)
    logger.info("Daily review logged for %s", date_str)
