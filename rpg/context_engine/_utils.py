"""context_engine._utils — 轻量工具函数（无业务依赖）."""
from __future__ import annotations

import re
import hashlib
from typing import Any


def _layer(layer_id: str, title: str, content: str, **extra) -> dict[str, Any]:
    return {"id": layer_id, "title": title, "content": content or "", **extra}


def _trim(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20].rstrip() + "\n……（已按预算截断）"


def _preview(text: str, limit: int = 140) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def _estimate_tokens(text: str) -> int:
    return max(1, len(text or "") // 2)


def _cache_plan(debug_layers: list[dict[str, Any]], prompt_parts: list[str]) -> dict[str, Any]:
    # 与 build_context_bundle 的 layer 顺序对齐：rules → agent_runtime → player_card
    # 是真正的稳定前缀，每轮不变；后面接 npc_cards/worldbook 算半稳定（可选纳入）。
    strict_stable_ids = ["rules", "agent_runtime", "player_card"]
    semi_stable_ids = ["npc_cards", "worldbook"]

    stable_chars = 0
    stable_tokens = 0
    stable_titles: list[str] = []
    semi_chars = 0
    semi_tokens = 0
    semi_titles: list[str] = []
    # 严格按 layer 顺序累加，遇到第一个不属于"已知稳定前缀"的就 break
    i = 0
    for layer in debug_layers:
        lid = layer["id"]
        if lid == strict_stable_ids[i] if i < len(strict_stable_ids) else False:
            stable_chars += int(layer.get("chars", 0))
            stable_tokens += int(layer.get("estimated_tokens", 0))
            stable_titles.append(layer.get("title", ""))
            i += 1
            continue
        # 严格稳定结束后，紧接着如果是 semi-stable 也算缓存候选
        if lid in semi_stable_ids and i >= len(strict_stable_ids):
            semi_chars += int(layer.get("chars", 0))
            semi_tokens += int(layer.get("estimated_tokens", 0))
            semi_titles.append(layer.get("title", ""))
            continue
        break
    total_tokens = sum(int(layer.get("estimated_tokens", 0)) for layer in debug_layers)
    joined_stable = "\n\n".join(prompt_parts[:len(stable_titles)])
    extended_titles = stable_titles + semi_titles
    extended_chars = stable_chars + semi_chars
    extended_tokens = stable_tokens + semi_tokens
    joined_extended = "\n\n".join(prompt_parts[:len(extended_titles)])
    return {
        "strategy": "stable-prefix-first",
        "request_shape": "rules -> agent_runtime -> player_card -> (npc/world) -> dynamic -> user_input",
        # 严格稳定（rules/agent_runtime/player_card）
        "stable_prefix_layers": stable_titles,
        "stable_prefix_chars": stable_chars,
        "stable_prefix_tokens": stable_tokens,
        # 扩展候选（包含 npc_cards/worldbook，玩家不换角色时也稳定）
        "cacheable_prefix_layers": extended_titles,
        "cacheable_prefix_chars": extended_chars,
        "cacheable_prefix_tokens": extended_tokens,
        "volatile_tail_tokens": max(0, total_tokens - extended_tokens),
        "estimated_cacheable_ratio": round(extended_tokens / max(total_tokens, 1), 3),
        "strict_stable_ratio": round(stable_tokens / max(total_tokens, 1), 3),
        "stable_prefix_hash": hashlib.sha256(joined_stable.encode("utf-8")).hexdigest()[:16] if joined_stable else "",
        "cacheable_prefix_hash": hashlib.sha256(joined_extended.encode("utf-8")).hexdigest()[:16] if joined_extended else "",
        "note": "真实缓存命中率由模型厂商返回的用量字段确认；当前请求形状把动态 RAG/context_agent/recent_chat 都放到末尾。",
    }
