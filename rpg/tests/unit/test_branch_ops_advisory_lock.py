"""分支写操作(切换/激活/删子树/回滚)必须取与回合提交 / autosave 同 key 的
pg_advisory_xact_lock,否则多 tab 并发下会与 record_runtime_turn / persist_runtime_state
互相覆盖 game_saves 活跃指针 → 丢回合 / 回滚被冲掉。

与本目录 test_persist_runtime_no_pointer_regress.py 同风格:用源码断言锁定确定性代码缝
(锁的 key 表达式 + 在写 game_saves 之前调用),不依赖跑库。
"""
import re
import unittest
from pathlib import Path

BR = Path(__file__).resolve().parents[2] / "platform_app" / "branches"
HELPERS = (BR / "_helpers.py").read_text(encoding="utf-8")
ACTIVATION = (BR / "activation.py").read_text(encoding="utf-8")
DELETION = (BR / "deletion.py").read_text(encoding="utf-8")
RUNTIME = (BR / "runtime.py").read_text(encoding="utf-8")


def _func_body(src: str, name: str) -> str:
    idx = src.find(f"def {name}(")
    assert idx != -1, f"未找到函数 {name}"
    end = src.find("\ndef ", idx + 1)
    return src[idx: end if end != -1 else len(src)]


class BranchOpsAdvisoryLock(unittest.TestCase):
    def test_helper_exists_with_matching_key(self):
        body = _func_body(HELPERS, "acquire_save_advisory_lock")
        self.assertIn("pg_advisory_xact_lock", body)
        # key 表达式必须与 runtime.py 两处逐字一致,否则锁不互斥
        for token in ("rpg_turn_", "save_{save_id}", "save_id * 7919"):
            self.assertIn(token, body, f"helper lock key 缺 {token}")

    def test_helper_key_identical_to_runtime(self):
        # 锁 key 单一真相已收敛进共享 helper(runtime.py 的内联 SQL 重构为调 helper):
        # ① helper 本体仍是那句 SQL + 参数(逐字);② record_runtime_turn 与
        # persist_runtime_state 都必须调同一个 helper 且实参一致 —— 单一来源保证 key 互斥。
        helper = _func_body(HELPERS, "acquire_save_advisory_lock")
        sql = 'select pg_advisory_xact_lock(hashtext(%s)::int, hashtext(%s)::int)'
        self.assertIn(sql, helper)
        params = '(f"rpg_turn_{uid_for_lock}", f"save_{save_id}")'
        self.assertIn(params, helper)
        call = "acquire_save_advisory_lock(db, save_id, user_id)"
        for name in ("record_runtime_turn", "persist_runtime_state"):
            body = _func_body(RUNTIME, name)
            self.assertIn(call, body,
                          f"{name} 必须调共享锁 helper(同 key 才互斥),不得绕开或另写锁")

    def test_all_five_branch_writers_acquire_lock(self):
        cases = [
            (ACTIVATION, "continue_from"),
            (ACTIVATION, "activate_node"),
            (ACTIVATION, "activate_save"),
            (DELETION, "delete_subtree"),
            (DELETION, "rollback_to_message"),
        ]
        for src, name in cases:
            body = _func_body(src, name)
            self.assertIn(
                "acquire_save_advisory_lock(", body,
                f"{name} 未取 advisory lock,会与并发回合提交覆盖活跃指针",
            )

    def test_lock_before_set_save_active(self):
        # 锁必须在写 game_saves 活跃指针(_set_save_active)之前取
        for src, name in [
            (ACTIVATION, "continue_from"),
            (ACTIVATION, "activate_node"),
            (ACTIVATION, "activate_save"),
            (DELETION, "rollback_to_message"),
        ]:
            body = _func_body(src, name)
            lock_pos = body.find("acquire_save_advisory_lock(")
            set_pos = body.find("_set_save_active(")
            self.assertGreater(lock_pos, 0, f"{name} 无锁调用")
            self.assertGreater(set_pos, 0, f"{name} 无 _set_save_active")
            self.assertLess(lock_pos, set_pos, f"{name}: 锁必须在 _set_save_active 之前")

    def test_lock_before_game_saves_read_where_applicable(self):
        # activate_save / rollback_to_message 会读 game_saves 活跃指针决定行为,锁须在读之前
        for src, name in [(ACTIVATION, "activate_save"), (DELETION, "rollback_to_message")]:
            body = _func_body(src, name)
            lock_pos = body.find("acquire_save_advisory_lock(")
            read_pos = body.find("from game_saves where id = %s")
            self.assertGreater(lock_pos, 0)
            self.assertGreater(read_pos, 0)
            self.assertLess(lock_pos, read_pos, f"{name}: 锁必须在读 game_saves 之前")


if __name__ == "__main__":
    unittest.main()
