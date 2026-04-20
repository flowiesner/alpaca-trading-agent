import logging
from datetime import datetime, timezone

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    TakeProfitRequest,
    StopLossRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, OrderStatus

import config
from storage import db

logger = logging.getLogger(__name__)

_client: TradingClient | None = None


def _get_client() -> TradingClient:
    global _client
    if _client is None:
        _client = TradingClient(
            api_key=config.ALPACA_API_KEY,
            secret_key=config.ALPACA_SECRET_KEY,
            paper=config.ALPACA_PAPER,
        )
    return _client


def get_account():
    return _get_client().get_account()


def get_current_position() -> dict:
    """Return dict with keys: status ('LONG'|'SHORT'|'CASH'), qty, entry_price, unrealized_pnl_pct."""
    try:
        pos = _get_client().get_open_position(config.SYMBOL)
        qty = float(pos.qty)
        entry = float(pos.avg_entry_price)
        current = float(pos.current_price)
        side = "LONG" if qty > 0 else "SHORT"
        pnl_pct = round((current - entry) / entry * 100 * (1 if side == "LONG" else -1), 2)
        return {"status": side, "qty": abs(qty), "entry_price": entry, "unrealized_pnl_pct": pnl_pct}
    except Exception:
        return {"status": "CASH", "qty": 0, "entry_price": 0.0, "unrealized_pnl_pct": 0.0}


def _close_position() -> bool:
    try:
        _get_client().close_position(config.SYMBOL)
        logger.info("Closed existing position for %s", config.SYMBOL)
        return True
    except Exception as e:
        logger.error("Failed to close position: %s", e)
        return False


def _calc_qty(current_price: float) -> int:
    account = get_account()
    portfolio_value = float(account.portfolio_value)
    return max(1, int((portfolio_value * config.POSITION_SIZE_PCT) / current_price))


def execute_long(current_price: float) -> str | None:
    """Open a long bracket order. Returns Alpaca order ID or None on failure."""
    qty = _calc_qty(current_price)
    stop_price = round(current_price * (1 - config.STOP_LOSS_PCT), 2)
    try:
        order = _get_client().submit_order(
            MarketOrderRequest(
                symbol=config.SYMBOL,
                qty=qty,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
                order_class="bracket",
                stop_loss=StopLossRequest(stop_price=stop_price),
            )
        )
        logger.info("LONG order submitted: id=%s qty=%d sl=%.2f", order.id, qty, stop_price)
        return str(order.id)
    except Exception as e:
        logger.error("Failed to submit LONG order: %s", e)
        return None


def execute_short(current_price: float) -> str | None:
    """Open a short bracket order. Returns Alpaca order ID or None on failure."""
    qty = _calc_qty(current_price)
    stop_price = round(current_price * (1 + config.STOP_LOSS_PCT), 2)
    try:
        order = _get_client().submit_order(
            MarketOrderRequest(
                symbol=config.SYMBOL,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
                order_class="bracket",
                stop_loss=StopLossRequest(stop_price=stop_price),
            )
        )
        logger.info("SHORT order submitted: id=%s qty=%d sl=%.2f", order.id, qty, stop_price)
        return str(order.id)
    except Exception as e:
        logger.error("Failed to submit SHORT order: %s", e)
        return None


def execute_action(action: str, current_price: float, existing_position: dict) -> str | None:
    """
    Dispatch the Claude action. Returns order_id or None.
    Handles position switching (close before re-opening).
    """
    status = existing_position["status"]

    if action == "HOLD":
        return None

    if action == "CLOSE":
        if status != "CASH":
            _close_position()
        return None

    if action == "LONG":
        if status == "SHORT":
            if not _close_position():
                return None
        return execute_long(current_price)

    if action == "SHORT":
        if status == "LONG":
            if not _close_position():
                return None
        return execute_short(current_price)

    logger.error("Unknown action: %s", action)
    return None


def reconcile_sl_hits() -> list[dict]:
    """
    Compare Alpaca position vs open decisions in DB.
    If DB shows an open position but Alpaca shows CASH, the SL was hit.
    Returns list of reconciled outcomes (for notification).
    """
    open_decision = db.get_open_decision()
    if open_decision is None:
        return []

    alpaca_pos = get_current_position()
    if alpaca_pos["status"] != "CASH":
        return []

    # SL was hit – record outcome
    now = datetime.now(timezone.utc).isoformat()
    # We don't know exact exit price from reconciliation; use 0 as placeholder
    # A more precise implementation could fetch filled order price via parent order ID
    pnl_pct = _estimate_sl_pnl(open_decision)
    db.log_outcome(
        decision_id=open_decision["id"],
        exit_timestamp=now,
        exit_price=0.0,
        pnl_pct=pnl_pct,
        exit_reason="SL_HIT",
    )
    logger.warning("SL hit detected for decision id=%d", open_decision["id"])
    return [{"decision_id": open_decision["id"], "pnl_pct": pnl_pct}]


def _estimate_sl_pnl(decision: dict) -> float:
    """Estimate P&L from SL hit based on entry price stored in features."""
    features = decision.get("features_snapshot", {})
    entry_price = features.get("current_price", 0.0)
    action = decision.get("action", "LONG")
    if action == "LONG":
        return round(-config.STOP_LOSS_PCT * 100, 2)
    return round(-config.STOP_LOSS_PCT * 100, 2)
