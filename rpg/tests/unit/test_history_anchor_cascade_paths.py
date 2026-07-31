"""test_history_anchor_cascade_paths.py — 历史锚点级联挂在哪条路上(07-31 群反馈)。

反馈:「之前反馈过这个问题好像还没修,存档永远只有 2 个锚点」。

查下来是**级联挂在了不跑的那条路上**:v1.73.0 把「锚点脱离 pending → 补写历史锚点」
挂在 GM 工具 `mark_anchor_*` 上,可真正在标锚点的是 `anchor_reconcile` 的每回合确定性
兜底判定 —— 它先把 pending 确定性地标掉,GM 再没有可标的东西,于是级联一次都没跑过,
玩家看到的时间线永远停在建档初期那两条 phase 浓缩。

本测试锁的就是这条教训:**状态迁移的每个写入方都必须走同一条留档缝**,新增写入方
不接线就红。豁免的两类(批量退役)在下面白名单里写明理由。
"""
from __future__ import annotations

import ast
import os
import pathlib
import sys
import unittest
from unittest.mock import MagicMock, patch

_RPG = pathlib.Path(__file__).resolve().parents[2]   # …/rpg —— import 根
REPO = _RPG.parent                                   # 仓库根(_src 的相对起点)
if str(_RPG) not in sys.path:
    sys.path.insert(0, str(_RPG))

os.environ.setdefault("RPG_REQUIRE_AUTH", "0")

from agents import save_history as sh  # noqa: E402

_SEAM = "cascade_history_from_anchor"


def _src(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def _fn_calls(source: str, fn_name: str) -> set[str]:
    """函数体里**真实调用**的名字(AST,不是字符串匹配 —— 注释里提到不算接线)。"""
    tree = ast.parse(source)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fn_name)
    return {getattr(c.func, "id", None) or getattr(c.func, "attr", "")
            for c in ast.walk(fn) if isinstance(c, ast.Call)}


class TestEveryTransitionWriterRecordsHistory(unittest.TestCase):
    """把锚点标成 occurred/variant/superseded 的写入方,必须补写历史锚点。"""

    def test_reconciler_cascades(self):
        """真正在跑的那条路 —— 漏了它,整个功能等于没有。"""
        self.assertIn(_SEAM, _fn_calls(_src("rpg/gm_serving/anchor_reconcile.py"), "_apply_hits"))

    def test_gm_tool_paths_cascade(self):
        src = _src("rpg/tools_dsl/command_tools_anchors.py")
        for fn in ("_t_mark_anchor_satisfied", "_t_mark_anchor_superseded"):
            self.assertIn("_cascade_history_from_anchor", _fn_calls(src, fn), fn)
        # 工具侧只是薄委托,真身在 save_history(单一缝)
        self.assertIn(_SEAM, _fn_calls(src, "_cascade_history_from_anchor"))

    def test_player_manual_mark_cascades(self):
        """玩家在世界线面板手动标记 —— 唯一能让「玩家声明 N」不恒为 0 的入口。"""
        src = _src("rpg/platform_app/api/saves.py")
        self.assertIn(_SEAM, src)
        self.assertIn('source="player_declared"', src)

    def test_bulk_retirement_writers_are_deliberately_exempt(self):
        """豁免:这两类是**批量退役/失效**,不是「发生了什么」,记进玩家历史只会刷屏。

        · anchor_reconcile 的角色死亡失效(一次退役几十个单人锚点)
        · save_phase_manager 的 phase 关闭自动绕过(同上,按 phase 批量)

        这条测试存在的意义是:哪天有人想给它们接级联,先看见这行理由。
        """
        self.assertIn("死亡", _src("rpg/gm_serving/anchor_reconcile.py"))
        self.assertIn("自动绕过", _src("rpg/save_phase_manager.py"))


