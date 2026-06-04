"""platform_app.api.scripts — /api/scripts*, /api/uploads/* 路由。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from .. import knowledge, script_import
from ..db import connect
from ._deps import json_response, require_user

router = APIRouter()


# task 141: 测试期只允许 .txt / .md 剧本文本上传
_ALLOWED_SCRIPT_EXTS = (".txt", ".md")


def _check_script_ext(filename: str) -> None:
    name = (filename or "").lower()
    if not name.endswith(_ALLOWED_SCRIPT_EXTS):
        raise ValueError("仅支持 .txt / .md 剧本文件 — 测试期已禁用其他文件类型")


def _safe_zip_read(zf, name: str, max_bytes: int) -> bytes:
    """有界解压单个 ZIP 成员,防 zip 炸弹(CWE-409)。

    1) 先用 ZipInfo.file_size 预检(挡诚实的炸弹,免解压);
    2) 再以 max_bytes+1 上限流式读取(挡谎报 header 的炸弹,实读超限即拒)。
    """
    info = zf.getinfo(name)
    if info.file_size > max_bytes:
        raise ValueError(f"成员解压后过大: {name}")
    with zf.open(name) as fh:
        data = fh.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"成员解压超限: {name}")
    return data


@router.get("/api/scripts")
async def api_scripts(limit: int | None = None, cursor: str | None = None, user=Depends(require_user)):
    from .. import workspace
    return json_response({"ok": True, **workspace.scripts_page(user["id"], limit, cursor)})


@router.post("/api/scripts/import")
async def api_import_script(request: Request, user=Depends(require_user)):
    body = await request.json()
    from .. import import_pipeline
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
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


# phase_backend: 旧 POST /api/scripts/{id}/embed 移到 api/imports.py 作为
# /rebuild/embeddings 的 alias(走统一 import_jobs + SSE);此处只留 /embed/status。


@router.get("/api/scripts/{script_id}/modules-status")
async def api_script_modules_status(script_id: int, user=Depends(require_user)):
    """phase_backend: 一次返 7 模块各自的 done/total/stale/last_job_id。

    7 模块:chunks/chapter-facts/canon/cards/worldbook/anchors/embeddings
    每模块返:
      done: 当前已落库的行数(>0 即视为可用)
      total: 目标数(章节数 / canon entity 数 等参考值)
      stale: 是否过期(若有更晚的同 script 写入但本模块未跟上,如 chapters 改了但 chunks 未重建)
      last_job_id: 最近一次本模块的 import_jobs.job_id(可用于继续/重订 SSE)
    """
    with connect() as db:
        owned = db.execute(
            """select s.chapter_count, s.updated_at from scripts s
            where s.id = %s and (
              s.owner_id = %s
              or s.id in (select script_id from user_script_subscriptions where user_id = %s)
            )""",
            (script_id, user["id"], user["id"]),
        ).fetchone()
        if not owned:
            return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
        script_updated = owned.get("updated_at")
        chapter_count = int(owned.get("chapter_count") or 0)

        # 各模块当前 done / total
        def _scalar(sql: str) -> int:
            row = db.execute(sql, (script_id,)).fetchone()
            return int(row["c"]) if row else 0

        chunks_done = _scalar("select count(*) as c from document_chunks where script_id = %s")
        facts_done = _scalar("select count(*) as c from chapter_facts where script_id = %s")
        canon_done = _scalar("select count(*) as c from kb_canon_entities where script_id = %s")
        cards_done = _scalar("select count(*) as c from character_cards where script_id = %s and card_type='npc'")
        wb_done = _scalar("select count(*) as c from worldbook_entries where script_id = %s")
        anchors_done = _scalar("select count(*) as c from script_timeline_anchors where script_id = %s")
        # embeddings — chunks 的 embedding_vec 是真相源(不是 jsonb embedding)
        embed_done = _scalar(
            "select count(*) as c from document_chunks where script_id = %s and embedding_vec is not null"
        )

        # 每模块最近一次 job(by kind)
        kind_to_module = {
            "rebuild_chunks": "chunks",
            "rebuild_facts": "chapter-facts",
            "rebuild_canon": "canon",
            "rebuild_cards": "cards",
            "rebuild_worldbook": "worldbook",
            "rebuild_anchors": "anchors",
            "rebuild_embeddings": "embeddings",
            "full_pipeline": "full_pipeline",
            "llm_extract": "llm_extract",
        }
        job_rows = db.execute(
            "select kind, job_id, status, finished_at, created_at "
            "from import_jobs where script_id = %s "
            "order by created_at desc limit 50",
            (script_id,),
        ).fetchall()
        last_job_by_module: dict[str, dict[str, Any]] = {}
        for r in job_rows:
            kind = r.get("kind") or ""
            mod = kind_to_module.get(kind)
            if not mod or mod in last_job_by_module:
                continue
            last_job_by_module[mod] = {
                "job_id": r.get("job_id"),
                "status": r.get("status"),
                "finished_at": str(r.get("finished_at")) if r.get("finished_at") else None,
                "kind": kind,
            }

    # E2E 暴露:rebuild-panel agent 的前端读 m.done_count/m.total_count/m.status,
    # 但 _build 返的是 done/total + 没 status → 卡片"条数:—" + "modules.status.unknown"
    # 同时双写新字段(done_count/total_count/status)+ 老字段(done/total)兼容
    def _build(name: str, done: int, total: int) -> dict[str, Any]:
        lj = last_job_by_module.get(name)
        stale = False
        if lj and lj.get("finished_at") and script_updated and done > 0:
            stale = str(script_updated) > str(lj.get("finished_at"))
        # status 派生:
        #   running: 有活跃 job (pending/running)
        #   stale:   旧版数据但 chapters 已变
        #   ready:   done>=total>0 或 done>0 且 total=0(canon/cards 等无 total 概念)
        #   partial: 0<done<total
        #   missing: done==0
        if lj and lj.get("status") in ("pending", "running"):
            status = "running"
        elif stale:
            status = "stale"
        elif total > 0:
            status = "ready" if done >= total else ("partial" if done > 0 else "missing")
        else:
            status = "ready" if done > 0 else "missing"
        return {
            "module": name,
            "done": done,
            "total": total,
            "done_count": done,       # 新字段名,前端 ModuleStatusCard 期望的
            "total_count": total,     # 同上
            "status": status,         # 派生 'ready'|'partial'|'missing'|'stale'|'running'
            "stale": stale,
            "last_job_id": (lj or {}).get("job_id"),
            "last_status": (lj or {}).get("status"),
        }

    return json_response({
        "ok": True,
        "script_id": script_id,
        "modules": [
            _build("chunks", chunks_done, max(chapter_count, 1)),
            _build("chapter-facts", facts_done, max(chapter_count, 1)),
            _build("canon", canon_done, 0),
            _build("cards", cards_done, 0),
            _build("worldbook", wb_done, 0),
            _build("anchors", anchors_done, 0),
            _build("embeddings", embed_done, max(chunks_done, 1)),
        ],
    })


@router.get("/api/scripts/{script_id}/embed/status")
async def api_script_embed_status(script_id: int, user=Depends(require_user)):
    """task 51: 查询某剧本的向量化进度。前端轮询用。"""
    from ..knowledge import embedding as _embed
    with connect() as db:
        owned = db.execute(
            """select 1 from scripts s
            where s.id = %s and (
              s.owner_id = %s
              or s.id in (select script_id from user_script_subscriptions where user_id = %s)
            )""",
            (script_id, user["id"], user["id"]),
        ).fetchone()
    if not owned:
        return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
    return json_response({"ok": True, "status": _embed.embed_status(script_id)})


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


@router.get("/api/scripts/{script_id}/timeline")
async def api_script_timeline(script_id: int, user=Depends(require_user)):
    """剧本时间线锚点 — script_timeline_anchors 全量按 chapter_min 顺序返。

    跟 /birthpoints (按 phase 聚合采样,给入场选择用) 不同:
    本 endpoint 给"时间线编辑器 tab"用,要看到所有 anchor + 故事时间标签。
    返:{phases: [{phase_label, anchors: [{chapter_min/max, story_time_label, sample_summary, story_phase}]}]}
    若 story_phase 全为空(LLM extract 没填),把全部 anchor 放到一个"未分阶段"桶。
    """
    with connect() as db:
        owned = db.execute(
            """select 1 from scripts s
            where s.id = %s and (
              s.owner_id = %s
              or s.id in (select script_id from user_script_subscriptions where user_id = %s)
            )""",
            (script_id, user["id"], user["id"]),
        ).fetchone()
        if not owned:
            return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
        rows = db.execute(
            """
            select id, story_phase, story_time_label, chapter_min, chapter_max,
                   chapter_count, sample_summary, confidence
            from script_timeline_anchors
            where script_id = %s
            order by chapter_min asc, id asc
            """,
            (script_id,),
        ).fetchall()
    # 按 story_phase 聚合;phase 全空时归"未分阶段"
    buckets: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        phase = (r.get("story_phase") or "").strip() or "未分阶段"
        buckets.setdefault(phase, []).append({
            "anchor_id": r["id"],
            "id": r["id"],
            "story_time_label": r["story_time_label"],
            "chapter_min": r["chapter_min"],
            "chapter_max": r["chapter_max"],
            "chapter_count": r["chapter_count"],
            "sample_summary": r["sample_summary"],
            "confidence": float(r["confidence"] or 0),
        })
    phases = []
    for p, items in buckets.items():
        cmins = [a["chapter_min"] for a in items if a.get("chapter_min") is not None]
        cmaxs = [a["chapter_max"] for a in items if a.get("chapter_max") is not None]
        phases.append({
            "phase_label": p,
            "chapter_min": min(cmins) if cmins else None,
            "chapter_max": max(cmaxs) if cmaxs else None,
            "anchor_count": len(items),
            "anchors": items,
        })
    return json_response({"ok": True, "phases": phases, "total": len(rows)})


@router.get("/api/scripts/{script_id}/birthpoints")
async def api_script_birthpoints(script_id: int, user=Depends(require_user)):
    """入场选出生点：按 phase 聚合 + 每 phase 均匀采样代表性 anchor。

    返回 phase_digests 的各阶段，以及每阶段从 script_timeline_anchors 均匀采样的
    5-15 个 anchor（≤15 全取，否则步长 round(N/12) 采样）。
    """
    with connect() as db:
        owned = db.execute(
            """select 1 from scripts s
            where s.id = %s and (
              s.owner_id = %s
              or s.id in (select script_id from user_script_subscriptions where user_id = %s)
            )""",
            (script_id, user["id"], user["id"]),
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

        # phase_digests 空时 fallback:把 script_timeline_anchors 按章节分 5 段
        # (开端/发展前期/发展中期/发展后期/结局),每段渲染成一个 phase。
        # 否则 wizard 出生点选择面板永远显示"暂无出生点锚点"。
        if not phase_rows:
            anchor_chapter_min_max = db.execute(
                """
                select coalesce(min(chapter_min), 1) as chmin,
                       coalesce(max(chapter_max), 1) as chmax,
                       count(*) as n
                from script_timeline_anchors where script_id = %s
                """,
                (script_id,),
            ).fetchone()
            n = int((anchor_chapter_min_max or {}).get("n") or 0)
            if n > 0:
                chmin = int(anchor_chapter_min_max["chmin"])
                chmax = int(anchor_chapter_min_max["chmax"])
                span = max(1, chmax - chmin + 1)
                seg = max(1, span // 5)
                phase_labels = ["开端", "发展前期", "发展中期", "发展后期", "结局"]
                phase_rows = []
                for i, label in enumerate(phase_labels):
                    lo = chmin + i * seg
                    hi = chmin + (i + 1) * seg - 1 if i < 4 else chmax
                    phase_rows.append({
                        "phase_label": label,
                        "chapter_min": lo,
                        "chapter_max": hi,
                        "chapter_count": hi - lo + 1,
                        "summary": "",
                    })

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
            """select 1 from scripts s
            where s.id = %s and (
              s.owner_id = %s
              or s.id in (select script_id from user_script_subscriptions where user_id = %s)
            )""",
            (script_id, user["id"], user["id"]),
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
        # player_origin: 'isekai'(穿越/转生) | 'native'(原作角色) — 透到 LLM 工具,
        # 决定生成的 4 个候选是"现代灵魂穿越成 X"还是"原作世界里的 X 身份"
        po = str(body.get("player_origin") or "").lower()
        if po == "isekai":
            po = "soul"  # 旧值兼容
        if po in ("soul", "body", "dual", "native"):
            args["player_origin"] = po
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
            return json_response({"ok": False, "error": result.error or "工具执行失败"}, status_code=200)
        # task: 工具自报 ok=false (LLM 403 / 上下文不足 / 模型不可用 等)返 200 + ok:false,
        # payload.error 含详细原因(如 Vertex 403:用户 SA 缺权限 / 未启用 API)。
        # 前端按 ok 字段判断,不再被 HTTP 502 generic message 吞掉真因。
        # (旧设计返 502 让前端"区分系统问题",反而让真错误信息丢失。)
        if isinstance(payload, dict) and payload.get("ok") is False:
            return json_response(payload, status_code=200)
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


@router.get("/api/scripts/{script_id}/chapters/{chapter_index}")
async def api_chapter_detail(script_id: int, chapter_index: int, user=Depends(require_user)):
    """单章节完整 content(列表 API 只返 180 字符 preview,这里是 lazy fetch 真章节正文)。"""
    with connect() as db:
        owned = db.execute(
            """select 1 from scripts s
            where s.id = %s and (
              s.owner_id = %s
              or s.id in (select script_id from user_script_subscriptions where user_id = %s)
            )""",
            (script_id, user["id"], user["id"]),
        ).fetchone()
        if not owned:
            return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
        row = db.execute(
            """
            select id, public_id, chapter_index, title, volume_title,
                   word_count, content, created_at, updated_at
            from script_chapters
            where script_id = %s and chapter_index = %s
            """,
            (script_id, chapter_index),
        ).fetchone()
    if not row:
        return json_response({"ok": False, "error": "章节不存在"}, status_code=404)
    from ..db import expose as _expose
    return json_response({"ok": True, "chapter": _expose(row)})


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
        raise HTTPException(status_code=403, detail="无权访问该剧本") from None
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
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return JSONResponse(result)


# ── 在线剧本库(公开分享 / 浏览 / 导入)─────────────────────────────────────────

@router.post("/api/scripts/{script_id}/visibility")
async def api_script_visibility(request: Request, script_id: int, user=Depends(require_user)):
    """owner 设置剧本是否公开分享。Body: {is_public: bool}。

    公开后内容(章节/角色卡/世界书)对所有用户可浏览并导入到自己账户。
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    is_public = bool(body.get("is_public"))
    with connect() as db:
        owned = db.execute(
            "SELECT chapter_count, review_status FROM scripts WHERE id = %s AND owner_id = %s",
            (script_id, user["id"]),
        ).fetchone()
        if not owned:
            return json_response({"ok": False, "error": "无权操作该剧本"}, status_code=403)
        if is_public:
            # 护栏:0 章空剧本(注册默认档 / 未导入正文)不允许公开,避免污染公开库。
            # 以 script_chapters 实际行数为准(chapter_count 列可能陈旧)。
            real_ch = db.execute(
                "SELECT count(*) AS n FROM script_chapters WHERE script_id = %s",
                (script_id,),
            ).fetchone()
            if not (dict(real_ch) or {}).get("n", 0):
                return json_response(
                    {"ok": False, "error": "空剧本(0 章)不能公开分享,请先导入正文。"},
                    status_code=400,
                )
            # KB 复核闸:未通过复核的剧本不允许分享到公开库(与新建存档闸一致),
            # 防止未审实体/未消歧别名/错章节污染公开剧本库。前端也会预拦并引导,
            # 此处是确定性后端兜底(不依赖前端)。重切(resplit)后会自动回 unreviewed。
            if (dict(owned) or {}).get("review_status", "unreviewed") != "reviewed":
                return json_response(
                    {"ok": False, "error": "REVIEW_REQUIRED",
                     "message": "分享到公开库前需先通过 KB 复核:请在剧本「KB 核查」中检查实体/世界线/时间锚无误后点击「标记已复核」。"},
                    status_code=409,
                )
        db.execute(
            "UPDATE scripts SET is_public = %s, "
            "published_at = COALESCE(published_at, CASE WHEN %s THEN now() ELSE NULL END) "
            "WHERE id = %s",
            (is_public, is_public, script_id),
        )
        db.commit()
    return json_response({"ok": True, "is_public": is_public})


