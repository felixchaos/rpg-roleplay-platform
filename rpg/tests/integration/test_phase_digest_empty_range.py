"""倒挂空段 / 无回合 phase:真库上验 SQL 行为(单测里用假库测不了的那部分)。

  1. find_pending 不挑倒挂行、不挑 digest_empty 终态行(显式 needs_rebuild 的除外);
     倒挂毒行再多也挤不掉真该重试的行。
  2. compact_phase 对 closed 空段标 digest_empty 终态、不调 LLM;open 行不标;
     之后补上回合、显式重摘成功会撤掉终态标记。
  3. 每回合钩子的真实顺序(ensure → update_turn_end → detect → open_new_phase):
     分支回退到开场后新分支 turn 1 标签一变,残留的旧 open phase 被就地改造,不关成 [1,0];
     正常换段仍关到 turn_index-1 并触发摘要与审计。

DB 走真实 Postgres,用户名前缀 integtest_,跑完清理。不调真实 LLM。
"""
from __future__ import annotations

import json
import unittest

from tests.helpers import cleanup_test_users, integtest_username


class _FakeBackend:
    def __init__(self):
        self.call_count = 0

    def call_structured(self, system, messages, max_tokens):
        self.call_count += 1
        return json.dumps({"summary": "玩家在码头打听消息,决定夜里再来。", "key_events": [],
                           "key_npcs": [], "key_locations": [], "key_decisions": [],
                           "emotion_arc": "好奇 → 决断"}, ensure_ascii=False)


class _State:
    def __init__(self, turn: int, current_phase: str):
        self.data = {"turn": turn, "world": {"timeline": {"current_phase": current_phase}}}


