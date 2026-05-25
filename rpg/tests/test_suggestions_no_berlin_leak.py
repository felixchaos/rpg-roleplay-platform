"""
test_suggestions_no_berlin_leak.py — task 41 回归

用户报告：从『雾港/蓝色罗盘/灯塔星门』剧本创建并激活存档后，Game Console
建议动作里仍出现『要求一份柏林当前势力图和行动时限』——这是 MuMuAINovel
柏林默认 fallback 泄漏。

修复：state.suggestions() 把 fallback 拆为通用 / 默认柏林专属两组；
context 不含柏林/图卢兹/哈布斯堡/蛇信/...等默认 token 时只用通用 fallback。
"""
from __future__ import annotations

import copy
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("RPG_REQUIRE_AUTH", "0")

from state import GameState, DEFAULT_STATE  # noqa: E402


BERLIN_TOKENS = ("柏林", "图卢兹", "哈布斯堡", "蛇信", "薇瑟", "扎兹巴鲁姆",
                 "蕾穆丽娜", "斯雷因", "伊奈帆", "甲胄骑士", "Kataphrakt")


def _make_scrubbed_state() -> GameState:
    """模拟 task 34/40 跑过 _scrub_berlin_default 之后的状态：
    没有任何柏林 token 在 player/world/memory 里。"""
    s = GameState(copy.deepcopy(DEFAULT_STATE))
    # 清空 berlin 默认
    s.data["player"]["current_location"] = "雾港码头"
    s.data["world"]["time"] = "申时三刻"
    s.data["world"]["timeline"]["current_label"] = "申时三刻"
    s.data["world"]["timeline"]["current_phase"] = ""
    s.data["world"]["known_events"] = ["开场：第一章 雾港入夜"]
    s.data["memory"]["current_objective"] = "确认蓝色罗盘是否能打开灯塔星门"
    s.data["memory"]["facts"] = []
    s.data["memory"]["pinned"] = []
    s.data["history"] = []
    return s


class SuggestionsDoNotLeakBerlinOnImportedScript(unittest.TestCase):
    def test_no_berlin_token_in_suggestions_for_imported_script(self):
        """核心：scrub 后的 state（雾港/申时三刻/灯塔星门 上下文）→ suggestions 不含任何柏林 token"""
        s = _make_scrubbed_state()
        sugs = s.suggestions()
        self.assertTrue(sugs, f"suggestions 不应为空；实际 {sugs!r}")
        blob = " | ".join(sugs)
        for tok in BERLIN_TOKENS:
            self.assertNotIn(tok, blob,
                f"task 41：导入剧本上下文下 suggestions 不应含柏林 token『{tok}』；"
                f"all={sugs!r}")
        # 通用 fallback 应该至少有一条出现
        generic_markers = ["观察当前场景", "整理当下已知情报", "确认下一步目标", "和关键人物单独", "回顾当前剧本开场"]
        self.assertTrue(any(m in blob for m in generic_markers),
            f"task 41：导入剧本上下文下应至少出现一条通用 fallback；suggestions={sugs!r}")

    def test_default_berlin_state_still_gets_berlin_fallback(self):
        """对照：MuMuAINovel 默认柏林 state（DEFAULT_STATE 未 scrub）→ 仍允许出现『柏林势力图』fallback"""
        s = GameState(copy.deepcopy(DEFAULT_STATE))  # 完整默认，含柏林 known_events / time / location
        sugs = s.suggestions()
        blob = " | ".join(sugs)
        # 不强制柏林 fallback 必须出现（它在 fallback 列表尾部），只要 context 含柏林 token，
        # 它就有资格被推入。这里至少校验：要么出现，要么 context 里的命名候选有出现，证明柏林上下文识别正确。
        is_default_berlin = any(tok in blob for tok in BERLIN_TOKENS + ("柏林势力图",))
        self.assertTrue(is_default_berlin,
            f"对照：DEFAULT_STATE 应可生成柏林相关建议（命名 fallback 或势力图 fallback）；实际 {sugs!r}")

    def test_partial_scrub_with_pinned_memory_still_no_berlin(self):
        """边角：用户在 scrub 后的 state 上又往 memory.pinned 加入一些不含柏林的笔记，
        suggestions 仍然不应出现柏林 fallback。"""
        s = _make_scrubbed_state()
        s.data["memory"]["pinned"] = ["玩家：测试旅人手里只有一枚蓝色罗盘", "线索：星门只持续一刻钟"]
        sugs = s.suggestions()
        blob = " | ".join(sugs)
        for tok in BERLIN_TOKENS:
            self.assertNotIn(tok, blob,
                f"task 41：scrub + pinned 笔记仍不应出现柏林『{tok}』；suggestions={sugs!r}")

    def test_imported_script_with_user_set_phase_does_not_trigger_berlin(self):
        """边角：用户用 /set 设了 timeline.current_phase=港口黄昏测试 后，
        即便 phase 字段非空，也不应反向触发柏林 fallback。"""
        s = _make_scrubbed_state()
        s.data["world"]["timeline"]["current_phase"] = "港口黄昏测试"
        sugs = s.suggestions()
        blob = " | ".join(sugs)
        for tok in BERLIN_TOKENS:
            self.assertNotIn(tok, blob,
                f"task 41：/set current_phase 后仍不应出现柏林『{tok}』；suggestions={sugs!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
