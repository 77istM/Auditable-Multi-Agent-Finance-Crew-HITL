"""
Database helpers for audit logging and refund-request tracking.

Supports both SQLite (local / dev) and PostgreSQL via Neon (production).
Set the ``DATABASE_URL`` environment variable for PostgreSQL; leave it unset
to use the local SQLite fallback so the app is always runnable without cloud
credentials.

Connection strategy
───────────────────
Every public function opens and closes its own connection through the
``_get_conn()`` context manager.  For a Streamlit app with sequential
requests this is sufficient; a connection pool can be added later if needed.
"""

import os
import sqlite3
from contextlib import contextmanager
from typing import Dict, Generator, List, Optional

DB_PATH = "audit.db"


# ──────────────────────────────────────────────────────────────────────────────
# Connection factory
# ──────────────────────────────────────────────────────────────────────────────

@contextmanager
def _get_conn() -> Generator:
    """Yield a database connection, committing on success or rolling back on error."""
    url = os.getenv("DATABASE_URL", "")
    if url:
        import psycopg2  # noqa: PLC0415 — lazy import keeps SQLite-only installs working
        conn = psycopg2.connect(url)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        conn = sqlite3.connect(DB_PATH)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _ph() -> str:
    """Return the correct SQL parameter placeholder (``%s`` for PG, ``?`` for SQLite)."""
    return "%s" if os.getenv("DATABASE_URL") else "?"


def _rows_to_dicts(cur) -> List[Dict]:
    """Convert cursor results to a list of plain dicts (works for both adapters)."""
    if cur.description is None:
        return []
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ──────────────────────────────────────────────────────────────────────────────
# Schema
# ──────────────────────────────────────────────────────────────────────────────

def init_db() -> None:
    """Create tables if they don't already exist."""
    is_pg = bool(os.getenv("DATABASE_URL"))
    with _get_conn() as conn:
        cur = conn.cursor()
        if is_pg:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS refund_requests (
                    id               SERIAL PRIMARY KEY,
                    user_id          TEXT    NOT NULL,
                    amount           REAL    NOT NULL,
                    transaction_id   TEXT,
                    status           TEXT    NOT NULL DEFAULT 'investigating',
                    risk_score       INTEGER,
                    stripe_refund_id TEXT,
                    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id          SERIAL PRIMARY KEY,
                    request_id  INTEGER NOT NULL,
                    agent       TEXT    NOT NULL,
                    action      TEXT    NOT NULL,
                    details     TEXT    DEFAULT '',
                    timestamp   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (request_id) REFERENCES refund_requests(id)
                )
            """)
        else:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS refund_requests (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id          TEXT    NOT NULL,
                    amount           REAL    NOT NULL,
                    transaction_id   TEXT,
                    status           TEXT    NOT NULL DEFAULT 'investigating',
                    risk_score       INTEGER,
                    stripe_refund_id TEXT,
                    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id  INTEGER NOT NULL,
                    agent       TEXT    NOT NULL,
                    action      TEXT    NOT NULL,
                    details     TEXT    DEFAULT '',
                    timestamp   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (request_id) REFERENCES refund_requests(id)
                )
            """)


# ──────────────────────────────────────────────────────────────────────────────
# Core CRUD helpers
# ──────────────────────────────────────────────────────────────────────────────

def create_refund_request(user_id: str, amount: float, transaction_id: str = "") -> int:
    """Insert a new refund request and return its row ID."""
    ph = _ph()
    is_pg = bool(os.getenv("DATABASE_URL"))
    with _get_conn() as conn:
        cur = conn.cursor()
        if is_pg:
            cur.execute(
                f"INSERT INTO refund_requests (user_id, amount, transaction_id) "
                f"VALUES ({ph}, {ph}, {ph}) RETURNING id",
                (user_id, amount, transaction_id),
            )
            return cur.fetchone()[0]
        else:
            cur.execute(
                f"INSERT INTO refund_requests (user_id, amount, transaction_id) "
                f"VALUES ({ph}, {ph}, {ph})",
                (user_id, amount, transaction_id),
            )
            return cur.lastrowid


