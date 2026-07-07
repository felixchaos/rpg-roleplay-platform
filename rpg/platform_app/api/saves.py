"""platform_app.api.saves — /api/saves*, /api/branches/* 路由。"""
from __future__ import annotations

import json as _json
import logging
from typing import Any
from urllib.parse import quote as _quote

log = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response

from .. import branches, knowledge, workspace
from ..db import connect
from ..perms import owns_save, script_readable
from ._deps import json_response, require_user

router = APIRouter()


_MAX_SAVE_IMPORT_BYTES = 16 * 1024 * 1024  # 16MB 上限,防内存炸


@router.get("/api/saves")
async def api_saves(limit: int | None = None, cursor: str | None = None, user=Depends(require_user)):
    """轻量列表：只返摘要字段（turn/player_name/world_time/history_count），不含 state_snapshot。"""
    return json_response({"ok": True, **workspace.saves_page(user["id"], limit, cursor)})


@router.get("/api/saves/{save_id}/export")
async def api_save_export(save_id: int, user=Depends(require_user)):
    """task 69: 下载存档 JSON 文件 (Content-Disposition: attachment)。

    之前返 application/json body 浏览器只渲染不下载,window.open 拿到的是网页 →
    用户无法把它再喂回 import。改 Response + attachment header。
    """
    from .. import save_io
    try:
        payload = save_io.export_save(user["id"], save_id)
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=403)
    body = _json.dumps({"ok": True, **payload}, ensure_ascii=False).encode("utf-8")
    # 文件名 — 用 save title 或兜底 save-{id}
    save_title = (payload.get("save") or {}).get("title") or f"save-{save_id}"
    safe_name = f"save-{save_id}-{save_title}.json"
    ascii_fallback = safe_name.encode("ascii", "ignore").decode("ascii") or f"save-{save_id}.json"
    quoted = _quote(safe_name, safe="")
    cd = f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quoted}"
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": cd, "X-Content-Type-Options": "nosniff"},
    )


@router.get("/api/saves/{save_id}/export/txt")
async def api_save_export_txt(save_id: int, user=Depends(require_user)):
    """下载存档为人类可读 .txt(游戏 / 酒馆通用,二者皆 game_saves)——群反馈「想当小说给别人看」。

    剥掉 ops JSON / 工具脚手架,玩家输入 + GM 正文交替成文。媒体类型 text/plain + attachment。
    """
    from .. import save_io
    try:
        filename, text = save_io.export_transcript_txt(user["id"], save_id)
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=403)
    ascii_fallback = filename.encode("ascii", "ignore").decode("ascii") or f"save-{save_id}.txt"
    quoted = _quote(filename, safe="")
    cd = f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quoted}"
    return Response(
        content=text.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": cd, "X-Content-Type-Options": "nosniff"},
    )


@router.get("/api/saves/{save_id}/export/estimate")
async def api_save_export_estimate(save_id: int, user=Depends(require_user)):
    """即时算各档自包含导出包大小(前端导出弹窗按所选存档实时显示,不静态预估)。"""
    from .. import save_bundle
    try:
        return json_response(save_bundle.estimate_bundle_sizes(user["id"], save_id))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=403)


@router.get("/api/saves/{save_id}/export/bundle")
async def api_save_export_bundle(save_id: int, tier: str = "no_vectors", user=Depends(require_user)):
    """自包含存档导出(zip:剧本+知识库[+向量]+per-save)。tier=full|no_vectors。**仅限自有剧本**。"""
    from .. import save_bundle
    try:
        zip_bytes, filename = save_bundle.export_save_bundle(user["id"], save_id, tier=tier)
    except PermissionError:
        return json_response({"ok": False, "error": "只能完整导出自己拥有的剧本(订阅的公开剧本不可打包,版权)"}, status_code=403)
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=403)
    ascii_fallback = filename.encode("ascii", "ignore").decode("ascii") or f"save-bundle-{save_id}.zip"
    quoted = _quote(filename, safe="")
    cd = f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quoted}"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": cd, "X-Content-Type-Options": "nosniff"},
    )


