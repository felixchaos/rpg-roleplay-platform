"""
test_scripts_preview.py — UI 审计任务 16 后端契约

确保 POST /api/scripts/preview：
  1) 收到合法 {file: {name, base64}} 时不再 400，返回真实切分结果
  2) 收到旧前端的 {rule, pattern, title, filename, size} 时 4xx（防止哪天后端
     无意中向后兼容反而让前端继续走假数据回退路径）
  3) 对 3 章测试文件返回 total_chapters>=2（中文「第X章」识别）
"""
from __future__ import annotations

import base64
import unittest
from pathlib import Path

from tests.helpers import cleanup_test_users, make_client, register_user

REPO = Path(__file__).resolve().parents[3]
TEST_NOVEL = REPO / "output" / "playwright" / "timeline_set_test_novel.md"


class ScriptsPreviewContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def test_preview_with_real_file_returns_real_chapters(self):
        if not TEST_NOVEL.exists():
            self.skipTest(f"测试文件不存在：{TEST_NOVEL}")
        raw = TEST_NOVEL.read_bytes()
        body = {
            "file": {"name": TEST_NOVEL.name, "base64": base64.b64encode(raw).decode("ascii")},
            "split_rule": "auto",
            "custom_pattern": "",
            "sample_limit": 20,
        }
        u = register_user(self.client)
        r = self.client.post("/api/scripts/preview", json=body, cookies=u["cookies"])
        self.assertEqual(r.status_code, 200, f"预览必须 200，实际 {r.status_code} body={r.text[:300]}")
        out = r.json()
        self.assertTrue(out.get("ok"))
        self.assertIn("total_chapters", out)
        self.assertGreaterEqual(
            int(out["total_chapters"]), 2,
            f"3 章测试文件应至少识别 2 章，实际 {out['total_chapters']}",
        )
        self.assertGreater(int(out.get("total_words") or 0), 100,
                           f"3 章测试文件字数应 > 100：{out.get('total_words')}")
        self.assertIn("preview", out)

    def test_preview_with_old_shape_rejected(self):
        """前端旧 buggy 形态：只发 {filename,size,rule}，后端必须 4xx，
        让前端 toast 报错而不是被静默掩盖。
        """
        u = register_user(self.client)
        r = self.client.post(
            "/api/scripts/preview",
            json={"rule": "auto", "filename": "x.md", "size": 1024},
            cookies=u["cookies"],
        )
        self.assertIn(r.status_code, (400, 422),
                      f"旧形态应 4xx 让前端显式报错，实际 {r.status_code}")

    def test_preview_with_empty_text_is_4xx(self):
        """传一个空 base64 → ValueError → 400，前端会显示 toast"""
        u = register_user(self.client)
        body = {
            "file": {"name": "empty.txt", "base64": ""},
            "split_rule": "auto",
        }
        r = self.client.post("/api/scripts/preview", json=body, cookies=u["cookies"])
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
