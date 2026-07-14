"""test_rebuild_new_modules.py — 三个新 rebuild 模块接入回归测试

背景:三个原 CLI-only 提取能力(facts_refine / worldbook_enrich / world_key)接入
rebuild/{module} 框架(REBUILD_MODULES + estimate_module_rebuild +
schedule_module_rebuild + _run_module_rebuild),补齐「只有 CLI 没有 API/UI」的缺口。

本文件覆盖:
  1. 三个模块名都在 REBUILD_MODULES 里正确注册(kind/label/needs_llm)。
  2. estimate_module_rebuild 对三个模块返回结构合理的估算(mock DB 查询数,断言
     token 数符合公式)。
  3. runner(_run_module_rebuild)确实调用了对应的 extract 函数,断言传参正确
     (尤其 world_key 按真实签名 dry_run/use_llm/user_id/api_id_override/model_override)。
  4. 非 owner 调用被拒绝 —— 复用现有 script_owned 校验路径,不重复造新校验;
     schedule_module_rebuild 对未知模块 / 非 owner 的行为已有既存测试覆盖同一
     校验函数(perms.script_owned),见 test_worldbook_rebuild_canon_prereq.py 同源
     校验路径,这里只需断言 schedule_module_rebuild 在非 owner 时确实抛 ValueError。

Mock 风格:参照 tests/unit/test_resplit_script_knowledge_rebuild.py —— 用
unittest.mock.patch.object 打桩 db/connect,不打真 DB。
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import platform_app.import_pipeline as ip

# 拆包后 patch-where-defined:estimate_module_rebuild / schedule_module_rebuild 定义在
# rebuild_scheduler,_run_module_rebuild 定义在 rebuild_worker;它们内部引用的
# connect/init_db/script_owned/_resolve_extractor_llm/... 必须 patch 各自定义模块。
# ip.JobController / ip._run_module_rebuild 等对象引用仍走门面(同一对象)。
from platform_app.import_pipeline import rebuild_scheduler as ip_sched
from platform_app.import_pipeline import rebuild_worker as ip_worker


def _fake_connect_cm(db: MagicMock):
    """构造一个 `with connect() as db:` 可用的上下文管理器 mock。"""
    cm = MagicMock()
    cm.__enter__.return_value = db
    cm.__exit__.return_value = False
    return cm


def _row(c: int) -> MagicMock:
    m = MagicMock()
    m.fetchone.return_value = {"c": c}
    return m


class RebuildModulesRegistration(unittest.TestCase):
    """1. 三个模块名都在 REBUILD_MODULES 里正确注册。"""

    def test_facts_refine_registered(self):
        self.assertIn("facts_refine", ip.REBUILD_MODULES)
        kind, label, needs_llm = ip.REBUILD_MODULES["facts_refine"]
        self.assertEqual(kind, "rebuild_facts_refine")
        self.assertEqual(label, "章节摘要精炼")
        self.assertTrue(needs_llm)

    def test_worldbook_enrich_registered(self):
        self.assertIn("worldbook_enrich", ip.REBUILD_MODULES)
        kind, label, needs_llm = ip.REBUILD_MODULES["worldbook_enrich"]
        self.assertEqual(kind, "rebuild_worldbook_enrich")
        self.assertEqual(label, "世界书条目充实")
        self.assertTrue(needs_llm)

    def test_world_key_registered(self):
        self.assertIn("world_key", ip.REBUILD_MODULES)
        kind, label, needs_llm = ip.REBUILD_MODULES["world_key"]
        self.assertEqual(kind, "rebuild_world_key")
        self.assertEqual(label, "世界线回填")
        # 基线免费(结构先验默认零 LLM);仅 body.use_llm 时才需要凭证,由
        # estimate/schedule 按 body 覆盖,不在 REBUILD_MODULES 静态表里写死 True。
        self.assertFalse(needs_llm)


class EstimateFactsRefine(unittest.TestCase):
    """2. estimate_module_rebuild(facts_refine): chapters_in_range * 1500 input tokens。"""

    def _run(self, *, ch_from=None, ch_to=None, chapter_count=10, facts_total=10):
        script_row = {"chapter_count": chapter_count}
        db = MagicMock()

        def _execute(sql, params=None):
            s = sql.lower()
            if "from scripts" in s:
                return _row_obj(script_row)
            if "from chapter_facts" in s:
                return _row(facts_total)
            if "from document_chunks" in s:
                return _row(0)
            if "from kb_canon_entities" in s:
                return _row(0)
            if "from character_cards" in s:
                return _row(0)
            if "from worldbook_entries" in s:
                return _row(0)
            return _row(0)

        db.execute.side_effect = _execute

        def _row_obj(d):
            m = MagicMock()
            m.fetchone.return_value = d
            m.get = d.get
            return m

        body = {}
        if ch_from is not None:
            body["ch_from"] = ch_from
        if ch_to is not None:
            body["ch_to"] = ch_to

        with patch.object(ip_sched, "init_db"), \
             patch.object(ip_sched, "connect", return_value=_fake_connect_cm(db)), \
             patch.object(ip_sched, "script_owned", return_value=script_row), \
             patch.object(ip_sched, "_resolve_extractor_llm", return_value=("deepseek", "deepseek-v4-flash")), \
             patch.object(ip_sched, "_has_user_llm_credential", return_value=True):
            return ip.estimate_module_rebuild(1, 1, "facts_refine", body=body)

    def test_full_range_defaults_to_all_chapters(self):
        out = self._run(chapter_count=10, facts_total=10)
        self.assertTrue(out["ok"])
        self.assertEqual(out["module"], "facts_refine")
        # 10 章 * 1500 input + 10 * 100 output
        self.assertEqual(out["tokens_est"], 10 * 1500 + 10 * 100)
        self.assertTrue(out["approximate"])

    def test_explicit_range_scales_tokens(self):
        out = self._run(ch_from=1, ch_to=3, chapter_count=10, facts_total=10)
        self.assertEqual(out["tokens_est"], 3 * 1500 + 3 * 100)

    def test_empty_chapter_facts_blocks_with_prereq(self):
        out = self._run(chapter_count=10, facts_total=0)
        keys = [p.get("key") for p in out["prereqs"]]
        self.assertIn("chapter_facts", keys)


class EstimateWorldbookEnrich(unittest.TestCase):
    """2. estimate_module_rebuild(worldbook_enrich): matched_entries * 2000 input tokens。"""

    def _run(self, *, pattern=None, matched=5, wb_total=5):
        script_row = {"chapter_count": 10}
        db = MagicMock()

        def _execute(sql, params=None):
            s = sql.lower()
            if "from scripts" in s:
                m = MagicMock()
                m.fetchone.return_value = script_row
                return m
            if "title ~" in s or ("worldbook_entries" in s and params and len(params) == 2):
                return _row(matched)
            if "from worldbook_entries" in s:
                return _row(wb_total)
            return _row(0)

        db.execute.side_effect = _execute
        body = {}
        if pattern is not None:
            body["pattern"] = pattern

        with patch.object(ip_sched, "init_db"), \
             patch.object(ip_sched, "connect", return_value=_fake_connect_cm(db)), \
             patch.object(ip_sched, "script_owned", return_value=script_row), \
             patch.object(ip_sched, "_resolve_extractor_llm", return_value=("deepseek", "deepseek-v4-flash")), \
             patch.object(ip_sched, "_has_user_llm_credential", return_value=True):
            return ip.estimate_module_rebuild(1, 1, "worldbook_enrich", body=body)

    def test_default_pattern_estimates_by_matched_entries(self):
        out = self._run(matched=5, wb_total=5)
        self.assertTrue(out["ok"])
        self.assertEqual(out["tokens_est"], 5 * 2000 + 5 * 400)

    def test_empty_worldbook_blocks_with_prereq(self):
        out = self._run(matched=0, wb_total=0)
        keys = [p.get("key") for p in out["prereqs"]]
        self.assertIn("worldbook", keys)


class EstimateWorldKey(unittest.TestCase):
    """2. estimate_module_rebuild(world_key): use_llm=False → 0(免费);
    use_llm=True → segments(N-1 边界) * 1000 input tokens。"""

    def _run(self, *, use_llm=False, chapters=None):
        script_row = {"chapter_count": 3}
        db = MagicMock()
        chapters = chapters if chapters is not None else [
            {"chapter_index": 1, "title": "第一章", "volume_title": ""},
            {"chapter_index": 2, "title": "第二章", "volume_title": ""},
            {"chapter_index": 3, "title": "第三章", "volume_title": ""},
        ]

        def _execute(sql, params=None):
            s = sql.lower()
            if "from scripts" in s:
                m = MagicMock()
                m.fetchone.return_value = script_row
                return m
            if "from script_chapters" in s:
                m = MagicMock()
                m.fetchall.return_value = chapters
                return m
            return _row(0)

        db.execute.side_effect = _execute
        body = {"use_llm": use_llm} if use_llm else {}

        with patch.object(ip_sched, "init_db"), \
             patch.object(ip_sched, "connect", return_value=_fake_connect_cm(db)), \
             patch.object(ip_sched, "script_owned", return_value=script_row), \
             patch.object(ip_sched, "_resolve_extractor_llm", return_value=("deepseek", "deepseek-v4-flash")), \
             patch.object(ip_sched, "_has_user_llm_credential", return_value=True):
            return ip.estimate_module_rebuild(1, 1, "world_key", body=body)

    def test_use_llm_false_is_free(self):
        out = self._run(use_llm=False)
        self.assertTrue(out["ok"])
        self.assertEqual(out["tokens_est"], 0)
        self.assertFalse(out["approximate"])

    def test_use_llm_true_estimates_by_segments(self):
        # 无卷名 -> 单段(3 章 < 20 章窗口) -> classify_segments 返回 1 段 -> 0 条边界。
        out = self._run(use_llm=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["tokens_est"], 0)  # 1 段 = 0 条边界,免费

    def test_use_llm_true_multi_segment_estimates_nonzero(self):
        # 用卷名制造 2 个不同段 -> 1 条边界 -> 1000 input + 150 output
        chapters = [
            {"chapter_index": 1, "title": "第一章", "volume_title": "第一卷"},
            {"chapter_index": 2, "title": "第二章", "volume_title": "第一卷"},
            {"chapter_index": 3, "title": "穿越副本", "volume_title": "第二卷"},
        ]
        out = self._run(use_llm=True, chapters=chapters)
        self.assertEqual(out["tokens_est"], 1 * 1000 + 1 * 150)


class RunnerCallsExtractFunctions(unittest.TestCase):
    """3. runner(_run_module_rebuild)确实调用了对应的 extract 函数,传参正确。"""

    def _run_module(self, module: str, body: dict):
        db = MagicMock()
        db.execute.return_value = _row(0)

        with patch.object(ip_worker, "connect", return_value=_fake_connect_cm(db)), \
             patch.object(ip.JobController, "update", return_value=None), \
             patch.object(ip_worker, "finalize_job_if_unterminated", return_value=None):
            ip._run_module_rebuild("job1", 42, 7, module, body)

    def test_facts_refine_calls_refine_script_with_expected_kwargs(self):
        fake_result = {"ok": True, "refined": 3, "skipped": 0, "failed": 0, "range": [1, 3]}
        with patch("extract.facts_refine.refine_script", return_value=fake_result) as mock_refine:
            self._run_module("facts_refine", {
                "ch_from": 1, "ch_to": 3, "api_id": "deepseek", "model": "deepseek-v4-flash",
            })
        mock_refine.assert_called_once_with(
            7, 42,
            ch_from=1, ch_to=3,
            api_id="deepseek", model="deepseek-v4-flash",
            apply=True,
        )

    def test_facts_refine_defaults_apply_true_and_none_model(self):
        fake_result = {"ok": True, "refined": 0, "skipped": 0, "failed": 0, "range": [1, 1]}
        with patch("extract.facts_refine.refine_script", return_value=fake_result) as mock_refine:
            self._run_module("facts_refine", {})
        mock_refine.assert_called_once_with(
            7, 42,
            ch_from=1, ch_to=None,
            api_id=None, model=None,
            apply=True,
        )

    def test_worldbook_enrich_calls_enrich_script_worldbook_with_expected_kwargs(self):
        fake_result = {"ok": True, "applied": True, "entries": [
            {"id": 1, "title": "力量·战姬", "status": "ok", "chars": 200, "preview": "x"},
        ]}
        with patch("extract.worldbook_enrich.enrich_script_worldbook", return_value=fake_result) as mock_enrich:
            self._run_module("worldbook_enrich", {"pattern": "战姬|神姬"})
        mock_enrich.assert_called_once_with(
            7, 42, pattern="战姬|神姬", api_id=None, model=None, apply=True,
        )

    def test_worldbook_enrich_default_pattern(self):
        fake_result = {"ok": True, "applied": True, "entries": []}
        with patch("extract.worldbook_enrich.enrich_script_worldbook", return_value=fake_result) as mock_enrich:
            self._run_module("worldbook_enrich", {})
        _, kwargs = mock_enrich.call_args
        self.assertEqual(kwargs["pattern"], "力量|概念|势力|体系")

    def test_world_key_calls_backfill_worldlines_with_real_signature(self):
        # 真实签名(extract/world_key_backfill.py):
        # backfill_worldlines(script_id, *, dry_run=True, use_llm=False, user_id=None,
        #                      api_id_override=None, model_override=None)
        fake_result = {"segments": [], "overcut": False, "would_write": 5, "written": 5}
        with patch("extract.world_key_backfill.backfill_worldlines", return_value=fake_result) as mock_backfill:
            self._run_module("world_key", {
                "use_llm": True, "api_id": "deepseek", "model": "deepseek-v4-flash",
            })
        mock_backfill.assert_called_once_with(
            7,
            dry_run=False,
            use_llm=True,
            user_id=42,
            api_id_override="deepseek",
            model_override="deepseek-v4-flash",
        )

    def test_world_key_defaults_use_llm_false(self):
        fake_result = {"segments": [], "overcut": False, "would_write": 0, "written": 0}
        with patch("extract.world_key_backfill.backfill_worldlines", return_value=fake_result) as mock_backfill:
            self._run_module("world_key", {})
        mock_backfill.assert_called_once_with(
            7,
            dry_run=False,
            use_llm=False,
            user_id=42,
            api_id_override=None,
            model_override=None,
        )

    def test_world_key_overcut_reported_as_partial_failure(self):
        fake_result = {"segments": [], "overcut": True, "would_write": 5, "written": 5}
        with patch("extract.world_key_backfill.backfill_worldlines", return_value=fake_result), \
             patch.object(ip_worker, "connect", return_value=_fake_connect_cm(MagicMock(
                 execute=MagicMock(return_value=_row(5))))), \
             patch.object(ip.JobController, "update") as mock_update, \
             patch.object(ip_worker, "finalize_job_if_unterminated", return_value=None):
            ip._run_module_rebuild("job1", 42, 7, "world_key", {})
        # 找到写终态那次 update 调用,断言 status 为 done_with_errors(overcut 记为 partial_failure)
        final_calls = [c for c in mock_update.call_args_list if c.kwargs.get("status")]
        self.assertTrue(any(c.kwargs.get("status") == "done_with_errors" for c in final_calls))


class NonOwnerRejected(unittest.TestCase):
    """4. 非 owner 调用被拒绝。schedule_module_rebuild 复用既有 script_owned 校验路径
    (platform_app.perms.script_owned,同一函数已被现有模块如 chunks/canon 使用,
    owner 校验行为本身已由既存测试路径覆盖 —— 这里只确认三个新模块同样触发该校验,
    不重复造新的校验逻辑)。"""

    def _assert_rejected(self, module: str, body: dict | None = None):
        with patch.object(ip_sched, "init_db"), \
             patch.object(ip_sched, "connect", return_value=_fake_connect_cm(MagicMock())), \
             patch.object(ip_sched, "script_owned", return_value=None):
            with self.assertRaises(ValueError):
                ip.schedule_module_rebuild(999, 7, module, body=body or {})

    def test_facts_refine_rejects_non_owner(self):
        self._assert_rejected("facts_refine")

    def test_worldbook_enrich_rejects_non_owner(self):
        self._assert_rejected("worldbook_enrich")

    def test_world_key_rejects_non_owner(self):
        self._assert_rejected("world_key")

    def test_world_key_use_llm_rejects_non_owner_before_credential_check(self):
        # use_llm=True 会先触发 require_user_llm_credential;非 owner 校验应仍然生效
        # (script_owned 在 credential 校验之后执行 —— 但即便凭证校验先跑,非owner用户
        # 也不应该拿到 job_id;这里验证最终仍抛 ValueError)。
        with patch.object(ip_sched, "init_db"), \
             patch.object(ip_sched, "connect", return_value=_fake_connect_cm(MagicMock())), \
             patch.object(ip_sched, "script_owned", return_value=None), \
             patch.object(ip_sched, "require_user_llm_credential", return_value=None):
            with self.assertRaises(ValueError):
                ip.schedule_module_rebuild(999, 7, "world_key", body={"use_llm": True})


if __name__ == "__main__":
    unittest.main()