@router.get("/api/scripts/public")
async def api_public_scripts(q: str | None = None, limit: int = 30, offset: int = 0,
                             user=Depends(require_user)):
    """浏览公开剧本库。支持标题/简介搜索,按发布时间倒序。"""
    limit = max(1, min(int(limit or 30), 60))
    offset = max(0, int(offset or 0))
    where = "s.is_public"
    params: list = []
    if q:
        where += " AND (s.title ILIKE %s OR s.description ILIKE %s)"
        like = f"%{q}%"
        params += [like, like]
    with connect() as db:
        rows = db.execute(
            f"""
            SELECT s.id, s.title, s.description, s.chapter_count, s.word_count,
                   s.clone_count, s.published_at, s.owner_id,
                   u.display_name AS author, u.username AS author_username
            FROM scripts s JOIN users u ON u.id = s.owner_id
            WHERE {where}
            ORDER BY s.published_at DESC NULLS LAST, s.id DESC
            LIMIT %s OFFSET %s
            """,
            (*params, limit + 1, offset),
        ).fetchall()
        rows = [dict(r) for r in rows]
    has_more = len(rows) > limit
    items = rows[:limit]
    for it in items:
        it["mine"] = (it.pop("owner_id") == user["id"])
    return json_response({"ok": True, "items": items, "has_more": has_more,
                          "limit": limit, "offset": offset})


