"""
PostgreSQL 版本的价格快照服务。
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime
from statistics import median
from typing import Any, Iterable, Optional

from src.infrastructure.persistence.postgres_connection import init_pg_schema, pg_connection


DEFAULT_HISTORY_WINDOW_DAYS = 30


def normalize_keyword_slug(keyword: str) -> str:
    text = "".join(
        char for char in str(keyword or "").lower().replace(" ", "_")
        if char.isalnum() or char in "-_"
    ).rstrip("_")
    return text or "unknown"


def parse_price_value(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)

    text = str(value).strip().replace("¥", "").replace(",", "")
    if not text or text in {"价格异常", "暂无", "-", "N/A"}:
        return None
    if text.endswith("万"):
        text = str(float(text[:-1]) * 10000)
    try:
        return round(float(text), 2)
    except (TypeError, ValueError):
        return None


def _safe_iso_datetime(value: Optional[str]) -> str:
    if value:
        return value
    return datetime.now().isoformat()


def _to_day(iso_text: str) -> str:
    return iso_text[:10]


def _build_snapshot_record(
    *,
    keyword: str,
    task_name: str,
    item: dict,
    run_id: str,
    snapshot_time: str,
) -> Optional[dict]:
    item_id = str(item.get("商品ID") or "").strip()
    link = str(item.get("商品链接") or "").strip()
    unique_id = item_id or link
    price_value = parse_price_value(item.get("当前售价"))
    if not unique_id or price_value is None:
        return None

    return {
        "snapshot_time": snapshot_time,
        "snapshot_day": _to_day(snapshot_time),
        "run_id": run_id,
        "task_name": task_name,
        "keyword": keyword,
        "item_id": unique_id,
        "title": item.get("商品标题") or "",
        "price": price_value,
        "price_display": item.get("当前售价") or "",
        "tags": item.get("商品标签") or [],
        "region": item.get("发货地区") or "",
        "seller": item.get("卖家昵称") or "",
        "publish_time": item.get("发布时间") or "",
        "link": link,
    }


def record_market_snapshots(
    *,
    keyword: str,
    task_name: str,
    items: Iterable[dict],
    run_id: str,
    snapshot_time: Optional[str] = None,
    seen_item_ids: Optional[set[str]] = None,
) -> list[dict]:
    init_pg_schema()
    snapshot_time = _safe_iso_datetime(snapshot_time)
    seen = seen_item_ids if seen_item_ids is not None else set()
    records: list[dict] = []

    for item in items:
        record = _build_snapshot_record(
            keyword=keyword,
            task_name=task_name,
            item=item,
            run_id=run_id,
            snapshot_time=snapshot_time,
        )
        if record is None or record["item_id"] in seen:
            continue
        seen.add(record["item_id"])
        records.append(record)

    if not records:
        return []

    keyword_slug = normalize_keyword_slug(keyword)
    with pg_connection() as conn:
        with conn.cursor() as cur:
            for record in records:
                cur.execute(
                    """
                    INSERT INTO app.price_snapshots (
                        keyword_slug, keyword, task_name, snapshot_time, snapshot_day,
                        run_id, item_id, title, price, price_display, tags_json, region,
                        seller, publish_time, link
                    ) VALUES (
                        %(keyword_slug)s, %(keyword)s, %(task_name)s, %(snapshot_time)s, %(snapshot_day)s,
                        %(run_id)s, %(item_id)s, %(title)s, %(price)s, %(price_display)s, %(tags_json)s, %(region)s,
                        %(seller)s, %(publish_time)s, %(link)s
                    )
                    ON CONFLICT (keyword_slug, run_id, item_id) DO NOTHING
                    """,
                    {
                        "keyword_slug": keyword_slug,
                        "keyword": record.get("keyword", keyword),
                        "task_name": record.get("task_name", task_name),
                        "snapshot_time": record.get("snapshot_time", snapshot_time),
                        "snapshot_day": record.get("snapshot_day", _to_day(snapshot_time)),
                        "run_id": record.get("run_id", run_id),
                        "item_id": record.get("item_id", ""),
                        "title": record.get("title", ""),
                        "price": record.get("price"),
                        "price_display": record.get("price_display", ""),
                        "tags_json": json.dumps(record.get("tags") or [], ensure_ascii=False),
                        "region": record.get("region", ""),
                        "seller": record.get("seller", ""),
                        "publish_time": record.get("publish_time", ""),
                        "link": record.get("link", ""),
                    },
                )
        conn.commit()
    return records


def load_price_snapshots(keyword: str) -> list[dict]:
    init_pg_schema()
    with pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT snapshot_time, snapshot_day, run_id, task_name, keyword,
                       item_id, title, price, price_display, tags_json, region,
                       seller, publish_time, link
                FROM app.price_snapshots
                WHERE keyword_slug = %(keyword_slug)s
                ORDER BY snapshot_time ASC, id ASC
                """,
                {"keyword_slug": normalize_keyword_slug(keyword)},
            )
            rows = cur.fetchall()

    snapshots: list[dict] = []
    for row in rows:
        tags = row.get("tags_json")
        if isinstance(tags, str):
            tags = json.loads(tags or "[]")
        snapshots.append(
            {
                "snapshot_time": row["snapshot_time"],
                "snapshot_day": row["snapshot_day"],
                "run_id": row["run_id"],
                "task_name": row["task_name"],
                "keyword": row["keyword"],
                "item_id": row["item_id"],
                "title": row["title"],
                "price": row["price"],
                "price_display": row["price_display"],
                "tags": tags or [],
                "region": row["region"],
                "seller": row["seller"],
                "publish_time": row["publish_time"],
                "link": row["link"],
            }
        )
    return snapshots


