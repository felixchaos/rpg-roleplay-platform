"""game.py 顶层 except 把 str(exc) 直透进 SSE error 事件给客户端 → DB 表名/连接串、
文件路径、SDK 内部细节泄露给玩家。_client_safe_error 应只回泛化文案 + error_id,
原始异常仅进服务端日志。"""
import re
import unittest
from pathlib import Path

from routes.game import _client_safe_error

SRC = (Path(__file__).resolve().parents[2] / "routes" / "game.py").read_text(encoding="utf-8")


class ChatErrorNoLeak(unittest.TestCase):
    def test_secret_db_detail_not_in_client_message(self):
        exc = RuntimeError(
            'connection to server at "10.0.0.5", port 5432 failed: '
            'password authentication failed for user "rpg_admin"'
        )
        msg = _client_safe_error(exc)
        self.assertNotIn("10.0.0.5", msg)
        self.assertNotIn("rpg_admin", msg)
        self.assertNotIn("password", msg)
        # 应含一个 error_id 便于排障对账
        self.assertTrue(re.search(r"[0-9a-f]{8}", msg), "缺 error_id")

    def test_path_and_sdk_detail_not_leaked(self):
        exc = FileNotFoundError("/opt/rpg-roleplay/.env: No such file")
        msg = _client_safe_error(exc)
        self.assertNotIn("/opt/rpg-roleplay", msg)
        self.assertNotIn(".env", msg)

    def test_known_vertex_config_error_is_actionable(self):
        exc = RuntimeError(
            "未找到 Vertex AI Service Account。"
            "请在「设置 → API & 模型 → Agent Platform」上传自己的 SA JSON 文件。"
        )
        msg = _client_safe_error(exc)
        self.assertIn("未找到 Vertex AI Service Account", msg)
        self.assertIn("Agent Platform", msg)
        self.assertTrue(re.search(r"[0-9a-f]{8}", msg), "缺 error_id")

    def test_invalid_byok_key_is_actionable_without_raw_sdk_detail(self):
        exc = RuntimeError(
            "Error code: 401 - {'error': {'message': 'Incorrect API key provided: 123. "
            "You can find your API key at https://platform.openai.com/account/api-keys.', "
            "'type': 'invalid_request_error', 'code': 'invalid_api_key'}}"
        )
        msg = _client_safe_error(exc)
        self.assertIn("API Key 无效或已过期", msg)
        self.assertIn("API 设置", msg)
        self.assertNotIn("123", msg)
        self.assertNotIn("platform.openai.com", msg)
        self.assertNotIn("invalid_request_error", msg)
        self.assertTrue(re.search(r"[0-9a-f]{8}", msg), "缺 error_id")

    def test_source_no_raw_str_exc_to_client_sse(self):
        # 两处 client-facing SSE error 不应再直传 str(exc)
        self.assertNotIn('_sse("error", {"message": str(exc)', SRC,
                         "仍有 str(exc) 直透进 SSE error 给客户端")
        self.assertEqual(SRC.count("_client_safe_error(exc)"), 2,
                         "两处 SSE error 未都改用 _client_safe_error")


if __name__ == "__main__":
    unittest.main()
