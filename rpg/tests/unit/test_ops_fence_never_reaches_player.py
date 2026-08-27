"""test_ops_fence_never_reaches_player.py — 「玩家永远不该看到 ops 围栏」的不变量守卫。

`strip_json_state_ops` 的 docstring 从一开始就写着这条不变量,但生产扫全量 messages
(2026-08)发现 **29 条 / 11 个存档**的玩家可见正文里仍带着 ```json 围栏,而且逐月上升
(6 月 7 → 7 月 4 → 8 月 18)。两种形态各有各的原因:

  A. 截断:`...正文。\\n\\n```json\\n[,,,,`
     **第 2 层毁掉了第 3 层的判据** —— `_strip_bare_json_ops` 把截断的 ops 对象剥走只留
     `[,,,,`,而 `_strip_trailing_unclosed_ops` 要求文本里还有 `"op"`/`"path"` 才动手。
  B. 文本形态工具调用:`...正文。\\n\\n```json\\n</parameter>\\n</function>`
     围栏体里根本不是 JSON,前三层按定义都不匹配。

本文件用**生产实测的真实尾巴**锁死,并守住反向不变量:普通围栏(```python)不许误伤。
"""
from __future__ import annotations

import pytest

from state.json_ops import strip_json_state_ops

# 生产原样(已去掉剧情正文,只留围栏与紧邻的一句)
REAL_TAILS = [
    ("截断-逗号残骸", "那里。它知道门开了。\n\n```json\n[,,,,"),
    ("截断-带小标题", "没有。\n\n〔世界状态〕\n```json\n[,,"),
    ("文本形态工具调用", "气平静。「罚完了。」\n\n```json\n</parameter>\n</fu"),
    ("截断-半个 op", '正文。\n\n```json\n[{"op":"set","path":"world.time",'),
    ("截断-刚开括号", "正文。\n\n```json\n["),
    ("截断-只有围栏头", "正文。\n\n```json"),
    ("state-ops 变体", "正文。\n\n```state-ops\n[,,"),
    ("裸围栏开数组", "正文。\n\n```\n[{"),
]


@pytest.mark.parametrize("label,text", REAL_TAILS, ids=[t[0] for t in REAL_TAILS])
def test_ops_fence_never_survives(label, text):
    out = strip_json_state_ops(text)
    assert "```" not in out, f"[{label}] 围栏漏给玩家了: {out!r}"
    assert "op\"" not in out, f"[{label}] ops 残片漏给玩家了: {out!r}"


def test_narrative_before_the_fence_is_kept():
    """只砍围栏,不许连正文一起砍 —— 这是与「宁可漏也别误删」的平衡点。"""
    out = strip_json_state_ops("她推开门,风灌了进来。\n\n```json\n[,,,,")
    assert out == "她推开门,风灌了进来。", out


ORDINARY_FENCES = [
    ("闭合的 python", "讲解一下:\n```python\nprint(1)\n```"),
    ("未闭合的 python", "他说:「看这段」\n\n```python\nx=1"),
    ("未闭合的 bash", "执行:\n```bash\nls -la"),
    ("闭合的无 info 文本围栏", "引用:\n```\n一段引文\n```"),
]


@pytest.mark.parametrize("label,text", ORDINARY_FENCES, ids=[t[0] for t in ORDINARY_FENCES])
def test_ordinary_fences_are_not_touched(label, text):
    """窄口径:只认 ops info(json/state-ops/state)或直接开 [ / { 的围栏。
    其余围栏原样保留 —— 与流式那侧 StreamFenceGuard 同一口径,别各写各的。"""
    out = strip_json_state_ops(text)
    assert "```" in out, f"[{label}] 普通围栏被误删: {out!r}"


def test_layer_two_no_longer_hides_the_evidence_from_the_last_layer():
    """回归锁:第 2 层剥掉 ops 对象后 `"op"` 标记消失,第 3 层因此不动手 ——
    第 4 层必须与围栏体内容无关,不能再依赖任何 ops 标记。"""
    truncated = ('正文。\n\n```json\n'
                 '[{"op":"set","path":"world.time","value":"黄昏"},'
                 '{"op":"append","path":"memory.notes","value":"门开了"},')
    out = strip_json_state_ops(truncated)
    assert out == "正文。", out


def test_whole_message_is_just_a_fence():
    """整条回复只有 ops(没有叙事)→ 玩家侧得到空串,而不是一坨围栏。"""
    assert strip_json_state_ops('```json\n[{"op":"set","path":"a","value":1}]\n```') == ""
    assert strip_json_state_ops("```json\n[,,") == ""
