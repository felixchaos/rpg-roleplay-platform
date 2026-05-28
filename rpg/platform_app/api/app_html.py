"""platform_app.api.app_html — GET /app, /app/{path:path} 路由。"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from ..pages import PLATFORM_HTML

router = APIRouter()


@router.get("/app", response_class=HTMLResponse)
@router.get("/app/{path:path}", response_class=HTMLResponse)
async def app_page() -> HTMLResponse:
    return HTMLResponse(PLATFORM_HTML)
