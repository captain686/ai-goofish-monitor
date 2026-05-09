"""PostgreSQL 版本的账号状态持久化服务。"""
from __future__ import annotations

from typing import Any, Optional
import json

from src.infrastructure.persistence.postgres_connection import (
    init_pg_schema as ensure_pg_schema,
    pg_connection,
)


def init_pg_schema() -> None:
    ensure_pg_schema()


def load_account_state(state_file: str) -> Optional[dict[str, Any]]:
    init_pg_schema()
    with pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT state_file, account_id, nickname, state_json, updated_at
                FROM app.account_states
                WHERE state_file = %(state_file)s
                """,
                {"state_file": state_file},
            )
            row = cur.fetchone()
    if not row:
        return None
    return {
        "state_file": row["state_file"],
        "account_id": row["account_id"],
        "nickname": row["nickname"],
        "state_json": dict(row["state_json"] or {}),
        "updated_at": row["updated_at"],
    }


def save_account_state(
    *,
    state_file: str,
    account_id: Optional[str],
    nickname: Optional[str],
    state_json: dict[str, Any],
) -> None:
    init_pg_schema()
    with pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app.account_states (
                    state_file, account_id, nickname, state_json, updated_at
                ) VALUES (
                    %(state_file)s, %(account_id)s, %(nickname)s, %(state_json)s::jsonb, NOW()
                )
                ON CONFLICT (state_file) DO UPDATE SET
                    account_id = EXCLUDED.account_id,
                    nickname = EXCLUDED.nickname,
                    state_json = EXCLUDED.state_json,
                    updated_at = NOW()
                """,
                {
                    "state_file": state_file,
                    "account_id": account_id,
                    "nickname": nickname,
                    "state_json": json.dumps(state_json, ensure_ascii=False),
                },
            )
        conn.commit()


def delete_account_state(state_file: str) -> int:
    init_pg_schema()
    with pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM app.account_states WHERE state_file = %(state_file)s",
                {"state_file": state_file},
            )
            deleted = cur.rowcount or 0
        conn.commit()
    return int(deleted)


def list_account_states() -> list[dict[str, Any]]:
    init_pg_schema()
    with pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT state_file, account_id, nickname, updated_at
                FROM app.account_states
                ORDER BY state_file ASC
                """
            )
            rows = cur.fetchall()
    return [
        {
            "state_file": row["state_file"],
            "account_id": row["account_id"],
            "nickname": row["nickname"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]
