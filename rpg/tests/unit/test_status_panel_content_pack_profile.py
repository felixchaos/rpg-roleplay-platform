"""
test_status_panel_content_pack_profile.py
=========================================

Codex 评审定调:右侧状态栏不能在"模组"和"剧本"之间硬塞同一套字段。
应做成**同一个状态栏组件,不同 content pack profile**。

profile 切换规则:
- content_pack.kind === "module_adventure" 或 scene.module_id 存在 → module profile
- content_pack.kind === "novel_adaptation" → novel profile
- 其他 → freeform (与 novel 共用 NovelStatusProfile)

历史 bug (用户截图):
  1. Ash Mine 场景下,状态栏标题写"当下世界" — 该是"冒险现场"
  2. "身上之物 0 件" — Cinder 实际有短剑/短弓/火把,数据在
     `player_character.inventory`,不是 `player.inventory`
  3. "本轮已知事件"混入未经 RulesEngine 裁定的"遭遇灰布教徒并展开战斗"
  4. "身份: 5E 探险者" — 应该是 "Lv1 探险者 · HP 10/10 · AC 14"

本测试用纯文本扫描 jsx 源,锁死:
- 存在 ModuleStatusProfile + NovelStatusProfile + 单一 PanelStatus 入口
- ModuleStatusProfile 读 player_character.{level,class_name,hp,max_hp,ac}
- ModuleStatusProfile 读 player_character.inventory (不读 player.inventory)
- ModuleStatusProfile 标题用"冒险现场"而非"当下世界"
- ModuleStatusProfile 不渲染"已知事件"(world.known_events 在模组场景不可信)
- NovelStatusProfile 保持旧 4 section 布局
- _statusProfileFor 根据 content_pack.kind / scene.module_id 切换
"""
from __future__ import annotations

import unittest
from pathlib import Path

PANELS = (Path(__file__).resolve().parents[3]
          / "frontend" / "src" / "game-panels.jsx").read_text(encoding="utf-8")


def _extract_function(text: str, name: str) -> str:
    """从 jsx 源里拿到某 function 的函数体 (从 `function Name(` 到下一个 `function ` 的起点)。"""
    idx = text.find(f"function {name}(")
    if idx < 0:
        return ""
    # 找下一个顶层 function 声明
    next_idx = text.find("\nfunction ", idx + 1)
    if next_idx < 0:
        next_idx = len(text)
    return text[idx:next_idx]


class StatusPanelArchitecture(unittest.TestCase):
    """单一 PanelStatus 入口 + 两个 profile 子组件 + 选择器。"""

    def test_single_panel_status_entry(self):
        # PanelStatus 仍是唯一对外组件名,被 RightPanel 引用
        self.assertIn("function PanelStatus(", PANELS,
            "应保留 PanelStatus 作为唯一对外入口")
        # 入口里应分派 (不再直接渲染老布局)
        body = _extract_function(PANELS, "PanelStatus")
        self.assertIn("_statusProfileFor", body,
            "PanelStatus 应调用 _statusProfileFor 做 profile 分派")
        self.assertIn("ModuleStatusProfile", body)
        self.assertIn("NovelStatusProfile", body)

    def test_profile_selector_exists(self):
        # 选择器函数:content_pack.kind 或 scene.module_id 决定 profile
        self.assertIn("function _statusProfileFor(", PANELS)
        sel = _extract_function(PANELS, "_statusProfileFor")
        self.assertIn("content_pack", sel,
            "_statusProfileFor 必须读 content_pack.kind")
        self.assertIn("module_id", sel,
            "_statusProfileFor 必须 fallback 到 scene.module_id "
            "(content_pack.kind 缺失时也要 work)")
        self.assertIn("module_adventure", sel)
        self.assertIn("novel_adaptation", sel)

    def test_both_profile_components_exist(self):
        self.assertIn("function ModuleStatusProfile(", PANELS)
        self.assertIn("function NovelStatusProfile(", PANELS)


