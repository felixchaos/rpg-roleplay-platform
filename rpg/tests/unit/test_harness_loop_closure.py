"""
test_harness_loop_closure.py — Phase 2 harness 闭环 + 附件/导入工具测试

覆盖:
  · import_pipeline.wait_for_import_job  — 终态立返 / not_found 立返 / 超时返 timed_out
  · import_pipeline.summarize_job_result — done / done_with_errors / failed / cancelled / timed_out
  · rebuild_script_module — 别名归一 / 未知模块 / llm_chat 被 origin 拦 / console_assistant 成功(闭环)
  · read_attached_text — 无附件 / 成功 + offset 分段
  · import_attached_script — 无附件 / chapters(取消流水线) / full(闭环等结果)

所有 DB / 重活用 unittest.mock 替身,不打真 DB / 不真跑 LLM。
"""
from __future__ import annotations

import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
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
from platform_app import import_pipeline as ip  # noqa: E402

# 拆包后 patch-where-defined:wait_for_import_job 定义在子模块 runner,它内部调的
# get_job_status 要拦截必须 patch runner(打门面拦不到定义模块的全局名)。
# 本文件其余 patch.object(ip, ...)(schedule_module_rebuild/wait_for_import_job/cancel_job)
# 拦的是 tools_dsl 经门面属性的外部调用,维持打门面不变。
from platform_app.import_pipeline import runner as ip_runner  # noqa: E402


def _new_state(turn=3) -> GameState:
    s = GameState(copy.deepcopy(DEFAULT_STATE))
    s.data["turn"] = turn
    return s


# ════════════════════════════════════════════════════════════
# wait_for_import_job
# ════════════════════════════════════════════════════════════


class WaitForImportJob(unittest.TestCase):
    def test_terminal_returns_immediately(self):
        with patch.object(ip_runner, "get_job_status") as gjs:
            gjs.return_value = {"ok": True, "found": True,
                                "job": {"status": "done", "overall_progress": 8, "overall_total": 8}}
            res = ip.wait_for_import_job(1, "job-x", timeout_s=30.0, poll_s=0.01)
        self.assertEqual(res["status"], "done")
        self.assertNotIn("timed_out", res)
        gjs.assert_called_once()

    def test_done_with_errors_is_terminal(self):
        with patch.object(ip_runner, "get_job_status") as gjs:
            gjs.return_value = {"ok": True, "found": True,
                                "job": {"status": "done_with_errors", "warnings": "卡 3 失败"}}
            res = ip.wait_for_import_job(1, "job-x", timeout_s=30.0)
        self.assertEqual(res["status"], "done_with_errors")

    def test_not_found_returns_immediately(self):
        with patch.object(ip_runner, "get_job_status") as gjs:
            gjs.return_value = {"ok": True, "found": False}
            res = ip.wait_for_import_job(1, "ghost", timeout_s=30.0)
        self.assertEqual(res["status"], "not_found")
        self.assertIs(res["found"], False)

    def test_timeout_returns_timed_out(self):
        with patch.object(ip_runner, "get_job_status") as gjs, patch("time.sleep"):
            gjs.return_value = {"ok": True, "found": True, "job": {"status": "running"}}
            res = ip.wait_for_import_job(1, "job-x", timeout_s=0.0, poll_s=0.0)
        self.assertTrue(res.get("timed_out"))
        self.assertEqual(res["status"], "running")


# ════════════════════════════════════════════════════════════
# summarize_job_result
# ════════════════════════════════════════════════════════════


class SummarizeJobResult(unittest.TestCase):
    def test_done_with_counts(self):
        res = {"status": "done", "stages": [
            {"id": "cards", "label": "角色卡", "status": "done", "count": 12},
            {"id": "worldbook", "label": "世界书", "status": "skipped"},
        ]}
        s = ip.summarize_job_result(res, "测试")
        self.assertIn("完成", s)
        self.assertIn("角色卡:12", s)
        self.assertNotIn("世界书", s)  # skipped 不展示

    def test_done_with_errors(self):
        s = ip.summarize_job_result({"status": "done_with_errors", "warnings": "X"}, "测试")
        self.assertIn("部分", s)

    def test_failed(self):
        s = ip.summarize_job_result({"status": "failed", "error": "boom"}, "测试")
        self.assertIn("失败", s)
        self.assertIn("boom", s)

    def test_cancelled_and_timeout_and_notfound(self):
        self.assertIn("取消", ip.summarize_job_result({"status": "cancelled"}, "X"))
        self.assertIn("后台", ip.summarize_job_result({"status": "running", "timed_out": True}, "X"))
        self.assertIn("未找到", ip.summarize_job_result({"status": "not_found"}, "X"))


