"""
将现有 SQLite 数据迁移到 PostgreSQL（参数化 SQL，幂等写入）。

用法：
  APP_DATABASE_FILE=data/app.db APP_POSTGRES_DSN=postgresql://... \
  python scripts/migrate_sqlite_to_postgres.py
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

from src.infrastructure.persistence.postgres_connection import init_pg_schema, pg_connection
from src.infrastructure.persistence.sqlite_connection import get_database_path


def _sqlite_conn() -> sqlite3.Connection:
    path = os.getenv("APP_DATABASE_FILE", get_database_path())
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate_tasks(sqlite_conn: sqlite3.Connection) -> int:
    rows = sqlite_conn.execute("SELECT * FROM tasks ORDER BY id ASC").fetchall()
    if not rows:
        return 0

    with pg_connection() as pg_conn, pg_conn.cursor() as cur:
        for row in rows:
            payload = dict(row)
            cur.execute(
                """
                INSERT INTO app.tasks (
                    id, task_name, enabled, keyword, description, analyze_images,
                    max_pages, personal_only, min_price, max_price, cron,
                    ai_prompt_base_file, ai_prompt_criteria_file, account_state_file,
                    account_strategy, free_shipping, new_publish_option, region,
                    decision_mode, keyword_rules_json, is_running
                ) VALUES (
                    %(id)s, %(task_name)s, %(enabled)s, %(keyword)s, %(description)s, %(analyze_images)s,
                    %(max_pages)s, %(personal_only)s, %(min_price)s, %(max_price)s, %(cron)s,
                    %(ai_prompt_base_file)s, %(ai_prompt_criteria_file)s, %(account_state_file)s,
                    %(account_strategy)s, %(free_shipping)s, %(new_publish_option)s, %(region)s,
                    %(decision_mode)s, %(keyword_rules_json)s::jsonb, %(is_running)s
                )
                ON CONFLICT (id) DO UPDATE SET
                    task_name=EXCLUDED.task_name,
                    enabled=EXCLUDED.enabled,
                    keyword=EXCLUDED.keyword,
                    description=EXCLUDED.description,
                    analyze_images=EXCLUDED.analyze_images,
                    max_pages=EXCLUDED.max_pages,
                    personal_only=EXCLUDED.personal_only,
                    min_price=EXCLUDED.min_price,
                    max_price=EXCLUDED.max_price,
                    cron=EXCLUDED.cron,
                    ai_prompt_base_file=EXCLUDED.ai_prompt_base_file,
                    ai_prompt_criteria_file=EXCLUDED.ai_prompt_criteria_file,
                    account_state_file=EXCLUDED.account_state_file,
                    account_strategy=EXCLUDED.account_strategy,
                    free_shipping=EXCLUDED.free_shipping,
                    new_publish_option=EXCLUDED.new_publish_option,
                    region=EXCLUDED.region,
                    decision_mode=EXCLUDED.decision_mode,
                    keyword_rules_json=EXCLUDED.keyword_rules_json,
                    is_running=EXCLUDED.is_running,
                    updated_at=NOW()
                """,
                {
                    "id": int(payload["id"]),
                    "task_name": payload.get("task_name") or "",
                    "enabled": bool(payload.get("enabled", 0)),
                    "keyword": payload.get("keyword") or "",
                    "description": payload.get("description"),
                    "analyze_images": bool(payload.get("analyze_images", 1)),
                    "max_pages": int(payload.get("max_pages") or 1),
                    "personal_only": bool(payload.get("personal_only", 0)),
                    "min_price": payload.get("min_price"),
                    "max_price": payload.get("max_price"),
                    "cron": payload.get("cron"),
                    "ai_prompt_base_file": payload.get("ai_prompt_base_file") or "prompts/base_prompt.txt",
                    "ai_prompt_criteria_file": payload.get("ai_prompt_criteria_file") or "",
                    "account_state_file": payload.get("account_state_file"),
                    "account_strategy": payload.get("account_strategy") or "auto",
                    "free_shipping": bool(payload.get("free_shipping", 1)),
                    "new_publish_option": payload.get("new_publish_option"),
                    "region": payload.get("region"),
                    "decision_mode": payload.get("decision_mode") or "ai",
                    "keyword_rules_json": payload.get("keyword_rules_json") or "[]",
                    "is_running": bool(payload.get("is_running", 0)),
                },
            )
        pg_conn.commit()
    return len(rows)


def _upsert_result_file(cur, result_filename: str, keyword: str, task_name: str, crawl_time: str) -> None:
    cur.execute(
        """
        INSERT INTO app.result_files (result_filename, keyword, task_name, latest_crawl_time)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (result_filename) DO UPDATE SET
            keyword=EXCLUDED.keyword,
            task_name=COALESCE(EXCLUDED.task_name, app.result_files.task_name),
            latest_crawl_time=GREATEST(COALESCE(app.result_files.latest_crawl_time, ''), COALESCE(EXCLUDED.latest_crawl_time, '')),
            updated_at=NOW()
        """,
        (result_filename, keyword, task_name or None, crawl_time or None),
    )


def _migrate_results(sqlite_conn: sqlite3.Connection) -> int:
    rows = sqlite_conn.execute("SELECT * FROM result_items ORDER BY id ASC").fetchall()
    if not rows:
        return 0

    with pg_connection() as pg_conn, pg_conn.cursor() as cur:
        for row in rows:
            payload = dict(row)
            _upsert_result_file(
                cur,
                str(payload.get("result_filename") or ""),
                str(payload.get("keyword") or ""),
                str(payload.get("task_name") or ""),
                str(payload.get("crawl_time") or ""),
            )
            cur.execute(
                """
                INSERT INTO app.result_items (
                    id, result_file_id, result_filename, keyword, task_name, crawl_time,
                    publish_time, price, price_display, item_id, title, link, link_unique_key,
                    seller_nickname, is_recommended, analysis_source, keyword_hit_count,
                    status, raw_json
                )
                SELECT
                    %(id)s,
                    rf.id,
                    %(result_filename)s,
                    %(keyword)s,
                    %(task_name)s,
                    %(crawl_time)s,
                    %(publish_time)s,
                    %(price)s,
                    %(price_display)s,
                    %(item_id)s,
                    %(title)s,
                    %(link)s,
                    %(link_unique_key)s,
                    %(seller_nickname)s,
                    %(is_recommended)s,
                    %(analysis_source)s,
                    %(keyword_hit_count)s,
                    %(status)s,
                    %(raw_json)s::jsonb
                FROM app.result_files rf
                WHERE rf.result_filename = %(result_filename)s
                ON CONFLICT (result_filename, link_unique_key) DO UPDATE SET
                    task_name=EXCLUDED.task_name,
                    crawl_time=EXCLUDED.crawl_time,
                    publish_time=EXCLUDED.publish_time,
                    price=EXCLUDED.price,
                    price_display=EXCLUDED.price_display,
                    item_id=EXCLUDED.item_id,
                    title=EXCLUDED.title,
                    link=EXCLUDED.link,
                    seller_nickname=EXCLUDED.seller_nickname,
                    is_recommended=EXCLUDED.is_recommended,
                    analysis_source=EXCLUDED.analysis_source,
                    keyword_hit_count=EXCLUDED.keyword_hit_count,
                    status=EXCLUDED.status,
                    raw_json=EXCLUDED.raw_json
                """,
                {
                    "id": int(payload["id"]),
                    "result_filename": str(payload.get("result_filename") or ""),
                    "keyword": str(payload.get("keyword") or ""),
                    "task_name": str(payload.get("task_name") or ""),
                    "crawl_time": str(payload.get("crawl_time") or ""),
                    "publish_time": payload.get("publish_time"),
                    "price": payload.get("price"),
                    "price_display": payload.get("price_display"),
                    "item_id": payload.get("item_id"),
                    "title": payload.get("title"),
                    "link": payload.get("link"),
                    "link_unique_key": str(payload.get("link_unique_key") or ""),
                    "seller_nickname": payload.get("seller_nickname"),
                    "is_recommended": bool(payload.get("is_recommended", 0)),
                    "analysis_source": payload.get("analysis_source"),
                    "keyword_hit_count": int(payload.get("keyword_hit_count") or 0),
                    "status": payload.get("status") or "active",
                    "raw_json": payload.get("raw_json") or "{}",
                },
            )
        pg_conn.commit()
    return len(rows)


def _migrate_blacklist(sqlite_conn: sqlite3.Connection) -> int:
    rows = sqlite_conn.execute("SELECT * FROM result_blacklist_rules").fetchall()
    if not rows:
        return 0

    with pg_connection() as pg_conn, pg_conn.cursor() as cur:
        for row in rows:
            payload = dict(row)
            cur.execute(
                """
                INSERT INTO app.result_blacklist_rules (result_filename, blacklist_keywords_json, updated_at)
                VALUES (%s, %s::jsonb, COALESCE(NULLIF(%s, ''), NOW()::text)::timestamptz)
                ON CONFLICT (result_filename) DO UPDATE SET
                    blacklist_keywords_json=EXCLUDED.blacklist_keywords_json,
                    updated_at=EXCLUDED.updated_at
                """,
                (
                    str(payload.get("result_filename") or ""),
                    payload.get("blacklist_keywords_json") or "[]",
                    payload.get("updated_at") or "",
                ),
            )
        pg_conn.commit()
    return len(rows)


def _migrate_price_snapshots(sqlite_conn: sqlite3.Connection) -> int:
    rows = sqlite_conn.execute("SELECT * FROM price_snapshots ORDER BY id ASC").fetchall()
    if not rows:
        return 0

    with pg_connection() as pg_conn, pg_conn.cursor() as cur:
        for row in rows:
            payload = dict(row)
            cur.execute(
                """
                INSERT INTO app.price_snapshots (
                    id, keyword_slug, keyword, task_name, snapshot_time, snapshot_day,
                    run_id, item_id, title, price, price_display, tags_json, region,
                    seller, publish_time, link
                ) VALUES (
                    %(id)s, %(keyword_slug)s, %(keyword)s, %(task_name)s, %(snapshot_time)s, %(snapshot_day)s,
                    %(run_id)s, %(item_id)s, %(title)s, %(price)s, %(price_display)s, %(tags_json)s::jsonb, %(region)s,
                    %(seller)s, %(publish_time)s, %(link)s
                )
                ON CONFLICT (keyword_slug, run_id, item_id) DO UPDATE SET
                    title=EXCLUDED.title,
                    price=EXCLUDED.price,
                    price_display=EXCLUDED.price_display,
                    tags_json=EXCLUDED.tags_json,
                    region=EXCLUDED.region,
                    seller=EXCLUDED.seller,
                    publish_time=EXCLUDED.publish_time,
                    link=EXCLUDED.link
                """,
                {
                    "id": int(payload["id"]),
                    "keyword_slug": str(payload.get("keyword_slug") or ""),
                    "keyword": str(payload.get("keyword") or ""),
                    "task_name": str(payload.get("task_name") or ""),
                    "snapshot_time": str(payload.get("snapshot_time") or ""),
                    "snapshot_day": str(payload.get("snapshot_day") or ""),
                    "run_id": str(payload.get("run_id") or ""),
                    "item_id": str(payload.get("item_id") or ""),
                    "title": payload.get("title"),
                    "price": payload.get("price") if payload.get("price") is not None else 0,
                    "price_display": payload.get("price_display"),
                    "tags_json": payload.get("tags_json") or "[]",
                    "region": payload.get("region"),
                    "seller": payload.get("seller"),
                    "publish_time": payload.get("publish_time"),
                    "link": payload.get("link"),
                },
            )
        pg_conn.commit()
    return len(rows)


def _migrate_metadata_to_prompt_templates(sqlite_conn: sqlite3.Connection) -> int:
    rows = sqlite_conn.execute("SELECT key, value FROM app_metadata").fetchall()
    if not rows:
        return 0

    count = 0
    with pg_connection() as pg_conn, pg_conn.cursor() as cur:
        for row in rows:
            key = str(row["key"])
            value = str(row["value"])
            if key.startswith("bootstrap:") or key.startswith("migration:"):
                cur.execute(
                    """
                    INSERT INTO app.prompt_templates (key, content, metadata)
                    VALUES (%s, %s, %s::jsonb)
                    ON CONFLICT (key) DO UPDATE SET
                        content=EXCLUDED.content,
                        metadata=EXCLUDED.metadata,
                        updated_at=NOW()
                    """,
                    (f"legacy:{key}", value, json.dumps({"source": "sqlite.app_metadata"}, ensure_ascii=False)),
                )
                count += 1
        pg_conn.commit()
    return count


def main() -> None:
    init_pg_schema()
    sqlite_conn = _sqlite_conn()
    try:
        tasks = _migrate_tasks(sqlite_conn)
        results = _migrate_results(sqlite_conn)
        blacklist = _migrate_blacklist(sqlite_conn)
        snapshots = _migrate_price_snapshots(sqlite_conn)
        templates = _migrate_metadata_to_prompt_templates(sqlite_conn)
    finally:
        sqlite_conn.close()

    print(
        json.dumps(
            {
                "tasks": tasks,
                "result_items": results,
                "result_blacklist_rules": blacklist,
                "price_snapshots": snapshots,
                "prompt_templates": templates,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