@router.post("/api/saves/import")
async def api_save_import(request: Request, user=Depends(require_user)):
    """task 69: 上传存档 JSON 文件恢复成新存档。

    支持两种协议(前端目前用 multipart/form-data,旧脚本可能直接发 JSON):
    - multipart/form-data 字段 file=<.json>  ← 前端 saves.jsx 走这条
    - application/json body { payload: {...} } 或直接是 export payload
    """
    from .. import save_io
    content_type = request.headers.get("content-type", "")
    payload: dict[str, Any]
    try:
        if "multipart/form-data" in content_type:
            form = await request.form()
            file = form.get("file")
            if not file or not hasattr(file, "read"):
                raise HTTPException(status_code=400, detail="缺 file 字段")
            raw = await file.read()
            # 自包含存档包(zip:剧本+知识库[+向量]+per-save) → bundle 导入路径
            # (大小由 import_script_pack 的 MAX_ZIP_BYTES / 解压炸弹预检把关)
            if raw[:4] == b"PK\x03\x04":
                from .. import save_bundle
                return json_response(save_bundle.import_save_bundle(user["id"], raw))
            if len(raw) > _MAX_SAVE_IMPORT_BYTES:
                raise HTTPException(status_code=400, detail=f"文件过大 (>{_MAX_SAVE_IMPORT_BYTES // 1024 // 1024}MB)")
            try:
                payload = _json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, _json.JSONDecodeError) as exc:
                raise HTTPException(status_code=400, detail=f"JSON 解析失败: {exc}") from exc
        else:
            # JSON body 路径也要受大小约束(multipart 已有 _MAX_SAVE_IMPORT_BYTES 关,
            # 这条原来直接 request.json() 无上限 → 超大 body 撑内存)。先按 raw 长度卡再解析。
            raw_body = await request.body()
            if len(raw_body) > _MAX_SAVE_IMPORT_BYTES:
                raise HTTPException(status_code=400, detail=f"文件过大 (>{_MAX_SAVE_IMPORT_BYTES // 1024 // 1024}MB)")
            try:
                body = _json.loads(raw_body.decode("utf-8"))
            except (UnicodeDecodeError, _json.JSONDecodeError) as exc:
                raise HTTPException(status_code=400, detail=f"JSON 解析失败: {exc}") from exc
            payload = body.get("payload") if isinstance(body, dict) and isinstance(body.get("payload"), dict) else body
        if not isinstance(payload, dict):
            return json_response({"ok": False, "error": "payload 必须是对象"}, status_code=400)
        return json_response(save_io.import_save(user["id"], payload))
    except HTTPException:
        raise
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/saves/{save_id}")
async def api_save_detail(save_id: int, user=Depends(require_user)):
    """单条详情：包含完整 state_snapshot。"""
    try:
        return json_response({"ok": True, "save": workspace.save_detail(user["id"], save_id)})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=403)


