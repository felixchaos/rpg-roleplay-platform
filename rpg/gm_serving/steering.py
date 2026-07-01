"""gm_serving/steering.py — Phase D 规范世界线引导(D §5)。

每轮:定位玩家最近在哪条线/下一节点 → 引导(锚点软目标)→ 放权(怎么达成交玩家)→
重锚(偏到另一条规范枝叉切锚点)。粗弧层(script_worldline_nodes)坐在细 save_anchor_states 之上。
"""
from __future__ import annotations

from kb import canon_repo


def resolve_steering_target(db, *, save_id: int, script_id: int,
                            progress_chapter: int | None = None,
                            steering_strength: str = "guided") -> dict:
    """产出 ① 层软目标。

    返回 {worldline, passed_nodes, next_node, soft_goal, pending_anchors, strength}。
    定位:看已 occurred 的 save_anchor_states 簇匹配到哪个 worldline 节点;取序号下一个节点。

    steering_strength(三档真实强度梯度,详见 settings.py SETTINGS_SCHEMA):
      rail    — 贴原著:把【当前/下一个待发生锚点】当成「接下来必须推进到的下一拍」用
                **强措辞**注入(非温和软目标),要求 GM 主动收束、玩家偏离 1-3 轮内拉回。
                配合 anchor_reconcile 确定性兜底形成「强推 + 确定性跟踪」。
      guided  — 现状默认,软目标引导但不强制(保守措辞)。
      free    — 不注入软目标,完全自由发挥。

    `strength` 字段回传给 context_inject.build_injection,决定外层包裹标签的强弱
    (rail 用「强制下一拍」硬标签;guided 用「软目标·引导非铁轨」温和标签)。
    """
    worldlines = canon_repo.read_worldlines(db, script_id)
    if not worldlines:
        return _fallback_soft_goal(save_id, wl_key=None, steering_strength=steering_strength,
                                   progress_chapter=progress_chapter)
    # 默认主线(is_primary),否则第一条
    wl = next((w for w in worldlines if w.get("is_primary")), worldlines[0])
    nodes = canon_repo.read_worldline_nodes(db, script_id, wl["wl_key"], progress_chapter=progress_chapter)
    if not nodes:
        # 粗弧层 script_worldline_nodes 没建(很多剧本只 seed 了细 save_anchor_states,
        # 没跑世界树脊柱)→ 旧代码静默返回空 soft_goal,GM 完全无引导、玩家「锚点推不动」。
        # 降级:用细锚点层的 top-1~2 pending 合成 soft_goal,受 steering_strength 控制。
        return _fallback_soft_goal(save_id, wl_key=wl["wl_key"], steering_strength=steering_strength,
                                   progress_chapter=progress_chapter)

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
        must_str = f" 须保留:{'、'.join(must)}。" if must else ""
        if steering_strength == "free":
            # 自由模式:不注入软目标,让 GM 自由发挥
            soft = ""
        elif steering_strength == "rail":
            # 贴原著:强措辞「下一拍」收束。带 top-2 后续节点提示往哪推。
            nxt2 = nodes[passed_idx + 2] if passed_idx + 2 < len(nodes) else None
            after = (
                f" 其后将推进到「{nxt2['label']}」({nxt2.get('summary', '')})。" if nxt2 else ""
            )
            soft = _rail_directive(
                target=f"节点「{next_node['label']}」:{next_node.get('summary', '')}",
                must_str=must_str,
                after=after,
            )
        else:
            # guided(默认):软目标,温和引导
            soft = (
                f"下一关键节点「{next_node['label']}」:{next_node.get('summary', '')}"
                + must_str
                + " ——朝这个方向自然推进即可,具体怎么发生交给玩家选择;不要生硬照搬原著。"
            )
        pending = next_node.get("anchor_keys") or []
    else:
        if steering_strength == "rail":
            soft = (
                "已抵达/超出当前规范世界线末节点。保持世界自洽,围绕已确立的原著走向收尾,"
                "不要凭空另起一条与原著无关的新主线。"
            )
        elif steering_strength == "free":
            # free 档:不注入任何软目标(与有 next_node 时 line 63-65 对称)。
            # 原缺这一分支 → free 档走到末节点仍收到"自由发挥"文本约束,违背"free=不注入引导"契约。
            soft = ""
        else:
            soft = "已抵达/超出当前规范世界线末节点,自由发挥并尽量保持世界自洽。"

    return {
        "worldline": wl["wl_key"],
        "passed_nodes": passed_idx + 1,
        "next_node": next_node["node_key"] if next_node else None,
        "soft_goal": soft,
        "pending_anchors": pending,
        "strength": steering_strength,
    }


