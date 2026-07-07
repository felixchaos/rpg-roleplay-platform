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
CAST_RANK_TOP_N = 5  # D1:剧本内 importance 排名 top N(不看绝对值)
LOCATION_RANK_TOP_N = 15  # D2:地点白名单独立源 top N


def _ranked_top_names(imp: dict[str, int], limit: int = CAST_RANK_TOP_N) -> list[str]:
    """D1(P0):importance 排名化,废除绝对阈值。

    审计实锤:character_cards.importance 量纲因剧本是否被 RC1 重提取而分裂两派——
    新提取 score*1000+1(量程约1-1001),旧提取=裸计次(常见个位数到两位数)。绝对线
    >=100 曾把全平台存档数最高的剧本(无职转生,172张卡)砍到只剩1个NPC。
    只看该剧本内相对排名:top N 且 importance>0,按 importance 降序。纯函数。"""
    ranked = sorted(
        ((n, int(v or 0)) for n, v in (imp or {}).items() if int(v or 0) > 0),
        key=lambda kv: -kv[1],
    )[:max(0, int(limit))]
    return [n for n, _ in ranked]


def _load_canon_beats(db, script_id: int, chapter_from: int, chapter_to: int | None = None,
                      *, limit: int = 12) -> dict:
    """D3:河道退化阶梯——chapter_facts 优先;为空则回退 script_timeline_anchors(剧本级
    聚合锚点表,sample_summary/sample_title 充当摘要文本);两者皆空=自由演化(rows=[])。

    返回形状与既有 canon_rows 消费一致:{"rows": [{"chapter": int, "summary": str}, ...],
    "source": "chapter_facts" | "anchors" | "none"}。tick 初次装配与 canon_refill 续拉
    共用本函数,避免退化逻辑散落两处各写一份、日后一处修另一处忘。

    chapter_to=None 时不设上界(续拉场景:只要凑够 limit 条,不管跨多少章);
    传入 chapter_to 时按 [chapter_from, chapter_to] 闭区间(初次装配场景,窗口对齐进度)。
    """
    if not script_id:
        return {"rows": [], "source": "none"}
    if chapter_to is None:
        cf_sql = ("select chapter, summary from chapter_facts where script_id=%s and chapter>=%s "
                  "and coalesce(summary,'') <> '' order by chapter limit %s")
        cf_args = (script_id, chapter_from, limit)
        an_sql = ("select chapter_min as chapter, "
                  "coalesce(nullif(sample_summary,''), sample_title) as summary "
                  "from script_timeline_anchors where script_id=%s and chapter_min>=%s "
                  "and (coalesce(sample_summary,'')<>'' or coalesce(sample_title,'')<>'') "
                  "order by chapter_min limit %s")
        an_args = (script_id, chapter_from, limit)
    else:
        cf_sql = ("select chapter, summary from chapter_facts where script_id=%s "
                  "and chapter between %s and %s and coalesce(summary,'') <> '' "
                  "order by chapter limit %s")
        cf_args = (script_id, chapter_from, chapter_to, limit)
        an_sql = ("select chapter_min as chapter, "
                  "coalesce(nullif(sample_summary,''), sample_title) as summary "
                  "from script_timeline_anchors where script_id=%s "
                  "and chapter_min between %s and %s "
                  "and (coalesce(sample_summary,'')<>'' or coalesce(sample_title,'')<>'') "
                  "order by chapter_min limit %s")
        an_args = (script_id, chapter_from, chapter_to, limit)
    rows = db.execute(cf_sql, cf_args).fetchall()
    if rows:
        return {"rows": [dict(r) for r in rows], "source": "chapter_facts"}
    arows = db.execute(an_sql, an_args).fetchall()
    if arows:
        return {"rows": [dict(r) for r in arows], "source": "anchors"}
    return {"rows": [], "source": "none"}


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
        "save_kind": row.get("save_kind"),
        "pause_reason": row.get("pause_reason"),
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


def _resolve_progress(save_id: int, script_id: int) -> int:
    """预检用轻量进度估计(权威=平台 get_progress_window,与 tick_experiment 段1同一口径;
    解析失败静默回退第1章——预检允许粗略,绝不能因此报错阻断)。"""
    try:
        from agents.anchor_seed_agent import get_progress_window as _gpw
        pw = _gpw(int(save_id), script_id=int(script_id), window_size=12) if script_id else None
        if pw and pw.get("chapter_min"):
            return int(pw["chapter_min"])
    except Exception:
        pass
    return 1