class ModuleProfileContent(unittest.TestCase):
    """ModuleStatusProfile 字段必须反映 5E 模组事实,不能继承小说字段。"""

    @classmethod
    def setUpClass(cls):
        cls.body = _extract_function(PANELS, "ModuleStatusProfile")
        assert cls.body, "ModuleStatusProfile 不存在"

    def test_reads_player_character_not_player(self):
        # 必须从 state.player_character 拿 5E 字段
        self.assertIn("state.player_character", self.body,
            "ModuleStatusProfile 必须读 state.player_character")
        # 不允许把 player.inventory 当 5E 背包来源 (这是 bug 现场)
        self.assertNotIn("p.inventory", self.body,
            "ModuleStatusProfile 不应读 player.inventory;5E 背包在 player_character.inventory")

    def test_renders_5e_stats(self):
        # Lv/Class/HP/AC 必须出现 (用户要求:'Lv1 探险者 · HP 10/10 · AC 14')
        # 写法宽松:这些字符串作为 label 或 jsx 文本出现就算
        # 标签文案已迁 i18n(HP/AC label 现为 t('game.status.hp/ac'));查 i18n key + 数据字段
        for needle in ("Lv", "game.status.hp", "game.status.ac", "class_name", "max_hp"):
            self.assertIn(needle, self.body,
                f"ModuleStatusProfile 缺 {needle!r} — 该展示 5E 角色卡核心字段")

    def test_renders_inventory_from_player_character(self):
        # 资源 section 读 pc.inventory
        self.assertIn("pc.inventory", self.body,
            "Module profile 资源 section 必须读 player_character.inventory")
        # 标题用"资源"(用户建议),不再叫"身上之物"
        self.assertIn("资源", self.body)

    def test_uses_adventure_field_titles_not_world(self):
        # 用户明确说:Ash Mine 场景下'当下世界'对玩家无意义,应改为'冒险现场'
        self.assertIn("冒险现场", self.body,
            "Module profile 必须有'冒险现场' section 标题")
        self.assertNotIn("当下世界", self.body,
            "Module profile 不应有'当下世界' — 那是小说剧本术语")

    def test_renders_room_facts(self):
        # 可见线索 / 出口 都直接从 scene.current_room 取
        self.assertIn("visible_clues", self.body,
            "Module profile 应渲染 scene.current_room.visible_clues")
        self.assertIn("room.exits", self.body,
            "Module profile 应渲染 scene.current_room.exits")
        self.assertIn("可见线索", self.body)
        self.assertIn("出口", self.body)

    def test_combat_section_conditional_on_encounter_active(self):
        # 战斗 section 仅 encounter.active 时显示(标题文案已迁 i18n)
        self.assertIn("encounter.active", self.body)
        self.assertIn("game.status.combat", self.body)
        # 显示 round / 当前行动 / 敌人 HP
        self.assertIn("round", self.body)
        self.assertIn("turn_index", self.body)
        self.assertIn("game.status.current_action", self.body)

    def test_recent_dice_log_section(self):
        # 最近裁定 — dice_log 末尾一条
        self.assertIn("dice_log", self.body)
        self.assertIn("最近裁定", self.body)

    def test_does_not_render_known_events(self):
        # 用户明确:'本轮已知事件'里出现的'遭遇灰布教徒'是 GM 口胡,
        # Module profile 不应渲染 world.known_events,避免 GM 写脏数据被前端 echo。
        self.assertNotIn("known_events", self.body,
            "Module profile 不应渲染 world.known_events;那是小说叙事字段,"
            "5E 模组 GM 写入未经 RulesEngine 裁定的事件会污染状态栏。")
        self.assertNotIn("本轮已知事件", self.body)


class NovelProfileBackwardCompat(unittest.TestCase):
    """NovelStatusProfile 保留旧 4 section 布局,小说存档观感不变。"""

    @classmethod
    def setUpClass(cls):
        cls.body = _extract_function(PANELS, "NovelStatusProfile")
        assert cls.body, "NovelStatusProfile 不存在"

    def test_keeps_legacy_four_sections(self):
        # section 标题已迁 i18n:玩家/当下世界/身上之物/本轮已知事件 → 对应 key
        for needle in ("game.status.player", "game.status.world_now",
                       "game.status.inventory", "game.status.known_events"):
            self.assertIn(needle, self.body,
                f"Novel profile 应保留 section: {needle}")

    def test_reads_player_and_world_fields(self):
        # 小说字段:player.{name,role,current_location,background} / world.{time,weather,known_events}
        for needle in ("p.name", "p.role", "p.current_location", "p.background",
                       "w.time", "w.weather", "w.known_events"):
            self.assertIn(needle, self.body,
                f"Novel profile 应读取 {needle}")


class CrossProfileSeparation(unittest.TestCase):
    """两 profile 之间互不污染。"""

    def test_module_profile_not_in_novel(self):
        # ModuleStatusProfile 的特征字段在 NovelStatusProfile 里不出现
        nov = _extract_function(PANELS, "NovelStatusProfile")
        # Novel profile 不应该出现 5E 战斗/资源/线索 section
        self.assertNotIn("冒险现场", nov)
        self.assertNotIn("最近裁定", nov)
        # ⚠️ visible_clues 是小说也可能有的字段,这里不强行禁止;
        # 但'可见线索'作为 section 标题不应在 novel profile 里
        self.assertNotIn("可见线索", nov)


if __name__ == "__main__":
    unittest.main(verbosity=2)
