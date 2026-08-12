"""流水线去 fork · 批次7:收尾全清(C 主线派生 + 死代码删 + 提示词对齐 + _default_judge)。"""
import inspect
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def test_main_quest_derived_from_phase_nondestructive():
    import retrieval
    src = inspect.getsource(retrieval.retrieve_context)
    # C:从 phase 派生 main_quest,且非破坏(仅空/等于上次派生值时刷新)
    assert "_resolve_active_phase_range" in src and "main_quest" in src
    assert "_derived_main_quest" in src  # 保护手写主线的标记


def test_default_judge_produces_progress_motion():
    from gm_serving import anchor_reconcile
    src = inspect.getsource(anchor_reconcile._default_judge)
    assert '"progress_motion": progress_motion' in src


def test_worldline_provider_has_permission_mode_behavior():
    from context_providers import worldline
    src = inspect.getsource(worldline.WorldlineProvider.collect)
    assert "本轮写入权限行为" in src and "read_only" in src


def test_worldline_layer_dead_code_removed():
    import context_engine
    assert not hasattr(context_engine, "_worldline_layer")


def test_candidate_actions_wording_consistent():
    # master.py 与 rules_text 都应「不强制」,不再「不得原创」
    m = (REPO / "agents" / "gm" / "master.py").read_text(encoding="utf-8")
    assert "不要原创一个不在候选里的动作" not in m
    assert "不强制" in m
