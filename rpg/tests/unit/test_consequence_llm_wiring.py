"""后果账本 LLM 侧接线的 parity 守卫。

三条铁律回归:
1. recorder 的 prompt 与 tool-schema 必须同源同值启用 consequence(progress_motion
   漏进 tool-schema 的 parity 事故同款,原生 tool-use 通道漏 schema = 静默哑火)。
2. flag 关闭时两处都不得出现 consequence(零行为漂移)。
3. GM 系统提示词指引块 _consequence_guide_block 严格随 flag 开关。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agents.recorder import _build_system_prompt, _build_tool_schema  # noqa: E402

_OPS_TASKS = frozenset({"ops", "anchors"})


def _schema_op_enum(schema: dict) -> list[str]:
    return schema["input_schema"]["properties"]["ops"]["items"]["properties"]["op"]["enum"]


def _schema_item_props(schema: dict) -> dict:
    return schema["input_schema"]["properties"]["ops"]["items"]["properties"]


def test_recorder_schema_consequence_on():
    schema = _build_tool_schema(_OPS_TASKS, None, consequence_enabled=True)
    assert "consequence" in _schema_op_enum(schema)
    props = _schema_item_props(schema)
    assert "due_turns" in props and "due_location" in props


def test_recorder_schema_consequence_off_by_default():
    schema = _build_tool_schema(_OPS_TASKS, None)
    assert "consequence" not in _schema_op_enum(schema)
    props = _schema_item_props(schema)
    assert "due_turns" not in props and "due_location" not in props


def test_recorder_prompt_consequence_parity_with_schema():
    # 开:prompt 与 schema 都有;关:都没有 —— 两处不允许各说各话
    p_on = _build_system_prompt(_OPS_TASKS, consequence_enabled=True)
    s_on = _build_tool_schema(_OPS_TASKS, None, consequence_enabled=True)
    assert "consequence" in p_on and "后果登记" in p_on
    assert "consequence" in _schema_op_enum(s_on)

    p_off = _build_system_prompt(_OPS_TASKS, consequence_enabled=False)
    s_off = _build_tool_schema(_OPS_TASKS, None, consequence_enabled=False)
    assert "consequence" not in p_off
    assert "consequence" not in _schema_op_enum(s_off)


def test_recorder_prompt_no_ops_task_no_consequence():
    # 未启用 ops 任务时,即便 flag 开也不该出现 consequence 段
    p = _build_system_prompt(frozenset({"anchors"}), consequence_enabled=True)
    assert "后果登记" not in p


def test_gm_guide_block_follows_flag(monkeypatch):
    import agents.gm.master as master
    import core.feature_flags as ff

    monkeypatch.setattr(ff, "feature_enabled", lambda key, uid=None: True)
    block_on = master._consequence_guide_block(1)
    assert "后果账本" in block_on and "consequence" in block_on

    monkeypatch.setattr(ff, "feature_enabled", lambda key, uid=None: False)
    assert master._consequence_guide_block(1) == ""


def test_gm_guide_block_exception_safe(monkeypatch):
    import agents.gm.master as master
    import core.feature_flags as ff

    def _boom(key, uid=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(ff, "feature_enabled", _boom)
    assert master._consequence_guide_block(1) == ""


def test_fingerprint_dedup_survives_punctuation_drift():
    # 生产实测:史官跨回合重提取,「期限约定——…，将再次对峙」vs「期限约定——…,将再次对峙」
    from state.consequence_ledger import register_consequence
    sd = {"turn": 8}
    ok1, _ = register_consequence(sd, text="两日期限约定——后天正午前若不撤离，将再次对峙", due_turns=4)
    ok2, msg2 = register_consequence(sd, text="两日期限约定——后天正午前若不撤离,将再次对峙", due_turns=4)
    assert ok1 and not ok2 and "重复" in msg2


def test_recorder_user_prompt_lists_pending_ledger():
    from agents.recorder import _build_user_prompt
    sd = {
        "player": {}, "world": {}, "memory": {}, "relationships": {},
        "consequence_ledger": [
            {"text": "答应雷纳德查兽伤", "status": "pending"},
            {"text": "已兑现的旧事", "status": "fired"},
        ],
    }
    p_on = _build_user_prompt("正文", sd, frozenset({"ops"}), None, None, None, consequence_enabled=True)
    assert "严禁重复登记" in p_on and "答应雷纳德查兽伤" in p_on and "已兑现的旧事" not in p_on
    p_off = _build_user_prompt("正文", sd, frozenset({"ops"}), None, None, None, consequence_enabled=False)
    assert "严禁重复登记" not in p_off


# ── NPC 议程 parity 守卫(柱子3,consequence 同款铁律)────────────────────

def test_recorder_agenda_parity():
    p_on = _build_system_prompt(_OPS_TASKS, agenda_enabled=True)
    s_on = _build_tool_schema(_OPS_TASKS, None, agenda_enabled=True)
    assert "agenda" in _schema_op_enum(s_on)
    props_on = _schema_item_props(s_on)
    assert "goal" in props_on and "stance" in props_on and "name" in props_on
    assert "NPC 议程" in p_on

    p_off = _build_system_prompt(_OPS_TASKS, agenda_enabled=False)
    s_off = _build_tool_schema(_OPS_TASKS, None, agenda_enabled=False)
    assert "agenda" not in _schema_op_enum(s_off)
    assert "NPC 议程更新" not in p_off


def test_recorder_user_prompt_lists_current_agendas():
    from agents.recorder import _build_user_prompt
    sd = {
        "player": {}, "world": {}, "memory": {}, "relationships": {},
        "npc_agendas": {"雷纳德": {"goal": "查兽伤", "stance": "信任", "updated_turn": 5}},
    }
    p_on = _build_user_prompt("正文", sd, frozenset({"ops"}), None, None, None, agenda_enabled=True)
    assert "NPC 议程·当前值" in p_on and "雷纳德" in p_on
    p_off = _build_user_prompt("正文", sd, frozenset({"ops"}), None, None, None, agenda_enabled=False)
    assert "NPC 议程·当前值" not in p_off


def test_gm_agenda_guide_follows_flag(monkeypatch):
    import agents.gm.master as master
    import core.feature_flags as ff

    monkeypatch.setattr(ff, "feature_enabled", lambda key, uid=None: key == "npc_agenda")
    assert "agenda" in master._agenda_guide_block(1)
    monkeypatch.setattr(ff, "feature_enabled", lambda key, uid=None: False)
    assert master._agenda_guide_block(1) == ""
