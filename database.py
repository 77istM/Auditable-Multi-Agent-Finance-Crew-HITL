"""
SQLite helpers for audit logging and refund-request tracking.

All refund requests and agent actions are persisted here, providing an
auditable "source of truth" that survives restarts.
"""

import sqlite3
from datetime import datetime
from typing import List, Dict, Optional

DB_PATH = "audit.db"


def init_db() -> None:
    """Create tables if they don't already exist."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS refund_requests (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         TEXT    NOT NULL,
                amount          REAL    NOT NULL,
                transaction_id  TEXT,
                status          TEXT    NOT NULL DEFAULT 'investigating',
                risk_score      INTEGER,
                stripe_refund_id TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id  INTEGER NOT NULL,
                agent       TEXT    NOT NULL,
                action      TEXT    NOT NULL,
                details     TEXT    DEFAULT '',
                timestamp   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (request_id) REFERENCES refund_requests(id)
            );
        """)


def create_refund_request(user_id: str, amount: float, transaction_id: str = "") -> int:
    """Insert a new refund request and return its row ID."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO refund_requests (user_id, amount, transaction_id) VALUES (?, ?, ?)",
            (user_id, amount, transaction_id),
        )
        return cursor.lastrowid


def update_refund_status(
    request_id: int,
    status: str,
    risk_score: Optional[int] = None,
    stripe_refund_id: Optional[str] = None,
) -> None:
    """Update the status (and optionally risk_score / stripe_refund_id) of a request."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """UPDATE refund_requests
               SET status = ?, risk_score = COALESCE(?, risk_score),
                   stripe_refund_id = COALESCE(?, stripe_refund_id),
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (status, risk_score, stripe_refund_id, request_id),
        )


def log_audit(request_id: int, agent: str, action: str, details: str = "") -> None:
    """Append an audit-log entry for a specific refund request."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO audit_logs (request_id, agent, action, details) VALUES (?, ?, ?, ?)",
            (request_id, agent, action, details),
        )


def get_monthly_refund_count(user_id: str) -> int:
    """Return the total number of refund requests for *user_id* in the current month."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """SELECT COUNT(*) FROM refund_requests
               WHERE user_id = ?
                 AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')""",
            (user_id,),
        )
        return cursor.fetchone()[0]


def get_recent_requests(limit: int = 20) -> List[Dict]:
    """Return the most-recent refund requests with their latest audit action."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            """SELECT r.id, r.user_id, r.amount, r.transaction_id, r.status,
                      r.risk_score, r.stripe_refund_id, r.created_at, r.updated_at
               FROM refund_requests r
               ORDER BY r.created_at DESC
               LIMIT ?""",
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]


def get_audit_trail(request_id: int) -> List[Dict]:
    """Return all audit-log entries for a given request."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            """SELECT agent, action, details, timestamp
               FROM audit_logs WHERE request_id = ?
               ORDER BY timestamp ASC""",
            (request_id,),
        )
        return [dict(row) for row in cursor.fetchall()]
