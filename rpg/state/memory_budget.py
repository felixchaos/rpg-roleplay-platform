"""memory_budget.py — GM 往玩家记忆桶追加条目的洪泛闸(确定性)。

群反馈(07-30):玩家一轮点亮 36 处星窍,GM **每点亮一处就写一条能力** → 能力面板被
同一门功法刷了二十多条,玩家只能一条条点 × 删。原因是全链路没有任何追加预算:
`known_events` 早就因为同样的「GM 记流水账无界堆积」加了硬上限,记忆桶却一直没有
(典型的修 A 漏 B)。

阈值按真实对局的追加节奏校准过:正常节奏是每回合往一个桶追加 1-2 条,超过阈值的
属于长尾里的病理形态(同一件事被逐条拆写)。两条闸都只打这条长尾,不碰正常节奏:

  1. **每回合每桶** 最多追加 N 条(默认 6)
  2. **同族** 最多 M 条(默认 4)—— 只管 abilities / resources

族闸为什么不管 facts / pinned / notes:facts 天然按人名聚族(同一个 NPC 名下攒下七八条
事实完全正常),pinned 里的「玩家强制设定：X」是玩家自己写的。给它们上族闸就是误伤。

**拒绝,不是丢弃**:超额的写入原样退回并给出理由,GM 下一轮能看见 → 自己合并成一条;
玩家自己的写入(ui_button / llm_set / api_direct)**永不受闸** —— 前科是「玩家笔记/
固定记忆被自动归档悄悄丢」,玩家可见资产绝不能被系统悄悄吃掉。
"""
from __future__ import annotations

from typing import Any

# 闸只对 GM 来源生效,而且是**白名单式的 fail-open**:认不出来的来源一律放行。
# 这条方向很重要 —— 漏拦一次洪泛只是面板多几条,误拦一次玩家写入就是「玩家笔记
# 被系统悄悄吃掉」(那个前科修过一次,不能再犯)。
#
# 两套来源词汇都要认:
#   · dispatcher 的 origin(`env.args["_origin"]`):GM 回合 = llm_chat,
#     后处理 JSON op = llm_chat_json_op;玩家侧是 ui_button / llm_set / api_direct
#   · apply_ops 的 source:GM 侧是 "gm" / "gm:json",玩家 /set 侧是 "user:*" / "player*"
_GM_ORIGIN_PREFIXES = ("llm_chat", "gm")


def is_gated_origin(origin: str) -> bool:
    """该来源是否受闸。认不出来 → 不受闸(fail-open,见上方注释)。"""
    o = (origin or "").strip()
    return bool(o) and o.startswith(_GM_ORIGIN_PREFIXES)

# 只有这两个桶上族闸:它们是玩家资产面板(能力 / 资源),一门功法刷二十条就是污染。
FAMILY_GUARDED_BUCKETS = frozenset({"abilities", "resources"})

# 结构化分隔符 —— 都是 GM 自己拼标题时用的记号,不是语义推断。
# (中文语义信号不许用单字符判定;这里判的是**结构**,且还要求前缀 ≥3 字 + 同族已达 M 条。)
_FAMILY_SEPS = frozenset("·・:：|｜(（[【")

_MIN_HEAD = 3


def family_head(text: str) -> str:
    """取「族名」= 首个结构分隔符之前的部分,不足 3 字或没有分隔符则返回空(=不参与族闸)。

    `周天命星炼窍法·神庭·星宿(神光凝聚)` → `周天命星炼窍法`
    `指尖点火`(无分隔符)              → ``(整串相同的条目已被精确去重挡掉,无需族闸)
    """
    t = (text or "").strip()
    for i, ch in enumerate(t):
        if ch in _FAMILY_SEPS:
            head = t[:i].strip()
            return head if len(head) >= _MIN_HEAD else ""
    return ""


def _added_this_turn(data: dict[str, Any], bucket: str, turn: int) -> int:
    """本回合已往该桶追加了几条 —— 从 memory.items 现算,不新增状态字段。

    items 每条都带 turn + legacy_bucket(add_memory 的 dual-write),所以计数天然
    跟着存档走:回滚 / fork 都不会留下一个对不上的计数器。
    """
    n = 0
    for it in (data.get("memory") or {}).get("items") or []:
        if not isinstance(it, dict):
            continue
        if it.get("legacy_bucket") == bucket and int(it.get("turn") or 0) == turn:
            n += 1
    return n


def _family_count(data: dict[str, Any], bucket: str, head: str) -> int:
    """该桶里已有几条同族条目(跨回合)。桶本身就是权威列表,直接数它。"""
    if not head:
        return 0
    return sum(1 for t in (data.get("memory") or {}).get(bucket) or []
               if isinstance(t, str) and family_head(t) == head)


def check_append(data: dict[str, Any], bucket: str, text: str, origin: str = "") -> str:
    """允许追加返回空串;该拦则返回**给 GM 看的理由**(会原样进工具失败串)。"""
    if not is_gated_origin(origin):
        return ""

    from core.config import memory_append_per_turn_max, memory_family_max

    turn = int(data.get("turn") or 0)
    per_turn = memory_append_per_turn_max()
    if per_turn > 0 and _added_this_turn(data, bucket, turn) >= per_turn:
        return (f"本回合已往 memory.{bucket} 追加 {per_turn} 条(上限),多余的条目请合并成一条再写 —— "
                f"玩家面板里每条都要手动删,刷屏比漏记更伤")

    if bucket in FAMILY_GUARDED_BUCKETS:
        head = family_head(text)
        cap = memory_family_max()
        if head and cap > 0 and _family_count(data, bucket, head) >= cap:
            return (f"「{head}」在 memory.{bucket} 里已有 {cap} 条(同族上限),别再逐条拆写;"
                    f"请用 remove_memory_item 合并,或把新进展并进已有条目")

    return ""


__all__ = ["FAMILY_GUARDED_BUCKETS", "check_append", "family_head", "is_gated_origin"]
