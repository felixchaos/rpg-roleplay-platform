"""同一存档并发开段的真库串行化(READ COMMITTED 下的真实加锁行为,假库测不出来)。

场景:回合落库钩子与另一个标签页的 phase_advance 工具同时给同一个存档开段。A 已经在事务里
关掉旧 open 行 p0、插入新 open 行 p1 [T,T],还没提交;B 此时调 open_new_phase(T)。

只锁 open 行时:B 那条 select ... for update 的快照里 p1 不可见,等到 A 提交后重评 p0 发现已不是
open 被排除 → 读到「没有 open」走关段分支,随后的 UPDATE 却能看到 p1,按 T-1 把它关成倒挂
[T,T-1](还会对当前剧情段跑锚点审计)。先锁 game_saves 行后,B 在第一句就等 A 提交,之后再读
open 行,看到的就是 p1,走就地改造,不产生倒挂。
"""
from __future__ import annotations

import os
import threading
import time
import unittest
from unittest import mock

os.environ.setdefault("RPG_DEPLOYMENT_MODE", "local")


class TestPhaseOpenSerialization(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            db.execute("delete from users where username = %s", ("integtest_phase_serial",))
            uid = db.execute(
                "insert into users(username, display_name, password_hash, email) "
                "values (%s, %s, %s, %s) returning id",
                ("integtest_phase_serial", "Phase Serial", "x", "integtest_phase_serial@example.test"),
            ).fetchone()["id"]
            sid = db.execute(
                "insert into game_saves(user_id, title, state_path, save_kind) "
                "values (%s, %s, %s, 'tavern') returning id",
                (uid, "phase serial", ""),
            ).fetchone()["id"]
        cls.uid, cls.sid = int(uid), int(sid)

    @classmethod
    def tearDownClass(cls):
        from platform_app.db import connect
        with connect() as db:
            db.execute("delete from save_phase_digests where save_id = %s", (cls.sid,))
            db.execute("delete from game_saves where id = %s", (cls.sid,))
            db.execute("delete from users where id = %s", (cls.uid,))

    def setUp(self):
        from platform_app.db import connect
        with connect() as db:
            db.execute("delete from save_phase_digests where save_id = %s", (self.sid,))
            db.execute(
                "insert into save_phase_digests(save_id, phase_index, turn_start, turn_end, "
                "story_time_label, phase_label, summary, status, generated_by) "
                "values (%s, 0, 1, 10, '', 'a', '', 'open', 'llm')",
                (self.sid,),
            )
        import save_phase_manager as spm
        for attr in ("_fire_and_forget_compact", "_audit_anchors_on_phase_close"):
            p = mock.patch.object(spm, attr, lambda *a, **k: None)
            p.start()
            self.addCleanup(p.stop)

    def _rows(self):
        from platform_app.db import connect
        with connect() as db:
            return [dict(r) for r in db.execute(
                "select phase_index, turn_start, turn_end, status from save_phase_digests "
                "where save_id = %s order by phase_index", (self.sid,),
            ).fetchall()]

    def test_concurrent_open_does_not_invert_freshly_opened_phase(self):
        import save_phase_manager as spm
        from platform_app.db import connect

        a_locked = threading.Event()
        release_a = threading.Event()

        def opener_a():
            # A:与 open_new_phase 的正常换段同形,但在提交前停住,让 B 撞上来。
            with connect() as db:
                db.execute("select 1 from game_saves where id = %s for update", (self.sid,))
                db.execute("select phase_index from save_phase_digests where save_id = %s "
                           "and status = 'open' for update", (self.sid,))
                db.execute("update save_phase_digests set status = 'closed', turn_end = 10 "
                           "where save_id = %s and status = 'open'", (self.sid,))
                db.execute(
                    "insert into save_phase_digests(save_id, phase_index, turn_start, turn_end, "
                    "story_time_label, phase_label, summary, status, generated_by) "
                    "values (%s, 1, 11, 11, '', 'b', '', 'open', 'llm')",
                    (self.sid,),
                )
                a_locked.set()
                release_a.wait(10)

        ta = threading.Thread(target=opener_a)
        ta.start()
        self.assertTrue(a_locked.wait(10))
        result: dict = {}
        tb = threading.Thread(target=lambda: result.update(
            spm.open_new_phase(self.sid, turn_index=11, phase_label="b") or {}))
        tb.start()
        time.sleep(0.5)  # B 已经在等锁
        release_a.set()
        ta.join(10)
        tb.join(10)

        rows = self._rows()
        inverted = [r for r in rows if r["turn_end"] < r["turn_start"]]
        self.assertEqual(inverted, [], f"并发开段把刚开的 phase 关成了倒挂空段: {rows}")
        self.assertEqual([r["status"] for r in rows], ["closed", "open"])
        self.assertEqual((rows[1]["turn_start"], rows[1]["turn_end"]), (11, 11))

    def test_ensure_rechecks_open_phase_under_lock(self):
        """ensure_active_phase 锁外那次读看到「没有 open」,但读完到拿锁之间另一次开段已提交了
        一条 open 行 → 锁内复查到它,不再补开第二条(否则同一存档同时开着两条 open phase)。"""
        import save_phase_manager as spm
        from platform_app.db import connect
        with connect() as db:
            db.execute("update save_phase_digests set status = 'closed' where save_id = %s", (self.sid,))
            db.execute(
                "insert into save_phase_digests(save_id, phase_index, turn_start, turn_end, "
                "story_time_label, phase_label, summary, status, generated_by) "
                "values (%s, 1, 11, 11, '', 'b', '', 'open', 'llm')",
                (self.sid,),
            )
        with mock.patch.object(spm, "get_active_phase", lambda _sid: None):  # 锁外那次读(已过时)
            spm.ensure_active_phase(self.sid, 11, "b", "")
        opens = [r for r in self._rows() if r["status"] == "open"]
        self.assertEqual(len(opens), 1, f"同时开着两条 open phase: {self._rows()}")

if __name__ == "__main__":
    unittest.main()
