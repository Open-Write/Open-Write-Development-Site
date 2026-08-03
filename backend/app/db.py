"""Thin PostgreSQL access layer using a psycopg2 connection pool.

The hosted database caps concurrent connections at 25, so we keep a small
pool and hand out connections via context managers. All helpers return plain
dicts (RealDictCursor) so routers can serialize them directly.
"""
from __future__ import annotations

import contextlib
import threading
from typing import Any, Iterator

import psycopg2
import psycopg2.extras
from fastapi import HTTPException
from psycopg2.pool import PoolError, ThreadedConnectionPool

from app.config import DATABASE_URL

_pool: ThreadedConnectionPool | None = None
# Guards creation of the global pool so two concurrent requests can't both
# construct a pool (the previous unguarded `if _pool is None` check raced).
_pool_lock = threading.Lock()


def init_pool() -> None:
    """Create the global connection pool. Called once at app startup.

    Thread-safe: the lock + double-checked `_pool is None` guarantees the pool
    is built exactly once even under concurrent first requests.

    minconn=0: no connections are pre-warmed at startup, so there is nothing
    to go stale from the DB server's idle-connection timeout before the first
    real request arrives.
    """
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadedConnectionPool(minconn=0, maxconn=10, dsn=DATABASE_URL)


def close_pool() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.closeall()
            _pool = None


def _get_healthy_conn() -> Any:
    """Get a connection from the pool and verify it is alive with a lightweight ping.

    psycopg2 does not detect server-side connection drops (idle-timeout kills,
    TCP resets) — conn.closed stays 0 even though the socket is dead. The first
    query on a stale connection raises DatabaseError / OperationalError, which
    previously propagated as a plain 500 "Internal Server Error".

    We guard against this by running ``SELECT 1`` before handing the connection
    to the caller. If the ping fails the broken connection is closed and removed
    from the pool, and we get a fresh one. The cost is one extra round-trip per
    request; on a local/LAN DB this is < 1 ms and is negligible for a writing app.
    """
    assert _pool is not None
    for attempt in range(2):
        try:
            conn = _pool.getconn()
        except PoolError:
            raise HTTPException(
                status_code=503,
                detail="Database temporarily unavailable. Please retry.",
            )
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return conn          # connection is healthy
        except psycopg2.Error:
            # Stale / dead connection — discard it and let the loop retry once.
            try:
                _pool.putconn(conn, close=True)
            except Exception:
                pass
            if attempt == 1:
                raise HTTPException(
                    status_code=503,
                    detail="Database connection unavailable. Please retry in a moment.",
                )
    # unreachable, but satisfies type checkers
    raise HTTPException(status_code=503, detail="Database unavailable.")


@contextlib.contextmanager
def get_conn() -> Iterator[Any]:
    """Yield a healthy pooled connection, committing on success and rolling back on error.

    Broken connections (server-side idle-timeout drops) are detected by the
    pre-yield ping in _get_healthy_conn and discarded before the caller's query
    runs. If a psycopg2 error occurs during the actual query the connection is
    also discarded rather than returned to the pool in a broken state.
    """
    if _pool is None:
        init_pool()

    conn = _get_healthy_conn()
    try:
        yield conn
        conn.commit()
    except psycopg2.Error:
        # Connection died mid-request or query failed. Discard it so the pool
        # replaces it with a fresh connection on the next request, then surface
        # a clean 503 instead of an opaque 500 "Internal Server Error".
        try:
            conn.rollback()
        except Exception:
            pass
        try:
            _pool.putconn(conn, close=True)  # type: ignore[union-attr]
        except Exception:
            pass
        conn = None
        raise HTTPException(
            status_code=503,
            detail="Database connection lost — please try again.",
        )
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        if conn is not None:
            _pool.putconn(conn)  # type: ignore[union-attr]


def query_one(sql: str, params: tuple = ()) -> dict | None:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None


def query_all(sql: str, params: tuple = ()) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


def execute(sql: str, params: tuple = ()) -> dict | None:
    """Execute a write. Returns the first RETURNING row if present, else None."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            if cur.description is not None:
                row = cur.fetchone()
                return dict(row) if row else None
            return None
