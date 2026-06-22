"""save_io 导入健壮性回归(v1.1.4 事故:本地存档导入网页端 500 / 只有剧本没存档 / 转圈网络异常)。

三个独立缺陷的回归保护:
  1. jsonb 列的【标量】值未包 Jsonb → 裸 int 塞 jsonb 列类型错 → 整行失败(kb_worldline_vars 全灭)。
  2. 单行插入失败无 savepoint → 失败语句把整个事务标 aborted → 下一表 _table_columns
     InFailedSqlTransaction → 500 → with connect() 回滚 → 整个存档丢失。
  3. COW commit 外键(born_commit 等)未随 branch_commits 重映射 → 旧 id 撞他档 commit → 孤儿行
     → materialize 祖先查询查空 → 导入存档加载为空。
"""
import unittest
from pathlib import Path

from psycopg.types.json import Jsonb

from platform_app.save_io import _build_insert

SRC = (Path(__file__).resolve().parents[2] / "platform_app" / "save_io.py").read_text(encoding="utf-8")


class JsonbValueWrap(unittest.TestCase):
    def test_scalar_value_for_jsonb_col_wrapped(self):
        allowed = frozenset({"save_id", "logical_key", "value", "born_commit"})
        _sql, vals = _build_insert("kb_worldline_vars", {"logical_key": "turn", "value": 5},
                                   1, allowed, frozenset({"value"}))
        self.assertTrue(any(isinstance(v, Jsonb) for v in vals), "jsonb 列标量必须包 Jsonb")
        self.assertNotIn(5, vals)

    def test_dict_value_still_wrapped(self):
        allowed = frozenset({"save_id", "logical_key", "value"})
        _sql, vals = _build_insert("kb_worldline_vars", {"logical_key": "player", "value": {"name": "x"}},
                                   1, allowed, frozenset({"value"}))
        self.assertTrue(any(isinstance(v, Jsonb) for v in vals))

    def test_none_is_sql_null_not_jsonb(self):
        allowed = frozenset({"save_id", "logical_key", "value"})
        _sql, vals = _build_insert("kb_worldline_vars", {"logical_key": "x", "value": None},
                                   1, allowed, frozenset({"value"}))
        self.assertIn(None, vals)
        self.assertFalse(any(isinstance(v, Jsonb) for v in vals), "None 应为 SQL NULL,非 Jsonb('null')")

    def test_non_jsonb_scalar_not_wrapped(self):
        # 普通列的标量不应被包(只有 jsonb 列才包)
        allowed = frozenset({"save_id", "anchor_key", "source_chapter"})
        _sql, vals = _build_insert("save_anchor_states", {"anchor_key": "a", "source_chapter": 3},
                                   1, allowed, frozenset())
        self.assertIn(3, vals)


class ImportRobustnessSource(unittest.TestCase):
    def test_per_row_savepoint(self):
        self.assertIn("with db.transaction():", SRC)

    def test_commit_fk_remapped(self):
        self.assertIn("born_commit", SRC)
        self.assertIn("old_to_new.get(int(row[_ck]))", SRC)

    def test_jsonb_columns_queried(self):
        self.assertIn("data_type = 'jsonb'", SRC)
        self.assertIn("jsonb_cols", SRC)

    def test_branch_refs_restored_from_payload(self):
        # #78: 导入须恢复 export 的所有 branch_refs(命名分支头),target 随 commit 重映射;
        # 不能再只硬造单个 refs/heads/main(否则选其余分支「继续」时从根另开新分支)。
        self.assertIn('payload.get("refs")', SRC)
        self.assertIn("on conflict (save_id, name)", SRC)
        # active_commit_id 须优先取 export 标 active 的 ref
        self.assertIn("active_commit_id", SRC)


if __name__ == "__main__":
    unittest.main()
