"""
PostgreSQL 连接与 schema 初始化。
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row


def get_postgres_dsn() -> str:
    dsn = os.getenv("APP_POSTGRES_DSN", "").strip()
    if dsn:
        return dsn

    host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "ai_goofish")
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "postgres")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


@contextmanager
def pg_connection(autocommit: bool = False) -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(get_postgres_dsn(), row_factory=dict_row, autocommit=autocommit)
    try:
        yield conn
    finally:
        conn.close()


SCHEMA_STATEMENTS = (
    "CREATE SCHEMA IF NOT EXISTS app",
    """
    CREATE TABLE IF NOT EXISTS app.app_metadata (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app.tasks (
        id BIGSERIAL PRIMARY KEY,
        task_name TEXT NOT NULL,
        enabled BOOLEAN NOT NULL,
        keyword TEXT NOT NULL,
        description TEXT,
        analyze_images BOOLEAN NOT NULL,
        max_pages INTEGER NOT NULL,
        personal_only BOOLEAN NOT NULL,
        min_price TEXT,
        max_price TEXT,
        cron TEXT,
        ai_prompt_base_file TEXT NOT NULL,
        ai_prompt_criteria_file TEXT NOT NULL,
        account_state_file TEXT,
        account_strategy TEXT NOT NULL,
        free_shipping BOOLEAN NOT NULL,
        new_publish_option TEXT,
        region TEXT,
        decision_mode TEXT NOT NULL,
        keyword_rules_json JSONB NOT NULL DEFAULT '[]'::jsonb,
        is_running BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tasks_name ON app.tasks(task_name)",
    """
    CREATE TABLE IF NOT EXISTS app.result_files (
        id BIGSERIAL PRIMARY KEY,
        result_filename TEXT NOT NULL UNIQUE,
        keyword TEXT NOT NULL,
        task_name TEXT,
        latest_crawl_time TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app.result_items (
        id BIGSERIAL PRIMARY KEY,
        result_file_id BIGINT NOT NULL REFERENCES app.result_files(id) ON DELETE CASCADE,
        result_filename TEXT NOT NULL,
        keyword TEXT NOT NULL,
        task_name TEXT NOT NULL,
        crawl_time TEXT NOT NULL,
        publish_time TEXT,
        price DOUBLE PRECISION,
        price_display TEXT,
        item_id TEXT,
        title TEXT,
        link TEXT,
        link_unique_key TEXT NOT NULL,
        seller_nickname TEXT,
        is_recommended BOOLEAN NOT NULL,
        analysis_source TEXT,
        keyword_hit_count INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'active',
        raw_json JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE(result_filename, link_unique_key)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_results_file_crawl ON app.result_items(result_filename, crawl_time DESC)",
    "CREATE INDEX IF NOT EXISTS idx_results_file_publish ON app.result_items(result_filename, publish_time DESC)",
    "CREATE INDEX IF NOT EXISTS idx_results_file_price ON app.result_items(result_filename, price DESC)",
    "CREATE INDEX IF NOT EXISTS idx_results_file_reco ON app.result_items(result_filename, is_recommended, analysis_source, crawl_time DESC)",
    "CREATE INDEX IF NOT EXISTS idx_results_file_status ON app.result_items(result_filename, status, crawl_time DESC)",
    """
    CREATE TABLE IF NOT EXISTS app.result_blacklist_rules (
        result_filename TEXT PRIMARY KEY,
        blacklist_keywords_json JSONB NOT NULL DEFAULT '[]'::jsonb,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app.price_snapshots (
        id BIGSERIAL PRIMARY KEY,
        keyword_slug TEXT NOT NULL,
        keyword TEXT NOT NULL,
        task_name TEXT NOT NULL,
        snapshot_time TEXT NOT NULL,
        snapshot_day TEXT NOT NULL,
        run_id TEXT NOT NULL,
        item_id TEXT NOT NULL,
        title TEXT,
        price DOUBLE PRECISION NOT NULL,
        price_display TEXT,
        tags_json JSONB NOT NULL DEFAULT '[]'::jsonb,
        region TEXT,
        seller TEXT,
        publish_time TEXT,
        link TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE(keyword_slug, run_id, item_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_snapshots_keyword_time ON app.price_snapshots(keyword_slug, snapshot_time DESC)",
    "CREATE INDEX IF NOT EXISTS idx_snapshots_keyword_item_time ON app.price_snapshots(keyword_slug, item_id, snapshot_time DESC)",
    """
    CREATE TABLE IF NOT EXISTS app.failure_guard_state (
        task_name TEXT PRIMARY KEY,
        cookie_path TEXT,
        consecutive_failures INTEGER NOT NULL DEFAULT 0,
        last_reason TEXT,
        paused_until TIMESTAMPTZ,
        last_notified_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "ALTER TABLE app.failure_guard_state ADD COLUMN IF NOT EXISTS cookie_mtime DOUBLE PRECISION",
    "ALTER TABLE app.failure_guard_state ADD COLUMN IF NOT EXISTS last_failure_at TIMESTAMPTZ",
    "ALTER TABLE app.failure_guard_state ADD COLUMN IF NOT EXISTS last_success_at TIMESTAMPTZ",
    """
    CREATE TABLE IF NOT EXISTS app.notification_configs (
        key TEXT PRIMARY KEY,
        payload JSONB NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app.prompt_templates (
        key TEXT PRIMARY KEY,
        content TEXT NOT NULL,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app.account_states (
        id BIGSERIAL PRIMARY KEY,
        state_file TEXT NOT NULL UNIQUE,
        account_id TEXT,
        nickname TEXT,
        state_json JSONB NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
)


def init_pg_schema() -> None:
    with pg_connection() as conn:
        with conn.cursor() as cur:
            for stmt in SCHEMA_STATEMENTS:
                cur.execute(stmt)
        conn.commit()
