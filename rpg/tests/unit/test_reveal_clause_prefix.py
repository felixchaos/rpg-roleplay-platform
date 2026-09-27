"""test_reveal_clause_prefix.py — BUG-2 building block.

canon_repo._reveal_clause 的 prefix 形参(retrieval.py 层级图 parent self-join 复用)
+ 防剧透方向铁律:
  - none 模式按 first_revealed_chapter <= progress 过滤,绝不放行后期实体;
  - progress=None / omniscient 不过滤(管理/全知视角);
  - prefix 给 self-join 别名表加列前缀,语义一致。
"""
from __future__ import annotations

import unittest

from kb.canon_repo import _reveal_clause


class RevealClausePrefix(unittest.TestCase):
    def test_none_mode_filters_by_progress(self):
        clause, params = _reveal_clause(10, "none")
        self.assertIn("first_revealed_chapter <= %s", clause)
        self.assertIn("public_knowledge", clause)
        # 铁律:没有任何 `is null` 放行口子
        self.assertNotIn("is null", clause.lower())
        self.assertEqual(params, [10])

    def test_prefix_qualifies_columns(self):
        clause, params = _reveal_clause(10, "none", prefix="p.")
        self.assertIn("p.first_revealed_chapter <= %s", clause)
        self.assertIn("p.public_knowledge", clause)
        # 不应残留裸列(否则 self-join 会指错表)
        self.assertNotIn("(first_revealed_chapter", clause)
        self.assertEqual(params, [10])

    def test_partial_adds_famous_with_prefix(self):
        clause, params = _reveal_clause(5, "partial", prefix="p.")
        self.assertIn("p.first_revealed_chapter <= %s", clause)
        self.assertIn("p.metadata->>'famous'", clause)
        self.assertEqual(params, [5])

    def test_omniscient_and_none_progress_dont_filter(self):
        self.assertEqual(_reveal_clause(10, "omniscient"), ("true", []))
        self.assertEqual(_reveal_clause(None, "none"), ("true", []))
        # prefix 不改变"不过滤"结果
        self.assertEqual(_reveal_clause(None, "none", prefix="p."), ("true", []))


if __name__ == "__main__":
    unittest.main()
