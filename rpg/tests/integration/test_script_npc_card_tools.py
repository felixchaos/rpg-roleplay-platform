"""LLM 工具回归:剧本 NPC 角色卡读取接口(用户报「工具接口不全，缺少 npc 角色卡接口」)。

历史 bug:`list_script_npcs` 查的是 v28 起已不存在的 `script_character_cards` 表 → 线上
UndefinedTable 抛错,LLM 在酒馆/游戏里「提取不到 NPC 角色卡」。修复:改查权威表
`character_cards`(card_type='npc'),并新增 `get_script_character_card` 读完整人设。

真实 DB 验证:owner 可列/可读、非订阅者被权限拦、空剧本给友好提示、card_id 缺失提示。
"""
from __future__ import annotations

import json
import os
import unittest

os.environ.setdefault("RPG_DEPLOYMENT_MODE", "local")


class TestScriptNpcCardTools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            cls.owner = int(db.execute(
                "insert into users(username, display_name, password_hash, email) "
                "values (%s,%s,%s,%s) returning id",
                ("npc_tool_owner", "Owner", "x", "npc_tool_owner@example.com"),
            ).fetchone()["id"])
            cls.outsider = int(db.execute(
                "insert into users(username, display_name, password_hash, email) "
                "values (%s,%s,%s,%s) returning id",
                ("npc_tool_outsider", "Outsider", "x", "npc_tool_outsider@example.com"),
            ).fetchone()["id"])
            # 有卡的剧本
            cls.sid = int(db.execute(
                "insert into scripts(owner_id, title) values (%s,%s) returning id",
                (cls.owner, "全裸校园运动会(测试)"),
            ).fetchone()["id"])
            book_id = int(db.execute(
                "insert into books(owner_id, script_id, title, slug) values (%s,%s,%s,%s) returning id",
                (cls.owner, cls.sid, "全裸校园运动会", "naked-sports-test"),
            ).fetchone()["id"])
            cls.card1 = int(db.execute(
                "insert into character_cards(book_id, script_id, name, full_name, identity, "
                "  background, appearance, personality, speech_style, importance, card_type, source, scope) "
                "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'npc','extracted','script') returning id",
                (book_id, cls.sid, "林宣", "林宣", "运动会女主",
                 "某校园运动会的核心人物。", "黑发少女。", "倔强又害羞。", "语气直接。", 90),
            ).fetchone()["id"])
            db.execute(
                "insert into character_cards(book_id, script_id, name, identity, importance, "
                "  card_type, source, scope) values (%s,%s,%s,%s,%s,'npc','extracted','script')",
                (book_id, cls.sid, "裁判老师", "运动会裁判", 40),
            )
            # 空剧本(同 owner,无卡)
            cls.empty_sid = int(db.execute(
                "insert into scripts(owner_id, title) values (%s,%s) returning id",
                (cls.owner, "空剧本(测试)"),
            ).fetchone()["id"])

    @classmethod
    def tearDownClass(cls):
        from platform_app.db import connect
        with connect() as db:
            db.execute("delete from character_cards where script_id = any(%s)", ([cls.sid, cls.empty_sid],))
            db.execute("delete from books where script_id = any(%s)", ([cls.sid, cls.empty_sid],))
            db.execute("delete from scripts where id = any(%s)", ([cls.sid, cls.empty_sid],))
            db.execute("delete from users where id = any(%s)", ([cls.owner, cls.outsider],))

    def test_list_returns_npc_cards_for_owner(self):
        from tools_dsl.command_tools_queries import _t_list_script_npcs
        out = _t_list_script_npcs(self.owner, self.sid, {}, None)
        self.assertFalse(out.startswith("失败"), out)
        rows = json.loads(out)
        names = {r["name"] for r in rows}
        self.assertEqual(names, {"林宣", "裁判老师"})
        # 重要度降序:林宣(90)在前
        self.assertEqual(rows[0]["name"], "林宣")
        self.assertIn("identity", rows[0])

    def test_get_returns_full_card_for_owner(self):
        from tools_dsl.command_tools_queries import _t_get_script_character_card
        out = _t_get_script_character_card(self.owner, self.sid, {"card_id": self.card1}, None)
        self.assertFalse(out.startswith("失败"), out)
        card = json.loads(out)
        # 完整人设字段在(list 不含,get 才有)
        blob = json.dumps(card, ensure_ascii=False)
        self.assertIn("黑发少女", blob)
        self.assertIn("倔强又害羞", blob)

    def test_outsider_blocked_on_list(self):
        from tools_dsl.command_tools_queries import _t_list_script_npcs
        out = _t_list_script_npcs(self.outsider, self.sid, {}, None)
        self.assertTrue(out.startswith("失败 (权限)"), out)

    def test_outsider_blocked_on_get(self):
        from tools_dsl.command_tools_queries import _t_get_script_character_card
        out = _t_get_script_character_card(self.outsider, self.sid, {"card_id": self.card1}, None)
        self.assertTrue(out.startswith("失败 (权限)"), out)

    def test_empty_script_friendly_message(self):
        from tools_dsl.command_tools_queries import _t_list_script_npcs
        out = _t_list_script_npcs(self.owner, self.empty_sid, {}, None)
        self.assertNotIn("UndefinedTable", out)
        self.assertIn("暂无 NPC 角色卡", out)

    def test_get_requires_card_id(self):
        from tools_dsl.command_tools_queries import _t_get_script_character_card
        out = _t_get_script_character_card(self.owner, self.sid, {}, None)
        self.assertIn("card_id 必填", out)

    def test_tools_registered_and_visible_to_llm_chat(self):
        from tools_dsl.command_dispatcher import get_registry
        from tools_dsl.command_tools_register import ensure_registered
        ensure_registered()
        reg = get_registry()
        self.assertTrue(reg.has("list_script_npcs"))
        self.assertTrue(reg.has("get_script_character_card"))
        names = {t["name"] if isinstance(t, dict) else t.name
                 for t in reg.list_for_origin("llm_chat")}
        self.assertIn("get_script_character_card", names)


if __name__ == "__main__":
    unittest.main()
