"""PostgreSQL 版本的失败熔断器状态服务。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from src.infrastructure.persistence.postgres_connection import (
    init_pg_schema as ensure_pg_schema,
    pg_connection,
)


def init_pg_schema() -> None:
    ensure_pg_schema()


def load_state(task_name: str) -> Optional[dict[str, Any]]:
    init_pg_schema()
    with pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT task_name, cookie_path, consecutive_failures, last_reason,
                       paused_until, last_notified_at, updated_at
                FROM app.failure_guard_state
                WHERE task_name = %(task_name)s
                """,
                {"task_name": task_name},
            )
            row = cur.fetchone()
    if not row:
        return None
    return {
        "task_name": row["task_name"],
        "cookie_path": row["cookie_path"],
        "consecutive_failures": int(row["consecutive_failures"] or 0),
        "last_reason": row["last_reason"],
        "paused_until": row["paused_until"],
        "last_notified_at": row["last_notified_at"],
        "updated_at": row["updated_at"],
    }


def upsert_state(
    *,
    task_name: str,
    cookie_path: Optional[str],
    consecutive_failures: int,
    last_reason: Optional[str],
    paused_until: Optional[datetime],
    last_notified_at: Optional[datetime],
) -> None:
    init_pg_schema()
    with pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app.failure_guard_state (
                    task_name, cookie_path, consecutive_failures,
                    last_reason, paused_until, last_notified_at, updated_at
                ) VALUES (
                    %(task_name)s, %(cookie_path)s, %(consecutive_failures)s,
                    %(last_reason)s, %(paused_until)s, %(last_notified_at)s, NOW()
                )
                ON CONFLICT (task_name) DO UPDATE SET
                    cookie_path = EXCLUDED.cookie_path,
                    consecutive_failures = EXCLUDED.consecutive_failures,
                    last_reason = EXCLUDED.last_reason,
                    paused_until = EXCLUDED.paused_until,
                    last_notified_at = EXCLUDED.last_notified_at,
                    updated_at = NOW()
                """,
                {
                    "task_name": task_name,
                    "cookie_path": cookie_path,
                    "consecutive_failures": max(0, int(consecutive_failures)),
                    "last_reason": (last_reason or None),
                    "paused_until": paused_until,
                    "last_notified_at": last_notified_at,
                },
            )
        conn.commit()


def delete_state(task_name: str) -> int:
    init_pg_schema()
    with pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM app.failure_guard_state WHERE task_name = %(task_name)s",
                {"task_name": task_name},
            )
            deleted = cur.rowcount or 0
        conn.commit()
    return int(deleted)
