"""Unit tests for core.json_parse.parse_llm_json (统一鲁棒 LLM JSON 解析)。

覆盖:① 直接解析 ② 剥 ```json 围栏 ③ 带字符串/转义感知的平衡括号扫描
(取最早开括号);want=dict/list 类型过滤;解析不到返 None;以及本次收口
要修掉的两个历史弱点(rfind('}') 被正文 } 坑 / 贪婪 .* 跨多对象)。
纯本地函数,无 DB / 无网络。
"""
from __future__ import annotations

import unittest

from core.json_parse import parse_llm_json


class ParseLlmJsonBasic(unittest.TestCase):
    def test_empty_returns_none(self):
        self.assertIsNone(parse_llm_json(""))
        self.assertIsNone(parse_llm_json("   "))

    def test_direct_object(self):
        self.assertEqual(parse_llm_json('{"a": 1}'), {"a": 1})

    def test_direct_list(self):
        self.assertEqual(parse_llm_json("[1, 2, 3]"), [1, 2, 3])

    def test_unparseable_returns_none(self):
        self.assertIsNone(parse_llm_json("纯文本没有 JSON"))
        self.assertIsNone(parse_llm_json("{not valid"))


class ParseLlmJsonFence(unittest.TestCase):
    def test_json_fence(self):
        text = '前言\n```json\n{"k": "v"}\n```\n后语'
        self.assertEqual(parse_llm_json(text), {"k": "v"})

    def test_bare_fence(self):
        text = '```\n[1, 2]\n```'
        self.assertEqual(parse_llm_json(text), [1, 2])


class ParseLlmJsonBalancedScan(unittest.TestCase):
    def test_leading_garbage_object(self):
        text = 'Sure, here you go: {"x": 10} -- done'
        self.assertEqual(parse_llm_json(text), {"x": 10})

    def test_earliest_open_bracket_list_wins(self):
        # list 响应里内层有 {} —— 必须先抓最早的 [ 而非内层 {
        text = 'noise [{"name": "a"}, {"name": "b"}] tail'
        self.assertEqual(
            parse_llm_json(text),
            [{"name": "a"}, {"name": "b"}],
        )

    def test_brace_inside_string_not_confused(self):
        # 字符串字面量里的 } 不能提前闭合
        text = '{"note": "结尾有个右括号 } 在文字里", "ok": true}'
        self.assertEqual(
            parse_llm_json(text),
            {"note": "结尾有个右括号 } 在文字里", "ok": True},
        )

    def test_escaped_quote_in_string(self):
        text = r'prefix {"q": "他说\"你好\"", "n": 1} suffix'
        self.assertEqual(parse_llm_json(text), {"q": '他说"你好"', "n": 1})

    def test_fixes_rfind_brace_weakness(self):
        # 历史弱点:lo=first{ hi=last} 会把正文里的 } 算进去致解析失败。
        # 平衡扫描取最早 { 的平衡块,正确解析,正文的 } 不干扰。
        text = '回答如下 {"summary": "见上"} 备注:别忘了 } 这个符号'
        self.assertEqual(parse_llm_json(text), {"summary": "见上"})

    def test_fixes_greedy_multi_object_weakness(self):
        # 历史弱点:贪婪 \{.*\} 会从第一个 { 吃到最后一个 } 跨多对象 →
        # 拼出非法 JSON。平衡扫描保证拿到的是**一个完整对象**(本测试的原始意图)。
        # 2026-08-24 起选块规则从「取最早」改为「取信息量最大、同分取最后」:
        # 模型爱先复述一份 schema 示例再给答案,取最早 = 把示例当答案(静默错答案,
        # 比解析失败更糟)。本例两块同为 1 个字段 → 同分取最后。
        text = '{"first": 1}\n后面还有一个对象\n{"second": 2}'
        self.assertEqual(parse_llm_json(text), {"second": 2})

    def test_example_then_real_answer_picks_the_answer(self):
        # 反馈 #99 现场形态:模型先摆示例 {"is_character": false},再给真答案。
        text = ('参考格式:{"is_character": false}\n'
                '实际结果:\n{"is_character": true, "identity": "镖师", "appearance": "刀疤"}')
        self.assertEqual(
            parse_llm_json(text, want=dict),
            {"is_character": True, "identity": "镖师", "appearance": "刀疤"},
        )