@router.get("/api/scripts/public/{script_id}")
async def api_public_script_detail(script_id: int, user=Depends(require_user)):
    """公开剧本详情:元信息 + 前若干章标题 + 角色卡/世界书条目数。"""
    with connect() as db:
        row = db.execute(
            """
            SELECT s.id, s.title, s.description, s.chapter_count, s.word_count,
                   s.clone_count, s.published_at, s.content_fingerprint, s.owner_id,
                   u.display_name AS author, u.username AS author_username
            FROM scripts s JOIN users u ON u.id = s.owner_id
            WHERE s.id = %s AND s.is_public
            """,
            (script_id,),
        ).fetchone()
        if not row:
            return json_response({"ok": False, "error": "剧本不存在或未公开"}, status_code=404)
        d = dict(row)
        chapter_titles = db.execute(
            "SELECT title FROM script_chapters WHERE script_id = %s ORDER BY chapter_index LIMIT 12",
            (script_id,),
        ).fetchall()
        card_count = db.execute(
            "SELECT count(*) AS n FROM character_cards WHERE script_id = %s", (script_id,),
        ).fetchone()
        wb_count = db.execute(
            "SELECT count(*) AS n FROM worldbook_entries WHERE script_id = %s", (script_id,),
        ).fetchone()
        fp = d.get("content_fingerprint") or ""
        already = False
        if fp:
            already = bool(db.execute(
                "SELECT 1 FROM scripts WHERE owner_id = %s AND content_fingerprint = %s LIMIT 1",
                (user["id"], fp),
            ).fetchone())
    mine = d.pop("owner_id") == user["id"]
    d.pop("content_fingerprint", None)
    d["mine"] = mine
    d["already_imported"] = already or mine
    d["chapter_titles"] = [r["title"] for r in chapter_titles]
    d["card_count"] = (dict(card_count) or {}).get("n", 0)
    d["worldbook_count"] = (dict(wb_count) or {}).get("n", 0)
    return json_response({"ok": True, "script": d})


