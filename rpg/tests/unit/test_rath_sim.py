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


# ── D2: 地点白名单独立源(canon_locations)──────────────────────────

def test_canon_locations_param_populates_places():
    """engine.py 按 importance 排名从 kb_canon_entities(type=location)查到的地点名,
    经 canon_locations 关键字参数直接进白名单——不依赖世界书标题是否含"地点"类关键词
    (审计实锤:无职转生等剧本世界书标题几乎不命中该关键词集,只能靠独立查询补上)。"""
    sim = init_sim_state(_snapshot(), _cards(), [{"title": "势力·某某军团"}],
                         clock_min=0, canon_locations=["毛瑟厂", "老城墙"])
    assert "毛瑟厂" in sim["places"] and "老城墙" in sim["places"]
    assert "林有德的小屋" in sim["places"]  # 玩家出生点仍在


def test_canon_locations_dedup_against_player_location_and_wb_titles():
    sim = init_sim_state(_snapshot(), _cards(), [{"title": "地点·毛瑟厂"}],
                         clock_min=0, canon_locations=["毛瑟厂", "林有德的小屋", "毛瑟厂"])
    assert sim["places"].count("毛瑟厂") == 1
    assert sim["places"].count("林有德的小屋") == 1


def test_canon_locations_none_falls_back_to_wb_title_scan_only():
    """canon_locations 缺省(旧调用点/未接线场景)不崩,世界书标题扫描路径仍照常工作
    (向后兼容:sim.py 的这条老路径不因新参数而失效)。"""
    sim = init_sim_state(_snapshot(), _cards(), [{"title": "地点·毛瑟厂"}], clock_min=0)
    assert "毛瑟厂" in sim["places"]


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
    # 种子线基线3 + thread_updates显式+1 + B1喂养(interaction参与者∈该线participants)+1 = 5
    assert sim["threads"][0]["tension"] == 5
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
    assert sim["threads"][0]["tension"] == 4  # 基线3,+2 被夹成 +1


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
    assert "留学生会馆" in out["applied"]["canon_text"]  # engine 靠它把动向并入事件流
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


def test_alias_resolve_strips_view_tags():
    from rath.sim import resolve_cast_name
    cast = {"菲莉丝·卡俄斯": {}, "林有德": {}}
    assert resolve_cast_name(cast, "菲莉丝[玩家]") == "菲莉丝·卡俄斯"
    assert resolve_cast_name(cast, "菲莉丝·卡俄斯[玩家]") == "菲莉丝·卡俄斯"
    assert resolve_cast_name(cast, "林有德(睡眠中)") == "林有德"
    assert resolve_cast_name(cast, "[玩家]") is None


# ── 夜归(浸泡实锤:自由线滚雪球→夜宿石灰窑深坑,行为脱设定) ──────────────

def test_night_curfew_pulls_forced_sleepers_home():
    from rath.sim import enforce_night
    sim = init_sim_state(_snapshot(), _cards(), [], clock_min=23 * 60 + 30)
    npc = next(n for n, c in sim["cast"].items() if c.get("kind") != "player")
    c = sim["cast"][npc]
    home = c["home"]
    sim.setdefault("places", []).append("水泥厂石灰窑深坑")
    c["location"] = "水泥厂石灰窑深坑"
    c["activity"] = "监听坑底异响"
    assert enforce_night(sim) >= 1
    assert c["activity"] == "睡眠" and c["location"] == home


def test_night_curfew_respects_voluntary_lodging_and_legacy_state():
    from rath.sim import enforce_night
    sim = init_sim_state(_snapshot(), _cards(), [], clock_min=23 * 60 + 30)
    names = [n for n, c in sim["cast"].items() if c.get("kind") != "player"]
    a = sim["cast"][names[0]]
    a["location"] = "德绍旅馆"
    a["activity"] = "在旅馆睡下"  # LLM 主动外宿:尊重
    if len(names) > 1:
        b = sim["cast"][names[1]]
        b.pop("home", None)  # 老档无 home:只改活动不瞬移
        b["location"] = "河岸"
        b["activity"] = "巡逻"
    enforce_night(sim)
    assert a["location"] == "德绍旅馆"
    if len(names) > 1:
        assert b["activity"] == "睡眠" and b["location"] == "河岸"


