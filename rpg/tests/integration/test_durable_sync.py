"""
test_durable_sync.py — B5 验证拆书 / knowledge_sync 改 DB durable worker

覆盖：
- 新建任务后 DB 立刻有 import_jobs 行（kind='knowledge_sync', status='pending'）
- 同 (user, script) 再调一次 → 返回同一 job_id（去重）
- 同 user 第二个 script 会被限流（拒绝抛 ValueError）
- get_sync_status 从 DB 读
- B5 加固：
  * single-claim：两次 _claim_pending_job 只有一次成功
  * restart recovery：DB 留下 pending 行，重启后能被 recover_pending_sync_jobs 重新提交
  * stale running 回收：heartbeat 超时的 running 行被回退到 pending 重新跑
  * 唯一索引：并发插入 (user,script,kind)/pending 撞索引仍只剩一行
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.helpers import cleanup_test_users, make_client, register_user


class DurableKnowledgeSync(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()
        u = register_user(cls.client)
        from platform_app.db import connect
        with connect() as db:
            row = db.execute(
                "select id from users where username = %s", (u["username"],),
            ).fetchone()
            cls.owner_id = int(row["id"])
            r1 = db.execute(
                "insert into scripts(owner_id, title) values (%s, %s) returning id",
                (cls.owner_id, "integtest_script_1"),
            ).fetchone()
            cls.script_id_1 = int(r1["id"])
            r2 = db.execute(
                "insert into scripts(owner_id, title) values (%s, %s) returning id",
                (cls.owner_id, "integtest_script_2"),
            ).fetchone()
            cls.script_id_2 = int(r2["id"])

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def _stub_pool_submit(self):
        """阻止真实 _SYNC_POOL 跑（不连 LLM）"""
        from platform_app import script_import
        return patch.object(script_import._SYNC_POOL, "submit", return_value=None)

    def test_creates_db_row(self):
        from platform_app import script_import
        from platform_app.db import connect
        with self._stub_pool_submit():
            job_id = script_import._schedule_knowledge_sync(self.owner_id, self.script_id_1)
        self.assertTrue(job_id.startswith("ks_"))
        with connect() as db:
            row = db.execute(
                "select kind, status from import_jobs where job_id = %s", (job_id,),
            ).fetchone()
        self.assertEqual(row["kind"], "knowledge_sync")
        self.assertEqual(row["status"], "pending")

    def test_dedup_returns_same_job(self):
        from platform_app import script_import
        with self._stub_pool_submit():
            j1 = script_import._schedule_knowledge_sync(self.owner_id, self.script_id_1)
            j2 = script_import._schedule_knowledge_sync(self.owner_id, self.script_id_1)
        self.assertEqual(j1, j2, "同 user/script 第二次调度应返回同一 job_id")

    def test_throttle_across_scripts(self):
        from platform_app import script_import
        with self._stub_pool_submit():
            script_import._schedule_knowledge_sync(self.owner_id, self.script_id_1)
            with self.assertRaises(ValueError):
                script_import._schedule_knowledge_sync(self.owner_id, self.script_id_2)

    def test_status_reads_from_db(self):
        from platform_app import script_import
        with self._stub_pool_submit():
            script_import._schedule_knowledge_sync(self.owner_id, self.script_id_1)
        status = script_import.get_sync_status(self.owner_id, self.script_id_1)
        self.assertEqual(status["status"], "pending")
        self.assertEqual(status["script_id"], self.script_id_1)

    # ── B5 加固：原子领取 / 重启恢复 / stale 回收 / 唯一索引 ─────────
    def test_claim_pending_is_single_winner(self):
        """两次 _claim_pending_job 只有一次能拿到，模拟两 worker 抢同一 pending 任务。"""
        from platform_app import script_import
        with self._stub_pool_submit():
            job_id = script_import._schedule_knowledge_sync(self.owner_id, self.script_id_1)
        first = script_import._claim_pending_job(job_id)
        second = script_import._claim_pending_job(job_id)
        self.assertIsNotNone(first, "首次 claim 应成功")
        self.assertIsNone(second, "重复 claim 必须失败（已 running）")
        # DB 实际状态必须是 running
        from platform_app.db import connect
        with connect() as db:
            row = db.execute(
                "select status from import_jobs where job_id = %s", (job_id,),
            ).fetchone()
        self.assertEqual(row["status"], "running")

    def test_unique_index_blocks_concurrent_active(self):
        """唯一索引 uq_import_jobs_active_per_script 保证 (user,script,kind) pending/running 只能有一行。
        手工 INSERT 第二行模拟竞争插入，必须被 PG 拒绝。
        """
        from platform_app import script_import
        from platform_app.db import connect
        with self._stub_pool_submit():
            script_import._schedule_knowledge_sync(self.owner_id, self.script_id_1)
        import psycopg
        with self.assertRaises(psycopg.errors.UniqueViolation):
            with connect() as db:
                db.execute(
                    """
                    insert into import_jobs(job_id, user_id, script_id, kind, status, stage,
                                            stage_progress, stage_total, overall_progress, overall_total)
                    values ('ks_dup_test', %s, %s, 'knowledge_sync', 'pending', 'pending', 0, 1, 0, 1)
                    """,
                    (self.owner_id, self.script_id_1),
                )

    def test_recover_pending_resubmits_to_pool(self):
        """重启场景：DB 残留 pending → recover_pending_sync_jobs 把它丢回线程池。"""
        from platform_app import script_import
        # 模拟上次启动：DB 写了 pending，但 submit 没成功（用 stub 让 submit 不真跑）
        with self._stub_pool_submit():
            job_id = script_import._schedule_knowledge_sync(self.owner_id, self.script_id_1)

        # 现在重新跑 recover：submit 仍然 stub，只验证 resubmitted 包含 job_id
        submitted: list = []

        def _capture(fn, *args, **kwargs):
            submitted.append(args[0] if args else None)
            return None

        with patch.object(script_import._SYNC_POOL, "submit", side_effect=_capture):
            result = script_import.recover_pending_sync_jobs()
        self.assertIn(job_id, submitted, "recover 必须把 pending job 丢回线程池")
        self.assertGreaterEqual(result["recovered_pending"] + result["reclaimed_stale"], 1)
        self.assertIn(job_id, result["resubmitted"])

    def test_stale_running_is_reclaimed_back_to_pending(self):
        """running 且 heartbeat 超时 → recover 时回退到 pending 再丢回线程池。"""
        from platform_app import script_import
        from platform_app.db import connect
        # 手工建一个 running、heartbeat 在 1 小时前的脏行
        job_id = "ks_stale_test_aaaa"
        with connect() as db:
            db.execute(
                """
                insert into import_jobs(job_id, user_id, script_id, kind, status, stage,
                                        stage_progress, stage_total, overall_progress, overall_total,
                                        started_at, heartbeat_at)
                values (%s, %s, %s, 'knowledge_sync', 'running', 'running', 0, 1, 0, 1,
                        now() - interval '2 hours', now() - interval '2 hours')
                """,
                (job_id, self.owner_id, self.script_id_1),
            )

        submitted: list = []

        def _capture(fn, *args, **kwargs):
            submitted.append(args[0] if args else None)
            return None

        with patch.object(script_import._SYNC_POOL, "submit", side_effect=_capture):
            result = script_import.recover_pending_sync_jobs(stale_running_seconds=60)
        self.assertIn(job_id, result["stale_job_ids"], "stale running 应被识别")
        self.assertIn(job_id, submitted, "stale 行回退 pending 后必须重新提交")
        # 现在 DB 状态应该是 pending（recover 已回退）；线程池提交是 stub，不会真跑
        with connect() as db:
            row = db.execute(
                "select status from import_jobs where job_id = %s", (job_id,),
            ).fetchone()
        self.assertEqual(row["status"], "pending")

    def test_recover_is_idempotent(self):
        """同一 pending 行重复 recover 都会 submit，但 _claim_pending_job 保证只有第一个真跑。"""
        from platform_app import script_import
        with self._stub_pool_submit():
            job_id = script_import._schedule_knowledge_sync(self.owner_id, self.script_id_1)
        submitted: list = []

        def _capture(fn, *args, **kwargs):
            submitted.append(args[0] if args else None)
            return None

        with patch.object(script_import._SYNC_POOL, "submit", side_effect=_capture):
            script_import.recover_pending_sync_jobs()
            script_import.recover_pending_sync_jobs()
        # 两次 recover 都会再 submit 同一 pending 行，但只有一次 claim 能赢
        self.assertGreaterEqual(submitted.count(job_id), 1)

    def setUp(self):
        # 每个测试前清掉旧 import_jobs 避免互相干扰
        from platform_app.db import connect
        with connect() as db:
            db.execute(
                "delete from import_jobs where user_id = %s and kind = 'knowledge_sync'",
                (self.owner_id,),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
