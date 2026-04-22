import logging
import requests
import config

logger = logging.getLogger(__name__)


def notify(title: str, message: str, priority: str = "default"):
    if not config.NTFY_TOPIC:
        logger.warning("NTFY_TOPIC not set – skipping notification")
        return
    try:
        requests.post(
            f"https://ntfy.sh/{config.NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={
                "Title": title.encode("ascii", errors="replace").decode("ascii"),
                "Priority": priority,
                "Tags": "chart_with_upwards_trend",
                "Content-Type": "text/plain; charset=utf-8",
            },
            timeout=10,
        )
    except Exception as e:
        logger.error("ntfy notification failed: %s", e)


def notify_decision(decision: dict, position_pnl: float):
    action = decision["action"]
    confidence = decision["confidence"]
    title = f"[{action}] confidence={confidence:.0%} | P&L={position_pnl:+.2f}%"

    lines = [
        f"Action: {action}",
        f"Confidence: {confidence:.0%}",
        f"",
        f"Reasoning: {decision['reasoning']}",
        f"",
        f"Concerns: {decision.get('concerns', '')}",
    ]
    if decision.get("exit_trigger"):
        lines.append(f"Exit trigger: {decision['exit_trigger']}")
    lines.append(f"Current P&L: {position_pnl:+.2f}%")

    notify(title, "\n".join(lines))


def notify_daily_review(content: str, date_str: str):
    notify(f"Daily Review - {date_str}", content)


def notify_strategy_review(content: str, win_rate: float):
    notify(
        f"Strategy Review - win rate {win_rate:.0%}",
        content,
        priority="high",
    )


def notify_sl_hit(decision_id: int, pnl_pct: float):
    notify(
        f"SL HIT - decision #{decision_id} | P&L={pnl_pct:+.2f}%",
        f"Stop-loss triggered on decision #{decision_id}. Estimated P&L: {pnl_pct:+.2f}%.",
        priority="urgent",
    )


def notify_error(context: str, error: str):
    notify(f"ERROR - {context}", error, priority="high")
