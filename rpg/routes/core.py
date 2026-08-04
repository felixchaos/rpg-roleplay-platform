"""core.py — 入口 + 状态路由。

包含：
  GET  /                 — backend 根路径
  GET  /api/state        — 当前游戏状态快照
  GET  /api/state_events — state-change SSE 通道 (task 69)
"""
from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from platform_app.api._deps import json_response
from routes._deps_fastapi import get_current_user

router = APIRouter()


@router.get("/")
async def index():
    """Backend root。

    有「已构建」前端(同源/桌面/自托管)→ 裸 `/` 直出 SPA 壳 Platform.html。
    这样**桌面自托管开了局域网开关后**,同网设备浏览器访问 `http://<ip>:<端口>/`
    直接进应用,而不是看到后端 JSON 描述符(群反馈 听枫叶吹落的声音)。
    无 dist(纯 API / 未构建 dev,配合 Vite :5173)→ 回服务描述 JSON。
    注:web 生产由 nginx 边缘直出 Platform.html、Electron 窗口直接 loadURL /Platform.html,
    两者本就不经过此路由;受影响的只有「裸 / 浏览器直连后端」这一路径。"""
    try:
        from app import _FRONTEND_DIR, _FRONTEND_HAS_DIST
        if _FRONTEND_HAS_DIST:
            shell = _FRONTEND_DIR / "Platform.html"
            if shell.is_file():
                # 与 _SPAStaticFiles 同步:HTML 壳 no-cache(防部署后停在旧版)。
                return FileResponse(
                    str(shell),
                    media_type="text/html",
                    headers={"Cache-Control": "no-cache, must-revalidate"},
                )
    except Exception:
        pass
    from app import APP_TITLE
    return json_response({
        "ok": True,
        "service": f"{APP_TITLE} RPG backend",
        "frontend": {
            "platform": "Platform.html (Vite dev: http://127.0.0.1:5173/Platform.html)",
            "game_console": "Game Console.html (Vite dev: http://127.0.0.1:5173/Game%20Console.html)",
        },
        "docs": "/docs",
    })


@router.get("/api/health")
async def api_health() -> JSONResponse:
    """Liveness probe — 检查 DB 连通性。无需鉴权，供 k8s/nginx/监控调用。"""
    from core.version import app_version
    try:
        from platform_app.db import connect
        with connect() as db:
            db.execute("SELECT 1")
        return json_response({"ok": True, "db": "ok", "app_version": app_version()})
    except Exception as exc:
        return json_response(
            {"ok": False, "db": "error", "detail": str(exc)[:200], "app_version": app_version()},
            status_code=503)


@router.get("/api/state")
async def api_state(
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    from app import _payload
    return json_response(_payload(api_user))


@router.get("/api/state_events")
async def api_state_events(
    request: Request,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> StreamingResponse:
    """长连 SSE,推送当前 user 范围内的 state 变更事件。

    前端每个标签页开一条,收到 `event: state_change` 后转 CustomEvent
    `rpg-{topic}-updated`,各页面已有的 reload listener 自动触发。
    """
    import asyncio as _asyncio

    from state_event_bus import TooManySubscribers, subscribe, unsubscribe

    user_id = int((api_user or {}).get("id") or 0)
    if not user_id:
        return StreamingResponse(
            iter([f"event: error\ndata: {json.dumps({'message':'需要登录'}, ensure_ascii=False)}\n\n"]),
            media_type="text/event-stream",
            status_code=401,
        )

    try:
        queue = subscribe(user_id)
    except TooManySubscribers as exc:
        # 429: 单用户 SSE 上限保护, 防止 DoS
        return StreamingResponse(
            iter([f"event: error\ndata: {json.dumps({'message': str(exc), 'code': 'E_TOO_MANY_SUBSCRIBERS'}, ensure_ascii=False)}\n\n"]),
            media_type="text/event-stream",
            status_code=429,
        )

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
