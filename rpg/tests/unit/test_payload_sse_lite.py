"""
test_payload_sse_lite.py
========================

回归:聊天 SSE 的 status/done 事件每轮塞整份模型目录(payload["models"])+ tools。
前端 on_status/on_done 根本不读它们(目录另由 /api/models、/api/state 拉),纯属流量垃圾
——用户能在「本轮 SSE 事件流」里看到 xiaomi_mimo 的「base_url 待小米发布后填入」等内部占位
(群反馈)。且每轮多次重建 → 白跑 _redact_catalog 深拷贝 + has_credential DB 查询。

不变量(锁死):
  · _payload 有 include_catalog 开关,False 时不放 models/tools;
  · _payload_sse = include_catalog=False;
  · game.py 的 SSE(status/done)+ chat 管线 payload_fn 全走 _payload_sse;
  · 仅 /api/new、/api/state 两个 JSON 引导端点保留整份目录(前端渲染选择器需要)。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]  # rpg/
APP_PY = (PROJECT / "app.py").read_text(encoding="utf-8")
GAME_PY = (PROJECT / "routes" / "game.py").read_text(encoding="utf-8")


class PayloadHasCatalogSwitch(unittest.TestCase):
    def test_payload_signature_has_include_catalog(self):
        self.assertRegex(APP_PY, r"def _payload\([^)]*\*,\s*include_catalog:\s*bool\s*=\s*True")

    def test_models_tools_guarded(self):
        # models/tools 必须在 `if include_catalog:` 下,不再无条件塞
        self.assertRegex(
            APP_PY,
            r"if include_catalog:\s*\n\s*payload\[\"models\"\]\s*=\s*_redact_catalog[^\n]*\n\s*payload\[\"tools\"\]\s*=\s*_redact_tools",
        )

    def test_payload_sse_helper_exists(self):
        self.assertRegex(APP_PY, r"def _payload_sse\([^)]*\)[^\n]*:")
        self.assertIn("return _payload(api_user, include_catalog=False)", APP_PY)


class GameRouteUsesLiteOnSse(unittest.TestCase):
    def test_status_done_sse_use_lite(self):
        # 所有 status/done 的 SSE yield 都走 _payload_sse,且不再有 _sse(...) 配 _payload(api_user)
        self.assertNotRegex(GAME_PY, r'_sse\("status",\s*_payload\(api_user\)\)')
        self.assertNotRegex(GAME_PY, r'_sse\("done",\s*\{[^}]*_payload\(api_user\)')
        self.assertGreaterEqual(len(re.findall(r'_sse\("status",\s*_payload_sse\(api_user\)\)', GAME_PY)), 1)

    def test_payload_fn_bound_to_lite(self):
        self.assertNotIn("payload_fn=_payload,", GAME_PY)
        self.assertGreaterEqual(GAME_PY.count("payload_fn=_payload_sse,"), 1)

    def test_json_bootstrap_endpoints_keep_full_catalog(self):
        # /api/new 与 /api/state 的 JSON 响应仍用整份 _payload(前端选择器要目录)
        self.assertIn('JSONResponse({"ok": True, "backup": backup, "state": _payload(api_user)})', GAME_PY)
        self.assertIn('JSONResponse({"ok": True, "state": _payload(api_user)})', GAME_PY)

    def test_lite_imported(self):
        self.assertIn("_payload_sse", GAME_PY)


if __name__ == "__main__":
    unittest.main()
