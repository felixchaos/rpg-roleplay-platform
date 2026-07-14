"""platform_app.api.me.account —— 账号级数据导出 / 导入端点(免部署服务 → 本地自部署 迁移)。

export/estimate 轻量统计、export 全量 zip、import 上传恢复。纯机械搬家,行为零变化。
"""
from __future__ import annotations

from fastapi import Depends, Request

from .._deps import json_response, require_user
from ._shared import router


# ── 账号级数据导出 / 导入(免部署服务 → 本地自部署 迁移)─────────────────
_MAX_ACCOUNT_IMPORT_BYTES = 300 * 1024 * 1024  # 与 account_io.MAX_ACCOUNT_ZIP_BYTES 对齐


@router.get("/api/me/account/export/estimate")
async def api_account_export_estimate(user=Depends(require_user)):
    """导出前轻量统计:剧本/存档/角色卡/模型条目数量,供前端展示规模。"""
    from ... import account_io
    return json_response(account_io.estimate_account(user["id"]))


@router.get("/api/me/account/export")
async def api_account_export(include_chunks: int = 0, user=Depends(require_user)):
    """聚合本账号全部个人数据为单个 zip 下载(剧本/存档/角色卡/偏好/模型清单)。

    include_chunks=1 时剧本包内含 document_chunks(体积大,默认不含)。不含 API 密钥。
    """
    from urllib.parse import quote as _quote

    from fastapi.responses import Response

    from fastapi.concurrency import run_in_threadpool

    from ... import account_io
    try:
        # #64:export_account 是重活(全量拉库 + 建 zip)。放线程池跑,别阻塞事件循环 ——
        # 否则大账号导出期间整个 worker 卡死,前端连接被代理判超时 → spinner 反复重置像失败。
        zip_bytes, filename = await run_in_threadpool(
            account_io.export_account, user["id"], bool(include_chunks),
        )
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)
    ascii_fallback = filename.encode("ascii", "ignore").decode("ascii") or "account-export.zip"
    quoted = _quote(filename, safe="")
    cd = f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quoted}"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": cd, "X-Content-Type-Options": "nosniff"},
    )


@router.post("/api/me/account/import")
async def api_account_import(request: Request, user=Depends(require_user)):
    """上传账号数据包 zip,把里面的剧本/存档/角色卡/偏好恢复到当前账号。

    支持 multipart/form-data 字段 file=<.zip>(前端走这条),或直接 application/zip body。
    """
    from fastapi import HTTPException

    from ... import account_io
    content_type = request.headers.get("content-type", "")
    try:
        if "multipart/form-data" in content_type:
            form = await request.form()
            file = form.get("file")
            if not file or not hasattr(file, "read"):
                raise HTTPException(status_code=400, detail="缺 file 字段")
            raw = await file.read()
        else:
            raw = await request.body()
        if not raw:
            raise HTTPException(status_code=400, detail="空文件")
        if len(raw) > _MAX_ACCOUNT_IMPORT_BYTES:
            raise HTTPException(status_code=400, detail=f"文件过大 (>{_MAX_ACCOUNT_IMPORT_BYTES // 1024 // 1024}MB)")
        if raw[:4] != b"PK\x03\x04":
            raise HTTPException(status_code=400, detail="不是合法的 zip 文件")
        # 异步作业:返回 job_id,前端用 streamImport 看真实逐项进度(剧本 i/N…)。
        return json_response(account_io.import_account_job(user["id"], raw))
    except HTTPException:
        raise
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)
