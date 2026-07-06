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
    compact_view,
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
        "cast_updates": {"林有德": {"activity": "在桌边整理笔记", "goal": "弄清少女来历", "stance": "困惑"}},
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


# ── 浸泡实锤回归(v1.59.2) ────────────────────────────────────────────

def test_thread_decay_releases_pressure():
    from rath.sim import decay_threads
    sim = _sim(); sim["threads"][0]["tension"] = 10
    assert decay_threads(sim) == 1
    assert sim["threads"][0]["tension"] == 9
    sim["threads"][0]["tension"] = 1
    assert decay_threads(sim) == 0  # 下限1不再降


def test_tension_ratchet_capped_plus_one():
    sim = _sim()
    apply_scheduler_output(sim, {"thread_updates": [{"id": "t1", "tension_delta": 2}]})
    assert sim["threads"][0]["tension"] == 6  # +2 被夹成 +1


def test_apparatus_around_player_rejected():
    """浸泡实锤:铭牌与玩家头顶能量共振=围绕玩家搭建解释装置,硬闸拒收。"""
    sim = _sim()
    out = apply_scheduler_output(sim, {
        "new_facts": ["铭牌与菲莉丝头顶的能量残余产生共振"],
        "world_events": ["某个装置在菲莉丝靠近时被激活条件触发"],
    })
    assert not any("共振" in f for f in sim["facts"])
    # 两条都必须被拒(理由可能是「解释装置」或名词闸先拦「装置」类新词,拦住即胜利)
    assert len(out["rejected"]) >= 2
    assert any("解释装置" in r for r in out["rejected"])


# ── 原著河道(用户实锤:与原著0%重合,v1.60.0) ─────────────────────────

def _sim_canon():
    return init_sim_state(_snapshot(), _cards(), [], clock_min=0, canon_beats=[
        {"chapter": 1, "summary": "林有德在留学生会馆与同胞辩论,首次听说战姬与神姬的存在"},
        {"chapter": 2, "summary": "金发房东以修屋顶为名试探林有德,暴露神姬身份被他制服"},
    ])


def test_canon_in_view_and_whitelist():
    sim = _sim_canon()
    view = compact_view(sim)
    assert "原著河道" in view and "留学生会馆" in view
    # 河道文本进入白名单:调度提及「留学生会馆」不再被名词闸拒
    out = apply_scheduler_output(sim, {"new_facts": ["镇上有人议论留学生会馆的那场辩论"]})
    assert any("留学生会馆" in f for f in sim["facts"])


def test_canon_advance_moves_cursor_into_facts():
    sim = _sim_canon()
    out = apply_scheduler_output(sim, {"canon_advance": True})
    assert out["applied"]["canon_advance"] is True
    assert sim["canon"]["cursor"] == 1
    assert any("【原著进程】" in f and "留学生会馆" in f for f in sim["facts"])


def test_canon_stall_forced_advance():
    from rath.sim import CANON_STALL_LIMIT, advance_stalled_canon
    sim = _sim_canon()
    for _ in range(CANON_STALL_LIMIT):
        apply_scheduler_output(sim, {})  # 每次不 advance → stall+1
    text = advance_stalled_canon(sim)
    assert text and "留学生会馆" in text
    assert sim["canon"]["cursor"] == 1 and sim["canon"]["stall"] == 0


def test_unconscious_player_location_settles_once():
    """昏迷守恒=不可移动,但「落定未知位置」不是移动(实锤:位置永远卡死未知地点)。"""
    snap = _snapshot(); snap["player"]["current_location"] = ""
    sim = init_sim_state(snap, _cards(), [], clock_min=0)
    assert sim["cast"]["菲莉丝"]["location"] == "未知地点"
    apply_scheduler_output(sim, {"cast_updates": {"菲莉丝": {"location": "林有德的小屋"}}})
    assert sim["cast"]["菲莉丝"]["location"] == "林有德的小屋"
    # 落定后再改=移动 → 拒
    out = apply_scheduler_output(sim, {"cast_updates": {"菲莉丝": {"location": "毛瑟厂"}}})
    assert sim["cast"]["菲莉丝"]["location"] == "林有德的小屋"
    assert any("昏迷" in r for r in out["rejected"])


# ── 河道低水位补给(500k 浸泡前置修:烧穿 12 段后不空转,v1.61.1) ──────────

