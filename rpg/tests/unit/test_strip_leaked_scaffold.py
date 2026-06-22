"""确定性剥离泄漏进正文的【检索/世界线脚手架块】+ 防误伤正常叙事(线上反馈 #77)。

弱模型(deepseek-v4-flash)有时把后端注入的 `=== 时间线检索锚点 ===` 等隐形上下文块 +
内部推理直接吐进玩家可见正文。这些 header 是后端自己生成的固定字符串,正常叙事永不逐字
产出,因此整块剥除零误伤。
"""
from state.json_ops import strip_leaked_scaffold


def test_strips_reported_77_leak():
    # 反馈 #77 原文(纯泄漏:推理前言 + 两个脚手架块,无真正文)
    leaked = (
        "等等，还有【时间跳跃待确认】——当前有pending的时间跳跃请求，但玩家没有在本次输入中"
        "确认，所以我不能把时间推进到目标时间。继续保持当前时间线。 好，开始输出。\n\n"
        "=== 时间线检索锚点 ===\n"
        "当前时间：序章 · 站台\n"
        '待确认跳跃："" </parameter\n'
        '本轮检索标签："" </parameter\n'
        "来源：当前导入剧本（不读默认 MuMu 原著时间线）\n\n"
        "=== 存档独立时间线·玩家创造的历史 ===\n"
        "本存档暂无历史锚点。"
    )
    out = strip_leaked_scaffold(leaked)
    assert "===" not in out
    assert "时间线检索锚点" not in out
    assert "存档独立时间线" not in out
    assert "本轮检索标签" not in out


def test_interleaved_recovers_real_narrative():
    # 推理前言 + 脚手架块 + 真正文 → 只留真正文
    text = (
        "好，开始输出。\n\n"
        "=== 时间线检索锚点 ===\n"
        "当前时间：序章 · 站台\n"
        "待确认跳跃：无\n\n"
        "林夕睁开眼睛，废墟在昏黄的天光下铺展开来。空气里有铁锈味。"
    )
    out = strip_leaked_scaffold(text)
    assert out == "林夕睁开眼睛，废墟在昏黄的天光下铺展开来。空气里有铁锈味。"


def test_mid_scaffold_keeps_surrounding_prose():
    text = "她走进废墟。\n\n=== 相关原文片段 ===\n某段原文\n\n她捡起一把生锈的刀。"
    out = strip_leaked_scaffold(text)
    assert "相关原文片段" not in out
    assert "她走进废墟。" in out
    assert "她捡起一把生锈的刀。" in out


def test_clean_narrative_unchanged():
    clean = "林夕站在站台上，列车的残骸横在轨道上。「有人吗？」她低声问。"
    assert strip_leaked_scaffold(clean) == clean


def test_non_scaffold_divider_untouched():
    # 模型用 === 序章 === 当分隔线,不是后端脚手架 header → 不剥
    divider = "第一幕\n\n=== 序章 ===\n\n她推开门，走了进去。"
    assert strip_leaked_scaffold(divider) == divider


def test_empty_and_no_marker():
    assert strip_leaked_scaffold("") == ""
    assert strip_leaked_scaffold("普通正文没有任何标记。") == "普通正文没有任何标记。"
