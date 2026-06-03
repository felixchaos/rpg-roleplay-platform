"""世界树两处修复:
- collect_ids 必须防环(损坏 parent 图不得死循环 → 否则 delete_subtree worker 永挂)。
- resolve_commit_id_by_message 必须沿活跃 commit 血缘上溯(多分支下不得跨分支命中错节点)。
"""
import unittest
from pathlib import Path

from platform_app.branches import tree_ops

SRC = Path(tree_ops.__file__).read_text(encoding="utf-8")


class _CycleDB:
    """模拟损坏 parent 图:1→2→3→1 成环。execute(select id ... parent_id=%s) 返子节点。"""
    _children = {1: [2], 2: [3], 3: [1]}  # 环!

    def execute(self, sql, params=None):
        pid = params[0]
        rows = [{"id": c} for c in self._children.get(pid, [])]
        return _Result(rows)


class _Result:
    def __init__(self, rows):
        self._rows = rows
    def fetchall(self):
        return self._rows


class CollectIdsCycleGuard(unittest.TestCase):
    def test_terminates_on_cyclic_parent_graph(self):
        # 无 seen 集会无限循环;有 seen 则返回 {1,2,3} 并终止
        ids = tree_ops.collect_ids(_CycleDB(), 1)
        self.assertEqual(set(ids), {1, 2, 3})
        self.assertEqual(len(ids), len(set(ids)), "collect_ids 返回了重复 id(去重失效)")


class ResolveUsesLineage(unittest.TestCase):
    def test_resolve_walks_active_lineage(self):
        i = SRC.find("def resolve_commit_id_by_message(")
        end = SRC.find("\ndef ", i + 1)
        body = SRC[i:end]
        self.assertIn("with recursive lineage", body,
                      "resolve 未沿活跃 commit 血缘上溯 → 多分支下会跨分支命中错节点")
        self.assertIn("active_commit_id", body, "未读取活跃 commit 作为血缘起点")
        # 仍保留无活跃指针时的全 save 兜底(不返 None 阻断)
        self.assertIn("turn_index <= %s", body, "缺口兜底丢失")


if __name__ == "__main__":
    unittest.main()
