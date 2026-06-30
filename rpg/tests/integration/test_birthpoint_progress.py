"""出生点存档的进度信号回归测试(根因 #62/#63/#66/#67)。

历史 bug:玩家入场选「出生点」(从原著第 N 章开局)时,workspace 只把章节范围写进
state.world.timeline.anchor_chapter_range,却【没有】把它灌进进度信号
worldline.progress_chapter。后果:
  - retrieve_context 的 _progress_chapter 默认 1 → reveal 闸锁序章、ch2+ 角色被藏;
  - get_progress_window 退回 fallback [1,30] → pending 锚点窗口 / NPC 抽取 / ongoing 回合
    贴原著正文都按序章走 → 「选了出生点仍从序章开始 / 原著正文+对话消失」。

修复(两处确定性缝):
  A. workspace._build_initial_snapshot:出生点同时写 worldline.progress_chapter = chapter_min。
  B. anchor_seed_agent.get_progress_window:无 occurred 锚点时读 worldline.progress_chapter 作下限。

用 rpg_platform 真库 + integtest_ 用户隔离 + 即清理。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from psycopg.types.json import Jsonb  # noqa: E402

from platform_app.db import connect  # noqa: E402
from platform_app.db.init import init_db  # noqa: E402

_UNAME = "integtest_birthpoint_progress"


def _cleanup():
    with connect() as db:
        db.execute("delete from users where username = %s", (_UNAME,))


class BirthpointProgressTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        _cleanup()
        with connect() as db:
            cls.uid = int(db.execute(
                "insert into users(username, display_name, email) values (%s,%s,%s) returning id",
                (_UNAME, "bp", _UNAME + "@example.test"),
            ).fetchone()["id"])
            cls.script_id = int(db.execute(
                "insert into scripts(owner_id, title) values (%s,%s) returning id",
                (cls.uid, "birthpoint_progress_script"),
            ).fetchone()["id"])

    @classmethod
    def tearDownClass(cls):
        _cleanup()

    def _make_save(self, worldline: dict) -> int:
        """建一个最小存档 + game_sessions 行,返回 save_id。"""
        with connect() as db:
            save_id = int(db.execute(
                "insert into game_saves(user_id, script_id, title, state_path, state_snapshot) "
                "values (%s,%s,%s,%s,%s) returning id",
                (self.uid, self.script_id, "bp", "x", Jsonb({})),
            ).fetchone()["id"])
            db.execute(
                "insert into game_sessions(save_id, user_id, worldline) values (%s,%s,%s)",
                (save_id, self.uid, Jsonb(worldline)),
            )
        return save_id

    # ── Fix A ────────────────────────────────────────────────────────────
    def test_build_initial_snapshot_sets_progress_chapter_from_birthpoint(self):
        from platform_app import workspace
        bp = {"phase_label": "ph", "anchor_id": 1, "chapter_min": 50,
              "chapter_max": 80, "story_time_label": "第50章·测试时点"}
        snap = workspace._build_initial_snapshot(self.uid, self.script_id, None, None, birthpoint=bp)
        wl = (snap or {}).get("worldline", {}) or {}
        self.assertEqual(wl.get("progress_chapter"), 50,
                         "出生点 chapter_min 必须灌进 worldline.progress_chapter")
        # 旧行为(timeline 范围)保持
        acr = ((snap or {}).get("world", {}) or {}).get("timeline", {}).get("anchor_chapter_range")
        self.assertEqual(acr, [50, 80])

    def test_no_birthpoint_leaves_progress_chapter_unset(self):
        """无出生点的普通新档不得被塞进 bogus progress_chapter(防过度修正)。"""
        from platform_app import workspace
        snap = workspace._build_initial_snapshot(self.uid, self.script_id, None, None, birthpoint=None)
        wl = (snap or {}).get("worldline", {}) or {}
        self.assertIsNone(wl.get("progress_chapter"))

    # ── Fix B ────────────────────────────────────────────────────────────
    def test_get_progress_window_floors_at_progress_chapter(self):
        from agents.anchor_seed_agent import get_progress_window
        save_id = self._make_save({"progress_chapter": 50})
        pw = get_progress_window(save_id, world_time_label="", script_id=self.script_id, window_size=50)
        self.assertEqual(pw["chapter_min"], 50,
                         "无 occurred 锚点时进度窗口必须从 progress_chapter 起,而非 fallback[1,30]")
        self.assertEqual(pw["source"], "progress_chapter")
        self.assertEqual(pw["chapter_max"], 100)

    def test_get_progress_window_fallback_when_no_progress(self):
        """progress_chapter 缺失/=1 时仍回退剧本开头(不破坏既有行为)。"""
        from agents.anchor_seed_agent import get_progress_window
        save_id = self._make_save({})
        pw = get_progress_window(save_id, world_time_label="", script_id=self.script_id, window_size=50)
        self.assertEqual(pw["chapter_min"], 1)
        self.assertEqual(pw["source"], "fallback")


if __name__ == "__main__":
    unittest.main()
