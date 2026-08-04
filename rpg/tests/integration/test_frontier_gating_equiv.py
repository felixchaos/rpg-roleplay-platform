"""test_frontier_gating_equiv.py — P4 前沿门控等价性回归(O 方案 temporal KB 统一)。

证明:flag on 时 reveal_clause_v2(save_id) 前沿门控 ≡ 旧标量 first_revealed_chapter<=progress
(对锚点范围内的实体),且 derived_progress_chapter 由前沿确定性派生。这是「切换前影子零 diff」的
单元级证明 —— 各收口点(S1 canon / S3 角色 / S4 世界书)都是把同一 reveal_clause_v2 嵌进各自 SQL,
故本等价性成立即可放心按 save 灰度。

需要本地 Postgres(与其它 integration 测试一致)。
"""
from __future__ import annotations

import os
import unittest

from psycopg.types.json import Jsonb

from tests.helpers import cleanup_test_users, make_client, register_user

_LAST_REACHED = 5      # 标记原著 ch1..ch5 锚点 occurred
_MAX_CH = 10           # chapter_facts 覆盖 ch1..ch10 → 10 条揭示锚点


class FrontierGatingEquiv(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()
        u = register_user(cls.client)
        from kb.reveal import backfill_entity_reveal_anchors, backfill_reveal_anchors, seed_frontier
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            cls.owner_id = int(db.execute(
                "select id from users where username=%s", (u["username"],)).fetchone()["id"])
            cls.book_id = int(db.execute(
                "insert into books(owner_id, slug, title) values (%s,%s,%s) returning id",
                (cls.owner_id, f"fg_book_{cls.owner_id}", "fg_book")).fetchone()["id"])
            cls.script_id = int(db.execute(
                "insert into scripts(owner_id, title) values (%s,%s) returning id",
                (cls.owner_id, "fg_script")).fetchone()["id"])
            cls.save_id = int(db.execute(
                "insert into game_saves(user_id, script_id, title, state_path) "
                "values (%s,%s,%s,%s) returning id",
                (cls.owner_id, cls.script_id, "fg_save",
                 f"/tmp/fg_save_{cls.owner_id}.json")).fetchone()["id"])

            # chapter_facts ch1..10,每章一个事件 → backfill 出 10 条揭示锚点 chapter:{n}:event:0
            for n in range(1, _MAX_CH + 1):
                db.execute(
                    "insert into chapter_facts(book_id, script_id, chapter, events) "
                    "values (%s,%s,%s,%s)",
                    (cls.book_id, cls.script_id, n,
                     Jsonb([{"event": f"第{n}章发生的关键事件", "importance": "high"}])),
                )
            # canon faction:frc 1/5/10(锚点范围内)+ 0(恒可见)
            from kb import canon_repo
            for lk, name, frc in (("f1", "势力1", 1), ("f5", "势力5", 5),
                                  ("f10", "势力10", 10), ("f0", "势力0", 0)):
                canon_repo.upsert_canon_entity(
                    db, cls.script_id, lk, name=name, type="faction",
                    first_revealed_chapter=frc, importance=80, entity_subtype="x")
            # partial 模式 famous 分支测试用:两个未来(frc=400)概念,一个 famous 一个普通。
            canon_repo.upsert_canon_entity(
                db, cls.script_id, "cfame", name="名概念", type="concept",
                first_revealed_chapter=400, importance=70, metadata={"famous": "true"})
            canon_repo.upsert_canon_entity(
                db, cls.script_id, "cplain", name="普通概念", type="concept",
                first_revealed_chapter=400, importance=70)
            # 角色卡 npc:同 frc 谱
            for name, frc in (("角色1", 1), ("角色5", 5), ("角色10", 10), ("角色0", 0)):
                db.execute(
                    "insert into character_cards(script_id, book_id, name, card_type, enabled, "
                    "first_revealed_chapter) values (%s,%s,%s,'npc',true,%s)",
                    (cls.script_id, cls.book_id, name, frc))
            # 世界书:同 frc 谱(旧无门控,S4 是新增门控)
            for title, frc in (("设定1", 1), ("设定5", 5), ("设定10", 10), ("设定0", 0)):
                db.execute(
                    "insert into worldbook_entries(script_id, book_id, title, content, enabled, "
                    "first_revealed_chapter) values (%s,%s,%s,%s,true,%s)",
                    (cls.script_id, cls.book_id, title, "内容", frc))
            # 标记 ch1..ch5 锚点 occurred(玩家已到达)
            for n in range(1, _LAST_REACHED + 1):
                db.execute(
                    "insert into save_anchor_states(save_id, script_id, anchor_key, source_chapter, "
                    "status, summary) values (%s,%s,%s,%s,'occurred',%s)",
                    (cls.save_id, cls.script_id, f"chapter:{n}:event:0", n, f"ch{n} 锚点"))

        # 回填揭示锚点 DAG + 实体映射 + 存档前沿(确定性 ETL)
        r1 = backfill_reveal_anchors(cls.script_id)
        assert r1["ok"] and r1["anchors"] == _MAX_CH, r1
        r2 = backfill_entity_reveal_anchors(cls.script_id)
        assert r2["ok"] and r2["total"] > 0, r2
        r3 = seed_frontier(cls.save_id)
        assert r3["ok"] and r3["visible"] == _LAST_REACHED, r3  # 闭包 = ch1..ch5

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()
        for k in ("RPG_TKB_FRONTIER", "RPG_TKB_FRONTIER_SHADOW", "RPG_TKB_FRONTIER_SAVES"):
            os.environ.pop(k, None)

    def _flag_on(self):
        os.environ["RPG_TKB_FRONTIER"] = "on"
        os.environ.pop("RPG_TKB_FRONTIER_SAVES", None)

    def _flag_off(self):
        os.environ["RPG_TKB_FRONTIER"] = "off"

    # ── 1. 派生进度 ────────────────────────────────────────────────────────────
    def test_derived_progress_equals_frontier_floor(self):
        from kb.reveal import derived_progress_chapter
        self.assertEqual(derived_progress_chapter(self.save_id), _LAST_REACHED)

    # ── 2. S1 canon:新前沿门控 ≡ 旧标量门控 ─────────────────────────────────────
    def test_canon_new_gating_equals_old(self):
        from kb import canon_repo
        from platform_app.db import connect
        with connect() as db:
            self._flag_off()
            old = {r["name"] for r in canon_repo.read_canon_entities(
                db, self.script_id, progress_chapter=_LAST_REACHED, mode="none",
                entity_type="faction")}
            self._flag_on()
            new = {r["name"] for r in canon_repo.read_canon_entities(
                db, self.script_id, progress_chapter=None, mode="none",
                entity_type="faction", save_id=self.save_id)}
        self.assertEqual(old, new, "前沿门控与旧标量门控不等价(canon)")
        self.assertEqual(new, {"势力1", "势力5", "势力0"})  # 势力10(ch10>5)被挡

    # ── 3. S3 角色卡:新 ≡ 旧 ────────────────────────────────────────────────────
    def test_characters_new_gating_equals_old(self):
        from context_engine.loaders import _load_characters_db
        self._flag_off()
        old = set(_load_characters_db(self.script_id, None, progress_chapter=_LAST_REACHED,
                                      foreknowledge_mode="none").keys())
        self._flag_on()
        new = set(_load_characters_db(self.script_id, None, progress_chapter=None,
                                      foreknowledge_mode="none", save_id=self.save_id).keys())
        self.assertEqual(old, new, "前沿门控与旧标量门控不等价(角色卡)")
        self.assertEqual(new, {"角色1", "角色5", "角色0"})

    # ── 4. NULL(frc=0)恒可见(I2 不变式) ──────────────────────────────────────
    def test_null_anchor_always_visible(self):
        from kb.reveal import reveal_clause_v2
        from platform_app.db import connect
        clause, params = reveal_clause_v2(self.save_id, "none", prefix="")
        with connect() as db:
            rows = db.execute(
                f"select logical_key from kb_canon_entities where script_id=%s and {clause}",
                (self.script_id, *params)).fetchall()
        keys = {r["logical_key"] for r in rows}
        self.assertIn("f0", keys, "reveal_anchor_key IS NULL 必须恒可见(等价旧 frc=0)")
        self.assertNotIn("f10", keys)

    # ── 5. S4 世界书:flag on 时新增门控挡掉未揭示条目(非等价,gap-fix) ──────────
    def test_worldbook_new_gate_hides_future(self):
        from context_engine.loaders import _load_worldbook_db
        self._flag_off()
        old = {e["title"] for e in _load_worldbook_db(self.script_id, None)}
        self._flag_on()
        new = {e["title"] for e in _load_worldbook_db(self.script_id, None,
                                                      save_id=self.save_id, mode="none")}
        self.assertEqual(old, {"设定1", "设定5", "设定10", "设定0"}, "旧路径应无门控(全集)")
        self.assertEqual(new, {"设定1", "设定5", "设定0"}, "新门控应挡掉 ch10 未揭示条目")
        self.assertTrue(new < old)

    # ── 6b. S1+审计修复:partial 模式 famous 分支 新 ≡ 旧 ───────────────────────
    def test_canon_partial_famous_equiv(self):
        from kb import canon_repo
        from platform_app.db import connect
        with connect() as db:
            self._flag_off()
            old = {r["name"] for r in canon_repo.read_canon_entities(
                db, self.script_id, progress_chapter=_LAST_REACHED, mode="partial",
                entity_type="concept")}
            self._flag_on()
            new = {r["name"] for r in canon_repo.read_canon_entities(
                db, self.script_id, progress_chapter=None, mode="partial",
                entity_type="concept", save_id=self.save_id)}
        self.assertEqual(old, new, "partial 模式 famous 分支 新≠旧(reveal_clause_v2 漏 famous)")
        self.assertIn("名概念", new, "famous=true 的未来概念在 partial 模式应可见")
        self.assertNotIn("普通概念", new, "非 famous 的未来概念在 partial 模式应被挡")

    # ── 6. omniscient 模式不门控(两路一致) ─────────────────────────────────────
    def test_omniscient_unfiltered(self):
        from kb import canon_repo
        from platform_app.db import connect
        self._flag_on()
        with connect() as db:
            rows = canon_repo.read_canon_entities(
                db, self.script_id, mode="omniscient", entity_type="faction",
                save_id=self.save_id)
        self.assertEqual({r["name"] for r in rows}, {"势力1", "势力5", "势力10", "势力0"})


class FrontierWritePath(unittest.TestCase):
    """P4 写路径(S6/S7):退役估章器(flag on 时不再 over-shoot)、GM 工具写前沿、rewind 收缩前沿。"""

    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()
        u = register_user(cls.client)
        from kb.reveal import backfill_reveal_anchors
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            cls.owner_id = int(db.execute(
                "select id from users where username=%s", (u["username"],)).fetchone()["id"])
            cls.book_id = int(db.execute(
                "insert into books(owner_id, slug, title) values (%s,%s,%s) returning id",
                (cls.owner_id, f"fw_book_{cls.owner_id}", "fw_book")).fetchone()["id"])
            cls.script_id = int(db.execute(
                "insert into scripts(owner_id, title) values (%s,%s) returning id",
                (cls.owner_id, "fw_script")).fetchone()["id"])
            for n in range(1, 11):
                db.execute(
                    "insert into chapter_facts(book_id, script_id, chapter, events) values (%s,%s,%s,%s)",
                    (cls.book_id, cls.script_id, n,
                     Jsonb([{"event": f"第{n}章关键事件发生", "importance": "high"}])))
        assert backfill_reveal_anchors(cls.script_id)["anchors"] == 10

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()
        for k in ("RPG_TKB_FRONTIER", "RPG_TKB_FRONTIER_SHADOW", "RPG_TKB_FRONTIER_SAVES"):
            os.environ.pop(k, None)

    def _seed_save(self, occurred_upto: int) -> int:
        """建档 + ch1..10 锚点(ch1..occurred_upto occurred,余 pending)+ seed_frontier。返回 save_id。"""
        from kb.reveal import seed_frontier
        from platform_app.db import connect
        with connect() as db:
            sid = int(db.execute(
                "insert into game_saves(user_id, script_id, title, state_path) "
                "values (%s,%s,%s,%s) returning id",
                (self.owner_id, self.script_id, "fw_save",
                 f"/tmp/fw_save_{self.owner_id}_{occurred_upto}.json")).fetchone()["id"])
            for n in range(1, 11):
                st = "occurred" if n <= occurred_upto else "pending"
                db.execute(
                    "insert into save_anchor_states(save_id, script_id, anchor_key, source_chapter, "
                    "status, summary) values (%s,%s,%s,%s,%s,%s)",
                    (sid, self.script_id, f"chapter:{n}:event:0", n, st, f"ch{n}"))
        seed_frontier(sid)
        return sid

    def _set_progress(self, save_id: int, ch: int):
        from psycopg.types.json import Jsonb

        from platform_app.db import connect
        with connect() as db:
            db.execute("insert into game_sessions(save_id, user_id, worldline) values (%s,%s,%s) "
                       "on conflict (save_id) do update set worldline=excluded.worldline",
                       (save_id, self.owner_id, Jsonb({"progress_chapter": ch})))

    def _read_progress(self, save_id: int):
        from platform_app.db import connect
        with connect() as db:
            r = db.execute("select worldline from game_sessions where save_id=%s", (save_id,)).fetchone()
        wl = (r or {}).get("worldline") if r else None
        return (wl or {}).get("progress_chapter") if isinstance(wl, dict) else None

    # ── 史官进度判断在前沿模式下【照常生效】(修正「确定性绕过三贤者」的回退)──────────────
    #   曾经:前沿启用时关掉史官估章,进度纯由「已到达锚点」派生 → 发散局(锚点命中不了)进度冻死
    #   (行者无疆 268 号档 turn 503 卡在第 7 章)。现恢复:史官估章/motion 照常跑,确定性只做有界护栏
    #   (clamp/pace_cap/单调),前沿退回只当揭示护栏(reached 地板防剧透)。
    def test_estimator_active_under_frontier(self):
        """核心契约:前沿模式【不再关掉史官估章】→ flag on 的进度推进 == flag off(史官照常生效、
        不被绕过),且比初始进度有前进(未冻)。此前 flag on 强制 est_on=False → 史官被绕过、发散冻死。
        (估章的确切落点依赖 chapter_facts 环境,故断等价而非固定数值。)"""
        from gm_serving.anchor_reconcile import reconcile_anchors_for_turn
        judge = lambda uid, text, pending, save_id=None: {"reached": [], "estimated_chapter": 9}

        os.environ["RPG_TKB_FRONTIER"] = "off"
        sid_off = self._seed_save(3)
        self._set_progress(sid_off, 1)
        reconcile_anchors_for_turn(sid_off, self.owner_id, "本回合剧情正文" * 20, _judge=judge)
        prog_off = self._read_progress(sid_off)

        os.environ["RPG_TKB_FRONTIER"] = "on"
        sid_on = self._seed_save(3)
        self._set_progress(sid_on, 1)
        reconcile_anchors_for_turn(sid_on, self.owner_id, "本回合剧情正文" * 20, _judge=judge)
        prog_on = self._read_progress(sid_on)

        self.assertEqual(prog_on, prog_off, "flag on 史官估章应与 flag off 一致(前沿不再绕过史官)")
        self.assertGreater(prog_on, 1, "史官估章在前沿模式下应推进进度(不冻死)")

    # ── S6.3:GM 工具 mark_anchor_satisfied 写前沿 ──────────────────────────────
    def test_mark_satisfied_writes_frontier(self):
        from kb.reveal import derived_progress_chapter
        from platform_app.db import connect
        from tools_dsl.command_tools_anchors import _t_mark_anchor_satisfied
        os.environ["RPG_TKB_FRONTIER"] = "on"
        sid = self._seed_save(3)
        self.assertEqual(derived_progress_chapter(sid), 3)
        out = _t_mark_anchor_satisfied(self.owner_id, {
            "save_id": sid, "anchor_key": "chapter:4:event:0",
            "how_it_happened": "玩家本回合到达第4章锚点", "occurred_at_turn": 1})
        self.assertIn('"ok": true', out)
        with connect() as db:
            n = db.execute("select count(*) c from save_reveal_frontier where save_id=%s "
                           "and anchor_key=%s", (sid, "chapter:4:event:0")).fetchone()["c"]
        self.assertEqual(n, 1, "GM 工具标记后前沿应含该锚点")
        self.assertEqual(derived_progress_chapter(sid), 4, "派生进度应随前沿推进到 4")

    # ── S7.2+审计修复 #4:read_settings 派生进度 floor 兜底 ─────────────────────
    def test_read_settings_floor(self):
        from psycopg.types.json import Jsonb

        from gm_serving.settings import read_settings
        from platform_app.db import connect
        os.environ["RPG_TKB_FRONTIER"] = "on"
        # (a) 前沿已种:read_settings 进度 == derived == 3
        sid = self._seed_save(3)
        self._set_progress(sid, 3)
        with connect() as db:
            self.assertEqual(read_settings(db, sid)["progress_chapter"], 3)
        # (b) 前沿【未种】+ worldline 标量被旧猜章器冲到 9:read_settings 应取「已确认锚点 floor=3」,
        #     既不坍缩到 1(derived),也不带回 over-shoot 的 9(legacy 标量)。
        with connect() as db:
            sid2 = int(db.execute(
                "insert into game_saves(user_id, script_id, title, state_path) "
                "values (%s,%s,%s,%s) returning id",
                (self.owner_id, self.script_id, "fw_noseed",
                 f"/tmp/fw_noseed_{self.owner_id}.json")).fetchone()["id"])
            for n in range(1, 4):
                db.execute(
                    "insert into save_anchor_states(save_id, script_id, anchor_key, source_chapter, "
                    "status, summary) values (%s,%s,%s,%s,'occurred',%s)",
                    (sid2, self.script_id, f"chapter:{n}:event:0", n, f"ch{n}"))
            db.execute("insert into game_sessions(save_id, user_id, worldline) values (%s,%s,%s)",
                       (sid2, self.owner_id, Jsonb({"progress_chapter": 9})))
        # 不调 seed_frontier → save_visible_anchors 空 → derived=1
        from kb.reveal import derived_progress_chapter
        self.assertEqual(derived_progress_chapter(sid2), 1, "前沿未种时 derived 应=1")
        with connect() as db:
            ps = read_settings(db, sid2)["progress_chapter"]
        # 修正(去确定性绕过):read_settings 现纳入史官进度(worldline 标量,由【有界】估章/pace 写入),
        # 取 max(floor=3, derived=1, 史官=9)=9。史官判断不再被钳到 floor;over-shoot 由【上游有界估章/
        # pace_cap/clamp】兜住,而非在这里无视史官(生产实测 frontier 档 pc 与 floor 差≤1,无旧过冲残留)。
        self.assertEqual(ps, 9, "read_settings 采纳史官进度=9(不再钳 floor;over-shoot 由上游有界护栏兜)")

    # ── S7.3:rewind 收缩前沿 → derived 下降 ────────────────────────────────────
    def test_rewind_shrinks_frontier(self):
        from kb.reveal import derived_progress_chapter, recompute_visible_set
        from platform_app.db import connect
        os.environ["RPG_TKB_FRONTIER"] = "on"
        sid = self._seed_save(5)
        self.assertEqual(derived_progress_chapter(sid), 5)
        # 复现 rewind 到 ch3 的前沿收缩(handler 内确定性逻辑):删 source_chapter>3 的前沿 + 重算
        target = 3
        with connect() as db:
            db.execute(
                "delete from save_reveal_frontier where save_id=%s and anchor_key in "
                "(select anchor_key from save_anchor_states where save_id=%s and source_chapter > %s)",
                (sid, sid, target))
            recompute_visible_set(db, sid, self.script_id)
        self.assertEqual(derived_progress_chapter(sid), 3, "rewind 后派生进度应降到 3")


if __name__ == "__main__":
    unittest.main()
