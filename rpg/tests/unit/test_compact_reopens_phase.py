"""/compact 把当前 open phase 就地 closed 后,必须重开一个新 open phase,
否则该存档自此永久停止自动折叠历史(ensure_initial_phase 见 closed 行早退、
detect_phase_boundary 因无 active phase 恒 False)→ GM 对 /compact 后的剧情失忆。
"""
import unittest
from pathlib import Path

PIPE = "\n".join(_p.read_text(encoding="utf-8") for _p in sorted((Path(__file__).resolve().parents[2] / "chat_pipeline").glob("*.py")))


class CompactReopensPhase(unittest.TestCase):
    def test_compact_success_path_reopens_phase(self):
        # 定位 /compact handler 段(_is_compact_command 块)
        i = PIPE.find("_is_compact_command:")
        self.assertNotEqual(i, -1)
        block = PIPE[i:i + 4000]
        self.assertIn("compact_phase(", block, "应调 compact_phase")
        self.assertIn("open_new_phase", block,
                      "/compact 成功后未重开 phase → 该存档永久停止自动折叠历史")

    def test_reopen_is_after_compact_success(self):
        # open_new_phase 必须在 compact_phase 之后、且在非 error 分支
        i = PIPE.find("from agents.phase_digest_agent import compact_phase")
        self.assertNotEqual(i, -1)
        after = PIPE[i:i + 2500]
        compact_pos = after.find("compact_phase(_sid")
        reopen_pos = after.find("open_new_phase")
        self.assertGreater(compact_pos, -1)
        self.assertGreater(reopen_pos, compact_pos, "重开必须在 compact 之后")
        # 重开落在 else(非 error)分支内
        self.assertIn("turn_index=_cur_turn + 1", after,
                      "重开应以当前 turn+1 作为新 phase turn_start")


if __name__ == "__main__":
    unittest.main()