@router.post("/api/scripts/public/{script_id}/clone")
async def api_clone_public_script(script_id: int, user=Depends(require_user)):
    """task: 公开剧本「导入」= O(1) subscribe(指针挂载),不再物理复制。

    剧本是 immutable knowledge,只有原 owner 能编辑;普通用户挂载即可,几毫秒 INSERT
    替代原来 30-60s 的全表 clone(scripts + chapters + cards + worldbook + canon +
    timeline_anchors + phase_digests + worldlines + nodes 跨 9 张表)。

    如需「另存为可编辑副本」(真复制),走 /api/scripts/public/{id}/fork。
    """
    with connect() as db:
        # 1. 校验剧本存在 + 公开
        row = db.execute(
            "select id, owner_id, is_public, title from scripts where id = %s",
            (script_id,),
        ).fetchone()
        if not row:
            return json_response({"ok": False, "error": "剧本不存在"}, status_code=404)
        if not row.get("is_public"):
            return json_response({"ok": False, "error": "该剧本未公开,无法导入"}, status_code=403)
        if int(row["owner_id"]) == int(user["id"]):
            return json_response({"ok": False, "error": "这是你自己的剧本,无需订阅"}, status_code=400)
        # 2. O(1) INSERT subscription(主键冲突即已订阅)。RETURNING 1 只在【真正插入】
        #    时返回一行 → 据此判断是否首次订阅,避免重复订阅也把 clone_count +1(指标虚高)。
        inserted = db.execute(
            """
            insert into user_script_subscriptions (user_id, script_id)
            values (%s, %s)
            on conflict (user_id, script_id) do nothing
            returning 1
            """,
            (user["id"], script_id),
        ).fetchone()
        # 3. 热度计数 +1(仅首次订阅)
        if inserted:
            try:
                db.execute("update scripts set clone_count = clone_count + 1 where id = %s", (script_id,))
            except Exception:
                pass
    return json_response({
        "ok": True,
        "script_id": script_id,
        "subscribed": True,
        "title": row.get("title"),
    })