class TestCascadeReusesCallerConnection(unittest.TestCase):
    def test_passing_db_opens_no_new_connection(self):
        """调用方已持连接时再开新连接 = PgBouncer 上叠连接(有前科)。"""
        db = MagicMock()
        db.execute.return_value.fetchone.return_value = None  # 无重复 + turn 查询
        with patch("platform_app.db.connect") as conn:
            sh.cascade_history_from_anchor(
                7, anchor_key="k1", anchor_summary="锚点摘要", new_status="occurred",
                detail="怎么发生的", turn_occurred=12, source="system", db=db)
        conn.assert_not_called()
        sqls = " ".join(str(c.args[0]) for c in db.execute.call_args_list)
        self.assertIn("insert into save_history_anchors", sqls)

    def test_duplicate_anchor_key_is_not_double_written(self):
        """GM 已手动 record_history_anchor 并关联过该锚点 → 不再重复补写。"""
        db = MagicMock()
        db.execute.return_value.fetchone.return_value = {"1": 1}  # dup 命中
        sh.cascade_history_from_anchor(
            7, anchor_key="k1", anchor_summary="x", new_status="occurred", db=db)
        sqls = " ".join(str(c.args[0]) for c in db.execute.call_args_list)
        self.assertNotIn("insert into save_history_anchors", sqls)

    def test_never_raises(self):
        """级联失败绝不许影响锚点标记本身。"""
        db = MagicMock()
        db.execute.side_effect = RuntimeError("DB 炸了")
        sh.cascade_history_from_anchor(7, anchor_key="k", anchor_summary="x",
                                       new_status="occurred", db=db)  # 不抛即通过

    def test_empty_key_is_a_noop(self):
        db = MagicMock()
        sh.cascade_history_from_anchor(7, anchor_key="  ", anchor_summary="x",
                                       new_status="occurred", db=db)
        db.execute.assert_not_called()


class TestImportanceMapping(unittest.TestCase):
    def test_status_to_importance(self):
        """阈值语义来自 record_history_anchor 的文档串,别随手改。"""
        self.assertEqual(sh._CASCADE_IMPORTANCE["superseded"], 80)
        self.assertEqual(sh._CASCADE_IMPORTANCE["variant"], 70)
        self.assertEqual(sh._CASCADE_IMPORTANCE["occurred"], 60)

    def test_written_importance_follows_status(self):
        db = MagicMock()
        db.execute.return_value.fetchone.return_value = None
        with patch.object(sh, "record_history_anchor") as rec:
            sh.cascade_history_from_anchor(7, anchor_key="k", anchor_summary="s",
                                           new_status="superseded", db=db)
        self.assertEqual(rec.call_args.kwargs["importance"], 80)
        self.assertEqual(rec.call_args.kwargs["linked_pending_anchors"], ["k"])
        self.assertIs(rec.call_args.kwargs["db"], db)


class TestPanelCounters(unittest.TestCase):
    """面板此前只报 GM/玩家两格,系统写的几十条一条不算 → 显示「0 / 0」像坏了。"""

    def test_summary_counts_system_rows(self):
        self.assertIn("system_count", _src("rpg/agents/save_history.py"))
        sql_region = _src("rpg/agents/save_history.py")
        self.assertIn("source not in ('gm_generated', 'player_declared')", sql_region)

    def test_injected_line_shows_system_count(self):
        line_src = _src("rpg/retrieval/assemble.py")
        self.assertIn("系统判定", line_src)
        self.assertIn("system_count", line_src)


class TestInsertSeamIsShared(unittest.TestCase):
    def test_both_connection_modes_use_one_insert(self):
        """自带连接 / 复用连接两条路共用 _insert_anchor_row,别写第二份 INSERT。"""
        src = _src("rpg/agents/save_history.py")
        self.assertEqual(src.count("insert into save_history_anchors"), 1)
        self.assertIn("_insert_anchor_row", _fn_calls(src, "record_history_anchor"))


if __name__ == "__main__":
    unittest.main()
