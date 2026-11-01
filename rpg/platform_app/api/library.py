"""platform_app.api.library — /api/library/* 路由（S5 只读资产管理器）。

S5 重构：文件库从"手动上传文件管理器"改为"统一用户资产只读管理器"。
  - 移除 POST /api/library/upload（恒 405）
  - 移除 POST /api/library/mkdir（恒 405）
  - 移除 RPG_LIBRARY_ENABLED 禁用闸（只读 + 功能组件产生，无任意上传风险）
  - 列表/单项/下载/删除关联检查，全部接 assets_registry / storage

端点列表：
  GET  /api/library                       — 列出资产（kind/limit/offset）
  GET  /api/library/asset/{id}            — 单项（owner 校验）
  GET  /api/library/asset/{id}/download   — 文件下载（attachment）
  POST /api/library/asset/{id}/delete     — 删除（含引用检查 + confirm 二次确认）
  POST /api/library/upload                — 405 已移除
  POST /api/library/mkdir                 — 405 已移除
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from .. import library as _library
from ._deps import json_response, require_user

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /api/library — 列出资产
# ---------------------------------------------------------------------------

@router.get("/api/library")
async def api_library_list(
    kind: str | None = None,
    limit: int = 50,
    offset: int = 0,
    user=Depends(require_user),
):
    """列出当前用户所有资产（来自 user_assets 表）。

    ?kind=image|video|document|file|archive  可选过滤
    ?limit=50&offset=0                       分页
    """
    try:
        return json_response(_library.list_assets(user["id"], kind=kind, limit=limit, offset=offset))
    except Exception as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# GET /api/library/asset/{asset_id} — 单项
# ---------------------------------------------------------------------------

@router.get("/api/library/asset/{asset_id}")
async def api_library_asset(asset_id: int, user=Depends(require_user)):
    """查单个资产，owner 校验。不存在或不属于该用户返回 404。"""
    asset = _library.get_asset(user["id"], asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="资产不存在或无权访问")
    return json_response({"ok": True, "asset": asset})


# ---------------------------------------------------------------------------
# GET /api/library/asset/{asset_id}/download — 文件下载
# ---------------------------------------------------------------------------

@router.get("/api/library/asset/{asset_id}/download")
async def api_library_download(asset_id: int, user=Depends(require_user)):
    """下载资产文件（Content-Disposition: attachment）。

    owner 校验在 asset_download_path 内完成（get_asset → user_id 严格匹配）。
    强制 attachment + nosniff，防止同源 XSS。
    """
    try:
        asset, path = _library.asset_download_path(user["id"], asset_id)
    except ValueError as exc:
        err = str(exc)
        if err == "not_found":
            raise HTTPException(status_code=404, detail="资产不存在或无权访问")
        if err == "file_missing":
            raise HTTPException(status_code=404, detail="物理文件已丢失")
        raise HTTPException(status_code=400, detail=err)

    filename = path.name
    mime = asset.get("mime") or "application/octet-stream"
    return FileResponse(
        path,
        media_type=mime,
        filename=filename,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
        },
    )


# ---------------------------------------------------------------------------
# POST /api/library/asset/{asset_id}/delete — 删除关联检查
# ---------------------------------------------------------------------------

@router.post("/api/library/asset/{asset_id}/delete")
async def api_library_delete_asset(asset_id: int, request: Request, user=Depends(require_user)):
    """删除资产（含引用检查 + 二次确认 + 置空引用 + force 删）。

    request body（可选）: {"confirm": true}，只有 JSON 布尔 true 才确认解除引用。

    返回：
      {ok: false, needs_confirm: true, references: [...]}  — 有引用且未 confirm
      {ok: true,  deleted: true}                           — 删除成功
      {ok: false, error: "not_found"}                      — 不存在或无权
    """
    body: dict = {}
    if await request.body():
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象") from None
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")

    confirm = body.get("confirm") is True
    result = _library.delete_asset_with_refs(user["id"], asset_id, confirm=confirm)
    status = 200
    if not result.get("ok") and result.get("error") == "not_found":
        status = 404
    return json_response(result, status_code=status)


# ---------------------------------------------------------------------------
# 已移除：上传 / mkdir（返回 405）
# ---------------------------------------------------------------------------

@router.post("/api/library/upload")
async def api_library_upload_removed(user=Depends(require_user)):
    """手动上传已移除。文件库只展示系统功能组件（生图/头像/封面等）自动产生的资产。"""
    return json_response(
        {"ok": False, "error": "文件库不支持手动上传，请使用生图或头像上传功能", "code": "method_not_allowed"},
        status_code=405,
    )


@router.post("/api/library/mkdir")
async def api_library_mkdir_removed(user=Depends(require_user)):
    """创建文件夹已移除，文件库无目录树结构。"""
    return json_response(
        {"ok": False, "error": "文件库不支持手动创建文件夹", "code": "method_not_allowed"},
        status_code=405,
    )
