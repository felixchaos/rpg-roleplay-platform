"""agents._harness — 统一三通道 LLM JSON 调用。

把 extractor.py:201-378 的 anthropic native tool_use + vertex call_structured +
openai_compat response_format 三档 dispatch 抽成共享 helper,让 context_agent /
black_swan_agent / phase_digest_agent 都能复用,消除"两套 harness"技术债。

设计要点:
- anthropic + tool_schema → native tool_use,input_schema 强校验,错误率比文本 JSON 低 5-10×
- anthropic 无 schema     → system prompt 里要求 JSON,文本解析(降级)
- vertex_ai               → call_structured (response_mime_type=application/json)
- openai/openai_compat    → /chat/completions response_format=json_object,失败降级到无 json_object

签名:
    call_agent_json(api_id, model, system, user, user_id, *,
                    tool_schema=None, max_tokens=1024, timeout_sec=30)
        -> tuple[text: str, usage: dict]

返回 text 总是 JSON 字符串(或 LLM 原始输出,调用方再 parse):
- anthropic tool_use: tool.input JSON 序列化
- 其它通道: 模型原始字符串(已经是 JSON 格式)

usage 是 {"input_tokens", "output_tokens", "cached_input_tokens",
"reasoning_tokens", "total_tokens"};通道不支持时返回 {}。
"""
from __future__ import annotations

import json
from typing import Any

from core.logging import get_logger

log = get_logger(__name__)


def call_agent_json(
    api_id: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    user_id: int | None,
    *,
    tool_schema: dict | None = None,
    max_tokens: int = 1024,
    timeout_sec: int = 30,
) -> tuple[str, dict]:
    """三通道 dispatch,返回 (text, usage)。

    tool_schema (可选):
        Anthropic tool_use 的工具定义,形如
        {"name": "emit_xxx", "description": "...", "input_schema": {...}}
        只在 api_id="anthropic" 时启用 native tool_use,其它 provider 忽略。
    """
    if api_id == "anthropic":
        if tool_schema:
            return _anthropic_tool_use(
                model, system_prompt, user_prompt, user_id,
                tool_schema, max_tokens,
            )
        return _anthropic_json_text(
            model, system_prompt, user_prompt, user_id, max_tokens,
        )
    if api_id == "vertex_ai":
        return _vertex_structured(
            model, system_prompt, user_prompt, max_tokens,
        )
    # OpenAI 兼容:openai / siliconflow / dashscope / qwen 等
    return _openai_compat_json_mode(
        api_id, model, system_prompt, user_prompt,
        user_id, timeout_sec, max_tokens,
    )


# ── Anthropic native tool_use ─────────────────────────────────────

def _anthropic_tool_use(
    model: str,
    system_prompt: str,
    user_prompt: str,
    user_id: int | None,
    tool_schema: dict,
    max_tokens: int,
) -> tuple[str, dict]:
    """Anthropic native tool_use,强制 schema 校验。

    模型必须输出 tool_use block;返回 tool.input 的 JSON 序列化。
    失败(模型不配合)返回 ('{}', usage)。
    """
    from anthropic import Anthropic

    from platform_app.user_credentials import resolve_api_key
    result = resolve_api_key(user_id, "anthropic", env_fallback="ANTHROPIC_API_KEY")
    key = result.get("key")
    if not key:
        raise RuntimeError("找不到 Anthropic API Key for agent harness")
    client = Anthropic(api_key=key)
    tool_name = tool_schema.get("name") or "emit_payload"
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        tools=[tool_schema],
        tool_choice={"type": "tool", "name": tool_name},
    )
    usage = _anthropic_usage(resp)
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
            inp = block.input or {}
            return json.dumps(inp, ensure_ascii=False), usage
    # 模型没拿出 tool_use block(罕见)
    return "{}", usage


def _anthropic_json_text(
    model: str,
    system_prompt: str,
    user_prompt: str,
    user_id: int | None,
    max_tokens: int,
) -> tuple[str, dict]:
    """Anthropic 无 schema 时:在 system prompt 里要求 JSON,纯文本解析。

    主要给调用方没有定义 tool_schema 的场景兜底。
    """
    from anthropic import Anthropic

    from platform_app.user_credentials import resolve_api_key
    result = resolve_api_key(user_id, "anthropic", env_fallback="ANTHROPIC_API_KEY")
    key = result.get("key")
    if not key:
        raise RuntimeError("找不到 Anthropic API Key for agent harness")
    client = Anthropic(api_key=key)
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt + "\n\n严格只输出 JSON,不要 markdown 围栏,不要解释。",
        messages=[{"role": "user", "content": user_prompt}],
    )
    usage = _anthropic_usage(resp)
    parts: list[str] = []
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text or "")
    return "".join(parts), usage


