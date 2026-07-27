"""时间跳跃误判族第三案(群反馈·行者无疆 2026-07-05)回归测试。

「进入后先用真气感知四周环境」被判成时间线请求:「进入」触发+「后/四周」单字命中。
根修=looks_like_time_value 从单字符判定升级为时间形状 token(_TIME_TOKEN)。
族谱:v1.26.4 回忆从句(前) → 第三案 动作叙述(后/周) → v1.73.2 第四案 叙述里的年份/章号。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from timeline_state import (  # noqa: E402
    detect_time_directives, has_jump_verb, is_bare_time_target,
    looks_like_time_value, mentions_time_without_intent,
)


# ── 本案 + 同族假阳性:动作叙述绝不能判成跳跃 ──────────────────────────

def test_reported_case_no_directive():
    """群反馈原话:进入+后+四周,三个旧命中点都不该触发。"""
    assert detect_time_directives("进入后先用真气感知四周环境") == []


def test_action_narration_family_no_directive():
    for text in [
        "进入洞穴查看四周",          # 进入+地点
        "进入战斗状态",              # 进入+状态
        "推开门进入大厅",            # 句中进入
        "来到桥边观察周围动静",      # 来到+周(周围)
        "切到防御姿态",              # 切到+非时间
        "等到他们全都睡着",          # 等到+人称(人称否决)
        "直接进入正题吧",            # 进入+抽象名词
    ]:
        assert detect_time_directives(text) == [], text


def test_single_char_values_rejected():
    """单字命中的旧假阳性源:周(四周)/次(第二次)/天(天空)/早(早知道)。"""
    for v in ["四周环境", "第二次尝试", "天空之城", "早知道这样", "后山小路"]:
        assert not looks_like_time_value(v), v


# ── 真跳跃指令回归:token 化后不能漏 ──────────────────────────────────

def test_legit_directives_still_detected():
    cases = {
        "时间跳到三天后": "三天后",
        "快进到第二天清晨": "第二天清晨",
        "跳转到第 3 章": "第 3 章",
        "时间线来到公元1024年": "公元1024年",
        "等到天亮": "天亮",
        "快进到傍晚": "傍晚",
        "进入夜晚": "夜晚",
        "跳到明天早上八点": "明天早上八点",
    }
    for text, expect_substr in cases.items():
        got = detect_time_directives(text)
        assert got, f"漏检: {text}"
        assert expect_substr in got[0].target, f"{text} → {got[0].target}"


def test_legit_time_values_accepted():
    for v in ["三天后", "翌日", "第二天", "深夜", "半个月后", "两年前", "八点半", "片刻之后"]:
        assert looks_like_time_value(v), v


def test_recall_framing_still_suppressed():
    """v1.26.4 既有行为:回忆框架不判跳跃。"""
    assert detect_time_directives("我继续回想:在进入主神空间前的日子") == []


# ── 第四案(v1.73.2,群反馈·行者无疆):叙述里顺带提到年份/章号 ≠ 跳跃指令 ──────
# 原话:「推剧情的时候输入 NPC 背景:起源故事:1968年,年幼的威廉(昵称"JB")和母亲
# 参加了'天空景观餐厅'的开幕派对。AIGM 会识别到时间跳转到 1968 年」。
# 病灶:「第N章」「N年」两条 pattern 把跳跃动词写成**可选**前缀,退化成
# 「文本里出现章号/年份检测器」。它们在有动词时本就被第一条 pattern 覆盖,
# 唯一的独立价值是玩家只打一个裸时点。

_CONTENT_DUMPS = [
    'NPC背景: 起源故事: 1968年，年幼的威廉（昵称"JB"）和母亲参加了"天空景观餐厅"的开幕派对。',
    "他在1985年出生，后来搬到了纽约",
    "设定补充：主角的祖父生于1902年，参加过战争",
    "我翻开那本书，扉页上写着1937年出版",
    "根据档案，第12章记载的事件另有隐情",
]


def test_year_or_chapter_inside_prose_is_not_a_directive():
    for text in _CONTENT_DUMPS:
        assert detect_time_directives(text) == [], text


def test_mentions_time_without_intent_flags_those_dumps():
    """LLM 侧门控(context_agent)用同一个判据,别让子代理绕过确定性结论。"""
    for text in _CONTENT_DUMPS:
        assert mentions_time_without_intent(text), text


def test_mentions_time_without_intent_does_not_flag_real_directives():
    for text in ["跳到1968年", "推进到第30章", "第30章", "1968年", "/time 第30章",
                 "我们等到天亮再行动", "睡到第二天早上"]:
        assert not mentions_time_without_intent(text), text


def test_bare_time_target_still_works():
    """唯一允许省略跳跃动词的形态:整条输入就是一个时点。"""
    for text, expect in [("第30章", "第30章"), ("1968年", "1968年"),
                         ("  第 12 章 ", "第 12 章"), ("第30章 中秋夜", "第30章 中秋夜")]:
        assert is_bare_time_target(text), text
        got = detect_time_directives(text)
        assert got and got[0].target == expect, f"{text} → {[g.target for g in got]}"


def test_verb_forms_unaffected_by_removing_optional_prefix_patterns():
    """去掉那两条可选前缀 pattern 后,带动词的写法必须仍由第一条 pattern 兜住。"""
    for text, expect in [("跳到1968年", "1968年"), ("推进到第30章", "第30章"),
                         ("时间线来到公元1024年", "公元1024年"), ("直接进入第 7 章", "第 7 章")]:
        got = detect_time_directives(text)
        assert got and expect in got[0].target, f"{text} → {[g.target for g in got]}"


def test_has_jump_verb_shape():
    assert has_jump_verb("跳到1968年") and has_jump_verb("/time 第30章")
    assert not has_jump_verb("他在1985年出生")
