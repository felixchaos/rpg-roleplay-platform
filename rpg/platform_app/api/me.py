"""platform_app.api.me — /api/me/* 路由 (profile/usage/stats/personas/character-cards/credentials/preference)。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..db import connect
from ..security import public_user
from ._deps import SESSION_COOKIE, json_response, require_user

from psycopg.types.json import Jsonb

router = APIRouter()


# ── 个人主页 ────────────────────────────────────────────────────────
@router.get("/api/me/profile")
async def api_my_profile(request: Request):
    """个人主页一次拉全：账户 + 用量摘要 + 凭证清单 + 偏好"""
    user = require_user(request)
    from .. import usage as usage_mod
    from .. import user_credentials
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
    return json_response({
        "ok": True,
        "user": {k: v for k, v in user.items() if k != "password_hash"},
        "stats": {
            "saves": int(save_count["n"]) if save_count else 0,
            "scripts": int(script_count["n"]) if script_count else 0,
        },
        "usage_30d": usage_mod.aggregate_usage(user["id"], days=30),
        "credentials": user_credentials.list_credentials(user["id"])["items"],
        "preferences": dict(prefs_row["preferences"]) if prefs_row else {},
        "preferences_updated_at": str(prefs_row["updated_at"]) if prefs_row else None,
    })


@router.get("/api/me/usage")
async def api_my_usage(request: Request):
    """单独的用量明细 API（dashboard 用）"""
    user = require_user(request)
    days = int(request.query_params.get("days") or 30)
    from .. import usage as usage_mod
    return json_response(usage_mod.aggregate_usage(user["id"], days=days))


@router.get("/api/me/usage/timeline")
async def api_my_usage_timeline(request: Request):
    """时间序列用量（dashboard 图表用）。group_by=day|model"""
    user = require_user(request)
    from .. import usage as usage_mod
    try:
        return json_response(usage_mod.timeline_usage(
            user["id"],
            days=int(request.query_params.get("days") or 30),
            group_by=request.query_params.get("group_by") or "day",
        ))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/me/stats")
async def api_my_stats(request: Request):
    """玩家档案统计：回合数 / 分支 / 字数 / 连续登录。

    task 49（mock 清扫第二轮）：之前 MeOverview 用 totalRounds = saves.reduce(× 7)、
    playHours = totalRounds × 1.2 / 60，以及 "本周 +6.4h / 最深 6 层 / 共 418 万字 /
    7 天连续登录 / 最长 14 天" 全部硬编码。这里给出全部真实派生值；没有真实
    来源的字段（如累计游玩分钟数）返回 null，由前端显示「—」而不是假数字。
    """
    user = require_user(request)
    cur_token = request.cookies.get(SESSION_COOKIE) or ""
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


@router.post("/api/me/preference")
async def api_set_preference(request: Request):
    """更新或合并界面偏好（主题/字号/默认模型...）"""
    user = require_user(request)
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


# ── 用户级 API 凭证（加密存储，按用户隔离）──────────────────────────────
# ── 用户级 persona / character card（独立于剧本存档）─────────────
@router.get("/api/me/personas")
async def api_my_personas(request: Request):
    """列出本人所有玩家身份卡（杭雁菱穿越者 / 林知意信使 / ...）"""
    user = require_user(request)
    from .. import user_cards
    return json_response(user_cards.list_personas(user["id"]))


@router.post("/api/me/personas")
async def api_upsert_persona(request: Request):
    """创建或更新 persona。传 id 强制更新某条；否则按 slug upsert。"""
    user = require_user(request)
    body = await request.json()
    from .. import user_cards
    try:
        return json_response({"ok": True, "persona": user_cards.upsert_persona(user["id"], body)})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/me/personas/{persona_id}")
async def api_get_persona(request: Request, persona_id: int):
    user = require_user(request)
    from .. import user_cards
    p = user_cards.get_persona(user["id"], persona_id)
    if not p:
        return json_response({"ok": False, "error": "persona 不存在"}, status_code=404)
    return json_response({"ok": True, "persona": p})


@router.post("/api/me/personas/{persona_id}/delete")
async def api_delete_persona(request: Request, persona_id: int):
    user = require_user(request)
    from .. import user_cards
    return json_response(user_cards.delete_persona(user["id"], persona_id))


@router.get("/api/me/character-cards")
async def api_my_character_cards(request: Request):
    """用户自创的 NPC 卡库，可挂任何剧本/存档"""
    user = require_user(request)
    from .. import user_cards
    q = request.query_params.get("q") or None
    enabled = request.query_params.get("enabled") == "1"
    return json_response(user_cards.list_user_cards(user["id"], q=q, enabled_only=enabled))


@router.post("/api/me/character-cards")
async def api_upsert_character_card(request: Request):
    user = require_user(request)
    body = await request.json()
    from .. import user_cards
    try:
        return json_response({"ok": True, "card": user_cards.upsert_user_card(user["id"], body)})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/me/character-cards/{card_id}")
async def api_get_character_card(request: Request, card_id: int):
    user = require_user(request)
    from .. import user_cards
    c = user_cards.get_user_card(user["id"], card_id)
    if not c:
        return json_response({"ok": False, "error": "card 不存在"}, status_code=404)
    return json_response({"ok": True, "card": c})


@router.post("/api/me/character-cards/{card_id}/delete")
async def api_delete_character_card(request: Request, card_id: int):
    user = require_user(request)
    from .. import user_cards
    return json_response(user_cards.delete_user_card(user["id"], card_id))


# ── 酒馆 (SillyTavern) 角色卡兼容 ───────────────────────────────────
@router.post("/api/me/character-cards/import-tavern")
async def api_import_tavern_card(request: Request):
    """导入酒馆角色卡。

    payload 形态（支持多种来源）：
    - {"json": {...V2 dict...}}                # 直接传 V2 对象
    - {"json_string": "{...}"}                  # JSON 字符串
    - {"base64": "..."}                          # base64-encoded JSON
    - {"png_base64": "..."}                      # PNG 文件 base64（解析 tEXt chunk）
    """
    user = require_user(request)
    body = await request.json()
    from .. import tavern_cards, user_cards
    try:
        if body.get("png_base64"):
            import base64 as _b64
            try:
                blob = _b64.b64decode(body["png_base64"], validate=True)
            except Exception as exc:
                raise ValueError(f"png_base64 不合法：{exc}")
            v2 = tavern_cards.parse_png_card(blob)
        elif body.get("json") is not None:
            v2 = tavern_cards.parse_card(body["json"])
        elif body.get("json_string"):
            v2 = tavern_cards.parse_card(body["json_string"])
        elif body.get("base64"):
            v2 = tavern_cards.parse_card(body["base64"])
        else:
            return json_response({"ok": False, "error": "需要 json / json_string / base64 / png_base64 之一"}, status_code=400)

        payload = tavern_cards.tavern_to_user_card(v2)
        card = user_cards.upsert_user_card(user["id"], payload)
        return json_response({"ok": True, "card": card, "imported_from": "tavern_v2"})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/me/character-cards/{card_id}/export-tavern")
async def api_export_tavern_card(request: Request, card_id: int):
    """导出本人 NPC 卡为酒馆 V2 JSON 格式（可直接下载/给酒馆导入）。"""
    user = require_user(request)
    from .. import user_cards, tavern_cards
    card = user_cards.get_user_card(user["id"], card_id)
    if not card:
        return json_response({"ok": False, "error": "card 不存在"}, status_code=404)
    v2 = tavern_cards.user_card_to_tavern_v2(card)
    return json_response({"ok": True, "card": v2, "spec": "chara_card_v2"})


@router.get("/api/me/character-cards/{card_id}/export-png")
async def api_export_tavern_png(request: Request, card_id: int):
    """导出 PNG 嵌入式酒馆卡（tEXt chara chunk），可直接拖进酒馆。"""
    from fastapi.responses import Response
    user = require_user(request)
    from .. import user_cards, tavern_cards
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


@router.get("/api/me/credentials")
async def api_my_credentials(request: Request):
    """列出当前用户已配置的 API 凭证（不含 raw key）"""
    user = require_user(request)
    from .. import user_credentials
    return json_response(user_credentials.list_credentials(user["id"]))


@router.post("/api/me/credentials")
async def api_set_credential(request: Request):
    """设置/更新当前用户某个 provider 的 API key。

    base_url_override 仅 admin 可设；普通用户的 base_url 强制走 catalog。
    """
    user = require_user(request)
    body = await request.json()
    from .. import user_credentials
    is_admin = user.get("role") == "admin"
    try:
        result = user_credentials.set_credential(
            user["id"],
            body.get("api_id", ""),
            body.get("api_key", ""),
            base_url_override=body.get("base_url_override", "") if is_admin else "",
            enabled=bool(body.get("enabled", True)),
            allow_base_url=is_admin,
        )
        return json_response(result)
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/me/credentials/delete")
async def api_delete_credential(request: Request):
    user = require_user(request)
    body = await request.json()
    from .. import user_credentials
    return json_response(user_credentials.delete_credential(user["id"], body.get("api_id", "")))


@router.get("/api/me/credentials/test")
async def api_test_credential(request: Request):
    """用户级凭证可用性自检（不暴露 key）"""
    user = require_user(request)
    api_id = request.query_params.get("api_id", "")
    from .. import user_credentials
    cred = user_credentials.get_credential(user["id"], api_id)
    return json_response({
        "ok": True,
        "api_id": api_id,
        "has_credential": cred is not None,
        "base_url_override": (cred or {}).get("base_url_override", ""),
    })
