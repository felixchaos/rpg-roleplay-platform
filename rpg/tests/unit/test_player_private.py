"""Unit tests for task 138: player_private namespace + secret strip.

覆盖 3 层:
1. short_summary 排除 state.player_private.* 字面 + 老 player.secrets
2. short_summary 调 _strip_secret_sections 剥离 ## 秘密 段
3. workspace._build_initial_snapshot 把 user_card.secrets / ## 秘密 段
   抽到 state.player_private.secrets, 原 player.personality 不再含该段
4. /reveal <text> ephemeral 注入 + record_turn 自动清空
"""
from __future__ import annotations

import unittest


class TestStripSecretSections(unittest.TestCase):
    """_strip_secret_sections / _extract_secret_sections 行为。"""

    def test_strip_basic_secret_section(self):
        from state.core import _strip_secret_sections
        text = "正常段\n\n## 秘密\nABC\n\n## 公开\nDEF"
        out = _strip_secret_sections(text)
        self.assertNotIn("ABC", out)
        self.assertIn("DEF", out)
        self.assertIn("正常段", out)

    def test_strip_multiple_keywords(self):
        from state.core import _strip_secret_sections
        for kw in ("秘密", "隐藏", "内心", "元知识", "真实身份", "来历", "背景秘密", "未公开"):
            text = f"# 头\n\n## {kw}\n绝对机密\n\n## 后续\n公开内容"
            out = _strip_secret_sections(text)
            self.assertNotIn("绝对机密", out, f"kw={kw}")
            self.assertIn("公开内容", out, f"kw={kw}")

    def test_strip_empty_or_none(self):
        from state.core import _strip_secret_sections
        self.assertEqual(_strip_secret_sections(""), "")
        self.assertEqual(_strip_secret_sections(None), "")
        # 没秘密段时原样返回(strip 后)
        self.assertEqual(_strip_secret_sections("纯文本"), "纯文本")

    def test_extract_secret_sections(self):
        from state.core import _extract_secret_sections
        text = "前\n\n## 秘密\nXXX\n\n## 普通\nYYY\n\n## 隐藏\nZZZ"
        out = _extract_secret_sections(text)
        # 应该抽到两段(秘密 + 隐藏),不抽 ## 普通
        self.assertEqual(len(out), 2)
        self.assertTrue(any("XXX" in s for s in out))
        self.assertTrue(any("ZZZ" in s for s in out))
        self.assertFalse(any("YYY" in s for s in out))


class TestShortSummaryExcludesPlayerPrivate(unittest.TestCase):
    """short_summary 必须排除 player_private.* 整个 namespace + 老 player.secrets。"""

    def _new_state(self):
        from state.core import GameState
        gs = GameState.new()
        gs.data["player"]["name"] = "测试玩家"
        gs.data["player"]["role"] = "测试角色"
        return gs

    def test_short_summary_excludes_player_private(self):
        gs = self._new_state()
        gs.data["player_private"]["secrets"] = ["秘密 X"]
        gs.data["player_private"]["hidden_traits"] = ["元身份 Y"]
        gs.data["player_private"]["story_intent"] = "我想 Z"
        gs.data["player_private"]["flags"]["meta_flag"] = "FLAG_VALUE"
        summary = gs.short_summary()
        self.assertNotIn("秘密 X", summary)
        self.assertNotIn("元身份 Y", summary)
        self.assertNotIn("我想 Z", summary)
        self.assertNotIn("FLAG_VALUE", summary)

    def test_short_summary_excludes_old_player_secrets(self):
        """老存档 player.secrets 字段(task 137 残留)也不应注入。"""
        gs = self._new_state()
        gs.data["player"]["secrets"] = "OLD_SECRET_FIELD"
        summary = gs.short_summary()
        self.assertNotIn("OLD_SECRET_FIELD", summary)

    def test_short_summary_excludes_user_variables_story_intent(self):
        """worldline.user_variables.story_intent 即使存在也不注入(改归 player_private)。"""
        gs = self._new_state()
        gs.data["worldline"]["user_variables"]["story_intent"] = {"value": "私密剧情意图"}
        gs.data["worldline"]["user_variables"]["公开变量"] = {"value": "OK_PUBLIC"}
        summary = gs.short_summary()
        self.assertNotIn("私密剧情意图", summary)
        # 公开变量应该正常显示
        self.assertIn("OK_PUBLIC", summary)