@router.post("/api/scripts/public/{script_id}/fork")
async def api_fork_public_script(script_id: int, user=Depends(require_user)):
    """task: 「另存为可编辑副本」= 旧 clone 行为(全表物理复制)。

    谨慎使用 — 慢(30-60s),会失去与原剧本的同步。
    """
    from platform_app.knowledge.script_pack import clone_public_script
    try:
        result = clone_public_script(script_id, user["id"])
    except PermissionError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=403)
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)
    return json_response({"ok": True, **result})


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


@router.get("/api/scripts/{script_id}/gm-style")
async def api_get_script_gm_style(script_id: int, user=Depends(require_user)):
    """读剧本级 GM 叙事风格(owner 可读;用默认补全未设旋钮)。"""
    with connect() as db:
        owned = db.execute(
            "SELECT 1 FROM scripts WHERE id = %s AND owner_id = %s", (script_id, user["id"])
        ).fetchone()
    if not owned:
        return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
    from agents.gm.style_harness import normalize_profile
    from platform_app.knowledge.script_overrides import get_overrides_by_script_id
    data = get_overrides_by_script_id(script_id) or {}
    stored = data.get("gm_style") if isinstance(data.get("gm_style"), dict) else {}
    return json_response({"ok": True, "gm_style": normalize_profile(stored), "stored": stored})


