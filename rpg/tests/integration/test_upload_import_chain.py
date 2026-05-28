"""
test_upload_import_chain.py — UI 审计任务 17/19/20 端到端契约

完整跑：upload_init → put_chunk → finish → /api/scripts/import → /api/scripts → /api/saves
全程用 frontend 现在发的真实 JSON 形状，确保没有契约漂移。
"""
from __future__ import annotations

import base64
import unittest
from pathlib import Path

from tests.helpers import cleanup_test_users, make_client, register_user

REPO = Path(__file__).resolve().parents[3]
TEST_NOVEL = REPO / "output" / "playwright" / "timeline_set_test_novel.md"


class UploadImportChain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def test_full_chain_with_real_file(self):
        if not TEST_NOVEL.exists():
            self.skipTest(f"测试文件不存在：{TEST_NOVEL}")
        u = register_user(self.client)
        cookies = u["cookies"]
        raw = TEST_NOVEL.read_bytes()

        # 1) upload init —— 前端发 {filename, total_bytes, total_chunks}
        chunk_size = 1024 * 1024
        total_chunks = max(1, (len(raw) + chunk_size - 1) // chunk_size)
        r_init = self.client.post("/api/v1/uploads/init", json={
            "filename": TEST_NOVEL.name,
            "total_bytes": len(raw),
            "total_chunks": total_chunks,
        }, cookies=cookies)
        self.assertEqual(r_init.status_code, 200, f"init 必须 200，实际 {r_init.status_code} body={r_init.text[:200]}")
        upload_id = r_init.json()["upload_id"]
        self.assertTrue(upload_id.startswith("up_"))

        # 2) put_chunk —— 前端发 {chunk_index, base64}
        for i in range(total_chunks):
            blob = raw[i * chunk_size:(i + 1) * chunk_size]
            r_chunk = self.client.post(f"/api/v1/uploads/{upload_id}/chunk", json={
                "chunk_index": i,
                "base64": base64.b64encode(blob).decode("ascii"),
            }, cookies=cookies)
            self.assertEqual(r_chunk.status_code, 200, f"chunk {i} 必须 200：{r_chunk.text[:200]}")

        # 3) finish
        r_finish = self.client.post(f"/api/v1/uploads/{upload_id}/finish", json={}, cookies=cookies)
        self.assertEqual(r_finish.status_code, 200, r_finish.text[:200])

        # 4) /api/scripts/import 用 upload_id —— task 17 之前后端漏 upload_id 透传
        r_import = self.client.post("/api/v1/scripts/import", json={
            "upload_id": upload_id,
            "title": "timeline_set_test",
            "split_rule": "auto",
            "custom_pattern": "",
        }, cookies=cookies)
        self.assertEqual(r_import.status_code, 200,
                         f"import 必须 200（之前后端 api_import_script 没透传 upload_id）：{r_import.text[:200]}")
        out = r_import.json()
        self.assertTrue(out.get("ok"))
        self.assertIn("script", out)
        script_id = out["script"]["id"]
        self.assertIsInstance(script_id, int)

        # 5) /api/scripts 列表必须新增这一条 —— task 19 锚
        r_list = self.client.get("/api/v1/scripts", cookies=cookies)
        self.assertEqual(r_list.status_code, 200)
        items = r_list.json().get("items") or r_list.json().get("scripts") or []
        ids = {int(s["id"]) for s in items if s.get("id") is not None}
        self.assertIn(int(script_id), ids,
                      f"导入的 script_id={script_id} 必须在 /api/scripts 列表里：{ids}")

        # 6) /api/saves POST 用真实 script_id —— task 20 锚（前端只发 script_id+title）
        r_save = self.client.post("/api/v1/saves", json={
            "title": "E2E 任务 17 / 20 存档",
            "script_id": script_id,
        }, cookies=cookies)
        self.assertEqual(r_save.status_code, 200, f"create save 必须 200：{r_save.text[:200]}")
        save = r_save.json()["save"]
        self.assertIsInstance(save["id"], int)

        # 7) /api/saves 列表 +1
        r_saves = self.client.get("/api/v1/saves", cookies=cookies)
        self.assertEqual(r_saves.status_code, 200)
        save_items = r_saves.json().get("items") or r_saves.json().get("saves") or []
        save_ids = {int(s["id"]) for s in save_items if s.get("id") is not None}
        self.assertIn(int(save["id"]), save_ids)

    def test_save_create_with_foreign_script_403(self):
        """task 20：发 mock/非自己的 script_id 必须 403，不能静默成功。"""
        register_user(self.client)
        b = register_user(self.client)
        # a 没有任何 script，b 也没有；随便选一个不存在的 id
        r = self.client.post("/api/v1/saves", json={"title": "foreign", "script_id": 999999}, cookies=b["cookies"])
        self.assertEqual(r.status_code, 403)

    def test_upload_init_old_shape_400(self):
        """task 17 锚：旧前端形态 {size, kind, chunk_size} 缺 total_bytes/total_chunks → 400。"""
        u = register_user(self.client)
        r = self.client.post("/api/v1/uploads/init", json={
            "filename": "x.md", "size": 1184, "kind": "script", "chunk_size": 1048576,
        }, cookies=u["cookies"])
        self.assertEqual(r.status_code, 400, "缺 total_bytes 必须 400")


if __name__ == "__main__":
    unittest.main(verbosity=2)
