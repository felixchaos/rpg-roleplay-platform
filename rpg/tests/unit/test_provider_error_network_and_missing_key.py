"""连接层失败 / 凭据缺失必须被分类，且自测书专名不得留在 LLM-facing prompt 里。

两个来源都是同一次群反馈(2026-08-25,桌面版玩家从本地 Ollama 切 DeepSeek):
玩家只拿到「本轮处理出错,请重试(错误码 Exxx)」——48ms 就失败,既不是限流也不是余额,
而是**请求根本没发出去/没送达**这两类:api_key 为空(SDK 构造期崩)、地址连不上。
两类都没有 HTTP 状态码,原先一路穿过所有按 status 的分支落进泛化兜底,
把「去把 key 填上」「去看看 Ollama 起没起」这种一句话能解决的问题变成了随机错误码。
"""
import unittest

import httpx
import openai

from agents.provider_errors import classify_provider_error


class _Fake(Exception):
    def __init__(self, msg, status=None):
        super().__init__(msg)
        if status is not None:
            self.status_code = status


class TestMissingCredentials(unittest.TestCase):
    def test_empty_api_key_construction_is_auth(self):
        """openai SDK 对 api_key="" 与 None 一视同仁,构造即抛 —— 必须归 auth 不落兜底。"""
        with self.assertRaises(openai.OpenAIError) as cm:
            openai.OpenAI(api_key="")
        known = classify_provider_error(cm.exception)
        self.assertIsNotNone(known, "空 key 构造异常仍落进泛化兜底(玩家只见随机错误码)")
        self.assertEqual(known[0], "auth")
        self.assertIn("API Key", known[1])


class TestConnectionFailure(unittest.TestCase):
    def _req(self):
        return httpx.Request("POST", "http://127.0.0.1:11434/v1/chat/completions")

    def test_openai_connection_error(self):
        known = classify_provider_error(openai.APIConnectionError(request=self._req()))
        self.assertEqual((known or [None])[0], "network")

    def test_openai_timeout_is_subclass_of_connection(self):
        """APITimeoutError 是 APIConnectionError 子类 —— 靠 MRO 命中,别只认精确类名。"""
        known = classify_provider_error(openai.APITimeoutError(request=self._req()))
        self.assertEqual((known or [None])[0], "network")

    def test_httpx_connect_error(self):
        known = classify_provider_error(httpx.ConnectError("All connection attempts failed"))
        self.assertEqual((known or [None])[0], "network")

    def test_bare_refused_string(self):
        """状态码/类型都丢了的裸串(经中间层包装过)也要兜住。"""
        known = classify_provider_error(_Fake("[Errno 61] Connection refused"))
        self.assertEqual((known or [None])[0], "network")


class TestOrderingGuard(unittest.TestCase):
    """连接层判定必须排在所有带 status 的分支之后,否则会吞掉真正的上游错误。"""

    def test_504_gateway_timeout_stays_upstream(self):
        known = classify_provider_error(_Fake("gateway timeout", 504))
        self.assertEqual((known or [None])[0], "upstream",
                         "504 的措辞含 timeout,被 network 分支吞了 → 渠道健康门控收不到信号")

    def test_429_with_timeout_wording_stays_ratelimit(self):
        known = classify_provider_error(_Fake("rate limit exceeded, request timed out", 429))
        self.assertEqual((known or [None])[0], "ratelimit")

    def test_unrelated_exception_still_unclassified(self):
        self.assertIsNone(classify_provider_error(KeyError("player")))


class TestNoTestBookLeakInPrompts(unittest.TestCase):
    """自测书(阿衡/北港·灯塔/1937/沈知微)的专名不得出现在任何进 LLM 的 prompt 里。

    few-shot 里写死具体作品的人名地名,模型会把它当本局已存在的设定:玩家 SSE 日志里
    直接看到「没把 1937 原著事件当本局已发生」「你想让阿衡先在塔下观察」——那既不是他的
    剧本,也不是他的角色。同族前科:test_suggestions_no_berlin_leak(柏林泄进雾港档)。
    """

    LEAKED = ("阿衡", "北港", "黄铜怀表", "沈知微", "1937", "蓝色罗盘")
    # 每轮都会被组装进 LLM 上下文的 prompt 模块
    LLM_FACING = (
        "agents/context_agent.py",
        "agents/extractor.py",
        "agents/gm/master.py",
        "context_engine/layers.py",
    )

    def test_no_hardcoded_test_book_names(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[2]
        for rel in self.LLM_FACING:
            text = (root / rel).read_text(encoding="utf-8")
            for name in self.LEAKED:
                self.assertNotIn(name, text,
                                 f"{rel} 的 prompt 里又出现自测书专名「{name}」")


if __name__ == "__main__":
    unittest.main()
