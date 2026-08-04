"""test_episodic_recall — 永恒记忆·情景召回的分支隔离 + 向量检索(真库 + pgvector)。

钉死最关键的正确性:retrieve_episodic 沿当前分支谱系召回,**兄弟分支的事件绝不串味**
(rewind / 平行线隔离),且按向量相似度排序。embedder 用 monkeypatch 注固定查询向量,
不依赖真 embedder。
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
os.environ.setdefault("RPG_REQUIRE_AUTH", "1")

from tests.helpers import cleanup_test_users, make_client, register_user  # noqa: E402


def _vec(lead: float) -> str:
    """768 维 pgvector 字面量:首维=lead,其余 0(余弦相似度由首维主导,便于构造排序)。"""
    dims = [0.0] * 768
    dims[0] = lead
    return "[" + ",".join(str(x) for x in dims) + "]"


def _mk_commit(db, save_id: int, parent_id, msg: str) -> int:
    import uuid
    h = uuid.uuid4().hex
    row = db.execute(
        """insert into branch_commits
           (save_id, parent_id, object_hash, tree_hash, turn_index, kind, title, message,
            summary, content_preview, state_path, player_input, gm_output, metadata,
            created_at, public_id, row_version, state_snapshot)
           values (%s,%s,%s,%s,0,'turn','',%s,'','','','','', '{}'::jsonb,
                   now(), %s, 0, '{}'::jsonb)
           returning id""",
        (save_id, parent_id, h, h, msg, str(uuid.uuid4())),
    ).fetchone()
    return int(row["id"])


def _mk_event(db, save_id: int, born: int, key: str, summary: str, vec: str):
    db.execute(
        """insert into kb_events(save_id, born_commit, logical_key, summary, embedding_vec)
           values (%s,%s,%s,%s,%s::vector)""",
        (save_id, born, key, summary, vec),
    )


class EpisodicRecallBranchIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def _setup_save(self):
        from platform_app.db import connect
        u = register_user(self.client)
        uid = int(self.client.get("/api/v1/auth/me", cookies=u["cookies"]).json()["user"]["id"])
        with connect() as db:
            sid = int(db.execute(
                "insert into scripts(owner_id, title) values (%s,%s) returning id",
                (uid, "integtest_episodic"),
            ).fetchone()["id"])
            save_id = int(db.execute(
                "insert into game_saves(user_id, script_id, title, state_path) values (%s,%s,%s,%s) returning id",
                (uid, sid, "epi save", ""),
            ).fetchone()["id"])
            root = _mk_commit(db, save_id, None, "root")
            branch_b = _mk_commit(db, save_id, root, "B 分支")
            branch_c = _mk_commit(db, save_id, root, "C 分支")  # 与 B 同父 = 兄弟
            # 事件:共同祖先 root 一条,B 分支一条,C 分支一条
            _mk_event(db, save_id, root, "e_common", "在站台第一次遇见楚轩", _vec(0.8))
            _mk_event(db, save_id, branch_b, "e_b", "在 B 线击败了爬行者", _vec(0.9))
            _mk_event(db, save_id, branch_c, "e_c", "在 C 线与郑吒结盟", _vec(0.95))
            db.commit()
        return uid, save_id, root, branch_b, branch_c

    def test_branch_isolation_and_ranking(self):
        import platform_app.knowledge.embedding as emb
        from kb.episodic import retrieve_episodic
        uid, save_id, root, b, c = self._setup_save()

        # 注固定查询向量(首维=1.0),所有事件都相似,只由分支谱系决定可见性
        emb.embed_query = lambda text, user_id, **kw: _vec(1.0)

        # 站在 B 分支召回 → 应见 共同祖先 + B 分支事件,绝不见 C 分支事件
        res_b = retrieve_episodic(save_id, b, uid, "回忆一下过去", k=10)
        keys_b = {r["logical_key"] for r in res_b}
        self.assertIn("e_common", keys_b)
        self.assertIn("e_b", keys_b)
        self.assertNotIn("e_c", keys_b, "兄弟分支 C 的事件不应在 B 分支被召回(分支串味!)")

        # 站在 C 分支召回 → 见 共同祖先 + C,不见 B
        res_c = retrieve_episodic(save_id, c, uid, "回忆一下过去", k=10)
        keys_c = {r["logical_key"] for r in res_c}
        self.assertIn("e_common", keys_c)
        self.assertIn("e_c", keys_c)
        self.assertNotIn("e_b", keys_c, "兄弟分支 B 的事件不应在 C 分支被召回")

        # 站在 root 召回 → 只见共同祖先(B/C 都还没发生)
        res_root = retrieve_episodic(save_id, root, uid, "回忆一下过去", k=10)
        keys_root = {r["logical_key"] for r in res_root}
        self.assertEqual(keys_root, {"e_common"}, f"root 只应见祖先事件:{keys_root}")

    def test_no_embedder_returns_empty(self):
        import platform_app.knowledge.embedding as emb
        from kb.episodic import retrieve_episodic
        uid, save_id, root, b, c = self._setup_save()
        emb.embed_query = lambda text, user_id, **kw: None  # 无 embedder
        self.assertEqual(retrieve_episodic(save_id, b, uid, "回忆", k=5), [],
                         "无 embedder 应静默返空,降级到近因检索")


if __name__ == "__main__":
    unittest.main()
