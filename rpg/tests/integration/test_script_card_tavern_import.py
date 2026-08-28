"""剧本 NPC 卡「导入酒馆卡」集成测试(真库 round-trip)。

群反馈(白玖):剧本详情的「NPC 角色卡」只能一张张手填,手里现成的酒馆卡没有入口。
新端点 POST /api/scripts/{id}/character-cards/import-tavern。

覆盖:
  · 新卡:字段落库 + 剧本侧默认值与手建卡一致;
  · 同名卡:更新人设,但**保留**该卡在本剧本里的首现章节 / 重要度 / 主角锁 / 已有字段;
  · 非 owner(订阅者)拒绝;
  · HTTP 端到端:粘贴 JSON 形态 → 卡出现在列表接口里。
"""
from __future__ import annotations

import json
import unittest

from platform_app import knowledge
from platform_app.tavern_cards import parse_card, tavern_to_npc_card
from tests.helpers import cleanup_test_users, make_client, random_suffix

CARD_V2 = {
    "spec": "chara_card_v2",
    "spec_version": "2.0",
    "data": {
        "name": "夜莺",
        "description": "身份：北境刺客\n外貌：银发,左眼有疤\n背景：孤儿出身",
        "personality": "寡言",
        "first_mes": "你来了。",
        "mes_example": "<START>\n{{char}}: 别出声。",
        "tags": ["刺客", "北境"],
    },
}


def _payload(name: str = "夜莺") -> dict:
    data = {**CARD_V2["data"], "name": name}
    return tavern_to_npc_card(parse_card({**CARD_V2, "data": data}))


