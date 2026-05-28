"""platform_app.api.saves — /api/saves*, /api/branches/* 路由。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from .. import branches, knowledge, workspace
from ..db import connect
from ._deps import json_response, require_user

router = APIRouter()


@router.get("/api/saves")
async def api_saves(limit: int | None = None, cursor: str | None = None, user=Depends(require_user)):
    """轻量列表：只返摘要字段（turn/player_name/world_time/history_count），不含 state_snapshot。"""
    return json_response({"ok": True, **workspace.saves_page(user["id"], limit, cursor)})


@router.get("/api/saves/{save_id}/export")
async def api_save_export(save_id: int, user=Depends(require_user)):
    """下载存档 JSON（含 commits + messages + memories）。"""
    from .. import save_io
    try:
        return json_response({"ok": True, **save_io.export_save(user["id"], save_id)})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=403)


@router.post("/api/saves/import")
async def api_save_import(request: Request, user=Depends(require_user)):
    """上传一份导出的 JSON 恢复成新存档，按当前 user 重映射 owner。"""
    body = await request.json()
    from .. import save_io
    try:
        return json_response(save_io.import_save(user["id"], body.get("payload") or body))
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
    # 校验 script 归属
    with connect() as db:
        owned = db.execute("select 1 from scripts where id = %s and owner_id = %s", (script_id, user["id"])).fetchone()
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
    return json_response({"ok": True, "save": workspace.create_save(
        user["id"], script_id, body.get("title", ""),
        new_card=new_card, character=character,
        birthpoint=birthpoint, identity=identity,
    )})


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
