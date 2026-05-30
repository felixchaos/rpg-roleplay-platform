"""gm_serving/impact.py — Phase D 抗提示词污染:影响因子分级(D §6)。

动作按"爆炸半径"分级:绝大多数对话/移动是 local → 零世界推演零污染;
只有 faction/world 级动作才触发带外世界推演子代理(隔离上下文算涟漪,写结构化 delta,
叙事 GM 看不到推理)。这样叙事 prompt 始终精简稳定(利于 prompt 缓存)。
"""
from __future__ import annotations

import re

# 关键词驱动的保守分级(宁可低估为 local,避免无谓世界推演)
_WORLD = re.compile(r"宣战|开战|世界大战|核|毁灭|灭国|登基|加冕|帝国覆灭|签订和约|停战协定|改变历史走向")
_FACTION = re.compile(r"势力|阵营|军队|联盟|背叛|结盟|政变|刺杀(?:首领|元首|国王|皇帝|领袖)|占领|攻陷|起义|叛乱|公开身份")
_SCENE = re.compile(r"战斗|交火|爆炸|起火|逃跑|追击|抓捕|搜查|集会|宴会|审讯")

IMPACT_LEVELS = ("local", "scene", "faction", "world")


def classify_impact(action_text: str) -> str:
    """对玩家动作/事件分级。返回 local|scene|faction|world。"""
    t = action_text or ""
    if _WORLD.search(t):
        return "world"
    if _FACTION.search(t):
        return "faction"
    if _SCENE.search(t):
        return "scene"
    return "local"


def needs_offband_sim(level: str) -> bool:
    """是否需要带外世界推演子代理(faction 及以上才需要算涟漪)。"""
    return level in ("faction", "world")
