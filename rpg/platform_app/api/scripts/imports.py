"""platform_app.api.scripts.imports —— 剧本导入触发 + 分片上传 + pack 导入导出端点。

/api/scripts/import、preview、batch-import、/api/uploads/*(init/chunk/finish/cancel)、
export-pack、import-pack。含 .txt/.md 扩展名后端二次校验与 ZIP 炸弹有界解压。
纯机械搬家,行为零变化。
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response

from ... import script_import
from .._deps import json_response, require_user, value_error_response
from ._shared import router

# task 141: 测试期只允许 .txt / .md 剧本文本上传
_ALLOWED_SCRIPT_EXTS = (".txt", ".md")


def _check_script_ext(filename: str) -> None:
    name = (filename or "").lower()
    if not name.endswith(_ALLOWED_SCRIPT_EXTS):
        raise ValueError("仅支持 .txt / .md 剧本文件 — 测试期已禁用其他文件类型")


def _safe_zip_read(zf, name: str, max_bytes: int) -> bytes:
    """有界解压单个 ZIP 成员,防 zip 炸弹(CWE-409)。薄委托:权威实现见
    knowledge/script_pack.py 的 `_safe_member_read`(account_io.py 等已跨模块调用它)。
    """
    from platform_app.knowledge.script_pack import _safe_member_read
    return _safe_member_read(zf, name, max_bytes)


@router.post("/api/scripts/import")
async def api_import_script(request: Request, user=Depends(require_user)):
    body = await request.json()
    from ... import import_pipeline
    try:
        if body.get("require_llm_credentials"):
            import_pipeline.require_user_llm_credential(user["id"])
        # task 141: 后端二次校验文件名扩展。
        # 分片上传路径在 /api/uploads/init 已按真实 filename 校验过；这里的 title
        # 是剧本标题，不是文件名，不能拿它判断 .txt/.md，否则合法 upload_id 导入会被误拒。
        file_item = body.get("file") or {}
        fn = (file_item.get("name") or file_item.get("filename") or "")
        if fn:
            _check_script_ext(fn)
        # task 17: 之前漏传 upload_id，分片上传走完后端拿不到 raw → "请提供 file 或 upload_id"。
        # 现在透传 body.upload_id,单次 POST + 分片两条路径都能工作。
        return json_response({
            "ok": True,
            **script_import.import_script(
                user["id"],
                file_item,
                split_rule=body.get("split_rule", "auto"),
                custom_pattern=body.get("custom_pattern", ""),
                title=body.get("title", ""),
                upload_id=str(body.get("upload_id") or ""),
            ),
        })
    except import_pipeline.MissingUserCredentialError as exc:
        return json_response({
            "ok": False,
            "code": "credentials_required",
            "error_key": "credentials_required",
            "needs_credentials": True,
            "api_id": exc.api_id,
            "model": exc.model,
            "credential_api_id": exc.credential_api_id,
            "settings_hash": "settings-models",
            "error": str(exc),
        }, status_code=400)
    except ValueError as exc:
        return value_error_response(exc)


@router.post("/api/scripts/preview")
async def api_script_preview(request: Request, user=Depends(require_user)):
    """Dry-run：不入库返切分预览，前端调参用。"""
    body = await request.json()
    try:
        return json_response(script_import.preview_split(
            file_item=body.get("file"),
            split_rule=body.get("split_rule", "auto"),
            custom_pattern=body.get("custom_pattern", ""),
            upload_id=body.get("upload_id", ""),
            user_id=user["id"],
            sample_limit=int(body.get("sample_limit", 20)),
        ))
    except ValueError as exc:
        return value_error_response(exc)


@router.post("/api/scripts/batch-import")
async def api_scripts_batch_import(request: Request, user=Depends(require_user)):
    """从 ZIP 包批量导入剧本：每个 TXT/MD 视为一本书。

    Body: {"file": {"name": "books.zip", "base64": "..."}}
    """
    body = await request.json()
    file_item = body.get("file") or {}
    if not file_item:
        return json_response({"ok": False, "error": "缺 file"}, status_code=400)
    from ...library import decode_upload
    try:
        raw = decode_upload(file_item)
    except ValueError as exc:
        return value_error_response(exc)

    import io
    import zipfile
    if not zipfile.is_zipfile(io.BytesIO(raw)):
        return json_response({"ok": False, "error": "不是合法 ZIP 文件"}, status_code=400)

    imported = []
    failed = []
    max_per = script_import.MAX_SCRIPT_UPLOAD_BYTES
    max_total = max_per * 50  # 解压后总量上限,防 zip 炸弹累加打爆内存
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith((".txt", ".md"))]
        if len(names) > 50:
            return json_response({"ok": False, "error": "ZIP 最多包含 50 个文件"}, status_code=400)
        # 解压前用 ZipInfo.file_size 预检总量(CWE-409),超限直接拒,不进读取循环
        declared_total = sum(zf.getinfo(n).file_size for n in names)
        if declared_total > max_total:
            return json_response(
                {"ok": False, "error": f"ZIP 解压后总大小超限(max {max_total // 1024 // 1024}MB)"},
                status_code=400,
            )
        read_total = 0
        for name in names:
            try:
                content = _safe_zip_read(zf, name, max_per)
                read_total += len(content)
                if read_total > max_total:
                    return json_response(
                        {"ok": False, "error": "ZIP 实际解压总量超限"}, status_code=400
                    )
                import base64 as _b64
                result = script_import.import_script(
                    user["id"],
                    file_item={"name": name.rsplit("/", 1)[-1], "base64": _b64.b64encode(content).decode()},
                    split_rule=body.get("split_rule", "auto"),
                )
                imported.append({"name": name, "script_id": result["script"]["id"]})
            except Exception as exc:
                failed.append({"name": name, "error": str(exc)[:200]})
    return json_response({
        "ok": True, "imported": imported, "failed": failed,
        "total": len(names), "succeeded": len(imported),
    })


# ── 大文件分片上传（替代单次 base64 POST，避免内存爆）─────────────
@router.post("/api/uploads/init")
async def api_upload_init(request: Request, user=Depends(require_user)):
    """开始分片上传，返回 upload_id。"""
    body = await request.json()
    try:
        # task 141: 后端二次校验 — 阻止 .png/.zip/.jsonl 等通过分片上传通道绕过
        _check_script_ext(body.get("filename", ""))
        return json_response({"ok": True, **script_import.init_upload(
            user["id"],
            body.get("filename", ""),
            int(body.get("total_bytes") or 0),
            int(body.get("total_chunks") or 0),
        )})
    except ValueError as exc:
        return value_error_response(exc)


@router.post("/api/uploads/{upload_id}/chunk")
async def api_upload_chunk(request: Request, upload_id: str, user=Depends(require_user)):
    """上传一个 chunk。body: {"chunk_index": N, "base64": "..."}"""
    body = await request.json()
    try:
        import base64 as _b64
        blob = _b64.b64decode(str(body.get("base64") or ""), validate=True)
        return json_response({"ok": True, **script_import.put_chunk(
            user["id"], upload_id, int(body.get("chunk_index") or 0), blob,
        )})
    except (ValueError, __import__("binascii").Error) as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/uploads/{upload_id}/finish")
async def api_upload_finish(upload_id: str, user=Depends(require_user)):
    """全部分片到齐后调，返回 file_item（可直接传给 /api/scripts/import 的 file 字段）。"""
    try:
        return json_response(script_import.finish_upload(user["id"], upload_id))
    except ValueError as exc:
        return value_error_response(exc)


@router.post("/api/uploads/{upload_id}/cancel")
async def api_upload_cancel(upload_id: str, user=Depends(require_user)):
    """放弃上传，清掉服务器上的临时块。"""
    try:
        return json_response(script_import.cancel_upload(user["id"], upload_id))
    except ValueError as exc:
        return value_error_response(exc)


# ── script pack export / import ───────────────────────────────────────────────

@router.get("/api/scripts/{script_id}/export-pack")
async def api_export_script_pack(
    script_id: int,
    include_chunks: bool = False,
    user=Depends(require_user),
):
    """导出剧本为 zip pack。include_chunks=true 时把 document_chunks 一并打包。"""
    from platform_app.knowledge.script_pack import export_script_pack
    try:
        zip_bytes, filename = export_script_pack(script_id, user["id"], include_chunks=include_chunks)
    except PermissionError:
        raise HTTPException(status_code=403, detail="无权访问该剧本")
    # 文件名含中文时按 RFC 5987 编码,否则 latin-1 header 报 codec 错
    from urllib.parse import quote as _quote
    ascii_fallback = filename.encode("ascii", "ignore").decode("ascii") or "script_pack.zip"
    quoted = _quote(filename, safe="")
    cd = f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quoted}"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": cd},
    )


@router.post("/api/scripts/import-pack")
async def api_import_script_pack(request: Request, user=Depends(require_user)):
    """导入剧本 pack zip。

    接受 multipart/form-data 的 file 字段，或 application/octet-stream body。
    返回 {ok, script_id, warnings}。

    task 67: pack v2 完整(kb_canon/timeline_anchors/phase_digests/worldlines/nodes
    全部包含),旧 v1 包仍兼容导入(给出 warning 提示重跑 knowledge/sync)。
    """
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        form = await request.form()
        file = form.get("file")
        if not file:
            raise HTTPException(status_code=400, detail="missing file field")
        zip_bytes = await file.read()
    else:
        zip_bytes = await request.body()

    if not zip_bytes:
        raise HTTPException(status_code=400, detail="empty request body")

    from platform_app.knowledge.script_pack import MAX_ZIP_BYTES, import_script_pack
    if len(zip_bytes) > MAX_ZIP_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"file too large (max {MAX_ZIP_BYTES // 1024 // 1024}MB)",
        )

    try:
        result = import_script_pack(zip_bytes, user["id"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return json_response(result)
