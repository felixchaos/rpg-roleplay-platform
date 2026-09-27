from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from platform_app import cluster  # noqa: E402


class _Cursor:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeDb:
    def __init__(self, row=None):
        self.queries = []
        self._row = row

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.queries.append((sql, params))
        return _Cursor(self._row if "select 1" in sql.lower() else None)


class StopSignalSafetyTest(unittest.TestCase):
    def test_request_stop_refreshes_existing_signal(self):
        db = _FakeDb()
        with mock.patch.object(cluster, "_ensure_stop_table"), mock.patch.object(cluster, "connect", return_value=db):
            cluster.request_stop(7, 123456789)

        sql, params = db.queries[0]
        self.assertIn("on conflict", sql.lower())
        self.assertIn("requested_at = now()", sql.lower())
        self.assertEqual(params, (7, 123456789))

    def test_is_stop_requested_ignores_stale_rows(self):
        db = _FakeDb(row=(1,))
        # 过期行清理按模块级 _last_stop_cleanup_at 节流(30s 一次):同进程里 30s 内跑过任何真实
        # /api/chat 回合(会调 is_stop_requested)的测试都会让这里跳过 DELETE。钉住它,别依赖测试顺序。
        with mock.patch.object(cluster, "_ensure_stop_table"), mock.patch.object(cluster, "connect", return_value=db), \
                mock.patch.object(cluster, "_last_stop_cleanup_at", float("-inf")):
            self.assertTrue(cluster.is_stop_requested(7, 123456789))

        self.assertEqual(len(db.queries), 2)
        delete_sql, delete_params = db.queries[0]
        select_sql, select_params = db.queries[1]
        self.assertIn("delete from stop_signals", delete_sql.lower())
        self.assertIn("requested_at <", delete_sql.lower())
        self.assertEqual(delete_params, (cluster.STOP_SIGNAL_MAX_AGE_SEC,))
        self.assertIn("requested_at >=", select_sql.lower())
        self.assertEqual(select_params, (7, 123456789, cluster.STOP_SIGNAL_MAX_AGE_SEC))


if __name__ == "__main__":
    unittest.main()
