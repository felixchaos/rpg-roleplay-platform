"""kb/view.py — 读路径合并:规范层(钉死,进度过滤)∪ 活态层(当前分支 newest-per-key)。

GM 注入与查询工具共用。活态覆盖规范(玩家改了 NPC 状态以玩家版为准)。
设计 BC_kb_schema_worldtree.md §4 + D_gm_serving.md §7/§8。
"""
from __future__ import annotations

from kb import canon_repo, live_repo
from kb.canon_repo import ForeknowledgeMode


def resolve_world_view(
    db,
    *,
    script_id: int,
    save_id: int,
    commit_id: int,
    progress_chapter: int | None = None,
    mode: ForeknowledgeMode = "none",
) -> dict:
    """合并世界现状。

    实体:规范实体(进度过滤)为底,活态行按 logical_key 覆盖(玩家改动 > 原著);
          活态独有(origin=player 的新造)直接加入。
    事件/关系/变量:规范层不在 kb_* 里(规范事件在 timeline/worldline_nodes),
          这里返回活态层为主 + 规范世界线节点作为引导锚点(由 steering 单独取)。
    """
    canon_entities = canon_repo.read_canon_entities(
        db, script_id, progress_chapter=progress_chapter, mode=mode
    )
    live = live_repo.live_world_view(db, save_id, commit_id)

    # 实体合并:canon 为底,live 覆盖同 logical_key
    merged: dict[str, dict] = {}
    for e in canon_entities:
        merged[e["logical_key"]] = {**e, "_source": "canon"}
    for e in live["entities"]:
        existing = merged.get(e["logical_key"])
        merged[e["logical_key"]] = {
            **(existing or {}),
            **e,
            "_source": "live_override" if existing else "live_new",
        }

    return {
        "entities": list(merged.values()),
        "events": live["events"],            # 玩家这一周目的事件(原著事件走 timeline/锚点)
        "relationships": live["relationships"],
        "worldline_vars": live["worldline_vars"],
    }


def steering_context(db, *, script_id: int, progress_chapter: int | None = None) -> dict:
    """规范世界线引导上下文:可见的世界线 + 节点(供 D 篇 steering 取下一锚点)。"""
    worldlines = canon_repo.read_worldlines(db, script_id)
    out = []
    for wl in worldlines:
        nodes = canon_repo.read_worldline_nodes(
            db, script_id, wl["wl_key"], progress_chapter=progress_chapter
        )
        out.append({**wl, "nodes": nodes})
    return {"worldlines": out}
