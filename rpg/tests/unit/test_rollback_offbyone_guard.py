"""回归:rollback_to_message(删除消息及之后)不得「多回退一个回合」。

群反馈(行者无疆/晓卡/星之游):「点 删除此消息及以后 会多回退一个回合,要手动去分支树里再切回来。
之前不会」「修了一个出来两个」。第一根因=delete 路径用 message_row_by_index 读 **flat messages 表**
(含开场空 user 行 + 非分支隔离 → 与前端 blob history index 错位 ≥1 位),已在 v1.32.x 改为
`msg_index // 2` + 活跃血缘递归定位修掉。

第二根因(2026-07-17 根修):`msg_index // 2` + 「偶数再退一格」把 **opening_offset 恒写死为 1**——
该奇偶契约仅当 history[0] 是「单条 GM 开场」时成立;无开场对话(空起手 / 角色卡无 first_mes)整体
反相 → 玩家消息落偶数位 → 误退一轮、上上轮被删。修=开场感知统一公式:
    target_turn  = max(0, (msg_index - opening_offset) // 2)
    deleted_turn = target_turn + 1
opening_offset 取**活跃 commit** 的 history[0].role(=='assistant' → 1 否则 0);活跃 commit 恒在
history_elide 保护集 → state_snapshot->'history'->0 全量可靠。delete 与 fork 两条 index→turn 映射
共用模块级 `_opening_offset_from_history`(tree_ops.py),防再次相位漂移。

本测试为源码不变量守卫(行为在 tests/integration/test_rollback_opening_offset.py 真库跨开场/无开场档验证)。
"""
import unittest
from pathlib import Path

from platform_app.branches import deletion

SRC = Path(deletion.__file__).read_text(encoding="utf-8")


def _strip_comments(text: str) -> str:
    """去掉整行 # 注释 —— 注释里会引用旧符号名,只验真实代码。"""
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def _rollback_code() -> str:
    i = SRC.find("def rollback_to_message(")
    assert i != -1, "找不到 rollback_to_message"
    end = SRC.find("\ndef ", i + 1)
    return _strip_comments(SRC[i:end if end != -1 else len(SRC)])


