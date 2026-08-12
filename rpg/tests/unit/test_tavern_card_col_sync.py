"""酒馆 card 404 修复 —— 写侧列同步 + 读侧 state_snapshot fallback 的纯函数单测。

背景(生产事故):LLM 工具 set_tavern_character / set_tavern_persona /
import_character_card 只 mutate state.data['tavern'](单写者铁律,不裸写 game_saves 列),
导致 game_saves.tavern_character_card_id / tavern_persona_card_id 列仍是 create 时的初值
(空起手对话为 NULL),而 state JSON 已是新卡 id。走列的读卡路径 NULL → card 404。

两道防线(本测试覆盖纯逻辑部分,DB UPDATE 拼装见 refs.py / runtime.py):
  · 写侧:单写者落库时用 tavern_card_cols(snapshot) 抽列值,COALESCE 追平 game_saves 列。
  · 读侧:_expose_save 在列为 NULL 时回退读 state_snapshot->'tavern' 卡 id(存量自愈)。
"""
from platform_app.branches._helpers import tavern_card_cols
from routes.tavern import _expose_save, _snapshot_card_ids

# ── 写侧:tavern_card_cols(state_snapshot) ───────────────────────────────────

def test_write_extracts_both_card_ids():
    snap = {"tavern": {"character_card_id": 2809, "persona_card_id": 2810}}
    assert tavern_card_cols(snap) == (2809, 2810)


def test_write_coerces_string_ids():
    snap = {"tavern": {"character_card_id": "2809", "persona_card_id": "2810"}}
    assert tavern_card_cols(snap) == (2809, 2810)


def test_write_missing_persona_returns_none_for_it():
    snap = {"tavern": {"character_card_id": 42}}
    assert tavern_card_cols(snap) == (42, None)


def test_write_non_tavern_save_returns_none_none():
    # 非酒馆存档(无 tavern 块)→ (None, None) → COALESCE 落库时保留旧列,绝不清成 NULL。
    assert tavern_card_cols({"player": {"name": "X"}, "turn": 3}) == (None, None)


def test_write_empty_tavern_returns_none_none():
    assert tavern_card_cols({"tavern": {}}) == (None, None)


def test_write_zero_and_garbage_ids_become_none():
    # 0 / 负数 / 非数字都视为无效(避免把 FK 列写成非法值)。
    assert tavern_card_cols({"tavern": {"character_card_id": 0, "persona_card_id": -5}}) == (None, None)
    assert tavern_card_cols({"tavern": {"character_card_id": "x", "persona_card_id": None}}) == (None, None)


def test_write_handles_non_dict_input():
    assert tavern_card_cols(None) == (None, None)
    assert tavern_card_cols("not a dict") == (None, None)
    assert tavern_card_cols({"tavern": "not a dict"}) == (None, None)


# ── 读侧 fallback:_snapshot_card_ids + _expose_save ─────────────────────────

def test_read_snapshot_card_ids_from_dict_snapshot():
    save = {"state_snapshot": {"tavern": {"character_card_id": 2809, "persona_card_id": 2810}}}
    assert _snapshot_card_ids(save) == (2809, 2810)


def test_read_snapshot_card_ids_from_json_string_snapshot():
    # state_snapshot 可能以 JSON 字符串形态出现(某些读路径未自动反序列化)。
    save = {"state_snapshot": '{"tavern": {"character_card_id": 7, "persona_card_id": 8}}'}
    assert _snapshot_card_ids(save) == (7, 8)


def test_read_snapshot_missing_tavern_returns_none():
    assert _snapshot_card_ids({"state_snapshot": {"player": {}}}) == (None, None)
    assert _snapshot_card_ids({"state_snapshot": "not json {"}) == (None, None)
    assert _snapshot_card_ids({}) == (None, None)


def test_expose_uses_columns_when_present():
    # 列有值 → 直接用列,不碰 snapshot。
    save = {
        "id": 1, "title": "t", "save_kind": "tavern",
        "tavern_character_card_id": 100, "tavern_persona_card_id": 200,
        "state_snapshot": {"tavern": {"character_card_id": 999, "persona_card_id": 888}},
    }
    out = _expose_save(save)
    assert out["tavern_character_card_id"] == 100
    assert out["tavern_persona_card_id"] == 200


def test_expose_falls_back_to_snapshot_when_columns_null():
    # 复刻生产 save 65:列 NULL,JSON=2809/2810 → fallback 取 JSON,不再 404。
    save = {
        "id": 65, "title": "t", "save_kind": "tavern",
        "tavern_character_card_id": None, "tavern_persona_card_id": None,
        "state_snapshot": {"tavern": {"character_card_id": 2809, "persona_card_id": 2810}},
    }
    out = _expose_save(save)
    assert out["tavern_character_card_id"] == 2809
    assert out["tavern_persona_card_id"] == 2810


def test_expose_partial_fallback_mixes_column_and_snapshot():
    # 角色列有值、persona 列 NULL → 角色用列、persona 回退 JSON。
    save = {
        "id": 66, "title": "t", "save_kind": "tavern",
        "tavern_character_card_id": 42, "tavern_persona_card_id": None,
        "state_snapshot": {"tavern": {"character_card_id": 2809, "persona_card_id": 2810}},
    }
    out = _expose_save(save)
    assert out["tavern_character_card_id"] == 42
    assert out["tavern_persona_card_id"] == 2810


def test_expose_both_empty_stays_none():
    # 列 NULL 且 JSON 也无卡(空起手未自举)→ 两边都空,保持 None(不 404,前端自有兜底)。
    save = {
        "id": 67, "title": "t", "save_kind": "tavern",
        "tavern_character_card_id": None, "tavern_persona_card_id": None,
        "state_snapshot": {"tavern": {}},
    }
    out = _expose_save(save)
    assert out["tavern_character_card_id"] is None
    assert out["tavern_persona_card_id"] is None