def delete_price_snapshots(keyword: str) -> int:
    init_pg_schema()
    with pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM app.price_snapshots WHERE keyword_slug = %(keyword_slug)s",
                {"keyword_slug": normalize_keyword_slug(keyword)},
            )
            deleted = cur.rowcount or 0
        conn.commit()
    return int(deleted)


def _dedupe_latest(records: Iterable[dict], group_key: str) -> list[dict]:
    latest_by_key: dict[str, dict] = {}
    for record in records:
        key = str(record.get(group_key) or "").strip()
        if not key:
            continue
        latest_by_key[key] = record
    return list(latest_by_key.values())


def _summarize_prices(records: Iterable[dict]) -> dict:
    entries = [record for record in records if parse_price_value(record.get("price")) is not None]
    prices = [float(record["price"]) for record in entries]
    if not prices:
        return {
            "sample_count": 0,
            "min_price": None,
            "max_price": None,
            "median_price": None,
            "avg_price": None,
            "std_dev": None,
        }

    avg_price = sum(prices) / len(prices)
    variance = 0.0
    if len(prices) > 1:
        variance = sum((price - avg_price) ** 2 for price in prices) / len(prices)

    return {
        "sample_count": len(prices),
        "min_price": round(min(prices), 2),
        "max_price": round(max(prices), 2),
        "median_price": round(median(prices), 2),
        "avg_price": round(avg_price, 2),
        "std_dev": round(math.sqrt(variance), 2) if variance else 0.0,
    }


def _build_day_series(records: Iterable[dict]) -> list[dict]:
    grouped = defaultdict(list)
    for record in records:
        day = str(record.get("snapshot_day") or "").strip()
        if not day:
            continue
        price = parse_price_value(record.get("price"))
        if price is None:
            continue
        grouped[day].append(float(price))

    series = []
    for day, day_prices in grouped.items():
        summary = _summarize_prices(day_prices)
        if summary["sample_count"] > 0:
            series.append({
                "day": day,
                "min_price": summary["min_price"],
                "max_price": summary["max_price"],
                "median_price": summary["median_price"],
                "avg_price": summary["avg_price"],
                "std_dev": summary["std_dev"],
                "sample_count": summary["sample_count"],
            })
    return series
