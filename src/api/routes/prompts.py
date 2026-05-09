"""
Prompt 管理路由
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.services.prompt_template_service import (
    get_prompt_content,
    list_prompt_names,
    upsert_prompt_content,
)


router = APIRouter(prefix="/api/prompts", tags=["prompts"])


class PromptUpdate(BaseModel):
    """Prompt 更新模型"""
    content: str


@router.get("")
async def list_prompts():
    """列出所有 prompt 模板"""
    return list_prompt_names()


@router.get("/{filename}")
async def get_prompt(filename: str):
    """获取 prompt 内容"""
    try:
        content = get_prompt_content(filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if content is None:
        raise HTTPException(status_code=404, detail="Prompt 文件未找到")
    return {"filename": filename, "content": content}


@router.put("/{filename}")
async def update_prompt(
    filename: str,
    prompt_update: PromptUpdate,
):
    """更新 prompt 内容"""
    try:
        upsert_prompt_content(filename, prompt_update.content)
        return {"message": f"Prompt 文件 '{filename}' 更新成功"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as e:
        print(f"更新 Prompt 文件时出错: {e}")
        raise HTTPException(status_code=500, detail="更新 Prompt 文件失败，请检查服务日志")
