"""
test_message_edit_single_occurrence.py
======================================

群反馈(白玖):对话框想加个修改编辑功能 —— 「有时候只是想加一句话,要复制删除粘贴太麻烦」。
前端此前把编辑按钮限死在 role === "assistant",玩家发言改不了。

放开玩家发言编辑之前必须先修后端一个隐患:`_amend_history_message` 定位到目标后,
是按【内容相等】把各存储里所有同文本消息**全部**改掉的:

    for m in snap["history"]:
        if m.get("content") == original:      # ← 全量
            m["content"] = new_content
    update messages set content=%s where save_id=%s and role=%s and content=%s   # ← 全量

GM 正文动辄几百字、同一存档内天然唯一,所以一直没炸;但玩家输入大量重复
(「继续」「嗯」「好」),改其中一条会把整局所有同文本的玩家发言一起改写 —— 玩家消息
编辑一旦放开,这就是确定性的数据损坏。

不变量(行为级):
- 同一存档里存在 N 条完全相同的玩家发言时,编辑第 k 条**只改第 k 条**,其余原样。
- commit 快照 / 工作树快照 / messages 表三处都遵守单点替换。
- GM 正文(全文唯一)的既有行为零变化。
"""
from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch

from routes.game.saves import _amend_history_message


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeDb:
    """只认 _amend_history_message 实际发出的那几条 SQL,按子串分派。"""

    SAVE_ID = 1
    COMMIT_ID = 5

    def __init__(self, history, msgs):
        self.commit_snapshot = {"history": copy.deepcopy(history)}
        self.worktree_snapshot = {"history": copy.deepcopy(history)}
        self.msgs = copy.deepcopy(msgs)

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        if s.startswith("select active_commit_id from game_saves"):
            return _Result([{"active_commit_id": self.COMMIT_ID}])
        if s.startswith("select state_snapshot from branch_commits"):
            return _Result([{"state_snapshot": json.dumps(self.commit_snapshot, ensure_ascii=False)}])
        if s.startswith("update branch_commits set state_snapshot"):
            self.commit_snapshot = copy.deepcopy(params[0].obj)
            return _Result([])
        if s.startswith("select role, content from messages"):
            return _Result([{"role": m["role"], "content": m["content"]} for m in self.msgs])
        if s.startswith("select id, state_snapshot from game_saves"):
            return _Result([{"id": self.SAVE_ID,
                             "state_snapshot": json.dumps(self.worktree_snapshot, ensure_ascii=False)}])
        if s.startswith("select id, state_snapshot from runtime_checkouts"):
            return _Result([])  # 本用例只验 game_saves 工作树这一路
        if s.startswith("update game_saves set state_snapshot"):
            self.worktree_snapshot = copy.deepcopy(params[0].obj)
            return _Result([])
        if s.startswith("select id from messages"):
            if "and role = %s" in s:
                _sid, _role, _content = params
                rows = [m for m in self.msgs if m["role"] == _role and m["content"] == _content]
            else:
                _sid, _content = params
                rows = [m for m in self.msgs if m["content"] == _content]
            rows = sorted(rows, key=lambda m: (m["turn"], m["id"]))
            return _Result([{"id": m["id"]} for m in rows])
        if s.startswith("update messages set content = %s where id = %s"):
            new_content, mid = params
            for m in self.msgs:
                if m["id"] == mid:
                    m["content"] = new_content
            return _Result([])
        # 旧的批量 UPDATE 形态(where content = ...)。这里**如实照做**(全量替换),
        # 目的是让本文件的断言去抓「内容被连坐改写」这个行为,而不是靠 SQL 文本不认识就报错 ——
        # 否则改写一下 SQL 措辞就能骗过守卫。
        if s.startswith("update messages set content = %s where save_id = %s"):
            if "and role = %s" in s:
                new_content, _sid, _role, _content = params
                hit = [m for m in self.msgs if m["role"] == _role and m["content"] == _content]
            else:
                new_content, _sid, _content = params
                hit = [m for m in self.msgs if m["content"] == _content]
            for m in hit:
                m["content"] = new_content
            return _Result([])
        raise AssertionError(f"未预期的 SQL: {s}")

    def commit(self):
        pass


def _mk(history):
    """history(展示序,全部非空)→ messages 行。"""
    return [{"id": i + 1, "turn": i, "role": h["role"], "content": h["content"]}
            for i, h in enumerate(history)]