@router.post("/api/saves")
async def api_create_save(request: Request, user=Depends(require_user)):
    body = await request.json()
    raw_script_id = body.get("script_id")
    if raw_script_id is None:
        return json_response({"ok": False, "error": "script_id 必填"}, status_code=400)
    try:
        script_id = int(raw_script_id)
    except (TypeError, ValueError):
        return json_response({"ok": False, "error": "script_id 必须为整数"}, status_code=400)
    # 校验 script 归属(task 74: 接受 owner OR subscriber)— 读级:owner ∪ subscription
    with connect() as db:
        if not script_readable(db, script_id, user["id"]):
            return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
    # 前置校验:新存档实际会用哪个 api_id(与 app.py._ensure_loaded 同一优先级链:
    # gm.* 用户偏好 → 该用户已配凭据的第一个模型 → 全局 catalog 默认),若解析到
    # vertex_ai 且该用户在生产鉴权模式下没上传 SA → 现在就拒,别等玩家写完人设、
    # 发第一条消息才在 GM 构造时炸「未找到 Service Account」。解析不出(极端情况)
    # 时放行,不误伤。
    try:
        _uid_new_save = int(user["id"])
        from core.llm_backend import first_user_model, resolve_preferred_api
        _resolved_api_id = resolve_preferred_api(_uid_new_save, "gm.api_id")
        if not _resolved_api_id:
            _user_default = first_user_model(_uid_new_save)
            if _user_default:
                _resolved_api_id = _user_default[0]
        if not _resolved_api_id:
            from app import selected_model
            _resolved_api_id = (selected_model() or {}).get("api_id")
        if _resolved_api_id:
            from model_registry import normalize_api_id
            if normalize_api_id(_resolved_api_id) == "vertex_ai":
                from core.vertex_sa import vertex_selection_blocked
                _block_reason = vertex_selection_blocked(_uid_new_save)
                if _block_reason:
                    return json_response(
                        {"ok": False, "error": _block_reason, "needs_model_config": True},
                        status_code=400,
                    )
    except Exception:
        log.warning("[api_create_save] vertex 前置校验异常,放行", exc_info=True)
    # task 29：把 UI 填的 new_card / character 传到 create_save，让初始 state_snapshot
    # 真的反映用户输入的姓名/身份/设定，否则 NewGameModal 的角色卡字段就被丢了。
    new_card = body.get("new_card") if isinstance(body.get("new_card"), dict) else None
    character: dict[str, Any] | None = None
    cid = body.get("character_id")
    ckind = body.get("character_kind")
    if cid is not None and ckind:
        character = {"id": cid, "kind": str(ckind)}
    birthpoint = body.get("birthpoint") if isinstance(body.get("birthpoint"), dict) else None
    identity = body.get("identity") if isinstance(body.get("identity"), dict) else None
    story_intent = str(body.get("story_intent") or "").strip() or None
    # player_origin: 显式从 body 取(独立字段),回落 identity.player_origin(身份卡里冗余字段)。
    # 默认 None — 老存档 / 老 wizard 不传时不写,前端徽章按 falsy 处理。
    player_origin = body.get("player_origin")
    if not player_origin and isinstance(identity, dict):
        player_origin = identity.get("player_origin")
    player_origin = str(player_origin or "").lower() or None
    # 4 档新模型(soul/body/dual/native) + 旧值兼容(isekai→soul);非法值丢弃。
    if player_origin == "isekai":
        player_origin = "soul"
    if player_origin and player_origin not in ("soul", "body", "dual", "native"):
        player_origin = None
    # identity_known: 开局是否知道身份卡(知道/不知道)。没有挂身份卡时没有可感知对象,忽略。
    _ik = body.get("identity_known")
    if _ik is None and isinstance(identity, dict):
        _ik = identity.get("identity_known")
    identity_known = _ik if isinstance(_ik, bool) else None
    if player_origin == "body" or not identity:
        identity_known = None
    try:
        save = workspace.create_save(
            user["id"], script_id, body.get("title", ""),
            new_card=new_card, character=character,
            birthpoint=birthpoint, identity=identity, story_intent=story_intent,
            player_origin=player_origin, identity_known=identity_known,
        )
    except ValueError as exc:
        # 复核闸/权限校验等业务级错误 → 400(带 review_status,前端能引导用户去复核页)
        msg = str(exc)
        out = {"ok": False, "error": msg}
        if "复核" in msg:
            out["needs_review"] = True
            out["script_id"] = script_id
        return json_response(out, status_code=400)
    return json_response({"ok": True, "save": save})


@router.get("/api/branches/{save_id}")
async def api_branches(save_id: int, limit: int | None = None, cursor: str | None = None, user=Depends(require_user)):
    # 先校验存档归属，避免 tree() 内部抛 raw exception
    with connect() as db:
        ok = owns_save(db, save_id, user["id"])
    if not ok:
        return json_response({"ok": False, "error": "无权访问该存档"}, status_code=403)
    return json_response(branches.tree(user["id"], save_id, limit, cursor))


@router.post("/api/branches/continue")
async def api_continue_branch(request: Request, user=Depends(require_user)):
    """task 38：接受两种 body 形态：
       A) {node_id: <int>}              —— 老路径，前端拿得到 commit id 时直接传
       B) {save_id, message_index, ...} —— Game Console 「从这里新建分支」用，
          后端把 message_index → turn_index → commit_id。
       缺字段或解析失败一律 400（不再因 int(None) 抛 TypeError 成 500）。"""
    body = await request.json() if (await request.body()) else {}
    node_id_raw = body.get("node_id")
    save_id_raw = body.get("save_id")
    msg_idx_raw = body.get("message_index")

    node_id: int | None = None
    if node_id_raw is not None and str(node_id_raw) != "":
        try:
            node_id = int(node_id_raw)
        except (TypeError, ValueError):
            return json_response({"ok": False, "error": "node_id 不是整数"}, status_code=400)

    if node_id is None and save_id_raw is not None and msg_idx_raw is not None:
        try:
            save_id = int(save_id_raw)
            message_index = int(msg_idx_raw)
        except (TypeError, ValueError):
            return json_response({"ok": False, "error": "save_id/message_index 不是整数"}, status_code=400)
        node_id = branches.resolve_commit_id_by_message(user["id"], save_id, message_index)
        if node_id is None:
            return json_response(
                {"ok": False, "error": f"无法在 save={save_id} 找到 message_index={message_index} 对应的提交"},
                status_code=400,
            )

    if node_id is None:
        return json_response(
            {"ok": False, "error": "缺字段：需要 node_id 或 (save_id + message_index)"},
            status_code=400,
        )
    try:
        result = branches.continue_from(user["id"], node_id)
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)
    # 同 activate:fork 后必须清缓存,否则 Game Console /api/state 仍读旧 runtime
    try:
        import app as _ui
        _ui._invalidate_user_cache(user)
    except Exception:
        pass
    return json_response(result)


