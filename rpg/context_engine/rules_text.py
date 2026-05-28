"""context_engine.rules_text — 规则文本生成函数."""
from __future__ import annotations

from typing import Any


def _story_rules() -> str:
    return "\n".join([
        "这是沉浸式文字 RPG。GM 只描写玩家角色能感知或通过合理渠道获知的信息。",
        "保持原著风格：克制、精确、信息密度高，不把 NPC 写成答题机器。",
        "不要替玩家决定行动。结尾可以给压力、线索或抉择，但不代替玩家选择。",
        "玩家行动可能改变原著分支，世界书和角色卡优先维持人物逻辑与势力边界。",
        "本轮发生状态变化时，在正文末尾追加结构化标签，方便系统写回存档。",
    ])


def _agent_runtime_rules() -> str:
    # task 67：主体契约已合并到 gm.py _SYSTEM_BASE「主 GM 运行契约」段，
    # 这里只保留 "本轮特定" 的运行提醒（动态层用，每轮可重申）。
    return "\n".join([
        "本轮务必执行: 读子代理决议 → 裁定世界反应 → 输出正文 → 输出 JSON ops 数组（仅当真有变化时）。",
        "如上下文不足以推进，在正文里说明不确定性并输出 question op 让玩家选择，不要瞎编。",
    ])


def _context_agent_decision(plan: dict[str, Any] | None) -> str:
    """task 79：DemandLedger 渲染。显式分组展示 hard vs soft constraint，
    acceptance 单独 section，confidence + clarifying_question 单独提示。"""
    if not plan:
        return "本轮没有大模型子代理决议；主 GM 必须按时间线层和检索参考保守生成。"
    must_include = plan.get("must_include") or (plan.get("retrieval_plan", {}) or {}).get("must_include") or []
    risk_flags = plan.get("risk_flags") or []
    hard = plan.get("hard_constraints") or []
    soft = plan.get("soft_preferences") or []
    targets_e = plan.get("target_entities") or []
    acceptance = plan.get("acceptance") or []
    candidates = plan.get("candidate_actions") or []
    conf = plan.get("confidence", 1.0)
    clarify = (plan.get("clarifying_question") or "").strip()

    lines = [
        f"子代理意图：{plan.get('intent') or '未说明'}",
    ]
    if plan.get("active_goal"):
        lines.append(f"底层真实目标：{plan['active_goal']}")
    lines.append(f"目标时间线：{plan.get('timeline_target') or '未请求跳转'}")
    if plan.get("target_location"):
        lines.append(f"目标地点：{plan['target_location']}")
    if plan.get("target_time"):
        lines.append(f"目标时间：{plan['target_time']}")
    if targets_e:
        lines.append(f"涉及实体：{'、'.join(str(x) for x in targets_e[:8])}")
    if hard:
        lines.append("【硬约束】（必须满足）")
        for c in hard[:6]:
            lines.append(f"  · {c}")
    if soft:
        lines.append("【软偏好】（最好满足，可妥协）")
        for c in soft[:6]:
            lines.append(f"  · {c}")
    lines.append(f"检索查询：{plan.get('retrieval_query') or '未提供'}")
    lines.append("必含事实：" + ("；".join(str(x) for x in must_include) if must_include else "无"))
    if acceptance:
        lines.append("【本轮 acceptance 验收】（输出后系统会检查每条是否满足）")
        for a in acceptance[:6]:
            lines.append(f"  · {a}")
    if candidates:
        lines.append("【候选动作建议】（GM 可优先从中选；不强制）")
        for c in candidates[:5]:
            lines.append(f"  · {c}")
    lines.append("风险标记：" + ("；".join(str(x) for x in risk_flags) if risk_flags else "无"))
    lines.append(f"子代理置信度：{conf:.2f}")
    if clarify:
        lines.append(f"⚠️ 子代理建议先问玩家：{clarify}")
    lines.append(f"选择理由：{plan.get('reason') or '未说明'}")
    lines.append("主 GM 只能把这些作为上下文选择结果使用，不得把子代理理由写成玩家可见事实。")
    return "\n".join(lines)


def _context_agent_debug(plan: dict[str, Any] | None) -> dict[str, Any]:
    if not plan:
        return {}
    return {
        "intent": plan.get("intent", ""),
        "active_goal": plan.get("active_goal", ""),
        "timeline_target": plan.get("timeline_target", ""),
        "retrieval_query": plan.get("retrieval_query", ""),
        "must_include": plan.get("must_include", []),
        "hard_constraints": plan.get("hard_constraints", []),
        "soft_preferences": plan.get("soft_preferences", []),
        "target_entities": plan.get("target_entities", []),
        "candidate_actions": plan.get("candidate_actions", []),
        "acceptance": plan.get("acceptance", []),
        "risk_flags": plan.get("risk_flags", []),
        "confidence": plan.get("confidence", 1.0),
        "clarifying_question": plan.get("clarifying_question", ""),
    }
