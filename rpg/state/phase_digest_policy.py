"""state.phase_digest_policy — phase digest 的归属划分与上限(v1.82.0)。

# 为什么要有这个模块

同一批 `save_phase_digests` 在此之前经**两条互不知情的路**进同一个请求:

  ① `context_providers/runtime_phase_digests.py` —— 渲染成「已发生历史摘要(本存档)」层,
     取最近 4 个 phase、每个封顶 450 字符,受层预算管辖。
  ② `state.core.history_messages()` —— 把**所有** closed phase 拼成一条 user 消息顶在
     messages[] 最前面,**无条数上限、无 token 上限**,而且完全在层预算体系之外。

长局里两者的 phase 集合重叠,于是同一段历史在一个请求里出现两次;而 ② 那条路会随着
存档变长无限膨胀 —— phase digest 的 summary 全站都是章节原文拼接(不是压缩摘要),
每条 600 字符 × N 个 phase。

本模块是这件事的**单一真相源**:谁负责哪些 phase、各自的上限是多少,都在这里定。
纯函数,零 IO,两边 import 它。
"""
from __future__ import annotations

# 最近 N 个 phase(按 phase_index 降序,含 open)归「层」那条路负责。
# 与 context_providers/runtime_phase_digests.py 的 MAX_PHASES 是同一个数。
RECENT_PHASE_WINDOW = 4

# 层那条路:单个 phase 的渲染上限(字符)。
PER_PHASE_BUDGET = 450

# messages[] 前情提要那条路的上限 —— 在此之前这两条都不存在。
# 只收「层没覆盖到的、更早的」closed phase,且最多这么多条、总共这么多字符。
# 更早的历史不靠这条路,靠 episodic_recall 层(全程历史召回)按相关性捞 —— 那才是
# 长局记忆的正经通道,而它在 v1.82.0 之前一直被默认上限砍到 1800 字符。
DIGEST_PREFIX_MAX_PHASES = 6
DIGEST_PREFIX_MAX_CHARS = 4000
DIGEST_SUMMARY_MAX_CHARS = 400


def layer_owned_phase_indexes(all_phase_indexes: list[int]) -> set[int]:
    """哪些 phase_index 由「层」那条路负责渲染。

    与 runtime_phase_digests 的取法一致:按 phase_index 降序取前 RECENT_PHASE_WINDOW 个
    (含 open phase)。传入的是该 save 的**全部** phase_index。
    """
    return set(sorted(set(int(i) for i in all_phase_indexes), reverse=True)[:RECENT_PHASE_WINDOW])


def select_prefix_phases(phases: list[dict], *, layer_owned: set[int],
                         max_recent_turn: int) -> list[dict]:
    """挑出该进 messages[] 前情提要的 phase,按 phase_index 升序返回。

    三道闸,顺序固定:
      ① 层已经负责的 phase 不再进这里(去重 —— 这是本模块存在的首要理由)
      ② 只收已 closed、有 summary、且 turn_end 已经落在近因窗口之外的
      ③ 按 phase_index 降序取最近 DIGEST_PREFIX_MAX_PHASES 个,再翻回时间正序

    ③ 之所以取「最近的 N 个」而不是「最早的 N 个」:更早的历史由 episodic_recall
    按相关性召回,而紧挨着近因窗口的那几段是连贯性最需要的。
    """
    cand = [
        p for p in phases
        if int(p.get("phase_index", -1)) not in layer_owned
        and (p.get("status") or "") == "closed"
        and (p.get("summary") or "").strip()
        and int(p.get("turn_end") or 0) <= max_recent_turn
    ]
    cand.sort(key=lambda p: int(p.get("phase_index", 0)), reverse=True)
    picked = cand[:DIGEST_PREFIX_MAX_PHASES]
    picked.sort(key=lambda p: int(p.get("phase_index", 0)))
    return picked


__all__ = [
    "RECENT_PHASE_WINDOW",
    "PER_PHASE_BUDGET",
    "DIGEST_PREFIX_MAX_PHASES",
    "DIGEST_PREFIX_MAX_CHARS",
    "DIGEST_SUMMARY_MAX_CHARS",
    "layer_owned_phase_indexes",
    "select_prefix_phases",
]
