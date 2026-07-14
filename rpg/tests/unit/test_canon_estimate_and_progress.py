"""
test_canon_estimate_and_progress.py
===================================

群反馈(行者无疆,430万字/507章剧本 canon 知识库人物提取):
1. 估算严重偏离:导入预览只显示「一百多k」(前端 IMPORT_STAGES extract=120/章 ×507=157k),
   实际烧 838k@63%(投影~1.3M);且 canon 模块重做估算我之前写成 sum(length(text))——列名
   错(实际列 content),会直接报错。三套估算口径(157k / 2.9M / budget 1.16M)互相打架。
2. 进度卡在 canon、知识库人物计数恒 0:① 延迟写库(arc 全跑完才在 resolve 一次性 upsert,正常)
   ② canon 重做 runner 漏传 progress_cb → overall_progress 全程 0(真 bug)③ _count 复用已还池 db。

本测试源码级锁:
- canon 模块估算改用 arc 感知的 extract.budget.estimate(不再 length(text)/chars 公式)。
- canon 重做 runner 传 progress_cb;_count 用独立 connect;终态 overall_total 重置 1。
- 前端导入预览 extract/章 token 标定到 arc 真实量级(>=2000,不再 120)。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]  # rpg/
# import_pipeline 已拆包:canon 估算在 rebuild_scheduler.py,canon 重做 runner 的
# progress_cb/_count/终态重置在 rebuild_worker.py。源码级不变量跨文件,读全部子模块
# 拼接后检查(「必须存在」不变;「必须不存在」覆盖面只增不减)。
_PKG = PROJECT / "platform_app" / "import_pipeline"
PIPELINE = "\n".join(
    p.read_text(encoding="utf-8") for p in sorted(_PKG.glob("*.py"))
)
SCRIPTS_JSX = (PROJECT.parent / "frontend" / "src" / "pages" / "scripts.jsx").read_text(encoding="utf-8")


class CanonEstimateUsesBudget(unittest.TestCase):
    def test_no_broken_length_text_column(self):
        # 不能再用 length(text)(列名错,实际列是 content);也不再用 chars/2 + chunks*200 公式
        self.assertNotIn("sum(length(text))", PIPELINE)

    def test_canon_estimate_uses_arc_budget(self):
        # canon 全量估算复用 extract.budget.estimate(arc 感知,与 wizard 同源)
        self.assertIn("from extract.budget import estimate as _budget_estimate", PIPELINE)
        self.assertRegex(PIPELINE, r'_budget_estimate\(\s*\n?\s*db,\s*script_id,\s*algorithm="arc"')
        self.assertIn('est_in = int(_b.get("est_input_tokens") or 0)', PIPELINE)


class CanonRunnerProgressAndConn(unittest.TestCase):
    def test_progress_cb_passed(self):
        # canon 全量 runner 必须把 progress_cb 传给 run_llm_extraction(否则进度恒 0=「卡死」)
        self.assertIn("def _canon_progress(", PIPELINE)
        self.assertIn("progress_cb=_canon_progress", PIPELINE)
        # arc_extract 映射到 overall 进度
        self.assertRegex(PIPELINE, r'if stage == "arc_extract" and total:')
        self.assertIn("overall_progress=done", PIPELINE)

    def test_count_uses_independent_connection(self):
        # 修连接复用:before/after 各取独立 with connect()(不复用已还池的 db)
        self.assertIn("with connect() as _dbc:", PIPELINE)
        self.assertIn("before = _count(_dbc,", PIPELINE)
        self.assertIn("after = _count(_dbc,", PIPELINE)
        # 不再用外层已退出的 db 做 canon 计数
        self.assertNotIn('before = _count(db, "kb_canon_entities"', PIPELINE)

    def test_final_state_resets_overall_total(self):
        # 进度回传期间把 overall_total 改成弧数;终态必须重置 1,否则 done 显示 1/弧数≈1%
        self.assertRegex(PIPELINE, r'overall_progress=1,\s*\n\s*overall_total=1,')


class ImportPreviewEstimateCalibrated(unittest.TestCase):
    def test_extract_per_chapter_realistic(self):
        # 导入预览 extract(canon)每章 token 不能再是 120(18 倍低估);标定到 arc 真实量级
        m = re.search(r'id:\s*"extract"[^\n]*tok_per_chap:\s*(\d+)', SCRIPTS_JSX)
        self.assertIsNotNone(m, "未找到 extract 阶段的 tok_per_chap")
        self.assertGreaterEqual(int(m.group(1)), 2000,
                                f"extract tok_per_chap={m.group(1)} 仍严重低估 canon arc 抽取真实量")


if __name__ == "__main__":
    unittest.main()
