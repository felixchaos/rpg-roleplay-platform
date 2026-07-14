"""
test_worldbook_rebuild_canon_prereq.py
======================================

回归:点「生成世界书 / 确认重做」没反应(群反馈行者无疆)。

根因(确定性):
  · runner `_run_module_rebuild` 的 worldbook 分支 `src = source or "canon"` —— **默认 canon**
    (零 LLM,从 kb_canon_entities 建)。
  · 但 `estimate_module_rebuild` / `schedule_module_rebuild` 只在 source 显式 == 'canon' 时才
    认作 canon:默认(无 source)被误判为「需 LLM」→ 估算显示模型 + 误加 LLM 凭证 prereq,且
    **漏掉「canon 为空」阻断**。用户确认后 runner 用 canon 跑 → 直接 failed
    「kb_canon_entities 为空,无法重建 worldbook」→ 前端「点了没反应」。

不变量(锁死,源码级):
  · estimate / schedule:`if module == "worldbook": needs_llm = (source_pref == "llm")`
    —— 与 runner 的默认 canon 对齐(不再只判显式 'canon')。
  · estimate:有效 source 为 canon(cards 恒为 / worldbook 非 llm)且 canon 为空 → 阻断 prereq。
  · runner:worldbook 默认 `src = source or "canon"`。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]  # rpg/
# import_pipeline 已拆包:estimate/schedule 在 rebuild_scheduler.py,runner 在
# rebuild_worker.py,REBUILD_MODULES 在 rebuild_registry.py。源码级不变量跨文件,
# 读全部子模块拼接后检查(「必须存在」不变;「必须不存在」覆盖面只增不减)。
_PKG = PROJECT / "platform_app" / "import_pipeline"
PIPELINE = "\n".join(
    p.read_text(encoding="utf-8") for p in sorted(_PKG.glob("*.py"))
)


class EstimateAlignsWorldbookDefaultCanon(unittest.TestCase):
    def test_worldbook_needs_llm_only_when_source_llm(self):
        # 必须有 `if module == "worldbook": needs_llm = (source_pref == "llm")`(出现两处:
        # estimate + schedule),不再是只判 `source_pref == "canon"`。
        occurrences = re.findall(
            r'if module == "worldbook":\s*\n\s*needs_llm = \(source_pref == "llm"\)',
            PIPELINE,
        )
        self.assertGreaterEqual(len(occurrences), 2,
                                "estimate 和 schedule 都应把 worldbook needs_llm 对齐为 source=='llm'")

    def test_old_explicit_canon_only_pattern_gone(self):
        self.assertNotRegex(
            PIPELINE,
            r'if module == "worldbook" and source_pref == "canon":\s*\n\s*needs_llm = False',
            "旧的『仅显式 source==canon 才置 needs_llm=False』反模式仍在 → 默认 worldbook 会被误判需 LLM",
        )


class CanonEmptyBlocksWorldbookOnly(unittest.TestCase):
    def test_worldbook_default_canon_empty_blocks(self):
        # worldbook 默认 canon 且无回退 → canon 为空必须阻断(仅 worldbook,非 llm)
        self.assertRegex(
            PIPELINE,
            r'if module == "worldbook" and source_pref != "llm" and canon_total == 0:',
        )

    def test_cards_not_blocked_on_empty_canon(self):
        # cards 有 facts 回退(rebuild_cards_from_canon),canon 为空不该被拦 → 不应再把 cards
        # 纳入 canon 阻断条件。
        self.assertNotRegex(PIPELINE, r'_uses_canon = \(module == "cards"\)')

    def test_old_canon_prereq_required_explicit_source(self):
        self.assertNotRegex(
            PIPELINE,
            r'if module in \{"cards", "worldbook"\} and source_pref == "canon" and canon_total == 0:',
            "旧的『canon prereq 仅在显式 source==canon』仍在 → 默认 worldbook 漏掉 canon 为空阻断",
        )


class EstimateIsHonestAboutCost(unittest.TestCase):
    def test_tokens_cost_not_hardcoded_zero(self):
        # 返回必须用计算出的 tokens_est/cost_est 变量,不再写死 0(否则 LLM 操作也显示「免费」)
        self.assertRegex(PIPELINE, r'"tokens_est": tokens_est')
        self.assertRegex(PIPELINE, r'"cost_est": cost_est')
        self.assertNotRegex(PIPELINE, r'"tokens_est": 0,\s*\n\s*"cost_est": 0\.0,')

    def test_llm_paths_estimate_tokens(self):
        # canon 全量 / worldbook-llm 走真实 token 估算 + get_pricing 算成本
        self.assertIn("from model_probe import get_pricing", PIPELINE)
        self.assertRegex(PIPELINE, r"est_in = est_out = 0")
        self.assertRegex(PIPELINE, r"tokens_est = est_in \+ est_out")

    def test_cards_is_zero_llm_unless_llm_enrich(self):
        # cards = rebuild_cards_from_canon **默认零 LLM**(显示免费);进度感知角色卡新增「LLM 丰富
        # 重建」选项后,仅 source/mode=='llm' 才烧 LLM。
        #   · estimate(_estimate_module_rebuild):`if module == "cards": needs_llm = (source_pref == "llm")`
        #   · schedule(schedule_module_rebuild):REBUILD_MODULES['cards'] 恒 False(默认免费),
        #     再 `if module == "cards" and source_pref == "llm": needs_llm = True`。
        # 锁住「默认免费、仅 llm 丰富才烧」这一行为,而非旧的无条件 needs_llm=False。
        self.assertRegex(
            PIPELINE,
            r'if module == "cards":\s*\n(?:\s*#.*\n)*\s*needs_llm = \(source_pref == "llm"\)',
            "estimate 应把 cards needs_llm 对齐为 source=='llm'(默认免费,仅 llm 丰富才烧)",
        )
        self.assertRegex(
            PIPELINE,
            r'if module == "cards" and source_pref == "llm":\s*\n(?:\s*#.*\n)*\s*needs_llm = True',
            "schedule 应仅在 source=='llm' 时把 cards 标记 needs_llm=True",
        )
        # cards 在 REBUILD_MODULES 默认表里必须是 False(免费基线),否则没配 key 的用户重建不了角色卡。
        self.assertRegex(
            PIPELINE,
            r'"cards":\s*\(\s*"rebuild_cards",[^)]*?,\s*False\s*\)',
            "REBUILD_MODULES['cards'] 默认 needs_llm 必须为 False(免费基线)",
        )


class RunnerDefaultsToCanon(unittest.TestCase):
    def test_worldbook_runner_default_canon(self):
        # 这是被对齐的「真相源」:runner 默认 canon
        self.assertRegex(PIPELINE, r'src = source or "canon"')


if __name__ == "__main__":
    unittest.main()
