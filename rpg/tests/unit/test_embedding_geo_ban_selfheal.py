"""地区封禁自愈(2026-07-05 生产实证):Google AI Studio 从 2026-07-04 起把服务器机房 IP
整段封禁(400 "User location is not supported" / FAILED_PRECONDITION)。每次检索都先撞
一次必然失败的 gemini 原生直连(_embed_via_gemini)才回退 Vertex → 白花 ~300ms + 日志噪声。

覆盖:
  · _is_geo_ban_error 特征识别(命中/不命中)
  · _geo_ban_mark / _geo_ban_active 标记 + TTL 边界(注入假 clock,不真 sleep)
  · _embed_via_gemini 命中地区封禁特征后自动标记该通道
  · _embed_via_gemini 标记生效期间,第二次调用直接跳过网络请求(不再撞墙)
  · 绝不改变「所有通道都失败」时的最终返回行为(仍是 None，upstream 仍会往下一通道走)
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("RPG_REQUIRE_AUTH", "0")
os.environ.setdefault("EMBED_MODEL", "text-embedding-004")
os.environ.setdefault("EMBED_API_ID", "vertex")

from platform_app.knowledge import embedding  # noqa: E402


class _FakeClock:
    """可控时钟：test 显式推进,不依赖真实 time.sleep。"""

    def __init__(self, start: float = 1_000_000.0):
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, secs: float) -> None:
        self._t += secs


class GeoBanErrorDetection(unittest.TestCase):
    """_is_geo_ban_error 字符串特征识别。"""

    def test_matches_user_location_message(self):
        self.assertTrue(embedding._is_geo_ban_error(
            'User location is not supported for the API use.'
        ))

    def test_matches_failed_precondition(self):
        self.assertTrue(embedding._is_geo_ban_error("400 FAILED_PRECONDITION"))

    def test_matches_case_insensitive(self):
        self.assertTrue(embedding._is_geo_ban_error("USER LOCATION IS NOT SUPPORTED"))

    def test_does_not_match_unrelated_error(self):
        self.assertFalse(embedding._is_geo_ban_error("401 Unauthorized: invalid api key"))
        self.assertFalse(embedding._is_geo_ban_error("connection timed out"))
        self.assertFalse(embedding._is_geo_ban_error(""))


class GeoBanCacheTTL(unittest.TestCase):
    """_geo_ban_mark / _geo_ban_active 标记 + TTL 窗口(注入假 clock)。"""

    def setUp(self):
        embedding._GEO_BAN_CACHE.clear()

    def tearDown(self):
        embedding._GEO_BAN_CACHE.clear()

    def test_mark_then_active_within_ttl(self):
        clock = _FakeClock()
        embedding._geo_ban_mark("chan_a", clock=clock)
        clock.advance(10.0)  # 10s 后仍在 1 小时 TTL 内
        self.assertTrue(embedding._geo_ban_active("chan_a", clock=clock))

    def test_inactive_before_marked(self):
        clock = _FakeClock()
        self.assertFalse(embedding._geo_ban_active("chan_never_marked", clock=clock))

    def test_expires_after_ttl(self):
        clock = _FakeClock()
        embedding._geo_ban_mark("chan_b", clock=clock)
        clock.advance(embedding._GEO_BAN_TTL + 1.0)  # 超过 TTL
        self.assertFalse(embedding._geo_ban_active("chan_b", clock=clock))
        # 过期后应从 cache 中清除(懒惰失效)
        self.assertNotIn("chan_b", embedding._GEO_BAN_CACHE)

    def test_channels_are_independent(self):
        clock = _FakeClock()
        embedding._geo_ban_mark("chan_x", clock=clock)
        self.assertTrue(embedding._geo_ban_active("chan_x", clock=clock))
        self.assertFalse(embedding._geo_ban_active("chan_y", clock=clock))


class EmbedViaGeminiGeoBanSelfHeal(unittest.TestCase):
    """_embed_via_gemini 命中地区封禁特征 → 自动标记 → 后续调用跳过网络请求。

    全程 monkeypatch 网络调用(safe_urlopen),零真实外网访问。
    """

    def setUp(self):
        embedding._GEO_BAN_CACHE.clear()

    def tearDown(self):
        embedding._GEO_BAN_CACHE.clear()

    def _make_geo_ban_http_error(self):
        import urllib.error
        import io
        body = (
            b'{"error": {"code": 400, "message": '
            b'"User location is not supported for the API use.", '
            b'"status": "FAILED_PRECONDITION"}}'
        )
        # fp 必须在构造时传入(HTTPError.read() 委托给 fp.read();构造后再赋值
        # 在部分 Python 版本上不会被 .read() 正确代理)。
        return urllib.error.HTTPError(
            url="https://generativelanguage.googleapis.com/v1beta/models/x:embedContent",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=io.BytesIO(body),
        )

    def test_first_call_hits_network_marks_ban_on_geo_error(self):
        """第一次调用：撞地区封禁 → 应发起真实(mock)网络调用 → 失败后标记该通道。"""
        err = self._make_geo_ban_http_error()

        def _raise_safe_urlopen(*args, **kwargs):
            raise err

        with patch("core.outbound.safe_urlopen", side_effect=_raise_safe_urlopen):
            result = embedding._embed_via_gemini("gemini-embedding-001", "fake-key", ["hello"])

        self.assertIsNone(result)
        self.assertTrue(embedding._geo_ban_active(embedding._GEO_BAN_CHANNEL_GEMINI_NATIVE))

    def test_second_call_skips_network_when_banned(self):
        """标记生效期间：第二次调用不应再发起网络请求(网络函数应完全不被调用)。

        _embed_via_gemini 内部调用 _geo_ban_active 不注入 clock(生产路径走真实
        time.time),这里用真实 clock 标记(而非 _FakeClock)以匹配生产调用方式。
        """
        embedding._geo_ban_mark(embedding._GEO_BAN_CHANNEL_GEMINI_NATIVE)  # 默认真实 clock

        with patch("core.outbound.safe_urlopen") as mock_urlopen:
            result = embedding._embed_via_gemini("gemini-embedding-001", "fake-key", ["hello"])

        mock_urlopen.assert_not_called()
        self.assertIsNone(result)

    def test_non_geo_ban_error_does_not_mark_channel(self):
        """非地区封禁错误(如 401)不应误标记通道(避免误伤真实可恢复故障)。"""
        import urllib.error
        import io
        err = urllib.error.HTTPError(
            url="https://generativelanguage.googleapis.com/v1beta/models/x:embedContent",
            code=401, msg="Unauthorized", hdrs=None,
            fp=io.BytesIO(b'{"error": {"code": 401, "message": "API key not valid"}}'),
        )

        with patch("core.outbound.safe_urlopen", side_effect=err):
            result = embedding._embed_via_gemini("gemini-embedding-001", "fake-key", ["hello"])

        self.assertIsNone(result)
        self.assertFalse(embedding._geo_ban_active(embedding._GEO_BAN_CHANNEL_GEMINI_NATIVE))

    def test_final_failure_behavior_unchanged_when_all_channels_fail(self):
        """自愈只跳过已知必然失败的直连步骤,不改变「全部通道失败」时的最终返回契约:
        _embed_via_vertex 在原生直连被跳过后仍应继续尝试 Vertex SDK client,
        client 也失败时最终仍返回 None(与自愈前行为一致,不吞掉/伪造成功)。
        """
        clock = _FakeClock()
        embedding._geo_ban_mark(embedding._GEO_BAN_CHANNEL_GEMINI_NATIVE, clock=clock)

        with patch.object(embedding, "_get_vertex_client", return_value=None), \
             patch.dict(os.environ, {"EMBED_API_KEY": "plat-key"}):
            result = embedding._embed_via_vertex(
                "text-embedding-004", ["hello"], user_id=None,
            )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
