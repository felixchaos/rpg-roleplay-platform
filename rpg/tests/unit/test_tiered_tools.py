"""阶梯化工具加载(tiered / progressive disclosure)回归。

动机:91 个工具完整 schema 每轮全发 ≈ 9.5k token;酒馆大多数轮次不调工具,白烧。改成
窗口内直发 + 窗口外进 load_tools 目录按需加载。本测试覆盖:① 共享助手切窗口/解析 load/
append-only;② 真实酒馆工具表在窗口=16 下的 token 体积大降;③ load_tools 元工具形态正确。
"""
from __future__ import annotations

import json
import os
import unittest

os.environ.setdefault("RPG_DEPLOYMENT_MODE", "local")


class TestTieredHelper(unittest.TestCase):
    def _tools(self, n):
        return [
            {"server_id": "__dispatcher__", "name": f"tool_{i}",
             "description": f"工具 {i} 的描述\n第二行不该进目录", "schema": {"type": "object", "properties": {}}}
            for i in range(n)
        ]

    def test_split_window(self):
        from agents.gm.backends import _tiered
        win, ovf, cat = _tiered.split_window(self._tools(30), 16, True)
        self.assertEqual(len(win), 16)
        self.assertEqual(len(ovf), 14)
        self.assertEqual(len(cat), 14)
        # 目录只取描述首行 + 截断
        self.assertIn("工具 16 的描述", cat[0])
        self.assertNotIn("第二行", cat[0])

    def test_disabled_discards_overflow(self):
        from agents.gm.backends import _tiered
        win, ovf, cat = _tiered.split_window(self._tools(30), 16, False)
        self.assertEqual(len(win), 16)
        self.assertEqual(ovf, {})
        self.assertEqual(cat, [])

    def test_resolve_load_append_only(self):
        from agents.gm.backends import _tiered
        tools = self._tools(30)
        _, ovf, _ = _tiered.split_window(tools, 16, True)
        name20 = _tiered.tool_full_name(tools[20])
        already: set[str] = set()
        newly, ack = _tiered.resolve_load({"names": [name20, "不存在"]}, ovf, already)
        self.assertEqual(len(newly), 1)
        self.assertIn("已加载", ack)
        self.assertIn("未找到", ack)
        # 重复 load 不重复 append(维持 append-only / 前缀缓存)
        newly2, _ = _tiered.resolve_load({"names": [name20]}, ovf, already)
        self.assertEqual(len(newly2), 0)

    def test_resolve_load_str_coercion(self):
        from agents.gm.backends import _tiered
        tools = self._tools(20)
        _, ovf, _ = _tiered.split_window(tools, 16, True)
        nm = _tiered.tool_full_name(tools[17])
        newly, _ = _tiered.resolve_load({"names": nm}, ovf, set())  # 传字符串而非数组
        self.assertEqual(len(newly), 1)

    def test_load_tools_meta_shape(self):
        from agents.gm.backends import _tiered
        self.assertEqual(_tiered.LOAD_TOOLS_FULL_NAME, "tiered__load_tools")
        self.assertTrue(_tiered.is_load_tools("tiered", "load_tools"))
        self.assertFalse(_tiered.is_load_tools("__dispatcher__", "generate_image"))
        desc = _tiered.load_tools_description(["- a: x", "- b: y"])
        self.assertIn("- a: x", desc)
        self.assertEqual(_tiered.LOAD_TOOLS_PARAMS["required"], ["names"])


class TestTokenReductionRealTavern(unittest.TestCase):
    """真实酒馆工具表(~91 个):窗口=16 的 OpenAI tools 数组应远小于全发。"""

    def _openai_array(self, mcp_tools, window):
        """复刻 openai_compat 的 tools 数组构建(窗口内完整 + load_tools 目录)。"""
        import re

        from agents.gm.backends import _tiered
        sep = "__"

        def mk(t):
            sid = re.sub(r"[^A-Za-z0-9_-]", "_", str(t.get("server_id", "")))
            tn = re.sub(r"[^A-Za-z0-9_-]", "_", str(t.get("name", "")))
            return {"type": "function", "function": {
                "name": f"{sid}{sep}{tn}"[:64],
                "description": (t.get("description") or "")[:512],
                "parameters": t.get("schema") or {"type": "object", "properties": {}},
            }}

        win, _ovf, cat = _tiered.split_window(mcp_tools, window, True)
        arr = [mk(t) for t in win]
        if cat:
            arr.append({"type": "function", "function": {
                "name": _tiered.LOAD_TOOLS_FULL_NAME,
                "description": _tiered.load_tools_description(cat),
                "parameters": _tiered.LOAD_TOOLS_PARAMS,
            }})
        return arr

    def test_window_16_far_smaller_than_full(self):
        from tools_dsl.chat_tool_router import build_unified_tool_list
        from tools_dsl.command_tools_register import ensure_registered
        ensure_registered()
        tav = build_unified_tool_list([], origin="llm_chat", mode="tavern_gm", bound_script_id=None)
        self.assertGreater(len(tav), 60, "酒馆应有几十个工具")

        from agents.gm.backends._tiered import effective_window

        full = self._openai_array(tav, window=len(tav))      # 全发(旧行为基线)
        win16 = self._openai_array(tav, window=16)           # 阶梯化
        full_chars = len(json.dumps(full, ensure_ascii=False))
        win16_chars = len(json.dumps(win16, ensure_ascii=False))
        # 阶梯化后体积应显著下降(目录是一句话 vs 完整 schema)
        self.assertLess(win16_chars, full_chars * 0.55,
                        f"窗口16={win16_chars} 应 < 全发{full_chars} 的 55%")
        # v1.82.0:传进去的 16 是**下限**,真实窗口由 effective_window 按「主回合闭环工具
        # 必须全部直发」这条不变量定容(酒馆自举 6 + 闭环 4 + 锚点族 6 → 会撑到 20)。
        # 断言必须走同一个函数,抄字面量 16 就会在任何一次档位调整后假红。
        eff = effective_window(tav, 16)
        self.assertGreaterEqual(eff, 16)
        names = {x["function"]["name"] for x in win16}
        self.assertIn("tiered__load_tools", names)
        self.assertEqual(len(win16), eff + 1)  # 窗口 + 1 load_tools


