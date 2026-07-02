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

    def test_choice_rewrite_swaps_message_and_logs(self):
        user = register_user(self.client)
        uid = _uid(user["username"])
        orig = "你走进房间,在椅子上坐了下来。"
        rewrite = "你走进房间,给自己泡了一杯红茶,慢慢啜饮。"
        save_id, msg_id = _seed_save_with_message(uid, orig)
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
            new_content = db.execute("select content from messages where id=%s", (msg_id,)).fetchone()["content"]
        self.assertEqual(chosen, "rewrite")
        self.assertEqual(new_content, rewrite, "assistant 消息未被换成服务端存的 rewrite_text")

    def test_choice_original_logs_only(self):
        user = register_user(self.client)
        uid = _uid(user["username"])
        orig = "首稿正文保持不动。"
        save_id, msg_id = _seed_save_with_message(uid, orig)
        alt_id = _seed_alt(uid, save_id, orig, "不该被用到的改写稿")

        r = self.client.post("/api/acceptance/choice",
                             json={"alt_id": alt_id, "choice": "original", "message_index": 0},
                             cookies=user["cookies"])
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json().get("swapped"), "original 不应 swap")
        from platform_app.db import connect
        with connect() as db:
            chosen = db.execute("select chosen from acceptance_ab_log where id=%s", (alt_id,)).fetchone()["chosen"]
            content = db.execute("select content from messages where id=%s", (msg_id,)).fetchone()["content"]
        self.assertEqual(chosen, "original")
        self.assertEqual(content, orig, "original 选择不应改动消息")

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
