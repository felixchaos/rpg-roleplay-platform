"""rath/engine.py — RATH 实验生命周期 + 离线 tick 编排。

铁律(设计 §1):离线绝不写游戏 state。tick 只【读】runtime_checkouts.state_snapshot,
产物落 kb_events(record_event,COW/谱系隔离,情景召回天然可见)+ rath_events(观测台)。

并发与连接纪律(血泪先例:advisory 锁内嵌套开连接曾致 PgBouncer 池死锁):
- 不用 advisory lock;worker 竞争用【CAS 认领】一条原子 UPDATE 解决(输家 0 行,天然只跑一次);
- 三段式:认领(连接A,秒级)→ LLM(不持任何 DB 连接)→ 落产物(连接B)。

预算闸(设计 §2):每日 ≤MAX_TICKS_PER_DAY tick / ≤MAX_SCENES_PER_DAY 场景;
72h 无人看自动 pause;每用户 ≤MAX_RUNNING_PER_USER 个进行中实验。
"""
from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)

MAX_TICKS_PER_DAY = 48
MAX_SCENES_PER_DAY = 12
MAX_RUNNING_PER_USER = 2
AUTO_PAUSE_UNVIEWED_HOURS = 72
ACCEL_CHOICES = (1, 60, 240)
SCENE_EVERY_N_TICKS = 2  # 每第 2 拍尝试一场对手戏


def _trace(exp_id: int, msg: str, *, clock: int = 0) -> None:
    """运行日志(用户需求:思考流/执行层可见):tick 各相位逐条落 rath_events kind=trace,
    前端快轮询实时展示。短连接、静默失败,绝不破 tick。"""
    try:
        from platform_app.db import connect
        with connect() as db:
            db.execute(
                "insert into rath_events (exp_id, kind, summary, world_clock_min) "
                "values (%s, 'trace', %s, %s)",
                (int(exp_id), str(msg)[:300], int(clock)),
            )
            if hasattr(db, "commit"):
                db.commit()
    except Exception:
        pass


def _clock_label(total_min: int) -> str:
    d, rem = divmod(max(0, int(total_min)), 1440)
    h, m = divmod(rem, 60)
    return f"第{d + 1}日 {h:02d}:{m:02d}"


def _read_snapshot(db, save_id: int) -> tuple[dict, int]:
    """只读活跃快照 + 活跃 commit id。绝不写。"""
    save = db.execute(
        "select active_commit_id from game_saves where id=%s", (int(save_id),)
    ).fetchone()
    commit_id = int((save or {}).get("active_commit_id") or 0)
    row = db.execute(
        "select state_snapshot from runtime_checkouts where save_id=%s", (int(save_id),)
    ).fetchone()
    snap = (row or {}).get("state_snapshot")
    if not isinstance(snap, dict) or not snap:
        row2 = db.execute(
            "select state_snapshot from branch_commits where id=%s", (commit_id,)
        ).fetchone()
        snap = (row2 or {}).get("state_snapshot") or {}
    return (snap if isinstance(snap, dict) else {}), commit_id


def _expose(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "save_id": int(row["save_id"]),
        "script_id": row.get("script_id"),
        "status": row.get("status"),
        "accel": int(row.get("accel") or 60),
        "tick_interval_sec": int(row.get("tick_interval_sec") or 1800),
        "world_clock_min": int(row.get("world_clock_min") or 0),
        "world_clock_label": _clock_label(int(row.get("world_clock_min") or 0)),
        "ticks_today": int(row.get("ticks_today") or 0),
        "scenes_today": int(row.get("scenes_today") or 0),
        "budget": {"ticks_per_day": MAX_TICKS_PER_DAY, "scenes_per_day": MAX_SCENES_PER_DAY},
        "directive": str(row.get("directive") or ""),
        "last_tick_at": str(row.get("last_tick_at") or ""),
        "created_at": str(row.get("created_at") or ""),
    }


