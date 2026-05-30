"""gm_serving/context_inject.py — Phase D 第①层常驻注入 + 预算(D §3①/§4)。

常驻层 = 世界观骨架(constant worldbook,治 1935)+ 当前场景 + 下一规范世界线锚点软目标。
constant 每轮无条件注入、prompt 缓存(决策2);预算 per-script 计算 + 封顶 ~3K。
"""
from __future__ import annotations

# 粗略 token 估算:中文 ~1.5 char/token,英文 ~4 char/token。保守按 1.6 char/token。
_CHARS_PER_TOKEN = 1.6
_BUDGET_MIN = 800
_BUDGET_MAX = 3000


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


def build_constant_layer(db, script_id: int, *, budget_tokens: int | None = None) -> str:
    """读 constant worldbook,按 priority 拼装到预算上限。这是治 1935 的常驻骨架。"""
    if budget_tokens is None:
        budget_tokens = compute_budget(db, script_id)
    rows = db.execute(
        "select title, content from worldbook_entries "
        "where script_id=%s and insertion_position='constant' and enabled=true "
        "order by priority desc, id",
        (script_id,),
    ).fetchall()
    if not rows:
        return ""
    parts = ["【世界观铁律 · 每轮常驻】"]
    used = _est_tokens(parts[0])
    for r in rows:
        block = f"· {r['title']}:{r['content']}"
        t = _est_tokens(block)
        if used + t > budget_tokens:
            break
        parts.append(block)
        used += t
    return "\n".join(parts)


def build_injection(db, *, script_id: int, scene_summary: str = "", steering_hint: str = "",
                    budget_tokens: int | None = None) -> dict:
    """组装第①层常驻注入。返回 {text, tokens, budget}。"""
    budget = budget_tokens if budget_tokens is not None else compute_budget(db, script_id)
    constant = build_constant_layer(db, script_id, budget_tokens=budget)
    blocks = [constant]
    if scene_summary:
        blocks.append(f"【当前场景】{scene_summary}")
    if steering_hint:
        blocks.append(f"【剧情软目标(引导非铁轨)】{steering_hint}")
    text = "\n\n".join(b for b in blocks if b)
    return {"text": text, "tokens": _est_tokens(text), "budget": budget}
