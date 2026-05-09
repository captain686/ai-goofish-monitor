"""
基于 PostgreSQL 的任务仓储实现。
"""
from __future__ import annotations

import asyncio
import json
from typing import List, Optional

from src.domain.models.task import Task
from src.domain.repositories.task_repository import TaskRepository
from src.infrastructure.persistence.postgres_connection import init_pg_schema, pg_connection


def _row_to_task(row: dict) -> Task:
    payload = dict(row)
    payload["keyword_rules"] = payload.pop("keyword_rules_json") or []
    return Task(**payload)


class PostgresTaskRepository(TaskRepository):
    """基于 PostgreSQL 的任务仓储"""

    async def find_all(self) -> List[Task]:
        return await asyncio.to_thread(self._find_all_sync)

    async def find_by_id(self, task_id: int) -> Optional[Task]:
        return await asyncio.to_thread(self._find_by_id_sync, task_id)

    async def save(self, task: Task) -> Task:
        return await asyncio.to_thread(self._save_sync, task)

    async def delete(self, task_id: int) -> bool:
        return await asyncio.to_thread(self._delete_sync, task_id)

    def _find_all_sync(self) -> List[Task]:
        init_pg_schema()
        with pg_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id, task_name, enabled, keyword, description, analyze_images,
                    max_pages, personal_only, min_price, max_price, cron,
                    ai_prompt_base_file, ai_prompt_criteria_file, account_state_file,
                    account_strategy, free_shipping, new_publish_option, region,
                    decision_mode, keyword_rules_json, is_running
                FROM app.tasks
                ORDER BY id ASC
                """
            )
            rows = cur.fetchall()
        return [_row_to_task(row) for row in rows]

    def _find_by_id_sync(self, task_id: int) -> Optional[Task]:
        init_pg_schema()
        with pg_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id, task_name, enabled, keyword, description, analyze_images,
                    max_pages, personal_only, min_price, max_price, cron,
                    ai_prompt_base_file, ai_prompt_criteria_file, account_state_file,
                    account_strategy, free_shipping, new_publish_option, region,
                    decision_mode, keyword_rules_json, is_running
                FROM app.tasks
                WHERE id = %s
                """,
                (task_id,),
            )
            row = cur.fetchone()
        return _row_to_task(row) if row else None

    def _save_sync(self, task: Task) -> Task:
        init_pg_schema()
        values = task.model_dump()
        values["keyword_rules_json"] = json.dumps(task.keyword_rules or [], ensure_ascii=False)
        values.pop("keyword_rules", None)

        with pg_connection() as conn, conn.cursor() as cur:
            if task.id is None:
                cur.execute(
                    """
                    INSERT INTO app.tasks (
                        task_name, enabled, keyword, description, analyze_images,
                        max_pages, personal_only, min_price, max_price, cron,
                        ai_prompt_base_file, ai_prompt_criteria_file, account_state_file,
                        account_strategy, free_shipping, new_publish_option, region,
                        decision_mode, keyword_rules_json, is_running
                    ) VALUES (
                        %(task_name)s, %(enabled)s, %(keyword)s, %(description)s, %(analyze_images)s,
                        %(max_pages)s, %(personal_only)s, %(min_price)s, %(max_price)s, %(cron)s,
                        %(ai_prompt_base_file)s, %(ai_prompt_criteria_file)s, %(account_state_file)s,
                        %(account_strategy)s, %(free_shipping)s, %(new_publish_option)s, %(region)s,
                        %(decision_mode)s, %(keyword_rules_json)s::jsonb, %(is_running)s
                    )
                    RETURNING id
                    """,
                    values,
                )
                new_id = int(cur.fetchone()["id"])
                conn.commit()
                return task.model_copy(update={"id": new_id})

            values["id"] = task.id
            cur.execute(
                """
                UPDATE app.tasks
                SET
                    task_name=%(task_name)s,
                    enabled=%(enabled)s,
                    keyword=%(keyword)s,
                    description=%(description)s,
                    analyze_images=%(analyze_images)s,
                    max_pages=%(max_pages)s,
                    personal_only=%(personal_only)s,
                    min_price=%(min_price)s,
                    max_price=%(max_price)s,
                    cron=%(cron)s,
                    ai_prompt_base_file=%(ai_prompt_base_file)s,
                    ai_prompt_criteria_file=%(ai_prompt_criteria_file)s,
                    account_state_file=%(account_state_file)s,
                    account_strategy=%(account_strategy)s,
                    free_shipping=%(free_shipping)s,
                    new_publish_option=%(new_publish_option)s,
                    region=%(region)s,
                    decision_mode=%(decision_mode)s,
                    keyword_rules_json=%(keyword_rules_json)s::jsonb,
                    is_running=%(is_running)s,
                    updated_at=NOW()
                WHERE id=%(id)s
                """,
                values,
            )
            conn.commit()
        return task

    def _delete_sync(self, task_id: int) -> bool:
        init_pg_schema()
        with pg_connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM app.tasks WHERE id = %s", (task_id,))
            deleted = cur.rowcount > 0
            conn.commit()
        return deleted
