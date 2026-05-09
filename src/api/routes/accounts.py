"""
闲鱼账号管理路由
"""
import json
import os
import re
import aiofiles
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List
from src.infrastructure.config.env_manager import env_manager
from src.services.account_state_pg_service import (
    delete_account_state as pg_delete_account_state,
    list_account_states as pg_list_account_states,
    load_account_state as pg_load_account_state,
    save_account_state as pg_save_account_state,
)


router = APIRouter(prefix="/api/accounts", tags=["accounts"])

ACCOUNT_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,50}$")


class AccountCreate(BaseModel):
    name: str
    content: str


class AccountUpdate(BaseModel):
    content: str


def _strip_quotes(value: str) -> str:
    if not value:
        return value
    if value.startswith(("\"", "'")) and value.endswith(("\"", "'")):
        return value[1:-1]
    return value


def _is_pg_enabled() -> bool:
    backend = os.getenv("APP_DB_BACKEND", "sqlite").strip().lower()
    return backend in {"postgres", "pgsql", "postgresql"}


def _state_dir() -> str:
    raw = env_manager.get_value("ACCOUNT_STATE_DIR", "state") or "state"
    return _strip_quotes(raw.strip())


def _ensure_state_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _validate_name(name: str) -> str:
    trimmed = name.strip()
    if not trimmed or not ACCOUNT_NAME_RE.match(trimmed):
        raise HTTPException(status_code=400, detail="账号名称只能包含字母、数字、下划线或短横线。")
    return trimmed


def _account_path(name: str) -> str:
    filename = f"{name}.json"
    return os.path.join(_state_dir(), filename)


def _validate_json(content: str) -> None:
    try:
        json.loads(content)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="提供的内容不是有效的JSON格式。")


@router.get("", response_model=List[dict])
async def list_accounts():
    if _is_pg_enabled():
        rows = pg_list_account_states()
        return [
            {
                "name": str((row.get("state_file") or "").rsplit("/", 1)[-1]).removesuffix(".json"),
                "path": row.get("state_file"),
            }
            for row in rows
        ]

    state_dir = _state_dir()
    if not os.path.isdir(state_dir):
        return []
    files = [f for f in os.listdir(state_dir) if f.endswith(".json")]
    accounts = []
    for filename in sorted(files):
        name = filename[:-5]
        accounts.append({
            "name": name,
            "path": os.path.join(state_dir, filename),
        })
    return accounts


@router.get("/{name}", response_model=dict)
async def get_account(name: str):
    account_name = _validate_name(name)
    path = _account_path(account_name)

    if _is_pg_enabled():
        row = pg_load_account_state(path)
        if not row:
            raise HTTPException(status_code=404, detail="账号不存在")
        content = json.dumps(row.get("state_json") or {}, ensure_ascii=False)
        return {"name": account_name, "path": path, "content": content}

    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="账号不存在")
    async with aiofiles.open(path, "r", encoding="utf-8") as f:
        content = await f.read()
    return {"name": account_name, "path": path, "content": content}


@router.post("", response_model=dict)
async def create_account(data: AccountCreate):
    account_name = _validate_name(data.name)
    _validate_json(data.content)
    state_dir = _state_dir()
    _ensure_state_dir(state_dir)
    path = _account_path(account_name)

    if _is_pg_enabled():
        existing = pg_load_account_state(path)
        if existing:
            raise HTTPException(status_code=409, detail="账号已存在")
        payload = json.loads(data.content)
        pg_save_account_state(
            state_file=path,
            account_id=str(payload.get("userId") or payload.get("user_id") or "") or None,
            nickname=str(payload.get("nickName") or payload.get("nickname") or "") or None,
            state_json=payload,
        )
        return {"message": "账号已添加", "name": account_name, "path": path}

    if os.path.exists(path):
        raise HTTPException(status_code=409, detail="账号已存在")
    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write(data.content)
    return {"message": "账号已添加", "name": account_name, "path": path}


@router.put("/{name}", response_model=dict)
async def update_account(name: str, data: AccountUpdate):
    account_name = _validate_name(name)
    _validate_json(data.content)
    state_dir = _state_dir()
    _ensure_state_dir(state_dir)
    path = _account_path(account_name)

    if _is_pg_enabled():
        existing = pg_load_account_state(path)
        if not existing:
            raise HTTPException(status_code=404, detail="账号不存在")
        payload = json.loads(data.content)
        pg_save_account_state(
            state_file=path,
            account_id=str(payload.get("userId") or payload.get("user_id") or "") or None,
            nickname=str(payload.get("nickName") or payload.get("nickname") or "") or None,
            state_json=payload,
        )
        return {"message": "账号已更新", "name": account_name, "path": path}

    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="账号不存在")
    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write(data.content)
    return {"message": "账号已更新", "name": account_name, "path": path}


@router.delete("/{name}", response_model=dict)
async def delete_account(name: str):
    account_name = _validate_name(name)
    path = _account_path(account_name)

    if _is_pg_enabled():
        deleted = pg_delete_account_state(path)
        if deleted <= 0:
            raise HTTPException(status_code=404, detail="账号不存在")
        return {"message": "账号已删除"}

    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="账号不存在")
    os.remove(path)
    return {"message": "账号已删除"}
