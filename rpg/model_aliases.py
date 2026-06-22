"""
model_aliases.py - 规范化 API provider id 的单一来源。

所有模块统一从此 import normalize_api_id / _API_ID_ALIASES，
避免多处维护同名字典导致方向不一致。
canonical = 小写 provider id（vertex_ai / openai / anthropic / deepseek /
dashscope / doubao / hunyuan / minimax / siliconflow / openrouter / xiaomi_mimo）。
"""
from __future__ import annotations

_API_ID_ALIASES: dict[str, str] = {
    "OpenAI": "openai",
    "openai": "openai",
    "OpenRouter": "openrouter",
    "openrouter": "openrouter",
    "DeepSeek": "deepseek",
    "deepseek": "deepseek",
    "Anthropic": "anthropic",
    "anthropic": "anthropic",
    "AlibabaQwen": "dashscope",
    "DashScope": "dashscope",
    "dashscope": "dashscope",
    "TencentHunyuan": "hunyuan",
    "Hunyuan": "hunyuan",
    "hunyuan": "hunyuan",
    "XiaomiMimo": "xiaomi_mimo",
    "MiMo": "xiaomi_mimo",
    "xiaomi_mimo": "xiaomi_mimo",
    "SiliconFlow": "siliconflow",
    "siliconflow": "siliconflow",
    "MiniMax": "minimax",
    "minimax": "minimax",
    "Doubao": "doubao",
    "doubao": "doubao",
    "AgentPlatform": "vertex_ai",
    "agent_platform": "vertex_ai",
    "vertex": "vertex_ai",
    "vertex_ai": "vertex_ai",
}


import re as _re

_API_ID_RE = _re.compile(r'^[a-z0-9][a-z0-9_.\-]*$')
_API_ID_MAX_LEN = 64


def normalize_api_id(api_id: str | None) -> str:
    value = str(api_id or "").strip()
    if not value:
        return ""
    canonical = _API_ID_ALIASES.get(value) or _API_ID_ALIASES.get(value.lower()) or value
    # 别名表内的值是已知合法 canonical id,跳过校验。只对「未识别的原始输入」校验。
    if canonical not in _API_ID_ALIASES.values():
        if len(canonical) > _API_ID_MAX_LEN:
            raise ValueError(
                f"api_id 过长(最多 {_API_ID_MAX_LEN} 字符): {canonical!r}"
            )
        if not _API_ID_RE.match(canonical):
            raise ValueError(
                f"api_id 含非法字符(仅允许 a-z0-9 _ . -,不能以 . 或 - 开头): {canonical!r}"
            )
    return canonical


def credential_storage_api_id(api_id: str) -> str:
    """canonical api_id → 凭证寻址 api_id（与 normalize_api_id **方向相反**)。

    ⚠️ 职责独立、不可与 normalize_api_id 合并:normalize 是 别名→canonical,
    本函数是 canonical→AgentPlatform 凭证存储键(Vertex 的 BYOK SA 存在
    user_api_credentials 的 "AgentPlatform" 行下,而非 "vertex_ai")。
    其它 provider 凭证存储键即 canonical 本身。
    """
    return "AgentPlatform" if api_id == "vertex_ai" else api_id
