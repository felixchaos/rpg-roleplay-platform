"""GM 倾向性旋钮底座 Phase 1:默认复刻现状 + 线性可调 + 归一/分层。"""
import re

from agents.gm import style_harness as sh


def _num_range(block: str) -> tuple[int, int]:
    m = re.search(r"目标约 (\d+)-(\d+) 字", block)
    assert m, block
    return int(m.group(1)), int(m.group(2))


def test_default_block_reproduces_current_rules_semantics():
    block = sh.render_style_block(sh.default_profile())
    # 6 维度都在
    for key in ("正文篇幅", "镜头焦点", "戏剧密度", "心理与潜台词", "收尾与悬念", "剧情推进与引导"):
        assert key in block, f"缺维度 {key}"
    # 关键语义短语(复刻原硬规则)
    assert "实质推进" in block and "严禁一两句话敷衍" in block      # 篇幅铁律
    assert "对方 NPC" in block and "至多一两句承接带过" in block      # 镜头铁律(#28)
    assert "镜像玩家" in block or "镜像玩家输入" in block            # 戏剧密度
    assert "外部描写" in block or "不展开内心独白" in block          # 心理(不补潜台词)
    assert "有张力的场景节拍" in block                              # 留白铁律
    assert "把这一轮推进了" in block                                # 推进铁律
    # 默认篇幅下限 ≥ 300(复刻"正文至少 300 字实质推进")
    lo, hi = _num_range(block)
    assert lo >= 280, (lo, hi)


def test_reply_length_is_linearly_adjustable():
    lo_short, hi_short = _num_range(sh.render_style_block({"reply_length": 0}))
    lo_mid, hi_mid = _num_range(sh.render_style_block({"reply_length": 50}))
    lo_long, hi_long = _num_range(sh.render_style_block({"reply_length": 100}))
    # 旋钮越高,字数目标越大(线性单调)
    assert hi_short < hi_mid < hi_long, (hi_short, hi_mid, hi_long)
    assert hi_long >= 800


def test_player_action_focus_knob_changes_camera_text():
    low = sh.render_style_block({"player_action_focus": 5})    # 强对方反应
    high = sh.render_style_block({"player_action_focus": 95})  # 细写玩家动作
    assert "至多一两句承接带过" in low
    assert "至多一两句承接带过" not in high
    assert "铺陈玩家" in high or "描摹玩家" in high


def test_normalize_handles_none_missing_and_out_of_range():
    p = sh.normalize_profile(None)
    assert p == sh.default_profile()
    p2 = sh.normalize_profile({"reply_length": 999, "drama_density": -50, "bogus": 1})
    assert p2["reply_length"] == 100 and p2["drama_density"] == 0
    assert "bogus" not in p2 and set(p2) == set(sh.KNOBS)


def test_resolve_profile_layering():
    merged = sh.resolve_profile(
        user_default={"reply_length": 70},
        script_override={"reply_length": 30, "drama_density": 80},
        save_override={"drama_density": 10},
    )
    assert merged["reply_length"] == 30      # script 覆盖 user
    assert merged["drama_density"] == 10     # save 覆盖 script
    assert merged["cliffhanger"] == sh.KNOBS["cliffhanger"]["default"]  # 未指定取默认


def test_resolve_profile_all_none_is_default():
    assert sh.resolve_profile() == sh.default_profile()


def test_system_base_extracts_tendency_rules_into_style_block():
    """集成:master._SYSTEM_BASE 已把倾向铁律抽成 {style_block} 占位,默认替换零回归。"""
    from agents.gm.master import _SYSTEM_BASE

    assert "{style_block}" in _SYSTEM_BASE
    assert "{world_section}" in _SYSTEM_BASE
    # 原硬编码倾向铁律文本应已被抽走(改由 style_harness 渲染)
    for gone in ("篇幅与质感铁律", "镜头铁律(对方反应优先", "每轮回应 150-400 字"):
        assert gone not in _SYSTEM_BASE, f"残留未抽走: {gone}"
    # 默认替换后:语义短语全在、无悬空占位
    out = _SYSTEM_BASE.replace("{world_section}", "").replace(
        "{style_block}", sh.render_style_block(sh.default_profile())
    )
    assert "{style_block}" not in out and "{world_section}" not in out
    for need in ("实质推进", "对方 NPC", "把这一轮推进了", "有张力的场景节拍", "至多一两句承接带过"):
        assert need in out, f"默认渲染缺语义: {need}"
