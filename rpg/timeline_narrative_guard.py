"""
timeline_narrative_guard.py — 时间线跳跃后 GM 叙事的禁词过滤。

用户报告:用 `/set 设置时间为火星·薇瑟帝国扬陆城内` 切换时间线后,
GM 把它叙事成"穿越/醒来/拨回时钟/时间被拉回"等过渡剧情。

主防线是 context_engine._timeline_layer() 给 GM 明示禁止这类措辞。
这个模块是 belt-and-suspenders:
  · 在 user_set 时间跳跃**当回合**,扫 GM 输出,检测禁词
  · 如果命中,在 audit_log 写违规记录,前端可展示警告
  · 不强 strip(避免误删合法叙事),仅 surface 让玩家决定是否 /retry

判断条件:
  state.world.timeline.last_transition.source == "user_set"
  AND state.world.timeline.last_transition.turn == state.turn
"""
from __future__ import annotations

import re
from typing import Any


# 禁词模式 — 涵盖"穿越/重置/醒来发现/时间倒流"类过渡叙事的常见表达。
# 用 regex 而非纯字符串,捕获各种变体(『时间被一双看不见的手拨回』『时钟被拨回最初』...)。
_FORBIDDEN_PATTERNS: list[tuple[str, str]] = [
    # 穿越类
    (r"穿越(?:事件|到|回去|回|回到|过去|时空)", "穿越叙事"),
    (r"时空(?:错乱|穿梭|裂缝|乱流)", "时空错乱叙事"),
    (r"回到\s*过去", "回到过去叙事"),
    (r"时间(?:倒流|流逝|被改写)", "时间倒流叙事"),
    # 醒来/失忆开场
    (r"再次睁开(?:眼睛|眼眸|双眼)", "再次睁开眼叙事"),
    (r"(?:当你|玩家)?醒来(?:发现|时)", "醒来发现叙事"),
    (r"从(?:昏迷|沉睡|失神|意识)中(?:醒来|惊醒|苏醒)", "失神苏醒叙事"),
    # 时间被拨回 / 时钟被拨回 (覆盖各种插入描写,如"被一双看不见的手生生拨回")
    (r"时间被[^,，。!?]{0,30}?[拉拨][^,，。]{0,5}?回", "时间被拨回叙事"),
    (r"时钟被[^,，。!?]{0,20}?[拨拉][^,，。]{0,5}?回", "时钟被拨回叙事"),
    (r"[拨拉][^,，。]{0,5}?回(?:最初|原点|开始|起点)", "拨回原点叙事"),
    # 重启/重置世界
    (r"重启(?:世界|时间|场景|剧情)", "重启世界叙事"),
    (r"重置(?:世界|时间|场景|剧情)", "重置世界叙事"),
    (r"世界(?:被|又|忽然)?\s*重写", "世界被重写叙事"),
    # 惊厥/失忆/无意识开场 (这类模板化开头是 LLM 时间跳跃的典型表达)
    (r"^冷[,，]\s*刺骨的冷", "刺骨的冷开场"),
    (r"^冷得发[抖颤栗]", "发抖开场"),
    (r"当你再次[^,，。]{0,8}时", "当你再次X时模板"),
]


def detect_time_jump_violations(text: str, state: Any) -> list[dict[str, Any]]:
    """检测 GM 文本是否在 user_set 时间跳跃**当回合**写了禁止叙事。

    返回违规列表:[{"pattern_label": str, "match": str, "position": int}, ...]
    若不在 user_set 当回合,或没命中禁词,返回空列表。

    用法:chat 主流程 GM 文本完成后调一次,把结果记到 audit_log。
    """
    if not text or not isinstance(text, str):
        return []
    data = getattr(state, "data", state) or {}
    timeline = (data.get("world") or {}).get("timeline") or {}
    last_t = timeline.get("last_transition") or {}
    if not isinstance(last_t, dict):
        return []
    if last_t.get("source") != "user_set":
        return []
    try:
        last_turn = int(last_t.get("turn") or -1)
        cur_turn = int(data.get("turn") or 0)
    except (TypeError, ValueError):
        return []
    # 仅当回合刚发生 user_set 跳跃时启用检测
    if last_turn != cur_turn:
        return []

    violations: list[dict[str, Any]] = []
    for pattern, label in _FORBIDDEN_PATTERNS:
        for m in re.finditer(pattern, text, re.MULTILINE):
            violations.append({
                "pattern": pattern,
                "pattern_label": label,
                "match": m.group(0),
                "position": m.start(),
            })
    return violations


def record_violations_to_audit(state: Any, violations: list[dict[str, Any]]) -> None:
    """把检测到的违规写到 state.permissions.audit_log,方便前端展示警告。"""
    if not violations:
        return
    data = getattr(state, "data", state) or {}
    permissions = data.setdefault("permissions", {})
    audit = permissions.setdefault("audit_log", [])
    from datetime import datetime
    audit.append({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "kind": "time_jump_narrative_violation",
        "source": "timeline_narrative_guard",
        "turn": int(data.get("turn") or 0),
        "violations": [
            {"label": v.get("pattern_label"), "match": v.get("match")}
            for v in violations
        ],
        "hint": (
            "GM 在 user_set 时间跳跃当回合写了"
            f"{len(violations)} 处过渡叙事禁词,"
            "可考虑 /retry 重新生成。"
        ),
    })
    if len(audit) > 200:
        permissions["audit_log"] = audit[-200:]


__all__ = ["detect_time_jump_violations", "record_violations_to_audit"]