@router.post("/api/branches/activate")
async def api_activate_branch(request: Request, user=Depends(require_user)):
    body = await request.json()
    try:
        nid = body.get("node_id")
        if nid is None:
            return json_response({"ok": False, "error": "node_id 必填"}, status_code=400)
        result = branches.activate_node(user["id"], int(nid))
    except (TypeError, ValueError) as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)
    # commit 级 activate 后必须清 app.py 进程内 state 缓存。
    # 之前 _ensure_loaded 自检只比较 save_id,同 save 内换 commit 缓存不会失效
    # → 用户在 ContinuePicker 选 #13 节点继续,进 Game Console 看到的还是上次
    # 末尾 commit 的 runtime(可能是另一个剧情的内容)。
    try:
        import app as _ui
        _ui._invalidate_user_cache(user)
    except Exception:
        pass
    return json_response(result)


@router.post("/api/branches/delete")
async def api_delete_branch(request: Request, user=Depends(require_user)):
    body = await request.json()
    nid = body.get("node_id")
    if nid is None:
        return json_response({"ok": False, "error": "node_id 必填"}, status_code=400)
    try:
        return json_response(branches.delete_subtree(user["id"], int(nid)))
    except (TypeError, ValueError) as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/branches/rollback")
async def api_rollback_to_message(request: Request, user=Depends(require_user)):
    """task 116c — 删除消息 N 及之后所有 (git-style 软回滚)。

    入参: { save_id, message_index }
    出参: { ok, restored_turn, dropped_turn_count, deleted: {...}, trash_ref, runtime }
    """
    body = await request.json()
    try:
        save_id = int(body.get("save_id"))
        message_index = int(body.get("message_index"))
    except (TypeError, ValueError):
        return json_response(
            {"ok": False, "error": "save_id 和 message_index 都必须是整数"},
            status_code=400,
        )
    try:
        result = branches.rollback_to_message(user["id"], save_id, message_index)
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)
    # 同 activate:回滚 commit 后必须清 app.py 进程内 state 缓存
    try:
        import app as _ui
        _ui._invalidate_user_cache(user)
    except Exception:
        pass
    return json_response(result)


@router.get("/api/saves/{save_id}/context-runs")
async def api_save_context_runs(save_id: int, limit: int | None = None, cursor: str | None = None, user=Depends(require_user)):
    try:
        return json_response({"ok": True, **knowledge.list_context_runs(user["id"], save_id, limit, cursor)})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/saves/{save_id}/anchors")
