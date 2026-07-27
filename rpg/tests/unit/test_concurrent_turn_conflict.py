"""双端并发状态覆盖(P0)+ message/edit 无锁(P1)确定性代码缝守卫。

与 test_branch_ops_advisory_lock.py / test_persist_runtime_no_pointer_regress.py 同风格:
源码断言锁定确定性代码缝(并发路径难在单测里真并发,故守卫「拒绝分支存在 + 假 rebase 已删 +
锁在改写前」),不依赖跑库。
"""
import re
import unittest
from pathlib import Path

RPG = Path(__file__).resolve().parents[2]
RUNTIME = (RPG / "platform_app" / "branches" / "runtime.py").read_text(encoding="utf-8")
APP = (RPG / "app.py").read_text(encoding="utf-8")
CHAT = (RPG / "routes" / "game" / "chat.py").read_text(encoding="utf-8")
OPENING = (RPG / "routes" / "game" / "opening.py").read_text(encoding="utf-8")
SAVES = (RPG / "routes" / "game" / "saves.py").read_text(encoding="utf-8")


def _func_body(src: str, name: str) -> str:
    idx = src.find(f"def {name}(")
    assert idx != -1, f"未找到函数 {name}"
    end = src.find("\ndef ", idx + 1)
    return src[idx: end if end != -1 else len(src)]


class RecordRuntimeTurnRejectsOnDrift(unittest.TestCase):
    """Fix 1 核心:record_runtime_turn 检测活跃指针漂移时【拒绝】而非【假 rebase 后旧快照覆盖】。"""

    def setUp(self):
        self.body = _func_body(RUNTIME, "record_runtime_turn")

    def test_conflict_return_present(self):
        # 漂移 → 返回明确 conflict 信号(整回合原子放弃)
        self.assertIn('"conflict": True', self.body,
                      "record_runtime_turn 漂移时必须返回 conflict 信号")
        self.assertIn("fresh_active != parent_id", self.body)

    def test_fake_rebase_removed(self):
        # 旧「假 rebase」逻辑(把 parent 换成最新 commit 后仍用旧 state_snapshot 覆盖)必须删除,
        # 否则后写者全量快照抹掉前写者回合(库尸检:同 (save,turn,assistant) 重复 90 组)。
        self.assertNotIn("parent = fresh_parent", self.body,
                         "假 rebase 未删:漂移仍会用旧基线快照整体覆盖 → 丢回合")

    def test_reject_before_insert_commit(self):
        # 拒绝必须早于 _insert_commit(不落 commit/kb_events),才是原子放弃
        conflict_pos = self.body.find('"conflict": True')
        insert_pos = self.body.find("_insert_commit(")
        self.assertGreater(conflict_pos, 0)
        self.assertGreater(insert_pos, 0)
        self.assertLess(conflict_pos, insert_pos,
                        "conflict 拒绝必须在 _insert_commit 之前(否则已落 commit,非原子放弃)")

    def test_advisory_lock_still_present(self):
        # 不能因本次改动丢掉既有 advisory lock(test_branch_ops_advisory_lock 的孪生约束)
        self.assertIn("acquire_save_advisory_lock(db, save_id, user_id)", self.body)


class ConflictPropagatesToSSE(unittest.TestCase):
    """Fix 1 传播链:record_runtime_turn(conflict) → _persist_chat_turn 抛 →
    chat SSE 层转 save_conflict 错误事件;opening 同面接住。"""

    def test_persist_chat_turn_raises_on_conflict(self):
        body = _func_body(APP, "_persist_chat_turn")
        self.assertIn('.get("conflict")', body, "_persist_chat_turn 未检测 conflict 返回")
        self.assertIn("RuntimeTurnConflict", body, "_persist_chat_turn 未抛 RuntimeTurnConflict")
        # 抛出必须早于写 messages(record_turn_messages)——整回合原子放弃
        raise_pos = body.find("raise platform_branches.RuntimeTurnConflict")
        msgs_pos = body.find("record_turn_messages(")
        self.assertGreater(raise_pos, 0)
        self.assertGreater(msgs_pos, 0)
        self.assertLess(raise_pos, msgs_pos,
                        "conflict 必须在写 messages 之前抛出(否则 messages 已落,非原子放弃)")

    def test_chat_stream_catches_conflict(self):
        self.assertIn("except RuntimeTurnConflict", CHAT,
                      "chat.py 未接住 RuntimeTurnConflict → 冲突会变通用 error / 误标渠道故障")
        self.assertIn('"kind": "save_conflict"', CHAT)
        # 不能把并发冲突计入渠道健康失败(那是模型/网络故障用的)
        conf_pos = CHAT.find("except RuntimeTurnConflict")
        generic_pos = CHAT.find("except Exception as exc:")
        self.assertGreater(conf_pos, 0)
        self.assertGreater(generic_pos, 0)
        self.assertLess(conf_pos, generic_pos,
                        "RuntimeTurnConflict 处理必须在通用 except Exception 之前")

    def test_opening_surfaces_conflict(self):
        self.assertIn('.get("conflict")', OPENING, "opening 路径未接住 conflict")
        self.assertIn('"kind": "save_conflict"', OPENING)


