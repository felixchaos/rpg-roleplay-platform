"""反馈 #28 回归锁定:短 RP 输入 → 确定性注入「镜头规则」元指令。

玩家写很短的 RP 时,GM 容易把叙事全用来扩写/复述玩家自己的动作,而玩家想看的是
对方 NPC 的反应。修复在 chat_pipeline 代码侧确定性判定短输入并前置最高优先级元指令。
这里锁定纯判定函数 _should_inject_short_input_directive 的行为,防回归。
"""
import importlib

import chat_pipeline as cp


def test_short_chinese_rp_fires():
    assert cp._should_inject_short_input_directive("我点头。") is True
    assert cp._should_inject_short_input_directive("我握紧拳头,看着她,沉默不语。") is True
    assert cp._should_inject_short_input_directive("我转身离开了这间房间，没有再回头。") is True


def test_long_rp_does_not_fire():
    long_rp = (
        "我走到窗边，推开那扇半掩的木窗，夜风裹着潮湿的海腥味灌进来，"
        "我深吸一口气，回头望向仍坐在桌前的她，缓缓开口问道：你还好吗？"
    )
    assert cp._should_inject_short_input_directive(long_rp) is False


def test_commands_and_empty_do_not_fire():
    assert cp._should_inject_short_input_directive("/set 现在是夜晚") is False
    assert cp._should_inject_short_input_directive("/reveal 我是穿越者") is False
    assert cp._should_inject_short_input_directive("") is False
    assert cp._should_inject_short_input_directive("   ") is False
    assert cp._should_inject_short_input_directive(None) is False


def test_threshold_boundary_respects_env(monkeypatch):
    # 阈值由 RPG_SHORT_INPUT_CHARS 决定;改环境后 reimport 生效。
    monkeypatch.setenv("RPG_SHORT_INPUT_CHARS", "5")
    # 拆包后阈值常量落在子模块 chat_pipeline._input_signals;先重载它,再重载门面包 cp。
    importlib.reload(cp._input_signals)
    importlib.reload(cp)
    try:
        assert cp._should_inject_short_input_directive("一二三四五") is True   # len 5 == 阈值
        assert cp._should_inject_short_input_directive("一二三四五六") is False  # len 6 > 阈值
    finally:
        monkeypatch.delenv("RPG_SHORT_INPUT_CHARS", raising=False)
        importlib.reload(cp._input_signals)
        importlib.reload(cp)


# 群反馈(行者无疆):「继续」按钮固定文案(7字)命中短输入镜头规则→GM 被钉在原地写
# 反应戏,与按钮承诺「推进一段剧情」相反=点继续必水文。豁免+由推进规则接管。

def test_continue_texts_recognized_and_exempt():
    from chat_pipeline import _is_continue_request, _should_inject_short_input_directive
    for t in ("（继续推进剧情）", "(Continue the scene)", "继续推进剧情", " （继续推进剧情） "):
        assert _is_continue_request(t), t
        assert not _should_inject_short_input_directive(t), t


def test_ordinary_short_input_still_hits_lens_rule():
    from chat_pipeline import _is_continue_request, _should_inject_short_input_directive
    for t in ("嗯", "我点点头", "继续", "（微笑）"):
        assert not _is_continue_request(t), t
        assert _should_inject_short_input_directive(t), t


def test_continue_directive_demands_forward_motion():
    from chat_pipeline import _CONTINUE_DIRECTIVE
    assert "推进" in _CONTINUE_DIRECTIVE
    assert "软目标" in _CONTINUE_DIRECTIVE