class TestShortSummaryStripsSecretSections(unittest.TestCase):
    """short_summary 注入 background/personality/appearance 前必须 strip ## 秘密 段。"""

    def _new_state(self):
        from state.core import GameState
        gs = GameState.new()
        gs.data["player"]["name"] = "测试"
        gs.data["player"]["role"] = "测试"
        return gs

    def test_short_summary_strips_secret_in_personality(self):
        gs = self._new_state()
        gs.data["player"]["personality"] = "外向开朗\n\n## 秘密\nABC_SECRET_PERSONALITY"
        summary = gs.short_summary()
        self.assertNotIn("ABC_SECRET_PERSONALITY", summary)
        # 公开部分仍在
        self.assertIn("外向开朗", summary)

    def test_short_summary_strips_secret_in_appearance(self):
        gs = self._new_state()
        gs.data["player"]["appearance"] = "黑发\n\n## 隐藏\nHIDDEN_APPEARANCE_DETAIL"
        summary = gs.short_summary()
        self.assertNotIn("HIDDEN_APPEARANCE_DETAIL", summary)
        self.assertIn("黑发", summary)

    def test_short_summary_strips_secret_in_background(self):
        gs = self._new_state()
        gs.data["player"]["background"] = "出生于北方\n\n## 真实身份\nTRUE_IDENTITY_X"
        summary = gs.short_summary()
        self.assertNotIn("TRUE_IDENTITY_X", summary)
        self.assertIn("出生于北方", summary)


class TestBuildInitialSnapshotExtractsSecrets(unittest.TestCase):
    """workspace._build_initial_snapshot 把 user_card.secrets / ## 秘密 段抽到 player_private。"""

    def test_extract_secret_sections_from_personality(self):
        """直接调 helper, 模拟卡片字段 → 抽取 + strip。"""
        from state.core import _extract_secret_sections, _strip_secret_sections
        personality_raw = "冷淡好奇\n\n## 秘密\n我是穿越者, 读过原著\n\n## 外观偏好\n喜欢黑色"
        hidden = _extract_secret_sections(personality_raw)
        stripped = _strip_secret_sections(personality_raw)
        self.assertTrue(any("穿越者" in h for h in hidden))
        self.assertNotIn("穿越者", stripped)
        self.assertIn("冷淡好奇", stripped)
        self.assertIn("外观偏好", stripped)

    def test_absorb_card_secrets_writes_to_private(self):
        """模拟 _absorb_card_secrets 行为: 角色卡 dict → state.player_private.secrets。
        因为 _absorb_card_secrets 是 workspace 内的闭包, 这里直接验证最终 state 形态。"""
        from state.core import GameState, _extract_secret_sections, _strip_secret_sections
        gs = GameState.new()
        # 模拟 workspace 入档逻辑
        card = {
            "name": "测试",
            "personality": "好奇\n\n## 秘密\nSEC_FROM_PERSONALITY",
            "secrets": "DIRECT_SECRET_FIELD",
            "appearance": "黑发\n\n## 隐藏\nHIDDEN_APP",
        }
        pp = gs.data.setdefault("player_private", {})
        secrets = pp.setdefault("secrets", [])
        # 抽 secrets 字段
        _sec_raw = str(card.get("secrets") or "").strip()
        if _sec_raw:
            secrets.append(_sec_raw)
        # 抽 personality / appearance 里的秘密段
        for _f in ("personality", "appearance"):
            _v = card.get(_f) or ""
            for _h in _extract_secret_sections(_v):
                if _h not in secrets:
                    secrets.append(_h)
            stripped = _strip_secret_sections(_v)
            if stripped:
                gs.data["player"][_f] = stripped
        # 验证结果
        self.assertIn("DIRECT_SECRET_FIELD", gs.data["player_private"]["secrets"])
        self.assertTrue(
            any("SEC_FROM_PERSONALITY" in s for s in gs.data["player_private"]["secrets"])
        )
        self.assertTrue(
            any("HIDDEN_APP" in s for s in gs.data["player_private"]["secrets"])
        )
        # 原 player.personality 不再含秘密段
        self.assertNotIn("SEC_FROM_PERSONALITY", gs.data["player"]["personality"])
        self.assertIn("好奇", gs.data["player"]["personality"])
        # short_summary 注入时秘密物理上不会出现
        summary = gs.short_summary()
        self.assertNotIn("DIRECT_SECRET_FIELD", summary)
        self.assertNotIn("SEC_FROM_PERSONALITY", summary)
        self.assertNotIn("HIDDEN_APP", summary)


