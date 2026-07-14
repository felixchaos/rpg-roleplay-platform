"""
test_image_gen_openai_compat.py
===============================

回归:生图 dispatch 之前只支持 doubao/dashscope/vertex,其它一律
`unsupported image provider`。而绝大多数 BYOK 用户配的是 OpenAI 兼容中转站
(prod 实测失败 top:openrouter/openai/deepseek/guiji/xiaomi_mimo)→ 选 dall-e /
gpt-image / gemini-*-image 全部失败 = 用户「APIKEY 选哪个都生成不了图片」。

修复:新增 openai_compat 适配器(标准 /images/generations + OpenRouter chat 图像模态回退),
dispatch 把所有非 vertex/anthropic 的 provider 都路由过去。本测试 mock 出站 HTTP 客户端
锁两条路径 + 路由 + UA + base_url 解析。

mock 缝(重要):生产代码经 SSRF 统一出站层改造后走
`with core.outbound.safe_httpx_client(...) as client: client.post(...)`,
**不再调用模块级 httpx.post** —— 老的 `mock.patch("httpx.post")` 会完全落空,
单测悄悄发真实网络请求(实测曾打到 api.openai.com 拿回真 401)。
故必须 patch `agents.image_gen.openai_compat.safe_httpx_client`,并用
_NoNetworkTestCase 断网守门(任何 DNS 解析直接炸 = mock 落空的回归信号)。
"""
from __future__ import annotations

import base64
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

PROJECT = Path(__file__).resolve().parents[2]  # rpg/
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agents.image_gen import dispatch, openai_compat  # noqa: E402
from agents.image_gen.base import ImageGenError  # noqa: E402

_PNG = b"\x89PNG\r\n\x1a\nFAKE"
_B64 = base64.b64encode(_PNG).decode()


class _Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


@contextmanager
def _patched_post(response=None, side_effect=None):
    """把 openai_compat 的出站缝 safe_httpx_client 换成假 client,yield 其 .post mock。

    生产代码每个请求都 `with safe_httpx_client(...) as client: client.post(...)`;
    这里让工厂恒返回同一个假 client(context manager 返回自身),.post 共享同一个
    mock —— 跨两次请求的 side_effect 序列(images 404 → chat 回退)也能按序吐。
    断言接口与旧 mock.patch("httpx.post") 完全一致(call_args/call_count/...)。
    """
    post = mock.Mock(return_value=response, side_effect=side_effect)
    client = mock.MagicMock()
    client.__enter__.return_value = client
    client.post = post
    with mock.patch("agents.image_gen.openai_compat.safe_httpx_client", return_value=client):
        yield post


class _NoNetworkTestCase(unittest.TestCase):
    """守门:本文件是纯单测,任何真实 DNS/TCP 都说明 mock 打错了缝(历史事故:
    httpx.post 落空后真打到 api.openai.com)。socket.getaddrinfo 直接炸掉。"""

    def setUp(self):
        guard = mock.patch(
            "socket.getaddrinfo",
            side_effect=AssertionError("单测不得发真实网络请求 —— mock 缝落空(见文件头注释)"),
        )
        guard.start()
        self.addCleanup(guard.stop)


class ImagesApiPath(_NoNetworkTestCase):
    def test_b64_json_decoded(self):
        with _patched_post(_Resp(200, {"data": [{"b64_json": _B64}]})) as p:
            out = openai_compat.generate("a cat", {"size": "1024x1024"},
                                         api_id="openai", model="gpt-image-1",
                                         api_key="k", base_url="https://relay.test/v1")
        self.assertEqual(out, [_PNG])
        # 命中标准 images 端点 + 浏览器 UA
        called = p.call_args
        self.assertTrue(called.args[0].endswith("/images/generations") or called.kwargs.get("json"))
        sent_headers = called.kwargs["headers"]
        self.assertIn("Mozilla/5.0", sent_headers["User-Agent"])
        self.assertNotIn("OpenAI/Python", sent_headers["User-Agent"])

    def test_url_downloaded(self):
        with _patched_post(_Resp(200, {"data": [{"url": "https://img.test/x.png"}]})), \
             mock.patch("agents.image_gen.openai_compat.download_url", return_value=_PNG) as dl:
            out = openai_compat.generate("x", {}, api_id="guiji", model="ERNIE-Image",
                                         api_key="k", base_url="https://relay.test/v1")
        self.assertEqual(out, [_PNG])
        dl.assert_called_once()

    def test_data_uri_b64_stripped(self):
        payload = {"data": [{"b64_json": "data:image/png;base64," + _B64}]}
        with _patched_post(_Resp(200, payload)):
            out = openai_compat.generate("x", {}, api_id="openai", model="dall-e-3",
                                         api_key="k", base_url="https://relay.test/v1")
        self.assertEqual(out, [_PNG])

    def test_auth_401_gives_actionable_key_message(self):
        # 401/403 → 「鉴权失败/检查 Key」可行动文案,而非裸 HTTP 401(生产实况:openrouter
        # key 无效 → Missing Authentication header,用户看不懂)
        with _patched_post(_Resp(401, {"error": {"message": "Missing Authentication header"}})):
            with self.assertRaises(ImageGenError) as ctx:
                openai_compat.generate("x", {}, api_id="openai", model="dall-e-3",
                                       api_key="k", base_url="https://relay.test/v1")
        msg = str(ctx.exception)
        self.assertIn("鉴权失败", msg)
        self.assertIn("Key", msg)
        self.assertIn("401", msg)