# 玩家重复发言 4 次「继续」,中间穿插 GM 正文
_DUP_HISTORY = [
    {"role": "user", "content": "继续"},
    {"role": "assistant", "content": "GM 第一段叙述,内容独一无二。"},
    {"role": "user", "content": "继续"},
    {"role": "assistant", "content": "GM 第二段叙述,内容也独一无二。"},
    {"role": "user", "content": "继续"},
    {"role": "assistant", "content": "GM 第三段叙述。"},
    {"role": "user", "content": "继续"},
]


class DuplicatePlayerMessagesAmendedIndividually(unittest.TestCase):
    def _amend(self, history, idx, new_content):
        db = FakeDb(history, _mk(history))
        with patch("platform_app.branches.commits._state_snapshot_hash", return_value="h"):
            ok, original = _amend_history_message(db, FakeDb.SAVE_ID, idx, new_content)
        return db, ok, original

    def test_editing_third_duplicate_only_changes_that_one(self):
        # 展示序 index 4 = 第 3 条「继续」
        db, ok, original = self._amend(_DUP_HISTORY, 4, "继续,但这次我先看看四周")
        self.assertTrue(ok)
        self.assertEqual(original, "继续")

        got = [m["content"] for m in db.commit_snapshot["history"]]
        self.assertEqual(got[4], "继续,但这次我先看看四周", "目标那条没改到")
        for i in (0, 2, 6):
            self.assertEqual(got[i], "继续",
                             f"展示序 {i} 的重复玩家发言被连坐改写(旧的全量替换 bug)")

    def test_messages_table_also_single_row(self):
        db, ok, _ = self._amend(_DUP_HISTORY, 4, "改过的第三条")
        self.assertTrue(ok)
        contents = [m["content"] for m in db.msgs]
        self.assertEqual(contents[4], "改过的第三条")
        self.assertEqual([contents[0], contents[2], contents[6]], ["继续", "继续", "继续"],
                         "messages 表里其余同文本行被连坐 UPDATE")

    def test_worktree_snapshot_also_single_row(self):
        db, ok, _ = self._amend(_DUP_HISTORY, 0, "第一条改了")
        self.assertTrue(ok)
        got = [m["content"] for m in db.worktree_snapshot["history"]]
        self.assertEqual(got[0], "第一条改了")
        self.assertEqual([got[2], got[4], got[6]], ["继续", "继续", "继续"],
                         "工作树快照里其余同文本行被连坐改写")

    def test_editing_last_duplicate(self):
        db, ok, _ = self._amend(_DUP_HISTORY, 6, "最后一条改了")
        self.assertTrue(ok)
        got = [m["content"] for m in db.commit_snapshot["history"]]
        self.assertEqual(got[6], "最后一条改了")
        self.assertEqual([got[0], got[2], got[4]], ["继续", "继续", "继续"])

    def test_gm_narration_unique_text_unchanged_behaviour(self):
        # GM 正文本来就唯一,行为与修复前一致:改中间那段,其它段不动
        db, ok, original = self._amend(_DUP_HISTORY, 3, "GM 第二段被改写了。")
        self.assertTrue(ok)
        self.assertEqual(original, "GM 第二段叙述,内容也独一无二。")
        got = [m["content"] for m in db.commit_snapshot["history"]]
        self.assertEqual(got[3], "GM 第二段被改写了。")
        self.assertEqual(got[1], "GM 第一段叙述,内容独一无二。")
        self.assertEqual(got[5], "GM 第三段叙述。")

    def test_out_of_range_index_returns_false(self):
        db, ok, original = self._amend(_DUP_HISTORY, 99, "x")
        self.assertFalse(ok)
        self.assertIsNone(original)

    def test_noop_edit_returns_ok_without_touching_stores(self):
        db, ok, original = self._amend(_DUP_HISTORY, 4, "继续")
        self.assertTrue(ok)
        self.assertEqual(original, "继续")
        self.assertEqual([m["content"] for m in db.msgs],
                         [h["content"] for h in _DUP_HISTORY], "内容没变时不应写任何存储")


class RequireRoleStillHonoured(unittest.TestCase):
    """acceptance 换稿走 require_role='assistant',放开玩家编辑不能松掉这道闸。"""

    def test_require_role_mismatch_refuses(self):
        db = FakeDb(_DUP_HISTORY, _mk(_DUP_HISTORY))
        with patch("platform_app.branches.commits._state_snapshot_hash", return_value="h"):
            ok, original = _amend_history_message(db, FakeDb.SAVE_ID, 0, "x",
                                                  require_role="assistant")
        self.assertFalse(ok, "index 0 是玩家发言,require_role='assistant' 应拒绝")
        self.assertIsNone(original)


if __name__ == "__main__":
    unittest.main()
