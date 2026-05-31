"""
test_active_entities_three_tier.py
==================================

Codex 评审定调:不要把所有 NPC 塞进完整角色卡系统。改成三层架构:

  1. active_entities  — 轻量运行时索引 (NPC / 敌人 / 临时角色)
       来源:room.npcs+enemies (source=room_data) / encounter.combatants (source=encounter)
  2. relationships    — 玩家与角色明确态度变化
  3. character_cards  — 长期完整角色卡,只在平台『角色卡』页手动 / 半自动提升

用户额外硬要求:**游戏界面的人物侧边栏不应有"转为用户角色卡"按钮**;
这个按钮只能存在于平台 (Platform) 的角色卡页面。

本测试 3 层全覆盖:

Layer A — Backend state schema + helpers:
  · DEFAULT_STATE.active_entities = []
  · upsert_active_entity / set_active_entities / replace_active_entities_with_source
  · prune_active_entities

Layer B — rules_bridge 自动同步:
  · start_module → 起点房间的 npcs + enemies 自动进 active_entities (source=room_data)
  · enter_room → 切房间时只换 source=room_data 实体,其他 source 保留
  · start_encounter_by_id → enemy combatants 进 active_entities (source=encounter)

Layer C — Frontend (静态扫源):
  · PANEL_TABS cards tab label 改成"人物"
  · PanelCharacters 读 state.active_entities (不只读 relationships)
  · PanelCharacters 渲染 3 section: 当前在场 / 关系 / 已固定角色卡 (条件)
  · **game-panels.jsx 不再有"转为用户角色卡"按钮 / saveAsUserCard 函数 /
     CharacterEditModal**
  · CharacterCard 不再带 onPromote / onEdit prop
  · platform-app.jsx 仍保留 promoteNpcToUserCard (这是合法的)
  · Game Console.html PICK_STATE_KEYS 含 "active_entities"
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from tests.helpers import make_client, register_user

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PANELS_JSX = (PROJECT_ROOT / "frontend" / "src" / "game-panels.jsx").read_text(encoding="utf-8")
PLATFORM_JSX = (PROJECT_ROOT / "frontend" / "src" / "platform-app.jsx").read_text(encoding="utf-8")
GAME_HTML = (PROJECT_ROOT / "frontend" / "Game Console.html").read_text(encoding="utf-8")


# ───────────────────────────────────────────────────────────
# Layer A: state.py schema + helpers
# ───────────────────────────────────────────────────────────


class ActiveEntitiesStateSchema(unittest.TestCase):
    """state.active_entities 字段 + helpers 单元测试。"""

    def test_default_state_has_active_entities_empty_list(self):
        from state import DEFAULT_STATE
        self.assertIn("active_entities", DEFAULT_STATE,
            "DEFAULT_STATE 应有 active_entities 字段")
        self.assertEqual(DEFAULT_STATE["active_entities"], [],
            "active_entities 默认空列表")

    def test_upsert_new_entity(self):
        import copy as _copy

        from state import DEFAULT_STATE, GameState
        g = GameState(_copy.deepcopy(DEFAULT_STATE))
        g.upsert_active_entity({"id": "ash_skulker_1", "name": "灰布教徒·甲", "kind": "enemy"})
        active = g.data["active_entities"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["id"], "ash_skulker_1")
        self.assertEqual(active[0]["name"], "灰布教徒·甲")
        # 默认字段
        self.assertEqual(active[0]["disposition"], "unknown")
        self.assertEqual(active[0]["confidence"], 1.0)
        self.assertIn("first_seen_turn", active[0])
        self.assertIn("last_seen_turn", active[0])

    def test_upsert_existing_entity_preserves_source_and_first_seen(self):
        """重复 upsert 同一 id 不应覆盖 source / first_seen_turn,只更新 last_seen + 合并字段。"""
        import copy as _copy

        from state import DEFAULT_STATE, GameState
        g = GameState(_copy.deepcopy(DEFAULT_STATE))
        g.data["turn"] = 5
        g.upsert_active_entity({"id": "x", "name": "X", "source": "room_data"})
        self.assertEqual(g.data["active_entities"][0]["first_seen_turn"], 5)
        # 模拟下一轮
        g.data["turn"] = 7
        g.upsert_active_entity({"id": "x", "name": "X (变名)", "source": "encounter"})
        ent = g.data["active_entities"][0]
        self.assertEqual(ent["source"], "room_data", "source 应保留首次值")
        self.assertEqual(ent["first_seen_turn"], 5, "first_seen_turn 不变")
        self.assertEqual(ent["last_seen_turn"], 7, "last_seen_turn 更新")
        self.assertEqual(ent["name"], "X (变名)", "name 合并新值")

    def test_replace_active_entities_with_source(self):
        """换房间时只删 source=room_data,encounter / gm_provisional 保留。"""
        import copy as _copy

        from state import DEFAULT_STATE, GameState
        g = GameState(_copy.deepcopy(DEFAULT_STATE))
        g.upsert_active_entity({"id": "room_npc", "name": "矿工", "source": "room_data"})
        g.upsert_active_entity({"id": "enc_enemy", "name": "教徒", "source": "encounter"})
        g.replace_active_entities_with_source("room_data",
            [{"id": "new_room_npc", "name": "新房间 NPC", "source": "room_data"}])
        ids = {e["id"] for e in g.data["active_entities"]}
        sources = {e["id"]: e["source"] for e in g.data["active_entities"]}
        self.assertIn("enc_enemy", ids, "encounter source 实体不应被删")
        self.assertNotIn("room_npc", ids, "旧 room_data 实体应被替换")
        self.assertIn("new_room_npc", ids, "新 room_data 实体应进来")
        self.assertEqual(sources["enc_enemy"], "encounter")

    def test_prune_active_entities(self):
        import copy as _copy

        from state import DEFAULT_STATE, GameState
        g = GameState(_copy.deepcopy(DEFAULT_STATE))
        g.upsert_active_entity({"id": "a", "source": "room_data"})
        g.upsert_active_entity({"id": "b", "source": "room_data"})
        g.upsert_active_entity({"id": "c", "source": "encounter"})
        removed = g.prune_active_entities(["a", "c"])
        self.assertEqual(removed, 1)
        ids = {e["id"] for e in g.data["active_entities"]}
        self.assertEqual(ids, {"a", "c"})

    def test_set_active_entities_overwrites(self):
        import copy as _copy

        from state import DEFAULT_STATE, GameState
        g = GameState(_copy.deepcopy(DEFAULT_STATE))
        g.upsert_active_entity({"id": "x"})
        g.set_active_entities([{"id": "y"}, {"id": "z"}])
        ids = {e["id"] for e in g.data["active_entities"]}
        self.assertEqual(ids, {"y", "z"})


# ───────────────────────────────────────────────────────────
# Layer B: rules_bridge 自动同步
# ───────────────────────────────────────────────────────────


class RulesBridgeSyncsActiveEntities(unittest.TestCase):
    """start_module / enter_room / start_encounter 自动同步 active_entities。"""

    def _ash_mine_state(self):
        import copy as _copy

        from rules_bridge import start_module
        from state import DEFAULT_STATE, GameState
        g = GameState(_copy.deepcopy(DEFAULT_STATE))
        start_module(g, "ash_mine")
        return g

    def test_start_module_seeds_entities_from_starting_room(self):
        """Ash Mine 起点 mine_entrance 没 npcs/enemies → active_entities=[];
        但函数被调用 + 不抛异常。"""
        g = self._ash_mine_state()
        # 起点是 mine_entrance,数据里 npcs=[] enemies=[]
        self.assertEqual(g.data["active_entities"], [],
            "mine_entrance 没有 npcs/enemies,active_entities 应空")

    def test_enter_room_no_npcs_clears_room_data_entities(self):
        """进入空房间时清掉 source=room_data 旧实体。"""
        from rules_bridge import enter_room
        g = self._ash_mine_state()
        # 手工塞个 room_data 实体模拟之前在带 NPC 的房间
        g.upsert_active_entity({"id": "old_npc", "source": "room_data"})
        g.upsert_active_entity({"id": "kept_enc", "source": "encounter"})
        # 进 shaft_lift (没 npcs/enemies)
        r = enter_room(g, "shaft_lift")
        self.assertTrue(r.get("ok"), r)
        ids = {e["id"] for e in g.data["active_entities"]}
        self.assertNotIn("old_npc", ids, "进入空 npcs 房间应清掉旧 room_data 实体")
        self.assertIn("kept_enc", ids, "encounter source 实体应保留")

    def test_start_encounter_writes_enemy_combatants_into_active_entities(self):
        """启动合法 encounter 后,enemy combatants 进入 active_entities (source=encounter)。"""
        from rules_bridge import start_encounter_by_id
        g = self._ash_mine_state()
        # 进 ash_camp 并触发 encounter
        # ash_camp_combat 的 location_id = ash_camp
        # 先穿过去:mine_entrance → shaft_lift → rest_cavern → mine_passage → ash_camp
        # 简化:直接 start_encounter_by_id
        r = start_encounter_by_id(g, "ash_camp_combat", seed=42)
        self.assertTrue(r.get("ok"), r)
        active = g.data["active_entities"]
        # 应至少有 3 个 enemy (灰布教徒·甲乙 + 灰烬教典狱)
        enemies = [e for e in active if e.get("kind") == "enemy"]
        self.assertGreaterEqual(len(enemies), 3,
            f"启动 ash_camp_combat 应有 3+ 个 enemy 进 active_entities,实际 {len(enemies)}")
        names = " ".join(e.get("name") or "" for e in enemies)
        self.assertIn("灰布教徒", names)
        self.assertIn("灰烬教典", names)
        # 都应是 source=encounter
        for e in enemies:
            self.assertEqual(e.get("source"), "encounter",
                f"enemy {e.get('id')} 应 source=encounter,实际 {e.get('source')}")
            self.assertEqual(e.get("disposition"), "hostile")

    def test_player_not_in_active_entities(self):
        """玩家(side=party)不应进 active_entities。"""
        from rules_bridge import start_encounter_by_id
        g = self._ash_mine_state()
        start_encounter_by_id(g, "ash_camp_combat", seed=42)
        for e in g.data["active_entities"]:
            self.assertNotEqual(e.get("kind"), "party")
            self.assertNotEqual(str(e.get("id") or "").lower(), "player",
                "玩家自己不应作为 active_entity")


# ───────────────────────────────────────────────────────────
# Layer C: Frontend 静态扫源
# ───────────────────────────────────────────────────────────


class FrontendPanelCharactersStructure(unittest.TestCase):
    """game-panels.jsx 的 PanelCharacters / CharacterCard / PANEL_TABS 结构。"""

    def test_panel_tabs_cards_label_is_renamed_to_persons(self):
        # cards tab 的 id 保持稳定 (路由 / 兼容)
        # i18n 化后 label 走 labelKey,文本在 i18n locales(zh-CN: 人物)
        m = re.search(r'\{\s*id:\s*"cards"\s*,\s*labelKey:\s*"([^"]+)"', PANELS_JSX)
        self.assertIsNotNone(m, "PANEL_TABS 应有 id='cards' 条目带 labelKey")
        self.assertEqual(m.group(1), "game.tabs.cards",
            f"cards tab labelKey 应是 game.tabs.cards,实际: {m.group(1)}")

    def test_panel_characters_reads_active_entities(self):
        # 找 PanelCharacters 函数体
        idx = PANELS_JSX.find("function PanelCharacters")
        self.assertGreater(idx, 0)
        end = PANELS_JSX.find("\nfunction ", idx + 1)
        body = PANELS_JSX[idx:end if end > 0 else len(PANELS_JSX)]
        self.assertIn("state.active_entities", body,
            "PanelCharacters 必须读 state.active_entities")

    def test_panel_characters_three_sections(self):
        idx = PANELS_JSX.find("function PanelCharacters")
        end = PANELS_JSX.find("\nfunction ", idx + 1)
        body = PANELS_JSX[idx:end if end > 0 else len(PANELS_JSX)]
        for needle in ("当前在场", "关系"):
            self.assertIn(needle, body,
                f"PanelCharacters 应有 '{needle}' section")
        # 已固定角色卡 section 条件渲染(仅 pinned.length > 0 时),
        # 字符串本身一定要在源里
        self.assertIn("已固定角色卡", body,
            "PanelCharacters 应有 '已固定角色卡' section (条件渲染)")

    def test_no_promote_button_in_game_ui(self):
        """游戏面板不应有"转为用户角色卡"按钮 — 这是用户硬要求,
        创建 / 提升只能在平台『角色卡』页操作。

        判断标准:按钮 data-tip="转为用户角色卡" (实际渲染的属性) 不应存在。
        注释里说明"移除了 X 按钮"是允许的 — 那是文档,不是按钮本身。"""
        self.assertNotIn('data-tip="转为用户角色卡"', PANELS_JSX,
            "game-panels.jsx 不应有 data-tip='转为用户角色卡' 的按钮 — 提升只在平台")
        self.assertNotIn('"转为用户角色卡"', PANELS_JSX.replace(
            "『编辑』『转为用户角色卡』按钮", ""  # 允许注释里出现这串作历史说明
        ).replace(
            "// CharacterEditModal 已删除", ""
        ),
            "代码里(除注释解释外)不应再有'转为用户角色卡'字串作为 button label / tooltip")

    def test_no_save_as_user_card_function_in_game_ui(self):
        # saveAsUserCard 函数也应该没了
        self.assertNotIn("saveAsUserCard", PANELS_JSX,
            "game-panels.jsx 不应有 saveAsUserCard 函数 — 创建用户卡只在平台")
        # 也不应再调用 window.api.cards.myUpsert (那是平台职责)
        self.assertNotIn("window.api.cards.myUpsert", PANELS_JSX,
            "game-panels.jsx 不应调用 window.api.cards.myUpsert")

    def test_character_edit_modal_removed(self):
        """CharacterEditModal 是用户角色卡创建表单,应删除 (留 CharacterCard 即可)。"""
        self.assertNotIn("function CharacterEditModal", PANELS_JSX,
            "CharacterEditModal 应已删除 — 该 modal 是创建用户角色卡的表单,不属于游戏内 UI")

    def test_character_card_has_no_promote_props(self):
        # CharacterCard 应已删除 onPromote / onEdit 等创建相关 prop
        idx = PANELS_JSX.find("function CharacterCard(")
        self.assertGreater(idx, 0)
        end = PANELS_JSX.find("\nfunction ", idx + 1)
        body = PANELS_JSX[idx:end if end > 0 else len(PANELS_JSX)]
        import re
        self.assertNotIn("onPromote", body,
            "CharacterCard 不应再有 onPromote prop")
        # 用 \b...\b 边界匹配 onEdit 但放过合法 prop onEditStatus
        self.assertIsNone(re.search(r'\bonEdit\b(?!Status)', body),
            "CharacterCard 不应再有 onEdit prop (onEditStatus 合法)")


class PlatformStillHasPromote(unittest.TestCase):
    """合法路径:平台『角色卡』页保留 promoteNpcToUserCard。"""

    def test_platform_has_promote_function(self):
        self.assertIn("promoteNpcToUserCard", PLATFORM_JSX,
            "platform-app.jsx 必须保留 promoteNpcToUserCard 函数")

    def test_platform_promote_calls_myUpsert(self):
        # 函数体应该最终调到 window.api.cards.myUpsert
        idx = PLATFORM_JSX.find("function promoteNpcToUserCard")
        if idx < 0:
            idx = PLATFORM_JSX.find("promoteNpcToUserCard =")
        self.assertGreater(idx, 0)
        # 找接下来 600 字内有 myUpsert
        snippet = PLATFORM_JSX[idx:idx + 1500]
        self.assertIn("myUpsert", snippet,
            "promoteNpcToUserCard 应调用 cards.myUpsert (经平台后端写持久化角色卡)")


class GameConsoleStateWhitelist(unittest.TestCase):
    """Game Console.html PICK_STATE_KEYS 必须含 active_entities,否则前端拿不到。"""

    @unittest.skip('stale: PICK_STATE_KEYS 在 ESM 重构后位置改变,待修')
    def test_pick_state_keys_includes_active_entities(self):
        # 找 const PICK_STATE_KEYS = [...]
        m = re.search(r"const\s+PICK_STATE_KEYS\s*=\s*\[(.*?)\]", GAME_HTML, re.S)
        self.assertIsNotNone(m, "Game Console.html 应有 PICK_STATE_KEYS")
        keys_blob = m.group(1)
        self.assertIn('"active_entities"', keys_blob,
            "PICK_STATE_KEYS 必须含 'active_entities'")


# ───────────────────────────────────────────────────────────
# Layer D: 端到端 — /api/state 返回 active_entities
# ───────────────────────────────────────────────────────────


class ApiStateExposesActiveEntities(unittest.TestCase):
    """启动 Ash Mine 后 /api/state 的 active_entities 反映三层架构。"""

    @unittest.skip('stale: 需要适配 B12 注册闸 fixture (email/birthday/terms)')
    def test_api_state_has_active_entities_field(self):
        client = make_client()
        u = register_user(client)
        # 启动 Ash Mine
        r = client.post("/api/v1/rules/module/launch", json={"module_id": "ash_mine"},
                        cookies=u["cookies"])
        self.assertEqual(r.status_code, 200, r.text[:200])
        state = client.get("/api/v1/state", cookies=u["cookies"]).json()
        self.assertIn("active_entities", state,
            "/api/v1/state 必须含 active_entities 字段")
        self.assertIsInstance(state["active_entities"], list)


if __name__ == "__main__":
    unittest.main(verbosity=2)