def test_night_gate_rejects_leaving_home_lowtension():
    from rath.sim import enforce_night
    sim = init_sim_state(_snapshot(), _cards(), [], clock_min=25 * 60)  # 次日 01:00
    npc = next(n for n, c in sim["cast"].items() if c.get("kind") != "player")
    sim.setdefault("places", []).append("水泥厂东边仓库")
    enforce_night(sim)  # 拍首:在家睡眠
    out = apply_scheduler_output(sim, {"cast_updates": {npc: {"location": "水泥厂东边仓库", "activity": "搜查痕迹"}}})
    assert any("夜间不外出" in r for r in out["rejected"])
    assert sim["cast"][npc]["location"] == sim["cast"][npc]["home"]


def test_night_gate_spares_hot_thread_and_daytime():
    sim = init_sim_state(_snapshot(), _cards(), [], clock_min=25 * 60)
    npc = next(n for n, c in sim["cast"].items() if c.get("kind") != "player")
    sim.setdefault("places", []).append("水泥厂东边仓库")
    sim["threads"] = [{"id": "t9", "desc": "追凶", "tension": 9, "participants": [npc]}]
    out = apply_scheduler_output(sim, {"cast_updates": {npc: {"location": "水泥厂东边仓库"}}})
    assert not any("夜间不外出" in r for r in out["rejected"])
    assert sim["cast"][npc]["location"] == "水泥厂东边仓库"
    sim2 = init_sim_state(_snapshot(), _cards(), [], clock_min=14 * 60)  # 白天
    npc2 = next(n for n, c in sim2["cast"].items() if c.get("kind") != "player")
    sim2.setdefault("places", []).append("水泥厂东边仓库")
    out2 = apply_scheduler_output(sim2, {"cast_updates": {npc2: {"location": "水泥厂东边仓库"}}})
    assert sim2["cast"][npc2]["location"] == "水泥厂东边仓库"


# ── 原著主线夺回(用户实锤:河道只当背景板,自由悬疑线绑架原著卡司) ──────────

def test_canon_anchor_resets_cast_goal_on_advance():
    from rath.sim import anchor_cast_to_beat
    sim = _sim_canon()
    npc = next(n for n, c in sim["cast"].items() if c.get("kind") != "player")
    beat_with_npc = f"{npc}在留学生会馆与同胞辩论"
    sim["cast"][npc]["goal"] = "调查水泥厂暗门"  # 自由线绑架中
    out = anchor_cast_to_beat(sim, beat_with_npc)
    assert npc in out
    assert sim["cast"][npc]["goal"].startswith("(原著行程)")
    assert "水泥厂" not in sim["cast"][npc]["goal"]


def test_canon_advance_triggers_anchor():
    sim = _sim_canon()
    # _sim_canon 的 beat1 提到「林有德」——若卡司里有他,goal 应被锚定
    if "林有德" in sim["cast"]:
        sim["cast"]["林有德"]["goal"] = "撬开地下车间铁门"
        apply_scheduler_output(sim, {"canon_advance": True})
        assert sim["cast"]["林有德"]["goal"].startswith("(原著行程)")


def test_player_probe_rejected():
    sim = _sim_fullname_player()
    out = apply_scheduler_output(sim, {"new_facts": ["千寻发现灵魂锚点研究或可定位菲莉丝的来历"]})
    assert out["applied"]["facts"] == 0
    assert any("探究" in r for r in out["rejected"])
    out2 = apply_scheduler_output(sim, {"new_threads": [{"desc": "研究菲莉丝随身物品以鉴定其身世", "tension": 4}]})
    assert not any(("菲莉丝" in (t.get("desc") or "") and "鉴定" in (t.get("desc") or ""))
                   for t in sim["threads"])


