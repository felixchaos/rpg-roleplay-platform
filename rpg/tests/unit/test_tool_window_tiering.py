"""test_tool_window_tiering.py — 直发工具窗口的档位守卫(v1.82.0)。

窗口是**硬名额**:backend 只把前 N 个(core.config.tool_window_size(),默认 18)的完整
schema 直发给模型,其余进 load_tools 目录 —— 而实测模型极少主动 load,所以排在窗口外
约等于这个工具不存在。历史上因此丢过两批锚点工具,都是几个月后靠群反馈发现的。

本文件锁四件事:
  ① 没有工具落进 UNCLASSIFIED —— 新工具不选档就红,不许再有「静默落兜底」这条路
  ② 平台/账户管理工具永远不占窗口(它们此前靠同档字母序挤进去过)
  ③ 主回合闭环工具在任何模式、任何信号下都在窗口内
  ④ 相关性门只降不升:有信号时的窗口 ⊇ 旧行为里那些族
"""
from __future__ import annotations

import pytest

from agents.gm.backends._tiered import effective_window
from core.config import tool_window_size
from tools_dsl import command_tools_register as _reg
from tools_dsl.chat_tool_router import (
    _ANCHOR_HISTORY_SIDE,
    _PLATFORM_NAMES,
    _PRIMARY_TURN_READS,
    _SECONDARY_READ_NAMES,
    TIER_PLATFORM,
    TIER_UNCLASSIFIED,
    build_unified_tool_list,
    classify_tool,
    turn_signals,
    unclassified_tools,
)
from tools_dsl.command_dispatcher import get_registry


@pytest.fixture(scope="module", autouse=True)
def _registered():
    _reg.ensure_registered()


def _names() -> list[str]:
    return [s.name for s in get_registry().list_for_origin("llm_chat")]


def _window(**kw) -> list[str]:
    tools = build_unified_tool_list([], origin="llm_chat", **kw)
    return [t["name"] for t in tools][: effective_window(tools, tool_window_size())]


def test_registry_is_not_empty():
    """自检:注册表空的话下面每条断言都会假绿。"""
    assert len(_names()) > 50


def test_no_tool_falls_into_the_unclassified_tier():
    stray = unclassified_tools(_names())
    assert not stray, (
        "这些工具匹配不到任何显式档位规则,会落 UNCLASSIFIED、排在所有人后面、"
        "永远进不了直发窗口。请在 chat_tool_router 的档位表里给它们选一档:\n  "
        + "\n  ".join(stray))


def test_unclassified_tier_sorts_last():
    assert TIER_UNCLASSIFIED > TIER_PLATFORM


@pytest.mark.parametrize("sig", [None, turn_signals(),
                                 turn_signals(has_pending_anchors=True, bound_script_id=1)])
def test_platform_tools_never_occupy_the_window(sig):
    """用量/凭据/导入进度/存档管理与本回合叙事无关。

    实测(v1.81,104 个工具 / 窗口 18):get_import_status、get_my_stats、get_my_usage
    三个平台查询挤在窗口里,而 query_memory / get_worldbook / get_pending_questions
    全在窗口外 —— 因为同档 tie-break 是字母序。
    """
    win = set(_window(signals=sig))
    intruders = win & _PLATFORM_NAMES
    assert not intruders, f"平台工具占了窗口名额: {sorted(intruders)}"


@pytest.mark.parametrize("mode,bound", [("novel", None), ("tavern_gm", 1), ("tavern_gm", None)])
@pytest.mark.parametrize("sig", [None, turn_signals(),
                                 turn_signals(has_pending_anchors=True, bound_script_id=1)])
def test_turn_critical_tools_are_always_in_the_window(mode, bound, sig):
    """主回合闭环:玩家选择题 + 当前场景/状态 + 存档历史锚点。任何模式任何信号都不许掉。"""
    win = set(_window(mode=mode, bound_script_id=bound, signals=sig))
    registered = set(_names())
    must = {"ask_player_choice", "get_current_scene", "get_game_state",
            "get_pending_questions"} | set(_ANCHOR_HISTORY_SIDE)
    missing = {n for n in must if n in registered} - win
    assert not missing, f"[mode={mode} bound={bound}] 主回合工具被挤出窗口: {sorted(missing)}"


def test_ask_player_choice_regression():
    """gm.py 的文宗精简档显式保它(「否则 GM 无法弹玩家选择」),但**非 slim 档**它
    在 v1.81 落兜底档、进不了窗口 —— 同一个根因在另一半路径上没修。锁住。"""
    assert classify_tool("ask_player_choice") == 0
    assert "ask_player_choice" in _window()