def _rail_directive(*, target: str, must_str: str = "", after: str = "") -> str:
    """贴原著(rail)模式的**强力**收束指令文本。强措辞、明确「下一拍」、要求主动收束 +
    偏离 1-3 轮内拉回 + 允许合理变体(drift)但不可另起炉灶长期跑偏。

    与 master.py「世界线收束」段话术一致(命运式手段:巧合/误会/他人介入/环境压力)。
    措辞定位:强力引导朝锚点 + 偏离尽快拉回,而非逐字复述原文(产品=活世界+收束)。
    """
    return (
        "【贴原著 · 强制收束】接下来必须主动推进到的下一拍 = "
        + target
        + must_str
        + after
        + " ——这不是可选的软目标,而是本回合/接下来你必须主动朝其收束的剧本节点。"
        " 玩家可以自由行动,但若偏离该下一拍,你须在 1-3 轮内用命运式手段(巧合 / 误会 /"
        " 他人介入 / 环境压力)把剧情自然拉回,让玩家感觉不到生硬却仍抵达锚点。"
        " 允许合理变体(地点 / 时机 / 旁观者不同,即 drift),但事件的【须保留】维度必须发生,"
        " 不可另起炉灶长期跑偏到与原著无关的世界线。pending_questions 选项中至少 1 个要通往该下一拍。"
    )


def _fallback_soft_goal(save_id: int, *, wl_key: str | None,
                        steering_strength: str = "guided",
                        progress_chapter: int | None = None) -> dict:
    """粗弧层(script_worldline_nodes)缺失时的确定性降级:用细锚点层的【最近的待发生】
    锚点合成 soft_goal,别让 GM 完全失去引导(用户「锚点推不动」根因之一)。本书
    script_worldline_nodes 多半为空,steering 实际就走这条路径。

    强度梯度必须保留:
      free   — 仍不注入(尊重玩家选择)。
      guided — 最近 1 个待发生锚点,温和软目标。
      rail   — **强力**版(非 gentle):最近 1 个当「下一拍」+ 次近 1 个提示后续,强措辞收束。

    根因修复(用户「开局强制下一拍指向多章以后的人物」反复反馈):
      「下一拍」本质是【最近的下一个】锚点,必须按 source_chapter ASC 取(order_by_chapter),
      绝不能按 importance DESC —— 否则开局会取到窗口/全档里最重要的远章人物(如几十章后登场
      的主要角色)当下一拍。同时锚定到玩家当前章(progress_chapter),开局窗口不再是死的
      [1,30]、也绝不取玩家身后的锚点。与 retrieval.py 进度窗口注入(已修)保持同一语义。
    """
    base = {"worldline": wl_key, "passed_nodes": 0, "next_node": None,
            "soft_goal": "", "pending_anchors": [], "strength": steering_strength}
    if steering_strength == "free":
        return base
    # rail 多带一条(次近)给「下一拍 + 其后」;guided 仍只取最近 1 个。
    want = 2 if steering_strength == "rail" else 1
    try:
        from agents.anchor_seed_agent import list_pending_for_phase, get_progress_window
        win = get_progress_window(int(save_id))
        ch_min = win.get("chapter_min")
        ch_max = win.get("chapter_max")
        # 锚定到玩家当前章:防开局(无 occurred 锚点)窗口回退成 [1,30] 后,
        # 仍把「下一拍」指到玩家身后或窗口内远章。progress_chapter 是权威当前章。
        if progress_chapter and int(progress_chapter) > 0:
            ch_min = max(int(ch_min or 1), int(progress_chapter))
            ch_max = max(int(ch_max or 0), ch_min + 50)
        # 「下一拍」= 当前章及以后【最近】的待发生锚点(chapter ASC),不是全局最重要的。
        pend = list_pending_for_phase(
            int(save_id), None, limit=want,
            chapter_min=ch_min, chapter_max=ch_max, order_by_chapter=True,
        )
        if not pend:
            # 窗口内没有 → 放宽 chapter_max,取当前章及以后最近的(仍 chapter ASC,
            # 绝不退回「全档按 importance」—— 那正是开局指向远章人物的旧 bug)。
            pend = list_pending_for_phase(
                int(save_id), None, limit=want,
                chapter_min=ch_min, order_by_chapter=True,
            )
    except Exception:
        pend = []
    if not pend:
        return base
    a = pend[0]
    summary = (a.get("summary") or "").strip()
    must = a.get("must_preserve") or []
    must_str = f" 须保留:{'、'.join(must)}。" if must else ""
    if steering_strength == "rail":
        after = ""
        if len(pend) > 1 and (pend[1].get("summary") or "").strip():
            after = f" 其后将推进到:{(pend[1].get('summary') or '').strip()}。"
        soft = _rail_directive(
            target=f"原著关键事件「{summary}」",
            must_str=must_str,
            after=after,
        )
    else:  # guided(默认)
        soft = (
            f"下一关键原著事件:{summary}"
            + must_str
            + " ——朝这个方向自然推进即可,具体怎么发生交给玩家选择;不要生硬照搬原著。"
        )
    base["soft_goal"] = soft
    base["pending_anchors"] = [a.get("anchor_key")] if a.get("anchor_key") else []
    return base
