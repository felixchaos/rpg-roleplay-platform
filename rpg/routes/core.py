"""core.py — 入口 + 状态路由。

包含：
  GET  /                 — backend 根路径
  GET  /api/state        — 当前游戏状态快照
  GET  /api/state_events — state-change SSE 通道 (task 69)
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

router = APIRouter()


@router.get("/")
async def index() -> JSONResponse:
    """Backend root。前端由 frontend/ React 应用提供（Vite dev server 或静态部署）。"""
    from app import APP_TITLE
    return JSONResponse({
        "ok": True,
        "service": f"{APP_TITLE} RPG backend",
        "frontend": {
            "platform": "Platform.html (Vite dev: http://127.0.0.1:5173/Platform.html)",
            "game_console": "Game Console.html (Vite dev: http://127.0.0.1:5173/Game%20Console.html)",
        },
        "docs": "/docs",
    })


@router.get("/api/state")
async def api_state(request: Request) -> JSONResponse:
    from app import _payload, _require_api_user
    api_user = _require_api_user(request)
    return JSONResponse(_payload(api_user))


@router.get("/api/state_events")
async def api_state_events(request: Request) -> StreamingResponse:
    """长连 SSE,推送当前 user 范围内的 state 变更事件。

    前端每个标签页开一条,收到 `event: state_change` 后转 CustomEvent
    `rpg-{topic}-updated`,各页面已有的 reload listener 自动触发。
    """
    import asyncio as _asyncio

    from app import _require_api_user
    from state_event_bus import subscribe, unsubscribe

    api_user = _require_api_user(request)
    user_id = int((api_user or {}).get("id") or 0)
    if not user_id:
        return StreamingResponse(
            iter([f"event: error\ndata: {json.dumps({'message':'需要登录'}, ensure_ascii=False)}\n\n"]),
            media_type="text/event-stream",
            status_code=401,
        )

    queue = subscribe(user_id)

    async def _gen():
        try:
            # 立刻发一个 hello 让前端知道连上了
            yield (
                f"event: hello\ndata: "
                f"{json.dumps({'user_id': user_id, 'ts': time.time()}, ensure_ascii=False)}\n\n"
            )
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await _asyncio.wait_for(queue.get(), timeout=25.0)
                except TimeoutError:
                    # 25 秒没动静就发 keepalive,防 proxy 切连接
                    yield f": keepalive {int(time.time())}\n\n"
                    continue
                yield f"event: state_change\ndata: {event.to_sse_data()}\n\n"
        finally:
            unsubscribe(user_id, queue)

    return StreamingResponse(_gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })
