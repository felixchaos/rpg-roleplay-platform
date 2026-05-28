"""
test_command_tools_misc.py — task 87 misc/phase4 工具测试

覆盖范围:
  · set_permission_mode / inject_pending_question (save 级)
  · set_preference / create/delete_persona / create/delete_character_card (user 级 DB)
  · mcp_server_* / select_model (admin)
  · start_script_import / get_import_status / cancel_import_job /
    resplit_script / delete_script / probe_models (Phase 4 异步)
  · get_save_detail / get_my_stats / get_chapter_facts / get_worldbook
    / list_my_credentials_meta (B 类补全)

所有 DB 操作用 unittest.mock 替身,不打真 DB。
"""
from __future__ import annotations

import copy
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("RPG_REQUIRE_AUTH", "0")

from state import DEFAULT_STATE, GameState  # noqa: E402
from tools_dsl.command_dispatcher import (  # noqa: E402
    ToolCallEnvelope,
    ToolDispatcher,
    get_registry,
)
from tools_dsl.command_tools_register import force_reset_for_tests  # noqa: E402


def _new_state(turn=3) -> GameState:
    s = GameState(copy.deepcopy(DEFAULT_STATE))
    s.data["turn"] = turn
    return s


# ────────────────────────────────────────────────────────────
# save 级
# ────────────────────────────────────────────────────────────


class SaveLevelMiscTools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def setUp(self):
        self.state = _new_state()
        self.dispatcher = ToolDispatcher(
            registry=get_registry(),
            state_provider=lambda env: self.state,
        )

    def _call(self, tool, args, origin="ui_button", trace_id=None):
        env = ToolCallEnvelope(
            user_id=1, save_id=100, tool=tool, args=args,
            origin=origin, trace_id=trace_id or f"t-{tool}",
        )
        return self.dispatcher.dispatch_sync(env)

    def test_set_permission_mode_full_access(self):
        r = self._call("set_permission_mode", {"mode": "full_access"})
        self.assertTrue(r.ok, r.error)
        self.assertEqual(self.state.data["permissions"]["mode"], "full_access")

    def test_set_permission_mode_invalid_mode(self):
        r = self._call("set_permission_mode", {"mode": "godmode"})
        self.assertFalse(r.ok)
        self.assertIn("非法", r.result or "")

    def test_set_permission_mode_blocked_from_llm(self):
        """敏感工具,llm_set / llm_chat 都不允许 (只 ui_button + api_direct)。"""
        for origin in ("llm_set", "llm_chat"):
            r = self._call("set_permission_mode",
                           {"mode": "full_access"},
                           origin=origin, trace_id=f"tspm-{origin}")
            self.assertFalse(r.ok, f"{origin} 应被拒")
            self.assertIn("origin_forbidden", r.error or "")

    def test_inject_pending_question(self):
        r = self._call("inject_pending_question",
                       {"question": "选 A 还是 B?", "options": ["A", "B"]})
        self.assertTrue(r.ok)
        qs = self.state.data["permissions"]["pending_questions"]
        self.assertEqual(len(qs), 1)
        self.assertEqual(qs[0]["question"], "选 A 还是 B?")


# ────────────────────────────────────────────────────────────
# user 级 (mock DB)
# ────────────────────────────────────────────────────────────


