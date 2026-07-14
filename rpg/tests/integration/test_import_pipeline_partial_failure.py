"""test_import_pipeline_partial_failure — phase_backend: 验证 _stage_worldbook
全员失败时,_run_pipeline 把 import_jobs.status 设为 'done_with_errors',
error 字段非空,且 stages_jsonb 含 worldbook status=error。
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


class WorldbookFullFailureMarksDoneWithErrors(unittest.TestCase):
    def test_final_stage_status_done_with_errors(self):
        """直接测 _final_stage_status helper:任意 stage error → done_with_errors。"""
        from platform_app.import_pipeline import _final_stage_status
        stages = [
            {"id": "chunks", "status": "done"},
            {"id": "facts", "status": "done"},
            {"id": "worldbook", "status": "error"},
        ]
        self.assertEqual(_final_stage_status(stages), "done_with_errors")
        stages2 = [
            {"id": "chunks", "status": "done"},
            {"id": "facts", "status": "done"},
            {"id": "worldbook", "status": "done"},
        ]
        self.assertEqual(_final_stage_status(stages2), "done")

    def test_stage_worldbook_records_failure(self):
        """_stage_worldbook 在 LLM 调用抛异常时:
        - 不再 silent return 0
        - setattr 写 _last_count=0
        - ctl.update 被调用写 warnings + error
        """
        from platform_app import import_pipeline as ip
        # 拆包后 patch-where-defined:_stage_worldbook 定义在子模块 stages_llm,
        # 它内部用的 connect 必须 patch 定义模块(打门面拦不到)。
        from platform_app.import_pipeline import stages_llm as ip_stages
        ctl = MagicMock()
        # 让 books 查询返 fake book id,然后让 call_agent_json 抛
        with patch.object(ip_stages, "connect") as mock_connect:
            cur = MagicMock()
            cur.execute.return_value.fetchone.side_effect = [
                {"id": 99},  # book row
                {"content": ""},  # era row
            ]
            cur.execute.return_value.fetchall.return_value = []
            mock_connect.return_value.__enter__.return_value = cur
            with patch("agents._harness.call_agent_json", side_effect=RuntimeError("boom")):
                count = ip._stage_worldbook(ctl, user_id=1, script_id=12)
        self.assertEqual(count, 0)
        self.assertEqual(getattr(ip._stage_worldbook, "_last_count"), 0)
        # ctl.update 应该至少被调用过(error + warnings)
        called_keys = set()
        for call_args in ctl.update.call_args_list:
            called_keys.update(call_args.kwargs.keys())
        self.assertIn("error", called_keys)
        self.assertIn("warnings", called_keys)


if __name__ == "__main__":
    unittest.main()
