"""确定性剥离 GM 泄漏进正文的英文'工具预告'元叙述 + 防误伤角色英文台词。"""
from state.json_ops import strip_meta_tool_preamble


def test_strips_reported_anchor_preamble():
    # 用户反馈截图原文
    narrative = (
        "殿外,廊下的风拂过,带着清晨特有的、微凉的草木气息。远处隐约传来钟楼的报时声,"
        "和皇都苏醒的市井微响。晨光在一片寂静中无声流转,将这一方天地分成内外两半。"
    )
    leaked = (
        narrative
        + "The scene has progressed naturally. "
        + "Let me mark the anchors that have been satisfied through our gameplay."
    )
    out = strip_meta_tool_preamble(leaked)
    assert out.rstrip() == narrative.rstrip(), out
    assert "Let me mark" not in out
    assert "scene has progressed" not in out


def test_strips_standalone_line_preamble():
    txt = "他握紧了拳头,转身离去。\nLet me update the anchors now."
    out = strip_meta_tool_preamble(txt)
    assert out.rstrip() == "他握紧了拳头,转身离去。"


def test_keeps_english_dialogue_in_quotes():
    # 角色英文台词在引号内,绝不剥
    txt = '老外摊手,用蹩脚的中文夹英文说:「Let me record this, my friend.」'
    out = strip_meta_tool_preamble(txt)
    assert out == txt, out


def test_pure_chinese_narrative_unchanged():
    txt = "雨停了,她抬头看了看灰白的天,低声说了句什么,转身走进巷子深处。"
    assert strip_meta_tool_preamble(txt) == txt


def test_non_meta_english_tail_unchanged():
    # 结尾的英文若不是工具预告(普通叙事/招牌),不应被剥
    txt = "他抬头,看见酒馆招牌上写着 The Rusty Anchor。"
    out = strip_meta_tool_preamble(txt)
    assert "Rusty Anchor" in out, out


def test_empty_and_none_safe():
    assert strip_meta_tool_preamble("") == ""
    assert strip_meta_tool_preamble("   ") in ("   ", "")
