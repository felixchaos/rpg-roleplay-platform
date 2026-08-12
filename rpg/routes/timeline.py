"""timeline.py — 存档时间线路由 (/api/saves/:save_id/timeline)。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from platform_app.api._deps import json_response
from routes._deps_fastapi import get_current_user

router = APIRouter()


@router.get("/api/saves/{save_id}/timeline")
async def api_saves_timeline(
    save_id: int,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """返回指定存档的双时间线数据:剧本期望线 + 实际足迹线。

    权限: 必须是该 save 的所有者,否则 403。
    """
    from app import _resolve_persist_target
    # 本地无鉴权时 api_user 可能为 None，回退到 runtime.json 的 user_id
    if api_user:
        user_id = int(api_user["id"])
    else:
        _rt_user_id, _ = _resolve_persist_target(None)  # returns (user_id, save_id)
        user_id = int(_rt_user_id or 0)

    from platform_app.db import connect, init_db
    init_db()

    with connect() as db:
        script_id, active_phase_index = _verify_ownership(db, save_id, user_id)
        script_anchors = _build_script_anchors(db, save_id, script_id)
        save_phases = _build_save_phases(db, save_id)
        current_chapter = _resolve_current_chapter(db, save_id, script_id)
    if not current_chapter or current_chapter < 1:
        current_chapter = 1
    current_chapter = max(1, int(current_chapter))

    return json_response({
        "ok": True,
        "script_anchors": script_anchors,
        "save_phases": save_phases,
        "current_phase_index": active_phase_index,
        "current_chapter": current_chapter,
    })


def _verify_ownership(db, save_id, user_id):
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
    return script_id, active_phase_index


def _build_script_anchors(db, save_id, script_id):
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

    # M10(进度信号矩阵审计):上面「剧本期望线」此前不带任何解锁/度过状态 ——
    # 前端(game-panels.jsx)自行拿 chapter_min/chapter_max 与 current_chapter 做
    # 纯章号比较来判定 isDone/isCurrent/isPending。而同一时间线面板下半「锚点收
    # 束状态」(/api/saves/{id}/anchors 的 recent_pending/recent_occurred)以
    # save_anchor_states.status(occurred/variant/pending/superseded,GM 实际
    # 落库的确定性状态机)为准。两段各按不同判据 → 双源矛盾(章号估算 vs 真实
    # 状态机可能不一致,例如某锚点章号已过但因剧情分叉仍 pending/superseded)。
    # 统一:script_anchors 每行补一个从 save_anchor_states 聚合出的 status 字段,
    # 章号只作展示(前端应改用此字段判定,不再自行按章号比较)。
    # 聚合键用 phase_label(= save_anchor_states.phase_label,同源自
    # chapter_facts.story_phase,与 script_timeline_anchors.story_phase 同一取值域)。
    anchor_status_by_phase: dict[str, dict[str, int]] = {}
    if script_anchors:
        status_rows = db.execute(
            """
            select phase_label,
                   sum(case when status in ('occurred','variant','superseded') then 1 else 0 end) as done_n,
                   sum(case when status = 'pending' then 1 else 0 end) as pending_n,
                   count(*) as total_n
              from save_anchor_states
             where save_id = %s
             group by phase_label
            """,
            (save_id,),
        ).fetchall()
        for r in status_rows:
            anchor_status_by_phase[r["phase_label"] or ""] = {
                "done": int(r["done_n"] or 0),
                "pending": int(r["pending_n"] or 0),
                "total": int(r["total_n"] or 0),
            }
    for a in script_anchors:
        agg = anchor_status_by_phase.get(a["phase_label"])
        if not agg or agg["total"] == 0:
            # 该 phase 无对应 save_anchor_states 记录(未 seed / 剧本无细锚点)——
            # 无真实状态机数据可用,标 unknown,前端回退章号估算展示(不新增判定)。
            a["status"] = "unknown"
        elif agg["pending"] == 0:
            a["status"] = "done"
        elif agg["done"] > 0:
            a["status"] = "current"
        else:
            a["status"] = "pending"
    return script_anchors


def _build_save_phases(db, save_id):
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
    return save_phases


def _resolve_current_chapter(db, save_id, script_id):
    # 4. 当前剧情章节 — 面板高亮的唯一确定性依据 (修复 active_phase_index 恒卡 0)。
    #    active_phase_index 是"实际足迹 phase 序号", 与剧本章节无关 → 拿它当
    #    scriptAnchors 下标永远高亮第 0 个。
    #
    #    M11(进度信号矩阵审计):get_progress_window 是综合了「锚点真实到达
    #    (save_anchor_states occurred/variant/superseded)+ 玩家显式进度
    #    (worldline.progress_chapter)」两路取 max 的单一权威读取器(见
    #    agents/anchor_seed_agent.get_progress_window)。此前本端点绕过它、
    #    自行按 progress_chapter → anchor_chapter_range 顺序判定,与其它读
    #    该权威值的路径(steering/retrieval/reveal)不同源,可能读到不同章号。
    #    改为:get_progress_window 优先(source + chapter_min 都取自它);
    #    它异常/不可用时才回退旧链(progress_chapter → 出生点兜底 → 1)。
    #    ⚠️ v1.73.4:上面这段注释写的是「chapter_min 取自它」,但实现取的是
    #    `last_satisfied_chapter or chapter_min` —— **注释与代码不一致,代码是错的**。
    #    `last_satisfied_chapter` 是 get_progress_window 内部的**锚点单路**中间值
    #    (`max(source_chapter)` over occurred/variant[/superseded]),而 `chapter_min` 才是
    #    「锚点真实到达」与「玩家显式进度 worldline.progress_chapter」**取 max 后**的权威值。
    #    取前者 = 主动丢掉那个 max,于是世界线面板成了唯一读单路信号的消费者,两个症状:
    #      ① 「世界线不变」:玩家 /set 推进到第 68 章,progress_chapter 抬到 68/69、检索与
    #         软引导都跟上了(所以玩家说「召回的和思考时的锚点是根据强制约束来的」),
    #         唯独面板还钉在锚点到达章;
    #      ② 「世界线回退」:锚点集合会因回滚/换分支而缩小,`max(source_chapter)` 随之下降
    #         (生产实证 save 268:曾到 ch64,后回落 occurred 最大 ch58 → 面板显示第 58 章
    #         当前),而 progress_chapter 是 advance_progress 单调只增的,永不回退。
    #    修:source=='progress_chapter'(玩家显式进度赢过锚点)时用 chapter_min;否则维持
    #    锚点章,保持 anchor_pace 开/关两种既有语义不变(pace off 时 chapter_min=last_sat+1,
    #    直接改用它会让所有存档的高亮整体前移一章)。
    current_chapter: int | None = None
    try:
        from agents.anchor_seed_agent import get_progress_window
        win = get_progress_window(save_id, script_id=script_id)
        _last = win.get("last_satisfied_chapter") or 0
        _cmin = win.get("chapter_min") or 0
        _ch = _cmin if win.get("source") == "progress_chapter" else (_last or _cmin)
        if _ch:
            current_chapter = int(_ch)
    except Exception:
        current_chapter = None

    # 回退链(get_progress_window 失败/无结果时):
    #   ① game_sessions.worldline->>'progress_chapter' (mark_anchor_satisfied/
    #      satisfy 端点 advance_progress 写入的权威进度)
    #   ② 出生点兜底:新档首回合前还没有 game_sessions 行(progress_chapter 为空),
    #      但所选出生点已由 workspace._build_initial_snapshot 写进
    #      game_saves.state_snapshot.world.timeline.anchor_chapter_range。读它的起始章作当前章,
    #      否则面板恒退回序章(反馈 #66/#67:游戏实际从选定锚点开始,仅世界线显示错回序章)。
    #   ③ 都没有兜底 1 (剧本开头)。
    if not current_chapter or current_chapter < 1:
        try:
            sess_row = db.execute(
                "select worldline->>'progress_chapter' as pc from game_sessions where save_id = %s",
                (save_id,),
            ).fetchone()
            if sess_row and sess_row.get("pc") is not None:
                current_chapter = int(sess_row["pc"])
        except Exception:
            current_chapter = None

    if not current_chapter or current_chapter < 1:
        try:
            bp = db.execute(
                "select (state_snapshot #>> '{world,timeline,anchor_chapter_range,0}') as ch "
                "from game_saves where id = %s",
                (save_id,),
            ).fetchone()
            if bp and bp.get("ch") is not None:
                current_chapter = int(bp["ch"])
        except Exception:
            pass
    return current_chapter
