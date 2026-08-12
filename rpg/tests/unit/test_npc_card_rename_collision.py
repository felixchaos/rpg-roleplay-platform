"""
test_npc_card_rename_collision.py
=================================

群反馈(晓卡):剧本里 NPC 角色卡点编辑后「保存按钮点了没反应」。prod 日志真因:
  psycopg.errors.UniqueViolation: duplicate key "uq_character_cards_npc_name"
  at character_cards.py upsert_character_card(UPDATE 分支)
即把 NPC 卡改名成**该剧本已有的另一个 NPC 名**,撞 UNIQUE(script_id,name) WHERE npc 约束,
裸 UniqueViolation 冒成 500(端点只 catch ValueError→400),前端表现为「保存没反应」。
普通编辑(不改名)不受影响——行更新自身不撞唯一约束。

不变量(源码级):
- upsert UPDATE 分支:改名前预检「别的 NPC 是否已占用该名」(id<>),命中给可行动 ValueError。
- 端点兜底:任何残留 UniqueViolation(竞态/其它路径)转 400,不再 500。
"""
from __future__ import annotations

import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]  # rpg/
CARDS_PY = (PROJECT / "platform_app" / "knowledge" / "character_cards.py").read_text(encoding="utf-8")
# scripts.py 已包化为 scripts/ 子包(纯机械搬家);按新住址读整包源码做结构断言。
_SCRIPTS_API_DIR = PROJECT / "platform_app" / "api" / "scripts"
SCRIPTS_API = "\n".join(p.read_text(encoding="utf-8") for p in sorted(_SCRIPTS_API_DIR.glob("*.py")))


class UpdatePreChecksNameCollision(unittest.TestCase):
    def test_rename_clash_precheck_present(self):
        # UPDATE 分支改名前查冲突:别的 NPC(id<>)已占用该 name
        self.assertRegex(
            CARDS_PY,
            r"select 1 from character_cards where script_id = %s and name = %s\s*"
            r"\"?\s*\n?\s*\"?and card_type='npc' and id <> %s",
        )

    def test_clash_raises_actionable_valueerror(self):
        self.assertIn("已存在同名 NPC 角色卡", CARDS_PY)
        # 必须是 ValueError(端点 ValueError→400 可显示),不是裸 raise
        self.assertRegex(CARDS_PY, r'if clash:\s*\n\s*raise ValueError\(')

    def test_same_name_self_update_not_blocked(self):
        # 预检带 id <> %s,确保「不改名的普通编辑」(更新自身)不被误拦
        self.assertIn("id <> %s", CARDS_PY)


class NpcUpsertPersistsTags(unittest.TestCase):
    """群反馈(大道):剧本 NPC 人物卡「基本信息 → 标签」这一栏保存不了。

    真因不是前端没提交(cardFormPayload 一直发 tags:[...]),而是 upsert_character_card
    的 fields / UPDATE / INSERT / on-conflict 四处都没有 tags 这一列 —— 整个字段被后端
    静默丢弃。PC 卡路径(user_cards.upsert_user_card)一直写这列,是典型「修 A 漏 B」。
    """

    def test_tags_in_fields_dict(self):
        # fields 里必须有 tags,且走与 PC 卡同一个 _normalize_list(避免解析规则漂移)
        self.assertRegex(CARDS_PY, r'"tags":\s*Jsonb\(_normalize_list\(payload\.get\("tags"\)\)\)')
        self.assertIn("from platform_app.user_cards import _normalize_list", CARDS_PY)

    def test_tags_written_by_update_branch(self):
        # UPDATE 分支(编辑已有卡 —— 用户实际踩到的路径)必须赋值 tags
        _update = CARDS_PY.split("update character_cards set", 1)[1].split("where id=%(id)s", 1)[0]
        self.assertIn("tags=%(tags)s", _update)

    def test_tags_written_by_insert_and_on_conflict(self):
        # INSERT 列清单 + values 占位 + on-conflict do update 三处都要带上,
        # 否则「新建带标签的卡」或「同名 upsert」还是会丢标签。
        _insert = CARDS_PY.split("insert into character_cards(", 1)[1]
        self.assertIn("sample_dialogue, tags,", _insert)
        self.assertIn("%(sample_dialogue)s, %(tags)s,", _insert)
        self.assertIn("tags=excluded.tags", _insert)


class EndpointConvertsUniqueViolationTo400(unittest.TestCase):
    def test_endpoint_catches_unique_violation(self):
        self.assertIn("from psycopg.errors import UniqueViolation", SCRIPTS_API)
        self.assertRegex(SCRIPTS_API, r"isinstance\(exc, UniqueViolation\)")
        # 转 400 + 可行动文案
        self.assertRegex(SCRIPTS_API, r'status_code=400')
        self.assertIn("已存在同名 NPC 角色卡", SCRIPTS_API)


if __name__ == "__main__":
    unittest.main()
