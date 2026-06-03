"""同回合消耗多个不同物品:两层去重都不得把第二个物品丢掉。
(深审发现:_chat_rule_candidates 去重 key 不含 item_id + _apply 按 kind 去重 →
"点火把+喝药剂"只火把生效,药剂静默吞掉不扣量不回血。)"""
import re
import unittest
from pathlib import Path

APP_PY = (Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")


def _func(name: str) -> str:
    idx = APP_PY.find(f"def {name}(")
    assert idx != -1, name
    end = APP_PY.find("\ndef ", idx + 1)
    return APP_PY[idx: end if end != -1 else len(APP_PY)]


class MultiConsumeDedup(unittest.TestCase):
    def test_candidate_dedup_key_includes_item_id(self):
        # _chat_rule_candidates 的 add() 去重 key 必须含 item_id
        body = _func("_chat_rule_candidates")
        start = body.find("key = (")
        # tuple 闭合在自成一行的 8 空格缩进 ")"
        end = body.find("\n        )", start)
        key_block = body[start:end]
        self.assertIn('action.get("item_id")', key_block,
                      "去重 key 未含 item_id → 多个 consume_item 会坍缩,第二个被丢")

    def test_apply_dedup_per_item_for_consume(self):
        # _apply_chat_rule_candidates 对 consume_item 按 item_id 去重(而非按 kind)
        body = _func("_apply_chat_rule_candidates")
        self.assertTrue(
            re.search(r'consume_item:\{action\.get\([\'"]item_id[\'"]\)\}', body),
            "consume_item 仍按 kind 去重 → 同回合第二个物品被跳过",
        )
        # 其它 kind 仍是每回合一次(dedup_key 回退到 kind)
        self.assertIn("else kind", body, "非消耗动作应仍按 kind 每回合一次")

    def test_apply_no_longer_blanket_kind_block(self):
        # 不应再有 `kind in consumed_kinds` 这种把 consume_item 一刀切的旧逻辑
        body = _func("_apply_chat_rule_candidates")
        self.assertNotIn("kind not in allowed or kind in consumed_kinds", body,
                         "旧的按 kind 一刀切去重仍在,consume_item 第二个会被跳")


if __name__ == "__main__":
    unittest.main()