async def api_save_anchors(save_id: int, user=Depends(require_user)):
    """task 136h: 世界线收束 — 存档锚点状态.

    返回:
      {
        ok: true,
        summary: {pending, occurred, variant, superseded, fatal_pending, avg_drift, total},
        by_phase: [{phase_label, pending, occurred, variant, ..., avg_drift, convergence_pressure}, ...],
        recent_pending: [...up to 12 most important pending anchors...],
        recent_occurred: [...up to 8 most recently occurred...]
      }
    """
    with connect() as db:
        if not owns_save(db, save_id, user["id"]):
            return json_response({"ok": False, "error": "无权访问该存档"}, status_code=403)
    try:
        from agents.anchor_seed_agent import (
            drift_by_phase,
            get_progress_window,
            list_pending_for_phase,
            summarize_save_anchor_state,
        )
        summary = summarize_save_anchor_state(save_id)
        by_phase = drift_by_phase(save_id)
        # M9(进度信号矩阵审计):此前不带章节窗口过滤,按 importance 全局取 top-12 —
        # 会把远超玩家当前进度的中后期锚点摘要(含 summary 剧透正文)直接推给前端。
        # 用权威读取器 get_progress_window 的 [chapter_min, chapter_max] 过滤,窗口外
        # 的待发生锚点不返回(与 steering/retrieval 对「玩家能看到多远」的口径一致)。
        try:
            _win = get_progress_window(save_id)
            _ch_min, _ch_max = _win.get("chapter_min"), _win.get("chapter_max")
        except Exception:
            _ch_min, _ch_max = None, None
        recent_pending = list_pending_for_phase(
            save_id, None, limit=12, chapter_min=_ch_min, chapter_max=_ch_max,
        )
        with connect() as db:
            occ_rows = db.execute(
                """
                select anchor_key, source_chapter, summary, phase_label,
                       status, variant_description, occurred_at_turn,
                       drift_score, is_fatal, updated_at
                from save_anchor_states
                where save_id = %s and status in ('occurred', 'variant')
                order by occurred_at_turn desc nulls last, updated_at desc
                limit 8
                """,
                (save_id,),
            ).fetchall() or []
        recent_occurred = [
            {
                "anchor_key": r["anchor_key"],
                "chapter": r["source_chapter"],
                "summary": r["summary"],
                "phase_label": r.get("phase_label") or "",
                "status": r["status"],
                "how_it_happened": r.get("variant_description") or "",
                "occurred_at_turn": r.get("occurred_at_turn"),
                "drift_score": float(r.get("drift_score") or 0),
                "is_fatal": bool(r.get("is_fatal")),
            }
            for r in occ_rows
        ]
        return json_response({
            "ok": True,
            "save_id": save_id,
            "summary": summary,
            "by_phase": by_phase,
            "recent_pending": recent_pending,
            "recent_occurred": recent_occurred,
        })
    except Exception:
        log.exception("save anchors error")
        return json_response(
            {"ok": False, "error": "服务内部错误，请稍后重试"},
            status_code=500,
        )


@router.post("/api/saves/{save_id}/anchors/reseed")
async def api_save_anchors_reseed(request: Request, save_id: int, user=Depends(require_user)):
    """task 136h: 强制重 seed 锚点 (调试用)。
    body 可选: {"keep_satisfied": true|false} 默认 true (保留已发生)。
    """
    with connect() as db:
        if not owns_save(db, save_id, user["id"]):
            return json_response({"ok": False, "error": "无权访问该存档"}, status_code=403)
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    keep = bool(body.get("keep_satisfied", True))
    try:
        from agents.anchor_seed_agent import reseed_anchors_for_save
        res = reseed_anchors_for_save(save_id, keep_satisfied=keep)
        return json_response({"ok": True, **res})
    except Exception:
        log.exception("save anchors reseed error")
        return json_response(
            {"ok": False, "error": "服务内部错误，请稍后重试"},
            status_code=500,
        )


