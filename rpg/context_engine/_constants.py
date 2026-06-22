"""context_engine._constants — 共享常量."""
from __future__ import annotations

from pathlib import Path

BASE = Path(__file__).parent.parent
CHAR_IDX = BASE / "indexes" / "characters.json"
WORLD_IDX = BASE / "indexes" / "world.json"

# GM 上下文预算 —— 这些是每层的 char 上限(≈ /2 = token)。
# 原值是给 8k 小窗模型设计的,导致整轮总上下文只有 ~4k token,而生产模型(deepseek-v4-pro
# 128k / gemini 1M)能吃几十万 token → 小说正文/角色卡/世界书被严重截断,GM 写不出原著
# 细节与文风、推进缓慢。这里整体放开到能装真正有用的素材;可用 RPG_CTX_SCALE 整体缩放。
import os as _os
try:
    _CTX_SCALE = max(0.25, float(_os.environ.get("RPG_CTX_SCALE", "1.0")))
except (TypeError, ValueError):
    _CTX_SCALE = 1.0

_BASE_LAYER_CHARS = {
    "rules": 2000,
    "agent_runtime": 1600,
    "timeline": 2400,
    "timeline_pending": 2400,     # provider 实际层 id,补全防默认 1800 截断
    "novel_timeline": 2400,
    "memory": 4000,
    "worldline": 3000,
    "worldline_directive": 3000,   # task 140: 玩家给 GM 的高优先级导演指令
    "anchor_pending": 8000,        # 世界线收束·接下来的锚点 — ch1 通常 8+ 实体
    "context_agent": 2400,
    "player_card": 2400,
    "npc_cards": 12000,            # 多 NPC 同台 → 别只塞 4 张卡
    "worldbook": 10000,
    "novel_worldbook": 10000,     # ★ 实际 provider 层 id 是这个,不是 "worldbook" → 之前走默认 1800
    "module_worldbook": 10000,
    "rag": 16000,                 # 旧 caller 兜底路径
    "novel_retrieval": 20000,     # ★ 关键:真正的小说正文 RAG(原来不在字典→默认 1800 被砍)
    "state": 3000,
    "state_schema": 1600,   # 纯 schema 模板,不需要长,保持精简
    "write_results": 1000,  # 上轮标签结果反馈,简洁即可
    "fact_groups": 4000,    # canon / runtime / user_constraint 分组渲染
    "hypotheses": 1200,
    "candidate_actions": 1600,
    "recent_chat": 16000,         # 多保留对话历史 → 连贯性
    "user_input": 2400,
    # task 107E: 双时间线 — 存档级历史摘要 + 剧本未来预期
    "runtime_phase_digests": 5000,        # GM 思考历史 (本存档)
    "script_phase_anticipation": 4000,    # GM 思考未来 (剧本预期)
}
MAX_LAYER_CHARS = {k: int(v * _CTX_SCALE) for k, v in _BASE_LAYER_CHARS.items()}

# Q 三贤者分层缓存:层 id → cache_tier。
#   A 会话级稳定 = 逐回合字节恒等 → 厂商缓存真命中(放可缓存前缀)。
#   B 场景级稳定 = 一幕戏内稳定,换场/换章才变(打断点免费:命中就赚,不中退化全价)。
#   C 回合动态   = 每回合变,永不缓存(放末尾)。
# provider 层可用 make_layer(cache_tier=...) 覆盖;未列出的层兜底 "C"。
# 详见 docs/design/Q_three_sage_pipeline.md §5。
LAYER_CACHE_TIER = {
    # ── A 会话级稳定 ──
    "rules": "A",
    "agent_runtime": "A",
    "player_card": "A",
    "state_schema": "A",
    "worldline_directive": "A",      # 玩家给 GM 的高优先级导演指令,改动很少
    "tavern_card_system": "A",       # 酒馆卡内嵌 system_prompt
    "tavern_character": "A",         # 酒馆角色定义
    "tavern_persona": "A",           # 玩家 persona
    # ── B 场景级稳定 ──
    "npc_cards": "B",
    "worldbook": "B",
    "novel_worldbook": "B",
    "module_worldbook": "B",
    "anchor_pending": "B",
    "novel_timeline": "B",
    "timeline": "B",
    "script_phase_anticipation": "B",
    "module_scene": "B",
    "module_encounter": "B",
    # ── C 回合动态(显式列出便于审计;未列出也兜底 C)──
    "timeline_pending": "C",
    "state": "C",
    "fact_groups": "C",
    "memory": "C",
    "worldline": "C",
    "write_results": "C",
    "hypotheses": "C",
    "context_agent": "C",
    "candidate_actions": "C",
    "novel_retrieval": "C",
    "rag": "C",
    "recent_chat": "C",
    "runtime_phase_digests": "C",
    "user_input": "C",
}


def layer_cache_tier(layer: dict) -> str:
    """解析单层的 cache_tier:层显式 > 中央映射 > 兜底 C。"""
    t = (layer.get("cache_tier") or "").strip().upper()
    if t in ("A", "B", "C"):
        return t
    return LAYER_CACHE_TIER.get(layer.get("id", ""), "C")