def update_refund_status(
    request_id: int,
    status: str,
    risk_score: Optional[int] = None,
    stripe_refund_id: Optional[str] = None,
) -> None:
    """Update the status (and optionally risk_score / stripe_refund_id) of a request."""
    ph = _ph()
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""UPDATE refund_requests
               SET status = {ph}, risk_score = COALESCE({ph}, risk_score),
                   stripe_refund_id = COALESCE({ph}, stripe_refund_id),
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = {ph}""",
            (status, risk_score, stripe_refund_id, request_id),
        )


def log_audit(request_id: int, agent: str, action: str, details: str = "") -> None:
    """Append an audit-log entry for a specific refund request."""
    ph = _ph()
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"INSERT INTO audit_logs (request_id, agent, action, details) "
            f"VALUES ({ph}, {ph}, {ph}, {ph})",
            (request_id, agent, action, details),
        )


# ──────────────────────────────────────────────────────────────────────────────
# Risk-scoring queries  (called by the Risk Auditor agent)
# ──────────────────────────────────────────────────────────────────────────────

def get_monthly_refund_count(user_id: str) -> int:
    """Return the total refund requests for *user_id* in the current calendar month."""
    ph = _ph()
    is_pg = bool(os.getenv("DATABASE_URL"))
    if is_pg:
        sql = (
            f"SELECT COUNT(*) FROM refund_requests "
            f"WHERE user_id = {ph} "
            f"AND DATE_TRUNC('month', created_at) = DATE_TRUNC('month', NOW())"
        )
    else:
        sql = (
            f"SELECT COUNT(*) FROM refund_requests "
            f"WHERE user_id = {ph} "
            f"AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')"
        )
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, (user_id,))
        return cur.fetchone()[0]


def get_duplicate_count(user_id: str, transaction_id: str) -> int:
    """Return prior submissions with the same (user_id, transaction_id) this month."""
    ph = _ph()
    is_pg = bool(os.getenv("DATABASE_URL"))
    if is_pg:
        sql = (
            f"SELECT COUNT(*) FROM refund_requests "
            f"WHERE user_id = {ph} AND transaction_id = {ph} "
            f"AND DATE_TRUNC('month', created_at) = DATE_TRUNC('month', NOW())"
        )
    else:
        sql = (
            f"SELECT COUNT(*) FROM refund_requests "
            f"WHERE user_id = {ph} AND transaction_id = {ph} "
            f"AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')"
        )
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, (user_id, transaction_id))
        return cur.fetchone()[0]


def get_user_avg_refund_amount(user_id: str) -> float:
    """Return the historical average refund amount for *user_id* (0.0 if no history)."""
    ph = _ph()
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT AVG(amount) FROM refund_requests WHERE user_id = {ph}",
            (user_id,),
        )
        result = cur.fetchone()[0]
        return float(result) if result is not None else 0.0


def get_recent_count_24h(user_id: str) -> int:
    """Return the number of refund requests by *user_id* in the last 24 hours."""
    ph = _ph()
    is_pg = bool(os.getenv("DATABASE_URL"))
    if is_pg:
        sql = (
            f"SELECT COUNT(*) FROM refund_requests "
            f"WHERE user_id = {ph} AND created_at >= NOW() - INTERVAL '1 day'"
        )
    else:
        sql = (
            f"SELECT COUNT(*) FROM refund_requests "
            f"WHERE user_id = {ph} AND created_at >= datetime('now', '-1 day')"
        )
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, (user_id,))
        return cur.fetchone()[0]


def get_tx_user_count(transaction_id: str) -> int:
    """Return the number of distinct users that have submitted *transaction_id*."""
    ph = _ph()
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT COUNT(DISTINCT user_id) FROM refund_requests "
            f"WHERE transaction_id = {ph}",
            (transaction_id,),
        )
        return cur.fetchone()[0]


def get_total_user_refund_count(user_id: str) -> int:
    """Return the all-time number of refund requests for *user_id*."""
    ph = _ph()
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT COUNT(*) FROM refund_requests WHERE user_id = {ph}",
            (user_id,),
        )
        return cur.fetchone()[0]


# ──────────────────────────────────────────────────────────────────────────────
# Read helpers  (called by the Streamlit UI)
# ──────────────────────────────────────────────────────────────────────────────

def get_recent_requests(
    limit: int = 20,
    user_id: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Dict]:
    """Return the most-recent refund requests with optional user_id / status filters."""
    ph = _ph()
    is_pg = bool(os.getenv("DATABASE_URL"))
    like_op = "ILIKE" if is_pg else "LIKE"
    where_clauses: List[str] = []
    params: List = []

    if user_id:
        where_clauses.append(f"r.user_id {like_op} {ph}")
        params.append(f"%{user_id}%")
    if status and status != "All":
        where_clauses.append(f"r.status = {ph}")
        params.append(status)

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    params.append(limit)

    sql = (
        f"SELECT r.id, r.user_id, r.amount, r.transaction_id, r.status, "
        f"r.risk_score, r.stripe_refund_id, r.created_at, r.updated_at "
        f"FROM refund_requests r {where_sql} "
        f"ORDER BY r.created_at DESC LIMIT {ph}"
    )
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return _rows_to_dicts(cur)


def get_audit_trail(request_id: int) -> List[Dict]:
    """Return all audit-log entries for a given request, ordered by time."""
    ph = _ph()
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT agent, action, details, timestamp "
            f"FROM audit_logs WHERE request_id = {ph} "
            f"ORDER BY timestamp ASC",
            (request_id,),
        )
        return _rows_to_dicts(cur)


# ──────────────────────────────────────────────────────────────────────────────
# Analytics aggregates  (called by the Analytics dashboard tab)
# ──────────────────────────────────────────────────────────────────────────────

def get_stats() -> Dict:
    """Return aggregate counts and totals across all refund requests."""
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT status, COUNT(*) AS cnt, COALESCE(SUM(amount), 0) AS total_amt "
            "FROM refund_requests GROUP BY status"
        )
        rows = _rows_to_dicts(cur)

    stats: Dict = {
        "total": 0,
        "executed": 0,
        "rejected": 0,
        "awaiting_approval": 0,
        "investigating": 0,
        "failed": 0,
        "total_refunded": 0.0,
    }
    for row in rows:
        s = row["status"]
        stats["total"] += row["cnt"]
        if s in stats:
            stats[s] = row["cnt"]
        if s == "executed":
            stats["total_refunded"] = float(row["total_amt"] or 0)
    return stats


def get_daily_counts(days: int = 30) -> List[Dict]:
    """Return per-day request counts for the last *days* days."""
    days = max(1, int(days))
    is_pg = bool(os.getenv("DATABASE_URL"))
    if is_pg:
        # days is always a validated positive int — safe to use as param
        sql = (
            "SELECT DATE(created_at) AS date, COUNT(*) AS count "
            "FROM refund_requests "
            "WHERE created_at >= NOW() - (%s * INTERVAL '1 day') "
            "GROUP BY DATE(created_at) ORDER BY date ASC"
        )
        params: tuple = (days,)
    else:
        # SQLite datetime modifiers cannot be parameterised; days is a validated int
        sql = (
            f"SELECT date(created_at) AS date, COUNT(*) AS count "
            f"FROM refund_requests "
            f"WHERE created_at >= datetime('now', '-{days} days') "
            f"GROUP BY date(created_at) ORDER BY date ASC"
        )
        params = ()
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return _rows_to_dicts(cur)


def get_risk_distribution() -> List[Dict]:
    """Return risk-score counts in 10-point buckets (0–9, 10–19, …, 90–99)."""
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT risk_score FROM refund_requests WHERE risk_score IS NOT NULL"
        )
        scores = [row[0] for row in cur.fetchall()]

    buckets = [f"{i * 10}–{i * 10 + 9}" for i in range(10)]
    counts = [0] * 10
    for score in scores:
        idx = min(int(score) // 10, 9)
        counts[idx] += 1
    return [{"bucket": buckets[i], "count": counts[i]} for i in range(10)]


def get_top_users(limit: int = 10) -> List[Dict]:
    """Return the top *limit* users ranked by total refund request count."""
    ph = _ph()
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT user_id, "
            f"COUNT(*) AS total_requests, "
            f"COALESCE(SUM(amount), 0) AS total_amount, "
            f"COALESCE(AVG(risk_score), 0) AS avg_risk_score, "
            f"COUNT(CASE WHEN status = 'executed' THEN 1 END) AS executed_count "
            f"FROM refund_requests "
            f"GROUP BY user_id "
            f"ORDER BY total_requests DESC "
            f"LIMIT {ph}",
            (limit,),
        )
        return _rows_to_dicts(cur)

