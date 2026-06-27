"""玩家笔记/固定记忆不被自动归档 + GM 优先读手记(行者无疆反馈)。

bug:容易变动的数值,玩家写进笔记后 AI GM 不读、改用事实库的过时数据。
根因①(主):context_providers.memory._maybe_auto_archive 把 turn 旧的 notes/pinned
        条目标 archived 并【从 bucket 移除】→ MemoryProvider(读 legacy bucket)不再
        渲染 → 几十回合后 GM 看不到玩家笔记/固定记忆。pinned(固定记忆)被归档尤其荒谬。
根因②:玩家笔记与自动累积事实混在一起、无优先级提示,GM 可能采信过时事实。
修:notes/pinned 永久豁免自动归档;MemoryProvider 注入「手记权威·覆盖事实」优先级提示;
   _fact_groups_layer 排除 archived(让归档真正减少过时数据漏进上下文)。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from context_engine.layers import _fact_groups_layer  # noqa: E402
from context_providers.memory import MemoryProvider, _maybe_auto_archive  # noqa: E402
from schemas.memory import MemorySettings  # noqa: E402


class _State:
    def __init__(self, data):
        self.data = data


class _Svc:
    user_id = None


def _long_session_state(turn):
    return _State({
        "turn": turn,
        "memory": {
            "notes": ["金币现在是500"],
            "pinned": ["主角真实身份是穿越者"],
            "facts": ["金币是100"],
            "items": [
                {"id": "n1", "kind": "runtime_fact", "text": "金币现在是500", "turn": 1, "status": "active", "legacy_bucket": "notes"},
                {"id": "p1", "kind": "runtime_fact", "text": "主角真实身份是穿越者", "turn": 1, "status": "active", "legacy_bucket": "pinned"},
                {"id": "f1", "kind": "runtime_fact", "text": "金币是100", "turn": 1, "status": "active", "legacy_bucket": "facts"},
            ],
        },
    })


def _archive_turn(ms):
    """选一个会触发归档、且 turn=1 的旧条目落在 cutoff 之前的回合数。"""
    return ((ms.auto_archive_after_turns // ms.summary_window) + 2) * ms.summary_window


def test_auto_archive_exempts_notes_and_pinned_but_archives_facts():
    ms = MemorySettings()  # auto_archive_after_turns=50, summary_window=10
    s = _long_session_state(_archive_turn(ms))
    _maybe_auto_archive(s, ms)
    mem = s.data["memory"]
    items = {i["id"]: i for i in mem["items"]}

    # 玩家笔记/固定记忆:绝不归档,仍留在 bucket(GM/面板都还看得到)
    assert not items["n1"].get("archived"), "玩家笔记被误自动归档"
    assert not items["p1"].get("archived"), "固定记忆(pinned)被误自动归档"
    assert "金币现在是500" in mem["notes"]
    assert "主角真实身份是穿越者" in mem["pinned"]

    # 自动累积的事实:照常归档 + 移出 bucket(减少过时数据)
    assert items["f1"].get("archived") is True
    assert "金币是100" not in mem["facts"]


def test_memory_provider_still_renders_notes_after_long_session_with_precedence():
    ms = MemorySettings()
    s = _long_session_state(_archive_turn(ms))
    text = MemoryProvider().collect(s, manifest=None, demand=None, services=_Svc()).layers[0]["content"]

    # 根因①修复:长会话归档后,玩家笔记/固定记忆仍被渲染给 GM
    assert "笔记：金币现在是500" in text, "长会话后玩家笔记从 GM 上下文消失(根因①)"
    assert "固定记忆：主角真实身份是穿越者" in text
    # 过时事实已归档移除,不再喂给 GM
    assert "金币是100" not in text
    # 根因②:优先级提示,GM 应以玩家手记为准
    assert "以笔记/固定记忆为准" in text


def test_fact_groups_layer_excludes_archived_items():
    s = _State({"turn": 5, "memory": {"items": [
        {"kind": "runtime_fact", "text": "新鲜事实", "turn": 5, "status": "active"},
        {"kind": "runtime_fact", "text": "过时归档事实", "turn": 1, "status": "active", "archived": True},
    ]}})
    out = _fact_groups_layer(s)
    assert "新鲜事实" in out
    assert "过时归档事实" not in out, "归档旧事实仍漏进 GM 上下文(过时数据)"


def test_no_precedence_hint_when_no_player_notes():
    """没有玩家手记时不注入优先级提示(零额外开销/零行为变化)。"""
    s = _State({"turn": 1, "memory": {"facts": ["某事实"], "items": []}})
    text = MemoryProvider().collect(s, manifest=None, demand=None, services=_Svc()).layers[0]["content"]
    assert "记忆优先级" not in text