def test_stale_threads_closed_and_recorded():
    from rath.sim import close_stale_threads, STALE_THREAD_TICKS
    sim = _sim_canon()
    sim["threads"] = [{"id": "t2", "desc": "伊萨尔河沿岸调查", "tension": 0, "last_touch": 1}]
    sim["tick_seq"] = 1 + STALE_THREAD_TICKS
    closed = close_stale_threads(sim)
    assert closed and "伊萨尔河" in closed[0]
    assert sim["threads"] == []
    assert any("(已平息)" in f for f in sim["facts"])
    # 高张力/近期触及的线不关
    sim["threads"] = [{"id": "t3", "desc": "活跃线", "tension": 5, "last_touch": 1}]
    assert close_stale_threads(sim) == []


# ── v3:关系网/长弧/离线简报(用户末班请求「RATH 更完善」) ──────────────────

def test_relation_updates_arbitrated_with_alias_and_gate():
    sim = _sim_fullname_player()
    npc = next(n for n, c in sim["cast"].items() if c.get("kind") != "player")
    out = apply_scheduler_output(sim, {"relation_updates": [
        {"pair": ["菲莉丝", npc], "kind": "守护", "note": "彻夜照料后生出责任感"},
        {"pair": ["不存在的人", npc], "kind": "同僚", "note": ""},
    ]})
    assert out["applied"].get("relations") == 1
    key = "|".join(sorted(["菲莉丝·卡俄斯", npc]))
    assert sim["relations"][key]["kind"] == "守护"
    assert any("relation:成员未识别" in r for r in out["rejected"])
    # 探究措辞照拒(神圣条款贯穿关系层)
    out2 = apply_scheduler_output(sim, {"relation_updates": [
        {"pair": ["菲莉丝", npc], "kind": "研究对象", "note": "想调查她的来历"}]})
    assert out2["applied"].get("relations") in (None, 0)


def test_thread_stage_lifecycle_deterministic():
    sim = _sim_canon()
    t = sim["threads"][0]
    t["tension"] = 3
    t.pop("stage", None)
    apply_scheduler_output(sim, {"thread_updates": [{"id": t["id"], "tension_delta": 1}]})
    assert t["stage"] == "rising"  # seed→rising(张力≥4)
    t["tension"] = 7
    apply_scheduler_output(sim, {"thread_updates": [{"id": t["id"], "tension_delta": 2}]})
    assert t["stage"] == "climax"  # ≥8 触发,+2 被夹成 +1 → 8
    # climax 停留 2 拍后自动转 aftermath 且泄压
    apply_scheduler_output(sim, {"thread_updates": [{"id": t["id"], "tension_delta": 0}]})
    apply_scheduler_output(sim, {"thread_updates": [{"id": t["id"], "tension_delta": 0}]})
    assert t["stage"] == "aftermath"
    assert t["tension"] <= 3
    assert len(t.get("tension_hist") or []) >= 3


def test_new_thread_born_as_seed_capped():
    sim = _sim_canon()
    apply_scheduler_output(sim, {"new_threads": [{"desc": "码头传来陌生船队的消息", "tension": 6}]})
    nt = sim["threads"][-1]
    assert nt["stage"] == "seed" and nt["tension"] <= 4


def test_director_prompt_carries_relation():
    from rath.sim import build_director_prompts
    sim = _sim_canon()
    ps = [n for n in sim["cast"]][:2]
    sim["relations"]["|".join(sorted(ps))] = {"kind": "心存芥蒂", "note": "上周争执未解"}
    _, user = build_director_prompts(sim, {"participants": ps, "place": "茶摊", "reason": "偶遇",
                                           "expected_outcome": "不欢而散"})
    assert "心存芥蒂" in user and "上周争执未解" in user


# ── B1:张力喂养对冲源(治「玩家缺席线卡seed张力恒0」) ─────────────────────

def test_canon_advance_feeds_thread_tension():
    """canon_advance 锚定的原著卡司若在场于某线 participants,代码确定性 +1
    (不依赖 LLM 主动给 thread_updates,给衰减一个对冲源)。"""
    sim = _sim_canon()
    t = sim["threads"][0]
    t["participants"] = ["林有德"]
    t["tension"] = 3
    out = apply_scheduler_output(sim, {"canon_advance": True})
    assert "林有德" in out["applied"]["canon_anchored"]
    assert t["tension"] == 4


