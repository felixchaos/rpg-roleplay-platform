"""gm_serving/steering.py — Phase D 规范世界线引导(D §5)。

每轮:定位玩家最近在哪条线/下一节点 → 引导(锚点软目标)→ 放权(怎么达成交玩家)→
重锚(偏到另一条规范枝叉切锚点)。粗弧层(script_worldline_nodes)坐在细 save_anchor_states 之上。
"""
from __future__ import annotations

from kb import canon_repo


def resolve_steering_target(db, *, save_id: int, script_id: int,
                            progress_chapter: int | None = None) -> dict:
    """产出 ① 层软目标。

    返回 {worldline, passed_nodes, next_node, soft_goal, pending_anchors}。
    定位:看已 occurred 的 save_anchor_states 簇匹配到哪个 worldline 节点;取序号下一个节点。
    """
    worldlines = canon_repo.read_worldlines(db, script_id)
    if not worldlines:
        return {"worldline": None, "next_node": None, "soft_goal": "", "pending_anchors": []}
    # 默认主线(is_primary),否则第一条
    wl = next((w for w in worldlines if w.get("is_primary")), worldlines[0])
    nodes = canon_repo.read_worldline_nodes(db, script_id, wl["wl_key"], progress_chapter=progress_chapter)
    if not nodes:
        return {"worldline": wl["wl_key"], "next_node": None, "soft_goal": "", "pending_anchors": []}

    # 已 occurred 的锚点 keys
    occurred = {
        r["anchor_key"]
        for r in db.execute(
            "select anchor_key from save_anchor_states where save_id=%s and status in ('occurred','variant')",
            (save_id,),
        ).fetchall()
    }
    # 找最后一个"其 anchor_keys 已大部分 occurred"的节点 → 下一个就是目标
    passed_idx = -1
    for i, node in enumerate(nodes):
        aks = node.get("anchor_keys") or []
        if aks and sum(1 for a in aks if a in occurred) >= max(1, len(aks) // 2):
            passed_idx = i
    next_node = nodes[passed_idx + 1] if passed_idx + 1 < len(nodes) else None

    pending = []
    if next_node:
        must = next_node.get("must_preserve") or []
        soft = (
            f"下一关键节点「{next_node['label']}」:{next_node.get('summary', '')}"
            + (f" 须保留:{'、'.join(must)}。" if must else "")
            + " ——朝这个方向自然推进即可,具体怎么发生交给玩家选择;不要生硬照搬原著。"
        )
        pending = next_node.get("anchor_keys") or []
    else:
        soft = "已抵达/超出当前规范世界线末节点,自由发挥并尽量保持世界自洽。"

    return {
        "worldline": wl["wl_key"],
        "passed_nodes": passed_idx + 1,
        "next_node": next_node["node_key"] if next_node else None,
        "soft_goal": soft,
        "pending_anchors": pending,
    }