class ChatModalityFallback(_NoNetworkTestCase):
    def test_404_falls_back_to_chat_modality(self):
        # 非 openrouter provider:先打 /images/generations,404 再回退 chat 模态
        seq = [
            _Resp(404, None, "not found"),  # images/generations 不存在
            _Resp(200, {"choices": [{"message": {"images": [
                {"image_url": {"url": "data:image/png;base64," + _B64}}
            ]}}]}),
        ]
        with _patched_post(side_effect=seq) as p:
            out = openai_compat.generate("a dog", {}, api_id="some-relay",
                                         model="flux-image",
                                         api_key="k", base_url="https://relay.test/v1")
        self.assertEqual(out, [_PNG])
        self.assertEqual(p.call_count, 2)
        self.assertTrue(p.call_args_list[1].args[0].endswith("/chat/completions"))

    def test_openrouter_goes_straight_to_chat_modality(self):
        # openrouter 直接走 chat 模态(不先打 /images/generations)
        resp = _Resp(200, {"choices": [{"message": {"images": [
            {"image_url": {"url": "data:image/png;base64," + _B64}}
        ]}}]})
        with _patched_post(resp) as p:
            out = openai_compat.generate("a dog", {}, api_id="openrouter",
                                         model="google/gemini-2.5-flash-image",
                                         api_key="k", base_url="https://openrouter.test/api/v1")
        self.assertEqual(out, [_PNG])
        self.assertEqual(p.call_count, 1)  # 只一发,且是 chat/completions
        self.assertTrue(p.call_args.args[0].endswith("/chat/completions"))

    def test_openrouter_bad_key_gives_actionable_message(self):
        # openrouter chat 模态遇 401 → 「鉴权失败」可行动文案(生产实况:用户 key 无效)
        with _patched_post(_Resp(401, {"error": {"message": "Missing Authentication header", "code": 401}})):
            with self.assertRaises(ImageGenError) as ctx:
                openai_compat.generate("x", {}, api_id="openrouter", model="google/gemini-3-pro-image-preview",
                                       api_key="k", base_url="https://openrouter.test/api/v1")
        msg = str(ctx.exception)
        self.assertIn("鉴权失败", msg)
        self.assertIn("sk-or-v1", msg)  # openrouter 专属提示 key 形如 sk-or-v1-…

    def test_chat_no_image_clear_error(self):
        resp = _Resp(200, {"choices": [{"message": {"content": "sorry, text only"}}]})
        with _patched_post(resp):
            with self.assertRaises(ImageGenError) as ctx:
                openai_compat.generate("x", {}, api_id="openrouter", model="some/text-model",
                                       api_key="k", base_url="https://openrouter.test/api/v1")
        self.assertIn("未返回图像", str(ctx.exception))


class BaseUrlResolution(_NoNetworkTestCase):
    def test_falls_back_to_catalog_base_url(self):
        # 内置 provider(非 chat-modality)无 override 时,base_url 回退 catalog
        fake_api = {"id": "openai", "base_url": "https://api.openai.com/v1"}
        with mock.patch("model_registry.load_model_catalog", return_value={"apis": [fake_api]}), \
             mock.patch("model_registry.find_api", return_value=fake_api), \
             mock.patch("model_registry.normalize_api_id", side_effect=lambda x: x), \
             _patched_post(_Resp(200, {"data": [{"b64_json": _B64}]})) as p:
            openai_compat.generate("x", {}, api_id="openai", model="dall-e-3", api_key="k", base_url=None)
        self.assertTrue(p.call_args.args[0].startswith("https://api.openai.com/v1/"))

    def test_missing_base_url_raises(self):
        with mock.patch("model_registry.load_model_catalog", return_value={"apis": []}), \
             mock.patch("model_registry.find_api", return_value=None), \
             mock.patch("model_registry.normalize_api_id", side_effect=lambda x: x):
            with self.assertRaises(ImageGenError):
                openai_compat.generate("x", {}, api_id="weird", model="m", api_key="k", base_url=None)


class DispatchRouting(_NoNetworkTestCase):
    def test_openai_compat_providers_routed(self):
        for pid in ("openai", "openrouter", "deepseek", "guiji", "xiaomi_mimo", "my-custom-relay"):
            with mock.patch("model_aliases.normalize_api_id", side_effect=lambda x: x), \
                 mock.patch("agents.image_gen.openai_compat.generate", return_value=[_PNG]) as g:
                out = dispatch.generate_image_bytes(api_id=pid, model="m", prompt="p",
                                                    params={}, api_key="k", base_url="https://x/v1")
            self.assertEqual(out, [_PNG])
            g.assert_called_once()

    def test_anthropic_rejected(self):
        with mock.patch("model_aliases.normalize_api_id", side_effect=lambda x: x):
            with self.assertRaises(ImageGenError) as ctx:
                dispatch.generate_image_bytes(api_id="anthropic", model="claude", prompt="p",
                                              params={}, api_key="k")
        self.assertIn("anthropic", str(ctx.exception).lower())

    def test_native_providers_unchanged(self):
        for pid, mod in (("doubao", "doubao"), ("dashscope", "dashscope")):
            with mock.patch("model_aliases.normalize_api_id", side_effect=lambda x: x), \
                 mock.patch(f"agents.image_gen.{mod}.generate", return_value=[_PNG]) as g:
                out = dispatch.generate_image_bytes(api_id=pid, model="m", prompt="p",
                                                    params={}, api_key="k")
            self.assertEqual(out, [_PNG])
            g.assert_called_once()


if __name__ == "__main__":
    unittest.main()