def test_interaction_feeds_thread_tension():
    """相遇成立(interaction 通过验收)同样是世界侧对冲信号,涉及的现存线 +1。"""
    sim = _sim_canon()
    names = [n for n, c in sim["cast"].items() if c.get("kind") != "player"]
    t = sim["threads"][0]
    t["participants"] = names[:2]
    t["tension"] = 3
    loc = sim["cast"][names[0]]["location"]
    out = apply_scheduler_output(sim, {"interaction": {
        "participants": names[:2], "place": loc, "reason": "偶遇", "expected_outcome": "寒暄几句"}})
    assert out["applied"]["interaction"]
    assert t["tension"] == 4


def test_aftermath_thread_not_fed():
    """余波/平息线不再被喂养——高潮已过的线不该被代码强行续命。"""
    sim = _sim_canon()
    t = sim["threads"][0]
    t["participants"] = ["林有德"]
    t["tension"] = 2
    t["stage"] = "aftermath"
    apply_scheduler_output(sim, {"canon_advance": True})
    assert t["tension"] == 2


# ── B2:高潮兑现(Façade beat 导演) ─────────────────────────────────────

def test_climax_director_note_injected_after_starve():
    """高潮线上一拍未获覆盖 → 饥饿计数 ≥1 → 下一拍调度 prompt 显式插入导演指令,
    不再指望 LLM 随缘兑现高潮。"""
    from rath.sim import build_scheduler_prompts
    sim = _sim_canon()
    t = sim["threads"][0]
    t["id"] = "t1"
    t["stage"] = "climax"
    sim["_climax_starve"] = {"t1": 1}
    _, user = build_scheduler_prompts(sim, elapsed_hint="约1小时")
    assert "导演指令" in user and "高潮" in user


def test_climax_starve_forces_interaction_when_colocated():
    """饥饿计数 ≥2 且高潮线 top-2 参与者本拍同地点 → 裁决层直接补设 interaction,
    不再靠随缘。"""
    sim = _sim_canon()
    names = [n for n, c in sim["cast"].items() if c.get("kind") != "player"]
    t = sim["threads"][0]
    t["id"] = "t1"
    t["stage"] = "climax"
    t["participants"] = names[:2]
    loc = sim["places"][0]
    sim["cast"][names[0]]["location"] = loc
    sim["cast"][names[1]]["location"] = loc
    sim["_climax_starve"] = {"t1": 2}
    out = apply_scheduler_output(sim, {})
    assert out["applied"]["interaction"]
    assert set(out["applied"]["interaction"]["participants"]) == set(names[:2])
    assert out["applied"]["interaction"]["place"] == loc


def test_climax_starve_no_force_when_different_locations():
    """不同地点不硬造移动——只留 prompt 指令,饥饿计数继续累加。"""
    sim = _sim_canon()
    names = [n for n, c in sim["cast"].items() if c.get("kind") != "player"]
    t = sim["threads"][0]
    t["id"] = "t1"
    t["stage"] = "climax"
    t["participants"] = names[:2]
    sim["cast"][names[0]]["location"] = "甲地"
    sim["cast"][names[1]]["location"] = "乙地"
    sim["_climax_starve"] = {"t1": 2}
    out = apply_scheduler_output(sim, {})
    assert out["applied"]["interaction"] is None
    assert sim["_climax_starve"]["t1"] == 3


# ── B3:夜律认原著行程 ───────────────────────────────────────────────

def test_enforce_night_respects_canon_anchored_goal():
    """goal 带「(原著行程)」标记的角色不受夜律管辖,否则刚被 canon_advance 锚定去
    异地过夜的原著卡司会被夜律强行拉回家,与河道主线自相矛盾。"""
    sim = init_sim_state(_snapshot(), _cards(), [], clock_min=23 * 60 + 30)
    npc = next(n for n, c in sim["cast"].items() if c.get("kind") != "player")
    c = sim["cast"][npc]
    c["goal"] = "(原著行程)连夜赶往邻镇"
    c["location"] = "邻镇客栈"
    c["activity"] = "连夜赶路"
    enforce_night(sim)
    assert c["activity"] == "连夜赶路"  # 未被强制改为睡眠
    assert c["location"] == "邻镇客栈"  # 未被拉回家


