"""手动添加的能力/资源随剧情推进消失(条目减少)—— 行者无疆反馈。

根因:context_providers.memory._maybe_auto_archive 每 summary_window 轮把 turn 早于
(current_turn - auto_archive_after_turns) 的记忆条目标 archived 并**从 legacy bucket 移除**。
notes/pinned 已豁免(v1.27.4),但 **abilities / resources 没豁免** —— 它们是【角色卡式持久状态】
(能力/物品/货币),不该因回合数增长而静默消失(手动添加的尤其荒谬)。只有 facts(叙事流水)该归档。

默认 MemorySettings:auto_archive_after_turns=50,summary_window=10 → 回合 60 时归档 turn<10 的条目。
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _state_with(bucket: str, text: str, item_turn: int, current_turn: int) -> dict:
    return {
        "turn": current_turn,
        "memory": {
            bucket: [text],
            "items": [{
                "id": "m1", "text": text, "legacy_bucket": bucket,
                "turn": item_turn, "status": "active", "kind": "runtime_fact",
                "source": "legacy_add_memory",
            }],
        },
    }


def _archive(state):
    from context_providers.memory import _maybe_auto_archive
    from schemas.memory import MemorySettings
    _maybe_auto_archive(state, MemorySettings())  # 默认:archive_after=50, window=10


def test_manual_ability_survives_auto_archive():
    st = _state_with("abilities", "飞行术", item_turn=1, current_turn=60)
    _archive(st)
    assert "飞行术" in st["memory"]["abilities"], "能力在剧情推进后被自动归档移除(条目减少)"
    # 结构化 item 也不该被标 archived(否则 items 视图也丢)
    assert not st["memory"]["items"][0].get("archived"), "能力 item 被标 archived"


def test_manual_resource_survives_auto_archive():
    st = _state_with("resources", "储物戒指×1", item_turn=1, current_turn=60)
    _archive(st)
    assert "储物戒指×1" in st["memory"]["resources"], "资源在剧情推进后被自动归档移除"


def test_facts_still_auto_archive():
    """facts 是叙事流水,该照常归档(不因本次修复而失效)。"""
    st = _state_with("facts", "路边有只猫经过", item_turn=1, current_turn=60)
    _archive(st)
    assert "路边有只猫经过" not in st["memory"]["facts"], "facts 应仍被归档移出 bucket"
    assert st["memory"]["items"][0].get("archived") is True


def test_real_gamestate_add_memory_ability_survives():
    """走真实 GameState.add_memory 写入路径(玩家手动加能力的实际后端路径),
    过 auto_archive 阈值后 MemoryProvider 触发归档,能力仍在。"""
    from context_providers.memory import _maybe_auto_archive
    from schemas.memory import MemorySettings
    from state import GameState

    g = GameState.new()
    g.data["turn"] = 1
    assert g.add_memory("abilities", "御剑飞行")   # 手动加,turn=1
    assert g.add_memory("resources", "灵石×99")
    assert g.add_memory("facts", "今天天气不错")   # 叙事流水,turn=1
    g.data["turn"] = 60                            # 剧情推进过阈值(50)+ 命中窗口(10)
    _maybe_auto_archive(g, MemorySettings())
    assert "御剑飞行" in g.data["memory"]["abilities"], "手动能力被归档移除(条目减少)"
    assert "灵石×99" in g.data["memory"]["resources"], "手动资源被归档移除"
    assert "今天天气不错" not in g.data["memory"]["facts"], "facts 应仍归档"