def create_experiment(user_id: int, save_id: int) -> dict:
    from platform_app.db import connect, init_db
    init_db()
    with connect() as db:
        save = db.execute(
            "select id, user_id, script_id from game_saves where id=%s", (int(save_id),)
        ).fetchone()
        if not save or int(save["user_id"]) != int(user_id):
            return {"ok": False, "error": "存档不存在或不属于你"}
        n = db.execute(
            "select count(*) c from rath_experiments where user_id=%s and status in ('running','paused')",
            (int(user_id),),
        ).fetchone()["c"]
        if int(n) >= MAX_RUNNING_PER_USER:
            return {"ok": False, "error": f"同时最多 {MAX_RUNNING_PER_USER} 个实验,请先归档旧实验"}
        dup = db.execute(
            "select id from rath_experiments where save_id=%s and status in ('running','paused')",
            (int(save_id),),
        ).fetchone()
        if dup:
            return {"ok": False, "error": "该存档已有进行中的实验", "id": int(dup["id"])}
        row = db.execute(
            """insert into rath_experiments (user_id, save_id, script_id, status, last_tick_at)
               values (%s, %s, %s, 'running', now()) returning *""",
            (int(user_id), int(save_id), save.get("script_id")),
        ).fetchone()
        if hasattr(db, "commit"):
            db.commit()
        return {"ok": True, "experiment": _expose(row)}


def _claim_tick(db, exp_id: int, *, manual: bool) -> dict | None:
    """原子认领一拍:CAS 推进 last_tick_at/world_clock_min/ticks_today。
    输家(未到期/已被别的 worker 认领/预算尽/非 running)得 None。
    返回 {new_clock, old_clock, save_id, user_id, exp_id, ticks_today, scenes_today, accel}。"""
    due_cond = "" if manual else \
        " and (t.last_tick_at is null or t.last_tick_at < now() - (interval '1 second' * t.tick_interval_sec))"
    status_cond = "t.status in ('running','paused')" if manual else "t.status = 'running'"
    row = db.execute(
        f"""
        with prev as (select id, last_tick_at, world_clock_min from rath_experiments where id = %s)
        update rath_experiments t
           set last_tick_at = now(),
               ticks_today = case when t.day_key = current_date then t.ticks_today + 1 else 1 end,
               scenes_today = case when t.day_key = current_date then t.scenes_today else 0 end,
               day_key = current_date,
               world_clock_min = t.world_clock_min + greatest(
                   %s,
                   (extract(epoch from (now() - coalesce(prev.last_tick_at, now())))::bigint * t.accel) / 60
               )
          from prev
         where t.id = prev.id
           and {status_cond}
           and (t.day_key <> current_date or t.ticks_today < %s)
           {due_cond}
        returning t.id, t.save_id, t.user_id, t.accel, t.ticks_today, t.scenes_today,
                  t.world_clock_min as new_clock, prev.world_clock_min as old_clock
        """,
        (int(exp_id),
         60 if manual else 1,  # 手动 tick 至少推 1 小时世界时,给戏留跨度
         MAX_TICKS_PER_DAY),
    ).fetchone()
    return dict(row) if row else None


