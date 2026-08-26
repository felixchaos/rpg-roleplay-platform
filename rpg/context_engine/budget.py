"""context_engine.budget — 层预算的全局求解(v1.82.0)。

# 为什么需要它

在此之前每层各有一个常量上限(`MAX_LAYER_CHARS`),互相不知道对方存在。33 个上限之和
≈17.3 万字符 ≈ 8.7 万 token,而**没有任何一处**拿这个和跟模型真实 context window 比过:
`context_window_for()` 只在 `app.py` 事后记账。于是:

  · 小窗模型上「超没超」是交给 backend 盲截的,截掉的是尾部 —— 而尾部恰好是
    `user_input`(priority 0,排最后)。
  · 换模型(128k ↔ 1M)只能手改 33 个常量。

# 求解语义

每层三个数:`min`(低于它这层没有信息价值)、`want`(现有常量)、`priority`(已有字段,
降序排在前)。

  ① 全部先给 min。min 之和超预算 → 按 priority **升序**整层丢弃(最不重要的先走),
     直到装得下;记进 `dropped`。
  ② 剩余预算按 priority 降序、按各层 (want-min) 的比例分配,单层封顶 want。

**求解结果永不超过 want** —— 所以预算宽裕时(gemini / deepseek 1M)每层拿满 want,
输出与引入求解前逐字节相同。只有真的装不下时才生效。这条是刻意的:让这次改动在
绝大多数生产会话上是零行为变更,把风险面收窄到「本来就要溢出」的那批请求。

纯函数,零 IO,零依赖。
"""
from __future__ import annotations

from typing import Any


def solve_layer_budgets(
    specs: list[dict[str, Any]],
    budget_chars: int,
    protected: frozenset[str] | set[str] | None = None,
) -> tuple[dict[str, int], list[str]]:
    """按 (min, want, priority) 求解每层实得字符数。

    specs: [{"id": str, "min": int, "want": int, "priority": int}, ...]
            顺序无关;同 id 重复出现时后者覆盖前者(层 id 在一次装配里应当唯一)。
    budget_chars: 本次装配可用的总字符预算。<= 0 表示不做求解。
    protected: 永不整层丢弃的 layer id(见 _constants.NEVER_DROP_LAYERS)。

    ⚠️ **priority 在本仓库是「层在 prompt 里的位置」,不是重要性。** user_input 的
    priority=0 是因为它必须垫底(近因),不是因为它可有可无 —— 直接拿 priority 当丢弃序,
    第一个被丢的就是玩家这一轮说的话。所以丢弃候选先排除 protected,再按 priority 升序。

    返回 (granted, dropped):
      granted — {layer_id: 实得 char 上限};被丢弃的层不在其中。
      dropped — 被整层丢弃的 layer_id,按丢弃顺序(最先丢的在前)。
    """
    protected = frozenset(protected or ())
    if budget_chars <= 0 or not specs:
        return {s["id"]: int(s["want"]) for s in specs}, []

    # 同 id 去重(保留后者),并把 min 夹到 [0, want]
    norm: dict[str, dict[str, int]] = {}
    order: list[str] = []
    for s in specs:
        lid = str(s["id"])
        want = max(0, int(s.get("want", 0)))
        lo = max(0, min(int(s.get("min", 0)), want))
        if lid not in norm:
            order.append(lid)
        norm[lid] = {"min": lo, "want": want, "priority": int(s.get("priority", 50))}

    # ① 保底:priority 升序丢弃直到 min 之和装得下
    dropped: list[str] = []
    alive = list(order)
    total_min = sum(norm[i]["min"] for i in alive)
    if total_min > budget_chars:
        # 只在**未受保护**的层里选丢弃对象;priority 升序(越靠 prompt 末尾越先让位),
        # priority 相同时按 want 降序丢(先丢大的,能更快腾出空间)。
        droppable = [i for i in alive if i not in protected]
        for lid in sorted(droppable, key=lambda i: (norm[i]["priority"], -norm[i]["want"])):
            if total_min <= budget_chars:
                break
            total_min -= norm[lid]["min"]
            dropped.append(lid)
        alive = [i for i in alive if i not in set(dropped)]
        if total_min > budget_chars:
            # 受保护的层自己就装不下 —— 预算小到这一步说明模型窗口本来就不够用。
            # 不丢它们(丢了这一轮直接废),按比例压到预算内并留个下限,让请求还能发出去。
            scale = budget_chars / float(total_min)
            granted_p = {}
            for lid in alive:
                granted_p[lid] = max(200, int(norm[lid]["min"] * scale))
            import logging
            logging.getLogger("context_engine").warning(
                "[context] 受保护层的下限之和 %d 超过预算 %d,已按 %.2f 压缩:%s",
                total_min, budget_chars, scale, ",".join(sorted(alive)))
            return granted_p, dropped

    granted = {lid: norm[lid]["min"] for lid in alive}
    remaining = budget_chars - sum(granted.values())
    if remaining <= 0:
        return granted, dropped

    # ② 剩余预算按 priority 降序、按 (want-min) 比例分配
    headroom = {lid: norm[lid]["want"] - norm[lid]["min"] for lid in alive}
    total_headroom = sum(v for v in headroom.values() if v > 0)
    if total_headroom <= 0:
        return granted, dropped

    if remaining >= total_headroom:
        # 装得下所有人的 want —— 全给满,行为等同于不求解
        for lid in alive:
            granted[lid] = norm[lid]["want"]
        return granted, dropped

    # 按 priority 降序发放,同 priority 内按 headroom 比例;逐层结算避免累计误差
    ranked = sorted(alive, key=lambda i: (-norm[i]["priority"], i))
    left = remaining
    for lid in ranked:
        h = headroom[lid]
        if h <= 0 or left <= 0:
            continue
        share = int(remaining * h / total_headroom)
        share = min(share, h, left)
        granted[lid] += share
        left -= share
    # 整数除法的余数按 priority 降序补给还有 headroom 的层
    if left > 0:
        for lid in ranked:
            if left <= 0:
                break
            room = norm[lid]["want"] - granted[lid]
            if room <= 0:
                continue
            add = min(room, left)
            granted[lid] += add
            left -= add

    return granted, dropped


def layer_budget_chars(api_id: str | None, model_name: str | None) -> int:
    """把模型的 context window(token)换算成留给「层」的字符预算。

    返回 0 表示拿不到窗口大小 → 调用方应退回「每层各拿 want」的旧行为(不做求解)。

    留给层的份额是 `RPG_CTX_LAYER_SHARE`(默认 0.45):其余要留给 system 模板、工具
    schema(直发窗口 18 个 ≈ 2k token)、messages[] 历史、以及输出预算 —— 这三样都
    **不是 layer**,不经过本模块(见 agents/gm/master.py 里 breakdown 的那段注释)。
    """
    import os

    if not api_id or not model_name:
        return 0
    try:
        from platform_app.usage import context_window_for
        window_tokens = int(context_window_for(api_id, model_name) or 0)
    except Exception:
        return 0
    if window_tokens <= 0:
        return 0
    try:
        share = float(os.getenv("RPG_CTX_LAYER_SHARE", "0.45"))
    except (TypeError, ValueError):
        share = 0.45
    share = min(max(share, 0.05), 0.9)
    # _estimate_tokens 用的是 len//2,这里按同一口径反算字符,保持全站一致
    return max(0, int(window_tokens * share) * 2)


__all__ = ["solve_layer_budgets", "layer_budget_chars"]