class PhaseDigestEmptyRangeDB(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    # ── 造数 ──────────────────────────────────────────────────

    def _save(self) -> tuple[int, int]:
        from platform_app.db import connect, init_db

        init_db()
        with connect() as db:
            uid = int(db.execute(
                "insert into users(username, display_name) values (%s, %s) returning id",
                (integtest_username(), "integ"),
            ).fetchone()["id"])
            script_id = int(db.execute(
                "insert into scripts(owner_id, title) values (%s, %s) returning id",
                (uid, "integtest_empty_range"),
            ).fetchone()["id"])
            save_id = int(db.execute(
                "insert into game_saves(user_id, script_id, title, state_path) "
                "values (%s, %s, %s, %s) returning id",
                (uid, script_id, "empty-range", "/tmp/_integtest_empty_range.json"),
            ).fetchone()["id"])
        return uid, save_id

    def _phase(self, save_id, idx, ts, te, *, status="closed", summary="", meta=None,
               label=""):
        from psycopg.types.json import Jsonb

        from platform_app.db import connect
        with connect() as db:
            db.execute(
                "insert into save_phase_digests(save_id, phase_index, turn_start, turn_end, "
                "phase_label, story_time_label, summary, status, generated_by, metadata) "
                "values (%s,%s,%s,%s,%s,'',%s,%s,'llm',%s)",
                (save_id, idx, ts, te, label, summary, status, Jsonb(meta or {})),
            )

    def _commits(self, save_id, turns):
        from platform_app.db import connect
        with connect() as db:
            for t in turns:
                db.execute(
                    "insert into branch_commits(save_id, object_hash, turn_index, kind, title, "
                    "player_input, gm_output) values (%s,%s,%s,'user',%s,%s,%s)",
                    (save_id, f"er_{save_id}_{t}", t, f"t{t}", f"玩家 turn {t}", f"GM turn {t}"),
                )

    def _rows(self, save_id) -> dict[int, dict]:
        from platform_app.db import connect
        with connect() as db:
            rows = db.execute(
                "select phase_index, turn_start, turn_end, status, phase_label, metadata "
                "from save_phase_digests where save_id = %s order by phase_index",
                (save_id,),
            ).fetchall()
        return {int(r["phase_index"]): dict(r) for r in rows}

    # ── 1. find_pending ───────────────────────────────────────

    def test_find_pending_skips_inverted_and_terminal_rows(self):
        from scripts.phase_digest_worker import find_pending

        _, sid = self._save()
        self._phase(sid, 0, 1, 0)                                          # 倒挂
        self._phase(sid, 1, 1, 5)                                          # 真待重试
        self._phase(sid, 2, 6, 9, meta={"digest_empty": True})            # 终态
        self._phase(sid, 3, 10, 12, meta={"digest_empty": True, "needs_rebuild": True})  # 显式重摘
        self._phase(sid, 4, 13, 12, meta={"needs_rebuild": True})         # 倒挂 + flagged
        picked = {int(p["phase_index"]) for p in find_pending(save_id=sid, limit=50)}
        self.assertEqual(picked, {1, 3})

    def test_inverted_rows_do_not_starve_real_retry(self):
        from scripts.phase_digest_worker import find_pending

        _, poisoned = self._save()
        for i in range(21):
            self._phase(poisoned, i, 1, 0, meta={"needs_rebuild": True})
        _, real = self._save()
        self._phase(real, 0, 1, 5, meta={"needs_rebuild": True})
        # 库里可能还有别的待重试行(共用测试库),所以不靠 LIMIT 20 断言:毒行一条都不进候选,
        # 就不可能占名额;真该重试的那条在候选里。
        picked = [(int(p["save_id"]), int(p["phase_index"]))
                  for p in find_pending(limit=100_000)]
        self.assertIn((real, 0), picked)
        self.assertFalse([p for p in picked if p[0] == poisoned],
                         "倒挂毒行还在候选里 —— cron 每轮按 save_id 升序取 20 条,会饿死真待重试行")

    # ── 2. compact_phase 终态标记 ─────────────────────────────

    def test_compact_marks_closed_empty_phase_terminal_and_success_clears_it(self):
        from agents.phase_digest_agent import compact_phase
        from scripts.phase_digest_worker import find_pending

        uid, sid = self._save()
        self._commits(sid, [1])                     # turn 1 有 commit,倒挂区间也不该去取
        self._phase(sid, 0, 1, 0, meta={"needs_rebuild": True})
        self._phase(sid, 1, 5, 8)                   # 区间正常但 commit 不在了
        self._phase(sid, 2, 9, 10, status="open")   # open 行:不标终态

        fake = _FakeBackend()
        for idx in (0, 1, 2):
            res = compact_phase(sid, idx, user_id=uid, force=True, _backend=fake)
            self.assertEqual(res.get("code"), "empty_range", res)
        self.assertEqual(fake.call_count, 0, "空段不该调 LLM")

        rows = self._rows(sid)
        for idx in (0, 1):
            meta = rows[idx]["metadata"] or {}
            self.assertIs(meta.get("digest_empty"), True)
            self.assertIs(meta.get("needs_rebuild"), False)
        self.assertNotIn("digest_empty", rows[2]["metadata"] or {})
        self.assertEqual(find_pending(save_id=sid, limit=50), [])

        # 补上回合、显式重摘(/phase rebuild 置 needs_rebuild)→ 重新被挑中,成功后撤掉终态
        self._commits(sid, [5, 6, 7, 8])
        from psycopg.types.json import Jsonb

        from platform_app.db import connect
        with connect() as db:
            db.execute(
                "update save_phase_digests set metadata = metadata || %s "
                "where save_id = %s and phase_index = 1",
                (Jsonb({"needs_rebuild": True}), sid),
            )
        self.assertEqual([int(p["phase_index"]) for p in find_pending(save_id=sid, limit=50)], [1])
        res = compact_phase(sid, 1, user_id=uid, force=True, _backend=fake)
        self.assertNotIn("error", res, res)
        meta = self._rows(sid)[1]["metadata"] or {}
        self.assertNotIn("digest_empty", meta)
        self.assertIs(meta.get("needs_rebuild"), False)

    # ── 3. 每回合钩子的真实顺序 ───────────────────────────────

    def _hook(self, sid, turn, label):
        """逐行镜像 app.py 回合钩子里 save_phase_manager 那一段。"""
        from save_phase_manager import (
            detect_phase_boundary,
            ensure_active_phase,
            open_new_phase,
            update_phase_turn_end,
        )
        ensure_active_phase(sid, turn, label, "")
        update_phase_turn_end(sid, turn)
        if detect_phase_boundary(sid, _State(turn, label)):
            open_new_phase(save_id=sid, turn_index=turn, phase_label=label, story_time_label="")

    def _patch_side_effects(self):
        import save_phase_manager as spm
        fired: list[int] = []
        audited: list[int] = []
        orig = (spm._fire_and_forget_compact, spm._audit_anchors_on_phase_close)
        spm._fire_and_forget_compact = lambda s, i: fired.append(i)
        spm._audit_anchors_on_phase_close = lambda s, i: audited.append(i)
        self.addCleanup(lambda: (setattr(spm, "_fire_and_forget_compact", orig[0]),
                                 setattr(spm, "_audit_anchors_on_phase_close", orig[1])))
        return fired, audited

    def test_branch_back_to_opening_does_not_close_empty_phase(self):
        fired, audited = self._patch_side_effects()
        _, sid = self._save()
        for t in (1, 2, 3):                       # 分支 A 打了 3 回合,标签「第一卷」
            self._hook(sid, t, "第一卷")
        self.assertEqual(self._rows(sid)[0]["turn_end"], 3)
        # continue_from 回开场(不修剪 phase),新分支 turn 1 标签翻成「玩家分支」
        self._hook(sid, 1, "玩家分支")
        rows = self._rows(sid)
        self.assertEqual(list(rows), [0], "不该多出一个 phase")
        self.assertEqual((rows[0]["turn_start"], rows[0]["turn_end"]), (1, 1))
        self.assertEqual(rows[0]["status"], "open")
        self.assertEqual(rows[0]["phase_label"], "玩家分支")
        self.assertEqual((fired, audited), ([], []), "空段不该触发摘要 / 审计")
        # 新分支继续打,phase 正常增长,下一回合不再误判换段
        self._hook(sid, 2, "玩家分支")
        rows = self._rows(sid)
        self.assertEqual((rows[0]["turn_start"], rows[0]["turn_end"]), (1, 2))
        self.assertFalse([r for r in rows.values() if r["turn_end"] < r["turn_start"]])

    def test_normal_label_switch_still_closes_and_digests(self):
        from platform_app.db import connect

        fired, audited = self._patch_side_effects()
        _, sid = self._save()
        for t in (1, 2, 3):
            self._hook(sid, t, "第一卷")
        self._hook(sid, 4, "第二卷")
        rows = self._rows(sid)
        self.assertEqual((rows[0]["turn_start"], rows[0]["turn_end"], rows[0]["status"]),
                         (1, 3, "closed"))
        self.assertEqual((rows[1]["turn_start"], rows[1]["turn_end"], rows[1]["status"]),
                         (4, 4, "open"))
        self.assertEqual((fired, audited), ([0], [0]))
        with connect() as db:
            api = db.execute("select active_phase_index from game_saves where id = %s",
                             (sid,)).fetchone()["active_phase_index"]
        self.assertEqual(api, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