class ScriptTavernImport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def _setup(self):
        """建 owner + 剧本 + 一张「提取出来的」同名旧卡(带剧本侧字段与主角锁)。"""
        from platform_app.db import connect
        uname = f"integtest_{random_suffix()}@x.test"
        with connect() as db:
            uid = int(db.execute(
                "insert into users(username,display_name,role,email,email_verified,terms_accepted_at,age_confirmed) "
                "values (%s,'i','user',%s,true,now(),true) returning id", (uname, uname)).fetchone()["id"])
            sid = int(db.execute(
                "insert into scripts(owner_id,title) values (%s,'北境旧事') returning id", (uid,)).fetchone()["id"])
            cid = int(db.execute(
                "insert into character_cards(script_id,name,card_type,source,scope,identity,secrets,aliases,"
                "first_revealed_chapter,importance,token_budget,priority,enabled,metadata) "
                "values (%s,'夜莺','npc','extracted','script','提取出来的身份','提取链路攒下的秘密',"
                "'[\"小夜\"]'::jsonb,30,80,900,110,false,"
                "'{\"is_protagonist\": true, \"protagonist_locked\": true}'::jsonb) returning id",
                (sid,)).fetchone()["id"])
        return uid, sid, cid

    def test_import_new_card_lands_with_manual_add_defaults(self):
        uid, sid, _cid = self._setup()
        out = knowledge.import_character_card(uid, sid, _payload("寒鸦"))
        self.assertFalse(out["replaced"], "新名字应当是新建")
        card = out["card"]
        self.assertEqual(card["name"], "寒鸦")
        self.assertIn("北境刺客", card["identity"])
        self.assertIn("银发", card["appearance"])
        self.assertEqual(card["sample_dialogue"], ["别出声。"])
        self.assertEqual(sorted(card["tags"]), sorted(["刺客", "北境"]))
        # 与前端手建卡(cardFormPayload)同默认值 —— 导入的卡不能一进来就 importance=0
        self.assertEqual(card["first_revealed_chapter"], 1)
        self.assertEqual(card["importance"], 100)
        self.assertEqual(card["token_budget"], 450)
        self.assertTrue(card["enabled"])
        self.assertEqual(card["card_type"], "npc")
        # 原文留档,供「AI 整理字段」兜底 / 用户对照
        self.assertIn("tavern_raw_description", card["metadata"])

    def test_same_name_updates_persona_but_keeps_script_side_fields(self):
        uid, sid, cid = self._setup()
        out = knowledge.import_character_card(uid, sid, _payload("夜莺"))
        self.assertTrue(out["replaced"], "同名卡应当是更新而不是又建一张")
        card = out["card"]
        self.assertEqual(int(card["id"]), cid, "必须更新原来那张卡")
        # 人设换成导入的
        self.assertIn("北境刺客", card["identity"])
        # 剧本侧字段全部保留
        self.assertEqual(card["first_revealed_chapter"], 30)
        self.assertEqual(card["importance"], 80)
        self.assertEqual(card["token_budget"], 900)
        self.assertEqual(card["priority"], 110)
        self.assertFalse(card["enabled"], "导入不该把用户关掉的卡重新打开")
        self.assertTrue(card["metadata"].get("protagonist_locked"), "主角锁必须活下来")
        # 酒馆卡不带的字段不被清空
        self.assertEqual(card["secrets"], "提取链路攒下的秘密")
        self.assertEqual(list(card["aliases"]), ["小夜"])
        # 没有多出第二张同名卡
        cards = knowledge.list_character_cards(uid, sid)["items"]
        self.assertEqual(len([c for c in cards if c["name"] == "夜莺"]), 1)

    def test_non_owner_rejected(self):
        from platform_app.db import connect
        _uid, sid, _cid = self._setup()
        uname = f"integtest_{random_suffix()}@x.test"
        with connect() as db:
            other = int(db.execute(
                "insert into users(username,display_name,role,email,email_verified,terms_accepted_at,age_confirmed) "
                "values (%s,'i','user',%s,true,now(),true) returning id", (uname, uname)).fetchone()["id"])
        with self.assertRaises(ValueError):
            knowledge.import_character_card(other, sid, _payload("寒鸦"))

    def test_http_paste_json_roundtrip(self):
        """端到端:登录 → POST import-tavern(粘贴 JSON 形态)→ 卡出现在列表接口。"""
        from platform_app.db import connect
        from tests.helpers import register_user
        reg = register_user(self.client)
        with connect() as db:
            uid = int(db.execute("select id from users where username = %s",
                                 (reg["username"],)).fetchone()["id"])
            sid = int(db.execute(
                "insert into scripts(owner_id,title) values (%s,'北境旧事') returning id",
                (uid,)).fetchone()["id"])
        resp = self.client.post(
            f"/api/v1/scripts/{sid}/character-cards/import-tavern",
            json={"json_string": json.dumps(CARD_V2, ensure_ascii=False)},
            cookies=reg["cookies"],
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["ok"], body)
        self.assertFalse(body["replaced"])
        self.assertEqual(body["card"]["name"], "夜莺")

        listed = self.client.get(f"/api/v1/scripts/{sid}/character-cards", cookies=reg["cookies"])
        self.assertEqual(listed.status_code, 200, listed.text)
        names = [c["name"] for c in listed.json()["items"]]
        self.assertIn("夜莺", names)

    def test_http_png_card_sets_npc_avatar(self):
        """PNG 内嵌卡:立绘要落成这张 NPC 卡的头像。

        头像落库共用 _store_imported_card_image —— 它原本只认 user_id(用户卡),NPC 卡的
        user_id 恒 NULL,不加 script_id 分支就会"更新 0 行"、卡进来没有立绘(典型半边功能)。
        """
        from platform_app.db import connect
        from platform_app.tavern_cards import write_png_card
        from tests.helpers import register_user
        reg = register_user(self.client)
        with connect() as db:
            uid = int(db.execute("select id from users where username = %s",
                                 (reg["username"],)).fetchone()["id"])
            sid = int(db.execute(
                "insert into scripts(owner_id,title) values (%s,'北境旧事') returning id",
                (uid,)).fetchone()["id"])
        png = write_png_card(parse_card(CARD_V2))
        resp = self.client.post(
            f"/api/v1/scripts/{sid}/character-cards/import-tavern",
            files={"file": ("nightingale.png", png, "image/png")},
            cookies=reg["cookies"],
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        card = resp.json()["card"]
        self.assertEqual(card["name"], "夜莺")
        self.assertTrue(card.get("avatar_path"), "PNG 卡的立绘应当落成 NPC 卡头像")

    def test_http_bad_payload_is_400_not_500(self):
        from platform_app.db import connect
        from tests.helpers import register_user
        reg = register_user(self.client)
        with connect() as db:
            uid = int(db.execute("select id from users where username = %s",
                                 (reg["username"],)).fetchone()["id"])
            sid = int(db.execute(
                "insert into scripts(owner_id,title) values (%s,'北境旧事') returning id",
                (uid,)).fetchone()["id"])
        resp = self.client.post(
            f"/api/v1/scripts/{sid}/character-cards/import-tavern",
            json={"nope": 1}, cookies=reg["cookies"],
        )
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertFalse(resp.json()["ok"])


if __name__ == "__main__":
    unittest.main()
