"""state/labels.py — 风险/校验标签 helpers (_risk_label, _validation_label)"""
from __future__ import annotations


# 风险评级。前端 ConfirmStrip 根据 risk 染色（low/medium/high）显示给玩家，
# 让玩家在批量待审时快速看到"高风险动作"先决策。
_HIGH_RISK_PREFIXES = (
    "world.timeline.",       # 改时间线 = 改剧情走向
    "worldline.",            # 世界线变量 = 全局推演规则
    "memory.pinned",         # 固定记忆 = 长期影响
)
_HIGH_RISK_EXACT = {
    "player.name", "player.role", "player.background",
    "world.time",
    "memory.main_quest",
}
_MEDIUM_RISK_PREFIXES = (
    "relationships.",
    "memory.facts",
    "memory.abilities",
    "memory.resources",
)


def _risk_label(path: str) -> str:
    """给路径派一个风险等级，前端按颜色分组显示。"""
    if path in _HIGH_RISK_EXACT or path.startswith(_HIGH_RISK_PREFIXES):
        return "high"
    if path.startswith(_MEDIUM_RISK_PREFIXES):
        return "medium"
    return "low"


def _validation_label(status: str) -> str:
    return {
        "passed": "通过",
        "conflict": "冲突",
        "review": "待审",
        "none": "无",
    }.get(status, status)