def test_history_side_anchors_ignore_the_relevance_gate():
    """存档过去侧与剧本有没有 pending anchor 无关 —— 门控它就重演 v1.72.4
    那个「877 回合零玩家锚点」的 bug。"""
    for name in _ANCHOR_HISTORY_SIDE:
        assert classify_tool(name, signals=turn_signals()) == 0, name
        assert classify_tool(name, signals=None) == 0, name


def test_relevance_gate_only_demotes_never_promotes():
    """有信号时的档位 <= 无信号时的档位(数字小=靠前),且永不优于不门控时。"""
    full = turn_signals(has_pending_anchors=True, bound_script_id=1)
    empty = turn_signals()
    for name in _names():
        assert classify_tool(name, signals=full) <= classify_tool(name, signals=empty), name
        assert classify_tool(name, signals=full) == classify_tool(name, signals=None), name


def test_relevance_gate_frees_slots_when_nothing_is_relevant():
    """没锚点没剧本时,剧本侧锚点族与 canon 读让位,窗口里换进真正的主回合读取。"""
    gated = set(_window(signals=turn_signals()))
    assert "get_worldbook" in gated, "让位后世界书查询仍进不了窗口,门控没起作用"
    assert "search_canon" not in gated, "没绑剧本却仍把 canon 读留在窗口里"


def test_kill_switch_shape():
    """RPG_TOOL_RELEVANCE=0 的退路是「不传 signals」,等价于 v1.81 行为。"""
    assert _window(signals=None) == _window()


# ── v1.82.1:精简档 / 非精简档的工具可达性不许不对称 ──────────────────────────

def test_curated_slim_whitelist_is_reachable_without_slim():
    """`GM_ALL_KB_TOOLS` 的定义就是「文宗精简档把工具收成 12 个后**仍必须保留**的
    存档级 KB 维护工具」。非精简档却因为窗口名额够不到它们,等于同一份审定结论在两条
    路径上生效程度不同 —— v1.82.0 只把 ask_player_choice 从这个不对称里救出来,漏了
    同一份清单里的 4 个 kb_* 写工具(修 A 漏 B)。

    受相关性门控的成员(canon 读)不在断言范围:没绑剧本时它们本就该让位。
    """
    from gm_serving.serve import GM_KB_WRITE_TOOLS
    registered = set(_names())
    for sig in (None, turn_signals(),
                turn_signals(has_pending_anchors=True, bound_script_id=1)):
        win = set(_window(signals=sig))
        missing = {n for n in GM_KB_WRITE_TOOLS if n in registered} - win
        assert not missing, f"审定为「精简后仍必须保留」的工具进不了直发窗口: {sorted(missing)}"


def test_curated_list_is_derived_not_hand_copied():
    """名单靠 import 派生 —— 往 GM_ALL_KB_TOOLS 加工具时不需要有人记得来这里补名字。"""
    from gm_serving.serve import GM_ALL_KB_TOOLS
    from tools_dsl.chat_tool_router import _TURN_CRITICAL_NAMES
    assert set(GM_ALL_KB_TOOLS) <= _TURN_CRITICAL_NAMES


def test_primary_turn_reads_are_always_in_the_window():
    """首要读=「这个存档此刻的状态」。v1.81 里它们输给字母序:get_import_status /
    get_my_usage 压过 query_memory / get_worldbook。"""
    registered = set(_names())
    for sig in (None, turn_signals(),
                turn_signals(has_pending_anchors=True, bound_script_id=1)):
        win = set(_window(signals=sig))
        missing = {n for n in _PRIMARY_TURN_READS if n in registered} - win
        assert not missing, f"主回合首要读被挤出窗口: {sorted(missing)}"


def test_editor_enumerations_do_not_outrank_primary_reads():
    """`list_anchors`/`list_canon_entities` 按自己的描述就是「更新前先拿 id」的编辑域
    清单,不该跟主回合读抢名额。"""
    for name in _SECONDARY_READ_NAMES:
        for primary in _PRIMARY_TURN_READS:
            assert classify_tool(name) > classify_tool(primary), (name, primary)


def test_relevance_gate_still_beats_the_curated_list():
    """canon 读同时出现在审定清单与门控族里 —— 两个权威冲突时门赢,否则没绑剧本
    也会把 canon 读留在窗口里白占名额。"""
    empty = turn_signals()
    for name in ("search_canon", "lookup_entity", "lookup_timeline", "graph_neighbors"):
        assert classify_tool(name, signals=empty) > 0, name
        assert classify_tool(name, signals=turn_signals(bound_script_id=1)) == 0, name


def test_window_only_grows_when_there_is_something_to_use():
    """定容不变量不许把窗口在「什么都用不上」的局面下也撑大。"""
    idle = len(_window(signals=turn_signals()))
    busy = len(_window(signals=turn_signals(has_pending_anchors=True, bound_script_id=1)))
    assert idle < busy, (idle, busy)
    assert idle <= tool_window_size() + 2, f"空闲局面窗口撑到 {idle},代价没有兑现"