@router.post("/api/saves/{save_id}/anchors/{anchor_key}/satisfy")
async def api_save_anchor_satisfy(save_id: int, anchor_key: str, user=Depends(require_user)):
    """玩家确定性推进:把一个【当前 pending 窗口内、非 fatal】的原著锚点标记为已到达。

    回答用户「世界线锚点卡在当前推不动 / 怎么推进」的治本路径 —— 不再只能靠 GM 调 LLM 工具。
    复用 command_tools_anchors._t_mark_anchor_satisfied 的同一写逻辑(置 occurred +
    advance_progress 到 source_chapter,取 max 只增不减),但加玩家级护栏:

      · owns_save 鉴权 + _get_sync_scope_lock((user,save)) 同锁防并发互覆盖
      · 仅允许 status='pending' 的锚点(已 occurred/variant/superseded → 明确提示)
      · 拒绝 is_fatal(关键命运/死亡锚点须在剧情中由 GM 触发)
      · 防剧透:锚点必须落在【该存档当前进度窗口】内(get_progress_window),
        不允许玩家跳到远未来锚点提前剧透
    """
    user_id = int(user["id"])
    anchor_key = (anchor_key or "").strip()
    if not anchor_key:
        return json_response({"ok": False, "error": "anchor_key 必填"}, status_code=400)

    from tools_dsl.command_dispatcher import _get_sync_scope_lock
    from gm_serving.settings import advance_progress

    try:
        # SEC: 整 read-modify-write 在 (user,save) 锁内,与 command_tools_anchors 同锁。
        with _get_sync_scope_lock((user_id, save_id)), connect() as db:
            if not owns_save(db, save_id, user_id):
                return json_response({"ok": False, "error": "无权访问该存档"}, status_code=403)
            row = db.execute(
                """
                select id, status, is_fatal, summary, source_chapter
                  from save_anchor_states
                 where save_id = %s and anchor_key = %s
                """,
                (save_id, anchor_key),
            ).fetchone()
            if not row:
                return json_response(
                    {"ok": False, "error": "找不到该锚点"}, status_code=404)
            if row.get("status") in ("occurred", "variant"):
                return json_response(
                    {"ok": False, "error": "该锚点已经发生过了,无需重复标记。"},
                    status_code=409)
            if row.get("status") == "superseded":
                return json_response(
                    {"ok": False, "error": "该锚点已被剧情绕过,不能再标记为已到达。"},
                    status_code=409)
            if row.get("is_fatal"):
                return json_response(
                    {"ok": False, "error": "这是关键命运锚点,须在剧情中触发,不能手动标记。"},
                    status_code=409)

            # 防剧透:锚点必须落在当前进度窗口 [chapter_min, chapter_max] 内。
            # get_progress_window 单独连库(已 init_db),与本事务读的是同一权威表,
            # 在锁内调用安全(它只读不写)。
            from agents.anchor_seed_agent import get_progress_window
            win = get_progress_window(save_id, script_id=None)
            src_ch = row.get("source_chapter")
            win_min = win.get("chapter_min")
            win_max = win.get("chapter_max")
            if isinstance(src_ch, int) and isinstance(win_max, int) and src_ch > win_max:
                return json_response(
                    {"ok": False,
                     "error": f"该锚点在第 {src_ch} 章,超出当前进度窗口,暂不能提前标记(防剧透)。"},
                    status_code=409)
            # 对称下界:早于当前进度窗口起点的锚点不应手动标记(原读了 win_min 却从未使用 → 漏判)。
            if isinstance(src_ch, int) and isinstance(win_min, int) and src_ch < win_min:
                return json_response(
                    {"ok": False,
                     "error": f"该锚点在第 {src_ch} 章,早于当前进度窗口起点,无法手动标记。"},
                    status_code=409)

            # 当前最大 turn(与 _t_mark_anchor_satisfied 一致默认)
            tr = db.execute(
                "select coalesce(max(turn_index), 0) as t from branch_commits where save_id = %s",
                (save_id,),
            ).fetchone()
            occurred_turn = int((tr or {}).get("t") or 0)

            # 玩家手动「标记已到达」语义 = 按原著方式发生(drift=0 → status='occurred')。
            # 复用 _t_mark_anchor_satisfied 的同款 UPDATE。
            db.execute(
                """
                update save_anchor_states set
                  status = 'occurred',
                  variant_description = %s,
                  occurred_at_turn = %s,
                  drift_score = 0.0,
                  updated_at = now()
                where save_id = %s and id = %s
                """,
                ("玩家在世界线面板手动标记已到达", occurred_turn, save_id, row["id"]),
            )
            # 同步玩家进度到该锚点章节(advance_progress 取 max 只增不减,幂等)。
            if isinstance(src_ch, int) and src_ch >= 1:
                try:
                    advance_progress(db, save_id, src_ch)
                except Exception:
                    pass  # 进度同步失败不阻断锚点标记
        return json_response({
            "ok": True,
            "anchor_id": row["id"],
            "anchor_key": anchor_key,
            "new_status": "occurred",
            "occurred_at_turn": occurred_turn,
            "advanced_to_chapter": src_ch if isinstance(src_ch, int) and src_ch >= 1 else None,
        })
    except Exception:
        log.exception("save anchor satisfy error")
        return json_response(
            {"ok": False, "error": "服务内部错误，请稍后重试"}, status_code=500)


