"""
test_cross_save_isolation.py — task 87 Phase 7 安全审查

核心安全不变量:
> 用户 A 在 save_100 的 chat 里跟 LLM 玩,LLM 不能通过工具调用偷偷修改 / 切换 /
> 删除/列出 save_200 的内容。

跨"世界泡"隔离矩阵:
  · 跨用户 (用户 A vs 用户 B): SQL where user_id = ? 强保护,任何工具都拒
  · 跨存档 (同用户 save_100 vs save_200): user 级 mutate 工具禁 LLM origin
  · 跨剧本 (script_id 不同): script 级工具默认只对当前 script

本测试枚举所有 user 级 mutate 工具,验证 LLM origin 一律被 origin_forbidden 拒。
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("RPG_REQUIRE_AUTH", "0")

from tools_dsl.command_dispatcher import (  # noqa: E402
    ToolCallEnvelope,
    ToolDispatcher,
    get_registry,
)
from tools_dsl.command_tools_register import force_reset_for_tests  # noqa: E402

# user 级 mutate 工具清单 (必须禁 LLM origin)
USER_MUTATE_TOOLS = {
    "activate_save", "rename_save", "delete_save",
    "activate_branch", "delete_branch", "continue_branch",
    "create_persona", "delete_persona",
    "create_character_card", "delete_character_card",
    "set_preference", "select_model",
    "start_script_import", "cancel_import_job",
    "resplit_script", "delete_script", "probe_models",
    "mcp_server_enable", "mcp_server_start", "mcp_server_stop",
}

# user 级 read 工具 (允许 LLM 调用)
USER_READ_TOOLS = {
    "list_my_saves", "list_branches",
    "list_my_personas", "list_my_character_cards",
    "list_my_credentials_meta", "list_scripts",
    "list_my_import_jobs", "get_import_status",
    "get_save_detail", "get_my_stats", "get_my_usage",
}


class UserMutateOriginsAreLocked(unittest.TestCase):
    """安全审查:任何 user 级 mutate 工具不允许 LLM origin。"""

    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def test_mutate_tools_reject_llm_chat(self):
        """LLM 在 GM 自由响应中不能调跨 save mutate 工具。"""
        registry = get_registry()
        for name in USER_MUTATE_TOOLS:
            spec = registry.get(name)
            self.assertIsNotNone(spec, f"工具 {name} 未注册")
            self.assertNotIn(
                "llm_chat", spec.origins,
                f"安全漏洞: {name} 允许 llm_chat origin (LLM 可在 GM 自由响应里调它)",
            )

    def test_mutate_tools_reject_llm_set(self):
        """玩家 /set 命令不能跨 save mutate (玩家在 save_A 里 /set 不该影响 save_B)。"""
        registry = get_registry()
        for name in USER_MUTATE_TOOLS:
            spec = registry.get(name)
            self.assertIsNotNone(spec, f"工具 {name} 未注册")
            self.assertNotIn(
                "llm_set", spec.origins,
                f"安全漏洞: {name} 允许 llm_set origin (玩家 /set 可跨 save 操作)",
            )

    def test_read_tools_still_allow_llm(self):
        """对照: read 工具允许 LLM,这样 LLM 可以查询自己的资源。"""
        registry = get_registry()
        for name in USER_READ_TOOLS:
            spec = registry.get(name)
            if spec is None:
                continue  # 个别工具可能未注册
            self.assertIn(
                "llm_chat", spec.origins,
                f"read 工具 {name} 应允许 llm_chat (否则 LLM 无法查询)",
            )


class CrossSaveDispatchRejection(unittest.TestCase):
    """实际通过 dispatcher 调用,验证 LLM 跨 save 操作被拒。"""

    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def setUp(self):
        self.dispatcher = ToolDispatcher(
            registry=get_registry(),
            state_provider=lambda env: None,
        )

    def _call_as_llm_chat(self, tool, args, trace_id=None):
        """模拟 LLM 在 GM 自由响应中调工具。"""
        env = ToolCallEnvelope(
            user_id=1, save_id=100, tool=tool, args=args,
            origin="llm_chat", trace_id=trace_id or f"gm-cross-{tool}",
        )
        return self.dispatcher.dispatch_sync(env)

    def test_activate_save_blocked_from_llm_chat(self):
        """GM 试图切到别的 save → 拒绝。"""
        r = self._call_as_llm_chat("activate_save", {"save_id": 999})
        self.assertFalse(r.ok)
        self.assertIn("origin_forbidden", r.error or "")

    def test_rename_save_blocked_from_llm_chat(self):
        r = self._call_as_llm_chat("rename_save", {"save_id": 999, "title": "黑客改名"})
        self.assertFalse(r.ok)
        self.assertIn("origin_forbidden", r.error or "")

    def test_continue_branch_blocked_from_llm_chat(self):
        r = self._call_as_llm_chat("continue_branch", {"save_id": 999, "from_turn": 1})
        self.assertFalse(r.ok)
        self.assertIn("origin_forbidden", r.error or "")

    def test_set_preference_blocked_from_llm_chat(self):
        """LLM 不能改用户偏好 (跨所有 save 影响)。"""
        r = self._call_as_llm_chat("set_preference",
                                    {"key": "gm.model_real_name", "value": "evil-model"})
        self.assertFalse(r.ok)
        self.assertIn("origin_forbidden", r.error or "")

    def test_select_model_blocked_from_llm_chat(self):
        """LLM 不能自己切换模型 (典型自我提权)。"""
        r = self._call_as_llm_chat("select_model",
                                    {"api_id": "anthropic", "model": "claude-jailbreak-7"})
        self.assertFalse(r.ok)
        self.assertIn("origin_forbidden", r.error or "")

    def test_create_persona_blocked_from_llm_chat(self):
        """LLM 不能创建持久 persona (跨所有 save 可见)。"""
        r = self._call_as_llm_chat("create_persona",
                                    {"name": "LLM 自造 persona", "summary": "x"})
        self.assertFalse(r.ok)
        self.assertIn("origin_forbidden", r.error or "")

    def test_start_script_import_blocked_from_llm_chat(self):
        """LLM 不能启动 LLM 调用任务 (触发外部费用)。"""
        r = self._call_as_llm_chat("start_script_import",
                                    {"upload_id": "x", "title": "y"})
        self.assertFalse(r.ok)
        self.assertIn("origin_forbidden", r.error or "")

    def test_delete_script_blocked_from_llm_chat(self):
        r = self._call_as_llm_chat("delete_script", {"script_id": 1})
        self.assertFalse(r.ok)
        self.assertIn("origin_forbidden", r.error or "")


class CrossUserDispatchRejection(unittest.TestCase):
    """同样的 user 级工具,user_A 调时 SQL 强制 user_id 过滤,
    user_A 不能拿/改 user_B 的资源(即便伪造 save_id)。"""

    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def setUp(self):
        self.dispatcher = ToolDispatcher(
            registry=get_registry(),
            state_provider=lambda env: None,
        )

    def test_save_ownership_enforced_in_sql(self):
        """检查 _t_rename_save 实现是否含 user_id 过滤。"""
        src = (REPO / "tools_dsl" / "command_tools_saves.py").read_text(encoding="utf-8")
        # rename / delete / list 都必须含 user_id 过滤
        for fn_marker in (
            "_t_rename_save", "_t_delete_save", "_t_activate_save",
            "_t_list_branches", "_t_delete_branch", "_t_activate_branch",
        ):
            import re as _re
            block = _re.search(
                fn_marker + r".*?(?=^def |\Z)",
                src, _re.DOTALL | _re.MULTILINE,
            )
            self.assertIsNotNone(block, f"找不到 {fn_marker}")
            body = block.group(0)
            self.assertIn(
                "user_id", body,
                f"{fn_marker} 不含 user_id 过滤 — 可能跨用户漏洞",
            )

    def test_persona_card_credentials_ownership_enforced(self):
        src = (REPO / "tools_dsl" / "command_tools_misc.py").read_text(encoding="utf-8")
        for fn_marker in (
            "_t_create_persona", "_t_delete_persona",
            "_t_create_character_card", "_t_delete_character_card",
            "_t_list_my_credentials_meta",
        ):
            import re as _re
            block = _re.search(
                fn_marker + r".*?(?=^def |\Z)",
                src, _re.DOTALL | _re.MULTILINE,
            )
            if block:
                self.assertIn(
                    "user_id", block.group(0),
                    f"{fn_marker} 不含 user_id 过滤",
                )


class CredentialsToolCannotLeakKey(unittest.TestCase):
    """list_my_credentials_meta 即使 LLM 调,也不会返回 key 实际值。"""

    def test_select_excludes_key_encrypted(self):
        src = (REPO / "tools_dsl" / "command_tools_misc.py").read_text(encoding="utf-8")
        import re as _re
        block = _re.search(
            r"_t_list_my_credentials_meta.*?(?=^def |\Z)",
            src, _re.DOTALL | _re.MULTILINE,
        )
        self.assertIsNotNone(block)
        body = block.group(0)
        # SELECT 不能裸 key_encrypted
        # 允许 length(key_encrypted) — 只暴露长度而非内容
        self.assertNotIn("select key_encrypted", body.lower(),
                         "不应裸 SELECT key_encrypted")


class RegistryToolCountStillRespectable(unittest.TestCase):
    """Phase 7 收紧 origins 后,工具总数应保持。"""

    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def test_tool_count_unchanged(self):
        self.assertGreaterEqual(
            len(get_registry().list_all()), 80,
            "Phase 7 不应减少工具,只收紧 origins",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
