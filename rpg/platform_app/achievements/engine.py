"""成就判定引擎 — 声明式白名单规则 + 统计快照 + 解锁落库。

设计见 docs/design/I_achievements.md。核心不变量:
- rule 只能引用白名单 metric / 白名单 op / 数字 target → admin 改规则也无注入面。
- 进度不落库,只落解锁;解锁只增不减。
"""
from __future__ import annotations

import operator
from datetime import date, timedelta
from typing import Any

# ── 白名单:admin 写规则只能引用这些 ───────────────────────────────────
# Phase 1 全部来自 /api/me/stats 已有数据源;Phase 2 事件埋点后在此追加。
ALLOWED_METRICS = {
    "saves_count",
    "total_rounds",
    "branches",
    "branch_nodes",
    "max_branch_depth",
    "scripts",
    "words",
    "chapters",
    "login_streak",
    "longest_login_streak",
    # Phase 2 事件型(均派生自已有表,无新埋点)
    "max_single_save_rounds",  # 单存档最深回合
    "night_turns",             # 深夜(亚洲/上海 0-5 点)推进的回合
    "anchors_completed",       # 已记录的历史锚点数
}
ALLOWED_OPS = {">=", ">", "=="}
_OPS = {">=": operator.ge, ">": operator.gt, "==": operator.eq}
_MAX_RULE_DEPTH = 3


# ── 统计快照(me.py 与 engine 共用,单一真相) ─────────────────────────
def build_stats_snapshot(db, user) -> dict[str, Any]:
    """汇总玩家真实统计,返回扁平 metric→数值 dict(外加 last_login_at)。

    供 /api/me/stats 与成就判定共用,避免两处查询漂移。
    """
    uid = user["id"]
    username = user.get("username")

    sc_row = db.execute(
        "select coalesce(count(*), 0) as n, "
        "coalesce(sum(word_count), 0) as words, "
        "coalesce(sum(chapter_count), 0) as chapters "
        "from scripts where owner_id = %s",
        (uid,),
    ).fetchone()
    # 回合数取 game_saves.state_snapshot->>'turn'(权威的每存档当前回合,与 saves API
    # 列表展示的 turn 完全同源,见 workspace._SAVE_LIST_COLUMNS)。
    # 注意:历史上这里读 branch_nodes.turn_index,但 branch_nodes 是旧/平行结构、turn_index
    # 未填 → total_rounds 恒为 0(生产实测发现:存档有真实 turn 但统计全 0)。
    sv_row = db.execute(
        "select count(*) as n, "
        "coalesce(sum((state_snapshot->>'turn')::int), 0) as rounds, "
        "coalesce(max((state_snapshot->>'turn')::int), 0) as max_single "
        "from game_saves where user_id = %s",
        (uid,),
    ).fetchone()
    # 分支树统计走 branch_commits(真实提交树:save_id/parent_id/turn_index/created_at)。
    nodes_row = db.execute(
        "select count(*) as n from branch_commits b join game_saves s on s.id = b.save_id "
        "where s.user_id = %s",
        (uid,),
    ).fetchone()
    branches_row = db.execute(
        """
        select coalesce(sum(extra), 0) as n from (
          select count(*) - 1 as extra
          from branch_commits b join game_saves s on s.id = b.save_id
          where s.user_id = %s and b.parent_id is not null
          group by b.parent_id
          having count(*) > 1
        ) t
        """,
        (uid,),
    ).fetchone()
    depth_row = db.execute(
        """
        with recursive bc as (
          select b.id, b.parent_id, 1 as depth
          from branch_commits b join game_saves s on s.id = b.save_id
          where s.user_id = %s and b.parent_id is null
          union all
          select c.id, c.parent_id, bc.depth + 1
          from branch_commits c join bc on c.parent_id = bc.id
        )
        select coalesce(max(depth), 0) as n from bc
        """,
        (uid,),
    ).fetchone()
    # 深夜回合:Asia/Shanghai 0-6 点推进的「不同回合」数(distinct save+turn_index)。
    night_row = db.execute(
        """
        select count(*) as n from (
          select distinct b.save_id, b.turn_index
          from branch_commits b join game_saves s on s.id = b.save_id
          where s.user_id = %s
            and extract(hour from b.created_at at time zone 'Asia/Shanghai') < 6
        ) t
        """,
        (uid,),
    ).fetchone()
    # 已记录历史锚点(save_history_anchors,一行=一个已发生的收束/历史事件)
    anchors_row = db.execute(
        """
        select count(*) as n
        from save_history_anchors a join game_saves s on s.id = a.save_id
        where s.user_id = %s
        """,
        (uid,),
    ).fetchone()
    last_login_row = db.execute(
        """
        select created_at from login_audit
        where username = %s and event = 'login_ok'
        order by created_at desc
        offset 1 limit 1
        """,
        (username,),
    ).fetchone()
    days_rows = db.execute(
        """
        select distinct date_trunc('day', created_at at time zone 'UTC')::date as d
        from login_audit
        where username = %s and event = 'login_ok'
          and created_at >= now() - interval '365 days'
        order by d desc
        """,
        (username,),
    ).fetchall()

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
        for d in login_days:  # desc
            if prev is None or (prev - d).days == 1:
                run += 1
            else:
                longest = max(longest, run)
                run = 1
            prev = d
        longest = max(longest, run)

    return {
        "saves_count": int(sv_row["n"] or 0),
        "total_rounds": int(sv_row["rounds"] or 0),
        "branches": int(branches_row["n"] or 0),
        "branch_nodes": int(nodes_row["n"] or 0),
        "max_branch_depth": int(depth_row["n"] or 0),
        "scripts": int(sc_row["n"] or 0),
        "words": int(sc_row["words"] or 0),
        "chapters": int(sc_row["chapters"] or 0),
        "login_streak": int(streak),
        "longest_login_streak": int(longest),
        "max_single_save_rounds": int(sv_row["max_single"] or 0),
        "night_turns": int(night_row["n"] or 0),
        "anchors_completed": int(anchors_row["n"] or 0),
        "last_login_at": (
            last_login_row["created_at"].isoformat()
            if last_login_row and last_login_row["created_at"]
            else None
        ),
    }