def tick_experiment(exp_id: int, *, manual: bool = False) -> dict:
    """跑一拍。三段式:认领(连接A)→ LLM(无连接)→ 落产物(连接B)。任何一步失败降级跳过。"""
    from platform_app.db import connect, init_db
    init_db()
    # ── 段1:认领 + 只读材料 ──
    with connect() as db:
        claim = _claim_tick(db, exp_id, manual=manual)
        if not claim:
            return {"ok": False, "reason": "未到期/预算尽/已被认领/未运行"}
        if hasattr(db, "commit"):
            db.commit()
        save_id, user_id = int(claim["save_id"]), int(claim["user_id"])
        snap, commit_id = _read_snapshot(db, save_id)
        recent_rows = db.execute(
            "select summary from rath_events where exp_id=%s and kind in ('heartbeat','scene') "
            "order by id desc limit 4",
            (int(exp_id),),
        ).fetchall()
        # 引导=插入日志的节点事件(用户重设计):最新一条从其插入点开始引导后续演化。
        # 不再读 experiments.directive 列(旧全局状态语义,已废)。
        drow = db.execute(
            "select id, summary from rath_events where exp_id=%s and kind='directive' "
            "order by id desc limit 1",
            (int(exp_id),),
        ).fetchone()

        # 世界观要点(拆书审计后补):离线戏/心跳没有世界书材料会滑向平庸写实(战姬味丢失实锤)。
        # 取该剧本高优先级条目压缩成要点,喂进两个 LLM prompt。
        wb_rows = []
        try:
            srow = db.execute(
                "select script_id from game_saves where id=%s", (save_id,)).fetchone()
            _sid = int((srow or {}).get("script_id") or 0)
            if _sid:
                wb_rows = db.execute(
                    "select title, content from worldbook_entries where script_id=%s "
                    "order by priority desc, id asc limit 6",
                    (_sid,),
                ).fetchall()
        except Exception:
            wb_rows = []
        # 原著 canon 卡司(用户实锤「主角团根本没这人」):按 importance 取头部角色,
        # 防剧透门控=first_revealed_chapter ≤ 玩家进度+3 且 reveal_known。
        # dossier 只用时间中性字段(personality/appearance),不用 identity(全书聚合含结局剧透)。
        cast_rows = []
        try:
            if _sid:
                _prog = 1
                try:
                    _prog = int(((snap.get("worldline") or {}).get("progress_chapter"))
                                or ((snap.get("world") or {}).get("progress_chapter")) or 1)
                except Exception:
                    _prog = 1
                cast_rows = db.execute(
                    "select name, personality, appearance from character_cards "
                    "where script_id=%s and coalesce(reveal_known, true) "
                    "and coalesce(first_revealed_chapter, 0) <= %s "
                    "and coalesce(importance, 0) >= 100 "  # 垃圾卡地板(实锤:「秘书」importance=1 混进卡司)
                    "order by importance desc nulls last limit 3",
                    (_sid, _prog + 3),
                ).fetchall()
        except Exception:
            cast_rows = []
    if not snap or not commit_id:
        return {"ok": False, "reason": "快照不可读"}
    world_context = "\n".join(
        f"- {r['title']}: {str(r.get('content') or '')[:140]}" for r in (wb_rows or [])
    )
    directive = str((drow or {}).get("summary") or "").strip()
    cast_names = [str(r.get("name") or "").strip() for r in (cast_rows or []) if r.get("name")]
    cast_dossiers = {
        str(r["name"]).strip():
            "原著重要人物(当前阶段身份以正文为准);" +
            ";".join(x for x in [
                str(r.get("personality") or "").strip()[:60],
                ("外貌:" + str(r.get("appearance") or "").strip()[:40]) if r.get("appearance") else "",
            ] if x)
        for r in (cast_rows or []) if r.get("name")
    }
    new_clock = int(claim["new_clock"])
    gain_min = max(0, new_clock - int(claim["old_clock"] or 0))
    _trace(exp_id, f"推进开始({'手动' if manual else '自动'}):世界时间 +{gain_min}分钟 → {_clock_label(new_clock)}", clock=new_clock)
    elapsed_hint = f"世界内约 {gain_min // 60} 小时 {gain_min % 60} 分钟(观测钟 {_clock_label(new_clock)})"
    recent = [r["summary"] for r in (recent_rows or [])]
    player_name = str(((snap.get("player") or {}).get("name")) or "")
    location = str(((snap.get("player") or {}).get("current_location")) or "")

    _trace(exp_id, "材料装配:卡司候选[" + ",".join(cast_names or ["无"]) + "]"
           + f" · 世界书要点 {len(wb_rows or [])} 条 · 引导:" + (directive[:40] + "…" if len(directive) > 40 else directive or "无"),
           clock=new_clock)

    # ── 段2:LLM(不持任何 DB 连接) ──
    wrote: list[str] = []
    scene: dict | None = None
    scene_pair: tuple[str, str] | None = None
    try:
        from agents.recorder import _resolve_recorder_api_and_model
        api_id, model = _resolve_recorder_api_and_model(user_id, None, None)
    except Exception:
        api_id, model = None, None
    if api_id and model:
        # 2a. 离线心跳事件(复用心跳材料/prompt/验收,绝不写 state)
        try:
            from agents._harness import call_agent_json
            from agents.world_heartbeat import _build_materials, _build_prompts, _validate_items
            from core.json_parse import parse_llm_json
            materials = _build_materials(snap, None)
            sys_p, usr_p = _build_prompts(materials)
            usr_p += f"\n\n【离线时长】玩家已离开,{elapsed_hint}。事件应体现这段时间的自然流逝。"
            if player_name:
                usr_p += (f"\n【玩家角色设定神圣】不得为玩家角色{player_name}编造来历/身份/标签,"
                          "不得生成把其归类为实验品/机构产物等的事件;其谜团只属于玩家。")
            if world_context:
                usr_p += f"\n【世界观要点(事件应符合此世界质感)】\n{world_context}"
            if directive:
                usr_p += f"\n【观测者引导(事件应朝此方向自然倾斜,不得突兀)】{directive}"
            text, _usage = call_agent_json(
                api_id=api_id, model=model, system_prompt=sys_p, user_prompt=usr_p,
                user_id=user_id, tool_schema=None, max_tokens=400, timeout_sec=25,
                agent_kind="rath_offline_heartbeat",
            )
            raw_items = parse_llm_json(text, want=list) or []
            wrote = _validate_items(raw_items, state_data=snap, player_name=player_name)[:2]
            _trace(exp_id, f"背景事件:模型产出 {len(raw_items)} 条 → 验收通过 {len(wrote)} 条"
                   + ("(其余因重复/提及玩家/超长被丢弃)" if len(raw_items) > len(wrote) else ""),
                   clock=new_clock)
        except Exception as exc:
            log.warning("[rath] 离线心跳跳过(非致命): %s", exc)
            _trace(exp_id, f"背景事件:失败跳过({str(exc)[:60]})", clock=new_clock)
        # 2b. NPC-NPC 对手戏(每第 N 拍,预算内)
        if (int(claim["ticks_today"]) % SCENE_EVERY_N_TICKS == 0
                and int(claim["scenes_today"]) < MAX_SCENES_PER_DAY):
            try:
                from rath.npc_scene import build_scene_prompts, select_scene_pair, validate_scene
                scene_pair = select_scene_pair(
                    snap, extra_candidates=cast_names,
                    exclude_names={player_name} if player_name else None)
                # 玩家化身自主行动(用户需求):偶数场次由玩家角色按【设定】参演——
                # 化身在玩家离线时也活着(状态守恒:昏迷只能梦呓;绝不替玩家做重大决定)。
                player_in_scene = ""
                if player_name and scene_pair and int(claim["scenes_today"]) % 2 == 0:
                    scene_pair = (scene_pair[0], player_name)
                    player_in_scene = player_name
                    _pl = snap.get("player") or {}
                    cast_dossiers = dict(cast_dossiers)
                    cast_dossiers[player_name] = (
                        "玩家角色(按其设定由模拟器驱动,状态守恒);"
                        + str(_pl.get("role") or "")[:50] + ";"
                        + str(_pl.get("background") or _pl.get("identity_role_desc") or "")[:180]
                    )
                if scene_pair:
                    _trace(exp_id, f"选角:{scene_pair[0]} × {scene_pair[1]}"
                           + ("(玩家化身场,按设定驱动)" if player_in_scene else "")
                           + f" · 来源=议程/关系+卡司", clock=new_clock)
                    from agents._harness import call_agent_json
                    sys_p, usr_p = build_scene_prompts(
                        snap, scene_pair[0], scene_pair[1],
                        elapsed_hint=elapsed_hint, recent_events=recent + wrote,
                        world_context=world_context, directive=directive,
                        extra_dossiers=cast_dossiers, player_in_scene=player_in_scene)
                    text, _usage = call_agent_json(
                        api_id=api_id, model=model, system_prompt=sys_p, user_prompt=usr_p,
                        user_id=user_id, tool_schema=None, max_tokens=900, timeout_sec=40,
                        agent_kind="rath_npc_scene",
                    )
                    _known = usr_p + " " + sys_p  # 材料全集=档案+世界观要点+近况+引导
                    scene = validate_scene(text or "", scene_pair[0], scene_pair[1],
                                           known_text=_known)
                    if scene:
                        _trace(exp_id, f"互动生成:通过验收 —— {scene['scene_summary'][:60]}", clock=new_clock)
                    else:
                        from rath.npc_scene import find_fabricated_nouns
                        _fab = find_fabricated_nouns(text or "", _known)
                        _trace(exp_id, ("互动拒收:新造名词 " + "/".join(_fab[:3])) if _fab
                               else "互动拒收:结构不合格或对白为空(本拍无戏,正常)", clock=new_clock)
            except Exception as exc:
                log.warning("[rath] 对手戏跳过(非致命): %s", exc)

    # ── 段3:落产物(连接B;只写 kb_events + rath 表) ──
    if wrote or scene:
        with connect() as db:
            try:
                from kb.live_repo import record_event
                for i, it in enumerate(wrote):
                    record_event(
                        db, save_id, commit_id, f"rath_hb_{exp_id}_{new_clock}_{i}",
                        summary=it, story_time=_clock_label(new_clock), location=location,
                        metadata={"source": "rath_offline_heartbeat"},
                    )
                    db.execute(
                        "insert into rath_events (exp_id, kind, summary, world_clock_min) values (%s,'heartbeat',%s,%s)",
                        (int(exp_id), it, new_clock),
                    )
                if scene and scene_pair:
                    npc_a, npc_b = scene_pair
                    record_event(
                        db, save_id, commit_id, f"rath_scene_{exp_id}_{new_clock}",
                        summary=f"{npc_a}与{npc_b}:{scene['scene_summary']}",
                        story_time=_clock_label(new_clock), participants=[npc_a, npc_b],
                        metadata={"source": "rath_npc_scene"},
                    )
                    db.execute(
                        "insert into rath_events (exp_id, kind, summary, payload, world_clock_min) values (%s,'scene',%s,%s,%s)",
                        (int(exp_id), scene["scene_summary"],
                         json.dumps({"npc_a": npc_a, "npc_b": npc_b,
                                     "transcript": scene["transcript"],
                                     "npc_updates": scene["npc_updates"]}, ensure_ascii=False),
                         new_clock),
                    )
                    db.execute(
                        "update rath_experiments set scenes_today = scenes_today + 1 where id=%s",
                        (int(exp_id),),
                    )
                if hasattr(db, "commit"):
                    db.commit()
            except Exception as exc:
                log.warning("[rath] 产物落库失败(非致命): %s", exc)
    _trace(exp_id, f"推进完成:落库 背景事件 {len(wrote)} 条"
           + (" + 互动 1 场" if scene else "") + f" @ {_clock_label(new_clock)}", clock=new_clock)
    log.info("[rath] tick exp=%s clock=%s events=%d scene=%s",
             exp_id, _clock_label(new_clock), len(wrote), bool(scene))
    return {"ok": True, "wrote_events": wrote,
            "scene": ({"npc_a": scene_pair[0], "npc_b": scene_pair[1],
                       "summary": scene["scene_summary"]} if scene and scene_pair else None),
            "world_clock_label": _clock_label(new_clock)}


