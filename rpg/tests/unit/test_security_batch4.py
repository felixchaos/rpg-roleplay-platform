"""test_security_batch4.py — 安全审计 Batch 4 回归测试(DoS / 越权 / 杂项)。

无 DB 依赖的纯逻辑校验:
- M-12: _t_set_preference 超大 value 在连库前即被拒(防 console_assistant 放大存储)。
- H-15: dispatcher 提供进程级 per-(user,save) 同步锁工厂(同 key 复用、可重入)。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class SetPreferenceValueCap(unittest.TestCase):
    def test_oversize_value_rejected_before_db(self):
        from tools_dsl.command_tools_misc import _t_set_preference
        r = _t_set_preference(1, {"key": "k", "value": "x" * 5000})
        self.assertIn("过大", r)

    def test_normal_value_passes_cap(self):
        # 不应被 cap 拦(可能因测试环境无 DB 而后续失败,但不应是 "value 过大")
        from tools_dsl.command_tools_misc import _t_set_preference
        r = _t_set_preference(1, {"key": "k", "value": "ok"})
        self.assertNotIn("value 过大", r)


class SyncScopeLock(unittest.TestCase):
    def test_same_key_returns_same_lock(self):
        from tools_dsl.command_dispatcher import _get_sync_scope_lock
        a = _get_sync_scope_lock((7, 100))
        b = _get_sync_scope_lock((7, 100))
        c = _get_sync_scope_lock((7, 101))
        self.assertIs(a, b)
        self.assertIsNot(a, c)

    def test_lock_is_reentrant(self):
        # RLock:同线程可重入,防工具执行器内再次 dispatch 同一存档时自死锁。
        from tools_dsl.command_dispatcher import _get_sync_scope_lock
        lk = _get_sync_scope_lock((9, 1))
        acquired = lk.acquire(blocking=False)
        self.assertTrue(acquired)
        try:
            self.assertTrue(lk.acquire(blocking=False))  # 重入
            lk.release()
        finally:
            lk.release()


if __name__ == "__main__":
    unittest.main()