def _anthropic_usage(resp: Any) -> dict:
    u = getattr(resp, "usage", None)
    if u is None:
        return {}
    input_tokens = int(getattr(u, "input_tokens", 0) or 0)
    output_tokens = int(getattr(u, "output_tokens", 0) or 0)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_input_tokens": int(getattr(u, "cache_read_input_tokens", 0) or 0),
        "reasoning_tokens": 0,
        "total_tokens": input_tokens + output_tokens,
    }


# ── Vertex AI (Gemini) ────────────────────────────────────────────

def _vertex_structured(
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
) -> tuple[str, dict]:
    """Vertex call_structured 已设了 response_mime_type=application/json。"""
    from agents.gm import _VertexBackend
    backend = _VertexBackend(model=model)
    text = backend.call_structured(
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        max_tokens=max_tokens,
    )
    usage = getattr(backend, "last_usage", None) or {}
    return text, dict(usage) if isinstance(usage, dict) else {}


# ── OpenAI 兼容 ────────────────────────────────────────────────────

def _openai_compat_json_mode(
    api_id: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    user_id: int | None,
    timeout_sec: int,
    max_tokens: int,
) -> tuple[str, dict]:
    """OpenAI / SiliconFlow / DashScope 等:response_format=json_object。

    旧 endpoint 不支持 response_format → 降级到普通 chat.completions。
    """
    from platform_app.user_credentials import resolve_api_key
    cred = resolve_api_key(user_id, api_id)
    if not cred.get("key"):
        raise RuntimeError(f"无 {api_id} 凭证可用于 agent harness")
    import urllib.request
    base_url = cred.get("base_url_override") or _api_base_url(api_id)
    if not base_url:
        raise RuntimeError(f"未知 base_url for {api_id}")
    body_dict = {
        "model": model,
        "messages": [
            {"role": "system",
             "content": system_prompt + "\n\n严格只输出 JSON 对象,不要 markdown,不要解释。"},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    body = json.dumps(body_dict).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cred['key']}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        text = payload["choices"][0]["message"]["content"]
        usage = _openai_usage(payload.get("usage") or {})
        return text or "", usage
    except Exception:
        body_dict.pop("response_format", None)
        body = json.dumps(body_dict).encode("utf-8")
        req = urllib.request.Request(
            base_url.rstrip("/") + "/chat/completions",
            data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {cred['key']}"},
        )
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        text = payload["choices"][0]["message"]["content"]
        usage = _openai_usage(payload.get("usage") or {})
        return text or "", usage


def _openai_usage(u: dict) -> dict:
    if not isinstance(u, dict):
        return {}
    input_tokens = int(u.get("prompt_tokens") or 0)
    output_tokens = int(u.get("completion_tokens") or 0)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_input_tokens": int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        if isinstance(u.get("prompt_tokens_details"), dict) else 0,
        "reasoning_tokens": int((u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
        if isinstance(u.get("completion_tokens_details"), dict) else 0,
        "total_tokens": input_tokens + output_tokens,
    }


def _api_base_url(api_id: str) -> str:
    try:
        from model_registry import find_api, load_model_catalog
        api = find_api(load_model_catalog(), api_id)
        return api.get("base_url", "") if api else ""
    except Exception:
        return ""


# ── 模型偏好解析(给三个 agent 的 api_id/model 优先级解析共用)──────

def resolve_api_and_model(
    user_id: int | None,
    *,
    api_pref_key: str,
    model_pref_key: str,
    default_api: str = "vertex_ai",
    default_model: str = "gemini-3.5-flash",
    api_id_override: str | None = None,
    model_override: str | None = None,
) -> tuple[str, str]:
    """统一 api_id/model 解析:override > user_preferences > default。"""
    from core.llm_backend import (
        resolve_preferred_api as _resolve_api,
        resolve_preferred_model as _resolve_model,
    )
    api_id = api_id_override or _resolve_api(user_id, pref_key=api_pref_key) or default_api
    model = model_override or _resolve_model(user_id, pref_key=model_pref_key) or default_model
    return api_id, model


__all__ = ["call_agent_json", "resolve_api_and_model"]
