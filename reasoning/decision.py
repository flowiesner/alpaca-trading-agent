import json
import logging
from datetime import datetime, timezone

import anthropic

import config
from data.features import compute_features
from execution.orders import execute_action, get_current_position, get_portfolio_value, reconcile_sl_hits
from notifications.ntfy import notify_decision, notify_error, notify_sl_hit
from reasoning.prompts import build_decision_prompt
from storage import db

logger = logging.getLogger(__name__)

_anthropic = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

VALID_ACTIONS = {"LONG", "SHORT", "HOLD", "CLOSE"}


def _call_claude(system: str, user: str) -> str:
    response = _anthropic.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return response.content[0].text.strip()


def _parse_and_validate(raw: str) -> dict:
    parsed = json.loads(raw)

    action = parsed.get("action", "")
    if action not in VALID_ACTIONS:
        raise ValueError(f"Invalid action: {action!r}")

    confidence = parsed.get("confidence")
    if not isinstance(confidence, (int, float)) or not (0.0 <= confidence <= 1.0):
        raise ValueError(f"Invalid confidence: {confidence!r}")

    if action == "HOLD":
        exit_trigger = parsed.get("exit_trigger", "")
        if not exit_trigger or not exit_trigger.strip():
            raise ValueError("HOLD action requires a non-empty exit_trigger")

    return parsed


def _get_market_status() -> str:
    """Return OPEN or CLOSED based on current Vienna time vs US market hours."""
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Europe/Vienna"))
    # US market: 15:30–22:00 MEZ
    market_open = now.replace(hour=15, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=22, minute=0, second=0, microsecond=0)
    if market_open <= now < market_close and now.weekday() < 5:
        return "OPEN"
    return "CLOSED"


def _summarize_for_history(decisions: list[dict]) -> list[dict]:
    result = []
    for d in decisions:
        outcome_pct = None
        if d.get("pnl_pct") is not None:
            outcome_pct = d["pnl_pct"]
        result.append({
            "action": d.get("action"),
            "reasoning_summary": (d.get("reasoning") or "")[:200],
            "confidence": d.get("confidence"),
            "outcome_pct": outcome_pct,
        })
    return result


def run_decision():
    try:
        _run_decision_inner()
    except Exception:
        logger.exception("Unhandled exception in run_decision – job aborted")
        notify_error("run_decision crashed", "See trading.log for full traceback")


