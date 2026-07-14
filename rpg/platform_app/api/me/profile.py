"""platform_app.api.me.profile —— 个人主页 / 用量 / 统计 / 最近活动端点。

profile 读写、welcome-dismiss、usage、usage/timeline、stats、activity。纯机械搬家,行为零变化。
"""
from __future__ import annotations

from fastapi import Depends, Request

from ...db import connect
from ...security import normalize_username, public_user
from .._deps import SESSION_COOKIE, json_response, require_user
from ._shared import router


# ── 个人主页 ────────────────────────────────────────────────────────
@router.get("/api/me/profile")
async def api_my_profile(user=Depends(require_user)):
    """个人主页一次拉全：账户 + 扩展资料 + 用量摘要 + 凭证清单 + 偏好"""
    from ... import usage as usage_mod
    from ... import user_credentials
    from ...frontend_routes import _ensure_profile_extras_table
    _ensure_profile_extras_table()
    with connect() as db:
        prefs_row = db.execute(
            "select preferences, updated_at from user_preferences where user_id = %s",
            (user["id"],),
        ).fetchone()
        save_count = db.execute(
            "select count(*) as n from game_saves where user_id = %s", (user["id"],)
        ).fetchone()
        script_count = db.execute(
            "select count(*) as n from scripts where owner_id = %s", (user["id"],)
        ).fetchone()
        extras_row = db.execute(
            "select * from profile_extras where user_id = %s", (user["id"],)
        ).fetchone()
        # 在同一 db 连接内派生 is_co_builder（registration_allowlist join）
        user_public = dict(public_user(user, db=db))
    # 合并 profile_extras 的扩展字段(真名/性别/生日/所在地/网站/代词/语言/时区/邮箱/手机)
    extras = dict(extras_row) if extras_row else {}
    for drop in ("user_id", "visibility", "preferences", "updated_at"):
        extras.pop(drop, None)
    user_public.update({k: v for k, v in extras.items() if v is not None})
    return json_response({
        "ok": True,
        "user": user_public,
        # profile 别名:编辑资料页直接读 .profile,与 frontend_routes 旧形状兼容
        "profile": user_public,
        "stats": {
            "saves": int(save_count["n"]) if save_count else 0,
            "scripts": int(script_count["n"]) if script_count else 0,
        },
        "usage_30d": usage_mod.aggregate_usage(user["id"], days=30),
        "credentials": user_credentials.list_credentials(user["id"])["items"],
        "preferences": dict(prefs_row["preferences"]) if prefs_row else {},
        "preferences_updated_at": str(prefs_row["updated_at"]) if prefs_row else None,
    })


@router.patch("/api/me/profile")
async def api_patch_profile(request: Request, user=Depends(require_user)):
    """首次注册补充昵称用。body: {username?, display_name?, co_builder_opt_out?}"""
    body = await request.json()
    username = normalize_username((body.get("username") or "").strip())[:32]
    display_name = (body.get("display_name") or "").strip()[:64]
    co_builder_opt_out = body.get("co_builder_opt_out")
    if not username and not display_name and co_builder_opt_out is None:
        return json_response({"ok": False, "error": "至少提供 username、display_name 或 co_builder_opt_out"}, status_code=400)
    with connect() as db:
        if username:
            dup = db.execute(
                "select 1 from users where username = %s and id != %s",
                (username, user["id"]),
            ).fetchone()
            if dup:
                return json_response({"ok": False, "error": "用户名已被占用"}, status_code=400)
            db.execute(
                "update users set username = %s, updated_at = now() where id = %s",
                (username, user["id"]),
            )
        if display_name:
            db.execute(
                "update users set display_name = %s, updated_at = now() where id = %s",
                (display_name, user["id"]),
            )
        if co_builder_opt_out is not None:
            db.execute(
                "update users set co_builder_opt_out = %s where id = %s",
                (bool(co_builder_opt_out), user["id"]),
            )
    return json_response({"ok": True})


@router.patch("/api/me/welcome-dismiss")
async def api_welcome_dismiss(user=Depends(require_user)):
    """用户关闭「使用须知」欢迎弹窗后调用，写入 welcome_dismissed_at 时间戳。
    幂等：重复调用仅更新时间戳（上次已 dismiss 的用户手动再打开「使用须知」后关闭时也会调）。
    """
    with connect() as db:
        db.execute(
            "update users set welcome_dismissed_at = now() where id = %s",
            (user["id"],),
        )
    return json_response({"ok": True})


@router.get("/api/me/usage")
async def api_my_usage(
    days: int = 30,
    recent_offset: int = 0,
    user=Depends(require_user),
):
    """单独的用量明细 API（dashboard 用）。

    B2: 返回 forecast 字段（7 天平均日消耗 + 30 天投影 + 趋势百分比）。
    B4: 支持 recent_offset 分页（limit 固定 20）。
    """
    from ... import usage as usage_mod
    data = usage_mod.aggregate_usage(
        user["id"],
        days=days,
        recent_offset=recent_offset,
        recent_limit=20,
    )
    data["forecast"] = usage_mod.forecast_daily_burn(user["id"], days_back=7)
    return json_response(data)


@router.get("/api/me/usage/timeline")
async def api_my_usage_timeline(days: int = 30, group_by: str = "day", user=Depends(require_user)):
    """时间序列用量（dashboard 图表用）。group_by=day|model"""
    from ... import usage as usage_mod
    try:
        return json_response(usage_mod.timeline_usage(
            user["id"],
            days=days,
            group_by=group_by,
        ))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/me/stats")