def run_due_ticks() -> int:
    """ticker 入口:扫 due 实验逐个 tick。CAS 认领天然防双 worker 重复,无需持锁。"""
    from platform_app.db import connect, init_db
    init_db()
    with connect() as db:
        db.execute(
            "update rath_experiments set status='paused' "
            "where status='running' and last_viewed_at < now() - (interval '1 hour' * %s)",
            (AUTO_PAUSE_UNVIEWED_HOURS,),
        )
        due = db.execute(
            """select id from rath_experiments
               where status='running'
                 and (last_tick_at is null or last_tick_at < now() - (interval '1 second' * tick_interval_sec))
               order by last_tick_at asc nulls first limit 4""",
        ).fetchall()
        if hasattr(db, "commit"):
            db.commit()
    ticked = 0
    try:
        with connect() as db:
            db.execute(
                """delete from rath_events t using (
                       select exp_id, id, row_number() over (partition by exp_id order by id desc) rn
                       from rath_events where kind='trace') x
                   where t.id = x.id and x.rn > 300""")
            if hasattr(db, "commit"):
                db.commit()
    except Exception:
        pass
    for r in (due or []):
        try:
            if tick_experiment(int(r["id"])).get("ok"):
                ticked += 1
        except Exception as exc:
            log.warning("[rath] tick %s 失败(非致命): %s", r["id"], exc)
    return ticked
