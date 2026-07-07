"""rath/briefing.py — 离线世界简报(RATH→游戏的确定性桥,v3)。

「以假乱真」的最后一环:玩家离开期间世界在转(RATH tick 落 kb_events),但玩家回到
游戏时感知不到——召回是被动的。本模块在玩家【回归回合】把离线期间的世界纪要
确定性聚合(零 LLM),作为材料注入 GM:NPC 可以自然提及这些事,世界显得真的活过。

铁律:只读 kb_events(rath_% 产物),不写任何 state;纪要≤700字;确定性拼装。
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

MAX_BRIEF_CHARS = 700
MIN_GAP_MINUTES = 120  # 上次玩家回合距今 < 2h 不注入(连续对话不打扰)


def build_offline_briefing(db, save_id: int) -> str | None:
    """玩家回归回合的离线世界纪要。无新产物/间隔太短 → None。

    窗口=上次玩家消息时间 → 现在;事件按 story_time(世界日)分组,场景(scene)优先、
    心跳(hb)限量;输出确定性文本段供 GM 材料装配。"""
    # 玩家活动时间源:kb_native 档不写 flat messages,回合的权威痕迹是 branch_commits
    # (RATH 铁律离线不写 game state=不建 commit,故 commit 时间恒等于玩家侧活动);
    # messages 兜底旧档,取两者最新。
    last = db.execute(
        """
        select greatest(
            (select max(created_at) from messages where save_id = %(sid)s and role = 'user'),
            (select max(created_at) from branch_commits where save_id = %(sid)s)
        ) as ts
        """,
        {"sid": int(save_id)},
    ).fetchone()
    since = (last or {}).get("ts")
    if since is None:
        return None
    gap = db.execute(
        "select extract(epoch from (now() - %s))/60 as m", (since,)).fetchone()
    if float((gap or {}).get("m") or 0) < MIN_GAP_MINUTES:
        return None
    rows = db.execute(
        r"""
        select logical_key, story_time, summary from kb_events
        where save_id = %s and logical_key like 'rath\_%%'
          and retired_at_commit is null and created_at > %s
          and coalesce(summary, '') <> ''
        order by id asc limit 60
        """,
        (int(save_id), since),
    ).fetchall()
    if not rows:
        return None
    by_day: dict[str, list[str]] = {}
    order: list[str] = []
    for r in rows:
        day = str(r.get("story_time") or "").strip() or "(时间不详)"
        if day not in by_day:
            by_day[day] = []
            order.append(day)
        is_scene = "scene" in str(r.get("logical_key") or "")
        txt = str(r.get("summary") or "").strip()
        if is_scene:
            by_day[day].insert(0, txt[:160])  # 场景优先且给更长篇幅
        elif len(by_day[day]) < 4:
            by_day[day].append(txt[:100])
    lines = ["=== 离线世界纪要(玩家不在场期间,世界自行发生的事) ==="]
    used = len(lines[0])
    for day in order:
        items = by_day[day][:4]
        seg = f"◆ {day}\n" + "\n".join("  · " + t for t in items)
        if used + len(seg) > MAX_BRIEF_CHARS:
            break
        lines.append(seg)
        used += len(seg)
    if len(lines) == 1:
        return None
    lines.append(
        "(以上是已发生的事实。让 NPC 在自然时机提及/谈论它们——寒暄、闲聊、汇报;"
        "不要一口气复述全部,更不要说「你不在的时候」这种打破沉浸的话。)")
    return "\n".join(lines)