class UserLevelMiscTools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def setUp(self):
        self.dispatcher = ToolDispatcher(
            registry=get_registry(),
            state_provider=lambda env: None,
        )

    def _call(self, tool, args, origin="ui_button", trace_id=None, user_id=1):
        env = ToolCallEnvelope(
            user_id=user_id, save_id=None, tool=tool, args=args,
            origin=origin, trace_id=trace_id or f"t-{tool}",
        )
        return self.dispatcher.dispatch_sync(env)

    def test_set_preference_handles_db_failure(self):
        with patch("platform_app.db.connect") as conn:
            conn.side_effect = Exception("DB 不可用")
            r = self._call("set_preference", {"key": "theme", "value": "dark"})
            self.assertFalse(r.ok)
            self.assertIn("失败", r.result or "")

    def test_create_persona_validates_name(self):
        r = self._call("create_persona", {"name": ""})
        self.assertFalse(r.ok)
        self.assertIn("name 为空", r.result or "")

    def test_delete_persona_validates_id(self):
        r = self._call("delete_persona", {"persona_id": "abc"})
        self.assertFalse(r.ok)
        self.assertIn("整数", r.result or "")

    def test_delete_persona_blocked_from_llm(self):
        for origin in ("llm_chat", "llm_set"):
            r = self._call("delete_persona", {"persona_id": 1},
                           origin=origin, trace_id=f"tdp-{origin}")
            self.assertFalse(r.ok)
            self.assertIn("origin_forbidden", r.error or "")

    def test_create_character_card_validates_name(self):
        r = self._call("create_character_card", {"name": ""})
        self.assertFalse(r.ok)
        self.assertIn("name 为空", r.result or "")

    def test_select_model_validates_args(self):
        r = self._call("select_model", {"api_id": "", "model": ""})
        self.assertFalse(r.ok)
        self.assertIn("不能为空", r.result or "")

    def test_mcp_server_start_validates(self):
        r = self._call("mcp_server_start", {"server_id": ""})
        self.assertFalse(r.ok)
        self.assertIn("server_id 为空", r.result or "")

    def test_mcp_server_start_only_admin_origin(self):
        for origin in ("llm_chat", "llm_set"):
            r = self._call("mcp_server_start", {"server_id": "x"},
                           origin=origin, trace_id=f"tmss-{origin}")
            self.assertFalse(r.ok)
            self.assertIn("origin_forbidden", r.error or "")


# ────────────────────────────────────────────────────────────
# Phase 4 异步包装
# ────────────────────────────────────────────────────────────


class AsyncJobToolsValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def setUp(self):
        self.dispatcher = ToolDispatcher(
            registry=get_registry(),
            state_provider=lambda env: None,
        )

    def _call(self, tool, args, origin="ui_button", trace_id=None, user_id=1):
        env = ToolCallEnvelope(
            user_id=user_id, save_id=None, tool=tool, args=args,
            origin=origin, trace_id=trace_id or f"t-{tool}",
        )
        return self.dispatcher.dispatch_sync(env)

    def test_start_script_import_validates_args(self):
        r = self._call("start_script_import", {"upload_id": "abc"})
        self.assertFalse(r.ok)
        self.assertIn("必填", r.result or "")

    def test_start_script_import_calls_module(self):
        with patch("platform_app.script_import.import_script") as imp:
            imp.return_value = {"script_id": 42, "job_id": "job-99"}
            r = self._call("start_script_import",
                           {"upload_id": "up1", "title": "测试剧本"},
                           trace_id="tssi-ok")
            self.assertTrue(r.ok, r.error or r.result)
            self.assertIn("script_id=42", r.result)
            self.assertIn("job-99", r.result)

    def test_cancel_import_job_validates(self):
        r = self._call("cancel_import_job", {"job_id": ""})
        self.assertFalse(r.ok)
        self.assertIn("job_id 为空", r.result or "")

    def test_resplit_script_destructive_blocked_from_llm(self):
        for origin in ("llm_chat", "llm_set"):
            r = self._call("resplit_script",
                           {"script_id": 1, "mode": "regex"},
                           origin=origin, trace_id=f"trs-{origin}")
            self.assertFalse(r.ok)
            self.assertIn("origin_forbidden", r.error or "")

    def test_delete_script_destructive_blocked_from_llm(self):
        r = self._call("delete_script", {"script_id": 1},
                       origin="llm_chat", trace_id="tds-llm")
        self.assertFalse(r.ok)
        self.assertIn("origin_forbidden", r.error or "")

    def test_probe_models_handles_missing_module(self):
        with patch("model_probe.probe", create=True) as probe:
            probe.return_value = {"apis": [{"id": "vertex_ai", "ok": True}]}
            r = self._call("probe_models", {}, trace_id="tpm-ok")
            self.assertTrue(r.ok, r.error or r.result)

    def test_get_import_status_validates_script_id(self):
        r = self._call("get_import_status", {"script_id": "xyz"})
        self.assertFalse(r.ok)
        self.assertIn("整数", r.result or "")