# ── 规则校验(admin 写入闸门) ─────────────────────────────────────────
def validate_rule(rule: Any, _depth: int = 0) -> None:
    """校验声明式规则;非法抛 ValueError。这是阻止越权/可执行规则的关键闸。"""
    if _depth > _MAX_RULE_DEPTH:
        raise ValueError("规则嵌套过深")
    if not isinstance(rule, dict):
        raise ValueError("规则必须是对象")
    if "all" in rule:
        parts = rule["all"]
        if not isinstance(parts, list) or not parts:
            raise ValueError("all 必须是非空数组")
        if set(rule.keys()) - {"all"}:
            raise ValueError("复合规则只能含 all")
        for r in parts:
            validate_rule(r, _depth + 1)
        return
    metric = rule.get("metric")
    op = rule.get("op")
    target = rule.get("target")
    if metric not in ALLOWED_METRICS:
        raise ValueError(f"未知 metric: {metric!r}(白名单:{sorted(ALLOWED_METRICS)})")
    if op not in ALLOWED_OPS:
        raise ValueError(f"未知 op: {op!r}(白名单:{sorted(ALLOWED_OPS)})")
    if isinstance(target, bool) or not isinstance(target, (int, float)):
        raise ValueError("target 必须是数字")


# ── 判定 ──────────────────────────────────────────────────────────────
def eval_rule(rule: dict, snap: dict) -> dict:
    """返回 {unlocked, pct(0-100), value, target}。复合规则 value/target 为 None,pct 取最小子项。"""
    if "all" in rule:
        parts = [eval_rule(r, snap) for r in rule["all"]]
        return {
            "unlocked": all(p["unlocked"] for p in parts),
            "pct": min((p["pct"] for p in parts), default=0),
            "value": None,
            "target": None,
        }
    metric = rule["metric"]
    op = rule["op"]
    target = rule["target"]
    value = snap.get(metric, 0) or 0
    unlocked = _OPS[op](value, target)
    if unlocked:
        pct = 100
    elif target:
        pct = int(min(100, max(0, value * 100 // target)))
    else:
        pct = 100
    return {"unlocked": unlocked, "pct": pct, "value": value, "target": target}


def _project(d: dict, unlocked: bool, res: dict, urow: dict | None,
             *, rarity: dict | None = None, seen: bool = True) -> dict:
    hidden = bool(d["hidden"])
    mask = hidden and not unlocked
    return {
        "id": d["id"],
        "name": "？？？" if mask else d["name"],
        "desc": "隐藏成就" if mask else d["description"],
        "icon": None if mask else d.get("icon"),
        "category": d["category"],
        "tier": d.get("tier"),
        "hidden": hidden,
        "unlocked": unlocked,
        "unlocked_at": (
            urow["unlocked_at"].isoformat() if urow and urow.get("unlocked_at") else None
        ),
        "pct": 100 if unlocked else res["pct"],
        "value": res["value"],
        "target": res["target"],
        "seen": bool(seen),
        "rarity": (rarity.get(d["id"]) if rarity else None),
    }


def compute_rarity(db) -> dict:
    """每条成就的全站解锁占比(%)。rarity[id] = round(100 * 解锁人数 / 总用户数)。"""
    total = db.execute("select count(*) as n from users").fetchone()["n"] or 0
    if not total:
        return {}
    rows = db.execute(
        "select achievement_id, count(*) as n from user_achievements group by achievement_id"
    ).fetchall()
    return {r["achievement_id"]: round(100 * int(r["n"]) / total, 1) for r in rows}


def evaluate(db, user) -> dict:
    """评估全部成就 + 落新解锁。返回 {items, newly_unlocked}。

    每个 item 带 seen(是否已提示过)与 rarity;前端据 unlocked&!seen 弹 toast。
    """
    snap = build_stats_snapshot(db, user)
    rarity = compute_rarity(db)
    defs = db.execute(
        "select * from achievement_defs where enabled order by category, sort_order, id"
    ).fetchall()
    have = {
        r["achievement_id"]: r
        for r in db.execute(
            "select * from user_achievements where user_id = %s", (user["id"],)
        ).fetchall()
    }
    items: list[dict] = []
    newly: list[str] = []
    for d in defs:
        try:
            res = eval_rule(d["rule"], snap)
        except Exception:
            res = {"unlocked": False, "pct": 0, "value": None, "target": None}
        urow = have.get(d["id"])
        already = urow is not None
        seen = bool(urow["seen"]) if already else False
        if res["unlocked"] and not already:
            db.execute(
                "insert into user_achievements (user_id, achievement_id, progress_at_unlock, seen) "
                "values (%s, %s, %s, false) on conflict do nothing",
                (user["id"], d["id"], res.get("value")),
            )
            newly.append(d["id"])
            seen = False  # 刚解锁,未提示
        unlocked = bool(res["unlocked"] or already)
        items.append(_project(d, unlocked, res, urow, rarity=rarity, seen=seen))
    return {"items": items, "newly_unlocked": newly}


def public_catalog(db) -> list[dict]:
    """匿名/公开目录:全锁态、进度 0、隐藏成就打码。无用户、无落库。"""
    rarity = compute_rarity(db)
    defs = db.execute(
        "select * from achievement_defs where enabled order by category, sort_order, id"
    ).fetchall()
    out: list[dict] = []
    for d in defs:
        res = {"unlocked": False, "pct": 0, "value": None, "target": None}
        out.append(_project(d, False, res, None, rarity=rarity))
    return out


def public_wall(db, target_user: dict) -> dict:
    """Phase 3:某用户的公开成就墙投影(只读、不落库)。

    只展示已解锁项的展示信息(隐藏成就解锁后正常显示);未解锁项也返回但打码/无进度。
    调用方需先校验目标用户的公开可见性。
    """
    rarity = compute_rarity(db)
    defs = db.execute(
        "select * from achievement_defs where enabled order by category, sort_order, id"
    ).fetchall()
    have = {
        r["achievement_id"]: r
        for r in db.execute(
            "select * from user_achievements where user_id = %s", (target_user["id"],)
        ).fetchall()
    }
    items: list[dict] = []
    for d in defs:
        urow = have.get(d["id"])
        unlocked = urow is not None
        res = {"unlocked": unlocked, "pct": 100 if unlocked else 0, "value": None, "target": None}
        items.append(_project(d, unlocked, res, urow, rarity=rarity))
    unlocked_n = sum(1 for i in items if i["unlocked"])
    return {"items": items, "unlocked_count": unlocked_n, "total": len(items)}
