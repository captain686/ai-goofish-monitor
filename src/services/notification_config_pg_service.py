"""PostgreSQL 版本的通知配置持久化服务。"""
from __future__ import annotations

from typing import Any, Optional
import json

from src.infrastructure.persistence.postgres_connection import (
    init_pg_schema as ensure_pg_schema,
    pg_connection,
)


def init_pg_schema() -> None:
    ensure_pg_schema()


def load_notification_config(key: str) -> Optional[dict[str, Any]]:
    init_pg_schema()
    with pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM app.notification_configs WHERE key = %(key)s",
                {"key": key},
            )
            row = cur.fetchone()
    if not row:
        return None
    payload = row["payload"]
    return dict(payload or {})


def save_notification_config(key: str, payload: dict[str, Any]) -> None:
    init_pg_schema()
    with pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app.notification_configs (key, payload, updated_at)
                VALUES (%(key)s, %(payload)s::jsonb, NOW())
                ON CONFLICT (key) DO UPDATE SET
                    payload = EXCLUDED.payload,
                    updated_at = NOW()
                """,
                {"key": key, "payload": json.dumps(payload, ensure_ascii=False)},
            )
        conn.commit()


def load_all_notification_configs() -> dict[str, dict[str, Any]]:
    init_pg_schema()
    with pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT key, payload FROM app.notification_configs ORDER BY key ASC")
            rows = cur.fetchall()
    return {str(row["key"]): dict(row["payload"] or {}) for row in rows}


def delete_notification_config(key: str) -> int:
    init_pg_schema()
    with pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM app.notification_configs WHERE key = %(key)s",
                {"key": key},
            )
            deleted = cur.rowcount or 0
        conn.commit()
    return int(deleted)