# ────────────────────────────────────────────────────────────
# B 类查询补全
# ────────────────────────────────────────────────────────────


class QueryToolsCompletion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def setUp(self):
        self.state = _new_state()
        self.dispatcher = ToolDispatcher(
            registry=get_registry(),
            state_provider=lambda env: self.state,
        )

    def test_get_save_detail_handles_db_failure(self):
        with patch("platform_app.db.connect") as conn:
            conn.side_effect = Exception("DB 不可用")
            env = ToolCallEnvelope(
                user_id=1, save_id=None, tool="get_save_detail",
                args={"save_id": 5}, origin="ui_button",
                trace_id="tgsd-fail",
            )
            r = self.dispatcher.dispatch_sync(env)
            self.assertFalse(r.ok)

    def test_get_chapter_facts_requires_script(self):
        env = ToolCallEnvelope(
            user_id=1, save_id=100, script_id=None, tool="get_chapter_facts",
            args={}, origin="llm_chat", trace_id="tgcf-fail",
        )
        r = self.dispatcher.dispatch_sync(env)
        self.assertFalse(r.ok)
        # scope_missing_script 或 失败提示
        self.assertTrue(
            "scope_missing_script" in (r.error or "") or "失败" in (r.result or ""),
            f"应拒;实际 {r}",
        )

    def test_list_my_credentials_meta_never_returns_keys(self):
        """安全: 此工具的实现里 SELECT 列表必须只含 provider/key_len/updated_at,
        永不 SELECT key_encrypted 实际值。"""
        src = Path(__file__).resolve().parents[1] / "tools_dsl" / "command_tools_misc.py"
        content = src.read_text(encoding="utf-8")
        # 搜 _t_list_my_credentials_meta 函数体
        import re
        match = re.search(
            r"def _t_list_my_credentials_meta.*?(?=^def |\Z)",
            content, re.DOTALL | re.MULTILINE,
        )
        self.assertIsNotNone(match)
        body = match.group(0)
        # SELECT 不应出现裸 key_encrypted (只允许 length(key_encrypted))
        self.assertNotIn("select key_encrypted", body.lower())
        self.assertIn("length(key_encrypted)", body)


# ────────────────────────────────────────────────────────────
# 注册总数 / 安全不变量
# ────────────────────────────────────────────────────────────


class ToolTableSanity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def test_total_count_at_least_80(self):
        self.assertGreaterEqual(len(get_registry().list_all()), 80)

    def test_destructive_count_meaningful(self):
        dest = [t for t in get_registry().list_all() if t.destructive]
        # 至少 10 个 destructive 工具 (delete_*, remove_*, resplit_*, set_player_*)
        self.assertGreaterEqual(len(dest), 10)

    def test_no_destructive_tool_allows_llm_chat(self):
        """重要安全不变量: 任何 destructive 工具的 origins 不可含 llm_chat。"""
        for t in get_registry().list_all():
            if t.destructive:
                self.assertNotIn(
                    "llm_chat", t.origins,
                    f"{t.name} destructive 工具不应允许 llm_chat (origins={sorted(t.origins)})",
                )

    def test_admin_tools_only_admin_origin(self):
        """mcp_server_* / set_permission_mode 等敏感工具,
        origins 不应含 llm_chat 或 llm_set。"""
        sensitive_names = {
            "set_permission_mode", "inject_pending_question",
            "mcp_server_enable", "mcp_server_start", "mcp_server_stop",
        }
        for t in get_registry().list_all():
            if t.name in sensitive_names:
                for forbidden in ("llm_chat", "llm_set"):
                    self.assertNotIn(
                        forbidden, t.origins,
                        f"{t.name} 不应允许 {forbidden}",
                    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