# ════════════════════════════════════════════════════════════
# rebuild_script_module (user 级, _USER_DEST)
# ════════════════════════════════════════════════════════════


class RebuildScriptModule(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def setUp(self):
        self.dispatcher = ToolDispatcher(
            registry=get_registry(), state_provider=lambda env: None,
        )

    def _call(self, args, origin="console_assistant", user_id=1, save_id=None):
        env = ToolCallEnvelope(
            user_id=user_id, save_id=save_id, tool="rebuild_script_module", args=args,
            origin=origin, trace_id=f"trsm-{origin}",
        )
        return self.dispatcher.dispatch_sync(env)

    def test_validates_script_id(self):
        r = self._call({"script_id": "xyz", "module": "cards"})
        self.assertFalse(r.ok)
        self.assertIn("整数", r.result or "")

    def test_unknown_module(self):
        r = self._call({"script_id": 1, "module": "不存在的模块"})
        self.assertFalse(r.ok)
        self.assertIn("未知模块", r.result or "")

    def test_blocked_from_llm_chat(self):
        # destructive + origins=_USER_DEST(无 llm_chat)→ dispatcher origin_forbidden
        r = self._call({"script_id": 1, "module": "cards"}, origin="llm_chat")
        self.assertFalse(r.ok)
        self.assertIn("origin_forbidden", r.error or "")

    def test_alias_and_success_closes_loop(self):
        with patch.object(ip, "schedule_module_rebuild") as sched, \
             patch.object(ip, "wait_for_import_job") as wait:
            sched.return_value = {"ok": True, "job_id": "rb-1"}
            wait.return_value = {"status": "done", "stages": [
                {"id": "anchors", "label": "时间线", "status": "done", "count": 7}]}
            # 别名 timeline → anchors
            r = self._call({"script_id": 9, "module": "timeline"})
        self.assertTrue(r.ok, r.error or r.result)
        # 归一后的 module 传给 schedule
        self.assertEqual(sched.call_args[0][2], "anchors")
        wait.assert_called_once()
        self.assertIn("时间线:7", r.result)

    def test_ownership_error_surfaces(self):
        with patch.object(ip, "schedule_module_rebuild") as sched:
            sched.side_effect = ValueError("无权访问该剧本")
            r = self._call({"script_id": 9, "module": "cards"})
        self.assertFalse(r.ok)
        self.assertIn("无权访问", r.result or "")


# ════════════════════════════════════════════════════════════
# read_attached_text + import_attached_script (save 级)
# ════════════════════════════════════════════════════════════


class AttachmentTools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def setUp(self):
        self.state = _new_state()
        self.dispatcher = ToolDispatcher(
            registry=get_registry(), state_provider=lambda env: self.state,
        )
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _attach_text(self, name: str, content: str):
        p = self.tmpdir / name
        p.write_text(content, encoding="utf-8")
        self.state.data["_uploaded_files"] = [
            {"name": name, "path": str(p), "type": "text/plain",
             "is_image": False, "size": len(content.encode("utf-8"))}
        ]
        return p

    def _call(self, tool, args, origin="llm_chat"):
        env = ToolCallEnvelope(
            user_id=1, save_id=55, tool=tool, args=args,
            origin=origin, trace_id=f"t-{tool}",
        )
        return self.dispatcher.dispatch_sync(env)

    # ── read_attached_text ──
    def test_read_no_attachment(self):
        r = self._call("read_attached_text", {})
        self.assertFalse(r.ok)
        self.assertIn("没有可读的文本附件", r.result or "")

    def test_read_success_and_offset(self):
        with patch("tools_dsl.command_tools_tavern._resolve_user_id", return_value=1):
            self._attach_text("大纲.txt", "ABCDEFGHIJ" * 10)  # 100 字
            r = self._call("read_attached_text", {})
            self.assertTrue(r.ok, r.error or r.result)
            self.assertIn("大纲.txt", r.result)
            self.assertIn("<untrusted_attachment>", r.result)
            # offset 续读
            r2 = self._call("read_attached_text", {"offset": 50})
            self.assertTrue(r2.ok)
            self.assertIn("50-100", r2.result)

    def test_read_picks_text_not_card(self):
        # 同轮既有卡片又有文本:read 只挑文本
        with patch("tools_dsl.command_tools_tavern._resolve_user_id", return_value=1):
            txt = self.tmpdir / "story.txt"
            txt.write_text("正文内容", encoding="utf-8")
            self.state.data["_uploaded_files"] = [
                {"name": "card.png", "path": str(self.tmpdir / "card.png"),
                 "type": "image/png", "is_image": True, "size": 10},
                {"name": "story.txt", "path": str(txt),
                 "type": "text/plain", "is_image": False, "size": 12},
            ]
            r = self._call("read_attached_text", {})
            self.assertTrue(r.ok, r.error or r.result)
            self.assertIn("story.txt", r.result)
            self.assertIn("正文内容", r.result)

    # ── import_attached_script ──
    def test_import_no_attachment(self):
        with patch("tools_dsl.command_tools_tavern._resolve_user_id", return_value=1):
            r = self._call("import_attached_script", {})
            self.assertFalse(r.ok)
            self.assertIn("没有可导入的文本附件", r.result or "")

    def test_import_chapters_cancels_pipeline(self):
        with patch("tools_dsl.command_tools_tavern._resolve_user_id", return_value=1), \
             patch("platform_app.script_import.import_script") as imp, \
             patch.object(ip, "cancel_job") as cancel:
            imp.return_value = {
                "script": {"id": 77, "chapter_count": 25},
                "knowledge": {"ok": True, "job_id": "fp-1", "kind": "full_pipeline"},
            }
            self._attach_text("剧本.txt", "第一章\n内容\n第二章\n内容")
            r = self._call("import_attached_script", {"scope": "chapters"})
            self.assertTrue(r.ok, r.error or r.result)
            self.assertIn("script_id=77", r.result)
            self.assertIn("25 章", r.result)
            self.assertIn("仅章节拆分", r.result)
            cancel.assert_called_once()  # chapters → 取消自动起的流水线
            # 用掉上传
            self.assertNotIn("_uploaded_files", self.state.data)

    def test_import_full_waits_for_result(self):
        with patch("tools_dsl.command_tools_tavern._resolve_user_id", return_value=1), \
             patch("platform_app.script_import.import_script") as imp, \
             patch.object(ip, "wait_for_import_job") as wait:
            imp.return_value = {
                "script": {"id": 88, "chapter_count": 12},
                "knowledge": {"ok": True, "job_id": "fp-2", "kind": "full_pipeline"},
            }
            wait.return_value = {"status": "done", "stages": [
                {"id": "cards", "label": "角色卡", "status": "done", "count": 9}]}
            self._attach_text("剧本2.txt", "正文")
            r = self._call("import_attached_script", {"scope": "full"})
            self.assertTrue(r.ok, r.error or r.result)
            self.assertIn("script_id=88", r.result)
            wait.assert_called_once()
            self.assertIn("角色卡:9", r.result)

    def test_import_full_zero_llm_fallback(self):
        # kind=knowledge_sync(无 BYOK 回退)→ 不等 import_jobs,给回退说明
        with patch("tools_dsl.command_tools_tavern._resolve_user_id", return_value=1), \
             patch("platform_app.script_import.import_script") as imp, \
             patch.object(ip, "wait_for_import_job") as wait:
            imp.return_value = {
                "script": {"id": 99, "chapter_count": 5},
                "knowledge": {"ok": True, "job_id": "ks-1", "kind": "knowledge_sync"},
            }
            self._attach_text("剧本3.txt", "正文")
            r = self._call("import_attached_script", {"scope": "full"})
            self.assertTrue(r.ok, r.error or r.result)
            self.assertIn("零 LLM 回退", r.result)
            wait.assert_not_called()


if __name__ == "__main__":
    unittest.main()
