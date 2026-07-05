"""「没绑定 embed model」告警降频(2026-07-05 生产实证)。

背景:剧本未绑定 embed_api_id/embed_model 时,_search._embed_query 每次检索(每 query
一次)都会命中 fallback 分支打一条 WARNING → 同一剧本反复检索时刷屏。该剧本的绑定状态
在重新拆书前不会变化,第一次告警已经把「需要重新拆书」的信息传达到位。

覆盖:
  · 同一 (script_id, 进程) 第一次 WARNING、第二次起降为 debug
  · 不同 script_id 各自独立(互不影响告警状态)
  · 绑定成功后(embed_script 写回 meta)会清掉降频标记,后续若又变回未绑定能重新告警一次
  · 零真实 DB / 网络:db 用假对象注入
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("RPG_REQUIRE_AUTH", "0")
os.environ.setdefault("EMBED_MODEL", "text-embedding-004")
os.environ.setdefault("EMBED_API_ID", "vertex")

from platform_app.knowledge import _search  # noqa: E402


class _FakeUnboundResult:
    """scripts 行查询：embed_api_id/embed_model 均为空 → 未绑定。"""
    def fetchone(self):
        return {"embed_api_id": "", "embed_model": ""}


class _FakeUnboundDB:
    def execute(self, sql, params=None):
        return _FakeUnboundResult()


class EmbedQueryUnboundWarningThrottle(unittest.TestCase):
    def setUp(self):
        _search._UNBOUND_EMBED_WARNED.clear()
        _search._SCRIPT_EMBED_META_CACHE.clear()

    def tearDown(self):
        _search._UNBOUND_EMBED_WARNED.clear()
        _search._SCRIPT_EMBED_META_CACHE.clear()

    def _call_embed_query_noop(self, script_id: int, db) -> None:
        """只触发未绑定分支的日志路径,不关心向量结果(embed_query 本身 mock 掉)。"""
        with patch("platform_app.knowledge.embedding.embed_query", return_value=None):
            _search._embed_query("你好", script_id=script_id, user_id=None, db=db)

    def test_first_call_warns_second_call_debug(self):
        db = _FakeUnboundDB()
        with self.assertLogs("platform_app.knowledge._search", level="DEBUG") as cm:
            self._call_embed_query_noop(script_id=42, db=db)
            self._call_embed_query_noop(script_id=42, db=db)

        warn_lines = [l for l in cm.output if l.startswith("WARNING") and "没绑定 embed model" in l]
        debug_lines = [l for l in cm.output if l.startswith("DEBUG") and "没绑定 embed model" in l]
        self.assertEqual(len(warn_lines), 1, f"应仅一次 WARNING,实际: {cm.output}")
        self.assertEqual(len(debug_lines), 1, f"第二次应降为 DEBUG,实际: {cm.output}")

    def test_third_call_still_debug_not_warning_again(self):
        db = _FakeUnboundDB()
        with self.assertLogs("platform_app.knowledge._search", level="DEBUG") as cm:
            for _ in range(5):
                self._call_embed_query_noop(script_id=7, db=db)
        warn_lines = [l for l in cm.output if l.startswith("WARNING")]
        debug_lines = [l for l in cm.output if l.startswith("DEBUG")]
        self.assertEqual(len(warn_lines), 1)
        self.assertEqual(len(debug_lines), 4)

    def test_different_scripts_warn_independently(self):
        db = _FakeUnboundDB()
        with self.assertLogs("platform_app.knowledge._search", level="DEBUG") as cm:
            self._call_embed_query_noop(script_id=1, db=db)
            self._call_embed_query_noop(script_id=2, db=db)
        warn_lines = [l for l in cm.output if l.startswith("WARNING")]
        # 两个不同 script_id,各自第一次都应是 WARNING
        self.assertEqual(len(warn_lines), 2)

    def test_rebind_clears_warned_flag_allows_rewarn(self):
        """embed 完成绑定后应清掉降频标记(embedding._embed_chunks_loop_inner 里的
        _UNBOUND_EMBED_WARNED.discard(script_id))。这里直接单测该清除动作本身:
        标记后 discard,再触发一次应重新 WARNING。"""
        db = _FakeUnboundDB()
        with self.assertLogs("platform_app.knowledge._search", level="DEBUG") as cm:
            self._call_embed_query_noop(script_id=99, db=db)  # 第一次 warning
        self.assertTrue(any(l.startswith("WARNING") for l in cm.output))
        self.assertIn(99, _search._UNBOUND_EMBED_WARNED)

        # 模拟重新绑定成功后的清理动作
        _search._UNBOUND_EMBED_WARNED.discard(99)

        with self.assertLogs("platform_app.knowledge._search", level="DEBUG") as cm2:
            self._call_embed_query_noop(script_id=99, db=db)  # 清除后应重新 warning 一次
        warn_lines = [l for l in cm2.output if l.startswith("WARNING")]
        self.assertEqual(len(warn_lines), 1)


if __name__ == "__main__":
    unittest.main()
