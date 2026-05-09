"""
PostgreSQL 版本的结果存储服务。
"""
from __future__ import annotations

import json
from typing import Any, Optional

from src.infrastructure.persistence.postgres_connection import init_pg_schema as ensure_pg_schema, pg_connection


def init_pg_schema() -> None:
    ensure_pg_schema()


def save_result_item(
    *,
    result_filename: str,
    keyword: str,
    task_name: str,
    item: dict,
    run_id: str,
) -> None:
    init_pg_schema()

    item_id = str(item.get("商品ID") or "").strip()
    link = str(item.get("商品链接") or "").strip()
    unique_id = item_id or link
    if not unique_id:
        return

    crawl_time = str(item.get("发布时间") or "").strip()
    publish_time = str(item.get("发布时间") or "").strip()
    price_display = str(item.get("当前售价") or "").strip()
    price_str = str(item.get("当前售价") or "").strip().replace("¥", "").replace(",", "")
    try:
        price = float(price_str) if price_str and price_str not in {"价格异常", "暂无", "-", "N/A"} else None
    except (ValueError, TypeError):
        price = None

    with pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app.result_items (
                    result_file_id, result_filename, keyword, task_name, crawl_time,
                    publish_time, price, price_display, item_id, title, link,
                    link_unique_key, seller_nickname, is_recommended,
                    keyword_hit_count, status, raw_json
                ) VALUES (
                    (SELECT id FROM app.result_files WHERE result_filename = %(result_filename)s LIMIT 1),
                    %(result_filename)s, %(keyword)s, %(task_name)s, %(crawl_time)s,
                    %(publish_time)s, %(price)s, %(price_display)s, %(item_id)s, %(title)s,
                    %(link)s, %(link_unique_key)s, %(seller_nickname)s, %(is_recommended)s,
                    %(keyword_hit_count)s, %(status)s, %(raw_json)s
                )
                ON CONFLICT (result_filename, link_unique_key) DO UPDATE SET
                    crawl_time = EXCLUDED.crawl_time,
                    publish_time = EXCLUDED.publish_time,
                    price = EXCLUDED.price,
                    price_display = EXCLUDED.price_display,
                    seller_nickname = EXCLUDED.seller_nickname,
                    is_recommended = EXCLUDED.is_recommended,
                    status = EXCLUDED.status,
                    raw_json = EXCLUDED.raw_json
                """,
                {
                    "result_filename": result_filename,
                    "keyword": keyword,
                    "task_name": task_name,
                    "crawl_time": crawl_time,
                    "publish_time": publish_time,
                    "price": price,
                    "price_display": price_display,
                    "item_id": item_id,
                    "title": str(item.get("商品标题") or ""),
                    "link": link,
                    "link_unique_key": unique_id,
                    "seller_nickname": str(item.get("卖家昵称") or ""),
                    "is_recommended": bool(item.get("是否推荐")),
                    "keyword_hit_count": int(item.get("关键词命中次数") or 0),
                    "status": str(item.get("status") or "active"),
                    "raw_json": json.dumps(item, ensure_ascii=False),
                },
            )
        conn.commit()


def load_result_items(
    *,
    result_filename: str,
    page: int = 1,
    page_size: int = 20,
    sort_by: str = "crawl_time",
    sort_order: str = "desc",
) -> dict[str, Any]:
    init_pg_schema()

    valid_sort_fields = {"crawl_time", "publish_time", "price", "is_recommended", "keyword_hit_count"}
    valid_sort_orders = {"asc", "desc"}

    if sort_by not in valid_sort_fields:
        sort_by = "crawl_time"
    if sort_order not in valid_sort_orders:
        sort_order = "desc"

    offset = (page - 1) * page_size

    with pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) FROM app.result_items WHERE result_filename = %(result_filename)s
                """,
                {"result_filename": result_filename},
            )
            total = cur.fetchone()["count"]

            cur.execute(
                f"""
                SELECT id, result_file_id, result_filename, keyword, task_name, crawl_time,
                       publish_time, price, price_display, item_id, title, link,
                       link_unique_key, seller_nickname, is_recommended,
                       keyword_hit_count, status, raw_json, created_at
                FROM app.result_items
                WHERE result_filename = %(result_filename)s
                ORDER BY {sort_by} {sort_order}
                LIMIT %(page_size)s OFFSET %(offset)s
                """,
                {
                    "result_filename": result_filename,
                    "page_size": page_size,
                    "offset": offset,
                },
            )
            rows = cur.fetchall()

    items = []
    for row in rows:
        raw_json = row.get("raw_json")
        if isinstance(raw_json, str):
            raw_json = json.loads(raw_json)
        items.append(
            {
                "id": row["id"],
                "result_file_id": row["result_file_id"],
                "result_filename": row["result_filename"],
                "keyword": row["keyword"],
                "task_name": row["task_name"],
                "crawl_time": row["crawl_time"],
                "publish_time": row["publish_time"],
                "price": row["price"],
                "price_display": row["price_display"],
                "item_id": row["item_id"],
                "title": row["title"],
                "link": row["link"],
                "link_unique_key": row["link_unique_key"],
                "seller_nickname": row["seller_nickname"],
                "is_recommended": row["is_recommended"],
                "keyword_hit_count": row["keyword_hit_count"],
                "status": row["status"],
                "raw_json": raw_json,
                "created_at": row["created_at"],
            }
        )

    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size,
    }


def delete_result_items(result_filename: str) -> int:
    init_pg_schema()
    with pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM app.result_items WHERE result_filename = %(result_filename)s",
                {"result_filename": result_filename},
            )
            deleted = cur.rowcount or 0
        conn.commit()
    return int(deleted)


def update_item_status(
    *,
    result_filename: str,
    item_id: str,
    status: str,
) -> bool:
    init_pg_schema()
    with pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE app.result_items
                SET status = %(status)s
                WHERE result_filename = %(result_filename)s AND item_id = %(item_id)s
                """,
                {
                    "result_filename": result_filename,
                    "item_id": item_id,
                    "status": status,
                },
            )
            updated = cur.rowcount or 0
        conn.commit()
    return updated > 0


def get_item_by_unique_key(
    *,
    result_filename: str,
    link_unique_key: str,
) -> Optional[dict]:
    init_pg_schema()
    with pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, result_file_id, result_filename, keyword, task_name, crawl_time,
                       publish_time, price, price_display, item_id, title, link,
                       link_unique_key, seller_nickname, is_recommended,
                       keyword_hit_count, status, raw_json, created_at
                FROM app.result_items
                WHERE result_filename = %(result_filename)s AND link_unique_key = %(link_unique_key)s
                """,
                {
                    "result_filename": result_filename,
                    "link_unique_key": link_unique_key,
                },
            )
            row = cur.fetchone()

    if not row:
        return None

    raw_json = row.get("raw_json")
    if isinstance(raw_json, str):
        raw_json = json.loads(raw_json)

    return {
        "id": row["id"],
        "result_file_id": row["result_file_id"],
        "result_filename": row["result_filename"],
        "keyword": row["keyword"],
        "task_name": row["task_name"],
        "crawl_time": row["crawl_time"],
        "publish_time": row["publish_time"],
        "price": row["price"],
        "price_display": row["price_display"],
        "item_id": row["item_id"],
        "title": row["title"],
        "link": row["link"],
        "link_unique_key": row["link_unique_key"],
        "seller_nickname": row["seller_nickname"],
        "is_recommended": row["is_recommended"],
        "keyword_hit_count": row["keyword_hit_count"],
        "status": row["status"],
        "raw_json": raw_json,
        "created_at": row["created_at"],
    }