def _compute_preflight(db, user_id: int, save_id: int) -> dict:
    """D4/D5:预检核心判定。preflight()(只读探测)与 create_experiment()(建前拒建闸)
    共用本函数——防止「预检说能建、真建又用另一套口径拒绝」的双标准漂移。

    调用方须已持有一条开着的 db 连接(遵循 engine.py 既有的三段式连接纪律)。
    契约见 scratchpad/rath_v4_contracts.md:字段一个不能少/拼错。"""
    save = db.execute(
        "select id, user_id, script_id, save_kind from game_saves where id=%s", (int(save_id),)
    ).fetchone()
    if not save or int(save["user_id"]) != int(user_id):
        return {"ok": False, "error": "存档不存在或不属于你"}
    sid = int(save.get("script_id") or 0)
    save_kind = str(save.get("save_kind") or "game")

    river = {"beats": 0, "source": "none"}
    cast_count = 0
    wb_count = 0
    loc_count = 0
    if sid:
        _prog = _resolve_progress(int(save_id), sid)
        _canon = _load_canon_beats(db, sid, _prog, _prog + 11, limit=12)
        river = {"beats": len(_canon["rows"]), "source": _canon["source"]}
        _imp_rows = db.execute(
            "select name, coalesce(importance,0) i from character_cards where script_id=%s",
            (sid,),
        ).fetchall()
        _imp = {r["name"]: int(r["i"]) for r in (_imp_rows or [])}
        # D1 同款排名算法(不是绝对阈值)——preflight 的「卡司够不够」判定必须与 tick 实际
        # 会拿到的卡司数一致,否则「预检说够、真建才发现地板清零」的旧病复发。
        cast_count = len(_ranked_top_names(_imp, CAST_RANK_TOP_N))
        wb_count = int((db.execute(
            "select count(*) c from worldbook_entries where script_id=%s and enabled",
            (sid,),
        ).fetchone() or {}).get("c") or 0)
        # D2:地点独立源(kb_canon_entities type=location)总量,供 warnings 提示;
        # 不做 tier 判定输入(tier 只按 D4 契约的河道+卡司+世界书三项)。
        loc_count = int((db.execute(
            "select count(*) c from kb_canon_entities where script_id=%s and type='location'",
            (sid,),
        ).fetchone() or {}).get("c") or 0)

    if not sid:
        tier = "free"
    elif river["source"] == "chapter_facts" and cast_count >= 3 and wb_count >= 3:
        tier = "full"
    else:
        tier = "degraded"

    warnings: list[str] = []
    if sid:
        if river["source"] == "none":
            warnings.append("本剧本无章节事实也无时间线锚点,河道将完全依赖自由演化")
        elif river["source"] == "anchors":
            warnings.append(f"本剧本无章节事实,河道将回退为时间线锚点({river['beats']}条)")
        if cast_count < 3:
            warnings.append(f"角色卡不足(仅{cast_count}人达标),离线戏可能单薄")
        if wb_count < 3:
            warnings.append(f"世界书条目不足(仅{wb_count}条),世界观细节可能欠缺")
        if loc_count == 0:
            warnings.append("无独立地点条目,场景可能局限于出生点")

    can_create = True
    reason = ""
    if save_kind == "tavern":
        can_create = False
        reason = "酒馆存档暂不支持离线世界"

    return {
        "ok": True, "can_create": can_create, "reason": reason, "tier": tier,
        "river": river, "cast": {"count": cast_count}, "worldbook": {"count": wb_count},
        "locations": {"count": loc_count}, "warnings": warnings,
    }


def preflight(user_id: int, save_id: int) -> dict:
    """GET /api/rath/preflight 底层实现(D4)。自行管理连接。"""
    from platform_app.db import connect, init_db
    init_db()
    with connect() as db:
        out = _compute_preflight(db, user_id, save_id)
        if hasattr(db, "commit"):
            db.commit()
    return out


