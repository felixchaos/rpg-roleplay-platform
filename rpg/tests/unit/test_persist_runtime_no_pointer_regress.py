"""持久化 runtime 状态(autosave)不得回退活跃指针 / 覆盖刚提交回合 — 并发竞态防护。

背景:`persist_runtime_state`(autosave,经 _persist_runtime_checkpoint 被多个 mutation
端点调用)与 `record_runtime_turn`(回合提交)可并发(多 tab)。若 autosave 用从 runtime 表
读来的可能滞后的 commit_id 回写 game_saves.active_commit_id,会在回合提交后回退活跃指针
N+1→N 并用过时 state 覆盖刚提交回合 → 用户丢回合。

修复:① persist_runtime_state 取与 record_runtime_turn 同 key 的 pg_advisory_xact_lock
串行化;② 以事务内 game_saves 当前活跃指针为权威,分歧时不回退指针/不覆盖快照。
本测试用源码断言锁定这两点(与本目录既有 grep 风格测试一致)。
"""
import re
import unittest
from pathlib import Path

RUNTIME_PY = (Path(__file__).resolve().parents[2] / "platform_app" / "branches" / "runtime.py").read_text(encoding="utf-8")


def _func_body(src: str, name: str) -> str:
    idx = src.find(f"def {name}(")
    assert idx != -1, f"未找到函数 {name}"
    end = src.find("\ndef ", idx + 1)
    return src[idx: end if end != -1 else len(src)]


class PersistRuntimeNoPointerRegress(unittest.TestCase):
    def setUp(self):
        self.body = _func_body(RUNTIME_PY, "persist_runtime_state")

    def test_acquires_advisory_lock(self):
        # 必须取事务级 advisory lock 串行化 autosave 与回合提交。锁已收敛为共享 helper
        # acquire_save_advisory_lock(_helpers.py,内部即 pg_advisory_xact_lock;
        # SQL/key 逐字断言见 test_branch_ops_advisory_lock.py)。
        self.assertIn("acquire_save_advisory_lock(", self.body,
                      "persist_runtime_state 缺 acquire_save_advisory_lock,无法与回合提交串行化")

    def test_lock_key_matches_record_runtime_turn(self):
        # lock key 必须与 record_runtime_turn 同,否则锁不互斥。key 单一真相在共享 helper 内,
        # 故这里断言:两个函数调的是**同一个 helper 且实参一致**(db, save_id, user_id)。
        call = "acquire_save_advisory_lock(db, save_id, user_id)"
        turn_body = _func_body(RUNTIME_PY, "record_runtime_turn")
        self.assertIn(call, turn_body, "record_runtime_turn 基准缺共享锁 helper 调用")
        self.assertIn(call, self.body,
                      "persist_runtime_state 未调同一共享锁 helper(或实参不一致),与回合提交锁不互斥")

    def test_uses_db_active_as_authority(self):
        # 必须以事务内 game_saves 的当前活跃指针为权威(读 save 的 active_commit_id)
        self.assertTrue(
            re.search(r"db_active\s*=\s*int\(\s*save\.get\(\s*[\"']active_commit_id", self.body),
            "persist_runtime_state 未以事务内 save.active_commit_id 为权威指针",
        )

    def test_no_regress_guard_present(self):
        # 分歧时(db_active != commit_id)必须改用 DB 真相,不回退指针/不覆盖快照
        self.assertIn("db_active != commit_id", self.body,
                      "缺分歧检测:db_active != commit_id")
        # 分歧分支里把 commit_id 校正回 db_active(不回退)
        self.assertTrue(
            re.search(r"db_active\s*!=\s*commit_id\s*:\s*\n\s*commit_id\s*=\s*db_active", self.body),
            "分歧分支未将 commit_id 校正为 db_active(指针仍可能回退)",
        )

    def test_advisory_lock_before_save_read(self):
        # 锁必须在读 save 之前获取,保证 save.active 在事务内稳定
        lock_pos = self.body.find("acquire_save_advisory_lock(")
        read_pos = self.body.find('select * from game_saves where id = %s')
        self.assertGreater(lock_pos, 0)
        self.assertGreater(read_pos, 0)
        self.assertLess(lock_pos, read_pos, "advisory lock 必须在读 game_saves 之前")


if __name__ == "__main__":
    unittest.main()
