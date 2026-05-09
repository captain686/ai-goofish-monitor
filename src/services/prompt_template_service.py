"""Prompt templates storage service (PostgreSQL + file fallback)."""
from __future__ import annotations

import os
from typing import Optional

from src.infrastructure.persistence.postgres_connection import init_pg_schema, pg_connection


PROMPTS_DIR = "prompts"


def _is_pg_enabled() -> bool:
    backend = os.getenv("APP_DB_BACKEND", "sqlite").strip().lower()
    return backend in {"postgres", "pgsql", "postgresql"}


def _validate_prompt_filename(filename: str) -> str:
    name = (filename or "").strip()
    if not name or "/" in name or "\\" in name or ".." in name:
        raise ValueError("无效的文件名")
    if not name.endswith(".txt"):
        raise ValueError("仅支持 .txt prompt 文件")
    return name


def list_prompt_names() -> list[str]:
    file_names = []
    if os.path.isdir(PROMPTS_DIR):
        file_names = [f for f in os.listdir(PROMPTS_DIR) if f.endswith(".txt")]

    if _is_pg_enabled():
        init_pg_schema()
        with pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT key FROM app.prompt_templates ORDER BY key ASC")
                rows = cur.fetchall()
        pg_names = [str(row["key"]) for row in rows]
        return sorted(set(pg_names) | set(file_names))

    return sorted(file_names)


def get_prompt_content(filename: str) -> Optional[str]:
    name = _validate_prompt_filename(filename)
    path = os.path.join(PROMPTS_DIR, name)

    if _is_pg_enabled():
        init_pg_schema()
        with pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT content FROM app.prompt_templates WHERE key = %(key)s",
                    {"key": name},
                )
                row = cur.fetchone()
        if row:
            return str(row["content"])
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        return None

    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def upsert_prompt_content(filename: str, content: str) -> None:
    name = _validate_prompt_filename(filename)

    if _is_pg_enabled():
        init_pg_schema()
        with pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.prompt_templates (key, content, metadata)
                    VALUES (%(key)s, %(content)s, '{}'::jsonb)
                    ON CONFLICT (key) DO UPDATE SET
                        content = EXCLUDED.content,
                        updated_at = NOW()
                    """,
                    {"key": name, "content": content},
                )
            conn.commit()
        return

    os.makedirs(PROMPTS_DIR, exist_ok=True)
    path = os.path.join(PROMPTS_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
