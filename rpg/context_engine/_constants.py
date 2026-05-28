"""context_engine._constants — 共享常量."""
from __future__ import annotations

from pathlib import Path

BASE = Path(__file__).parent.parent
CHAR_IDX = BASE / "indexes" / "characters.json"
WORLD_IDX = BASE / "indexes" / "world.json"

MAX_LAYER_CHARS = {
    "rules": 1800,
    "agent_runtime": 1200,
    "timeline": 1400,
    "worldline": 1800,
    "context_agent": 1200,
    "player_card": 1300,
    "npc_cards": 1800,
    "worldbook": 2200,
    "rag": 2200,
    "state": 2200,
    "state_schema": 1600,   # task 59：字段 schema + 已知 NPC enum，前 20 个
    "write_results": 800,   # task 54：上轮标签结果反馈，简洁即可
    "fact_groups": 1600,    # task 76：canon / runtime / user_constraint 分组渲染
    "hypotheses": 700,      # task 75：未确认推测，最多 8 条 short label
    "candidate_actions": 800,  # task 82：curator 列的 2-5 个候选动作 anchor
    "recent_chat": 2200,
    "user_input": 900,
    # task 107E: 双时间线 — 存档级历史摘要 + 剧本未来预期
    "runtime_phase_digests": 1800,        # GM 思考历史 (本存档)
    "script_phase_anticipation": 1200,    # GM 思考未来 (剧本预期)
}
