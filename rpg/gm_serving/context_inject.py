"""gm_serving/context_inject.py — Phase D 第①层常驻注入 + 预算(D §3①/§4)。

常驻层 = 世界观骨架(constant worldbook,治 1935)+ 当前场景 + 下一规范世界线锚点软目标。
constant 每轮无条件注入、prompt 缓存(决策2);预算 per-script 计算 + 封顶 ~3K。
"""
from __future__ import annotations

import time

# 粗略 token 估算:中文 ~1.5 char/token,英文 ~4 char/token。保守按 1.6 char/token。
_CHARS_PER_TOKEN = 1.6
_BUDGET_MIN = 800
_BUDGET_MAX = 3000

# 常驻层缓存:同 script 的 constant 骨架每回合相同,缓存掉省一次 DB 读 + 拼装。进程内(每 worker 一份)。
# ⚠️ 跨 worker 一致性:invalidate_constant_cache 只清当前 worker 的字典;workers=2 下另一 worker 编辑后
# 仍服务旧 constant 直到 TTL 过期。TTL 由 300s 收到 30s,把这段「改了世界书但某 worker 仍喂旧版」的窗口
# 限到 ≤30s(彻底解需 redis pub/sub 广播失效,见审计待办)。代价仅多一点 constant DB 重读。
_CONST_CACHE: dict[tuple[int, int], tuple[float, str]] = {}
_CONST_TTL = 30.0  # 秒(跨 worker 失效窗口上界)


def invalidate_constant_cache(script_id: int | None = None) -> None:
    """规范层 constant 被编辑后清缓存(None=全清)。"""
    if script_id is None:
        _CONST_CACHE.clear()
    else:
        for k in [k for k in _CONST_CACHE if k[0] == script_id]:
            _CONST_CACHE.pop(k, None)


def _est_tokens(text: str) -> int:
    return int(len(text) / _CHARS_PER_TOKEN)


def compute_budget(db, script_id: int) -> int:
    """per-script 常驻预算:base + 每条 constant 条目权重,clamp [800,3000]。"""
    n = db.execute(
        "select count(*) c from worldbook_entries where script_id=%s and insertion_position='constant'",
        (script_id,),
    ).fetchone()["c"]
    budget = 600 + n * 200
    return max(_BUDGET_MIN, min(_BUDGET_MAX, budget))


def build_constant_layer(db, script_id: int, *, budget_tokens: int | None = None, use_cache: bool = True) -> str:
    """读 constant worldbook,按 priority 拼装到预算上限。这是治 1935 的常驻骨架。"""
    if not script_id:
        # 无剧本(酒馆未绑剧本/自由模式)没有常驻世界书可注入
        return ""
    # pin 重定向:引用剧本(pinned/floating)读 pin 目标的常驻世界书(纯读取)。
    from platform_app.knowledge._pin import effective_kb_script_id
    script_id = effective_kb_script_id(db, script_id)
    if budget_tokens is None:
        budget_tokens = compute_budget(db, script_id)
    ckey = (int(script_id), int(budget_tokens))
    if use_cache:
        hit = _CONST_CACHE.get(ckey)
        if hit and (time.monotonic() - hit[0]) < _CONST_TTL:
            return hit[1]
    rows = db.execute(
        "select title, content from worldbook_entries "
        "where script_id=%s and insertion_position='constant' and enabled=true "
        "order by priority desc, id",
        (script_id,),
    ).fetchall()
    if not rows:
        if use_cache:
            _CONST_CACHE[ckey] = (time.monotonic(), "")
        return ""
    # SEC(C-2/H-12): constant 世界书内容会逐字进所有订阅者的 GM 最高优先级层。中和 【】 状态写入
    # 标签,切断「订阅恶意公开剧本 → 注入伪指令 → apply_structured_updates 落库」链路。
    from context_engine.helpers import _neutralize_state_write_tags as _neu
    parts = ["【世界观铁律 · 每轮常驻】"]
    used = _est_tokens(parts[0])
    for r in rows:
        block = f"· {_neu(r['title'])}:{_neu(r['content'])}"
        t = _est_tokens(block)
        if used + t > budget_tokens:
            break
        parts.append(block)
        used += t
    out = "\n".join(parts)
    if use_cache:
        _CONST_CACHE[ckey] = (time.monotonic(), out)
    return out


def build_injection(db, *, script_id: int, scene_summary: str = "", steering_hint: str = "",
                    steering_strength: str = "guided", budget_tokens: int | None = None) -> dict:
    """组装第①层常驻注入。返回 {text, tokens, budget}。

    steering_strength 决定 steering_hint 的**外层包裹标签强弱**——这是「贴原著」此前退化成
    与「软引导」一样温和的真根因:旧代码无条件用「软目标(引导非铁轨)」包裹,把 steering.py
    已产出的 rail 强措辞又软化掉。现按强度分档:
      rail   — 「世界线收束 · 强制下一拍(必须推进)」硬标签,与内文强措辞一致。
      其它   — 「剧情软目标(引导非铁轨)」温和标签(保持软引导/自由行为不变)。
    """
    budget = budget_tokens if budget_tokens is not None else compute_budget(db, script_id)
    constant = build_constant_layer(db, script_id, budget_tokens=budget)
    blocks = [constant]
    if scene_summary:
        blocks.append(f"【当前场景】{scene_summary}")
    if steering_hint:
        if steering_strength == "rail":
            blocks.append(f"【世界线收束 · 强制下一拍(必须推进)】{steering_hint}")
        else:
            blocks.append(f"【剧情软目标(引导非铁轨)】{steering_hint}")
    text = "\n\n".join(b for b in blocks if b)
    return {"text": text, "tokens": _est_tokens(text), "budget": budget}
