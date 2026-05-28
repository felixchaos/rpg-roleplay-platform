"""platform_app.api.scripts — /api/scripts*, /api/uploads/* 路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from .. import knowledge, script_import
from ..db import connect
from ._deps import json_response, require_user

router = APIRouter()


@router.get("/api/scripts")
async def api_scripts(limit: int | None = None, cursor: str | None = None, user=Depends(require_user)):
    from .. import workspace
    return json_response({"ok": True, **workspace.scripts_page(user["id"], limit, cursor)})


@router.post("/api/scripts/import")
async def api_import_script(request: Request, user=Depends(require_user)):
    body = await request.json()
    try:
        # task 17: 之前漏传 upload_id，分片上传走完后端拿不到 raw → "请提供 file 或 upload_id"。
        # 现在透传 body.upload_id，单次 POST + 分片两条路径都能工作。
        return json_response({
            "ok": True,
            **script_import.import_script(
                user["id"],
                body.get("file") or {},
                split_rule=body.get("split_rule", "auto"),
                custom_pattern=body.get("custom_pattern", ""),
                title=body.get("title", ""),
                upload_id=str(body.get("upload_id") or ""),
            ),
        })
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/scripts/{script_id}/chapters")
async def api_script_chapters(
    script_id: int,
    limit: int | None = None, cursor: str | None = None, q: str | None = None,
    user=Depends(require_user),
):
    """章节列表，支持 ?q=... 标题/内容全文 ILIKE 搜索。"""
    try:
        if q:
            # 全文搜索分支
            with connect() as db:
                owned = db.execute("select 1 from scripts where id=%s and owner_id=%s", (script_id, user["id"])).fetchone()
                if not owned:
                    return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
                rows = db.execute(
                    """
                    select id, chapter_index, title, volume_title, word_count,
                           substring(content for 200) as preview
                    from script_chapters
                    where script_id = %s and (title ilike %s or content ilike %s)
                    order by chapter_index limit %s
                    """,
                    (script_id, f"%{q}%", f"%{q}%", int(limit or 50)),
                ).fetchall()
            from ..db import expose as _expose
            return json_response({"ok": True, "items": [_expose(r) for r in rows], "query": q})
        return json_response({"ok": True, **script_import.list_chapters(user["id"], script_id, limit, cursor)})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/scripts/{script_id}/chapter-facts")
async def api_script_chapter_facts(script_id: int, limit: int | None = None, cursor: str | None = None, user=Depends(require_user)):
    try:
        return json_response({"ok": True, **knowledge.list_chapter_facts(user["id"], script_id, limit, cursor)})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/scripts/{script_id}/birthpoints")
async def api_script_birthpoints(script_id: int, user=Depends(require_user)):
    """入场选出生点：按 phase 聚合 + 每 phase 均匀采样代表性 anchor。

    返回 phase_digests 的各阶段，以及每阶段从 script_timeline_anchors 均匀采样的
    5-15 个 anchor（≤15 全取，否则步长 round(N/12) 采样）。
    """
    with connect() as db:
        owned = db.execute(
            "select 1 from scripts where id = %s and owner_id = %s",
            (script_id, user["id"]),
        ).fetchone()
        if not owned:
            return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)

        phase_rows = db.execute(
            """
            select phase_label, chapter_min, chapter_max, chapter_count, summary
            from phase_digests
            where script_id = %s
            order by chapter_min asc
            """,
            (script_id,),
        ).fetchall()

        phases = []
        for pr in phase_rows:
            anchor_rows = db.execute(
                """
                select id, story_time_label, chapter_min, chapter_max, chapter_count, sample_summary
                from script_timeline_anchors
                where script_id = %s
                  and chapter_min >= %s
                  and chapter_max <= %s
                order by chapter_min asc
                """,
                (script_id, int(pr["chapter_min"]), int(pr["chapter_max"])),
            ).fetchall()

            # 均匀采样：≤15 全取，否则步长 round(N/12)
            n = len(anchor_rows)
            if n <= 15:
                sampled = anchor_rows
            else:
                step = max(1, round(n / 12))
                sampled = anchor_rows[::step]
                # 确保末尾 anchor 也包含（代表 phase 尾部）
                if anchor_rows[-1] not in sampled:
                    sampled = list(sampled) + [anchor_rows[-1]]

            phases.append({
                "phase_label": pr["phase_label"],
                "chapter_min": int(pr["chapter_min"]),
                "chapter_max": int(pr["chapter_max"]),
                "chapter_count": int(pr["chapter_count"]),
                "summary": pr["summary"] or "",
                "anchors": [
                    {
                        "anchor_id": int(ar["id"]),
                        "story_time_label": ar["story_time_label"],
                        "chapter_min": int(ar["chapter_min"]),
                        "chapter_max": int(ar["chapter_max"]),
                        "chapter_count": int(ar["chapter_count"]),
                        "sample_summary": ar["sample_summary"] or "",
                    }
                    for ar in sampled
                ],
            })

    return json_response({"ok": True, "phases": phases})


@router.post("/api/scripts/{script_id}/recommend-identity")
async def api_script_recommend_identity(request: Request, script_id: int, user=Depends(require_user)):
    """task 123: 入场 wizard Step 4 — LLM 推荐玩家初始身份。
    入参 body: {birthpoint_phase, birthpoint_label, character_card_id?, character_card_kind?, n?}
    返回: {ok, recommendations: [{name, role, background}, ...]}
    """
    body = await request.json()
    # 校验 script 归属
    with connect() as db:
        owned = db.execute(
            "select 1 from scripts where id = %s and owner_id = %s",
            (script_id, user["id"]),
        ).fetchone()
        if not owned:
            return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
    # 调 recommend_player_identity 工具
    try:
        import secrets as _sec

        from console_assistant import dispatch_assistant_tool
        args = {
            "script_id": int(script_id),
            "birthpoint_phase": str(body.get("birthpoint_phase") or ""),
            "birthpoint_label": str(body.get("birthpoint_label") or ""),
            "n": int(body.get("n") or 4),
        }
        if body.get("character_card_id") is not None:
            args["character_card_id"] = int(body["character_card_id"])
        if body.get("character_card_kind"):
            args["character_card_kind"] = str(body["character_card_kind"])
        result = dispatch_assistant_tool(
            user_id=int(user["id"]),
            tool="recommend_player_identity",
            args=args,
            save_id=None,
            script_id=int(script_id),
            trace_id=f"wizard-{_sec.token_urlsafe(6)}",
            call_id=f"wiz-{_sec.token_urlsafe(6)}",
        )
        # 工具 return JSON 字符串, parse 一下
        import json as _j
        try:
            payload = _j.loads(result.result) if isinstance(result.result, str) else result.result
        except Exception:
            payload = {"ok": False, "error": "无法解析推荐结果", "raw": str(result.result)[:200]}
        if not result.ok:
            return json_response({"ok": False, "error": result.error or "工具执行失败"}, status_code=500)
        return json_response(payload)
    except Exception as exc:
        return json_response(
            {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
            status_code=500,
        )


@router.get("/api/scripts/{script_id}/character-cards")
async def api_script_character_cards(script_id: int, limit: int | None = None, cursor: str | None = None, user=Depends(require_user)):
    try:
        return json_response({"ok": True, **knowledge.list_character_cards(user["id"], script_id, limit, cursor)})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/scripts/{script_id}/character-cards/{card_id}")
async def api_script_character_card(script_id: int, card_id: int, user=Depends(require_user)):
    """单条剧本角色卡详情。"""
    try:
        card = knowledge.get_character_card(user["id"], script_id, card_id)
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=403)
    if not card:
        return json_response({"ok": False, "error": "character_card 不存在"}, status_code=404)
    return json_response({"ok": True, "card": card})


@router.post("/api/scripts/{script_id}/character-cards")
async def api_script_upsert_character_card(request: Request, script_id: int, user=Depends(require_user)):
    """创建/更新剧本角色卡（payload 传 id 则 update，否则 insert）。"""
    body = await request.json()
    try:
        return json_response({"ok": True, "card": knowledge.upsert_character_card(user["id"], script_id, body)})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/scripts/{script_id}/character-cards/{card_id}/delete")
async def api_script_delete_character_card(script_id: int, card_id: int, user=Depends(require_user)):
    try:
        return json_response(knowledge.delete_character_card(user["id"], script_id, card_id))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=403)


@router.post("/api/scripts/{script_id}/character-cards/{card_id}/enabled")
async def api_script_card_enabled(request: Request, script_id: int, card_id: int, user=Depends(require_user)):
    """快捷切换 enabled（检索中临时屏蔽某角色）。"""
    body = await request.json()
    try:
        return json_response({"ok": True, "card": knowledge.set_character_card_enabled(
            user["id"], script_id, card_id, bool(body.get("enabled", True))
        )})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/scripts/{script_id}/worldbook")
async def api_script_worldbook(script_id: int, limit: int | None = None, cursor: str | None = None, user=Depends(require_user)):
    try:
        return json_response({"ok": True, **knowledge.list_worldbook_entries(user["id"], script_id, limit, cursor)})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/scripts/{script_id}/chapters/{chapter_index}")
async def api_chapter_update(request: Request, script_id: int, chapter_index: int, user=Depends(require_user)):
    """编辑单章 title/content/volume_title。"""
    body = await request.json()
    try:
        return json_response(script_import.update_chapter(
            user["id"], script_id, chapter_index,
            title=body.get("title"), content=body.get("content"),
            volume_title=body.get("volume_title"),
        ))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/scripts/{script_id}/chapters/merge")
async def api_chapter_merge(request: Request, script_id: int, user=Depends(require_user)):
    """合并 first_index 和 first_index+1 两章。"""
    body = await request.json()
    try:
        return json_response(script_import.merge_chapters(
            user["id"], script_id, int(body.get("first_index") or 0),
            separator=body.get("separator") or "\n\n",
        ))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/scripts/{script_id}/chapters/{chapter_index}/split")
async def api_chapter_split(request: Request, script_id: int, chapter_index: int, user=Depends(require_user)):
    """按字符位置 split_at 把一章拆成两章。"""
    body = await request.json()
    try:
        return json_response(script_import.split_chapter(
            user["id"], script_id, chapter_index,
            split_at=int(body.get("split_at") or 0),
            new_title=body.get("new_title") or "",
        ))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/scripts/{script_id}/resplit")
async def api_script_resplit(request: Request, script_id: int, user=Depends(require_user)):
    """用新规则重切已导入剧本。保留 script + 存档，只换章节。"""
    body = await request.json()
    try:
        return json_response(script_import.resplit_script(
            user["id"], script_id,
            split_rule=body.get("split_rule", "auto"),
            custom_pattern=body.get("custom_pattern", ""),
        ))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/scripts/{script_id}/delete")
async def api_script_delete(request: Request, script_id: int, user=Depends(require_user)):
    """删除剧本。force=True 时连带删除其下所有存档。"""
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    try:
        return json_response(script_import.delete_script(user["id"], script_id, force=bool(body.get("force"))))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=403)


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
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/scripts/batch-import")
async def api_scripts_batch_import(request: Request, user=Depends(require_user)):
    """从 ZIP 包批量导入剧本：每个 TXT/MD 视为一本书。

    Body: {"file": {"name": "books.zip", "base64": "..."}}
    """
    body = await request.json()
    file_item = body.get("file") or {}
    if not file_item:
        return json_response({"ok": False, "error": "缺 file"}, status_code=400)
    from ..library import decode_upload
    try:
        raw = decode_upload(file_item)
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)

    import io
    import zipfile
    if not zipfile.is_zipfile(io.BytesIO(raw)):
        return json_response({"ok": False, "error": "不是合法 ZIP 文件"}, status_code=400)

    imported = []
    failed = []
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith((".txt", ".md"))]
        if len(names) > 50:
            return json_response({"ok": False, "error": "ZIP 最多包含 50 个文件"}, status_code=400)
        for name in names:
            try:
                content = zf.read(name)
                if len(content) > script_import.MAX_SCRIPT_UPLOAD_BYTES:
                    failed.append({"name": name, "error": "too large"})
                    continue
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
        return json_response({"ok": True, **script_import.init_upload(
            user["id"],
            body.get("filename", ""),
            int(body.get("total_bytes") or 0),
            int(body.get("total_chunks") or 0),
        )})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


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
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/uploads/{upload_id}/cancel")
async def api_upload_cancel(upload_id: str, user=Depends(require_user)):
    """放弃上传，清掉服务器上的临时块。"""
    try:
        return json_response(script_import.cancel_upload(user["id"], upload_id))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


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

    return JSONResponse(result)


# ── script overrides API ──────────────────────────────────────────────────────

@router.get("/api/scripts/{script_id}/overrides")
async def api_get_script_overrides(script_id: int, user=Depends(require_user)):
    """查询剧本 overrides（能访问该 script 的用户均可读）。"""
    with connect() as db:
        owned = db.execute(
            "SELECT 1 FROM scripts WHERE id = %s AND owner_id = %s",
            (script_id, user["id"]),
        ).fetchone()
    if not owned:
        return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
    from platform_app.knowledge.script_overrides import get_overrides_by_script_id
    data = get_overrides_by_script_id(script_id)
    return json_response({"ok": True, "data": data})


@router.post("/api/scripts/{script_id}/overrides")
async def api_update_script_overrides(request: Request, script_id: int, user=Depends(require_user)):
    """更新剧本 overrides（仅 owner）。

    Body: overrides data dict（直接替换整条记录）。
    """
    with connect() as db:
        owned = db.execute(
            "SELECT 1 FROM scripts WHERE id = %s AND owner_id = %s",
            (script_id, user["id"]),
        ).fetchone()
    if not owned:
        return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return json_response({"ok": False, "error": "请求 body 必须是合法 JSON"}, status_code=400)
    # 支持两种格式: {"data": {...}} 或直接 {...}
    overrides_data = body.get("data") if isinstance(body.get("data"), dict) else body
    from platform_app.knowledge.script_overrides import upsert_overrides
    upsert_overrides(script_id, overrides_data)
    return json_response({"ok": True})
