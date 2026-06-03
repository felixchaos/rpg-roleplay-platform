"""_player_card 注入 GM 前必须剥掉 player.background 里的秘密段(穿越者身份/原著剧透),
与 short_summary 对称。原来直接用 raw background → 玩家秘密泄漏给 GM(NPC 可能说出"异界来客")。"""
import unittest

from context_engine.formatters import _player_card


class _FakeState:
    def __init__(self, data):
        self.data = data


class PlayerCardSecretStrip(unittest.TestCase):
    def test_secret_section_stripped_from_player_card(self):
        st = _FakeState({"player": {
            "name": "测试侠", "role": "冒险者",
            "background": "出身边境小镇,以剑为生。\n## 秘密\n我其实是穿越者,知道原著女主会死。",
        }})
        text = _player_card(st, {})["text"]
        self.assertNotIn("穿越者", text, "穿越者秘密泄漏进玩家卡 → GM 可见")
        self.assertNotIn("原著", text, "原著剧透泄漏进玩家卡")
        self.assertIn("出身边境小镇", text, "正常背景被误删")

    def test_normal_background_preserved(self):
        st = _FakeState({"player": {"name": "A", "background": "一个普通的背景。"}})
        text = _player_card(st, {})["text"]
        self.assertIn("普通的背景", text)

    def test_no_background_no_crash(self):
        st = _FakeState({"player": {"name": "B"}})
        out = _player_card(st, {})
        self.assertIn("B", out["text"])  # 不崩


if __name__ == "__main__":
    unittest.main()