class SaveWriteEndpointsHoldAdvisoryLock(unittest.TestCase):
    """Fix 3(+ 孪生 acceptance):message/edit 与 acceptance 换稿的三表读改写必须在 advisory 锁内。"""

    def _assert_lock_before_amend(self, name: str):
        body = _func_body(SAVES, name)
        lock_pos = body.find("acquire_save_advisory_lock(")
        amend_pos = body.find("_amend_history_message(")
        self.assertGreater(lock_pos, 0, f"{name} 未取 advisory lock")
        self.assertGreater(amend_pos, 0, f"{name} 无 _amend_history_message 调用")
        self.assertLess(lock_pos, amend_pos, f"{name}: 锁必须在 _amend_history_message 之前")

    def test_message_edit_locked(self):
        self._assert_lock_before_amend("api_message_edit")

    def test_acceptance_choice_locked(self):
        self._assert_lock_before_amend("api_acceptance_choice")

    def test_lock_reuses_connection_no_new_connect(self):
        # 锁内严禁开新连接(advisory 锁嵌套开连接=PgBouncer 池死锁)。message/edit 用外层 with 的 db。
        body = _func_body(SAVES, "api_message_edit")
        after_lock = body[body.find("acquire_save_advisory_lock("):]
        self.assertNotIn("with connect()", after_lock,
                         "锁后不得再开新连接(池死锁血泪)")


class RewriteRetiresKbEvents(unittest.TestCase):
    """Fix 2:acceptance 换稿成功后退役该回合 kb_events + 重跑史官。"""

    def test_choice_calls_retire_helper_after_swap(self):
        body = _func_body(SAVES, "api_acceptance_choice")
        self.assertIn("_retire_and_remaintain_after_rewrite(", body)
        # 只在 swapped 成功后触发:`if swapped:` 门在退役调用之前
        swapped_gate = body.find("if swapped:")
        retire_call = body.find("_retire_and_remaintain_after_rewrite(")
        self.assertGreater(swapped_gate, 0, "退役必须在 swapped 成功门内")
        self.assertLess(swapped_gate, retire_call, "退役调用必须位于 if swapped 门之后")
        # 退役是 amend 之后(拿到 swapped 结果才退役)
        amend_pos = body.find("_amend_history_message(")
        self.assertLess(amend_pos, retire_call)

    def test_retire_is_inplace_update_not_tombstone(self):
        # episodic 扁平语料查询是 `where retired_at_commit is null`,tombstone(另插新行)遮蔽不了原行,
        # 只有【就地 UPDATE 原行 retired_at_commit】才能让扁平语料 + _newest_visible 双双排除。
        body = _func_body(SAVES, "_retire_and_remaintain_after_rewrite")
        self.assertIn("update kb_events set retired_at_commit", body)
        self.assertIn("maintain_structured_kb(", body, "换稿后未用新稿重跑史官维护")

    def test_episodic_corpus_filters_retired(self):
        # 语料查询确实带 retired 过滤(退役才有意义)——孪生保证:改这些查询不能丢掉该谓词。
        # v1.72.4 起两条路径共用 _DEDUP_BY_SUMMARY 一段 SQL(原本各写一份、各带一次谓词),
        # 所以断言从「出现 ≥2 次」改成「谓词在共享片段里 + 两条路径都真的用了共享片段」——
        # 覆盖面只增不减:任一路径绕开共享片段自己拼 SQL 会被抓住。
        from kb import episodic as _epi
        self.assertIn("retired_at_commit is null", _epi._DEDUP_BY_SUMMARY)
        epi = (RPG / "kb" / "episodic.py").read_text(encoding="utf-8")
        for fn in ("_retrieve_vector", "_fetch_keyword_corpus"):
            body = _func_body(epi, fn)
            self.assertIn("_DEDUP_BY_SUMMARY", body, f"{fn} 没走共享去重片段")
            # 谱系谓词只该出现在共享片段里。函数体里再出现一次 = 自己拼了条平行语料查询
            # (那条必然绕过 retired 过滤与文本去重)。_retrieve_vector 里那句
            # `select 1 from kb_events ... limit 1` 是存在性门、不带谱系,故不误伤。
            self.assertNotIn("select cid from ancestry", body,
                             f"{fn} 绕开共享片段自己拼了语料查询")


if __name__ == "__main__":
    unittest.main()