def create_experiment(user_id: int, save_id: int) -> dict:
    from platform_app.db import connect, init_db
    init_db()
    with connect() as db:
        # D5:复用 preflight 判定——can_create=false(酒馆/不属于你)直接拒建,
        # 不留「tick 每次崩溃后静默重试烧预算」的旧路(P0 finding1 同根)。
        pf = _compute_preflight(db, user_id, save_id)
        if not pf.get("ok"):
            return {"ok": False, "error": pf.get("error") or "存档不存在或不属于你"}
        if not pf.get("can_create"):
            return {"ok": False, "error": pf.get("reason") or "该存档暂不支持创建离线实验"}
        save = db.execute(
            "select id, user_id, script_id, save_kind from game_saves where id=%s", (int(save_id),)
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
        # v4.1(269 浸泡实锤):世界钟从 0=午夜起步,新实验前 6 拍全落夜律窗口(全员安睡
        # 零场景)=第一印象灾难。改从第1日 08:00(480min)清晨起步,建档即有戏可看。
        row = db.execute(
            """insert into rath_experiments (user_id, save_id, script_id, status, last_tick_at,
                                             world_clock_min)
               values (%s, %s, %s, 'running', now(), 480) returning *""",
            (int(user_id), int(save_id), save.get("script_id")),
        ).fetchone()
        if hasattr(db, "commit"):
            db.commit()
        row = dict(row)
        row["save_kind"] = save.get("save_kind")
        return {"ok": True, "experiment": _expose(row), "warnings": pf.get("warnings") or []}


def _claim_tick(db, exp_id: int, *, manual: bool) -> dict | None:
    """原子认领一拍:CAS 推进 last_tick_at/world_clock_min/ticks_today。
    输家(未到期/已被别的 worker 认领/预算尽/非 running)得 None。
    返回 {new_clock, old_clock, save_id, user_id, exp_id, ticks_today, scenes_today, accel}。"""
    due_cond = "" if manual else \
        " and (t.last_tick_at is null or t.last_tick_at < now() - (interval '1 second' * t.tick_interval_sec))"
    status_cond = "t.status in ('running','paused')" if manual else "t.status = 'running'"
    # P0(A2,世界钟封顶):暂停/72h 自动暂停后 resume,若 elapsed 无上限直接乘 accel(最高
    # 240x),banking 出的世界时会一拍暴涨(3天暂停×240≈30世界日)。双保险:①resume/pause
    # 路径统一冻结 last_tick_at=now()(防 banking 发生);②这里对 elapsed 本身再加硬顶——
    # raw_elapsed_sec 封 tick_interval_sec*4,advance_min 整体再封 4320(3 世界日绝对上限)。
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
                   least(
                       (least(
                           extract(epoch from (now() - coalesce(prev.last_tick_at, now())))::bigint,
                           t.tick_interval_sec * 4
                       ) * t.accel) / 60,
                       4320
                   )
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
    # ── 段0:模型预检(A4,no_model 降级)── 认领会推进世界钟(last_tick_at/world_clock_min),
    # 若放在认领之后才发现无可用模型,钟已经白白走了一拍。预检不可解析就根本不认领。
    status_cond = "status in ('running','paused')" if manual else "status = 'running'"
    api_id, model = None, None
    with connect() as db:
        pre = db.execute(
            f"select user_id from rath_experiments where id=%s and {status_cond}", (int(exp_id),)
        ).fetchone()
        if pre:
            try:
                from agents.recorder import _resolve_recorder_api_and_model
                api_id, model = _resolve_recorder_api_and_model(int(pre["user_id"]), None, None)
            except Exception:
                api_id, model = None, None
            if not api_id or not model:
                db.execute(
                    "update rath_experiments set status='paused', pause_reason='no_model', "
                    "paused_at=now(), last_tick_at=now() "
                    "where id=%s and status in ('running','paused')",
                    (int(exp_id),),
                )
                if hasattr(db, "commit"):
                    db.commit()
                _trace(exp_id, "无可用模型,已自动暂停,请检查模型凭据")
                return {"ok": False, "reason": "no_model"}
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
        # P0(A1):canon_rows 与 cast_rows 同点预置——_sid=0(无脚本/酒馆/自由档)时
        # 下面整个 `if _sid:` 块被跳过、也不触发 except,canon_rows 曾经从未绑定就被
        # 引用(NameError)。自由演化模式不该被拒建,预置空列表让它正常走"无剧本"路径。
        cast_rows = []
        canon_rows = []
        canon_locations: list[str] = []
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
                    # D1(P0):排名化,不用绝对阈值(见 _ranked_top_names 文档字符串)。
                    _top_names = _ranked_top_names(_imp, CAST_RANK_TOP_N)
                    _ranked = [(n, _cards_all[n]) for n in _top_names if n in _cards_all]
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
                # 原著河道(D3 退化阶梯):chapter_facts 优先,空则回退时间线锚点。
                _canon = _load_canon_beats(db, _sid, _prog, _prog + 11, limit=12)
                canon_rows = _canon["rows"]
                # D2:地点白名单独立源(审计实锤:约半数已玩剧本世界书标题不含「地点」类
                # 关键词,即便确有地点条目也几乎挤不进世界观要点的 top-6 切片——无职转生
                # 5条地点条目、id=11 33条地点条目,均0条进前6)。改读 kb_canon_entities
                # type='location',reveal 窗口对齐世界书同款口径(+3);旧 wb 标题关键词
                # 路径(sim.py init_sim_state 内)仍保留作补充,不互斥。
                try:
                    _loc_rows = db.execute(
                        "select name from kb_canon_entities where script_id=%s "
                        "and type='location' and coalesce(first_revealed_chapter,0) <= %s "
                        "order by importance desc, id asc limit %s",
                        (_sid, _prog + 3, LOCATION_RANK_TOP_N),
                    ).fetchall()
                    canon_locations = [str(r.get("name") or "").strip()
                                       for r in (_loc_rows or []) if r.get("name")]
                except Exception:
                    canon_locations = []
        except Exception:
            cast_rows = []
            canon_rows = []
            canon_locations = []
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
                # D3:续拉同样走 _load_canon_beats(chapter_facts 优先,回退时间线锚点),
                # 与初次装配单一来源,不再各写一份退化逻辑。
                _cb2 = _load_canon_beats(db, _sid, _refill_from, limit=8)
                _n_ref = S.extend_canon(sim, _cb2["rows"])
                if _n_ref:
                    _src_label = "锚点" if _cb2["source"] == "anchors" else "章节事实"
                    _trace(exp_id, f"原著河道补给:+{_n_ref} 段({_src_label},第{_refill_from}章起续拉)",
                           clock=new_clock)
    if not isinstance(sim, dict) or not sim.get("cast"):
        sim = S.init_sim_state(snap, [dict(r) for r in (cast_rows or [])],
                              [dict(r) for r in (wb_rows or [])], clock_min=new_clock,
                              canon_beats=[dict(r) for r in (canon_rows or [])],
                              canon_locations=list(canon_locations or []))
        _trace(exp_id, f"仿真态初始化:角色 {len(sim['cast'])} 人 · 地点 {len(sim['places'])} 处"
               + f" · 剧情线 {len(sim['threads'])} 条 · 原著河道 {len(sim['canon']['beats'])} 段", clock=new_clock)
    sim["clock_min"] = new_clock
    decayed = S.decay_threads(sim)
    if decayed:
        _trace(exp_id, f"张力衰减:{decayed} 条剧情线 -1(压力需持续喂养)", clock=new_clock)
    closed = S.close_stale_threads(sim)
    if closed:
        _trace(exp_id, "剧情线平息:" + ";".join(c[:30] for c in closed), clock=new_clock)
    forced = S.enforce_night(sim)
    if forced:
        _trace(exp_id, f"时间推进:夜间,{forced} 人转入睡眠", clock=new_clock)

    wrote: list[str] = []
    scene: dict | None = None
    interaction: dict | None = None
    # api_id/model 已在段0预检阶段解析(A4);不可解析的路径在预检就已 return,这里必然可用
    # (除非 pre 查不到该行,api_id/model 沿用段0的 None, None,下面自然跳过 LLM 两段)。

    if api_id and model:
        from agents._harness import call_agent_json
        # ① 调度(LLM-A):结构化意图
        try:
            sys_p, usr_p = S.build_scheduler_prompts(
                sim, elapsed_hint=elapsed_hint, directive=directive, world_context=world_context)
            data = None
            for _att in (1, 2):  # flash 结构化产出必配验收+重试(铁律)
                # 500k 浸泡实锤:800 顶格截断率 40/68 → 66% 拍静默。中文结构化 JSON
                # (4人cast+threads+facts)800 装不下,扩容是根因修,重试只是保险丝。
                text, _u = call_agent_json(api_id, model, sys_p, usr_p, user_id,
                                           tool_schema=None, max_tokens=1600, timeout_sec=60,
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
                # P0(A3):MAX_SCENES_PER_DAY 声明多年从未强制——单实验单日理论可达
                # MAX_TICKS_PER_DAY(48)场,超设计预算 4 倍(director LLM 调用+kb_events
                # scene 写入同比放大)。此处让预算真正生效:超额只丢弃呈现(interaction),
                # cast_updates/facts 已经 apply_scheduler_output 过、照常保留。
                if interaction and int(claim.get("scenes_today") or 0) >= MAX_SCENES_PER_DAY:
                    _trace(exp_id, f"预算闸:今日场景已达上限({MAX_SCENES_PER_DAY}),"
                           "本拍相遇改记为事实、不再演绎", clock=new_clock)
                    sim.setdefault("facts", []).append(
                        ("、".join(interaction["participants"]) + "在" + interaction["place"]
                         + "见了一面:" + interaction["reason"])[:120])
                    interaction = None
                # 河道前行的动向并入事件流(浸泡实锤:只进 facts 池=情景召回看不到
                # 原著进程,违反 RATH 产物 kb_events 闭环铁律)
                if ap.get("canon_advance"):
                    _trace(exp_id, "原著河道:动向成熟,前行一格", clock=new_clock)
                    if ap.get("canon_text"):
                        wrote = wrote + ["【原著进程】" + str(ap["canon_text"])[:120]]
                stalled = S.advance_stalled_canon(sim)
                if stalled:
                    _trace(exp_id, "原著河道:滞留强制前行 —— " + stalled[:60], clock=new_clock)
                    wrote = wrote + ["【原著进程】" + stalled[:120]]
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
                                               tool_schema=None, max_tokens=1400, timeout_sec=60,
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
            # P1(A9):段1 认领时读的 commit_id 到这里(LLM 等待 60-100s 后)可能已经被玩家侧
            # rewind/切分支抢先,若原样写入 record_event,born_commit 挂在被抛弃的谱系上,对
            # 新 active 分支的祖先 CTE 永久不可见(孤儿 kb_event)。落库前重读一次拿最新值。
            _fresh = db.execute(
                "select active_commit_id from game_saves where id=%s", (int(save_id),)
            ).fetchone()
            _fresh_cid = int((_fresh or {}).get("active_commit_id") or 0)
            if _fresh_cid and _fresh_cid != commit_id:
                _trace(exp_id, f"落库前发现分支已切换(commit {commit_id}→{_fresh_cid}),改用最新值",
                       clock=new_clock)
                commit_id = _fresh_cid
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
        # 72h 无人看自动暂停(A2/A7):pause_reason='unviewed',与手动 pause('user')/
        # no_model 预检/player_active 回归暂停各自独立枚举。last_tick_at/paused_at 同置
        # now() —— 防止 resume 后按整段暂停时长 banking 世界时(finding2 核心修复)。
        db.execute(
            "update rath_experiments set status='paused', pause_reason='unviewed', "
            "paused_at=now(), last_tick_at=now() "
            "where status='running' and last_viewed_at < now() - (interval '1 hour' * %s)",
            (AUTO_PAUSE_UNVIEWED_HOURS,),
        )
        # 玩家回合自动暂停(routes/game.py api_chat hook)的对偶恢复:玩家离场约 2 小时
        # (该 save 最新 branch_commit 距今 ≥2h)后,世界自动继续——同样冻结 last_tick_at。
        resumed = db.execute(
            """
            update rath_experiments t
               set status='running', pause_reason=null, paused_at=null, last_tick_at=now()
             where t.status='paused' and t.pause_reason='player_active'
               and (select max(bc.created_at) from branch_commits bc where bc.save_id = t.save_id)
                   < now() - interval '2 hours'
            returning t.id
            """,
        ).fetchall()
        due = db.execute(
            """select id from rath_experiments
               where status='running'
                 and (last_tick_at is null or last_tick_at < now() - (interval '1 second' * tick_interval_sec))
               order by last_tick_at asc nulls first limit 4""",
        ).fetchall()
        if hasattr(db, "commit"):
            db.commit()
    for r in (resumed or []):
        _trace(int(r["id"]), "玩家离开约2小时,世界继续")
    ticked = 0
    for r in (due or []):
        try:
            if tick_experiment(int(r["id"])).get("ok"):
                ticked += 1
        except Exception as exc:
            log.warning("[rath] tick %s 失败(非致命): %s", r["id"], exc)
    # P3(A11):trace 裁剪只在本轮确有实验被推进时才执行,避免功能全量开放后
    # 与运行中实验数×trace行数成正比的常驻 60 秒周期负载。
    if ticked > 0:
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
    return ticked