@router.post("/api/scripts/{script_id}/gm-style")
async def api_set_script_gm_style(request: Request, script_id: int, user=Depends(require_user)):
    """写剧本级 GM 叙事风格(仅 owner)。Body: {"gm_style": {旋钮: 0-100}}。
    只 merge 进 data.gm_style,不动其它 override 字段。"""
    with connect() as db:
        owned = db.execute(
            "SELECT 1 FROM scripts WHERE id = %s AND owner_id = %s", (script_id, user["id"])
        ).fetchone()
    if not owned:
        return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
    from agents.gm.style_harness import validate_patch
    from platform_app.knowledge.script_overrides import get_overrides_by_script_id, upsert_overrides
    body = await request.json()
    try:
        clean = validate_patch(body.get("gm_style") if "gm_style" in body else body)
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)
    data = dict(get_overrides_by_script_id(script_id) or {})
    cur = dict(data.get("gm_style") if isinstance(data.get("gm_style"), dict) else {})
    cur.update(clean)
    data["gm_style"] = cur
    upsert_overrides(script_id, data)
    return json_response({"ok": True, "gm_style": cur})


# ── Phase E: 可视化复核(只读图 + god 编辑)─────────────────────────────────
def _owned_script(db, script_id: int, user_id: int):
    return db.execute(
        "select id, title, import_report, review_status, reviewed_at "
        "from scripts where id=%s and owner_id=%s",
        (script_id, user_id),
    ).fetchone()


