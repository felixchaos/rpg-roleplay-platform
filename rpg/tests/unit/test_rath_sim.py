"""RATH 仿真核心 v2 纯函数单测(状态优先:调度裁决/状态守恒/夜律/呈现验收)。

铁律回归:①玩家昏迷=位置不可变+活动只能生理反应(裁决层代码强制,不靠prompt);
②散文层无权决定情节(呈现只演绎已裁决相遇);③名词闸贯穿裁决与呈现两层。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rath.sim import (  # noqa: E402
    absorb_scene,
    apply_scheduler_output,
    build_director_prompts,
    build_scheduler_prompts,
    enforce_night,
    init_sim_state,
    parse_scheduler_output,
    validate_director_output,
)


def _snapshot():
    return {
        "player": {"name": "菲莉丝", "role": "凭空降临的神姬", "current_location": "林有德的小屋",
                   "background": "灵魂来自异世界;当前深度昏迷"},
        "history": [{"role": "assistant", "content": "少女昏迷地躺在床上,呼吸微弱。"}],
    }


def _cards():
    return [{"name": "林有德", "personality": "文科生穿越者,嘴碎心善", "appearance": "西装青年"},
            {"name": "薇欧拉", "personality": "冷静的神姬房东", "appearance": "金发"}]


def _sim():
    return init_sim_state(_snapshot(), _cards(), [{"title": "地点·毛瑟厂"}], clock_min=600)


# ── 初始化 ───────────────────────────────────────────────────────────

def test_init_builds_cast_places_threads():
    sim = _sim()
    assert set(sim["cast"]) == {"菲莉丝", "林有德", "薇欧拉"}
    assert sim["cast"]["菲莉丝"]["kind"] == "player"
    assert sim["cast"]["菲莉丝"]["status"] == "昏迷"
    assert "林有德的小屋" in sim["places"] and "毛瑟厂" in sim["places"]
    assert sim["threads"] and "昏迷" in sim["threads"][0]["desc"]
    assert any("菲莉丝目前在" in f for f in sim["facts"])


# ── 调度裁决 ─────────────────────────────────────────────────────────

def test_apply_updates_and_interaction():
    sim = _sim()
    out = apply_scheduler_output(sim, {
        "cast_updates": {"林有德": {"activity": "在桌边整理笔记", "goal": "弄清少女来历", "mood": "困惑"}},
        "interaction": {"participants": ["林有德", "薇欧拉"], "place": "林有德的小屋",
                        "reason": "商量如何照料昏迷少女", "expected_outcome": "决定轮流看护"},
        "world_events": ["镇上的钟楼敲了十点,街坊照常开工"],
        "thread_updates": [{"id": "t1", "tension_delta": 1, "note": "两人决定轮流看护"}],
        "new_facts": ["林有德开始记录少女的呼吸变化"],
    })
    assert out["applied"]["cast"] == 3
    assert out["applied"]["interaction"]["participants"] == ["林有德", "薇欧拉"]
    assert sim["cast"]["林有德"]["goal"] == "弄清少女来历"
    assert sim["threads"][0]["tension"] == 6
    assert any("呼吸变化" in f for f in sim["facts"])


def test_player_unconscious_conservation_enforced_in_code():
    """状态守恒是裁决层代码,不是 prompt 请求。"""
    sim = _sim()
    out = apply_scheduler_output(sim, {
        "cast_updates": {"菲莉丝": {"location": "毛瑟厂", "activity": "起身与林有德交谈"}},
    })
    assert sim["cast"]["菲莉丝"]["location"] == "林有德的小屋"  # 位置未被改
    assert sim["cast"]["菲莉丝"]["activity"] == "昏迷沉睡"      # 活动未被改
    assert any("昏迷" in r for r in out["rejected"])


def test_fabricated_place_rejected():
    sim = _sim()
    out = apply_scheduler_output(sim, {
        "cast_updates": {"薇欧拉": {"location": "黑森研究所"}},
    })
    assert "黑森研究所" not in sim["places"]
    assert any("新造名词" in r or "新地点" in r for r in out["rejected"])


def test_unknown_cast_rejected():
    sim = _sim()
    out = apply_scheduler_output(sim, {"cast_updates": {"斯大林": {"activity": "视察"}}})
    assert any("未知角色" in r for r in out["rejected"])


# ── 夜律 ─────────────────────────────────────────────────────────────

def test_enforce_night_sleeps_cast_but_not_unconscious_player():
    sim = _sim()
    sim["clock_min"] = 1440 + 23 * 60 + 30  # 第2日 23:30
    n = enforce_night(sim)
    assert n == 2  # 林有德/薇欧拉入睡
    assert sim["cast"]["林有德"]["activity"] == "睡眠"
    assert sim["cast"]["菲莉丝"]["activity"] == "昏迷沉睡"  # 昏迷者不动


def test_enforce_night_spares_high_tension():
    sim = _sim()
    sim["clock_min"] = 23 * 60 + 30
    sim["threads"][0]["tension"] = 9
    sim["threads"][0]["participants"] = ["林有德"]
    n = enforce_night(sim)
    assert sim["cast"]["林有德"]["activity"] != "睡眠"
    assert sim["cast"]["薇欧拉"]["activity"] == "睡眠"


# ── 呈现验收 ─────────────────────────────────────────────────────────

def _interaction(passive=None):
    return {"participants": ["林有德", "菲莉丝"], "place": "林有德的小屋",
            "reason": "夜间看护", "expected_outcome": "他守到后半夜", "passive": passive or ["菲莉丝"]}


def test_director_passive_lines_filtered():
    sim = _sim()
    raw = json.dumps({
        "transcript": [
            {"speaker": "林有德", "line": "(压低声音)今晚就我守着你了。"},
            {"speaker": "菲莉丝", "line": "谢谢你,林有德。"},        # 昏迷者开口 → 必须被过滤
            {"speaker": "菲莉丝", "line": "(睫毛轻颤,发出模糊的梦呓)"},  # 生理反应 → 保留
        ],
        "scene_summary": "深夜,林有德守在昏迷的菲莉丝床边,她只有细微的生理反应。",
        "private_memories": {"林有德": "她的呼吸比白天平稳了些"},
    }, ensure_ascii=False)
    out = validate_director_output(raw, _interaction(), sim)
    assert out is not None
    speakers = [(r["speaker"], r["line"]) for r in out["transcript"]]
    assert all("谢谢你" not in ln for _, ln in speakers)
    assert any("梦呓" in ln for _, ln in speakers)


def test_director_fabrication_rejected_whole():
    sim = _sim()
    raw = json.dumps({
        "transcript": [{"speaker": "林有德", "line": "明天去黑森研究所问问。"}],
        "scene_summary": "林有德决定去黑森研究所调查。",
    }, ensure_ascii=False)
    assert validate_director_output(raw, _interaction(), sim) is None


def test_absorb_scene_writes_memory_and_fact():
    sim = _sim()
    absorb_scene(sim, _interaction(), {
        "scene_summary": "深夜的看护平静无事。",
        "private_memories": {"林有德": "守夜其实没那么难熬"},
    })
    assert "守夜其实没那么难熬" in sim["cast"]["林有德"]["memory"]
    assert "深夜的看护平静无事。" in sim["facts"]


# ── prompts 冒烟 ─────────────────────────────────────────────────────

def test_prompts_smoke():
    sim = _sim()
    sp, up = build_scheduler_prompts(sim, elapsed_hint="约2小时", directive="让薇欧拉起疑")
    assert "调度器" in sp and "菲莉丝" in up and "让薇欧拉起疑" in up
    dp, du = build_director_prompts(sim, _interaction())
    assert "只负责演绎" in dp and "[昏迷]" in du


def test_parse_scheduler_fenced():
    assert parse_scheduler_output('```json\n{"cast_updates": {}}\n```') == {"cast_updates": {}}
    assert parse_scheduler_output("garbage") is None
