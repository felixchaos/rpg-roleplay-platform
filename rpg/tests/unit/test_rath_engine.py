"""RATH·搖光观测台纯函数单测(npc_scene 验收/选角 + engine 时钟)。

铁律回归:①防臆造闸(transcript speaker 与 npc_updates 键必须∈被选两 NPC,同柱子3口径);
②引擎模块不 import 任何 state 写入口(离线绝不写游戏 state,设计 §1)。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rath.engine import _clock_label  # noqa: E402
from rath.npc_scene import (  # noqa: E402
    build_scene_prompts,
    select_scene_pair,
    validate_scene,
)

_ROOT = Path(__file__).resolve().parents[2]


def _snap():
    return {
        "player": {"name": "阿尔",  "current_location": "库尔斯克前线营地"},
        "world": {"time": "1943年7月 黎明前"},
        "npc_agendas": {
            "汉娜": {"goal": "修好她的四号坦克", "stance": "嘴硬但依赖玩家", "updated_turn": 9},
            "伊万": {"goal": "打探对岸兵力", "stance": "警惕", "updated_turn": 7},
        },
        "relationships": {"参谋长": "上级"},
    }


# ── select_scene_pair ────────────────────────────────────────────────

def test_pair_prefers_recent_agendas():
    assert select_scene_pair(_snap()) == ("汉娜", "伊万")


def test_pair_backfills_from_relationships():
    s = _snap()
    s["npc_agendas"] = {"汉娜": {"goal": "g", "stance": "s", "updated_turn": 1}}
    assert select_scene_pair(s) == ("汉娜", "参谋长")


def test_pair_none_when_insufficient():
    assert select_scene_pair({"npc_agendas": {}, "relationships": {}}) is None
    assert select_scene_pair({}) is None


# ── build_scene_prompts ──────────────────────────────────────────────

def test_prompts_carry_dossiers_and_geography_rule():
    sys_p, usr_p = build_scene_prompts(_snap(), "汉娜", "伊万", elapsed_hint="世界内约 3 小时")
    assert "汉娜" in usr_p and "伊万" in usr_p and "库尔斯克前线营地" in usr_p
    assert "地理连贯" in sys_p and "玩家不在场" in sys_p
    assert "世界内约 3 小时" in usr_p


# ── validate_scene 防臆造闸 ──────────────────────────────────────────

def _raw(transcript_speakers=("汉娜", "伊万"), updates_keys=("汉娜",)):
    import json
    return json.dumps({
        "transcript": [{"speaker": s, "line": f"{s}说了点什么"} for s in transcript_speakers],
        "scene_summary": "两人在营地边检修坦克边争论侦察路线",
        "npc_updates": {k: {"stance": "缓和了一点", "private_memory": "记住了对方让步"} for k in updates_keys},
    }, ensure_ascii=False)


def test_validate_accepts_clean_scene():
    out = validate_scene(_raw(), "汉娜", "伊万")
    assert out and len(out["transcript"]) == 2 and out["npc_updates"]["汉娜"]["stance"]


def test_validate_rejects_unknown_speaker_lines():
    out = validate_scene(_raw(transcript_speakers=("汉娜", "神秘人")), "汉娜", "伊万")
    assert out and all(r["speaker"] in ("汉娜", "伊万") for r in out["transcript"])


def test_validate_drops_unknown_update_keys():
    out = validate_scene(_raw(updates_keys=("汉娜", "斯大林")), "汉娜", "伊万")
    assert out and set(out["npc_updates"]) == {"汉娜"}


def test_validate_none_on_garbage():
    assert validate_scene("不是JSON", "甲", "乙") is None
    assert validate_scene('{"transcript": [], "scene_summary": ""}', "甲", "乙") is None


def test_validate_strips_code_fence():
    fenced = "```json\n" + _raw() + "\n```"
    assert validate_scene(fenced, "汉娜", "伊万") is not None


# ── engine 时钟 ──────────────────────────────────────────────────────

def test_clock_label():
    assert _clock_label(0) == "第1日 00:00"
    assert _clock_label(61) == "第1日 01:01"
    assert _clock_label(1441) == "第2日 00:01"


# ── 铁律源码守卫:引擎绝不 import state 写入口 ───────────────────────

def test_engine_never_imports_state_writers():
    src = (_ROOT / "rath" / "engine.py").read_text(encoding="utf-8")
    for forbidden in ("persist_runtime_state", "record_runtime_turn", "apply_ops",
                      "update_active_node", "import_state"):
        assert forbidden not in src, f"engine.py 不得触碰 state 写入口: {forbidden}"


def test_pair_excludes_generic_names_and_player():
    """生产实锤回归:开场史官把昏迷玩家记成「少女」进 relationships → 被选进对手戏
    (昏迷中的玩家开口说话)。泛指称谓+玩家名都不配登台;canon 卡司顶上。"""
    s = {"npc_agendas": {}, "relationships": {"少女": "未知", "薇欧拉": "房东"}}
    pair = select_scene_pair(s, extra_candidates=["林有德", "薇欧拉"],
                             exclude_names={"菲莉丝·卡俄斯"})
    assert pair is not None and "少女" not in pair
    assert set(pair) == {"薇欧拉", "林有德"}


def test_pair_all_generic_falls_back_to_cast():
    s = {"npc_agendas": {}, "relationships": {"少女": "未知", "陌生人": "警惕"}}
    assert select_scene_pair(s, extra_candidates=["林有德", "薇欧拉"]) == ("林有德", "薇欧拉")
