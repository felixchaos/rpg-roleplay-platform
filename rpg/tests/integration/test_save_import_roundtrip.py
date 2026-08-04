"""自包含存档 export_save → import_save 无损往返回归(锁住已修的 #78)。

历史 bug(#78「选任意分支均从头开始,并自动创建新分支」):import_save 旧实现丢弃
payload 里的 branch_refs,硬造单个 refs/heads/main → 多分支存档导入后只剩一个 main 头,
选任意非末尾 commit 续写时找不到 ref → 新建 runtime ref + 重置到根 → 「从头开始」。
修复后 import_save 遍历 payload.refs 逐条按 old→new 重映射 target_commit_id 写回。

本测试建一个【双分支头】存档,export_save → import_save,断言:
  - branch_refs 数量保持(=2),分支名保持,is_active 保持;
  - active_commit_id 指向【本存档内】的 commit(非孤儿);
  - commits 数量与 turn_index 保持。
真库 + integtest_ 隔离 + 即清理。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from psycopg.types.json import Jsonb  # noqa: E402

from platform_app import save_io  # noqa: E402
from platform_app.db import connect  # noqa: E402
from platform_app.db.init import init_db  # noqa: E402

_UNAME = "integtest_save_roundtrip"


def _cleanup():
    with connect() as db:
        db.execute("delete from users where username = %s", (_UNAME,))


class SaveImportRoundtripTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        _cleanup()
        with connect() as db:
            cls.uid = int(db.execute(
                "insert into users(username, display_name, email) values (%s,%s,%s) returning id",
                (_UNAME, "rt", _UNAME + "@example.test"),
            ).fetchone()["id"])
            cls.script_id = int(db.execute(
                "insert into scripts(owner_id, title) values (%s,%s) returning id",
                (cls.uid, "roundtrip_script"),
            ).fetchone()["id"])

    @classmethod
    def tearDownClass(cls):
        _cleanup()

    def _seed_two_branch_save(self) -> int:
        with connect() as db:
            save_id = int(db.execute(
                "insert into game_saves(user_id, script_id, title, state_path, active_commit_id) "
                "values (%s,%s,%s,%s,NULL) returning id",
                (self.uid, self.script_id, "rt", "x"),
            ).fetchone()["id"])
            root = int(db.execute(
                "insert into branch_commits(save_id, object_hash, turn_index, kind, title, parent_id, state_snapshot) "
                "values (%s,%s,%s,%s,%s,NULL,%s) returning id",
                (save_id, "h_root", 0, "root", "root", Jsonb({"history": []})),
            ).fetchone()["id"])
            child = int(db.execute(
                "insert into branch_commits(save_id, object_hash, turn_index, kind, title, parent_id, state_snapshot) "
                "values (%s,%s,%s,%s,%s,%s,%s) returning id",
                (save_id, "h_child", 1, "turn", "t1", root, Jsonb({"history": []})),
            ).fetchone()["id"])
            # 两个分支头:main → child(active),branch-b → root(非 active)
            db.execute(
                "insert into branch_refs(save_id, name, kind, target_commit_id, is_active) "
                "values (%s,'refs/heads/main','branch',%s,true)", (save_id, child),
            )
            db.execute(
                "insert into branch_refs(save_id, name, kind, target_commit_id, is_active) "
                "values (%s,'refs/heads/branch-b','branch',%s,false)", (save_id, root),
            )
            db.execute("update game_saves set active_commit_id=%s where id=%s", (child, save_id))
        return save_id

    def test_roundtrip_preserves_branch_refs(self):
        src = self._seed_two_branch_save()
        payload = save_io.export_save(self.uid, src)
        # export 应含两个 ref
        self.assertEqual(len(payload["refs"]), 2, "导出应保留两个分支头")

        result = save_io.import_save(self.uid, payload)
        new_save_id = int(result.get("save_id") or result.get("save", {}).get("id"))
        self.assertNotEqual(new_save_id, src)

        with connect() as db:
            refs = db.execute(
                "select name, is_active, target_commit_id from branch_refs where save_id=%s order by name",
                (new_save_id,),
            ).fetchall()
            commits = db.execute(
                "select turn_index from branch_commits where save_id=%s order by turn_index",
                (new_save_id,),
            ).fetchall()
            active = db.execute(
                "select active_commit_id from game_saves where id=%s", (new_save_id,),
            ).fetchone()["active_commit_id"]
            active_in_save = db.execute(
                "select 1 from branch_commits where id=%s and save_id=%s", (active, new_save_id),
            ).fetchone()

        # #78 核心:两个分支头都保留(没被坍缩成单 main)
        names = sorted(r["name"] for r in refs)
        self.assertEqual(names, ["refs/heads/branch-b", "refs/heads/main"],
                         "导入后分支头丢失 → #78 回归")
        # is_active 保持
        active_names = sorted(r["name"] for r in refs if r["is_active"])
        self.assertEqual(active_names, ["refs/heads/main"])
        # commits 与 turn_index 保持
        self.assertEqual([c["turn_index"] for c in commits], [0, 1])
        # active 指向本存档内 commit(非孤儿,且不是被重置到根)
        self.assertIsNotNone(active_in_save, "active_commit_id 指向孤儿 commit")
        active_target = [r["target_commit_id"] for r in refs if r["name"] == "refs/heads/main"][0]
        self.assertEqual(active, active_target, "active 应指向 main 头(child),而非被重置到根")


if __name__ == "__main__":
    unittest.main()
