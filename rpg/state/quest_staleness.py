"""quest_staleness.py — 主线(memory.main_quest)陈旧检测。

群反馈(08-01):「蓝框有起码五十个回合以上没变过了,只有下面的会变」——蓝框是主线,
下面是当前目标。查下来主线**冻了上百个回合零更新**,同期审计里 `set_current_objective`
被反复调用、`set_main_quest` **一次都没有**。没有任何东西在拒绝写入(无待审批、
权限完全放行、审计零 blocked)—— 是 GM 从来不写它。

根因不是「写不进去」,是**没有任何确定性机制提醒它该更新**:当前目标每轮都在议程上,
主线是长程字段,写完一次就没人再管,全靠 LLM 自己想起来,而它不会想起来。这正是
「确定性代码缝 > 指望提示词」那条铁律的反面案例 —— 但主线该改成什么是叙事判断,
不能由代码代写。所以这里只做**确定性的触发**:到点了就把「主线已 N 回合没动过」
摆到史官面前,改不改、改成什么仍归它判断。

阈值按真实对局的更新节奏校准:正常间隔在个位数回合量级,超过 25 的属于长尾。
取 25 只打长尾,不烦正常节奏。
"""
from __future__ import annotations

from typing import Any

MAIN_QUEST_STALE_TURNS = 25

# 记录「主线上次被写的回合」的字段。放在 memory 下随存档走,回滚/分叉都跟着对。
_STAMP_KEY = "main_quest_turn"


def stamp_main_quest(data: dict[str, Any], turn: int | None = None) -> None:
    """主线被写入后打时间戳。**每条写入路径都要调**,漏一条这个字段就会假性陈旧。"""
    try:
        mem = data.setdefault("memory", {})
        mem[_STAMP_KEY] = int(turn if turn is not None else (data.get("turn") or 0))
    except Exception:  # noqa: BLE001  戳失败绝不影响主线本身写入
        pass


def main_quest_age(data: dict[str, Any]) -> int | None:
    """主线距上次更新过了几回合。返回 None = 从未记录过(存量存档 / 从没写过)。"""
    mem = (data or {}).get("memory") or {}
    stamped = mem.get(_STAMP_KEY)
    if stamped is None:
        return None
    try:
        return max(0, int(data.get("turn") or 0) - int(stamped))
    except (TypeError, ValueError):
        return None


def main_quest_is_stale(data: dict[str, Any]) -> bool:
    """该提醒了吗。

    没有主线 → 不提醒(空字段是「还没定主线」,不是「主线过时」,催它反而会逼出编造)。
    没有时间戳 → **视为陈旧**:存量存档一律先提醒一次,写入时自然补上戳,之后按 age 走。
    """
    mem = (data or {}).get("memory") or {}
    if not str(mem.get("main_quest") or "").strip():
        return False
    age = main_quest_age(data)
    return age is None or age >= MAIN_QUEST_STALE_TURNS


__all__ = ["MAIN_QUEST_STALE_TURNS", "main_quest_age", "main_quest_is_stale", "stamp_main_quest"]
