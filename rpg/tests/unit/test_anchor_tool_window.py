"""test_anchor_tool_window.py — 锚点/存档历史族的「直发工具窗口」奇偶守卫。

背景(两次前科,同一族分两批被发现):
  ① mark_anchor_satisfied / mark_anchor_superseded 名字落 _rank 兜底档 3,窗口满时被截,
     GM 拿不到 schema → 剧情锚点「推不动」。修法是把这几个名字塞进 _rank 的字面量元组。
  ② 那次只修了「剧本未来侧」,漏了「存档过去侧」:record_history_anchor 在 100 个工具里
     排 89、list_recent_history 排 39,双双在窗口(16)外。生产实证:全站 gm_generated
     历史锚点仅 10 条 / 3 个存档,一个跑了 877 回合的存档一条都没有——玩家看到的两条
     全是 phase digest 写的 system 档(群反馈「50 多章从来没创造过玩家锚点」)。

窗口是**硬名额**不是软优先级:挤出去的工具等于消失(模型极少主动 load_tools)。所以本文件
锁两件事——族成员资格别再漏人、提权子集必须真的落在窗口内(novel 与 tavern_gm 两模式)。
"""
from __future__ import annotations

import pytest

from core.config import tool_window_size
from tools_dsl import command_tools_register as _reg
from tools_dsl.chat_tool_router import (_ANCHOR_FAMILY, _ANCHOR_WINDOW_PROMOTED,
                                        build_unified_tool_list)
from tools_dsl.command_dispatcher import get_registry

# list_anchors / update_anchor 属**剧本编辑**域(script_timeline_anchors 表,给编辑器用),
# 与本族的 save_anchor_states / save_history_anchors 是两套表两套语义 —— 刻意不入族。
_NOT_FAMILY = frozenset(("list_anchors", "update_anchor"))

_MODES = (("novel", None), ("tavern_gm", 1))


@pytest.fixture(scope="module", autouse=True)
def _registered():
    _reg.ensure_registered()


def _window(mode: str, bound_script_id: int | None) -> list[str]:
    names = [t["name"] for t in build_unified_tool_list(
        [], origin="llm_chat", mode=mode, bound_script_id=bound_script_id)]
    return names[: tool_window_size()]


def test_family_covers_every_registered_anchor_or_history_tool():
    """新增锚点/历史工具必须显式进族(或显式进 _NOT_FAMILY),不许悄悄落 rank 3。"""
    registered = {s.name for s in get_registry().list_for_origin("llm_chat")}
    suspects = {n for n in registered
                if ("anchor" in n.lower() or "history" in n.lower()) and n not in _NOT_FAMILY}
    assert suspects <= _ANCHOR_FAMILY, (
        f"这些锚点/历史工具没进 _ANCHOR_FAMILY,会落 _rank 兜底档、被挤出直发窗口: "
        f"{sorted(suspects - _ANCHOR_FAMILY)}")


def test_promoted_subset_is_inside_family():
    assert _ANCHOR_WINDOW_PROMOTED <= _ANCHOR_FAMILY


@pytest.mark.parametrize("mode,bound", _MODES)
def test_promoted_anchor_tools_are_in_direct_window(mode, bound):
    win = set(_window(mode, bound))
    registered = {s.name for s in get_registry().list_for_origin("llm_chat")}
    expected = {n for n in _ANCHOR_WINDOW_PROMOTED if n in registered}
    assert expected <= win, (
        f"[{mode}] 这些工具被挤出直发窗口(GM 拿不到 schema): {sorted(expected - win)}")


@pytest.mark.parametrize("mode,bound", _MODES)
def test_core_state_reads_survive_the_promotion(mode, bound):
    """提权不许把核心状态读挤出去——这正是把窗口从 16 提到 18 的原因(只增不减)。"""
    win = set(_window(mode, bound))
    for name in ("get_current_scene", "get_chapter_context", "get_chapter_facts", "search_canon"):
        assert name in win, f"[{mode}] 核心工具 {name} 被挤出直发窗口"


def test_tavern_self_management_tools_still_lead():
    """酒馆自管理工具 rank=-1,必须仍在最前(锚点提权不许抢它们的位)。"""
    win = _window("tavern_gm", 1)
    assert all(w.startswith(("set_tavern_", "edit_tavern_", "tavern_")) for w in win[:5]), win[:5]
