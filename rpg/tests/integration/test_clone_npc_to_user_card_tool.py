"""LLM 工具回归:clone_npc_to_user_card —— 把(自有或订阅共享的)剧本 NPC 卡复制成用户卡。

用户关注点:① 接口是否加了 ② 从「共享剧本(订阅、无编辑权限)」克隆是否顺
③ 鉴权顺不顺。这里用真实 DB 验证:owner 能克隆、**subscriber 能克隆(关键)**、
outsider 被权限拦、结果是独立 pc 副本(含头像)、origin 策略(console_assistant 可、
自由叙事 llm_chat 禁,与 create_character_card 一致)。
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("RPG_DEPLOYMENT_MODE", "local")


def _mku(db, uname):
    return int(db.execute(
        "insert into users(username, display_name, password_hash, email) "
        "values (%s,%s,%s,%s) returning id",
        (uname, uname, "x", uname + "@example.com"),
    ).fetchone()["id"])


class TestCloneNpcToUserCardTool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            cls.owner = _mku(db, "clone_owner")
            cls.subscriber = _mku(db, "clone_subscriber")
            cls.outsider = _mku(db, "clone_outsider")
            cls.sid = int(db.execute(
                "insert into scripts(owner_id, title) values (%s,%s) returning id",
                (cls.owner, "共享剧本(测试)"),
            ).fetchone()["id"])
            book_id = int(db.execute(
                "insert into books(owner_id, script_id, title, slug) values (%s,%s,%s,%s) returning id",
                (cls.owner, cls.sid, "共享剧本", "shared-script-test"),
            ).fetchone()["id"])
            cls.npc = int(db.execute(
                "insert into character_cards(book_id, script_id, name, full_name, identity, "
                "  background, appearance, personality, speech_style, secrets, avatar_path, "
                "  importance, card_type, source, scope) "
                "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'npc','extracted','script') returning id",
                (book_id, cls.sid, "薇拉", "薇拉·星河", "流亡贵族",
                 "出身没落贵族。", "银发紫眸。", "高傲又脆弱。", "用词考究。", "其实是私生女。",
                 "/api/storage/ai_images/npc_avatar_test.png", 80),
            ).fetchone()["id"])
            # subscriber 订阅该剧本(共享、只读,无编辑权限)
            db.execute(
                "insert into user_script_subscriptions(user_id, script_id) values (%s,%s) "
                "on conflict do nothing",
                (cls.subscriber, cls.sid),
            )

    @classmethod
    def tearDownClass(cls):
        from platform_app.db import connect
        uids = [cls.owner, cls.subscriber, cls.outsider]
        with connect() as db:
            db.execute("delete from character_cards where user_id = any(%s)", (uids,))
            db.execute("delete from character_cards where script_id = %s", (cls.sid,))
            db.execute("delete from user_script_subscriptions where script_id = %s", (cls.sid,))
            db.execute("delete from books where script_id = %s", (cls.sid,))
            db.execute("delete from scripts where id = %s", (cls.sid,))
            db.execute("delete from users where id = any(%s)", (uids,))

    def _user_card(self, user_id: int):
        from platform_app.db import connect
        with connect() as db:
            return db.execute(
                "select id, name, full_name, background, avatar_path, card_type, metadata "
                "from character_cards where user_id = %s and card_type = 'pc' order by id desc limit 1",
                (user_id,),
            ).fetchone()

    def test_owner_clone_full_copy(self):
        from tools_dsl.command_tools_persona import _t_clone_npc_to_user_card
        out = _t_clone_npc_to_user_card(self.owner, {"script_id": self.sid, "card_id": self.npc})
        self.assertTrue(out.startswith("已克隆"), out)
        card = self._user_card(self.owner)
        self.assertIsNotNone(card)
        self.assertEqual(card["card_type"], "pc")
        self.assertEqual(card["full_name"], "薇拉·星河")          # 全名带上(create_character_card 会丢)
        self.assertEqual(card["background"], "出身没落贵族。")      # 背景带上
        self.assertEqual(card["avatar_path"], "/api/storage/ai_images/npc_avatar_test.png")  # 头像复用
        self.assertNotEqual(int(card["id"]), self.npc)            # 独立副本,非指针
        self.assertEqual((card["metadata"] or {}).get("source_npc_id"), self.npc)

    def test_subscriber_can_clone_from_shared_script(self):
        """关键:订阅了共享剧本(无编辑权限)的用户,也能把 NPC 克隆成自己的卡。"""
        from tools_dsl.command_tools_persona import _t_clone_npc_to_user_card
        out = _t_clone_npc_to_user_card(self.subscriber, {"script_id": self.sid, "card_id": self.npc})
        self.assertTrue(out.startswith("已克隆"), out)
        card = self._user_card(self.subscriber)
        self.assertIsNotNone(card)
        self.assertEqual(card["full_name"], "薇拉·星河")
        self.assertEqual(card["avatar_path"], "/api/storage/ai_images/npc_avatar_test.png")

    def test_outsider_blocked(self):
        """既非 owner 也未订阅 → 读 NPC 被 _require_script 拦,不能克隆。"""
        from tools_dsl.command_tools_persona import _t_clone_npc_to_user_card
        out = _t_clone_npc_to_user_card(self.outsider, {"script_id": self.sid, "card_id": self.npc})
        self.assertTrue(out.startswith("失败 (权限)"), out)
        self.assertIsNone(self._user_card(self.outsider))

    def test_missing_args(self):
        from tools_dsl.command_tools_persona import _t_clone_npc_to_user_card
        self.assertIn("script_id 必填", _t_clone_npc_to_user_card(self.owner, {"card_id": self.npc}))
        self.assertIn("card_id 必填", _t_clone_npc_to_user_card(self.owner, {"script_id": self.sid}))

    def test_origin_policy(self):
        """控制台助手退役后放开酒馆/游戏 agent:console_assistant 与 llm_chat 都可见。"""
        from tools_dsl.command_dispatcher import get_registry
        from tools_dsl.command_tools_register import ensure_registered
        ensure_registered()
        reg = get_registry()
        self.assertTrue(reg.has("clone_npc_to_user_card"))

        def _names(origin):
            return {t["name"] if isinstance(t, dict) else t.name for t in reg.list_for_origin(origin)}

        self.assertIn("clone_npc_to_user_card", _names("console_assistant"))
        self.assertIn("clone_npc_to_user_card", _names("llm_chat"))
        # create_character_card / create_persona 同步放开
        self.assertIn("create_character_card", _names("llm_chat"))
        self.assertIn("create_persona", _names("llm_chat"))
        # 破坏性删除仍不给 llm_chat(走 UI 二次确认)
        self.assertNotIn("delete_character_card", _names("llm_chat"))


if __name__ == "__main__":
    unittest.main()
