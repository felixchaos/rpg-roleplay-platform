"""流水线去 fork · 批次3:确定性 P2 簇(层预算/缓存/污染/竞态/工具集)。"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def test_tavern_module_layers_have_budget():
    from context_engine._constants import MAX_LAYER_CHARS
    for k in ("tavern_character", "tavern_card_system", "tavern_persona",
              "module_scene", "module_encounter", "rules_state"):
        assert (MAX_LAYER_CHARS.get(k) or 0) >= 1800, f"{k} 仍走默认 1800 截断"


def test_novel_provider_neutralizes_state_tags():
    from context_providers import novel
    src = inspect.getsource(novel.NovelRetrievalProvider.collect)
    assert "_neutralize_state_write_tags" in src or "_neu(" in src, \
        "novel RAG body 未中和【】状态标签 → GM 复述可能误触发写入"


def test_rules_provider_dynamic_layer_not_a_tier():
    from context_providers import rules
    src = inspect.getsource(rules.RulesProvider.collect)
    # 动态 HP/骰子层必须显式 C 级缓存 + 独立 id(不与静态 rules A 级撞)
    assert 'cache_tier="C"' in src or "cache_tier='C'" in src
    assert '"rules_state"' in src or "'rules_state'" in src


def test_saves_rewind_uses_atomic_jsonb_set():
    saves = (REPO / "platform_app" / "api" / "saves.py").read_text(encoding="utf-8")
    # 进度回退端点必须原子 jsonb_set,不再整列读-改-写(workers=2 竞态)
    assert "jsonb_set" in saves and "'{progress_chapter}'" in saves


def test_acceptance_rewrite_does_not_continue_first_draft():
    """行者无疆(改写变续写)修复:改写候选不再走 respond_stream_with_tools 追加到实时历史(record_turn
    后历史含首稿 → 模型续写),改为【首稿历史快照 + 玩家行动 + 首稿作为待改写对象】文本直调 backend,
    并明确『不是续写』。此前『用同一工具集』的契约已过时(用同一调用路径正是续写根因)。"""
    cp = "\n".join(_p.read_text(encoding="utf-8") for _p in sorted((REPO / "chat_pipeline").glob("*.py")))
    helper = cp.split("def _rewrite_candidate_text", 1)[1].split("async def _gen_candidate_bg", 1)[0]
    assert "gm._backend.stream" in helper, "改写候选应文本直调 backend(不进工具循环、不追加实时历史)"
    assert "_pre_hist" in helper, "改写候选须用首稿时的历史快照重建上下文"
    assert ("不是续写" in helper) or ("不要接着往下写" in helper), "改写指令须明确『不是续写』"
    gate = cp.split("def _acceptance_gate", 1)[1].split("# ── W1 容量优化", 1)[0]
    assert "gm.respond_stream_with_tools" not in gate, "改写候选不应再走会续写的 respond_stream_with_tools"
    assert "list(state.history_messages())" in gate and "ctx.message_for_model" in gate


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
