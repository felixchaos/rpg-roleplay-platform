"""Unit tests for agents.command_agent.

测试 /set 命令解析逻辑 (纯本地函数 + mock LLM)。
不调真实 API,不依赖 DB / 文件系统。
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch


class TestParseToolCallJsonArray(unittest.TestCase):
    """_parse_tool_call_json_array: 容错 JSON 解析。"""

    def _fn(self, text):
        from agents.command_agent import _parse_tool_call_json_array
        return _parse_tool_call_json_array(text)

    def test_empty_string_returns_empty(self):
        self.assertEqual(self._fn(""), [])

    def test_none_like_empty(self):
        # None-ish: pass empty string
        self.assertEqual(self._fn("  "), [])

    def test_valid_json_array(self):
        calls = self._fn(
            '[{"name":"set_player_location","input":{"location":"北港码头"}}]'
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "set_player_location")
        self.assertEqual(calls[0]["input"]["location"], "北港码头")

    def test_json_fence_code_block(self):
        text = (
            "```json\n"
            '[{"name":"set_main_quest","input":{"quest":"营救蕾穆丽娜"}}]\n'
            "```"
        )
        calls = self._fn(text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "set_main_quest")

    def test_bare_array_embedded_in_text(self):
        text = (
            "以下是工具调用:\n"
            '[{"name":"set_world_time","input":{"target":"月球纪元"}}]\n'
            "已完成。"
        )
        calls = self._fn(text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "set_world_time")

    def test_invalid_json_returns_empty(self):
        calls = self._fn("这根本不是 JSON 格式哦")
        self.assertEqual(calls, [])

    def test_multiple_calls(self):
        arr = json.dumps([
            {"name": "set_world_time", "input": {"target": "黎明"}},
            {"name": "set_player_location", "input": {"location": "王都广场"}},
        ])
        calls = self._fn(arr)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["name"], "set_world_time")
        self.assertEqual(calls[1]["name"], "set_player_location")


class TestCoerceCalls(unittest.TestCase):
    """_coerce_calls: 把各种形状统一成 [{name, input}, ...]。"""

    def _fn(self, parsed):
        from agents.command_agent import _coerce_calls
        return _coerce_calls(parsed)

    def test_list_of_dicts(self):
        out = self._fn([{"name": "set_player_name", "input": {"name": "felix"}}])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["name"], "set_player_name")

    def test_dict_with_calls_key(self):
        out = self._fn({"calls": [{"name": "set_player_role", "input": {"role": "侦探"}}]})
        self.assertEqual(out[0]["name"], "set_player_role")

    def test_dict_with_tool_calls_key(self):
        out = self._fn({"tool_calls": [{"name": "add_memory_fact", "input": {"fact": "X"}}]})
        self.assertEqual(out[0]["name"], "add_memory_fact")

    def test_single_dict_with_name(self):
        out = self._fn({"name": "clarify", "input": {"question": "什么意思?"}})
        self.assertEqual(out[0]["name"], "clarify")

    def test_unknown_tool_name_filtered_out(self):
        out = self._fn([{"name": "hack_database", "input": {}}])
        self.assertEqual(out, [])

    def test_non_dict_items_skipped(self):
        out = self._fn(["not_a_dict", {"name": "set_player_name", "input": {"name": "Y"}}])
        self.assertEqual(len(out), 1)

    def test_alternative_keys_tool_and_arguments(self):
        """支持 tool/arguments 作为 name/input 的别名。"""
        out = self._fn([{"tool": "set_main_quest", "arguments": {"quest": "复仇"}}])
        self.assertEqual(out[0]["name"], "set_main_quest")
        self.assertEqual(out[0]["input"]["quest"], "复仇")

    def test_non_dict_args_skipped(self):
        """args 不是 dict 的条目应被过滤。"""
        out = self._fn([{"name": "set_player_name", "input": "not_a_dict"}])
        self.assertEqual(out, [])


class TestBuildUserPrompt(unittest.TestCase):
    """_build_user_prompt: 组装 user message 字符串。"""

    def _fn(self, set_text, state_data):
        from agents.command_agent import _build_user_prompt
        return _build_user_prompt(set_text, state_data)

    def test_includes_set_text(self):
        prompt = self._fn("/set 位置=北港", {"player": {}, "world": {}, "memory": {}})
        self.assertIn("/set 位置=北港", prompt)

    def test_includes_state_fields(self):
        state = {
            "player": {"name": "felix", "role": "侦探", "current_location": "码头"},
            "world": {"time": "黎明", "timeline": {"current_label": "第一幕"}},
            "memory": {"main_quest": "找人", "current_objective": "打探消息"},
        }
        prompt = self._fn("/set test", state)
        self.assertIn("felix", prompt)
        self.assertIn("侦探", prompt)
        self.assertIn("码头", prompt)
        self.assertIn("黎明", prompt)

    def test_empty_state_doesnt_crash(self):
        prompt = self._fn("/set x=1", {})
        self.assertIsInstance(prompt, str)
        self.assertIn("(空)", prompt)

    def test_long_set_text_truncated(self):
        long_text = "A" * 2000
        prompt = self._fn(long_text, {})
        # 截断到 1500 字符
        self.assertLessEqual(prompt.count("A"), 1500)


class TestParseSetCommandPublicApi(unittest.TestCase):
    """parse_set_command: 公开入口的边界条件 (mock LLM backend)。"""

    def test_empty_string_returns_empty(self):
        from agents.command_agent import parse_set_command
        result = parse_set_command("", {}, user_id=None)
        self.assertEqual(result, [])

    def test_whitespace_only_returns_empty(self):
        from agents.command_agent import parse_set_command
        result = parse_set_command("   ", {}, user_id=None)
        self.assertEqual(result, [])

    def test_exception_returns_empty(self):
        """后端调用抛异常时, 返回 [] 而不是崩溃。"""
        from agents.command_agent import parse_set_command
        with patch("agents.command_agent._call_anthropic_tools", side_effect=RuntimeError("boom")), \
             patch("agents.command_agent._detect_default_api", return_value="anthropic"), \
             patch("agents.command_agent._resolve_preferred_api", return_value=None), \
             patch("agents.command_agent._resolve_preferred_model", return_value=None):
            result = parse_set_command("/set 位置=北港", {}, user_id=None)
        self.assertEqual(result, [])

    def test_anthropic_backend_returns_calls(self):
        """anthropic 路径: mock _call_anthropic_tools 返回预期 tool calls。"""
        from agents.command_agent import parse_set_command
        mock_calls = [{"name": "set_player_location", "input": {"location": "北港码头"}}]
        with patch("agents.command_agent._call_anthropic_tools", return_value=mock_calls), \
             patch("agents.command_agent._detect_default_api", return_value="anthropic"), \
             patch("agents.command_agent._resolve_preferred_api", return_value=None), \
             patch("agents.command_agent._resolve_preferred_model", return_value=None):
            result = parse_set_command("/set 当前位置=北港码头", {}, user_id=None)
        self.assertEqual(result, mock_calls)

    def test_vertex_backend_returns_calls(self):
        """vertex_ai 路径: mock _call_vertex_tools。"""
        from agents.command_agent import parse_set_command
        mock_calls = [{"name": "set_world_time", "input": {"target": "黎明"}}]
        with patch("agents.command_agent._call_vertex_tools", return_value=mock_calls), \
             patch("agents.command_agent._detect_default_api", return_value="vertex_ai"), \
             patch("agents.command_agent._resolve_preferred_api", return_value=None), \
             patch("agents.command_agent._resolve_preferred_model", return_value=None):
            result = parse_set_command("/set 时间=黎明", {}, user_id=None)
        self.assertEqual(result, mock_calls)


class TestSchemaArgs(unittest.TestCase):
    """_schema_args: 把 JSON schema 序列化成函数签名字符串。"""

    def _fn(self, schema):
        from agents.command_agent import _schema_args
        return _schema_args(schema)

    def test_required_param_no_suffix(self):
        schema = {"properties": {"location": {}}, "required": ["location"]}
        result = self._fn(schema)
        self.assertIn("location", result)
        self.assertNotIn("?", result)

    def test_optional_param_has_question_mark(self):
        schema = {"properties": {"note": {}}, "required": []}
        result = self._fn(schema)
        self.assertIn("note?", result)

    def test_empty_schema(self):
        result = self._fn({})
        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