@router.get("/api/scripts/{script_id}/graph")
async def api_script_graph(script_id: int, user=Depends(require_user)):
    """Phase E.1 复核图:规范实体 + 世界线 DAG + 时间线 + 摄入质量 flag。"""
    with connect() as db:
        s = _owned_script(db, script_id, user["id"])
        if not s:
            return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
        entities = db.execute(
            "select id, logical_key, name, type, aliases, summary, importance, "
            "first_revealed_chapter, public_knowledge from kb_canon_entities "
            "where script_id=%s order by importance desc, logical_key limit 1000",
            (script_id,),
        ).fetchall()
        worldlines = db.execute(
            "select wl_key, label, parent_wl, branch_at_node, is_primary, source "
            "from script_worldlines where script_id=%s order by is_primary desc, wl_key",
            (script_id,),
        ).fetchall()
        nodes = db.execute(
            "select wl_key, node_key, seq, label, summary, chapter_min, chapter_max, "
            "anchor_keys, must_preserve, may_vary from script_worldline_nodes "
            "where script_id=%s order by wl_key, seq",
            (script_id,),
        ).fetchall()
        timeline = db.execute(
            "select story_time_label, chapter_min, chapter_max from script_timeline_anchors "
            "where script_id=%s order by chapter_min limit 500",
            (script_id,),
        ).fetchall()
        report = s.get("import_report") or {}
        review_flags = {
            "needs_review": report.get("needs_review"),
            "author_notes": report.get("author_notes", []),
            "weird_titles": report.get("weird_titles", []),
            "gaps": report.get("gaps", []),
            "cleaning": report.get("cleaning", {}),
        }
    return json_response({
        "ok": True, "script": {
            "id": script_id, "title": s["title"],
            "review_status": s.get("review_status") or "unreviewed",
            "reviewed_at": s.get("reviewed_at"),
        },
        "entities": [dict(e) for e in entities],
        "worldlines": [dict(w) for w in worldlines],
        "nodes": [dict(n) for n in nodes],
        "timeline": [dict(t) for t in timeline],
        "review_flags": review_flags,
    })


