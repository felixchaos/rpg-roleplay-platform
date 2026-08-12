"""routes/rath.py — RATH·搖光观测台 API(docs/design/rath_observation_deck_v0.md §2)。

全端点 require user + feature flag `rath_experiment`(默认关,灰度验后开)。
last_viewed_at(72h 无人看自动 pause 的依据)语义:GET 详情仅带 `?active=1`(前台
主动打开)才 bump,被动轮询不续命;POST 交互端点(tick/pause/resume/directive/accel)
一律 bump;GET 列表端点从不 bump。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from platform_app.api._deps import json_response
from routes._deps_fastapi import get_current_user

router = APIRouter()


def _flag_ok(user) -> bool:
    """灰度期两道**服务端**闸,缺一不可:

    ① 管理员角色 —— 这是真正的访问控制。前端的「导航隐藏 + 路由 AdminGuard」只是藏 UI,
      绕过成本是敲一下 URL;而 `rath_experiment` 那道 flag 读的是 **用户自己能写的**
      `user_preferences`(`POST /api/me/preference` 收任意键、无白名单)—— 任何登录用户
      POST 一句 `{"rath_experiment.enabled": true}` 就能给自己开(08-01 反馈:
      「为什么普通用户可以看到 rath」)。所以灰度期必须有一道用户改不了的闸。
    ② 特性 flag —— 决定这套东西在本部署上是否启用(env `RPG_RATH_EXPERIMENT`)。
      feature_flags 已把 rath 排除出「用户可自开」集合,这里读到的只会是环境值。

    对全体开放时:删掉 ① 这一行,同时删掉 platform-app.jsx 的 adminOnly 与
    entries/platform.jsx 的 AdminGuard 包裹(三处同批次改,别只改一处)。
    """
    from core.feature_flags import feature_enabled
    from platform_app.api._deps import is_admin
    if not is_admin(user):
        return False
    return feature_enabled("rath_experiment", int(user["id"]))


def _deny() -> JSONResponse:
    return json_response({"ok": False, "error": "RATH 实验未对当前账号开放"}, status_code=403)


@router.get("/api/rath/preflight")
async def api_rath_preflight(save_id: int, user=Depends(get_current_user)):
    """D4:建实验前预检——材料分层(tier)+ 拒建闸(酒馆档/IDOR)+ 具体化 warnings。
    契约见 scratchpad/rath_v4_contracts.md,字段一个不能少/拼错(前端已按此接好 UI)。"""
    if not _flag_ok(user):
        return _deny()
    from rath.engine import preflight
    out = preflight(int(user["id"]), int(save_id))
    if not out.get("ok"):
        return json_response(out, status_code=404)
    return out


@router.get("/api/rath/experiments")
async def api_rath_list(user=Depends(get_current_user)):
    if not _flag_ok(user):
        return _deny()
    from platform_app.db import connect, init_db
    from rath.engine import _expose
    init_db()
    with connect() as db:
        # A5:列表端点是被动轮询,不 bump last_viewed_at(72h 无人看自动 pause 只认真的
        # "看详情"/交互;否则任何只翻列表的前端模式都会让实验永不触发自动暂停)。
        rows = db.execute(
            "select t.*, s.save_kind from rath_experiments t "
            "join game_saves s on s.id = t.save_id "
            "where t.user_id=%s and t.status in ('running','paused') "
            "order by t.id desc limit 10",
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
        return json_response({"ok": False, "error": "缺 save_id"}, status_code=400)
    from rath.engine import create_experiment
    out = create_experiment(int(user["id"]), save_id)
    return out if out.get("ok") else json_response(out, status_code=400)


def _own_exp(db, exp_id: int, user_id: int):
    return db.execute(
        "select t.*, s.save_kind from rath_experiments t "
        "join game_saves s on s.id = t.save_id "
        "where t.id=%s and t.user_id=%s",
        (int(exp_id), int(user_id)),
    ).fetchone()


@router.get("/api/rath/experiments/{exp_id}")
async def api_rath_detail(exp_id: int, active: int = 0, user=Depends(get_current_user)):
    if not _flag_ok(user):
        return _deny()
    from platform_app.db import connect, init_db
    from rath.engine import _expose, _read_snapshot
    init_db()
    with connect() as db:
        exp = _own_exp(db, exp_id, user["id"])
        if not exp:
            return json_response({"ok": False, "error": "实验不存在"}, status_code=404)
        # A5:只有前台"我正盯着这个实验"的主动请求(?active=1)才续 72h 无人看的命,
        # 被动轮询(前端每隔几秒刷一次详情)不算"看"。
        if active == 1:
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
        # P2(A10):sim_state 已含 cast 时它是角色动态的权威源,下面 agendas 兜底路径根本
        # 用不上 snap——省一次 runtime_checkouts/branch_commits 整读(高频轮询端点减负)。
        _sim_pre = exp.get("sim_state") if isinstance(exp.get("sim_state"), dict) else None
        if _sim_pre and (_sim_pre.get("cast") or {}):
            snap: dict = {}
        else:
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
                "stance": c.get("stance") or "",
                "status": c.get("status") or "",
                "private_memories": (c.get("memory") or [])[-5:][::-1],
            })
        threads_out = [{"id": t.get("id"), "desc": t.get("desc"), "tension": t.get("tension"),
                        "stage": t.get("stage") or "rising",
                        "tension_hist": (t.get("tension_hist") or [])[-12:]}
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
    # v3:关系网+河道进度(观测台可视化数据)
    relations_out = []
    canon_out = None
    if sim:
        for k, v in (sim.get("relations") or {}).items():
            a, _, b = k.partition("|")
            relations_out.append({"a": a, "b": b, "kind": (v or {}).get("kind") or "",
                                  "note": (v or {}).get("note") or ""})
        _cn = sim.get("canon") or {}
        _beats = _cn.get("beats") or []
        _cur = int(_cn.get("cursor") or 0)
        canon_out = {
            "cursor": _cur, "total": len(_beats), "stall": int(_cn.get("stall") or 0),
            "current_chapter": (_beats[_cur].get("chapter") if _cur < len(_beats) else None),
            "next_text": (_beats[_cur].get("text") if _cur < len(_beats) else ""),
        }
    return {"ok": True, "experiment": _expose(dict(exp)), "events": ev_out,
            "trace": trace_out, "fluctlights": fluctlights, "threads": threads_out,
            "relations": relations_out, "canon": canon_out}


@router.post("/api/rath/experiments/{exp_id}/tick")
async def api_rath_tick(exp_id: int, user=Depends(get_current_user)):
    if not _flag_ok(user):
        return _deny()
    from platform_app.db import connect, init_db
    init_db()
    with connect() as db:
        if not _own_exp(db, exp_id, user["id"]):
            return json_response({"ok": False, "error": "实验不存在"}, status_code=404)
        # A5:POST 交互端点一律 bump(手动点了"推进"就是主动在看)。
        db.execute("update rath_experiments set last_viewed_at=now() where id=%s", (int(exp_id),))
        if hasattr(db, "commit"):
            db.commit()
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
        return json_response({"ok": False, "error": "未知操作"}, status_code=400)
    from platform_app.db import connect, init_db
    from rath.engine import ACCEL_CHOICES, _expose
    init_db()
    with connect() as db:
        exp = _own_exp(db, exp_id, user["id"])
        if not exp:
            return json_response({"ok": False, "error": "实验不存在"}, status_code=404)
        if action == "directive":
            # 引导=插入日志的节点事件:从插入点开始引导其后的演化,最新一条生效。
            # 历史全部留在 rath_events(kind=directive),日志可查。
            body = await request.json()
            directive = str(body.get("directive") or "").strip()[:200]
            if not directive:
                return json_response({"ok": False, "error": "引导内容不能为空"}, status_code=400)
            db.execute(
                "insert into rath_events (exp_id, kind, summary, world_clock_min) "
                "values (%s, 'directive', %s, %s)",
                (int(exp_id), directive, int(exp.get("world_clock_min") or 0)),
            )
            # A5:交互端点一律 bump last_viewed_at。
            db.execute(
                "update rath_experiments set last_viewed_at=now() where id=%s", (int(exp_id),))
            row = exp
        elif action == "accel":
            body = await request.json()
            accel = int(body.get("accel") or 0)
            if accel not in ACCEL_CHOICES:
                return json_response({"ok": False, "error": f"accel 只能是 {ACCEL_CHOICES}"}, status_code=400)
            # accel 联动(契约):tick_interval_sec = clamp(1800*60/accel, 600, 3600)
            # → 240x=600s,60x=1800s,1x=3600s。不联动的话高倍速下单拍会吞掉数天
            # (run_due_ticks 每 60s 才扫一次,固定 1800s 间隔配 240x accel 一拍=5世界日)。
            interval_sec = max(600, min(3600, (1800 * 60) // accel))
            row = db.execute(
                "update rath_experiments set accel=%s, tick_interval_sec=%s, "
                "last_viewed_at=now() where id=%s returning *",
                (accel, interval_sec, int(exp_id)),
            ).fetchone()
            row = dict(row)
            row["save_kind"] = exp.get("save_kind")
        elif action == "pause":
            # 手动暂停:pause_reason='user'(区别于 unviewed/no_model/player_active 三种
            # 自动暂停)。paused_at/last_tick_at 同置 now()——冻结钟基准,防 resume 时
            # 按整段暂停时长 banking 世界时。
            row = db.execute(
                "update rath_experiments set status='paused', pause_reason='user', "
                "paused_at=now(), last_tick_at=now(), last_viewed_at=now() "
                "where id=%s returning *",
                (int(exp_id),),
            ).fetchone()
            row = dict(row)
            row["save_kind"] = exp.get("save_kind")
        elif action == "resume":
            row = db.execute(
                "update rath_experiments set status='running', pause_reason=null, "
                "paused_at=null, last_tick_at=now(), last_viewed_at=now() "
                "where id=%s returning *",
                (int(exp_id),),
            ).fetchone()
            row = dict(row)
            row["save_kind"] = exp.get("save_kind")
        else:  # archive
            row = db.execute(
                "update rath_experiments set status='archived', last_viewed_at=now() "
                "where id=%s returning *",
                (int(exp_id),),
            ).fetchone()
            row = dict(row)
            row["save_kind"] = exp.get("save_kind")
        if hasattr(db, "commit"):
            db.commit()
    return {"ok": True, "experiment": _expose(dict(row))}
