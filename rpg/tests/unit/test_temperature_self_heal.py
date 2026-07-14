"""
test_temperature_self_heal.py
=============================

回归:某些模型「只允许 temperature=1」(moonshot kimi 部分模型、openai o-series/gpt-5
reasoning)。平台默认发 temperature=0.9/0.1 → provider 400「invalid temperature:
only 1 is allowed for this model」→ 未处理 → 整轮失败(用户只见随机错误码,如 Eca1fd130)。
prod 日志实证:POST api.moonshot.cn/v1/chat/completions 400 → unhandled stream error。

不变量:_OpenAICompatBackend._create 首次被 temperature 拒后,去掉 temperature 用模型
默认重试(当轮即成功),并记忆 (api_id, model) → 本进程后续不再发 temperature。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]  # rpg/
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

import httpx
from openai import BadRequestError

from agents.gm.backends.openai_compat import _OpenAICompatBackend, _is_temperature_rejected


def _temp_400() -> BadRequestError:
    resp = httpx.Response(400, request=httpx.Request("POST", "https://api.moonshot.cn/v1/chat/completions"))
    return BadRequestError(
        "Error code: 400 - invalid temperature: only 1 is allowed for this model",
        response=resp, body=None,
    )


class _FakeCompletions:
    def __init__(self, reject_when_temp: bool):
        self.reject_when_temp = reject_when_temp
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self.reject_when_temp and "temperature" in kwargs:
            raise _temp_400()
        return {"ok": True, "kwargs": kwargs}


class _FakeChat:
    def __init__(self, comp):
        self.completions = comp


class _FakeClient:
    def __init__(self, comp):
        self.chat = _FakeChat(comp)


def _backend(comp, api_id="moonshot", model="kimi-k2.5", user_id=None):
    b = object.__new__(_OpenAICompatBackend)  # 跳过 __init__(不连真 provider)
    b.client = _FakeClient(comp)
    b.api_id = api_id
    b.model_name = model
    # 反馈#93 之二(42e89ed65)后 combo 扩为 (api_id, model, user_id):采样参数自愈记忆按用户隔离
    b.user_id = user_id
    return b


class TemperatureSelfHeal(unittest.TestCase):
    def setUp(self):
        # 清掉类级记忆,避免用例间串味
        _OpenAICompatBackend._fixed_temp_combos.clear()

    def test_detector_only_matches_temperature_badrequest(self):
        self.assertTrue(_is_temperature_rejected(_temp_400()))
        self.assertFalse(_is_temperature_rejected(ValueError("temperature")))  # 非 BadRequest
        other = BadRequestError("invalid model", response=httpx.Response(400, request=httpx.Request("POST", "https://x")), body=None)
        self.assertFalse(_is_temperature_rejected(other))

    def test_retries_without_temperature_and_succeeds(self):
        comp = _FakeCompletions(reject_when_temp=True)
        b = _backend(comp)
        out = b._create(model="kimi-k2.5", messages=[], max_tokens=100, temperature=0.9)
        self.assertEqual(out["ok"], True)
        # 第一次带 temperature(被拒)→ 第二次不带(成功)
        self.assertEqual(len(comp.calls), 2)
        self.assertIn("temperature", comp.calls[0])
        self.assertNotIn("temperature", comp.calls[1])
        # 记忆该组合(combo 现含 user_id 维度)
        self.assertIn(("moonshot", "kimi-k2.5", None), _OpenAICompatBackend._fixed_temp_combos)

    def test_subsequent_calls_skip_temperature(self):
        comp = _FakeCompletions(reject_when_temp=True)
        b = _backend(comp)
        b._create(model="kimi-k2.5", messages=[], max_tokens=100, temperature=0.9)  # heal
        comp.calls.clear()
        out = b._create(model="kimi-k2.5", messages=[], max_tokens=100, temperature=0.9)
        self.assertEqual(out["ok"], True)
        # 已记忆 → 一次成功,且不带 temperature
        self.assertEqual(len(comp.calls), 1)
        self.assertNotIn("temperature", comp.calls[0])

    def test_normal_provider_keeps_temperature(self):
        comp = _FakeCompletions(reject_when_temp=False)  # 不拒 temperature 的正常 provider
        b = _backend(comp, api_id="openai", model="gpt-4o")
        out = b._create(model="gpt-4o", messages=[], max_tokens=100, temperature=0.9)
        self.assertEqual(out["ok"], True)
        self.assertEqual(len(comp.calls), 1)
        self.assertIn("temperature", comp.calls[0])  # 正常模型保留 temperature
        self.assertNotIn(("openai", "gpt-4o", None), _OpenAICompatBackend._fixed_temp_combos)

    def test_non_temperature_400_still_raises(self):
        comp = _FakeCompletions(reject_when_temp=False)
        def boom(**kwargs):
            raise BadRequestError("invalid model xyz", response=httpx.Response(400, request=httpx.Request("POST", "https://x")), body=None)
        comp.create = boom
        b = _backend(comp)
        with self.assertRaises(BadRequestError):
            b._create(model="kimi-k2.5", messages=[], max_tokens=100, temperature=0.9)


if __name__ == "__main__":
    unittest.main()
