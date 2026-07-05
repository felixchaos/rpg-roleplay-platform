"""时间连续性护栏 v0(天数倒退检测)单测。

哲学同星期验错器:确定性检测→surface,不拦截;标签解析不出「第N天」即休眠。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from state.time_ops import _cjk_to_int, _day_number, detect_day_regression  # noqa: E402


def test_cjk_to_int_common_forms():
    assert _cjk_to_int("3") == 3
    assert _cjk_to_int("三") == 3
    assert _cjk_to_int("十") == 10
    assert _cjk_to_int("十五") == 15
    assert _cjk_to_int("二十三") == 23
    assert _cjk_to_int("一百零五") == 105
    assert _cjk_to_int("两") == 2
    assert _cjk_to_int("甲") is None
    assert _cjk_to_int("") is None


def test_day_number_extraction():
    assert _day_number("第三天·正午") == 3
    assert _day_number("第5天·入夜") == 5
    assert _day_number("第二十三天清晨") == 23
    assert _day_number("申时三刻") is None      # 无天数计法 → 休眠
    assert _day_number("次日清晨") is None
    assert _day_number("") is None


def test_regression_detected():
    msg = detect_day_regression("第五天·清晨", "第三天·入夜")
    assert msg and "第5天" in msg and "第3天" in msg


def test_no_regression_forward_or_same():
    assert detect_day_regression("第三天·正午", "第三天·入夜") is None
    assert detect_day_regression("第三天", "第四天·清晨") is None


def test_dormant_when_unparseable_either_side():
    # 任一侧解析不出天数 → 休眠零误伤(玄幻/时辰计法存档)
    assert detect_day_regression("申时三刻", "戌时") is None
    assert detect_day_regression("第三天·正午", "黄昏") is None
    assert detect_day_regression("入夜", "第二天·清晨") is None


def _new_state():
    import copy
    from state import DEFAULT_STATE, GameState
    return GameState(copy.deepcopy(DEFAULT_STATE))


def test_tool_appends_warning_and_audit():
    s = _new_state()
    s.data["world"]["time"] = "第五天·清晨"
    from tools_dsl.command_tools import execute_tool
    r = execute_tool(s, "set_world_time", {"target": "第二天·正午"})
    assert "⚠" in r and "倒退" in r
    audit = s.data.get("permissions", {}).get("audit_log", [])
    assert any(a.get("kind") == "time_day_regression" for a in audit)


def test_tool_no_warning_on_forward():
    s = _new_state()
    s.data["world"]["time"] = "第五天·清晨"
    from tools_dsl.command_tools import execute_tool
    r = execute_tool(s, "set_world_time", {"target": "第五天·入夜"})
    assert "⚠" not in r and "倒退" not in r