class TestAnthropicLoadToolsRoundTrip(unittest.TestCase):
    """anthropic backend 的 load_tools 闭环:模型先 load 窗口外工具,下一轮即可调用它。
    用 __new__ 跳过需要 API key 的 __init__,monkeypatch stream_with_tools_native 脚本化事件。"""

    def test_load_then_call(self):
        from agents.gm.backends import _tiered
        from agents.gm.backends.anthropic import _AnthropicBackend as AnthropicBackend
        from tools_dsl.chat_tool_router import build_unified_tool_list
        from tools_dsl.command_tools_register import ensure_registered
        ensure_registered()
        from core.config import tool_window_size
        tav = build_unified_tool_list([], origin="llm_chat", mode="tavern_gm", bound_script_id=None)
        # 窗口大小取配置真值,别抄字面量——抄了就会在调 RPG_TOOL_WINDOW 默认值时假红
        # (v1.72.4:16→18,ovf 里挑的 target 其实已进窗口,断言 'target 不在轮1' 必炸)。
        # v1.82.0:「配置真值」现在是 effective_window(配置值只是下限,不变量会把它撑大)。
        _W = _tiered.effective_window(tav, tool_window_size())
        win_tools, ovf, _cat = _tiered.split_window(tav, _W, True)
        self.assertTrue(ovf, "应有窗口外工具")
        target = next(iter(ovf))  # 某个窗口外工具的 full name

        be = AnthropicBackend.__new__(AnthropicBackend)  # 跳过 __init__(无需 API key)

        seen_tool_names: list[list[str]] = []  # 每轮传给 native 的工具名
        script = [
            # 轮1:模型调 load_tools 加载窗口外的 target
            [{"type": "tool_use_block", "name": _tiered.LOAD_TOOLS_FULL_NAME,
              "id": "u1", "input": {"names": [target]}},
             {"type": "stop"}],
            # 轮2:模型调刚加载的 target 工具
            [{"type": "tool_use_block", "name": target, "id": "u2", "input": {}},
             {"type": "stop"}],
            # 轮3:收尾,无工具
            [{"type": "text", "text": "好的。"}, {"type": "stop"}],
        ]
        _it = iter(script)

        def fake_native(system, messages, anthropic_tools, max_tokens):
            seen_tool_names.append([t["name"] for t in anthropic_tools])
            for ev in next(_it):
                yield ev

        dispatched: list[tuple[str, str]] = []

        def fake_mcp(server_id, tool_name, args):
            dispatched.append((server_id, tool_name))
            return {"ok": True, "result": "done"}

        be.stream_with_tools_native = fake_native  # type: ignore[method-assign]
        list(be.stream_with_mcp_loop(
            "sys", [{"role": "user", "content": "hi"}], tav, 4, 256, fake_mcp))

        # 轮1:窗口工具 + load_tools,target 还没在
        self.assertIn(_tiered.LOAD_TOOLS_FULL_NAME, seen_tool_names[0])
        self.assertNotIn(target, seen_tool_names[0])
        self.assertLessEqual(len(seen_tool_names[0]), _W + 1)  # 窗口 + load_tools
        # 轮2:load 之后 target 被 append 进工具数组
        self.assertIn(target, seen_tool_names[1], "load 后 target 应出现在工具数组")
        # target 工具真的被 dispatch(且 load_tools 没走 dispatcher)
        self.assertNotIn(("tiered", "load_tools"), dispatched)
        sid, _, tn = target.partition("__")
        self.assertIn((sid, tn), dispatched)


if __name__ == "__main__":
    unittest.main()