class RollbackOffByOneGuard(unittest.TestCase):
    def test_uses_opening_aware_formula_not_hardcoded_parity(self):
        code = _rollback_code()
        # 新公式:统一 (msg_index - opening_offset) // 2,开场感知(替换旧的硬编码 msg_index//2 + 偶数退一格)。
        self.assertIn("(msg_index - opening_offset) // 2", code,
                      "rollback 未用开场感知统一公式 → 无开场档会偏一位/多回退一回合")
        self.assertIn("opening_offset", code, "rollback 未引入 opening_offset")
        self.assertIn("_opening_offset_from_history", code,
                      "rollback 未复用共用的 _opening_offset_from_history 判定(delete/fork 需同源)")
        # 旧的硬编码奇偶相位必须消失(否则 opening_offset 又被写死为 1)。
        self.assertNotIn("msg_index // 2", code,
                         "仍残留硬编码 msg_index//2(opening_offset 被写死为 1)")
        self.assertNotIn("msg_index % 2", code,
                         "仍残留硬编码奇偶分支(opening_offset 被写死为 1)")

    def test_opening_offset_from_active_commit_snapshot(self):
        code = _rollback_code()
        # opening_offset 的确定性来源 = 活跃 commit 的 state_snapshot->'history'->0(前端 msg_index 就是这份 blob 的下标)。
        self.assertIn("state_snapshot->'history'->0", code,
                      "opening_offset 未取活跃 commit 的 history[0] → 与前端 msg_index 语义不对齐")

    def test_does_not_use_messages_table(self):
        code = _rollback_code()
        self.assertNotIn("message_row_by_index", code,
                         "rollback 仍调 message_row_by_index(flat messages 表错位)= 第一根因未除")

    def test_resolves_along_active_lineage(self):
        code = _rollback_code()
        self.assertIn("with recursive lineage", code,
                      "rollback 未沿活跃 commit 血缘定位 → 多分支下跨分支命中错节点")
        self.assertIn("active_cid", code, "未以活跃 commit 为血缘起点")

    def test_deleted_turn_is_one_past_target(self):
        code = _rollback_code()
        self.assertIn("deleted_turn = target_turn + 1", code,
                      "删除起点应为保留点的下一回合(否则会连保留回合一起删/漏删)")

    # ── 行为级纯逻辑复核(与源码公式一致)──────────────────────────────
    @staticmethod
    def _target_turn(msg_index: int, opening_offset: int) -> int:
        """源码 target_turn 的纯逻辑镜像:max(0, (N - opening_offset)//2)。"""
        return max(0, (msg_index - opening_offset) // 2)

    def test_with_opening_matches_legacy_behavior(self):
        # 有开场(opening_offset=1):对玩家(奇)/GM(偶)/开场(0)三类输入,新公式与旧「N//2 偶数退一格」逐位相同。
        tt = self._target_turn
        # 旧行为:开场 idx0=GM(assistant),idx1=玩家 turn1,idx2=GM turn1,idx3=玩家 turn2 …
        self.assertEqual(tt(0, 1), 0, "删开场应 clamp 到 0(保留开场,删其后)")
        self.assertEqual(tt(1, 1), 0, "删玩家 turn1 输入 → 保留到 turn0(=开场)")
        self.assertEqual(tt(2, 1), 0, "删 GM turn1 回复(偶)→ 退到 turn0、删 turn1(连该 GM 回复一起删)")
        self.assertEqual(tt(3, 1), 1, "删玩家 turn2 输入 → 保留到 turn1")
        self.assertEqual(tt(4, 1), 1, "删 GM turn2 回复(偶)→ 退到 turn1、删 turn2")
        self.assertEqual(tt(5, 1), 2, "删玩家 turn3 输入 → 保留到 turn2")

    def test_without_opening_keeps_one_more_round(self):
        # 无开场(opening_offset=0,本 bug 核心):history idx0=玩家 turn1, idx1=GM turn1, idx2=玩家 turn2 …
        tt = self._target_turn
        self.assertEqual(tt(0, 0), 0, "删玩家 turn1 → 恢复 root(turn0)")
        self.assertEqual(tt(1, 0), 0, "删 GM turn1 回复(奇位)→ 退到 turn0、删 turn1")
        self.assertEqual(tt(2, 0), 1, "删玩家 turn2 输入 → 保留 turn1(旧代码会误退到 turn0=多删一轮)")
        self.assertEqual(tt(3, 0), 1, "删 GM turn2 回复(奇位)→ 退到 turn1、删 turn2、不越删 turn1")
        self.assertEqual(tt(4, 0), 2, "删玩家 turn3 输入 → 保留 turn2")

    def test_gm_message_deletion_drops_its_whole_turn(self):
        # 回归(v1.32.4 语义,开场感知后仍成立):点「删除此 GM 回复」必须把该回复连其所在回合一起删。
        tt = self._target_turn
        # 有开场:GM turn1 在 idx2 → 删除起点 = tt+1 = 1 = turn1(含该 GM 回复)。
        self.assertEqual(tt(2, 1) + 1, 1, "有开场:删 GM turn1 → deleted_turn=1,整回合(含 GM 回复)被删")
        # 无开场:GM turn2 在 idx3 → 删除起点 = tt+1 = 2 = turn2;且不越删 turn1。
        self.assertEqual(tt(3, 0) + 1, 2, "无开场:删 GM turn2 → deleted_turn=2,整回合被删、不越删 turn1")

    def test_module_import_drops_message_row_by_index(self):
        # 该符号在本模块已无用 → import 行不应再带它(防 ruff 未用导入 + 防误用回退)。
        import_lines = [ln for ln in SRC.splitlines()
                        if ln.startswith("from ") or ln.startswith("import ")]
        self.assertFalse(
            any("message_row_by_index" in ln for ln in import_lines),
            "deletion.py 仍 import message_row_by_index(已无用)",
        )


if __name__ == "__main__":
    unittest.main()
