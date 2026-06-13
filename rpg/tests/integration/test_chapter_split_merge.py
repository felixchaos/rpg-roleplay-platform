"""
test_chapter_split_merge.py — 章节 split / merge 的唯一约束回归

生产真因:split_chapter / merge_chapters 用单条 `chapter_index = chapter_index ± 1`
位移后续章节。script_chapters 的 (script_id, chapter_index) 是非 deferrable 唯一约束,
Postgres 逐行即时校验,按非确定顺序处理时会瞬时撞键 → 未捕获 500 UniqueViolation
(journalctl: split_chapter ... duplicate key ... script_chapters_script_id_chapter_index_key)。

修复:负区两段式位移(先把后续章挪到负数空间,腾位后再翻正),并加 per-script
advisory lock 串行化并发编辑。本测覆盖最易撞键的 case:split index 1(令 2→3 撞既存 3)。
"""
from __future__ import annotations

import unittest

from tests.helpers import cleanup_test_users, make_client, register_user


class ChapterSplitMergeUnique(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()
        u = register_user(cls.client)
        from platform_app.db import connect
        with connect() as db:
            cls.owner_id = int(db.execute(
                "select id from users where username = %s", (u["username"],),
            ).fetchone()["id"])
            cls.script_id = int(db.execute(
                "insert into scripts(owner_id, title) values (%s, %s) returning id",
                (cls.owner_id, "chapter_split_test"),
            ).fetchone()["id"])
            for i in range(5):
                db.execute(
                    "insert into script_chapters(script_id, chapter_index, title, content) "
                    "values (%s, %s, %s, %s)",
                    (cls.script_id, i, f"ch{i}", f"C{i}-" + "x" * 10),
                )

    @classmethod
    def tearDownClass(cls):
        from platform_app.db import connect
        with connect() as db:
            db.execute("delete from script_chapters where script_id = %s", (cls.script_id,))
            db.execute("delete from scripts where id = %s", (cls.script_id,))
        cleanup_test_users()

    def _indices(self):
        from platform_app.db import connect
        with connect() as db:
            return [(int(r["chapter_index"]), r["content"]) for r in db.execute(
                "select chapter_index, content from script_chapters where script_id = %s "
                "order by chapter_index", (self.script_id,),
            ).fetchall()]

    def test_split_then_merge_keeps_indices_contiguous(self):
        from platform_app import script_import as si

        # split index 1 @ 3 chars:旧实现位移 +1 时 2→3 会撞既存 3 → UniqueViolation。
        si.split_chapter(self.owner_id, self.script_id, 1, split_at=3, new_title="ch1-right")
        rows = self._indices()
        self.assertEqual([i for i, _ in rows], [0, 1, 2, 3, 4, 5])
        self.assertEqual(rows[1][1], "C1-")               # 原章左半
        self.assertEqual(rows[2][1], "x" * 10)            # 新插入右半
        self.assertTrue(rows[3][1].startswith("C2-"))     # 后续章正确后移

        # merge 1+2 还原:位移 -1 同样不得撞键。
        si.merge_chapters(self.owner_id, self.script_id, 1, separator="")
        rows = self._indices()
        self.assertEqual([i for i, _ in rows], [0, 1, 2, 3, 4])
        self.assertEqual(rows[1][1], "C1-" + "x" * 10)    # 合并后内容拼回

    def test_merge_heals_index_gap(self):
        """用户反馈:有序章 / 序号有缝隙的剧本合并不了。旧实现假设第二章 = first_index+1,
        缝隙(如 1,2,4,5)时找不到章 → 报「需要章节 X 和 X+1 都存在」。
        新实现取按序的下一章 + 重排连续。"""
        from platform_app import script_import as si
        from platform_app.db import connect

        with connect() as db:
            sid = int(db.execute(
                "insert into scripts(owner_id, title) values (%s, %s) returning id",
                (self.owner_id, "chapter_gap_test"),
            ).fetchone()["id"])
            for ci, ttl in [(1, "序章"), (2, "第一章"), (4, "第二章"), (5, "第三章")]:
                db.execute(
                    "insert into script_chapters(script_id, chapter_index, title, content) "
                    "values (%s, %s, %s, %s)", (sid, ci, ttl, "x" * 10),
                )
        try:
            # 前端传 first_index=2, second_index=4(显示相邻的两章,实际序号不相邻)
            si.merge_chapters(self.owner_id, sid, 2, second_index=4, separator="")
            with connect() as db:
                got = [int(r["chapter_index"]) for r in db.execute(
                    "select chapter_index from script_chapters where script_id = %s "
                    "order by chapter_index", (sid,),
                ).fetchall()]
            self.assertEqual(got, [1, 2, 3])  # 4 章合 1 对 → 3 章,且缝隙自愈为连续
        finally:
            with connect() as db:
                db.execute("delete from script_chapters where script_id = %s", (sid,))
                db.execute("delete from scripts where id = %s", (sid,))


if __name__ == "__main__":
    unittest.main()
