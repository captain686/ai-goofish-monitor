"""
新架构的主应用入口
整合所有路由和服务
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse, FileResponse
from fastapi import Request, HTTPException
from pydantic import BaseModel

from src.security import (
    SESSION_COOKIE_NAME,
    build_session_cookie,
    parse_session_cookie,
    session_store,
)

from src.api.routes import (
    dashboard,
    tasks,
    logs,
    settings,
    prompts,
    results,
    login_state,
    websocket,
    accounts,
)
from src.api.dependencies import (
    set_process_service,
    set_scheduler_service,
    set_task_generation_service,
)
from src.services.task_service import TaskService
from src.services.process_service import ProcessService
from src.services.scheduler_service import SchedulerService
from src.services.task_log_cleanup_service import cleanup_task_logs
from src.services.task_generation_service import TaskGenerationService
from src.infrastructure.persistence.sqlite_bootstrap import bootstrap_sqlite_storage
from src.infrastructure.persistence.sqlite_task_repository import SqliteTaskRepository
from src.infrastructure.persistence.postgres_task_repository import PostgresTaskRepository
from src.infrastructure.config.settings import settings as app_settings


# 全局服务实例
process_service = ProcessService()
scheduler_service = SchedulerService(process_service)
task_generation_service = TaskGenerationService()


def _build_task_repository():
    backend = os.getenv("APP_DB_BACKEND", "sqlite").strip().lower()
    if backend in {"postgres", "pgsql", "postgresql"}:
        return PostgresTaskRepository()
    return SqliteTaskRepository()


async def _sync_task_runtime_status(task_id: int, is_running: bool) -> None:
    task_service = TaskService(_build_task_repository())
    task = await task_service.get_task(task_id)
    if not task or task.is_running == is_running:
        return
    await task_service.set_task_running_flag(task_id, is_running)
    await websocket.broadcast_message(
        "task_status_changed",
        {"id": task_id, "is_running": is_running},
    )


process_service.set_lifecycle_hooks(
    on_started=lambda task_id: _sync_task_runtime_status(task_id, True),
    on_stopped=lambda task_id: _sync_task_runtime_status(task_id, False),
)

# 设置全局 ProcessService 实例供依赖注入使用
set_process_service(process_service)
set_scheduler_service(scheduler_service)
set_task_generation_service(task_generation_service)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时
    print("正在启动应用...")
    bootstrap_sqlite_storage()
    cleanup_task_logs(keep_days=app_settings.task_log_retention_days)

    if (
        app_settings.web_username == "admin"
        and app_settings.web_password == "admin123"
        and os.getenv("ALLOW_DEFAULT_WEB_CREDENTIALS", "false").strip().lower() not in {"1", "true", "yes", "on"}
    ):
        raise RuntimeError(
            "检测到默认管理账号密码(admin/admin123)。请在 .env 中设置 WEB_USERNAME/WEB_PASSWORD，或设置 ALLOW_DEFAULT_WEB_CREDENTIALS=true(仅开发环境)。"
        )

    # 重置所有任务状态为停止
    task_repo = _build_task_repository()
    task_service = TaskService(task_repo)
    tasks_list = await task_service.get_all_tasks()

    for task in tasks_list:
        if task.is_running:
            await task_service.update_task_status(task.id, False)

    # 加载定时任务
    await scheduler_service.reload_jobs(tasks_list)
    scheduler_service.start()

    print("应用启动完成")

    yield

    # 关闭时
    print("正在关闭应用...")
    scheduler_service.stop()
    await process_service.stop_all()
    print("应用已关闭")


# 创建 FastAPI 应用
app = FastAPI(
    title="闲鱼智能监控机器人",
    description="基于AI的闲鱼商品监控系统",
    version="2.0.0",
    lifespan=lifespan
)

# 注册路由
app.include_router(tasks.router)
app.include_router(dashboard.router)
app.include_router(logs.router)
app.include_router(settings.router)
app.include_router(prompts.router)
app.include_router(results.router)
app.include_router(login_state.router)
app.include_router(websocket.router)
app.include_router(accounts.router)

# 挂载静态文件
# 旧的静态文件目录（用于截图等）
app.mount("/static", StaticFiles(directory="static"), name="static")

# 挂载 Vue 3 前端构建产物
# 注意：需要在所有 API 路由之后挂载，以避免覆盖 API 路由
import os
if os.path.exists("dist"):
    app.mount("/assets", StaticFiles(directory="dist/assets"), name="assets")


# 健康检查端点
@app.get("/health")
async def health_check():
    """健康检查（无需认证）"""
    return {"status": "healthy", "message": "服务正常运行"}


# 全局会话鉴权中间件（除白名单外都要求登录）
AUTH_EXEMPT_PREFIXES = (
    "/assets",
    "/static",
)
AUTH_EXEMPT_EXACT = {
    "/",
    "/login",
    "/health",
    "/favicon.ico",
    "/auth/login",
    "/auth/me",
    "/auth/logout",
}


@app.middleware("http")
async def session_auth_middleware(request: Request, call_next):
    path = request.url.path

    # WebSocket upgrade / 前端静态与路由页面放行
    if path.startswith("/ws"):
        return await call_next(request)

    if path in AUTH_EXEMPT_EXACT or any(path.startswith(p) for p in AUTH_EXEMPT_PREFIXES):
        return await call_next(request)

    # 非 API 页面请求放行（由前端路由守卫处理登录跳转）
    if not path.startswith("/api") and not path.startswith("/auth"):
        return await call_next(request)

    cookie_value = request.cookies.get(SESSION_COOKIE_NAME)
    session_id = parse_session_cookie(cookie_value)
    if not session_id or not session_store.get_session(session_id):
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    request.state.session_id = session_id
    return await call_next(request)


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/auth/login")
async def auth_login(payload: LoginRequest):
    if payload.username != app_settings.web_username or payload.password != app_settings.web_password:
        raise HTTPException(status_code=401, detail="认证失败")

    session_id = session_store.create_session(payload.username)
    cookie = build_session_cookie(session_id)
    response = JSONResponse({"authenticated": True, "username": payload.username})
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=cookie,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=12 * 60 * 60,
        path="/",
    )
    return response


@app.get("/auth/me")
async def auth_me(request: Request):
    cookie_value = request.cookies.get(SESSION_COOKIE_NAME)
    session_id = parse_session_cookie(cookie_value)
    if not session_id:
        return JSONResponse(status_code=401, content={"authenticated": False})
    record = session_store.get_session(session_id)
    if not record:
        return JSONResponse(status_code=401, content={"authenticated": False})
    return {"authenticated": True, "username": record.username}


@app.post("/auth/logout")
async def auth_logout(request: Request):
    cookie_value = request.cookies.get(SESSION_COOKIE_NAME)
    session_id = parse_session_cookie(cookie_value)
    if session_id:
        session_store.revoke_session(session_id)
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response

@app.get("/")
async def read_root(request: Request):
    """提供 Vue 3 SPA 的主页面"""
    if os.path.exists("dist/index.html"):
        return FileResponse(
            "dist/index.html",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    else:
        return JSONResponse(
            status_code=500,
            content={"error": "前端构建产物不存在，请先运行 cd web-ui && npm run build"}
        )


# Catch-all 路由 - 处理所有前端路由（必须放在最后）
@app.get("/{full_path:path}")
async def serve_spa(request: Request, full_path: str):
    """
    Catch-all 路由，将所有非 API 请求重定向到 index.html
    这样可以支持 Vue Router 的 HTML5 History 模式
    """
    # 如果请求的是静态资源（如 favicon.ico），返回 404
    if full_path.endswith(('.ico', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.css', '.js', '.json')):
        return JSONResponse(status_code=404, content={"error": "资源未找到"})

    # 其他所有路径都返回 index.html，让前端路由处理
    if os.path.exists("dist/index.html"):
        return FileResponse(
            "dist/index.html",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    else:
        return JSONResponse(
            status_code=500,
            content={"error": "前端构建产物不存在，请先运行 cd web-ui && npm run build"}
        )


if __name__ == "__main__":
    import uvicorn
    from src.infrastructure.config.settings import settings

    print(f"启动新架构应用，端口: {app_settings.server_port}")
    uvicorn.run(app, host="0.0.0.0", port=app_settings.server_port)
