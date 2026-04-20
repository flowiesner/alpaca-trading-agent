import sqlite3
import json
from contextlib import contextmanager

DB_PATH = "journal.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp             TEXT NOT NULL,
    decision_number       INTEGER NOT NULL,
    market_status         TEXT NOT NULL,
    action                TEXT NOT NULL,
    reasoning             TEXT NOT NULL,
    confidence            REAL NOT NULL,
    exit_trigger          TEXT,
    concerns              TEXT,
    features_snapshot     TEXT NOT NULL,
    order_id              TEXT
);

CREATE TABLE IF NOT EXISTS outcomes (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id           INTEGER REFERENCES decisions(id),
    exit_timestamp        TEXT,
    exit_price            REAL,
    pnl_pct               REAL,
    exit_reason           TEXT
);

CREATE TABLE IF NOT EXISTS reviews (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp             TEXT NOT NULL,
    type                  TEXT NOT NULL,
    content               TEXT NOT NULL,
    decisions_covered     INTEGER,
    win_rate              REAL
);

CREATE TABLE IF NOT EXISTS strategy (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    updated_at            TEXT NOT NULL,
    content               TEXT NOT NULL,
    decision_count        INTEGER NOT NULL
);
"""


def init_db():
    with _connect() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_decision_count() -> int:
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()
        return row[0]


def log_decision(
    timestamp: str,
    decision_number: int,
    market_status: str,
    action: str,
    reasoning: str,
    confidence: float,
    exit_trigger: str | None,
    concerns: str,
    features_snapshot: dict,
    order_id: str | None,
) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO decisions
              (timestamp, decision_number, market_status, action, reasoning,
               confidence, exit_trigger, concerns, features_snapshot, order_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                decision_number,
                market_status,
                action,
                reasoning,
                confidence,
                exit_trigger,
                concerns,
                json.dumps(features_snapshot),
                order_id,
            ),
        )
        return cur.lastrowid


def log_outcome(
    decision_id: int,
    exit_timestamp: str,
    exit_price: float,
    pnl_pct: float,
    exit_reason: str,
):
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO outcomes (decision_id, exit_timestamp, exit_price, pnl_pct, exit_reason)
            VALUES (?, ?, ?, ?, ?)
            """,
            (decision_id, exit_timestamp, exit_price, pnl_pct, exit_reason),
        )


def get_open_decision() -> sqlite3.Row | None:
    """Return the most recent decision that has no outcome yet and is not HOLD/CLOSE."""
    with _connect() as conn:
        return conn.execute(
            """
            SELECT d.* FROM decisions d
            LEFT JOIN outcomes o ON o.decision_id = d.id
            WHERE o.id IS NULL AND d.action IN ('LONG', 'SHORT')
            ORDER BY d.id DESC LIMIT 1
            """
        ).fetchone()


def log_review(
    timestamp: str,
    review_type: str,
    content: str,
    decisions_covered: int | None = None,
    win_rate: float | None = None,
):
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO reviews (timestamp, type, content, decisions_covered, win_rate)
            VALUES (?, ?, ?, ?, ?)
            """,
            (timestamp, review_type, content, decisions_covered, win_rate),
        )


def get_current_strategy() -> str:
    with _connect() as conn:
        row = conn.execute(
            "SELECT content FROM strategy ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["content"] if row else "No strategy defined yet."


def save_strategy(timestamp: str, content: str, decision_count: int):
    with _connect() as conn:
        conn.execute(
            "INSERT INTO strategy (updated_at, content, decision_count) VALUES (?, ?, ?)",
            (timestamp, content, decision_count),
        )


def get_decisions_since_last_strategy_review() -> list[dict]:
    """Return last 20 decisions with their outcomes."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT d.*, o.exit_timestamp, o.exit_price, o.pnl_pct, o.exit_reason
            FROM decisions d
            LEFT JOIN outcomes o ON o.decision_id = d.id
            ORDER BY d.id DESC LIMIT 20
            """
        ).fetchall()
        return [_row_to_dict(r) for r in reversed(rows)]


def get_decisions_today(date_str: str) -> list[dict]:
    """Return all decisions where timestamp starts with date_str (YYYY-MM-DD)."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT d.*, o.exit_timestamp, o.exit_price, o.pnl_pct, o.exit_reason
            FROM decisions d
            LEFT JOIN outcomes o ON o.decision_id = d.id
            WHERE d.timestamp LIKE ?
            ORDER BY d.id ASC
            """,
            (f"{date_str}%",),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_reviews_since_last_strategy() -> list[dict]:
    with _connect() as conn:
        last_strategy = conn.execute(
            "SELECT updated_at FROM strategy ORDER BY id DESC LIMIT 1"
        ).fetchone()
        cutoff = last_strategy["updated_at"] if last_strategy else "1970-01-01"
        rows = conn.execute(
            "SELECT * FROM reviews WHERE type = 'daily' AND timestamp > ? ORDER BY id ASC",
            (cutoff,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_last_n_decisions(n: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT d.*, o.pnl_pct, o.exit_reason
            FROM decisions d
            LEFT JOIN outcomes o ON o.decision_id = d.id
            ORDER BY d.id DESC LIMIT ?
            """,
            (n,),
        ).fetchall()
        return [_row_to_dict(r) for r in reversed(rows)]


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    if "features_snapshot" in d and isinstance(d["features_snapshot"], str):
        d["features_snapshot"] = json.loads(d["features_snapshot"])
    return d
