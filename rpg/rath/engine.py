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
SCENE_EVERY_N_TICKS = 1  # 每拍都演一场互动(用户实锤:落单拍只剩随机背景事件=毫无意义)


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
        _sid = 0
        try:
            srow = db.execute(
                "select script_id from game_saves where id=%s", (save_id,)).fetchone()
            _sid = int((srow or {}).get("script_id") or 0)
            # 进度权威=平台 get_progress_window(reveal 钳制同源),不再土算
            _prog = 1
            try:
                from agents.anchor_seed_agent import get_progress_window as _gpw
                _pw = _gpw(save_id, script_id=_sid, window_size=12) if _sid else None
                if _pw and _pw.get("chapter_min"):
                    _prog = int(_pw["chapter_min"])
            except Exception:
                try:
                    _prog = int(((snap.get("worldline") or {}).get("progress_chapter"))
                                or ((snap.get("world") or {}).get("progress_chapter")) or 1)
                except Exception:
                    _prog = 1
            if _sid:
                # 基础设施对齐(用户实锤「我的基础设施一个没用」):世界书必须过平台
                # reveal 门控(first_revealed_chapter/reveal_known),裸查会泄漏未揭示条目。
                # 500k 浸泡实锤:reveal_known 默认 false(migration 1854)且只是揭示
                # 机制的记录位,当必要条件=任何剧本都被滤成 0 条(战姬味丢失同款)。
                # 正统口径=loaders reveal 闸:只看 first_revealed_chapter(0=保守放行)。
                wb_rows = db.execute(
                    "select title, content from worldbook_entries where script_id=%s "
                    "and enabled and coalesce(first_revealed_chapter, 0) <= %s "
                    "order by priority desc, id asc limit 6",
                    (_sid, _prog + 3),
                ).fetchall()
                # 存档级世界书新增(玩家权威)同样进世界观要点(群反馈实锤:注入只查剧本表)
                try:
                    _ov = db.execute(
                        "select title, content from save_worldbook_overlays "
                        "where save_id=%s and kind='addition' order by priority desc, id desc limit 4",
                        (save_id,),
                    ).fetchall() or []
                    wb_rows = list(_ov) + list(wb_rows or [])
                except Exception:
                    pass
        except Exception:
            wb_rows = []
        # 原著 canon 卡司(用户实锤「主角团根本没这人」):按 importance 取头部角色,
        # 防剧透门控=first_revealed_chapter ≤ 玩家进度+3 且 reveal_known。
        # dossier 只用时间中性字段(personality/appearance),不用 identity(全书聚合含结局剧透)。
        cast_rows = []
        try:
            if _sid:
                # 正典链路(用户实锤「角色卡抽取也是自创的」):_load_characters=平台进度感知
                # +揭示门控+前沿闸的卡片装载;_format_card=正典渲染(身份/背景/性格/秘密语义)。
                from context_engine import _format_card
                from context_engine.loaders import _load_characters
                _cards_all = _load_characters(
                    script_id=_sid, progress_chapter=_prog,
                    foreknowledge_mode="strict", save_id=save_id) or {}
                # 装载器不带 importance 列(实锤:地板过滤把空键当0全灭,cast只剩玩家)——
                # 门控与内容独家归装载器,重要度只做排名,单独轻查。
                cast_rows = []
                if _cards_all:
                    _imp_rows = db.execute(
                        "select name, coalesce(importance,0) i from character_cards "
                        "where script_id=%s and name = any(%s)",
                        (_sid, list(_cards_all.keys())),
                    ).fetchall()
                    _imp = {r["name"]: int(r["i"]) for r in (_imp_rows or [])}
                    _ranked = sorted(
                        ((n, c) for n, c in _cards_all.items() if _imp.get(n, 0) >= 100),
                        key=lambda kv: -_imp.get(kv[0], 0))[:5]
                    # Phase 2 投影(正典):把全书聚合的 identity/background 投影成
                    # 「当前进度下的角色态」——否则第1章就泄结局(美索不达米亚控制者)。
                    from context_engine import apply_projection_to_card
                    from context_engine.projection import project_character_state
                    cast_rows = []
                    for n, c in _ranked:
                        card = dict(c or {})
                        proj = None
                        try:
                            proj = project_character_state(
                                db, _sid, n, _prog, "strict",
                                aliases=card.get("aliases") or None)
                        except Exception:
                            proj = None
                        if proj:
                            card = apply_projection_to_card(card, proj)
                        else:
                            # 投影不出(开局常态:无早期角色态数据)→ 摘除时代敏感字段。
                            # 正典回退=原卡原样会把全书聚合身份(结局)泄进第1章;
                            # 「不知道此刻的他是谁」就什么都不说,好过说出结局。
                            for k in ("identity", "background", "current_status", "secrets"):
                                card.pop(k, None)
                        cast_rows.append({"name": n, "sheet": _format_card(n, card)[:260]})
                # 原著河道(用户实锤:仿真跟原著0%重合——原著剧情必须是世界的主时间流)
                canon_rows = db.execute(
                    "select chapter, summary from chapter_facts "
                    "where script_id=%s and chapter between %s and %s "
                    "and coalesce(summary,'') <> '' order by chapter limit 12",
                    (_sid, _prog, _prog + 11),
                ).fetchall()
        except Exception:
            cast_rows = []
            canon_rows = []
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

    # ── 段2:仿真环(v2,状态优先;LLM 阶段不持任何 DB 连接) ──
    from rath import sim as S
    sim = None
    with connect() as db:
        row = db.execute("select sim_state from rath_experiments where id=%s", (int(exp_id),)).fetchone()
        sim = (row or {}).get("sim_state")
        # 河道低水位补给:init 只装 12 段,长程仿真烧穿后河道空转=退回 0% 原著重合
        if isinstance(sim, dict) and sim.get("cast") and _sid:
            _refill_from = S.canon_refill_from(sim)
            if _refill_from:
                _more = db.execute(
                    "select chapter, summary from chapter_facts "
                    "where script_id=%s and chapter >= %s and coalesce(summary,'') <> '' "
                    "order by chapter limit 8",
                    (_sid, _refill_from),
                ).fetchall()
                _n_ref = S.extend_canon(sim, [dict(r) for r in (_more or [])])
                if _n_ref:
                    _trace(exp_id, f"原著河道补给:+{_n_ref} 段(第{_refill_from}章起续拉)", clock=new_clock)
    if not isinstance(sim, dict) or not sim.get("cast"):
        sim = S.init_sim_state(snap, [dict(r) for r in (cast_rows or [])],
                              [dict(r) for r in (wb_rows or [])], clock_min=new_clock,
                              canon_beats=[dict(r) for r in (canon_rows or [])])
        _trace(exp_id, f"仿真态初始化:角色 {len(sim['cast'])} 人 · 地点 {len(sim['places'])} 处"
               + f" · 剧情线 {len(sim['threads'])} 条 · 原著河道 {len(sim['canon']['beats'])} 段", clock=new_clock)
    sim["clock_min"] = new_clock
    decayed = S.decay_threads(sim)
    if decayed:
        _trace(exp_id, f"张力衰减:{decayed} 条剧情线 -1(压力需持续喂养)", clock=new_clock)
    forced = S.enforce_night(sim)
    if forced:
        _trace(exp_id, f"时间推进:夜间,{forced} 人转入睡眠", clock=new_clock)

    wrote: list[str] = []
    scene: dict | None = None
    interaction: dict | None = None
    try:
        from agents.recorder import _resolve_recorder_api_and_model
        api_id, model = _resolve_recorder_api_and_model(user_id, None, None)
    except Exception:
        api_id, model = None, None

    if api_id and model:
        from agents._harness import call_agent_json
        # ① 调度(LLM-A):结构化意图
        try:
            sys_p, usr_p = S.build_scheduler_prompts(
                sim, elapsed_hint=elapsed_hint, directive=directive, world_context=world_context)
            data = None
            for _att in (1, 2):  # flash 结构化产出必配验收+重试(铁律)
                text, _u = call_agent_json(api_id, model, sys_p, usr_p, user_id,
                                           tool_schema=None, max_tokens=800, timeout_sec=40,
                                           agent_kind="rath_scheduler")
                data = S.parse_scheduler_output(text or "")
                if data:
                    break
                if _att == 1:
                    _trace(exp_id, "调度:输出不可解析,重试一次", clock=new_clock)
            if data:
                verdict = S.apply_scheduler_output(sim, data, world_context=world_context)
                ap = verdict["applied"]
                _trace(exp_id, f"调度裁决:角色更新 {ap['cast']} 项 · 事件 {len(ap['events'])} 条"
                       + f" · 剧情线 {ap['threads']} 项 · 事实 {ap['facts']} 条"
                       + (" · 拒收:" + ";".join(verdict["rejected"][:3]) if verdict["rejected"] else ""),
                       clock=new_clock)
                wrote = ap["events"]
                interaction = ap.get("interaction")
                if ap.get("canon_advance"):
                    _trace(exp_id, "原著河道:动向成熟,前行一格", clock=new_clock)
                stalled = S.advance_stalled_canon(sim)
                if stalled:
                    _trace(exp_id, "原著河道:滞留强制前行 —— " + stalled[:60], clock=new_clock)
                if interaction:
                    _trace(exp_id, "相遇:" + "×".join(interaction["participants"])
                           + f" @ {interaction['place']} · 缘由:{interaction['reason'][:40]}", clock=new_clock)
            else:
                _trace(exp_id, "调度:输出不可解析,本拍世界静默", clock=new_clock)
        except Exception as exc:
            log.warning("[rath] 调度失败(非致命): %s", exc)
            _trace(exp_id, f"调度失败跳过({str(exc)[:60]})", clock=new_clock)

        # ② 呈现(LLM-B):只演绎已裁决的相遇
        if interaction:
            try:
                sys_p, usr_p = S.build_director_prompts(sim, interaction, elapsed_hint=elapsed_hint)
                scene = None
                for _att in (1, 2):  # flash 结构化产出必配验收+重试(铁律)
                    text, _u = call_agent_json(api_id, model, sys_p, usr_p, user_id,
                                               tool_schema=None, max_tokens=900, timeout_sec=45,
                                               agent_kind="rath_director")
                    scene = S.validate_director_output(text or "", interaction, sim,
                                                       world_context=world_context)
                    if scene:
                        break
                if scene:
                    S.absorb_scene(sim, interaction, scene)
                    _trace(exp_id, "呈现:通过验收 —— " + scene["scene_summary"][:60], clock=new_clock)
                else:
                    _trace(exp_id, "呈现拒收(结构/名词/状态守恒),相遇改记为事实", clock=new_clock)
                    sim.setdefault("facts", []).append(
                        ("、".join(interaction["participants"]) + "在" + interaction["place"]
                         + "见了一面:" + interaction["reason"])[:120])
            except Exception as exc:
                log.warning("[rath] 呈现失败(非致命): %s", exc)
                _trace(exp_id, f"呈现失败跳过({str(exc)[:60]})", clock=new_clock)

    # ── 段3:落库(连接B;sim_state + kb_events + rath 表) ──
    with connect() as db:
        try:
            db.execute("update rath_experiments set sim_state=%s where id=%s",
                       (json.dumps(sim, ensure_ascii=False), int(exp_id)))
            from kb.live_repo import record_event
            for i, it in enumerate(wrote):
                record_event(db, save_id, commit_id, f"rath_hb_{exp_id}_{new_clock}_{i}",
                             summary=it, story_time=_clock_label(new_clock), location=location,
                             metadata={"source": "rath_offline_heartbeat"})
                db.execute("insert into rath_events (exp_id, kind, summary, world_clock_min) values (%s,'heartbeat',%s,%s)",
                           (int(exp_id), it, new_clock))
            if scene and interaction:
                npc_a, npc_b = interaction["participants"][0], interaction["participants"][1]
                record_event(db, save_id, commit_id, f"rath_scene_{exp_id}_{new_clock}",
                             summary=f"{npc_a}与{npc_b}:{scene['scene_summary']}",
                             story_time=_clock_label(new_clock), participants=[npc_a, npc_b],
                             metadata={"source": "rath_npc_scene"})
                db.execute(
                    "insert into rath_events (exp_id, kind, summary, payload, world_clock_min) values (%s,'scene',%s,%s,%s)",
                    (int(exp_id), scene["scene_summary"],
                     json.dumps({"npc_a": npc_a, "npc_b": npc_b,
                                 "transcript": scene["transcript"],
                                 "npc_updates": {n: {"private_memory": v}
                                                 for n, v in (scene.get("private_memories") or {}).items()}},
                                ensure_ascii=False), new_clock))
                db.execute("update rath_experiments set scenes_today = scenes_today + 1 where id=%s",
                           (int(exp_id),))
            if hasattr(db, "commit"):
                db.commit()
        except Exception as exc:
            log.warning("[rath] 落库失败(非致命): %s", exc)
    _trace(exp_id, f"推进完成:事件 {len(wrote)} 条" + (" + 相遇 1 场" if scene else "")
           + f" · 剧情线 {len(sim.get('threads') or [])} 条 @ {_clock_label(new_clock)}", clock=new_clock)
    log.info("[rath] tick exp=%s clock=%s events=%d scene=%s",
             exp_id, _clock_label(new_clock), len(wrote), bool(scene))
    return {"ok": True, "wrote_events": wrote,
            "scene": ({"npc_a": interaction["participants"][0], "npc_b": interaction["participants"][1],
                       "summary": scene["scene_summary"]} if scene and interaction else None),
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
