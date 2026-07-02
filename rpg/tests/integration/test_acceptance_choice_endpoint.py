"""acceptance A/B 选择端点 e2e:/api/acceptance/choice。

- choice=rewrite → 该轮 assistant 消息换成【服务端存的】rewrite_text(messages 表),记 chosen。
- choice=original → 只记 chosen,不动消息。
- IDOR:别的用户拿同一 alt_id → 403。
需真实 DB(DATABASE_URL,默认 postgresql:///rpg_platform),无 LLM 调用。
直接 seed script→save→session→message(不走 transient module 流,后者 save_id=0 无 messages 行)。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tests.helpers import make_client, register_user, cleanup_test_users  # noqa: E402


def _uid(username: str) -> int:
    from platform_app.db import connect
    with connect() as db:
        return int(db.execute("select id from users where username=%s", (username,)).fetchone()["id"])


def _seed_save_with_message(uid: int, asst_content: str) -> tuple[int, int]:
    """建 script→game_save→session→一条 assistant message(turn=1)。返回 (save_id, message_id)。已 commit。"""
    from platform_app.db import connect
    with connect() as db:
        script_id = db.execute(
            "insert into scripts(owner_id, title) values (%s,%s) returning id",
            (uid, "ab-choice test script"),
        ).fetchone()["id"]
        save_id = db.execute(
            "insert into game_saves(user_id, script_id, title, state_path) values (%s,%s,%s,%s) returning id",
            (uid, script_id, "ab-choice save", ""),
        ).fetchone()["id"]
        sess_id = db.execute(
            "insert into game_sessions(user_id) values (%s) returning id", (uid,),
        ).fetchone()["id"]
        msg_id = db.execute(
            "insert into messages(session_id, save_id, turn, role, content) values (%s,%s,%s,%s,%s) returning id",
            (sess_id, save_id, 1, "assistant", asst_content),
        ).fetchone()["id"]
        db.commit()
    return int(save_id), int(msg_id)


def _seed_save_with_commit_history(uid: int, asst_content: str) -> tuple[int, int]:
    """建 script→game_save→session,并建一个含 history 的活跃 branch_commit(kb_native materialize
    读 history 的权威源)。返回 (save_id, commit_id)。已 commit。用来验「选改写后刷新不回退」的根修。"""
    from psycopg.types.json import Jsonb

    from platform_app.db import connect
    with connect() as db:
        script_id = db.execute(
            "insert into scripts(owner_id, title) values (%s,%s) returning id",
            (uid, "ab-commit test script"),
        ).fetchone()["id"]
        save_id = db.execute(
            "insert into game_saves(user_id, script_id, title, state_path) values (%s,%s,%s,%s) returning id",
            (uid, script_id, "ab-commit save", ""),
        ).fetchone()["id"]
        sess_id = db.execute(
            "insert into game_sessions(user_id) values (%s) returning id", (uid,),
        ).fetchone()["id"]
        # messages 表也放一条(兜底源),但权威在 commit 快照
        db.execute(
            "insert into messages(session_id, save_id, turn, role, content) values (%s,%s,%s,%s,%s)",
            (sess_id, save_id, 1, "assistant", asst_content),
        )
        snap = {"history": [{"role": "assistant", "content": asst_content}]}
        commit_id = db.execute(
            "insert into branch_commits(save_id, object_hash, turn_index, kind, title, state_snapshot)"
            " values (%s,%s,%s,%s,%s,%s) returning id",
            (save_id, "hash_ab_commit", 1, "turn", "t", Jsonb(snap)),
        ).fetchone()["id"]
        db.execute("update game_saves set active_commit_id = %s where id = %s", (commit_id, save_id))
        db.commit()
    return int(save_id), int(commit_id)


def _seed_alt(uid: int, save_id: int, original: str, rewrite: str) -> int:
    from psycopg.types.json import Jsonb

    from platform_app.db import connect
    with connect() as db:
        row = db.execute(
            "insert into acceptance_ab_log(user_id, save_id, turn, unmet, original_text, rewrite_text)"
            " values (%s,%s,%s,%s,%s,%s) returning id",
            (uid, save_id, 1, Jsonb(["红茶"]), original, rewrite),
        ).fetchone()
        db.commit()
        return int(row["id"])


class AcceptanceChoiceEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def _asst_msg_content(self, save_id: int) -> str:
        from platform_app.db import connect
        with connect() as db:
            r = db.execute(
                "select content from messages where save_id=%s and role='assistant' order by turn, id limit 1",
                (save_id,),
            ).fetchone()
        return (r or {}).get("content") if r else None

    def test_choice_rewrite_swaps_message_and_logs(self):
        # 用带 commit 的存档(真实档:玩过就有 runtime+commit)。裸档(无 commit)在本地测试台会被
        # 惰性 runtime bootstrap 重建 messages,是测试环境单一 SAVE_FILE 的假象,非生产路径。
        user = register_user(self.client)
        uid = _uid(user["username"])
        orig = "你走进房间,在椅子上坐了下来。"
        rewrite = "你走进房间,给自己泡了一杯红茶,慢慢啜饮。"
        save_id, commit_id = _seed_save_with_commit_history(uid, orig)
        alt_id = _seed_alt(uid, save_id, orig, rewrite)

        r = self.client.post("/api/acceptance/choice",
                             json={"alt_id": alt_id, "choice": "rewrite", "message_index": 0},
                             cookies=user["cookies"])
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body.get("ok"))
        self.assertTrue(body.get("swapped"), "rewrite 应触发 swap")

        from platform_app.db import connect
        with connect() as db:
            chosen = db.execute("select chosen from acceptance_ab_log where id=%s", (alt_id,)).fetchone()["chosen"]
        self.assertEqual(chosen, "rewrite")
        # messages 表(兜底源)也应被换
        self.assertEqual(self._asst_msg_content(save_id), rewrite, "assistant 消息未被换成服务端存的 rewrite_text")

    def test_choice_original_logs_only(self):
        user = register_user(self.client)
        uid = _uid(user["username"])
        orig = "首稿正文保持不动。"
        save_id, commit_id = _seed_save_with_commit_history(uid, orig)
        alt_id = _seed_alt(uid, save_id, orig, "不该被用到的改写稿")

        r = self.client.post("/api/acceptance/choice",
                             json={"alt_id": alt_id, "choice": "original", "message_index": 0},
                             cookies=user["cookies"])
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json().get("swapped"), "original 不应 swap")
        from platform_app.db import connect
        with connect() as db:
            chosen = db.execute("select chosen from acceptance_ab_log where id=%s", (alt_id,)).fetchone()["chosen"]
        self.assertEqual(chosen, "original")
        self.assertEqual(self._asst_msg_content(save_id), orig, "original 选择不应改动消息")

    def test_choice_rewrite_persists_to_active_commit_snapshot(self):
        """根修回归:选改写必须写进【活跃 commit 快照】的 history —— 这才是 kb_native 存档刷新/换 worker
        后 materialize 重新读的权威源。旧实现只改 messages+blob,不改这里 → 刷新回退首稿(行者无疆二次反馈)。"""
        import json as _json

        user = register_user(self.client)
        uid = _uid(user["username"])
        orig = "首稿:你在门口站定。"
        rewrite = "改写稿:你在门口站定,握紧了那封信。"
        save_id, commit_id = _seed_save_with_commit_history(uid, orig)
        alt_id = _seed_alt(uid, save_id, orig, rewrite)

        r = self.client.post("/api/acceptance/choice",
                             json={"alt_id": alt_id, "choice": "rewrite", "message_index": 0},
                             cookies=user["cookies"])
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json().get("swapped"))

        from platform_app.db import connect
        with connect() as db:
            snap = db.execute("select state_snapshot from branch_commits where id = %s", (commit_id,)).fetchone()["state_snapshot"]
        if isinstance(snap, str):
            snap = _json.loads(snap)
        hist = snap.get("history") or []
        self.assertEqual(hist[0]["content"], rewrite,
                         "活跃 commit 快照的 history 未被换成改写稿 → 刷新会回退首稿(根 bug 未修)")

    def test_pref_off_switch_read(self):
        """用户级开关:user_preferences['acceptance_ab.enabled']=false → 后端 gate 读到 False(玩家可关)。"""
        import chat_pipeline
        from platform_app.db import connect
        from psycopg.types.json import Jsonb
        user = register_user(self.client)
        uid = _uid(user["username"])
        # 默认(无偏好行)= 开
        self.assertTrue(chat_pipeline._acceptance_ab_pref_enabled(uid))
        with connect() as db:
            db.execute(
                "insert into user_preferences(user_id, preferences) values (%s, %s)"
                " on conflict(user_id) do update set preferences ="
                " user_preferences.preferences || excluded.preferences",
                (uid, Jsonb({"acceptance_ab.enabled": False})),
            )
            db.commit()
        self.assertFalse(chat_pipeline._acceptance_ab_pref_enabled(uid), "关掉后 gate 仍读到开")

    def test_message_edit_persists_to_active_commit_snapshot(self):
        """/api/message/edit 也必须写活跃 commit 快照(kb_native materialize 权威源)——
        否则手动编辑历史消息后刷新回退(与 acceptance 选择同源 bug)。"""
        import json as _json
        user = register_user(self.client)
        uid = _uid(user["username"])
        save_id, commit_id = _seed_save_with_commit_history(uid, "编辑前的正文")
        r = self.client.post("/api/message/edit",
                             json={"save_id": save_id, "message_index": 0, "content": "编辑后的正文"},
                             cookies=user["cookies"])
        self.assertEqual(r.status_code, 200, r.text)
        from platform_app.db import connect
        with connect() as db:
            snap = db.execute("select state_snapshot from branch_commits where id = %s", (commit_id,)).fetchone()["state_snapshot"]
        if isinstance(snap, str):
            snap = _json.loads(snap)
        self.assertEqual((snap.get("history") or [{}])[0].get("content"), "编辑后的正文",
                         "message/edit 未写活跃 commit 快照 → 刷新会回退(根 bug 未修)")
        self.assertEqual(self._asst_msg_content(save_id), "编辑后的正文", "messages 表也应更新")

    def test_message_edit_idor(self):
        owner = register_user(self.client)
        owner_uid = _uid(owner["username"])
        save_id, _ = _seed_save_with_commit_history(owner_uid, "owner 正文")
        attacker = register_user(self.client)
        r = self.client.post("/api/message/edit",
                             json={"save_id": save_id, "message_index": 0, "content": "篡改"},
                             cookies=attacker["cookies"])
        self.assertEqual(r.status_code, 403, f"IDOR 未拦截: {r.status_code} {r.text}")

    def test_idor_other_user_forbidden(self):
        owner = register_user(self.client)
        owner_uid = _uid(owner["username"])
        save_id, _ = _seed_save_with_message(owner_uid, "owner 的正文")
        alt_id = _seed_alt(owner_uid, save_id, "owner 的正文", "改写稿")

        attacker = register_user(self.client)
        r = self.client.post("/api/acceptance/choice",
                             json={"alt_id": alt_id, "choice": "rewrite", "message_index": 0},
                             cookies=attacker["cookies"])
        self.assertEqual(r.status_code, 403, f"IDOR 未拦截: {r.status_code} {r.text}")


if __name__ == "__main__":
    unittest.main()