class ParseLlmJsonWantFilter(unittest.TestCase):
    def test_want_dict_accepts_dict(self):
        self.assertEqual(parse_llm_json('{"a": 1}', want=dict), {"a": 1})

    def test_want_dict_rejects_list(self):
        self.assertIsNone(parse_llm_json("[1, 2, 3]", want=dict))

    def test_want_list_accepts_list(self):
        self.assertEqual(parse_llm_json("[1, 2]", want=list), [1, 2])

    def test_want_list_rejects_dict(self):
        self.assertIsNone(parse_llm_json('{"a": 1}', want=list))

    def test_want_none_accepts_any(self):
        self.assertEqual(parse_llm_json('{"a": 1}'), {"a": 1})
        self.assertEqual(parse_llm_json("[1]"), [1])


class ParseLlmJsonContractParity(unittest.TestCase):
    """核对各收口点失败契约的语义(在调用方各自包装,这里只验底层返回值)。"""

    def test_returns_none_not_raises(self):
        # parse_llm_json 永不抛(失败统一返 None);raise/[]/None 由调用方决定
        try:
            self.assertIsNone(parse_llm_json("garbage no json"))
            self.assertIsNone(parse_llm_json("garbage", want=dict))
            self.assertIsNone(parse_llm_json("garbage", want=list))
        except Exception as exc:  # pragma: no cover
            self.fail(f"parse_llm_json 不应抛异常: {exc!r}")


if __name__ == "__main__":
    unittest.main()


class ParseLlmJsonWeakModelShapes(unittest.TestCase):
    """反馈 #99 现场形态:思考型/中转模型的输出长什么样。

    这三种老实现全接不住,而拆书流水线恰好每种都撞得上:
      · <think>…</think> 推理块里自带花括号 → 扫描抓到推理里的假括号;
      · max_tokens 打断在半路 → 没有收尾括号,整份丢弃;
      · 尾逗号。
    """

    def test_think_block_with_braces_inside(self):
        text = ('<think>用户要 {"is_character": ...} 这种格式,我先想想这人是谁</think>\n'
                '{"is_character": true, "identity": "镖师"}')
        self.assertEqual(
            parse_llm_json(text, want=dict),
            {"is_character": True, "identity": "镖师"},
        )

    def test_unclosed_think_block_yields_none(self):
        # 被打断在推理里 = 根本没有正文,不能瞎捞
        self.assertIsNone(parse_llm_json("<think>我还在想这个人是谁"))

    def test_trailing_comma(self):
        self.assertEqual(parse_llm_json('{"a": 1,}'), {"a": 1})

    def test_truncated_object_needs_opt_in(self):
        raw = '{"is_character": true, "identity": "镖师", "personality": "嘴硬心'
        self.assertIsNone(parse_llm_json(raw), "默认不打捞截断响应")
        self.assertEqual(
            parse_llm_json(raw, allow_truncated=True),
            {"is_character": True, "identity": "镖师"},
            "打捞时保留已完整的字段,丢掉残缺的那个",
        )

    def test_truncated_list_keeps_only_complete_elements(self):
        # 截断的数组不能退化成「里面某个完整对象」—— 调用方按 list 迭代会拿到一堆键
        raw = '[{"name":"青云镖局","content":"江南最大镖局"},{"name":"落霞谷","content":"传说中'
        self.assertEqual(
            parse_llm_json(raw, allow_truncated=True),
            [{"name": "青云镖局", "content": "江南最大镖局"}],
        )

    def test_truncated_beyond_salvage(self):
        self.assertIsNone(parse_llm_json('{"is_char', allow_truncated=True))
        self.assertIsNone(parse_llm_json("[", allow_truncated=True))
