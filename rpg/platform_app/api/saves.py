"""platform_app.api.saves — /api/saves*, /api/branches/* 路由。"""
from __future__ import annotations

import json as _json
from typing import Any
from urllib.parse import quote as _quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from .. import branches, knowledge, workspace
from ..db import connect
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
    # 校验 script 归属(task 74: 接受 owner OR subscriber)
    with connect() as db:
        owned = db.execute(
            """
            select 1 from scripts s
            where s.id = %s and (
              s.owner_id = %s
              or s.id in (select script_id from user_script_subscriptions where user_id = %s)
            )
            """,
            (script_id, user["id"], user["id"]),
        ).fetchone()
    if not owned:
        return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
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
        owned = db.execute("select 1 from game_saves where id = %s and user_id = %s", (save_id, user["id"])).fetchone()
    if not owned:
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
        result = branches.activate_node(user["id"], int(body.get("node_id")))
    except ValueError as exc:
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
    try:
        return json_response(branches.delete_subtree(user["id"], int(body.get("node_id"))))
    except ValueError as exc:
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
        owned = db.execute(
            "select 1 from game_saves where id = %s and user_id = %s",
            (save_id, user["id"]),
        ).fetchone()
        if not owned:
            return json_response({"ok": False, "error": "无权访问该存档"}, status_code=403)
    try:
        from agents.anchor_seed_agent import (
            drift_by_phase,
            list_pending_for_phase,
            summarize_save_anchor_state,
        )
        summary = summarize_save_anchor_state(save_id)
        by_phase = drift_by_phase(save_id)
        recent_pending = list_pending_for_phase(save_id, None, limit=12)
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
    except Exception as exc:
        return json_response(
            {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
            status_code=500,
        )


@router.post("/api/saves/{save_id}/anchors/reseed")
async def api_save_anchors_reseed(request: Request, save_id: int, user=Depends(require_user)):
    """task 136h: 强制重 seed 锚点 (调试用)。
    body 可选: {"keep_satisfied": true|false} 默认 true (保留已发生)。
    """
    with connect() as db:
        owned = db.execute(
            "select 1 from game_saves where id = %s and user_id = %s",
            (save_id, user["id"]),
        ).fetchone()
        if not owned:
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
    except Exception as exc:
        return json_response(
            {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
            status_code=500,
        )


# ── Phase F/W6: 创建引导 + 游戏内设置(读 schema/设置,写 apply 锁死 enforcement)──
@router.get("/api/saves/{save_id}/settings")
async def api_save_settings_get(save_id: int, user=Depends(require_user)):
    """读当前存档设置 + 字段 schema(前端向导/设置面板用)。"""
    from gm_serving import settings as _set
    with connect() as db:
        owned = db.execute(
            "select 1 from game_saves where id=%s and user_id=%s", (save_id, user["id"]),
        ).fetchone()
        if not owned:
            return json_response({"ok": False, "error": "无权访问该存档"}, status_code=403)
        current = _set.read_settings(db, save_id)
    return json_response({"ok": True, "settings": current, "schema": _set.schema()})


@router.patch("/api/saves/{save_id}/settings")
async def api_save_settings_patch(request: Request, save_id: int, user=Depends(require_user)):
    """写设置(apply_settings:建档可设锁死项,游戏中锁死项拒改+非法值拒)。

    Body: {"updates": {...}, "is_create": bool}
    """
    from gm_serving import settings as _set
    with connect() as db:
        owned = db.execute(
            "select 1 from game_saves where id=%s and user_id=%s", (save_id, user["id"]),
        ).fetchone()
        if not owned:
            return json_response({"ok": False, "error": "无权访问该存档"}, status_code=403)
        try:
            body = await request.json()
        except Exception:
            return json_response({"ok": False, "error": "body 必须是合法 JSON"}, status_code=400)
        updates = body.get("updates") if isinstance(body.get("updates"), dict) else {}
        res = _set.apply_settings(db, save_id, updates, is_create=bool(body.get("is_create")))
    return json_response({"ok": True, **res})