# ── B4:夜间外出闸补 interaction.place ──────────────────────────────────

def test_night_gate_rejects_interaction_place_and_downgrades_to_fact():
    """之前只挡 cast_updates.location,同拍 interaction.place 却能绕过去演出深夜外出
    场景。非 hot 双人+夜间+地点非双方 home 一律拒收,降级为一条中性事实。"""
    sim = init_sim_state(_snapshot(), _cards(), [], clock_min=25 * 60)  # 次日 01:00
    names = [n for n, c in sim["cast"].items() if c.get("kind") != "player"]
    a, b = names[0], names[1]
    sim.setdefault("places", []).append("水泥厂东边仓库")
    out = apply_scheduler_output(sim, {"interaction": {
        "participants": [a, b], "place": "水泥厂东边仓库",
        "reason": "深夜密会", "expected_outcome": "达成默契"}})
    assert out["applied"]["interaction"] is None
    assert any("夜间外出" in r for r in out["rejected"])
    assert any("因夜深作罢" in f for f in sim["facts"])


def test_night_gate_allows_interaction_at_home():
    """地点是双方之一的 home 不算「外出」,放行。"""
    sim = init_sim_state(_snapshot(), _cards(), [], clock_min=25 * 60)
    names = [n for n, c in sim["cast"].items() if c.get("kind") != "player"]
    a, b = names[0], names[1]
    home = sim["cast"][a]["home"]
    out = apply_scheduler_output(sim, {"interaction": {
        "participants": [a, b], "place": home,
        "reason": "夜谈", "expected_outcome": "各自安睡"}})
    assert out["applied"]["interaction"] is not None


# ── B5:单次裁决内白名单惰性求值(不自我拒收本拍刚接受的新地点) ────────────────

def test_gate_whitelist_lazy_not_stale_within_tick():
    """同一次裁决调用内,cast_updates 先接受的新地点应立即计入后续 facts 判定的
    已知材料——旧 bug:_gate 只用函数开头那份旧快照,导致同一份 JSON 里刚被接受的
    新地点再次出现即被自我拒收「新造名词」。"""
    sim = _sim()
    out = apply_scheduler_output(sim, {
        "cast_updates": {"林有德": {"location": "兵工厂"}},
        "new_facts": ["林有德说城西兵工厂最近换了批新工人"],
    })
    assert "兵工厂" in sim["places"]
    assert any("城西兵工厂" in f for f in sim["facts"])
    assert not any("新造名词" in r for r in out["rejected"])


# ── B8:facts 去重 + canon 里程碑保护 ────────────────────────────────────

def test_new_facts_dedup_prefix_containment():
    """facts 入队前查重(简单前缀/包含判定)——近义重复文本不再挤占 FIFO 窗口。"""
    sim = _sim()
    apply_scheduler_output(sim, {"new_facts": ["林有德开始记录少女的呼吸变化"]})
    before = len(sim["facts"])
    out = apply_scheduler_output(sim, {"new_facts": ["林有德开始记录少女的呼吸变化,越发细致"]})
    assert out["applied"]["facts"] == 0  # 前缀/包含重复,被拒
    assert len(sim["facts"]) == before


def test_canon_history_survives_facts_fifo_eviction():
    """canon 里程碑独立存 canon.history(上限24),不与 facts 共池竞争——即便 facts
    被大量场景纪要挤爆 FIFO,里程碑仍能在 compact_view 里被看到。"""
    sim = _sim_canon()
    apply_scheduler_output(sim, {"canon_advance": True})
    assert "留学生会馆" in sim["canon"]["history"][0]
    for i in range(50):  # 灌爆 facts FIFO(远超 MAX_FACTS=40)
        sim["facts"].append(f"日常琐事{i}充数占位")
    if len(sim["facts"]) > 40:
        sim["facts"] = sim["facts"][-40:]
    assert not any("留学生会馆" in f for f in sim["facts"])  # facts 确实被挤出了
    assert any("留学生会馆" in h for h in sim["canon"]["history"])  # history 仍保留
    view = compact_view(sim)
    assert "原著进程" in view and "留学生会馆" in view


