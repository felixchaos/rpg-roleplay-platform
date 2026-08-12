"""按需 AI 复核 NPC 卡(card_audit)集成测试 —— mock LLM,验证裁决确定性应用。

覆盖:合并同人卡(金玉/玉儿/小玉)+ 锁定真主角 + 删非人名卡(将军);非 owner 拒绝;
被并走的主角 id 自动落到保留卡;LLM 解析失败抛错。
"""
from __future__ import annotations

import unittest

from platform_app.knowledge import card_audit
from tests.helpers import cleanup_test_users, make_client, random_suffix


class CardAuditIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def _setup(self):
        """建 owner + 剧本 + book + 5 张卡:金玉/玉儿/小玉(同人)、红姑(配角)、将军(非人名)。"""
        from platform_app.db import connect
        uname = f"integtest_{random_suffix()}@x.test"
        with connect() as db:
            uid = int(db.execute(
                "insert into users(username,display_name,role,email,email_verified,terms_accepted_at,age_confirmed) "
                "values (%s,'i','user',%s,true,now(),true) returning id", (uname, uname)).fetchone()["id"])
            sid = int(db.execute(
                "insert into scripts(owner_id,title) values (%s,'大漠谣') returning id", (uid,)).fetchone()["id"])
            bid = int(db.execute(
                "insert into books(owner_id,script_id,slug,title) values (%s,%s,%s,'大漠谣') returning id",
                (uid, sid, f"b_{uid}")).fetchone()["id"])
            ids = {}
            for name, imp in (("金玉", 30), ("玉儿", 50), ("小玉", 12), ("红姑", 40), ("将军", 8)):
                ids[name] = int(db.execute(
                    "insert into character_cards(book_id,script_id,name,card_type,source,scope,priority,importance,metadata) "
                    "values (%s,%s,%s,'npc','extracted','script',100,%s,'{}'::jsonb) returning id",
                    (bid, sid, name, imp)).fetchone()["id"])
        return uid, sid, ids

    def _patch_llm(self, verdict_json):
        """monkeypatch call_agent_json 返回固定裁决 + resolve_api_key 给假 key,免真 LLM/凭证。"""
        import agents._harness as _h
        import platform_app.user_credentials as _uc
        orig_call = _h.call_agent_json
        orig_key = _uc.resolve_api_key
        _h.call_agent_json = lambda *a, **k: (verdict_json, {"input_tokens": 1, "output_tokens": 1})
        _uc.resolve_api_key = lambda *a, **k: {"key": "test-key", "source": "user_db", "base_url_override": ""}
        self.addCleanup(lambda: setattr(_h, "call_agent_json", orig_call))
        self.addCleanup(lambda: setattr(_uc, "resolve_api_key", orig_key))

    def test_merge_protagonist_drop(self):
        import json

        from platform_app.db import connect
        uid, sid, ids = self._setup()
        verdict = {
            "merges": [{"keep": ids["金玉"], "merge_ids": [ids["玉儿"], ids["小玉"]]}],
            "protagonist_id": ids["玉儿"],   # 主角卡被并走 → 应落到保留卡 金玉
            "non_person_ids": [ids["将军"]],
            "confidence": 0.9,
        }
        self._patch_llm(json.dumps(verdict, ensure_ascii=False))
        out = card_audit.audit_character_cards(uid, sid, "vertex_ai", "gemini-3.5-flash")
        sm = out["summary"]

        with connect() as db:
            rows = db.execute(
                "select id, name, aliases, importance, (metadata->>'is_protagonist')::boolean as prot, "
                "(metadata->>'protagonist_locked')::boolean as locked "
                "from character_cards where script_id=%s and card_type='npc'", (sid,)).fetchall()
        by_name = {r["name"]: dict(r) for r in rows}
        # 玉儿/小玉 被并走删除
        self.assertNotIn("玉儿", by_name, "玉儿应被合并删除")
        self.assertNotIn("小玉", by_name, "小玉应被合并删除")
        # 将军 删除
        self.assertNotIn("将军", by_name, "非人名将军应删除")
        # 金玉 收齐别名 + 锁为主角(主角 id 被并走→落保留卡)
        self.assertIn("金玉", by_name)
        al = by_name["金玉"]["aliases"] or []
        self.assertIn("玉儿", al)
        self.assertIn("小玉", al)
        self.assertTrue(by_name["金玉"]["prot"], "金玉应被锁为主角")
        self.assertTrue(by_name["金玉"]["locked"])
        self.assertEqual(by_name["金玉"]["importance"], 50, "合并后取成员最高 importance")
        # 红姑 保留,非主角
        self.assertIn("红姑", by_name)
        self.assertFalse(by_name["红姑"]["prot"])
        # 摘要
        self.assertEqual(sm["protagonist"], "金玉")
        self.assertEqual(len(sm["merged"]), 1)
        self.assertIn("将军", sm["dropped"])

    def test_owner_only(self):
        import json
        uid, sid, ids = self._setup()
        self._patch_llm(json.dumps({"merges": [], "protagonist_id": None, "non_person_ids": []}))
        with self.assertRaises(ValueError):
            card_audit.audit_character_cards(uid + 999999, sid, "vertex_ai", "gemini-3.5-flash")

    def test_bad_llm_json_raises(self):
        uid, sid, ids = self._setup()
        self._patch_llm("这不是 JSON,模型抽风了")
        with self.assertRaises(ValueError):
            card_audit.audit_character_cards(uid, sid, "vertex_ai", "gemini-3.5-flash")

    def test_invalid_ids_ignored(self):
        """LLM 编造不存在的 id → 保守忽略,不误删/误并。"""
        import json

        from platform_app.db import connect
        uid, sid, ids = self._setup()
        self._patch_llm(json.dumps({
            "merges": [{"keep": 999999, "merge_ids": [888888]}],
            "protagonist_id": 777777,
            "non_person_ids": [666666],
        }))
        card_audit.audit_character_cards(uid, sid, "vertex_ai", "gemini-3.5-flash")
        with connect() as db:
            n = db.execute("select count(*) c from character_cards where script_id=%s and card_type='npc'", (sid,)).fetchone()["c"]
        self.assertEqual(int(n), 5, "编造 id 不应动任何卡")


if __name__ == "__main__":
    unittest.main(verbosity=2)
