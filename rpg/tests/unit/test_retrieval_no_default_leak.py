"""
test_retrieval_no_default_leak.py — task 42 回归

用户报告：导入自定义剧本 → /set → /api/state.memory.last_retrieval 含
  "第4章《## 第三章 次日码头》｜图卢兹失守后次日，柏林内城"
  "[第163章片段]... 薇瑟帝国... 扎兹巴鲁姆... 柏林..."
  "第1313章/1314章/1315章 ... 蕾穆丽娜 / 斯雷因 / 调令伪造 ..."
这都是默认 MuMu 剧本的章节/角色/原文，对导入剧本属于污染。

修复（retrieval.py）：
  - retrieve_context 加 script_id 参数；_is_default_mumu_script 判断当前 save 的 script
    是不是 MuMuAINovel 默认（source_path 以 rpg/indexes 开头 或 title==BASE_TITLE）
  - 非默认 script_id：跳过所有 .webnovel SQLite / indexes/*.json 来源
    （load_chapter_facts, bm25_search, load_summaries_window, character cards）
  - postgres retrieve_runtime_context 仍走（已按 save→script_id 严格 scope）
  - postgres 返回也做后处理：含柏林 token 的行被剔除（防御历史脏数据）

  context_agent.run_context_agent 透传 script_id 给 retrieve_context。
"""
from __future__ import annotations

import copy
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("RPG_REQUIRE_AUTH", "0")

import retrieval  # noqa: E402
from state import DEFAULT_STATE, GameState  # noqa: E402

FORBIDDEN_DEFAULT_TOKENS = (
    "柏林", "图卢兹", "哈布斯堡", "蛇信", "薇瑟", "扎兹巴鲁姆",
    "蕾穆丽娜", "斯雷因", "伊奈帆", "甲胄骑士", "Kataphrakt",
    "调令伪造", "娅赛兰",
)


class DefaultNovelDetection(unittest.TestCase):
    """单元：_is_default_mumu_script 判定逻辑（无 DB 也能跑—走 except 路径）"""

    def test_none_script_id_returns_false(self):
        self.assertFalse(retrieval._is_default_mumu_script(None))

    def test_zero_script_id_returns_false(self):
        self.assertFalse(retrieval._is_default_mumu_script(0))


class StripDefaultNovelLeakage(unittest.TestCase):
    """单元：_strip_default_novel_leakage 把柏林 token 行剔除"""

    def test_strips_lines_with_berlin_tokens(self):
        text = (
            "=== Postgres ChapterFact ===\n"
            "第1章《雾港入夜》｜申时三刻\n"
            "第4章《## 第三章 次日码头》｜图卢兹失守后次日，柏林内城\n"
            "摘要：次日清晨，黑潮退去...\n"
        )
        out = retrieval._strip_default_novel_leakage(text)
        # 含『图卢兹』和『柏林』的行被删
        self.assertNotIn("图卢兹", out)
        self.assertNotIn("柏林内城", out)
        # 干净的雾港和摘要保留
        self.assertIn("第1章《雾港入夜》", out)
        self.assertIn("次日清晨", out)

    def test_strips_berlin_chunks_block(self):
        text = (
            "=== Postgres 原文片段 ===\n"
            "[第163章片段]\n"
            "礼花？薇瑟帝国的阅兵式...\n"
            "蕾穆丽娜在赛亚尔...\n"
        )
        out = retrieval._strip_default_novel_leakage(text)
        for tok in ("薇瑟", "蕾穆丽娜"):
            self.assertNotIn(tok, out, f"含『{tok}』的行应被剔除")

    def test_empty_safe(self):
        self.assertEqual(retrieval._strip_default_novel_leakage(""), "")
        self.assertEqual(retrieval._strip_default_novel_leakage(None), None)


class RetrieveContextSkipsDefaultForImportedScript(unittest.TestCase):
    """关键回归：retrieve_context(script_id=<imported>) 不应含任何默认 MuMu 来源标题或柏林 token"""

    def _state_imported(self) -> GameState:
        s = GameState(copy.deepcopy(DEFAULT_STATE))
        # 模拟 task 34/40 scrub 后的状态
        s.data["player"]["current_location"] = "雾港码头"
        s.data["world"]["time"] = "四日后的黄昏"
        s.data["world"]["timeline"]["current_label"] = "四日后的黄昏"
        s.data["world"]["timeline"]["current_phase"] = "港口黄昏测试"
        s.data["world"]["known_events"] = ["开场：第一章 雾港入夜"]
        s.data["memory"]["current_objective"] = "确认蓝色罗盘是否能打开灯塔星门"
        s.data["history"] = []
        return s

    def test_imported_script_skips_all_default_sources(self):
        """script_id 给定且不是默认 → 输出不应含『相关原文片段』『最近剧情摘要』
        『ChapterFact时间线』『相关角色』section 标题，也不应含柏林 token。"""
        s = self._state_imported()
        # 给一个一定不是默认的 script_id（极大值；_is_default_mumu_script DB 查不到 row → False）
        ctx = retrieval.retrieve_context(
            "雾港发生了什么？",
            state=s,
            user_id=999_999_999,  # 跨用户：retrieve_runtime_context 也拿不到 runtime → 空
            script_id=999_888_777,
        )
        # 不应含 SQLite/JSON 来源 section 标题
        for header in ("ChapterFact时间线", "相关原文片段", "最近剧情摘要", "相关角色"):
            self.assertNotIn(header, ctx,
                f"task 42：导入剧本不应含默认来源 section『{header}』；ctx={ctx[:600]!r}")
        # 不应含任何柏林 token
        for tok in FORBIDDEN_DEFAULT_TOKENS:
            self.assertNotIn(tok, ctx,
                f"task 42：导入剧本不应含柏林 token『{tok}』；ctx={ctx[:600]!r}")
        # 应保留时间线锚点说明（但内容只有当前时间/标签，不引用原著章节）
        self.assertIn("时间线检索锚点", ctx, f"应仍保留时间线锚点 section；ctx={ctx[:300]!r}")
        self.assertIn("当前导入剧本", ctx,
            f"应明示『来源：当前导入剧本（不读默认 MuMu 原著时间线）』；ctx={ctx[:300]!r}")
        # 不应含『原著锚点』字段（那是默认走的）
        self.assertNotIn("原著锚点", ctx,
            f"非默认剧本不应输出『原著锚点』；ctx={ctx[:300]!r}")

    def test_default_mumu_script_path_still_includes_default_sources(self):
        """对照：不传 script_id（或 script_id 解析失败按默认走）→ 仍走原默认 MuMu 路径，
        即应包含『原著锚点』section。"""
        s = self._state_imported()
        ctx = retrieval.retrieve_context(
            "雾港发生了什么？",
            state=s,
            user_id=None,
            script_id=None,  # 老 caller / 兼容路径 → is_default=True
        )
        # 默认路径应有原著锚点 section
        self.assertIn("原著锚点", ctx,
            f"默认/兼容路径应保留『原著锚点』section；ctx={ctx[:600]!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
