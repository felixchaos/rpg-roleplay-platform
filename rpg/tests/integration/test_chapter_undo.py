"""test_chapter_undo — 撤销 AI 对章节的改动(确定性安全网,与落库前预览成对)。

写作 agent 改章节正文 → script_commits 存改前全文(payload.before)→ 作者可一键撤销恢复。
连续撤销逐次往前(undone 标记消费)。手动编辑(无 before)不可撤销 → 不受影响。
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
os.environ.setdefault("RPG_REQUIRE_AUTH", "1")

from tests.helpers import cleanup_test_users, make_client, register_user  # noqa: E402


def _chapter_content(sid: int, ci: int) -> str:
    from platform_app.db import connect
    with connect() as db:
        r = db.execute(
            "select content from script_chapters where script_id=%s and chapter_index=%s", (sid, ci),
        ).fetchone()
    return str((r or {}).get("content") or "")


class ChapterUndoE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def _setup(self):
        u = register_user(self.client)
        uid = int(self.client.get("/api/v1/auth/me", cookies=u["cookies"]).json()["user"]["id"])
        from platform_app.db import connect
        with connect() as db:
            sid = int(db.execute(
                "insert into scripts(owner_id, title) values (%s,%s) returning id",
                (uid, "integtest_undo"),
            ).fetchone()["id"])
            db.execute(
                "insert into script_chapters(script_id, chapter_index, title, content) values (%s,%s,%s,%s)",
                (sid, 1, "开篇", "原始正文 v0"),
            )
        return uid, sid

    def test_undo_restores_prior_content(self):
        from platform_app.api.script_edit import api_undo_chapter_edit
        from tools_dsl.command_tools_script_write import _t_update_script_chapter
        uid, sid = self._setup()

        # agent 改写
        out = _t_update_script_chapter(uid, sid, {"chapter_index": 1, "content": "AI 改写版 v1"}, None)
        self.assertIn("已更新", out)
        self.assertEqual(_chapter_content(sid, 1), "AI 改写版 v1")

        # 撤销 → 恢复 v0
        res = asyncio.run(api_undo_chapter_edit(sid, 1, user={"id": uid}))
        import json as _j
        body = _j.loads(bytes(res.body).decode())
        self.assertTrue(body.get("ok"), body)
        self.assertEqual(_chapter_content(sid, 1), "原始正文 v0", "撤销应恢复改前全文")

    def test_sequential_undo_walks_back(self):
        from platform_app.api.script_edit import api_undo_chapter_edit
        from tools_dsl.command_tools_script_write import _t_update_script_chapter
        uid, sid = self._setup()

        _t_update_script_chapter(uid, sid, {"chapter_index": 1, "content": "v1"}, None)
        _t_update_script_chapter(uid, sid, {"chapter_index": 1, "content": "v2"}, None)
        self.assertEqual(_chapter_content(sid, 1), "v2")

        asyncio.run(api_undo_chapter_edit(sid, 1, user={"id": uid}))  # v2 -> v1
        self.assertEqual(_chapter_content(sid, 1), "v1")
        asyncio.run(api_undo_chapter_edit(sid, 1, user={"id": uid}))  # v1 -> v0
        self.assertEqual(_chapter_content(sid, 1), "原始正文 v0")

    def test_undo_when_nothing_to_undo(self):
        from platform_app.api.script_edit import api_undo_chapter_edit
        uid, sid = self._setup()
        res = asyncio.run(api_undo_chapter_edit(sid, 1, user={"id": uid}))
        import json as _j
        body = _j.loads(bytes(res.body).decode())
        self.assertFalse(body.get("ok"))

    def test_undo_rejects_non_owner(self):
        from platform_app.api.script_edit import api_undo_chapter_edit
        from tools_dsl.command_tools_script_write import _t_update_script_chapter
        uid, sid = self._setup()
        _t_update_script_chapter(uid, sid, {"chapter_index": 1, "content": "v1"}, None)
        # 另一个用户撤销 → 403,正文不变
        other = register_user(self.client)
        ouid = int(self.client.get("/api/v1/auth/me", cookies=other["cookies"]).json()["user"]["id"])
        res = asyncio.run(api_undo_chapter_edit(sid, 1, user={"id": ouid}))
        self.assertEqual(res.status_code, 403)
        self.assertEqual(_chapter_content(sid, 1), "v1", "非 owner 撤销不应改动正文")


if __name__ == "__main__":
    unittest.main()
