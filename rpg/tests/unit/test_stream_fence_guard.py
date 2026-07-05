"""StreamFenceGuard(流式 ops 围栏抑制)+ dedupe_json_ops(同批去重)单测。

背景:生产基线局实测 5 回合有 2 回合把 ```json ops 围栏原样流给玩家(打出来又消失);
turn1 updates 双报(GM 自带 fence + 史官追加 fence 同批双 apply)。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from state.json_ops import StreamFenceGuard, dedupe_json_ops  # noqa: E402

OPS_FENCE = '```json\n[{"op": "set", "path": "world.time", "value": "清晨"}]\n```'


def _run(chunks: list[str]) -> str:
    g = StreamFenceGuard()
    out = "".join(g.feed(c) for c in chunks)
    return out + g.flush()


def test_plain_text_passthrough():
    assert _run(["你好,", "旅人。"]) == "你好,旅人。"


def test_ops_fence_single_chunk_suppressed():
    assert _run(["正文结束。\n\n" + OPS_FENCE]) == "正文结束。\n\n"


def test_ops_fence_split_across_chunks():
    # ``` 被拆成「``」+「`json」——生产 token 流的真实形态
    chunks = ["正文一段", "``", "`json\n[{\"op\":\"set\",\"path\":\"a\",\"value\":1}]\n``", "`"]
    assert _run(chunks) == "正文一段"


def test_text_resumes_after_fence_close():
    chunks = [OPS_FENCE + "\n后续叙事继续。"]
    assert _run(chunks) == "后续叙事继续。"


def test_state_ops_keyword_fence_suppressed():
    assert _run(['```state-ops\n[{"op":"set","path":"a","value":1}]\n```']) == ""
    assert _run(['```state\n{"op":"set","path":"a","value":1}\n```']) == ""


def test_bare_fence_with_bracket_suppressed():
    assert _run(['```\n[{"op":"set","path":"a","value":1}]\n```后文']) == "后文"


def test_bare_fence_inline_bracket_suppressed():
    # 与 _JSON_STATE_OPS_RE 同口径:```[ 同行直接开数组
    assert _run(['```[{"op":"set","path":"a","value":1}]```尾巴']) == "尾巴"


def test_non_ops_fence_passthrough():
    # 非 ops 围栏原样放行(闭合后还有后文,闭合 ``` 可被判定为非 ops 而放行)。
    # 已知权衡:若流恰好在裸 ``` 处结束,flush 会保守扣住那 3 个反引号 —— 因为
    # 无法区分「闭合」与「被截断的 ops 开栏」,宁可少显示 3 个反引号也不漏半个 ops。
    code = "```python\nprint('hi')\n```\n完。"
    assert _run([code]) == code


def test_inline_backticks_not_dropped():
    # 正文里的内联反引号最多延迟放行,永不丢字
    assert _run(["他说`你好`", "就走了"]) == "他说`你好`就走了"
    assert _run(["尾巴是一个`"]) == "尾巴是一个`"


def test_unclosed_fence_dropped_at_flush():
    # GM 被截断留半个围栏:不放行(落库侧 _strip_trailing_unclosed_ops 兜 response)
    assert _run(["正文。\n```json\n[{\"op\":\"set\",", "\"path\":\"a\""]) == "正文。\n"


def test_partial_fence_head_dropped_at_flush():
    # 流在围栏头打了一半时结束("```jso")
    assert _run(["正文。", "```jso"]) == "正文。"


def test_keyword_split_across_chunks():
    chunks = ["前文```st", "ate-ops\n[{\"op\":\"set\",\"path\":\"a\",\"value\":1}]\n```后文"]
    assert _run(chunks) == "前文后文"


def test_two_fences_same_stream():
    chunks = ["A" + OPS_FENCE + "B", OPS_FENCE, "C"]
    assert _run(chunks) == "ABC"


def test_dedupe_json_ops_removes_same_batch_duplicates():
    op1 = {"op": "set", "path": "world.time", "value": "清晨"}
    op2 = {"op": "set", "path": "player.current_location", "value": "村口"}
    ops = [op1, op2, dict(op1), dict(op2)]
    result = dedupe_json_ops(ops)
    assert result == [op1, op2]


def test_dedupe_json_ops_keeps_distinct_values():
    ops = [
        {"op": "set", "path": "world.time", "value": "清晨"},
        {"op": "set", "path": "world.time", "value": "正午"},
    ]
    assert dedupe_json_ops(ops) == ops


def test_dedupe_json_ops_unserializable_kept():
    class Weird:  # json.dumps 会失败
        pass

    ops = [{"op": "set", "path": "a", "value": Weird()}]
    assert len(dedupe_json_ops(ops)) == 1