def test_canon_refill_signals_low_water_and_extend_appends():
    from rath.sim import canon_refill_from, extend_canon, CANON_REFILL_THRESHOLD
    sim = _sim_canon()  # 2 段,cursor=0 → 未消费 2 ≤ 阈值,应报低水位
    assert canon_refill_from(sim) == 3  # 从最后一章(2)+1 续拉
    n = extend_canon(sim, [
        {"chapter": 3, "summary": "林有德随薇欧拉前往德绍容克斯工厂"},
        {"chapter": 2, "summary": "旧章重复,必须被拒"},
        {"chapter": 4, "summary": ""},  # 空文本,必须被拒
    ])
    assert n == 1
    assert [b["chapter"] for b in sim["canon"]["beats"]] == [1, 2, 3]
    # 补足后水位高于阈值 → 不再要求补给
    if len(sim["canon"]["beats"]) - sim["canon"]["cursor"] > CANON_REFILL_THRESHOLD:
        assert canon_refill_from(sim) is None


def test_canon_refill_trims_consumed_and_shifts_cursor():
    from rath.sim import canon_refill_from, extend_canon
    sim = _sim_canon()
    apply_scheduler_output(sim, {"canon_advance": True})
    apply_scheduler_output(sim, {"canon_advance": True})  # 烧穿:cursor=2=len
    assert canon_refill_from(sim) == 3
    extend_canon(sim, [{"chapter": 3, "summary": "德绍之行"},
                       {"chapter": 4, "summary": "工厂裁员风波"}])
    c = sim["canon"]
    # 已消费段裁剪只留 1 段,cursor 平移后指向新段(第3章)
    assert c["beats"][c["cursor"]]["chapter"] == 3
    assert [b["chapter"] for b in c["beats"]] == [2, 3, 4]
    assert compact_view(sim).count("德绍之行") == 1  # 新段进入河道视图


def test_canon_refill_none_when_never_had_beats():
    from rath.sim import canon_refill_from
    sim = init_sim_state(_snapshot(), _cards(), [], clock_min=0, canon_beats=[])
    assert canon_refill_from(sim) is None


# ── 实体别名归并(500k 浸泡实锤:简称「菲莉丝」被当未知角色,v1.61.2) ──────────

def _sim_fullname_player():
    snap = _snapshot()
    snap["player"]["name"] = "菲莉丝·卡俄斯"
    return init_sim_state(snap, _cards(), [], clock_min=0)


def test_alias_resolve_short_name_updates_full_key():
    from rath.sim import resolve_cast_name
    sim = _sim_fullname_player()
    assert "菲莉丝·卡俄斯" in sim["cast"]
    assert resolve_cast_name(sim["cast"], "菲莉丝") == "菲莉丝·卡俄斯"
    assert resolve_cast_name(sim["cast"], "不存在的人") is None
    out = apply_scheduler_output(sim, {"cast_updates": {"菲莉丝": {"stance": "安详"}}})
    assert out["applied"]["cast"] == 1
    assert sim["cast"]["菲莉丝·卡俄斯"]["stance"] == "安详"
    assert not any("未知角色" in r for r in out["rejected"])


def test_alias_ambiguous_not_guessed():
    from rath.sim import resolve_cast_name
    cast = {"汉斯·里希特": {}, "汉斯·韦伯": {}}
    assert resolve_cast_name(cast, "汉斯") is None  # 歧义不猜


def test_alias_interaction_participants_resolved_and_deduped():
    sim = _sim_fullname_player()
    npc = next(n for n, c in sim["cast"].items() if c.get("kind") != "player")
    out = apply_scheduler_output(sim, {"interaction": {
        "participants": ["菲莉丝", "菲莉丝·卡俄斯", npc],
        "place": sim["cast"][npc].get("location") or (sim.get("places") or ["未知地点"])[0],
        "reason": "查看昏迷少女", "expected_outcome": "无"}})
    it = out["applied"]["interaction"]
    assert it and it["participants"] == ["菲莉丝·卡俄斯", npc]


def test_apparatus_gate_catches_short_name():
    sim = _sim_fullname_player()
    out = apply_scheduler_output(sim, {"new_facts": ["菲莉丝头顶的能量核心与祭坛产生共振"]})
    assert out["applied"]["facts"] == 0
    assert any("解释装置" in r for r in out["rejected"])