@router.post("/api/saves/{save_id}/progress/rewind")
async def api_save_progress_rewind(save_id: int, request: Request, user=Depends(require_user)):
    """玩家回到之前的世界线节点:把 progress_chapter 显式设回 target_chapter(【可降】,
    区别于 advance_progress 的 max-only),并把 source_chapter > target 的已发生锚点重置回
    pending(重新上锁,时间线节点重新变「待解锁」)。用于纠正进度过推 / 主动回溯到更早节点。
    配合 retrieval BUG-3 修复(进度只按已确认锚点推进)后,回退能稳住不被下一回合再推回去。"""
    user_id = int(user["id"])
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        target = int(body.get("target_chapter"))
    except (TypeError, ValueError):
        return json_response({"ok": False, "error": "target_chapter 必须为整数"}, status_code=400)
    if target < 1:
        target = 1
    from tools_dsl.command_dispatcher import _get_sync_scope_lock
    from psycopg.types.json import Jsonb
    try:
        with _get_sync_scope_lock((user_id, save_id)), connect() as db:
            if not owns_save(db, save_id, user_id):
                return json_response({"ok": False, "error": "无权访问该存档"}, status_code=403)
            # 上界 clamp:不允许回退目标超过剧本真实章数
            _cc_row = db.execute(
                "SELECT s.chapter_count FROM game_saves gs JOIN scripts s ON s.id = gs.script_id "
                "WHERE gs.id = %s",
                (save_id,),
            ).fetchone()
            if _cc_row and _cc_row.get("chapter_count"):
                target = min(target, int(_cc_row["chapter_count"]))
            # 矩阵审计 M3 收口:进度信号族统一走 realign_progress_signals
            # (progress 可降 + user_progress_floor clamp[旧漏:曾 /set 抬高的地板会卡住
            # 揭示天花板不随回退收窄] + 锚点重锁 + frontier 收缩,同事务原子、异常整体
            # 回滚 —— 沿用原 P4(S7.3)不吞异常口径)。
            from gm_serving.settings import realign_progress_signals
            n_relocked = realign_progress_signals(db, save_id, target)
            # M4:同步写穿工作树快照的时间线(信号B)。rail/guided 原文选章优先读
            # state.world.timeline.chapter_min —— 不写穿则回退后 GM 每轮仍被喂旧章
            # 原文,需再手动 /set 一次才纠正。重算 snapshot_hash(跨 worker 缓存失效,
            # 同 snapshot_hash 漂移范式);工作树是回合加载真相源,commit 历史不动。
            _rc_row = db.execute(
                "select state_snapshot from runtime_checkouts where save_id=%s",
                (save_id,)).fetchone()
            _snap = (_rc_row or {}).get("state_snapshot")
            if isinstance(_snap, dict):
                _tl = _snap.setdefault("world", {}).setdefault("timeline", {})
                _tl["chapter_min"] = target
                _tl["chapter_max"] = target
                _tl["anchor_chapter"] = target
                from platform_app.branches.commits import _state_snapshot_hash
                db.execute(
                    "update runtime_checkouts set state_snapshot=%s, snapshot_hash=%s, "
                    "row_version = row_version + 1, updated_at=now() where save_id=%s",
                    (Jsonb(_snap), _state_snapshot_hash(_snap), save_id))
        return json_response({"ok": True, "progress_chapter": target,
                              "relocked": n_relocked})
    except Exception:
        log.exception("save progress rewind error")
        return json_response(
            {"ok": False, "error": "服务内部错误，请稍后重试"}, status_code=500)


# ── Phase F/W6: 创建引导 + 游戏内设置(读 schema/设置,写 apply 锁死 enforcement)──
@router.get("/api/saves/{save_id}/settings")
async def api_save_settings_get(save_id: int, user=Depends(require_user)):
    """读当前存档设置 + 字段 schema(前端向导/设置面板用)。"""
    from gm_serving import settings as _set
    with connect() as db:
        if not owns_save(db, save_id, user["id"]):
            return json_response({"ok": False, "error": "无权访问该存档"}, status_code=403)
        current = _set.read_settings(db, save_id)
    return json_response({"ok": True, "settings": current, "schema": _set.schema()})


@router.patch("/api/saves/{save_id}/settings")
async def api_save_settings_patch(request: Request, save_id: int, user=Depends(require_user)):
    """写设置(apply_settings:建档可设锁死项,游戏中锁死项拒改+非法值拒)。

    Body: {"updates": {...}, "is_create": bool}
    """
    from gm_serving import settings as _set
    try:
        body = await request.json()
    except Exception:
        return json_response({"ok": False, "error": "body 必须是合法 JSON"}, status_code=400)
    with connect() as db:
        if not owns_save(db, save_id, user["id"]):
            return json_response({"ok": False, "error": "无权访问该存档"}, status_code=403)
        updates = body.get("updates") if isinstance(body.get("updates"), dict) else {}
        res = _set.apply_settings(db, save_id, updates, is_create=bool(body.get("is_create")))
    return json_response({"ok": True, **res})