@router.patch("/api/scripts/{script_id}/canon")
async def api_patch_canon(request: Request, script_id: int, user=Depends(require_user)):
    """Phase E god 编辑(仅 owner)。

    Body 之一:
      {"op": "update_entity", "logical_key": "...", "summary": "...", "aliases": [...], "importance": N}
      {"op": "merge_entity", "from_key": "...", "into_key": "..."}  # from 的别名并入 into,删 from
      {"op": "delete_entity", "logical_key": "..."}
    """
    with connect() as db:
        if not _owned_script(db, script_id, user["id"]):
            return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
        try:
            body = await request.json()
        except Exception:
            return json_response({"ok": False, "error": "body 必须是合法 JSON"}, status_code=400)
        op = (body.get("op") or "").strip()
        if op == "update_entity":
            lk = (body.get("logical_key") or "").strip()
            if not lk:
                return json_response({"ok": False, "error": "缺 logical_key"}, status_code=400)
            sets, args = [], []
            for col in ("summary",):
                if col in body:
                    sets.append(f"{col}=%s")
                    args.append(str(body[col]))
            if "importance" in body:
                sets.append("importance=%s")
                args.append(int(body["importance"]))
            if "aliases" in body and isinstance(body["aliases"], list):
                from psycopg.types.json import Jsonb
                sets.append("aliases=%s")
                args.append(Jsonb(body["aliases"]))
            if not sets:
                return json_response({"ok": False, "error": "无可更新字段"}, status_code=400)
            args.extend([script_id, lk])
            n = db.execute(
                f"update kb_canon_entities set {', '.join(sets)} where script_id=%s and logical_key=%s",
                tuple(args),
            ).rowcount
            return json_response({"ok": True, "updated": n})
        if op == "merge_entity":
            frm = (body.get("from_key") or "").strip()
            into = (body.get("into_key") or "").strip()
            if not frm or not into:
                return json_response({"ok": False, "error": "缺 from_key/into_key"}, status_code=400)
            src = db.execute("select name, aliases from kb_canon_entities where script_id=%s and logical_key=%s", (script_id, frm)).fetchone()
            if src:
                from psycopg.types.json import Jsonb
                merged_aliases = list({*(src.get("aliases") or []), src["name"]})
                db.execute(
                    "update kb_canon_entities set aliases = (select to_jsonb(array(select distinct e from unnest("
                    "  array(select jsonb_array_elements_text(coalesce(aliases,'[]'::jsonb))) || %s::text[]) e))) "
                    "where script_id=%s and logical_key=%s",
                    (merged_aliases, script_id, into),
                )
                db.execute("delete from kb_canon_entities where script_id=%s and logical_key=%s", (script_id, frm))
            return json_response({"ok": True, "merged": bool(src)})
        if op == "delete_entity":
            lk = (body.get("logical_key") or "").strip()
            n = db.execute("delete from kb_canon_entities where script_id=%s and logical_key=%s", (script_id, lk)).rowcount
            return json_response({"ok": True, "deleted": n})
        return json_response({"ok": False, "error": f"未知 op: {op}"}, status_code=400)


@router.post("/api/scripts/{script_id}/mark-reviewed")
async def api_script_mark_reviewed(script_id: int, user=Depends(require_user)):
    """Phase E.1 复核状态机:owner 复核完点这个,scripts.review_status='reviewed'。

    解锁开局闸——之后建档接口才会接受这本剧本。重切(resplit)会 reset 回 unreviewed。
    """
    with connect() as db:
        if not _owned_script(db, script_id, user["id"]):
            return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
        db.execute(
            "update scripts set review_status='reviewed', reviewed_at=now(), updated_at=now() "
            "where id=%s",
            (script_id,),
        )
    return json_response({"ok": True, "review_status": "reviewed"})


@router.post("/api/scripts/{script_id}/unmark-reviewed")
async def api_script_unmark_reviewed(script_id: int, user=Depends(require_user)):
    """owner 重新打开复核(回 unreviewed)。"""
    with connect() as db:
        if not _owned_script(db, script_id, user["id"]):
            return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
        db.execute(
            "update scripts set review_status='unreviewed', reviewed_at=null, updated_at=now() "
            "where id=%s",
            (script_id,),
        )
    return json_response({"ok": True, "review_status": "unreviewed"})
