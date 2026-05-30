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

# 常驻层缓存:同 script 的 constant 骨架每回合相同,缓存掉省一次 DB 读 + 拼装。
# 进程内(多 worker 各自一份,只读常量,无一致性问题)。编辑器改 constant 后用 invalidate 清。
_CONST_CACHE: dict[tuple[int, int], tuple[float, str]] = {}
_CONST_TTL = 300.0  # 秒


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
    parts = ["【世界观铁律 · 每轮常驻】"]
    used = _est_tokens(parts[0])
    for r in rows:
        block = f"· {r['title']}:{r['content']}"
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
