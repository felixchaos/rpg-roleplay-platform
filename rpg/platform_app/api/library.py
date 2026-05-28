"""platform_app.api.library — /api/library/* 路由。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from .. import library as _library
from ._deps import json_response, require_user

router = APIRouter()


@router.get("/api/library")
async def api_library(request: Request, path: str = "", limit: int | None = None, cursor: str | None = None):
    user = require_user(request)
    try:
        return json_response(_library.list_dir(user["id"], path, limit, cursor))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/library/upload")
async def api_library_upload(request: Request):
    user = require_user(request)
    body = await request.json()
    try:
        return json_response(_library.upload(user["id"], body.get("path", ""), body.get("files") or []))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/library/mkdir")
async def api_library_mkdir(request: Request):
    user = require_user(request)
    body = await request.json()
    try:
        return json_response(_library.mkdir(user["id"], body.get("path", "")))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/library/delete")
async def api_library_delete(request: Request):
    user = require_user(request)
    body = await request.json()
    try:
        return json_response(_library.delete(user["id"], body.get("path", "")))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/library/download")
async def api_library_download(request: Request, path: str) -> FileResponse:
    user = require_user(request)
    try:
        target = _library.download_path(user["id"], path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not target.exists():
        raise HTTPException(status_code=404, detail="file not found")
    # 安全：所有用户上传文件强制下载，不允许浏览器把它当 html/svg/js 解析执行
    # 这避免了上传 .html → 同源 XSS、上传 .svg → 内嵌 JS 等场景
    download_name = target.name
    return FileResponse(
        target,
        media_type="application/octet-stream",
        filename=download_name,
        headers={
            "Content-Disposition": f'attachment; filename="{download_name}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
        },
    )
