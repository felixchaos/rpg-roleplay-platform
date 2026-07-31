"""test_backfill_history_anchors.py — 补记脚本的取数口径(它要往真实存档写数据)。

补记必须和线上级联**同口径**:补出来的行与往后自然产生的行同形、同来源、同去重键。
口径错了就是往玩家存档里灌噪声,所以这里逐条锁:

  · 批量退役(角色死亡失效 / phase 关闭自动绕过)不补 —— 那是「不再可能发生」,
    不是「发生了什么」,线上级联同样豁免
  · source 按当初是谁标的还原(系统兜底 / 玩家面板 / GM 工具)
  · 取数 SQL 自带 not exists 去重,且按发生回合升序(补出来的时间线才是顺的)
  · 默认 dry-run:不给 --apply 一行都不写
"""
from __future__ import annotations

import os
import pathlib
import sys
import unittest
from unittest.mock import MagicMock, patch

_RPG = pathlib.Path(__file__).resolve().parents[2]
if str(_RPG) not in sys.path:
    sys.path.insert(0, str(_RPG))

os.environ.setdefault("RPG_REQUIRE_AUTH", "0")

from scripts import backfill_history_anchors as B  # noqa: E402

_SYS = "系统每回合确定性兜底判定:本回合剧情明确到达此锚点"
_DEATH = "角色「张三」已死亡 —— 仅其单人参与的未来锚点不再可能发生(非死神来了)"
_PHASE = "phase '第一卷' 已结束 (turn 88),玩家已推进过第 12 章,本 phase 中更早未触发的锚点自动绕过"
_PLAYER = "玩家在世界线面板手动标记已到达"
_GM = "主角提前两章在码头与B相遇,而非车站"


class TestBulkRetirementExcluded(unittest.TestCase):
    def test_death_and_phase_bypass_are_not_backfilled(self):
        self.assertTrue(B._is_bulk_retirement(_DEATH))
        self.assertTrue(B._is_bulk_retirement(_PHASE))

    def test_real_events_are_backfilled(self):
        for d in (_SYS, _PLAYER, _GM, ""):
            self.assertFalse(B._is_bulk_retirement(d), d)


class TestSourceAttribution(unittest.TestCase):
    def test_maps_description_to_original_writer(self):
        self.assertEqual(B._source_of(_SYS), "system")
        self.assertEqual(B._source_of(_PLAYER), "player_declared")
        self.assertEqual(B._source_of(_GM), "gm_generated")
        self.assertEqual(B._source_of(""), "gm_generated")


class TestCollect(unittest.TestCase):
    def _db(self, rows):
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = rows
        return db

    def test_filters_bulk_retirement_out_of_the_result(self):
        db = self._db([
            {"anchor_key": "k1", "status": "occurred", "summary": "s", "variant_description": _SYS,
             "occurred_at_turn": 10, "updated_at": None},
            {"anchor_key": "k2", "status": "superseded", "summary": "s", "variant_description": _DEATH,
             "occurred_at_turn": 11, "updated_at": None},
            {"anchor_key": "k3", "status": "superseded", "summary": "s", "variant_description": _PHASE,
             "occurred_at_turn": 12, "updated_at": None},
        ])
        got = B.collect(db, 1)
        self.assertEqual([r["anchor_key"] for r in got], ["k1"])

    def test_query_dedupes_and_orders_by_turn(self):
        db = self._db([])
        B.collect(db, 1)
        sql = db.execute.call_args.args[0]
        self.assertIn("not exists", sql)
        self.assertIn("linked_pending_anchors @>", sql)   # 与线上级联同一把去重键
        self.assertIn("order by coalesce(s.occurred_at_turn, 0)", sql)
        # 只取已脱离 pending 的:还没发生的锚点补进「已发生的历史」= 剧透 + 记忆污染
        self.assertIn("status in ('occurred', 'variant', 'superseded')", sql)


class TestDryRunByDefault(unittest.TestCase):
    def _run(self, argv):
        rows = [{"anchor_key": "k1", "status": "variant", "summary": "s",
                 "variant_description": _SYS, "occurred_at_turn": 9, "updated_at": None}]
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = rows
        db.__enter__ = lambda s: db
        db.__exit__ = MagicMock(return_value=False)
        summary = {"total": 2, "gm_count": 0, "player_count": 0, "system_count": 2,
                   "max_importance": 60, "last_turn": 12}
        with patch("platform_app.db.connect", return_value=db), \
             patch("platform_app.db.init_db"), \
             patch("agents.save_history.history_summary", return_value=summary), \
             patch("agents.save_history.cascade_history_from_anchor") as casc:
            B.main(argv)
        return casc

    def test_without_apply_nothing_is_written(self):
        self._run(["--save-id", "1"]).assert_not_called()

    def test_with_apply_uses_the_shared_seam_and_tags_the_rows(self):
        casc = self._run(["--save-id", "1", "--apply"])
        casc.assert_called_once()
        kw = casc.call_args.kwargs
        self.assertEqual(kw["source"], "system")       # 还原当初是谁标的
        self.assertEqual(kw["via"], "backfill")        # 可精确撤回
        self.assertEqual(kw["extra_metadata"], {"backfill": True})
        self.assertEqual(kw["turn_occurred"], 9)       # 用当初的回合,时间线才排得对
        self.assertIsNotNone(kw["db"])                 # 复用连接,别叠连接


class TestReversibility(unittest.TestCase):
    def test_script_prints_the_undo_statement(self):
        src = pathlib.Path(B.__file__).read_text(encoding="utf-8")
        self.assertIn("delete from save_history_anchors", src)
        self.assertIn("metadata->>'backfill'", src)


if __name__ == "__main__":
    unittest.main()
