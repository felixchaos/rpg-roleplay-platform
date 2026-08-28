"""
test_script_npc_card_import.py
==============================

群反馈(白玖):剧本详情「NPC 角色卡」只能一张张手填,手里现成的酒馆卡没有导入口。
新增 POST /api/scripts/{id}/character-cards/import-tavern —— 与用户卡导入
(/api/me/character-cards/import-tavern)共用请求解析与字段映射,只有落点不同。

本文件锁三件事:
  1. 字段映射(tavern_to_npc_card):人设走与 PC 卡同一份映射,只补剧本侧默认值,
     且默认值与前端手建卡(cardFormPayload)一致 —— 导入的卡不能一进来就 importance=0。
  2. 同名合并(merge_imported_card):导入的是人设,不该抹掉该卡在本剧本里的位置
     (首现章节/重要度/主角锁),也不该把酒馆卡没有的字段清空。
  3. 两个导入端点共用 api/_card_import 的解析(奇偶守卫):防止将来有人只给一边加
     PNG / base64 支持,用户在两个入口拖同一张卡得到两种结果。
"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]  # rpg/
sys.path.insert(0, str(PROJECT))

from platform_app.api._card_import import parse_card_import_request  # noqa: E402
from platform_app.knowledge.character_cards import merge_imported_card  # noqa: E402
from platform_app.tavern_cards import (  # noqa: E402
    NPC_CARD_DEFAULTS,
    parse_card,
    tavern_to_npc_card,
    tavern_to_user_card,
)

CARD_V2 = {
    "spec": "chara_card_v2",
    "spec_version": "2.0",
    "data": {
        "name": "夜莺",
        "description": "身份：北境刺客\n外貌：银发,左眼有疤\n背景：孤儿出身",
        "personality": "寡言",
        "scenario": "雪夜的客栈",
        "first_mes": "你来了。",
        "mes_example": "<START>\n{{char}}: 别出声。",
        "tags": ["刺客", "北境"],
    },
}


class NpcMappingReusesPcMapping(unittest.TestCase):
    def test_persona_fields_identical_to_pc_mapping(self):
        pc = tavern_to_user_card(parse_card(CARD_V2))
        npc = tavern_to_npc_card(parse_card(CARD_V2))
        for field in ("name", "identity", "background", "appearance",
                      "personality", "speech_style", "current_status",
                      "secrets", "sample_dialogue", "tags", "metadata"):
            self.assertEqual(pc[field], npc[field], f"{field} 两条导入路径必须同一份映射")

    def test_script_side_defaults_match_manual_add(self):
        # frontend/src/components/cards/helpers.js 的 cardFormPayload:手建 NPC 卡的默认值
        npc = tavern_to_npc_card(parse_card(CARD_V2))
        self.assertEqual(npc["first_revealed_chapter"], 1)
        self.assertEqual(npc["importance"], 100)
        self.assertEqual(npc["token_budget"], 450)
        self.assertEqual(npc["priority"], 100)
        self.assertTrue(npc["enabled"])
        self.assertEqual(NPC_CARD_DEFAULTS["importance"], 100)

    def test_structured_split_still_applies(self):
        npc = tavern_to_npc_card(parse_card(CARD_V2))
        self.assertIn("北境刺客", npc["identity"])
        self.assertIn("银发", npc["appearance"])
        self.assertEqual(npc["sample_dialogue"], ["别出声。"])


class MergeKeepsScriptSideFields(unittest.TestCase):
    EXISTING = {
        "id": 42,
        "name": "夜莺",
        "identity": "被提取出来的身份",
        "background": "第 30 章才登场的旧背景",
        "secrets": "提取链路攒下的秘密",
        "aliases": ["小夜"],
        "tags": ["原著"],
        "sample_dialogue": ["旧样例"],
        "first_revealed_chapter": 30,
        "importance": 80,
        "token_budget": 900,
        "priority": 110,
        "enabled": False,
        "metadata": {"is_protagonist": True, "protagonist_locked": True},
    }

    def test_new_card_passthrough(self):
        payload = tavern_to_npc_card(parse_card(CARD_V2))
        merged = merge_imported_card(None, payload)
        self.assertEqual(merged, payload)
        self.assertNotIn("id", merged)

    def test_existing_card_updates_in_place(self):
        merged = merge_imported_card(self.EXISTING, tavern_to_npc_card(parse_card(CARD_V2)))
        self.assertEqual(merged["id"], 42)

    def test_script_side_fields_preserved(self):
        merged = merge_imported_card(self.EXISTING, tavern_to_npc_card(parse_card(CARD_V2)))
        self.assertEqual(merged["first_revealed_chapter"], 30, "导入不该把首现章节打回第 1 章")
        self.assertEqual(merged["importance"], 80)
        self.assertEqual(merged["token_budget"], 900)
        self.assertEqual(merged["priority"], 110)
        self.assertFalse(merged["enabled"], "导入不该把用户关掉的卡重新打开")

    def test_protagonist_lock_survives(self):
        merged = merge_imported_card(self.EXISTING, tavern_to_npc_card(parse_card(CARD_V2)))
        self.assertTrue(merged["metadata"]["is_protagonist"])
        self.assertTrue(merged["metadata"]["protagonist_locked"])
        self.assertTrue(merged["metadata"]["tavern_imported"], "导入标记也要在")

    def test_nonempty_import_overwrites_persona(self):
        merged = merge_imported_card(self.EXISTING, tavern_to_npc_card(parse_card(CARD_V2)))
        self.assertIn("北境刺客", merged["identity"])
        self.assertEqual(merged["tags"], ["刺客", "北境"])
        self.assertEqual(merged["sample_dialogue"], ["别出声。"])

    def test_empty_import_field_does_not_wipe(self):
        # 酒馆卡没有「秘密」这种字段 → 留空;留空 ≠ 用户要求清空旧卡的内容
        merged = merge_imported_card(self.EXISTING, tavern_to_npc_card(parse_card(CARD_V2)))
        self.assertEqual(merged["secrets"], "提取链路攒下的秘密")
        self.assertEqual(merged["aliases"], ["小夜"], "酒馆卡不带别名 → 保留旧别名")


class _StubRequest:
    """只实现 parse_card_import_request 真正用到的接口(headers/json)。"""

    def __init__(self, body: dict, content_type: str = "application/json"):
        self.headers = {"content-type": content_type}
        self._body = body

    async def json(self):
        return self._body


class RequestParsingIsShared(unittest.TestCase):
    def test_json_string_shape(self):
        req = _StubRequest({"json_string": json.dumps(CARD_V2)})
        v2, image, ai_split = asyncio.run(parse_card_import_request(req))
        self.assertEqual(v2["data"]["name"], "夜莺")
        self.assertIsNone(image)
        self.assertFalse(ai_split)

    def test_ai_split_optin(self):
        req = _StubRequest({"json": CARD_V2, "ai_split": "true"})
        _v2, _image, ai_split = asyncio.run(parse_card_import_request(req))
        self.assertTrue(ai_split)

    def test_unknown_shape_raises_valueerror(self):
        # 端点统一 `except ValueError → 400`,所以解析失败必须是 ValueError 而不是 500
        with self.assertRaises(ValueError):
            asyncio.run(parse_card_import_request(_StubRequest({"nope": 1})))


class BothEndpointsUseSharedParser(unittest.TestCase):
    """奇偶守卫:两个导入端点都必须用同一份解析,谁也别自己再写一遍 multipart 分支。"""

    ME_TAVERN = (PROJECT / "platform_app" / "api" / "me" / "tavern.py").read_text(encoding="utf-8")
    SCRIPT_CARDS = (PROJECT / "platform_app" / "api" / "scripts" / "cards.py").read_text(encoding="utf-8")

    def test_user_card_endpoint_imports_shared_parser(self):
        self.assertIn("parse_card_import_request", self.ME_TAVERN)

    def test_script_card_endpoint_imports_shared_parser(self):
        self.assertIn("parse_card_import_request", self.SCRIPT_CARDS)

    def test_no_local_multipart_reimplementation(self):
        for name, src in (("me/tavern.py", self.ME_TAVERN), ("scripts/cards.py", self.SCRIPT_CARDS)):
            self.assertNotIn('"multipart/form-data" in content_type', src,
                             f"{name} 又自己解析了一遍 multipart —— 解析只应住在 api/_card_import.py")

    def test_script_import_goes_through_owner_gated_helper(self):
        # 写路径必须走 knowledge.import_character_card(内含 _require_script_owner),
        # 不准在端点里手写归属 SQL。
        self.assertIn("knowledge.import_character_card", self.SCRIPT_CARDS)
        self.assertNotIn("select 1 from scripts", self.SCRIPT_CARDS)


if __name__ == "__main__":
    unittest.main()