# ── B9:探究闸近邻窗口(真阳性仍拦 + 误伤用例现在放行) ───────────────────────

def test_probe_gate_proximity_window_allows_distant_unrelated_mention():
    """旧判定=玩家名与探究词整句字符串共现即拒,常见词("调查")随手出现在与玩家
    无关的段落里也会被整体拒收。新判定要求词距≤15字内共现(或探究词后紧跟属格),
    超出窗口的无关提及应放行——既有真阳性用例(见其余 probe/apparatus 测试)距离
    天然 ≤15 字,不受影响,仍全部照拦。"""
    sim = _sim()
    far_text = ("薇欧拉正在调查这周账目里的一处细小差错,"
                "与此同时窗外雨声渐歇,菲莉丝的呼吸依旧平稳。")
    out = apply_scheduler_output(sim, {"new_facts": [far_text]})
    assert out["applied"]["facts"] == 1
    assert not any("探究" in r for r in out["rejected"])


# ── B11:决策槽位分配器(只给活跃 cast 全量卡片,省 token 防漂移) ────────────────

def test_active_cast_slot_allocator_compresses_stable_cast():
    """冷启动(last_changed_seq 缺失=未知)必须视为活跃给全量卡片——新实验首拍/旧实验
    升级后首拍,调度器需要完整画面;之后连续 4 拍未被触及才压缩进"状态稳定"一行;
    一旦被 cast_updates 触及即恢复活跃,互不干扰。"""
    snap = {"player": {"name": "菲莉丝", "role": "神姬", "current_location": "林有德的小屋",
                        "background": "普通日常"}, "history": []}
    sim = init_sim_state(snap, _cards(), [], clock_min=600)
    assert sim["threads"] == []
    view0 = compact_view(sim)
    assert "- 林有德" in view0 and "- 薇欧拉" in view0, "冷启动:未知=活跃,全量卡片"
    assert "(状态稳定:" not in view0
    # 第1拍触及两人(打上 seq 基准),之后连续 4 拍只动林有德 → 薇欧拉应被压缩
    apply_scheduler_output(sim, {"cast_updates": {"林有德": {"goal": "查清房东底细"},
                                                   "薇欧拉": {"goal": "整理账目"}}})
    for _ in range(4):
        apply_scheduler_output(sim, {"cast_updates": {"林有德": {"activity": "翻查旧报"}}})
    view1 = compact_view(sim)
    assert "- 林有德" in view1  # 持续变化 → 活跃
    assert "(状态稳定:" in view1 and "薇欧拉" in view1 and "- 薇欧拉" not in view1


def test_canon_advance_births_thread_when_none_alive():
    """v4.1(storylets 补完):河道前行且无活跃线 → 从 beat 确定性孵化剧情线
    (薄剧本无昏迷种子线时 threads 恒空,世界只有河道没有弧——269 浸泡实锤)。"""
    snap = {"player": {"name": "菲莉丝", "role": "神姬", "current_location": "家",
                        "background": "普通日常"}, "history": []}
    sim = init_sim_state(snap, _cards(), [], clock_min=600,
                         canon_beats=[{"chapter": 1, "summary": "林有德邀请薇欧拉参加测试"}])
    assert sim["threads"] == []
    out = apply_scheduler_output(sim, {"canon_advance": True})
    assert out["applied"]["canon_advance"] is True
    assert out["applied"].get("thread_born_from_beat") == "t1"
    t = sim["threads"][0]
    assert t["stage"] == "seed" and t["tension"] == 3
    assert t["desc"].startswith("(原著动向)")
    assert t["participants"], "participants 必须非空(锚定卡司或非玩家卡司兜底)"
    # 已有活跃线时不再重复孵化
    out2 = apply_scheduler_output(sim, {"canon_advance": True})
    assert out2["applied"].get("thread_born_from_beat") is None
