"""routes/rath.py — RATH·搖光观测台 API(docs/design/rath_observation_deck_v0.md §2)。

全端点 require user + feature flag `rath_experiment`(默认关,灰度验后开)。
GET 详情 bump last_viewed_at(72h 无人看自动 pause 的依据)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from routes._deps_fastapi import get_current_user

router = APIRouter()


def _flag_ok(user) -> bool:
    from core.feature_flags import feature_enabled
    return feature_enabled("rath_experiment", int(user["id"]))


def _deny() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "RATH 实验未对当前账号开放"}, status_code=403)


@router.get("/api/rath/experiments")
async def api_rath_list(user=Depends(get_current_user)):
    if not _flag_ok(user):
        return _deny()
    from platform_app.db import connect, init_db
    from rath.engine import _expose
    init_db()
    with connect() as db:
        rows = db.execute(
            "select * from rath_experiments where user_id=%s and status in ('running','paused') "
            "order by id desc limit 10",
            (int(user["id"]),),
        ).fetchall()
    return {"ok": True, "experiments": [_expose(dict(r)) for r in (rows or [])]}


@router.post("/api/rath/experiments")
async def api_rath_create(request: Request, user=Depends(get_current_user)):
    if not _flag_ok(user):
        return _deny()
    body = await request.json()
    save_id = int(body.get("save_id") or 0)
    if not save_id:
        return JSONResponse({"ok": False, "error": "缺 save_id"}, status_code=400)
    from rath.engine import create_experiment
    out = create_experiment(int(user["id"]), save_id)
    return out if out.get("ok") else JSONResponse(out, status_code=400)


def _own_exp(db, exp_id: int, user_id: int):
    return db.execute(
        "select * from rath_experiments where id=%s and user_id=%s",
        (int(exp_id), int(user_id)),
    ).fetchone()


@router.get("/api/rath/experiments/{exp_id}")
async def api_rath_detail(exp_id: int, user=Depends(get_current_user)):
    if not _flag_ok(user):
        return _deny()
    from platform_app.db import connect, init_db
    from rath.engine import _expose, _read_snapshot
    init_db()
    with connect() as db:
        exp = _own_exp(db, exp_id, user["id"])
        if not exp:
            return JSONResponse({"ok": False, "error": "实验不存在"}, status_code=404)
        db.execute(
            "update rath_experiments set last_viewed_at=now() where id=%s", (int(exp_id),))
        events = db.execute(
            "select id, kind, summary, payload, world_clock_min, created_at from rath_events "
            "where exp_id=%s and kind <> 'trace' order by id desc limit 30",
            (int(exp_id),),
        ).fetchall()
        # 运行日志(思考流/执行层):独立于故事日志,近40条
        trace_rows = db.execute(
            "select id, summary, world_clock_min, created_at from rath_events "
            "where exp_id=%s and kind='trace' order by id desc limit 40",
            (int(exp_id),),
        ).fetchall()
        snap, _commit = _read_snapshot(db, int(exp["save_id"]))
        if hasattr(db, "commit"):
            db.commit()
    agendas = snap.get("npc_agendas") or {}
    # 角色动态=三源合并(用户实锤:板子只读 state 议程,而离线模式铁律不写 state → 永远空)。
    # 源:state 议程(玩家亲玩产生) + 离线戏 npc_updates(goal/stance,最新优先) + 私记。
    offline_updates: dict[str, dict] = {}
    private_memories: dict[str, list[str]] = {}
    ev_out = []
    from rath.engine import _clock_label
    for r in (events or []):
        payload = r.get("payload") or {}
        if isinstance(payload, str):
            try:
                import json as _json
                payload = _json.loads(payload)
            except Exception:
                payload = {}
        for name, u in ((payload.get("npc_updates") or {}) if isinstance(payload, dict) else {}).items():
            nm = str(name)
            pm = (u or {}).get("private_memory")
            if pm and len(private_memories.setdefault(nm, [])) < 8:
                private_memories[nm].append(str(pm))
            # events 按 id desc 遍历 → 首见即最新,不覆盖
            if nm not in offline_updates and isinstance(u, dict):
                offline_updates[nm] = {k: str(u.get(k) or "") for k in ("goal", "stance")}
        ev_out.append({
            "id": int(r["id"]), "kind": r.get("kind"), "summary": r.get("summary"),
            "payload": payload if isinstance(payload, dict) else {},
            "world_clock_label": _clock_label(int(r.get("world_clock_min") or 0)),
            "created_at": str(r.get("created_at") or ""),
        })
    # 仿真态 v2:cast 即角色动态的权威源(位置/活动/目标/心情/私记全在状态里)。
    sim = exp.get("sim_state") if isinstance(exp.get("sim_state"), dict) else None
    threads_out = []
    if sim and (sim.get("cast") or {}):
        fluctlights = []
        for n, c in (sim.get("cast") or {}).items():
            fluctlights.append({
                "name": n,
                "kind": c.get("kind") or "npc",
                "location": c.get("location") or "",
                "activity": c.get("activity") or "",
                "goal": c.get("goal") or "",
                "stance": c.get("mood") or "",
                "status": c.get("status") or "",
                "private_memories": (c.get("memory") or [])[-5:][::-1],
            })
        threads_out = [{"id": t.get("id"), "desc": t.get("desc"), "tension": t.get("tension")}
                       for t in (sim.get("threads") or [])]
    else:
        # 兜底(仿真态未初始化):旧三源合并
        _names: list[str] = [n for n in agendas if isinstance(n, str)]
        for n in list(offline_updates) + list(private_memories):
            if n not in _names:
                _names.append(n)
        fluctlights = []
        for n in _names:
            a = agendas.get(n) or {}
            o = offline_updates.get(n) or {}
            fluctlights.append({
                "name": n,
                "goal": o.get("goal") or (a or {}).get("goal") or "",
                "stance": o.get("stance") or (a or {}).get("stance") or "",
                "private_memories": private_memories.get(n, []),
            })
    trace_out = [{
        "id": int(r["id"]), "summary": r.get("summary"),
        "world_clock_label": _clock_label(int(r.get("world_clock_min") or 0)),
        "created_at": str(r.get("created_at") or ""),
    } for r in (trace_rows or [])]
    return {"ok": True, "experiment": _expose(dict(exp)), "events": ev_out,
            "trace": trace_out, "fluctlights": fluctlights, "threads": threads_out}


@router.post("/api/rath/experiments/{exp_id}/tick")
async def api_rath_tick(exp_id: int, user=Depends(get_current_user)):
    if not _flag_ok(user):
        return _deny()
    from platform_app.db import connect, init_db
    init_db()
    with connect() as db:
        if not _own_exp(db, exp_id, user["id"]):
            return JSONResponse({"ok": False, "error": "实验不存在"}, status_code=404)
    # 异步化(用户实锤:同步跑两次 LLM 需 60-100s,前端超时误报失败):
    # 立即返回 started,tick 在后台线程跑完落日志,前端轮询自然刷出。
    import asyncio
    from rath.engine import tick_experiment
    task = asyncio.create_task(asyncio.to_thread(tick_experiment, int(exp_id), manual=True))
    _bg = getattr(api_rath_tick, "_bg_tasks", set())
    api_rath_tick._bg_tasks = _bg
    _bg.add(task)
    task.add_done_callback(_bg.discard)
    return {"ok": True, "started": True,
            "note": "已开始推进(约1分钟),完成后会出现在日志中。"}


@router.post("/api/rath/experiments/{exp_id}/{action}")
async def api_rath_action(exp_id: int, action: str, request: Request, user=Depends(get_current_user)):
    if not _flag_ok(user):
        return _deny()
    if action not in ("pause", "resume", "archive", "accel", "directive"):
        return JSONResponse({"ok": False, "error": "未知操作"}, status_code=400)
    from platform_app.db import connect, init_db
    from rath.engine import ACCEL_CHOICES, _expose
    init_db()
    with connect() as db:
        exp = _own_exp(db, exp_id, user["id"])
        if not exp:
            return JSONResponse({"ok": False, "error": "实验不存在"}, status_code=404)
        if action == "directive":
            # 引导=插入日志的节点事件:从插入点开始引导其后的演化,最新一条生效。
            # 历史全部留在 rath_events(kind=directive),日志可查。
            body = await request.json()
            directive = str(body.get("directive") or "").strip()[:200]
            if not directive:
                return JSONResponse({"ok": False, "error": "引导内容不能为空"}, status_code=400)
            db.execute(
                "insert into rath_events (exp_id, kind, summary, world_clock_min) "
                "values (%s, 'directive', %s, %s)",
                (int(exp_id), directive, int(exp.get("world_clock_min") or 0)),
            )
            row = exp
        elif action == "accel":
            body = await request.json()
            accel = int(body.get("accel") or 0)
            if accel not in ACCEL_CHOICES:
                return JSONResponse({"ok": False, "error": f"accel 只能是 {ACCEL_CHOICES}"}, status_code=400)
            row = db.execute(
                "update rath_experiments set accel=%s where id=%s returning *",
                (accel, int(exp_id)),
            ).fetchone()
        else:
            status = {"pause": "paused", "resume": "running", "archive": "archived"}[action]
            row = db.execute(
                "update rath_experiments set status=%s, last_viewed_at=now() where id=%s returning *",
                (status, int(exp_id)),
            ).fetchone()
        if hasattr(db, "commit"):
            db.commit()
    return {"ok": True, "experiment": _expose(dict(row))}
