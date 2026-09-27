"""extract/llm.py — 提取用 LLM 客户端(便宜模型 + 鲁棒 JSON 解析 + 用量记账)。

复用 GameMaster 的 backend(call_structured)。**只用便宜模型**(gemini-3.5-flash / claude-haiku-4-5),
逐章提取走 flash;Pass0/2 少量精判可临时升 haiku。绝不全程 frontier(成本铁律)。

并发安全:GameMaster._backend.last_usage 是 instance attr,跨线程共享会被覆盖。
解决:用 threading.local 让每个线程有独立 GameMaster + backend,各自 last_usage 不互相污染。

记账:每次 complete_text/complete_json 调完,自动 record_usage 到 token_usage 表
(若有 user_id 上下文)。
"""
from __future__ import annotations

import threading
from typing import Any

from core.json_parse import parse_llm_json

# 默认便宜模型(成本铁律)
CHEAP_VERTEX = ("gemini-3.5-flash", "vertex_ai")
CHEAP_ANTHROPIC = ("claude-haiku-4-5", "anthropic")
CHEAP_DEEPSEEK = ("deepseek-v4-flash", "deepseek")


class ExtractLLM:
    """薄封装:一次性 system+user → JSON。

    构造时不实际创建 GameMaster(避免主线程多余创建);每个工作线程首次调用时
    用 threading.local 懒构造一份。这样并发场景下 backend.last_usage 互不污染。

    user_id + script_id + algorithm 仅用于记账(record_usage),不影响 LLM 行为。
    """

    def __init__(self, model: str = CHEAP_VERTEX[0], api_id: str = CHEAP_VERTEX[1],
                 user_id: int | None = None, *, script_id: int | None = None,
                 algorithm: str | None = None):
        self.model = model
        self.api_id = api_id
        self.user_id = user_id
        self.script_id = script_id
        self.algorithm = algorithm
        self._tls = threading.local()

    @property
    def _gm(self):
        # 兼容老代码读 .gm._backend / .gm 直接访问
        return self._get_gm()

    @property
    def _backend(self):
        # 兼容老代码 .backend.call_structured 调用
        return self._get_gm()._backend

    def _get_gm(self):
        gm = getattr(self._tls, "gm", None)
        if gm is None:
            from agents.gm.master import GameMaster
            gm = GameMaster(model=self.model, api_id=self.api_id, user_id=self.user_id)
            self._tls.gm = gm
        return gm

    def _record(self, gm) -> None:
        """从 backend.last_usage 取本次调用 usage 写入 token_usage(失败不阻塞)。"""
        if not self.user_id:
            return
        try:
            backend = gm._backend
            usage = dict(getattr(backend, "last_usage", {}) or {})
            if not (usage.get("input_tokens") or usage.get("output_tokens")):
                return  # 无数据不写
            model_real = getattr(backend, "model_name", self.model)
            from platform_app.usage import record_usage
            record_usage(
                self.user_id, None, None,
                self.api_id, model_real, usage,
                metadata={"source": "extract", "script_id": self.script_id,
                          "algorithm": self.algorithm},
                scenario="extract",
            )
        except Exception:
            pass  # 记账失败绝不阻塞提取主流程

    def complete_text(self, system: str, user: str, max_tokens: int = 2000) -> str:
        gm = self._get_gm()
        text = gm._backend.call_structured(system, [{"role": "user", "content": user}], max_tokens)
        self._record(gm)
        return text

    def complete_json(self, system: str, user: str, max_tokens: int = 2000) -> Any:
        """返回解析后的 JSON(dict/list)。解析失败抛 ExtractOutputUnparseable(ValueError 子类,调用方决定重试)。"""
        raw = self.complete_text(system, user, max_tokens)
        return parse_json(raw)


class ExtractOutputUnparseable(ValueError):
    """模型答了,但答案里解析不出 JSON(或是空的)。

    这不是服务商报错:别拿去 provider_errors 分类。异常文本里夹着模型输出的前 200 字(小说正文),
    分类器会拿它去匹配服务商措辞 —— 正文里有「Forbidden Forest」就被说成 HTTP 403,
    有「timed out」就被说成连不上接口地址。仍是 ValueError 的子类,既有 `except ValueError` 照接。
    """


def parse_json(raw: str) -> Any:
    """鲁棒 JSON 解析:剥 ```json 围栏 / 取首个 {..} 或 [..] / 容忍前后散文。

    薄包装 core.json_parse.parse_llm_json;**对外签名不变**:解析失败仍
    raise ValueError(具体是 ExtractOutputUnparseable,触发 complete_json 调用方重试)。
    """
    result = parse_llm_json(raw)
    if result is None:
        if not raw:
            raise ExtractOutputUnparseable("空响应")
        raise ExtractOutputUnparseable(f"无法从响应解析 JSON: {raw.strip()[:200]!r}")
    return result