async def api_my_stats(request: Request, user=Depends(require_user)):
    """玩家档案统计：回合数 / 分支 / 字数 / 连续登录。

    task 49（mock 清扫第二轮）：之前 MeOverview 用 totalRounds = saves.reduce(× 7)、
    playHours = totalRounds × 1.2 / 60，以及 "本周 +6.4h / 最深 6 层 / 共 418 万字 /
    7 天连续登录 / 最长 14 天" 全部硬编码。这里给出全部真实派生值；没有真实
    来源的字段（如累计游玩分钟数）返回 null，由前端显示「—」而不是假数字。
    保留 request：需要读 request.cookies.get(SESSION_COOKIE) 用于 login_audit 查询。
    """
    request.cookies.get(SESSION_COOKIE) or ""
    # 统计查询统一抽到 achievements.engine.build_stats_snapshot,
    # 与成就判定共用同一真相,避免两处 SQL 漂移(task 127/128)。
    from ...achievements import build_stats_snapshot
    with connect() as db:
        snap = build_stats_snapshot(db, user)
    return json_response({
        "ok": True,
        "imported": {
            "scripts": snap["scripts"],
            "words": snap["words"],
            "chapters": snap["chapters"],
        },
        "saves_count": snap["saves_count"],
        "total_rounds": snap["total_rounds"],
        "branch_nodes": snap["branch_nodes"],
        "branches": snap["branches"],
        "max_branch_depth": snap["max_branch_depth"],
        "last_login_at": snap["last_login_at"],
        "login_streak": snap["login_streak"],
        "longest_login_streak": snap["longest_login_streak"],
        # 没有真实数据源的字段：显式 null，由 UI 显示 "—"，禁止编造
        "play_minutes_total": None,
        "play_minutes_week": None,
    })


@router.get("/api/me/activity")
async def api_my_activity(limit: int = 25, user=Depends(require_user)):
    """个人主页「最近活动」时间线：聚合真实事件，按时间倒序返回最近 limit 条。

    数据源（全部真实表，禁止编造）:
      - 回合: branch_nodes (role='gm'，每回合一条) join game_saves
      - 分支: branch_nodes 中 fork 出的兄弟节点（同 parent 的非首个 child）
      - 剧本: scripts 导入记录
    """
    limit = max(1, min(int(limit or 25), 100))
    events: list[dict] = []
    with connect() as db:
        # 回合：GM 节点 = 一回合完成
        for r in db.execute(
            """
            select b.turn_index, b.summary, b.created_at, b.save_id, s.title as save_title
            from branch_nodes b join game_saves s on s.id = b.save_id
            where s.user_id = %s and b.role = 'gm'
            order by b.created_at desc limit %s
            """,
            (user["id"], limit),
        ).fetchall():
            save_title = r["save_title"] or "未命名存档"
            events.append({
                "type": "turn", "tag": "回合", "icon": "play",
                "text": f"在《{save_title}》推进到第 {int(r['turn_index'])} 回合",
                "sub": (r["summary"] or "")[:60],
                "ts": r["created_at"].isoformat() if r["created_at"] else None,
                "save_id": r["save_id"],
            })
        # 分支：同一 parent 下 fork 出的兄弟（非首个 child 即为新开分支）
        for r in db.execute(
            """
            with sib as (
              select b.id, b.save_id, b.turn_index, b.created_at, b.parent_id,
                     s.title as save_title,
                     row_number() over (partition by b.parent_id order by b.created_at, b.id) as rn,
                     count(*) over (partition by b.parent_id) as cnt
              from branch_nodes b join game_saves s on s.id = b.save_id
              where s.user_id = %s and b.parent_id is not null
            )
            select save_id, turn_index, created_at, save_title
            from sib where cnt > 1 and rn > 1
            order by created_at desc limit %s
            """,
            (user["id"], limit),
        ).fetchall():
            save_title = r["save_title"] or "未命名存档"
            events.append({
                "type": "branch", "tag": "分支", "icon": "branch",
                "text": f"在《{save_title}》第 {int(r['turn_index'])} 回合开辟新分支",
                "sub": "",
                "ts": r["created_at"].isoformat() if r["created_at"] else None,
                "save_id": r["save_id"],
            })
        # 剧本导入
        for r in db.execute(
            """
            select id, title, chapter_count, word_count, created_at
            from scripts where owner_id = %s
            order by created_at desc limit %s
            """,
            (user["id"], limit),
        ).fetchall():
            wc = int(r["word_count"] or 0)
            cc = int(r["chapter_count"] or 0)
            parts = []
            if cc:
                parts.append(f"{cc} 章")
            if wc:
                parts.append(f"{wc / 10000:.1f} 万字" if wc >= 10000 else f"{wc} 字")
            events.append({
                "type": "script", "tag": "剧本", "icon": "book",
                "text": f"导入剧本《{r['title'] or '未命名'}》",
                "sub": " · ".join(parts),
                "ts": r["created_at"].isoformat() if r["created_at"] else None,
                "script_id": r["id"],
            })
    events = [e for e in events if e["ts"]]
    events.sort(key=lambda e: e["ts"], reverse=True)
    return json_response({"ok": True, "activity": events[:limit]})