class TestRevealCommand(unittest.TestCase):
    """/reveal <text> 命令: 本轮 ephemeral 注入 + record_turn 自动清空。"""

    def test_reveal_injects_into_summary(self):
        from state.core import GameState
        gs = GameState.new()
        gs.data["player"]["name"] = "测试"
        gs.data["player"]["role"] = "测试"
        gs.apply_player_directives("/reveal 我其实是穿越者")
        # /reveal 写入 flag
        self.assertEqual(
            gs.data["player_private"]["flags"].get("revealed_this_turn"),
            "我其实是穿越者",
        )
        # 同时累加到 secrets 历史
        self.assertIn("我其实是穿越者", gs.data["player_private"]["secrets"])
        # 本轮 short_summary 能看到
        summary = gs.short_summary()
        self.assertIn("我其实是穿越者", summary)
        self.assertIn("玩家本轮揭示", summary)

    def test_record_turn_clears_revealed(self):
        from state.core import GameState
        gs = GameState.new()
        gs.data["player"]["name"] = "测试"
        gs.data["player"]["role"] = "测试"
        gs.apply_player_directives("/reveal 一次性秘密")
        # GM 拿过 prompt 后 record_turn
        gs.record_turn("/reveal 一次性秘密", "GM 响应文本")
        # 下一轮 ephemeral 应该被清空
        self.assertFalse(gs.data["player_private"]["flags"].get("revealed_this_turn"))
        summary = gs.short_summary()
        self.assertNotIn("玩家本轮揭示", summary)
        self.assertNotIn("一次性秘密", summary)
        # secrets 历史仍然保留
        self.assertIn("一次性秘密", gs.data["player_private"]["secrets"])

    def test_reveal_entry_clears_stale_flag(self):
        """防御: 如果上一轮 record_turn 漏清(异常路径), 本轮入口先清。"""
        from state.core import GameState
        gs = GameState.new()
        gs.data["player"]["name"] = "测试"
        gs.data["player"]["role"] = "测试"
        # 模拟上一轮残留
        gs.data["player_private"]["flags"]["revealed_this_turn"] = "上轮残留 X"
        # 本轮玩家什么都没写
        gs.apply_player_directives("普通输入")
        self.assertFalse(gs.data["player_private"]["flags"].get("revealed_this_turn"))


class TestMigrationV6(unittest.TestCase):
    """v6 迁移函数把老 player.secrets / user_variables.story_intent 搬到 player_private。"""

    def test_migrate_old_player_secrets(self):
        from state.core import GameState
        old_state = {
            "schema_version": 5,
            "player": {"name": "X", "secrets": "OLD_SECRET"},
            "history": [],
        }
        gs = GameState(old_state)  # __init__ should call _migrate
        # 迁移后 player_private.secrets 应该包含 OLD_SECRET
        self.assertIn("OLD_SECRET", gs.data.get("player_private", {}).get("secrets", []))
        # schema 升级
        self.assertEqual(gs.data["schema_version"], 6)

    def test_migrate_story_intent(self):
        from state.core import GameState
        old_state = {
            "schema_version": 5,
            "worldline": {"user_variables": {"story_intent": {"value": "OLD_INTENT"}}},
            "history": [],
        }
        gs = GameState(old_state)
        self.assertEqual(
            gs.data.get("player_private", {}).get("story_intent"), "OLD_INTENT"
        )


if __name__ == "__main__":
    unittest.main()
