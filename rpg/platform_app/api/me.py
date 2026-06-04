"""platform_app.api.me — /api/me/* 路由 (profile/usage/stats/personas/character-cards/credentials/preference)。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request
from psycopg.types.json import Jsonb

from ..db import connect
from ..security import public_user
from ._deps import SESSION_COOKIE, json_response, require_user

router = APIRouter()


# ── 个人主页 ────────────────────────────────────────────────────────
@router.get("/api/me/profile")
async def api_my_profile(user=Depends(require_user)):
    """个人主页一次拉全：账户 + 扩展资料 + 用量摘要 + 凭证清单 + 偏好"""
    from .. import usage as usage_mod
    from .. import user_credentials
    from ..frontend_routes import _ensure_profile_extras_table
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
    username = (body.get("username") or "").strip()[:32]
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
    from .. import usage as usage_mod
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
    from .. import usage as usage_mod
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
    with connect() as db:
        # 剧本汇总
        sc_row = db.execute(
            "select coalesce(count(*), 0) as n, "
            "coalesce(sum(word_count), 0) as words, "
            "coalesce(sum(chapter_count), 0) as chapters "
            "from scripts where owner_id = %s",
            (user["id"],),
        ).fetchone()
        # 存档数
        sv_row = db.execute(
            "select count(*) as n from game_saves where user_id = %s", (user["id"],)
        ).fetchone()
        # 回合数：每个 save 取最大 turn_index 后求和
        rounds_row = db.execute(
            """
            select coalesce(sum(per_save_max), 0) as n from (
              select max(b.turn_index) as per_save_max
              from branch_nodes b join game_saves s on s.id = b.save_id
              where s.user_id = %s
              group by b.save_id
            ) t
            """,
            (user["id"],),
        ).fetchone()
        # 分支节点总数（含主线节点）
        nodes_row = db.execute(
            """
            select count(*) as n
            from branch_nodes b join game_saves s on s.id = b.save_id
            where s.user_id = %s
            """,
            (user["id"],),
        ).fetchone()
        # 分支条数 = 同一父节点下"额外的"子节点（fork 出来的兄弟）
        # 主线一路接龙时 parent_id 唯一 child 不算分支；
        # 真正的 fork 是 parent 有 ≥2 个 child，分支数 = sum(siblings - 1)
        branches_row = db.execute(
            """
            select coalesce(sum(extra), 0) as n from (
              select count(*) - 1 as extra
              from branch_nodes b join game_saves s on s.id = b.save_id
              where s.user_id = %s and b.parent_id is not null
              group by b.parent_id
              having count(*) > 1
            ) t
            """,
            (user["id"],),
        ).fetchone()
        # 最深分支层数：用递归 CTE 算每个 save 的最大深度
        depth_row = db.execute(
            """
            with recursive bn as (
              select b.id, b.save_id, b.parent_id, 1 as depth
              from branch_nodes b join game_saves s on s.id = b.save_id
              where s.user_id = %s and b.parent_id is null
              union all
              select c.id, c.save_id, c.parent_id, bn.depth + 1
              from branch_nodes c join bn on c.parent_id = bn.id
            )
            select coalesce(max(depth), 0) as n from bn
            """,
            (user["id"],),
        ).fetchone()
        # 上次登录：当前 session 之外，最近一次 login_ok
        last_login_row = db.execute(
            """
            select created_at from login_audit
            where username = %s and event = 'login_ok'
            order by created_at desc
            offset 1 limit 1
            """,
            (user.get("username"),),
        ).fetchone()
        # 取最近 365 天的登录日期集合
        days_rows = db.execute(
            """
            select distinct date_trunc('day', created_at at time zone 'UTC')::date as d
            from login_audit
            where username = %s and event = 'login_ok'
              and created_at >= now() - interval '365 days'
            order by d desc
            """,
            (user.get("username"),),
        ).fetchall()
    # 用 Python 算连续登录天数
    from datetime import date, timedelta
    login_days = [r["d"] for r in days_rows]
    today = date.today()
    streak = 0
    if login_days and login_days[0] in (today, today - timedelta(days=1)):
        cur = login_days[0]
        for d in login_days:
            if d == cur:
                streak += 1
                cur = cur - timedelta(days=1)
            elif d < cur:
                break
    longest = 0
    if login_days:
        prev = None
        run = 0
        for d in login_days:  # desc 排序
            if prev is None or (prev - d).days == 1:
                run += 1
            else:
                longest = max(longest, run)
                run = 1
            prev = d
        longest = max(longest, run)
    return json_response({
        "ok": True,
        "imported": {
            "scripts": int(sc_row["n"] or 0),
            "words": int(sc_row["words"] or 0),
            "chapters": int(sc_row["chapters"] or 0),
        },
        "saves_count": int(sv_row["n"] or 0),
        "total_rounds": int(rounds_row["n"] or 0),
        "branch_nodes": int(nodes_row["n"] or 0),
        "branches": int(branches_row["n"] or 0),
        "max_branch_depth": int(depth_row["n"] or 0),
        "last_login_at": last_login_row["created_at"].isoformat() if last_login_row and last_login_row["created_at"] else None,
        "login_streak": int(streak),
        "longest_login_streak": int(longest),
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


@router.post("/api/me/preference")
async def api_set_preference(request: Request, user=Depends(require_user)):
    """更新或合并界面偏好（主题/字号/默认模型...）"""
    body = await request.json()
    # 支持两种写法：整对象覆盖 (replace=true) 或 patch 合并 (默认)
    replace = bool(body.get("replace", False))
    payload = body.get("preferences") if "preferences" in body else body.get("value", body)
    if not isinstance(payload, dict):
        return json_response({"ok": False, "error": "preferences 必须是对象"}, status_code=400)
    with connect() as db:
        if replace:
            row = db.execute(
                """
                insert into user_preferences(user_id, preferences) values (%s, %s)
                on conflict(user_id) do update set preferences = excluded.preferences, updated_at = now()
                returning preferences, updated_at
                """,
                (user["id"], Jsonb(payload)),
            ).fetchone()
        else:
            row = db.execute(
                """
                insert into user_preferences(user_id, preferences) values (%s, %s)
                on conflict(user_id) do update set
                  preferences = user_preferences.preferences || excluded.preferences,
                  updated_at = now()
                returning preferences, updated_at
                """,
                (user["id"], Jsonb(payload)),
            ).fetchone()
    return json_response({"ok": True, "preferences": dict(row["preferences"]), "updated_at": str(row["updated_at"])})


@router.get("/api/gm-style/schema")
async def api_gm_style_schema(user=Depends(require_user)):
    """返回 GM 叙事风格 6 旋钮的 key + 默认值,供前端滑块与后端保持同步。"""
    from agents.gm.style_harness import KNOBS, default_profile
    return json_response({"ok": True, "knobs": list(KNOBS.keys()), "defaults": default_profile()})


@router.get("/api/me/gm-style")
async def api_get_my_gm_style(user=Depends(require_user)):
    """读当前用户级 GM 风格默认(用 schema 默认补全未设的旋钮)。"""
    from agents.gm.style_harness import normalize_profile
    with connect() as db:
        row = db.execute(
            "select preferences from user_preferences where user_id = %s", (user["id"],)
        ).fetchone()
    prefs = (row and dict(row["preferences"])) or {}
    stored = prefs.get("gm_style") if isinstance(prefs.get("gm_style"), dict) else {}
    return json_response({"ok": True, "gm_style": normalize_profile(stored), "stored": stored})


@router.post("/api/me/gm-style")
async def api_set_my_gm_style(request: Request, user=Depends(require_user)):
    """写用户级 GM 风格默认。Body: {"gm_style": {旋钮: 0-100}}。只校验已知 6 键。"""
    from agents.gm.style_harness import validate_patch
    body = await request.json()
    try:
        clean = validate_patch(body.get("gm_style") if "gm_style" in body else body)
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)
    # patch 合并进 preferences.gm_style(保留其它偏好 + 已设旋钮)
    with connect() as db:
        row = db.execute(
            """
            insert into user_preferences(user_id, preferences)
            values (%s, %s)
            on conflict(user_id) do update set
              preferences = jsonb_set(
                coalesce(user_preferences.preferences, '{}'::jsonb), '{gm_style}',
                coalesce(user_preferences.preferences->'gm_style', '{}'::jsonb) || %s, true),
              updated_at = now()
            returning preferences
            """,
            (user["id"], Jsonb({"gm_style": clean}), Jsonb(clean)),
        ).fetchone()
    saved = dict(row["preferences"]).get("gm_style", {}) if row else clean
    return json_response({"ok": True, "gm_style": saved})


# ── 用户级 API 凭证（加密存储，按用户隔离）──────────────────────────────
# ── 用户级 persona / character card（独立于剧本存档）─────────────
@router.get("/api/me/personas")
async def api_my_personas(user=Depends(require_user)):
    """列出本人所有玩家身份卡（杭雁菱穿越者 / 林知意信使 / ...）"""
    from .. import user_cards
    return json_response(user_cards.list_personas(user["id"]))


@router.post("/api/me/personas")
async def api_upsert_persona(request: Request, user=Depends(require_user)):
    """创建或更新 persona。传 id 强制更新某条；否则按 slug upsert。"""
    body = await request.json()
    from .. import user_cards
    try:
        return json_response({"ok": True, "persona": user_cards.upsert_persona(user["id"], body)})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/me/personas/{persona_id}")
async def api_get_persona(persona_id: int, user=Depends(require_user)):
    from .. import user_cards
    p = user_cards.get_persona(user["id"], persona_id)
    if not p:
        return json_response({"ok": False, "error": "persona 不存在"}, status_code=404)
    return json_response({"ok": True, "persona": p})


@router.post("/api/me/personas/{persona_id}/delete")
async def api_delete_persona(persona_id: int, user=Depends(require_user)):
    from .. import user_cards
    return json_response(user_cards.delete_persona(user["id"], persona_id))


@router.get("/api/me/character-cards")
async def api_my_character_cards(q: str | None = None, enabled: str | None = None, user=Depends(require_user)):
    """用户自创的 NPC 卡库，可挂任何剧本/存档"""
    from .. import user_cards
    enabled_only = enabled == "1"
    return json_response(user_cards.list_user_cards(user["id"], q=q or None, enabled_only=enabled_only))


@router.post("/api/me/character-cards")
async def api_upsert_character_card(request: Request, user=Depends(require_user)):
    body = await request.json()
    from .. import user_cards
    try:
        return json_response({"ok": True, "card": user_cards.upsert_user_card(user["id"], body)})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/me/character-cards/{card_id}")
async def api_get_character_card(card_id: int, user=Depends(require_user)):
    from .. import user_cards
    c = user_cards.get_user_card(user["id"], card_id)
    if not c:
        return json_response({"ok": False, "error": "card 不存在"}, status_code=404)
    return json_response({"ok": True, "card": c})


@router.post("/api/me/character-cards/{card_id}/delete")
async def api_delete_character_card(card_id: int, user=Depends(require_user)):
    from .. import user_cards
    return json_response(user_cards.delete_user_card(user["id"], card_id))


# ── 在线角色卡库(PC 卡:发布 / 浏览 / 完整克隆)────────────────────────
@router.post("/api/me/character-cards/{card_id}/visibility")
async def api_set_card_visibility(request: Request, card_id: int, user=Depends(require_user)):
    """作者发布/取消公开自己的 PC 卡到在线角色卡库。Body: {public: bool}。"""
    from .. import user_cards
    body = await request.json()
    try:
        return json_response(user_cards.set_card_public(user["id"], card_id, bool(body.get("public"))))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=403)


@router.get("/api/cards/public")
async def api_list_public_cards(q: str | None = None, limit: int = 30, offset: int = 0, user=Depends(require_user)):
    """在线角色卡库:浏览他人公开的 PC 卡(只列 is_public,不含作者私密 secrets)。"""
    from .. import user_cards
    return json_response(user_cards.list_public_cards(q=q or None, limit=limit, offset=offset))


@router.post("/api/cards/public/{card_id}/clone")
async def api_clone_public_card(card_id: int, user=Depends(require_user)):
    """把一张公开 PC 卡【完整复制】进自己的卡库(复制,非指针)。"""
    from .. import user_cards
    try:
        return json_response(user_cards.clone_public_card(user["id"], card_id))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


# ── 酒馆 (SillyTavern) 角色卡兼容 ───────────────────────────────────
def _truthy(v) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


@router.post("/api/me/character-cards/import-tavern")
async def api_import_tavern_card(request: Request, user=Depends(require_user)):
    """导入酒馆角色卡。

    两种 Content-Type 均支持：
    A) multipart/form-data: 含 "file" 字段（.png/.json/.webp 文件）
    B) application/json payload 形态:
      - {"json": {...V2 dict...}}
      - {"json_string": "{...}"}
      - {"base64": "..."}
      - {"png_base64": "..."}
    """
    from .. import tavern_cards, user_cards
    _MAX_IMPORT_PAYLOAD_BYTES = 16 * 1024 * 1024

    content_type = request.headers.get("content-type", "")
    ai_split = False  # 用户显式 opt-in「AI 整理字段」时才挂 LLM 兜底
    # 整理用模型统一走「设置 → 模型 → card_import」配置(apply_llm_structure 内部解析),
    # 不在导入请求里透传 per-import 模型。
    try:
        # ── multipart/form-data（前端 importTavern(file)）─────────────
        if "multipart/form-data" in content_type:
            form = await request.form()
            ai_split = _truthy(form.get("ai_split"))
            file_field = form.get("file")
            if file_field is None:
                return json_response({"ok": False, "error": "multipart 中缺少 file 字段"}, status_code=400)
            blob = await file_field.read()
            if len(blob) > _MAX_IMPORT_PAYLOAD_BYTES:
                raise ValueError(f"文件过大（上限 {_MAX_IMPORT_PAYLOAD_BYTES // (1024*1024)} MB）")
            fname = getattr(file_field, "filename", "") or ""
            if fname.lower().endswith(".png") or fname.lower().endswith(".webp"):
                v2 = tavern_cards.parse_png_card(blob)
            else:
                # treat as JSON
                try:
                    v2 = tavern_cards.parse_card(blob.decode("utf-8", errors="replace"))
                except Exception as exc:
                    raise ValueError(f"JSON 解析失败：{exc}") from exc
        # ── JSON body ────────────────────────────────────────────────
        else:
            body = await request.json()
            ai_split = _truthy(body.get("ai_split"))
            if body.get("png_base64"):
                import base64 as _b64
                png_b64 = body["png_base64"]
                if not isinstance(png_b64, str) or len(png_b64) > _MAX_IMPORT_PAYLOAD_BYTES:
                    raise ValueError(f"png_base64 过大或非字符串（上限 {_MAX_IMPORT_PAYLOAD_BYTES} 字节）")
                try:
                    blob = _b64.b64decode(png_b64, validate=True)
                except Exception as exc:
                    raise ValueError(f"png_base64 不合法：{exc}") from exc
                if len(blob) > 10 * 1024 * 1024:
                    raise ValueError("PNG 文件过大（解码后最大 10MB）")
                v2 = tavern_cards.parse_png_card(blob)
            elif body.get("json") is not None:
                v2 = tavern_cards.parse_card(body["json"])
            elif body.get("json_string"):
                v2 = tavern_cards.parse_card(body["json_string"])
            elif body.get("base64"):
                v2 = tavern_cards.parse_card(body["base64"])
            else:
                return json_response({"ok": False, "error": "需要 file(multipart) / json / json_string / base64 / png_base64 之一"}, status_code=400)

        payload = tavern_cards.tavern_to_user_card(v2)
        if ai_split:
            # LLM 兜底拆分(同步调用包进线程,失败不阻断导入)。模型走 card_import 统一配置,usage 自动入账。
            try:
                payload, _used = await asyncio.to_thread(
                    tavern_cards.apply_llm_structure, payload, user["id"]
                )
            except Exception:
                pass
        card = user_cards.upsert_user_card(user["id"], payload)
        return json_response({
            "ok": True, "card": card, "imported_from": "tavern_v2",
            "llm_structured": bool((payload.get("metadata") or {}).get("llm_structured_description")),
        })
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/me/character-cards/{card_id}/export-tavern")
async def api_export_tavern_card(card_id: int, user=Depends(require_user)):
    """导出本人 NPC 卡为酒馆 V2 JSON 格式（可直接下载/给酒馆导入）。"""
    from .. import tavern_cards, user_cards
    card = user_cards.get_user_card(user["id"], card_id)
    if not card:
        return json_response({"ok": False, "error": "card 不存在"}, status_code=404)
    v2 = tavern_cards.user_card_to_tavern_v2(card)
    return json_response({"ok": True, "card": v2, "spec": "chara_card_v2"})


@router.get("/api/me/character-cards/{card_id}/export-png")
async def api_export_tavern_png(card_id: int, user=Depends(require_user)):
    """导出 PNG 嵌入式酒馆卡（tEXt chara chunk），可直接拖进酒馆。"""
    from fastapi.responses import Response

    from .. import tavern_cards, user_cards
    card = user_cards.get_user_card(user["id"], card_id)
    if not card:
        return json_response({"ok": False, "error": "card 不存在"}, status_code=404)
    v2 = tavern_cards.user_card_to_tavern_v2(card)
    png = tavern_cards.write_png_card(v2)
    name = (card.get("name") or f"card_{card_id}").replace(" ", "_")
    return Response(
        content=png, media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="{name}.png"'},
    )


@router.post("/api/me/character-cards/import-json")
async def api_import_json_card(request: Request, user=Depends(require_user)):
    """导入 JSON 格式的酒馆角色卡（V1 / V2 均可）。

    payload: {"json": {...V2 dict...}}  或  {"json_string": "..."}
    """
    body = await request.json()
    ai_split = _truthy(body.get("ai_split"))
    from .. import tavern_cards, user_cards
    try:
        if body.get("json") is not None:
            v2 = tavern_cards.parse_card(body["json"])
        elif body.get("json_string"):
            v2 = tavern_cards.parse_card(body["json_string"])
        else:
            return json_response({"ok": False, "error": "需要 json 或 json_string 字段"}, status_code=400)
        payload = tavern_cards.tavern_to_user_card(v2)
        if ai_split:
            try:
                payload, _used = await asyncio.to_thread(
                    tavern_cards.apply_llm_structure, payload, user["id"]
                )
            except Exception:
                pass
        card = user_cards.upsert_user_card(user["id"], payload)
        return json_response({
            "ok": True, "card": card, "imported_from": "tavern_v2",
            "llm_structured": bool((payload.get("metadata") or {}).get("llm_structured_description")),
        })
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


# ── 酒馆聊天记录导入 ──────────────────────────────────────────────────
@router.post("/api/me/chats/import-tavern")
async def api_import_tavern_chat(request: Request, user=Depends(require_user)):
    """导入 SillyTavern 聊天记录 JSONL，新建存档（继续这段对话）。

    payload:
      {"jsonl": "<raw JSONL text>", "title": "可选存档标题"}

    Returns:
      {"ok": true, "save_id": 123, "commits_imported": N,
       "header": {...}, "preview": [first 3 commits]}
    """
    body = await request.json()
    from .. import save_io, tavern_chats

    jsonl_text = body.get("jsonl") or ""
    if not isinstance(jsonl_text, str) or not jsonl_text.strip():
        return json_response({"ok": False, "error": "需要 jsonl 字段（JSONL 字符串）"}, status_code=400)

    custom_title = (body.get("title") or "").strip() or None

    try:
        header, commits = tavern_chats.parse_chat_jsonl(jsonl_text)
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)

    payload = tavern_chats.chat_to_save_payload(header, commits, title=custom_title)

    try:
        result = save_io.import_save(user["id"], payload)
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)

    preview = [
        {"turn": c["turn_index"], "is_gm": bool(c.get("gm_output")), "preview": c.get("content_preview", "")}
        for c in commits[:3]
    ]
    return json_response({
        "ok": True,
        "save_id": result["save_id"],
        "commits_imported": result["commits_imported"],
        "header": header,
        "preview": preview,
    })


# ── 账号级数据导出 / 导入(免部署服务 → 本地自部署 迁移)─────────────────
_MAX_ACCOUNT_IMPORT_BYTES = 300 * 1024 * 1024  # 与 account_io.MAX_ACCOUNT_ZIP_BYTES 对齐


@router.get("/api/me/account/export/estimate")
async def api_account_export_estimate(user=Depends(require_user)):
    """导出前轻量统计:剧本/存档/角色卡/模型条目数量,供前端展示规模。"""
    from .. import account_io
    return json_response(account_io.estimate_account(user["id"]))


@router.get("/api/me/account/export")
async def api_account_export(include_chunks: int = 0, user=Depends(require_user)):
    """聚合本账号全部个人数据为单个 zip 下载(剧本/存档/角色卡/偏好/模型清单)。

    include_chunks=1 时剧本包内含 document_chunks(体积大,默认不含)。不含 API 密钥。
    """
    from urllib.parse import quote as _quote

    from fastapi.responses import Response

    from .. import account_io
    try:
        zip_bytes, filename = account_io.export_account(user["id"], include_chunks=bool(include_chunks))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)
    ascii_fallback = filename.encode("ascii", "ignore").decode("ascii") or "account-export.zip"
    quoted = _quote(filename, safe="")
    cd = f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quoted}"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": cd, "X-Content-Type-Options": "nosniff"},
    )


@router.post("/api/me/account/import")
async def api_account_import(request: Request, user=Depends(require_user)):
    """上传账号数据包 zip,把里面的剧本/存档/角色卡/偏好恢复到当前账号。

    支持 multipart/form-data 字段 file=<.zip>(前端走这条),或直接 application/zip body。
    """
    from fastapi import HTTPException

    from .. import account_io
    content_type = request.headers.get("content-type", "")
    try:
        if "multipart/form-data" in content_type:
            form = await request.form()
            file = form.get("file")
            if not file or not hasattr(file, "read"):
                raise HTTPException(status_code=400, detail="缺 file 字段")
            raw = await file.read()
        else:
            raw = await request.body()
        if not raw:
            raise HTTPException(status_code=400, detail="空文件")
        if len(raw) > _MAX_ACCOUNT_IMPORT_BYTES:
            raise HTTPException(status_code=400, detail=f"文件过大 (>{_MAX_ACCOUNT_IMPORT_BYTES // 1024 // 1024}MB)")
        if raw[:4] != b"PK\x03\x04":
            raise HTTPException(status_code=400, detail="不是合法的 zip 文件")
        # 异步作业:返回 job_id,前端用 streamImport 看真实逐项进度(剧本 i/N…)。
        return json_response(account_io.import_account_job(user["id"], raw))
    except HTTPException:
        raise
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/me/credentials")
async def api_my_credentials(user=Depends(require_user)):
    """列出当前用户已配置的 API 凭证（不含 raw key）"""
    from .. import user_credentials
    return json_response(user_credentials.list_credentials(user["id"]))


@router.post("/api/me/credentials")
async def api_set_credential(request: Request, user=Depends(require_user)):
    """设置/更新当前用户某个 provider 的 API key。

    base_url_override 仅 admin 可设；普通用户的 base_url 强制走 catalog。
    """
    body = await request.json()
    from .. import user_credentials
    is_admin = user.get("role") == "admin"
    try:
        api_id = body.get("api_id", "")
        base_url_override = (body.get("base_url_override") or "").strip()
        if not is_admin:
            from model_registry import (
                default_api_for,
                find_api,
                load_model_catalog,
                normalize_api_id,
            )
            normalized_api_id = normalize_api_id(api_id)
            catalog = load_model_catalog()
            known = bool(find_api(catalog, normalized_api_id) or default_api_for(normalized_api_id))
            # 中转站(第三方 OpenAI 兼容端点): 普通用户也可添加。
            #  · 自定义(未知)provider 必须自带 base_url 指向中转站,否则无从路由;
            #  · 已知 provider 也允许覆盖 base_url(指向自己的中转/代理)。
            # base_url 的 SSRF 防护由下方 set_credential 的 _validate_base_url 兜底
            # (强制 https + 禁私网/本机),不再一刀切拒绝未知 provider。
            if not known and not base_url_override:
                raise ValueError("自定义供应商必须填写 Base URL(中转站地址)")
            api_id = normalized_api_id
        result = user_credentials.set_credential(
            user["id"],
            api_id,
            body.get("api_key", ""),
            base_url_override=base_url_override,
            enabled=bool(body.get("enabled", True)),
            allow_base_url=True,  # base_url 不再 admin 限定;SSRF 由 _validate_base_url 强制
        )
        return json_response(result)
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/me/credentials/delete")
async def api_delete_credential(request: Request, user=Depends(require_user)):
    body = await request.json()
    from .. import user_credentials
    return json_response(user_credentials.delete_credential(user["id"], body.get("api_id", "")))


_PING_CACHE: dict[tuple[int, str], tuple[float, dict]] = {}
_PING_TTL = 60.0  # 60s 内同 user+api_id 的 ping 结果直接复用,防 API 被封


@router.get("/api/me/embedder/status")
async def api_embedder_status(user=Depends(require_user)):
    """task: RAG 模型设置面板 + 导入向导用 — 告诉前端当前 embedder 实际生效路径。

    Returns:
        - is_admin: 用户是否 admin(决定能否走平台兜底)
        - user_configured: 用户自己配了 embedder credential
        - platform_fallback_available: 平台 EMBED_API_KEY 是否配置
        - effective_source: 'user' / 'platform_fallback' / 'none'
        - fallback_active: 当前是否在用平台兜底
        - preflight: embedding_preflight 结果(含 ok/error/hint/last_error_hint 等)
          - ok=False → 用户无可用 embedder,前端应展示引导 Alert
          - last_error_hint → 上次实际 embed 调用失败的友好描述(如 405 地址不支持)
    """
    import os as _os

    from .. import user_credentials
    from ..knowledge.embedding import embedding_preflight
    # task: 享受平台兜底的角色 — admin + vip_user(测试期高级用户)
    is_admin_user = (user.get("role") or "").lower() in ("admin", "vip_user")
    # 用户自己配了 embedder 任一种 provider?
    user_configured = False
    for api_id_alias in ("AgentPlatform", "vertex_ai", "openai", "cohere"):
        if user_credentials.get_credential(user["id"], api_id_alias):
            user_configured = True
            break
    platform_available = bool(_os.environ.get("EMBED_API_KEY"))
    if user_configured:
        effective = "user"
    elif is_admin_user and platform_available:
        effective = "platform_fallback"
    else:
        effective = "none"
    # 调 preflight 拿详细状态(含 last_error_hint/hint/code 等)
    try:
        preflight = embedding_preflight(user["id"])
    except Exception:
        preflight = {"ok": False, "error": "preflight check failed"}
    return json_response({
        "ok": True,
        "is_admin": is_admin_user,
        "user_configured": user_configured,
        "platform_fallback_available": platform_available,
        "effective_source": effective,
        "fallback_active": (effective == "platform_fallback"),
        "preflight": preflight,
    })


@router.get("/api/me/credentials/test")
async def api_test_credential(
    api_id: str = "",
    model: str = "",
    force: bool = False,
    user=Depends(require_user),
):
    """task: 用户级凭证可用性自检 — 实际发一次最小 LLM 调用,
    所有 provider(Vertex / Anthropic / OpenAI-compat)复用 GameMaster.call 路径。

    **throttle**: 同 (user_id, api_id) 60s 内只打一次真实 API,后续返缓存结果。
    `?force=1` 跳过缓存(用户手动点「重新测试」按钮时用)。

    Returns:
      ok=True: 可用,带 latency_ms
      ok=False: 不可用,带 error + error_kind
      cached=True 标记结果来自缓存
    """
    import time as _time

    from .. import user_credentials

    # task: throttle — 同 user+api_id 60s 内复用结果
    cache_key = (int(user["id"]), api_id)
    if not force:
        cached = _PING_CACHE.get(cache_key)
        if cached and (_time.monotonic() - cached[0]) < _PING_TTL:
            return json_response({**cached[1], "cached": True})

    cred = user_credentials.get_credential(user["id"], api_id)
    if cred is None:
        # credential 都没有,直接报「没配 key」
        return json_response({
            "ok": False, "api_id": api_id,
            "has_credential": False,
            "error": "未配置 API key/credential,请先在「API 设置」添加。",
        })

    # 找该 api_id 在 catalog 里的一个 enabled 模型(没传 model 时)
    if not model:
        try:
            from model_registry import find_api, load_model_catalog, normalize_api_id
            catalog = load_model_catalog()
            # credential id (AgentPlatform) → catalog id (vertex_ai)
            catalog_api_id = "vertex_ai" if normalize_api_id(api_id) == "AgentPlatform" else api_id
            api_def = find_api(catalog, catalog_api_id)
            models = (api_def or {}).get("models") or []
            enabled = next((m for m in models if m.get("enabled") is not False), None)
            if not enabled:
                return json_response({
                    "ok": False, "api_id": api_id, "has_credential": True,
                    "error": f"provider {catalog_api_id} 在 catalog 里没有 enabled 模型,无法 ping。",
                })
            model = enabled.get("real_name") or enabled.get("id") or ""
        except Exception as exc:
            return json_response({
                "ok": False, "api_id": api_id, "has_credential": True,
                "error": f"读取 catalog 失败: {type(exc).__name__}: {exc}",
            })

    # 实际打 ping:走 GameMaster.call 跟真实游戏一致
    started = _time.monotonic()
    try:
        from agents.gm import GameMaster
        # 走 GM 路径,user_id 传过去让 BYOK 凭证自动加载
        catalog_api_id = "vertex_ai" if user_credentials.normalize_api_id(api_id) == "AgentPlatform" else api_id
        gm = GameMaster(api_id=catalog_api_id, model=model, user_id=int(user["id"]))
        # 最小调用:max_tokens=1,system 空,user "ping"
        gm._backend.call(system="", messages=[{"role": "user", "content": "ping"}], max_tokens=8)
        elapsed_ms = int((_time.monotonic() - started) * 1000)
        result = {
            "ok": True, "api_id": api_id, "has_credential": True,
            "model": model, "latency_ms": elapsed_ms,
        }
        # task: 缓存成功结果 60s 防被频繁触发
        _PING_CACHE[cache_key] = (_time.monotonic(), result)
        return json_response(result)
    except Exception as exc:
        elapsed_ms = int((_time.monotonic() - started) * 1000)
        msg = str(exc) or type(exc).__name__
        # 简单分类:403 / 401 / quota / network
        kind = "unknown"
        if "403" in msg or "PERMISSION_DENIED" in msg or "forbidden" in msg.lower():
            kind = "permission_denied"
        elif "401" in msg or "unauthorized" in msg.lower() or "invalid api key" in msg.lower():
            kind = "auth_failed"
        elif "quota" in msg.lower() or "429" in msg or "rate" in msg.lower():
            kind = "rate_limited"
        elif "404" in msg or "not found" in msg.lower() or "model" in msg.lower() and "exist" in msg.lower():
            kind = "model_not_found"
        elif "timeout" in msg.lower() or "connection" in msg.lower():
            kind = "network"
        err_result = {
            "ok": False, "api_id": api_id, "has_credential": True,
            "model": model, "latency_ms": elapsed_ms,
            "error": msg[:600], "error_kind": kind,
        }
        # task: 错误结果也缓存 60s,防 403 / 401 等反复触发被封
        _PING_CACHE[cache_key] = (_time.monotonic(), err_result)
        return json_response(err_result)