def _run_decision_inner():
    logger.info("--- Running decision ---")

    # Reconcile SL hits before computing new features
    sl_hits = reconcile_sl_hits()
    for hit in sl_hits:
        notify_sl_hit(hit["decision_id"], hit["pnl_pct"])

    # Check Alpaca market clock
    try:
        from alpaca.trading.client import TradingClient
        clock = TradingClient(
            api_key=config.ALPACA_API_KEY,
            secret_key=config.ALPACA_SECRET_KEY,
            paper=config.ALPACA_PAPER,
        ).get_clock()
        if not clock.is_open and _get_market_status() == "OPEN":
            # Market unexpectedly closed (holiday etc.)
            logger.warning("Market unexpectedly closed – skipping decision")
            notify_error("Decision skipped", "Market is closed unexpectedly (holiday?)")
            return
    except Exception as e:
        logger.warning("Could not fetch market clock: %s – continuing anyway", e)
        notify_error("Market clock fetch failed", f"Continuing without clock check: {e}")

    market_status = _get_market_status()
    decision_number = db.get_decision_count() + 1
    current_strategy = db.get_current_strategy()
    position = get_current_position()
    logger.info("Decision #%d | market=%s | position=%s pnl=%.2f%%",
                decision_number, market_status, position["status"], position["unrealized_pnl_pct"])

    features = compute_features()
    features["portfolio_value"] = round(get_portfolio_value(), 2)
    logger.debug("Features computed: %s", features)
    features["current_position"] = position["status"]
    features["position_pnl"] = position["unrealized_pnl_pct"]
    features["market_status"] = market_status
    features["decision_number"] = decision_number

    # position_age_decisions: count open decisions without outcome
    open_dec = db.get_open_decision()
    features["position_age_decisions"] = (
        decision_number - open_dec["decision_number"] if open_dec else 0
    )

    last_3_raw = db.get_last_n_decisions(3)
    features["last_3_decisions"] = _summarize_for_history(last_3_raw)
    features["current_strategy"] = current_strategy

    system, user = build_decision_prompt(
        decision_number=decision_number,
        market_status=market_status,
        current_strategy=current_strategy,
        features=features,
        last_3_decisions=features["last_3_decisions"],
    )

    raw = _call_claude(system, user)
    decision = None

    try:
        decision = _parse_and_validate(raw)
    except (json.JSONDecodeError, ValueError) as e:
        reason = str(e)
        logger.warning("Invalid response (attempt 1): %s\nRaw response: %s", reason, raw)
        retry_user = user + f'\n\nYour previous response was invalid because: {reason}. Return only valid JSON.'
        raw2 = _call_claude(system, retry_user)
        try:
            decision = _parse_and_validate(raw2)
        except (json.JSONDecodeError, ValueError) as e2:
            logger.error("Invalid response (attempt 2): %s\nRaw response: %s", e2, raw2)
            notify_error(f"Decision #{decision_number} skipped", f"Claude returned invalid JSON twice: {e2}")
            return

    action = decision["action"]
    timestamp = datetime.now(timezone.utc).isoformat()

    # HOLD/CLOSE with no open position is a Claude error – re-prompt once
    if action in ("HOLD", "CLOSE") and position["status"] == "CASH":
        reason = f"Action {action!r} is invalid when current_position is CASH – no position to hold or close."
        logger.warning(reason)
        notify_error(f"Decision #{decision_number} invalid action", reason)
        retry_user = user + f'\n\nYour previous response was invalid because: {reason}. Return only valid JSON.'
        raw2 = _call_claude(system, retry_user)
        try:
            decision = _parse_and_validate(raw2)
            action = decision["action"]
        except (json.JSONDecodeError, ValueError) as e2:
            logger.error("Invalid response after HOLD/CLOSE-CASH retry: %s – skipping", e2)
            notify_error(f"Decision #{decision_number} skipped", str(e2))
            return
        if action in ("HOLD", "CLOSE") and position["status"] == "CASH":
            logger.error("Claude still returned %s with CASH position – skipping", action)
            notify_error(f"Decision #{decision_number} skipped", f"Claude insists on {action} with no position")
            return

    # Execute order – must succeed before logging
    order_id = execute_action(action, features["current_price"], position)

    # For LONG/SHORT, fail loudly if order was not placed
    if action in ("LONG", "SHORT") and order_id is None:
        logger.error("Order execution failed for action %s – decision not logged", action)
        notify_error(f"Decision #{decision_number}", f"Order execution failed for {action}")
        return

    # Log outcome for previous open position if we just closed/switched it
    if action in ("LONG", "SHORT", "CLOSE") and position["status"] != "CASH":
        if open_dec:
            db.log_outcome(
                decision_id=open_dec["id"],
                exit_timestamp=timestamp,
                exit_price=features["current_price"],
                pnl_pct=position["unrealized_pnl_pct"],
                exit_reason="CLAUDE_EXIT",
            )

    features_snapshot = {k: v for k, v in features.items()
                         if k not in ("last_3_decisions", "current_strategy")}

    db.log_decision(
        timestamp=timestamp,
        decision_number=decision_number,
        market_status=market_status,
        action=action,
        reasoning=decision["reasoning"],
        confidence=decision["confidence"],
        exit_trigger=decision.get("exit_trigger"),
        concerns=decision.get("concerns", ""),
        features_snapshot=features_snapshot,
        order_id=order_id,
    )

    notify_decision(decision, position["unrealized_pnl_pct"])

    # Trigger strategy review if this is a multiple-of-20 decision
    if decision_number % config.STRATEGY_REVIEW_EVERY == 0:
        from reasoning.strategy_review import run_strategy_review
        run_strategy_review(decision_number)

    logger.info("Decision #%d logged: %s (confidence=%.2f)", decision_number, action, decision["confidence"])
