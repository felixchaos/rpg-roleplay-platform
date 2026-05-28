"""
test_sync_job_jsonify.py — task 23 回归

确保 script_import._run_sync_job 把 sync_script_knowledge 返回里
含 datetime/Decimal/UUID/bytes 等 jsonb 不能直接吃的类型时，
import_jobs.usage_actual 仍能写入；status 是 done，不再因 TypeError 静默 failed。
"""
from __future__ import annotations

import datetime as _dt
import decimal as _dec
import json
import unittest
import uuid as _uuid
from unittest.mock import patch

from tests.helpers import cleanup_test_users, make_client, register_user


def _stub_sync_with_problem_types(user_id, script_id, rebuild=False):
    """模拟 knowledge.sync_script_knowledge 返回里嵌套 datetime / Decimal / UUID / bytes。
    包含一个 'book' 子树，其内部 'result.created_at' 是 datetime —— 即 task 23 实测崩点。
    """
    return {
        "documents": 3,
        "chunks": 12,
        "facts": 7,
        "characters": 2,
        "worldbook": 1,
        "book": {
            "id": 100,
            "title": "测试书",
            "result": {
                "created_at": _dt.datetime(2026, 5, 25, 13, 30, 0, tzinfo=_dt.UTC),
                "updated_at": _dt.date(2026, 5, 25),
                "cost": _dec.Decimal("0.42"),
                "uid": _uuid.UUID("00000000-0000-0000-0000-000000000042"),
                "raw_bytes": b"hello",
            },
        },
    }


class SyncJobJsonifies(unittest.TestCase):
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
            cls.user_id = int(row["id"])
            sc = db.execute(
                "insert into scripts(owner_id, title) values (%s, %s) returning id",
                (cls.user_id, "integtest_jsonify_script"),
            ).fetchone()
            cls.script_id = int(sc["id"])

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def setUp(self):
        from platform_app.db import connect
        with connect() as db:
            db.execute(
                "delete from import_jobs where user_id = %s and kind = 'knowledge_sync'",
                (self.user_id,),
            )

    def test_run_sync_job_serializes_datetime_result_without_typeerror(self):
        from platform_app import script_import
        from platform_app.db import connect

        # 1) schedule 一个 job（task 5 写好的 stub helper 防止真 LLM 跑起来）
        with patch.object(script_import._SYNC_POOL, "submit", return_value=None):
            job_id = script_import._schedule_knowledge_sync(self.user_id, self.script_id)

        # 2) 把 knowledge.sync_script_knowledge 替换成返 datetime/Decimal 的 stub
        from platform_app import knowledge
        with patch.object(knowledge, "sync_script_knowledge", side_effect=_stub_sync_with_problem_types):
            # 直接同步跑 _run_sync_job —— 走 claim → 调 stubbed sync → 写 usage_actual
            script_import._run_sync_job(job_id)

        # 3) DB 里 status 必须是 done，不能是 failed/pending
        with connect() as db:
            row = db.execute(
                "select status, error, usage_actual from import_jobs where job_id = %s",
                (job_id,),
            ).fetchone()
        self.assertIsNotNone(row, "job 行应存在")
        self.assertEqual(row["status"], "done",
                         f"task 23 修复后 status 必须 done；实际 {row['status']}，"
                         f"error={row['error']!r}")
        self.assertFalse(row["error"], f"error 应该为空：{row['error']!r}")

        # 4) usage_actual 里的 created_at 应被序列化成 ISO 字符串
        usage = row["usage_actual"]
        if isinstance(usage, str):
            usage = json.loads(usage)
        self.assertIsInstance(usage, dict)
        book_result = usage.get("result", {}).get("book", {}).get("result", {})
        self.assertIsInstance(book_result.get("created_at"), str)
        self.assertIn("2026-05-25", book_result["created_at"])
        self.assertEqual(book_result.get("updated_at"), "2026-05-25")
        self.assertEqual(book_result.get("cost"), 0.42)
        self.assertEqual(book_result.get("uid"), "00000000-0000-0000-0000-000000000042")
        self.assertEqual(book_result.get("raw_bytes"), "hello")

    def test_jsonify_unit_covers_problem_types(self):
        """_jsonify 单元测试，无需 DB"""
        from platform_app.script_import import _jsonify
        out = _jsonify({
            "dt": _dt.datetime(2026, 5, 25, 13, 30, 0),
            "d": _dt.date(2026, 5, 25),
            "td": _dt.timedelta(seconds=90),
            "dec": _dec.Decimal("3.14"),
            "uid": _uuid.UUID("11111111-1111-1111-1111-111111111111"),
            "by": b"abc",
            "nested": {"inner": [_dt.datetime(2025, 1, 1)]},
            "tup": (1, _dec.Decimal("2.5")),
            "set": {1, 2, 3},
            "scalar": "ok",
        })
        # 全部能 json.dumps 不抛
        s = json.dumps(out)
        self.assertIn("2026-05-25", s)
        self.assertIn("90.0", s)
        self.assertIn("3.14", s)
        self.assertIn("11111111", s)
        self.assertIn("abc", s)
        self.assertIn("2025-01-01", s)

    def test_jsonify_binary_unicode_safe(self):
        """非 utf-8 字节 fallback 到 base64"""
        from platform_app.script_import import _jsonify
        out = _jsonify({"b": b"\xff\xfe\xfd"})
        self.assertIn("__bytes_b64__", out["b"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
