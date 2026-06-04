"""GM 倾向旋钮存储层 Phase 2:三层读 + resolve + 零回归。"""
import re
import types

from agents.gm import style_config as sc
from agents.gm import style_harness as sh


def _target_hi(profile) -> int:
    block = sh.render_style_block(profile)
    m = re.search(r"目标约 \d+-(\d+) 字", block)
    assert m, block
    return int(m.group(1))


def test_script_override_raises_reply_length(monkeypatch):
    monkeypatch.setattr(sc, "_read_user_gm_style", lambda uid: None)
    monkeypatch.setattr(sc, "_read_script_gm_style", lambda sid: {"reply_length": 90})
    prof = sc.resolve_for_state(user_id=1, script_id=11, state=None)
    assert prof["reply_length"] == 90
    # 字数目标确实变大(对比默认)
    assert _target_hi(prof) > _target_hi(sh.default_profile())


def test_save_override_beats_script(monkeypatch):
    monkeypatch.setattr(sc, "_read_user_gm_style", lambda uid: {"reply_length": 70})
    monkeypatch.setattr(sc, "_read_script_gm_style", lambda sid: {"reply_length": 30, "drama_density": 80})
    state = types.SimpleNamespace(data={"player_private": {"gm_style": {"drama_density": 5}}})
    prof = sc.resolve_for_state(user_id=1, script_id=11, state=state)
    assert prof["reply_length"] == 30      # script 覆盖 user
    assert prof["drama_density"] == 5      # save 覆盖 script
    assert prof["cliffhanger"] == sh.KNOBS["cliffhanger"]["default"]  # 未配取默认


def test_all_layers_empty_is_default(monkeypatch):
    monkeypatch.setattr(sc, "_read_user_gm_style", lambda uid: None)
    monkeypatch.setattr(sc, "_read_script_gm_style", lambda sid: None)
    prof = sc.resolve_for_state(user_id=None, script_id=None, state=None)
    assert prof == sh.default_profile()


def test_malformed_layers_ignored(monkeypatch):
    monkeypatch.setattr(sc, "_read_user_gm_style", lambda uid: "not-a-dict")  # 脏数据
    monkeypatch.setattr(sc, "_read_script_gm_style", lambda sid: ["also", "bad"])
    prof = sc.resolve_for_state(user_id=1, script_id=11, state=None)
    assert prof == sh.default_profile()  # 脏层被 resolve_profile 忽略


def test_save_reader_handles_bad_state():
    # state 缺 data / player_private 不报错
    assert sc._read_save_gm_style(None) is None
    assert sc._read_save_gm_style(types.SimpleNamespace(data={})) is None
    assert sc._read_save_gm_style(types.SimpleNamespace(data={"player_private": {}})) is None
    good = types.SimpleNamespace(data={"player_private": {"gm_style": {"interiority": 88}}})
    assert sc._read_save_gm_style(good) == {"interiority": 88}
