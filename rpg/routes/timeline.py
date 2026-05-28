"""timeline.py — 存档时间线路由 (/api/saves/:save_id/timeline)。"""
from __future__ import annotations
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/api/saves/{save_id}/timeline")
async def api_saves_timeline(save_id: int, request: Request) -> JSONResponse:
    """返回指定存档的双时间线数据:剧本期望线 + 实际足迹线。

    权限: 必须是该 save 的所有者,否则 403。
    """
    from app import _require_api_user, _resolve_persist_target
    api_user = _require_api_user(request)
    # 本地无鉴权时 api_user 可能为 None，回退到 runtime.json 的 user_id
    if api_user:
        user_id = int(api_user["id"])
    else:
        _rt_user_id, _ = _resolve_persist_target(None)  # returns (user_id, save_id)
        user_id = int(_rt_user_id or 0)

    from platform_app.db import connect, init_db
    init_db()

    with connect() as db:
        # 1. 验证 ownership — 同时拿 script_id 和 active_phase_index
        # 本地无鉴权时 user_id 可能为 0（runtime.json 还没有），允许宽松查询
        if user_id:
            save_row = db.execute(
                """
                select id, script_id, active_phase_index
                  from game_saves
                 where id = %s and user_id = %s
                """,
                (save_id, user_id),
            ).fetchone()
        else:
            save_row = db.execute(
                "select id, script_id, active_phase_index from game_saves where id = %s",
                (save_id,),
            ).fetchone()
        if not save_row:
            raise HTTPException(status_code=403, detail="存档不存在或无权访问")

        script_id = save_row["script_id"]
        active_phase_index = save_row.get("active_phase_index") or 0

        # 2. 剧本期望线 — script_timeline_anchors 按 chapter_min 排序
        # 字段名: story_phase → 对应任务描述里的 phase_label
        anchor_rows = db.execute(
            """
            select chapter_min, chapter_max,
                   story_phase   as phase_label,
                   story_time_label
              from script_timeline_anchors
             where script_id = %s
             order by chapter_min
            """,
            (script_id,),
        ).fetchall()

        script_anchors = [
            {
                "chapter_min": r["chapter_min"],
                "chapter_max": r["chapter_max"],
                "phase_label": r["phase_label"] or "",
                "story_time_label": r["story_time_label"] or "",
            }
            for r in anchor_rows
        ]

        # 3. 实际足迹线 — save_phase_digests 按 phase_index 排序
        phase_rows = db.execute(
            """
            select phase_index, phase_label, turn_start, turn_end,
                   story_time_label, summary, key_events, status
              from save_phase_digests
             where save_id = %s
             order by phase_index
            """,
            (save_id,),
        ).fetchall()

        import json as _json

        def _parse_jsonb(v):
            if v is None:
                return []
            if isinstance(v, (list, dict)):
                return v
            try:
                return _json.loads(v)
            except Exception:
                return []

        save_phases = [
            {
                "phase_index": r["phase_index"],
                "phase_label": r["phase_label"] or "",
                "turn_start": r["turn_start"],
                "turn_end": r["turn_end"],
                "story_time_label": r["story_time_label"] or "",
                "summary": r["summary"] or "",
                "key_events": _parse_jsonb(r["key_events"]),
                "status": r["status"] or "open",
            }
            for r in phase_rows
        ]

    return JSONResponse({
        "ok": True,
        "script_anchors": script_anchors,
        "save_phases": save_phases,
        "current_phase_index": active_phase_index,
    })
