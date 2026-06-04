"""test_sse_module_field — phase_backend: SSE event 含 module/source/before_count/after_count
(get_job_status SELECT * 已包含这些列,SSE expose 直接转发)。
"""
from __future__ import annotations

import unittest


class ImportJobsColumns(unittest.TestCase):
    def test_v45_adds_module_source_before_after_columns(self):
        from platform_app import db as _db
        # 找到 v45
        v45 = next((m for m in _db.MIGRATIONS if m[0] == 45), None)
        self.assertIsNotNone(v45)
        statements = " ".join(v45[2])
        for col in ("module", "source", "before_count", "after_count", "warnings", "sub_kind"):
            self.assertIn(col, statements, f"v45 必须包含 {col} 列添加")

    def test_sse_handler_uses_get_job_status(self):
        # SSE 实现里调 import_pipeline.get_job_status,后者返 SELECT * 含新列
        import inspect

        from platform_app.api import imports as api_imports
        src = inspect.getsource(api_imports.api_import_job_stream)
        self.assertIn("get_job_status", src)


if __name__ == "__main__":
    unittest.main()
