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
        # 573bbde1b 后 auth 文案扩为「无效、已过期,或该 key 无权访问此模型(401/403)」+
        # 入口改「模型与密钥」;只锁稳定片段,不锁全句。
        self.assertIn("API Key 无效", msg)
        self.assertIn("模型与密钥", msg)
        self.assertNotIn("123", msg)
        self.assertNotIn("platform.openai.com", msg)
        self.assertNotIn("invalid_request_error", msg)
        self.assertTrue(re.search(r"[0-9a-f]{8}", msg), "缺 error_id")

    def test_deepseek_insufficient_balance_402_is_actionable(self):
        # 生产实况(2026-06-10 uid=53):DeepSeek BYOK 余额耗尽,玩家按「请重试」连撞 7 次
        exc = RuntimeError(
            "Error code: 402 - {'error': {'message': 'Insufficient Balance', "
            "'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}"
        )
        msg = _client_safe_error(exc)
        self.assertIn("余额不足", msg)
        self.assertIn("充值", msg)
        self.assertNotIn("Insufficient", msg)
        self.assertNotIn("请重试", msg, "余额不足重试无法恢复,不应引导重试")
        self.assertTrue(re.search(r"[0-9a-f]{8}", msg), "缺 error_id")

    def test_status_code_402_attr_is_actionable(self):
        # openai/anthropic SDK 的 APIStatusError 带 status_code 属性,message 可能不含关键词
        class _FakeAPIStatusError(Exception):
            status_code = 402
        msg = _client_safe_error(_FakeAPIStatusError("provider says no"))
        self.assertIn("余额不足", msg)
        self.assertNotIn("provider says no", msg)

    def test_openai_insufficient_quota_maps_to_balance_not_ratelimit(self):
        # OpenAI 配额耗尽走 429,但本质是计费问题:必须命中余额文案而非「稍候重试」
        class _FakeAPIStatusError(Exception):
            status_code = 429
        exc = _FakeAPIStatusError(
            "Error code: 429 - {'error': {'message': 'You exceeded your current quota, "
            "please check your plan and billing details.', 'code': 'insufficient_quota'}}"
        )
        msg = _client_safe_error(exc)
        self.assertIn("余额不足", msg)
        self.assertIn("充值", msg)

    def test_rate_limit_429_is_actionable(self):
        class _FakeAPIStatusError(Exception):
            status_code = 429
        msg = _client_safe_error(_FakeAPIStatusError("Too many requests, slow down"))
        self.assertIn("限流", msg)
        self.assertIn("重试", msg)
        self.assertNotIn("slow down", msg)
        self.assertTrue(re.search(r"[0-9a-f]{8}", msg), "缺 error_id")

    def test_status_code_401_attr_is_actionable(self):
        class _FakeAPIStatusError(Exception):
            status_code = 401
        msg = _client_safe_error(_FakeAPIStatusError("Authentication Fails"))
        self.assertIn("API Key 无效", msg)

    def test_deepseek_auth_fails_message_without_status_attr(self):
        # DeepSeek 401 文案不含既有 marker;靠 "authentication fails" 兜住
        msg = _client_safe_error(RuntimeError(
            "Error code: 401 - {'error': {'message': 'Authentication Fails (no such user)'}}"
        ))
        self.assertIn("API Key 无效", msg)

    def test_vertex_resource_exhausted_maps_to_ratelimit(self):
        # google.genai 的 ClientError 只有 .code 没有 .status_code,靠 message/code 兜住
        class _FakeGenaiClientError(Exception):
            code = 429
        exc = _FakeGenaiClientError(
            "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': "
            "'Quota exceeded for quota metric ...', 'status': 'RESOURCE_EXHAUSTED'}}"
        )
        msg = _client_safe_error(exc)
        self.assertIn("限流", msg)
        self.assertNotIn("RESOURCE_EXHAUSTED", msg)

    def test_openrouter_insufficient_credits_maps_to_balance(self):
        msg = _client_safe_error(RuntimeError(
            "Error code: 402 - {'error': {'message': 'Insufficient credits', 'code': 402}}"
        ))
        self.assertIn("余额不足", msg)

    def test_sqlstate_code_attr_not_mistaken_for_http_status(self):
        # psycopg 等异常的字符串 code(sqlstate)不能被当 HTTP 状态码
        class _FakeDBError(Exception):
            code = "23505"
        msg = _client_safe_error(_FakeDBError("duplicate key value violates unique constraint"))
        self.assertIn("本轮处理出错", msg)

    def test_source_no_raw_str_exc_to_client_sse(self):
        # 两处 client-facing SSE error 不应再直传 str(exc)
        self.assertNotIn('_sse("error", {"message": str(exc)', SRC,
                         "仍有 str(exc) 直透进 SSE error 给客户端")
        self.assertEqual(SRC.count("_client_safe_error(exc)"), 2,
                         "两处 SSE error 未都改用 _client_safe_error")


if __name__ == "__main__":
    unittest.main()
