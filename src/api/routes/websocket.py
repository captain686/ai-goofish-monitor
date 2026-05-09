"""
WebSocket 路由
提供实时通信功能
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request, HTTPException
from typing import Set
import os

from src.security import session_store, build_ws_exchange_key


router = APIRouter()

# 全局 WebSocket 连接管理
active_connections: Set[WebSocket] = set()
session_connections: dict[str, WebSocket] = {}
MAX_WS_CONNECTIONS = int(os.getenv("MAX_WS_CONNECTIONS", "200"))


@router.post("/api/ws/ticket")
async def create_ws_ticket(request: Request):
    session_id = getattr(request.state, "session_id", None)
    if not session_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not session_store.get_session(session_id):
        raise HTTPException(status_code=401, detail="Unauthorized")
    ticket = session_store.create_ws_ticket(session_id)
    return {"ticket": ticket}


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
):
    """WebSocket 端点（使用一次性 ticket + 101 upgrade）"""
    if len(active_connections) >= MAX_WS_CONNECTIONS:
        await websocket.close(code=1013, reason="server_busy")
        return

    ticket = websocket.query_params.get("ticket", "")
    session_id = session_store.consume_ws_ticket(ticket)
    if not session_id:
        await websocket.close(code=1008, reason="invalid_or_expired_ticket")
        return

    if not session_store.get_session(session_id):
        await websocket.close(code=1008, reason="session_expired")
        return

    old_conn = session_connections.get(session_id)
    if old_conn is not None:
        try:
            await old_conn.close(code=1000, reason="replaced_by_new_connection")
        except Exception:
            pass
        active_connections.discard(old_conn)

    await websocket.accept()
    active_connections.add(websocket)
    session_connections[session_id] = websocket

    # 101 upgrade 后下发高熵交换密钥（单连接）
    exchange_key = build_ws_exchange_key()
    await websocket.send_json({
        "type": "ws_handshake",
        "data": {
            "exchange_key": exchange_key,
            "single_use": True,
        },
    })

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        active_connections.discard(websocket)
        if session_connections.get(session_id) is websocket:
            session_connections.pop(session_id, None)
    except Exception as e:
        print(f"WebSocket 错误: {e}")
        active_connections.discard(websocket)
        if session_connections.get(session_id) is websocket:
            session_connections.pop(session_id, None)


async def broadcast_message(message_type: str, data: dict):
    """向所有连接的客户端广播消息"""
    message = {
        "type": message_type,
        "data": data
    }

    disconnected = set()

    for connection in active_connections:
        try:
            await connection.send_json(message)
        except Exception:
            disconnected.add(connection)

    for connection in disconnected:
        active_connections.discard(connection)
