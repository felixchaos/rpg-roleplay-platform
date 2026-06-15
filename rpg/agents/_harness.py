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


def _no_redirect_urlopen(req, *, timeout):
    """SEC(H-4): 已并入 `core.outbound.safe_urlopen` —— 不跟随重定向 + use-time 重解析并把
    socket pin 到已校验 IP(抗 DNS rebinding)。保留此薄包装仅为兼容既有调用点;新代码
    直接用 `core.outbound.safe_urlopen`。"""
    from core.outbound import safe_urlopen

    return safe_urlopen(req, timeout=timeout)


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
    agent_kind: str | None = None,
    save_id: int | None = None,
    context_run_id: int | None = None,
    metadata_extra: dict | None = None,
) -> tuple[str, dict]:
    """三通道 dispatch,返回 (text, usage)。

    tool_schema (可选):
        Anthropic tool_use 的工具定义,形如
        {"name": "emit_xxx", "description": "...", "input_schema": {...}}
        只在 api_id="anthropic" 时启用 native tool_use,其它 provider 忽略。

    agent_kind / save_id / context_run_id / metadata_extra:
        当 agent_kind + user_id 同时存在时,**内部自动**调
        `platform_app.usage.record_usage` 写入 token_usage 表,
        消除"返了 usage 没人 record"的赊账漏洞。
        agent_kind 用作 metadata.kind(如 "curator" / "black_swan" / "phase_digest")。
    """
    if api_id == "anthropic":
        if tool_schema:
            text, usage = _anthropic_tool_use(
                model, system_prompt, user_prompt, user_id,
                tool_schema, max_tokens,
            )
        else:
            text, usage = _anthropic_json_text(
                model, system_prompt, user_prompt, user_id, max_tokens,
            )
    elif api_id == "vertex_ai":
        if tool_schema:
            text, usage = _vertex_function_call(
                model, system_prompt, user_prompt, user_id, tool_schema, max_tokens,
            )
        else:
            text, usage = _vertex_structured(
                model, system_prompt, user_prompt, user_id, max_tokens,
            )
    else:
        # OpenAI 兼容:openai / siliconflow / dashscope / qwen 等
        if tool_schema:
            text, usage = _openai_function_call(
                api_id, model, system_prompt, user_prompt, tool_schema,
                user_id, timeout_sec, max_tokens,
            )
        else:
            text, usage = _openai_compat_json_mode(
                api_id, model, system_prompt, user_prompt,
                user_id, timeout_sec, max_tokens,
            )
    _maybe_record_usage(
        user_id=user_id, save_id=save_id, context_run_id=context_run_id,
        api_id=api_id, model=model, usage=usage,
        agent_kind=agent_kind, metadata_extra=metadata_extra,
    )
    return text, usage


def _maybe_record_usage(
    *,
    user_id: int | None,
    save_id: int | None,
    context_run_id: int | None,
    api_id: str,
    model: str,
    usage: dict,
    agent_kind: str | None,
    metadata_extra: dict | None,
    scenario: str = "tool",
) -> None:
    """内部自动 record_usage,失败静默(不影响主流程)。

    触发条件:user_id + agent_kind 都非空,且 usage 含至少一个 token 计数。
    scenario 默认 "tool"（harness 调用均为内部工具 agent）。
    """
    if not user_id or not agent_kind or not usage:
        return
    if not (usage.get("input_tokens") or usage.get("output_tokens")):
        return
    try:
        from platform_app.usage import record_usage
        meta = {"kind": agent_kind}
        if metadata_extra:
            meta.update(metadata_extra)
        record_usage(
            user_id=user_id, save_id=save_id, context_run_id=context_run_id,
            api_id=api_id, model_real_name=model,
            usage=usage, metadata=meta, scenario=scenario,
        )
    except Exception as exc:
        log.warning(f"[_harness] record_usage 失败(忽略): {exc}")


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
        system=_anthropic_cached_system(system_prompt),
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
        system=_anthropic_cached_system(
            system_prompt + "\n\n严格只输出 JSON,不要 markdown 围栏,不要解释。"
        ),
        messages=[{"role": "user", "content": user_prompt}],
    )
    usage = _anthropic_usage(resp)
    parts: list[str] = []
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text or "")
    return "".join(parts), usage


def _anthropic_cached_system(system_prompt: str) -> Any:
    """把 system prompt 包成 cache_control=ephemeral 的 block 列表。

    Anthropic prompt caching 规则:
    - system 改为 list of blocks,在长 block 末尾加 cache_control={"type":"ephemeral"}
    - 命中条件:同一 prefix 在 5 分钟内重复请求(对 agent 多次同 prompt 的场景非常划算)
    - 不足 1024 tokens 时不会缓存(但 API 不会报错,只是不省钱)
    - 节省 25% 输入成本(cached tokens 按 0.1× 计价)

    长度 < 200 字符的极短 prompt 不值得 cache(走原 string 路径)。
    """
    if not system_prompt or len(system_prompt) < 200:
        return system_prompt
    return [{
        "type": "text",
        "text": system_prompt,
        "cache_control": {"type": "ephemeral"},
    }]


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
    user_id: int | None,
    max_tokens: int,
) -> tuple[str, dict]:
    """Vertex call_structured 已设了 response_mime_type=application/json。"""
    from agents.gm import _VertexBackend
    backend = _VertexBackend(model=model, user_id=user_id)
    text = backend.call_structured(
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        max_tokens=max_tokens,
    )
    usage = getattr(backend, "last_usage", None) or {}
    return text, dict(usage) if isinstance(usage, dict) else {}


def _vertex_function_call(
    model: str,
    system_prompt: str,
    user_prompt: str,
    user_id: int | None,
    tool_schema: dict,
    max_tokens: int,
) -> tuple[str, dict]:
    """Vertex (Gemini) native function calling,强制 schema 校验。

    把 anthropic 风格 tool_schema(name/description/input_schema)翻译为 Gemini
    FunctionDeclaration,tool_config 设 ANY 模式强制必调函数。错误率比文本 JSON
    低 5-10×,跟 Anthropic native tool_use 对等。

    返回 (tool.args 序列化 JSON, usage_dict)。
    """
    from agents.gm import _VertexBackend
    backend = _VertexBackend(model=model, user_id=user_id)
    from google.genai import types

    fn_decl = types.FunctionDeclaration(
        name=tool_schema.get("name", "emit_payload"),
        description=tool_schema.get("description", ""),
        parameters=tool_schema.get("input_schema") or {"type": "object", "properties": {}},
    )
    tools = [types.Tool(function_declarations=[fn_decl])]
    tool_config = types.ToolConfig(
        function_calling_config=types.FunctionCallingConfig(
            mode="ANY",  # 强制必调,跟 anthropic tool_choice={type:"tool", name:...} 对等
            allowed_function_names=[fn_decl.name],
        ),
    )
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        max_output_tokens=max_tokens,
        temperature=0.1,
        thinking_config=types.ThinkingConfig(thinking_budget=0),
        tools=tools,
        tool_config=tool_config,
    )
    resp = backend.client.models.generate_content(
        model=backend.model_name,
        contents=[types.Content(role="user", parts=[types.Part(text=user_prompt)])],
        config=config,
    )
    backend._capture_usage(resp)
    usage = dict(getattr(backend, "last_usage", None) or {})

    # 抽 function_call.args
    try:
        for cand in (resp.candidates or []):
            for part in (cand.content.parts or []):
                fc = getattr(part, "function_call", None)
                if fc and fc.name == fn_decl.name:
                    args = dict(fc.args or {})
                    return json.dumps(args, ensure_ascii=False), usage
    except Exception:
        pass
    # 没拿到 function_call(罕见)→ 退化为文本(让调用方 parse)
    text = getattr(resp, "text", None) or ""
    return text.strip(), usage


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
        with _no_redirect_urlopen(req, timeout=timeout_sec) as resp:
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
        with _no_redirect_urlopen(req, timeout=timeout_sec) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        text = payload["choices"][0]["message"]["content"]
        usage = _openai_usage(payload.get("usage") or {})
        return text or "", usage


def _openai_function_call(
    api_id: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    tool_schema: dict,
    user_id: int | None,
    timeout_sec: int,
    max_tokens: int,
) -> tuple[str, dict]:
    """OpenAI / 兼容 endpoint native function calling,强制 schema 校验。

    把 anthropic 风格 tool_schema 翻译为 OpenAI tools format,tool_choice 强制必调。
    支持的 endpoint:OpenAI / SiliconFlow / DashScope / Qwen / DeepSeek 等。
    旧 endpoint 不支持 tools 时 → 降级到 response_format json_object。

    返回 (tool.arguments JSON, usage_dict)。
    """
    from platform_app.user_credentials import resolve_api_key
    cred = resolve_api_key(user_id, api_id)
    if not cred.get("key"):
        raise RuntimeError(f"无 {api_id} 凭证可用于 agent harness")
    import urllib.request
    base_url = cred.get("base_url_override") or _api_base_url(api_id)
    if not base_url:
        raise RuntimeError(f"未知 base_url for {api_id}")
    tool_name = tool_schema.get("name", "emit_payload")
    body_dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
        "tools": [{
            "type": "function",
            "function": {
                "name": tool_name,
                "description": tool_schema.get("description", ""),
                "parameters": tool_schema.get("input_schema")
                              or {"type": "object", "properties": {}},
            },
        }],
        "tool_choice": {"type": "function", "function": {"name": tool_name}},
    }
    body = json.dumps(body_dict).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cred['key']}",
        },
    )
    try:
        with _no_redirect_urlopen(req, timeout=timeout_sec) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        usage = _openai_usage(payload.get("usage") or {})
        msg = payload["choices"][0]["message"]
        tool_calls = msg.get("tool_calls") or []
        for tc in tool_calls:
            fn = tc.get("function") or {}
            if fn.get("name") == tool_name:
                args_text = fn.get("arguments") or "{}"
                # arguments 通常是 JSON-encoded string;直接返
                return args_text, usage
        # 没拿到 tool_call → 降级到文本内容(让调用方 parse)
        return msg.get("content") or "{}", usage
    except Exception:
        # endpoint 不支持 tools → 降级到 response_format json_object
        log.warning(f"[_harness] {api_id} tools 不支持,降级到 json_object")
        return _openai_compat_json_mode(
            api_id, model, system_prompt, user_prompt,
            user_id, timeout_sec, max_tokens,
        )


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
    # 薄委托 → model_registry.base_url_for(单一真源:live catalog 取 base_url,异常返空)。
    from model_registry import base_url_for
    return base_url_for(api_id)


# ── 模型偏好解析(给三个 agent 的 api_id/model 优先级解析共用)──────

def resolve_api_and_model(
    user_id: int | None,
    *,
    api_pref_key: str,
    model_pref_key: str,
    default_api: str | None = None,
    default_model: str | None = None,
    api_id_override: str | None = None,
    model_override: str | None = None,
) -> tuple[str, str]:
    """统一 api_id/model 解析,两级 fallback。

    优先级:
        1. override(传入参数,如来自 chat_pipeline 透传的 GM api_id)
        2. user_preferences[<api_pref_key>] / [<model_pref_key>](specific agent)
        3. user_preferences["agent.api_id"] / ["agent.model_real_name"](通配)
        4. default

    例:agent="black_swan",黑天鹅没单独配 model,user_preferences 里有
    "agent.api_id"="anthropic" + "agent.model_real_name"="claude-haiku-4-5"
    → 黑天鹅自动用 haiku。这是"所有子代理都用便宜模型"场景的常用配置,
    避免用户重复配 5 个命名空间。
    """
    from core.llm_backend import (
        DEFAULT_FALLBACK_API as _DEFAULT_FALLBACK_API,
        DEFAULT_FALLBACK_MODEL as _DEFAULT_FALLBACK_MODEL,
        first_user_model as _first_user_model,
        guard_byok_usable as _guard_byok_usable,
        resolve_preferred_api as _resolve_api,
        resolve_preferred_model as _resolve_model,
    )
    # 兜底默认统一引用 catalog.selected 对齐的常量(消除散落 'vertex_ai'/'gemini-3.5-flash' 漂移)。
    default_api = default_api or _DEFAULT_FALLBACK_API
    default_model = default_model or _DEFAULT_FALLBACK_MODEL
    user_default = _first_user_model(user_id)
    api_id = (
        api_id_override
        or _resolve_api(user_id, pref_key=api_pref_key)
        or _resolve_api(user_id, pref_key="agent.api_id")
        or (user_default[0] if user_default else None)
        or default_api
    )
    model = (
        model_override
        or _resolve_model(user_id, pref_key=model_pref_key)
        or _resolve_model(user_id, pref_key="agent.model_real_name")
        or (user_default[1] if user_default else None)
        or default_model
    )
    # BYOK 守卫:解析出的 provider 用户实际不可用(典型:默认 vertex_ai 或 stale 偏好,
    # 但用户没传 SA / 没配该 provider 的 key)→ 回退到用户配过 key 的第一个模型。
    # 否则所有 harness 子代理(curator / acceptance / extractor / 黑天鹅)对没 SA 的
    # 用户都会抛「未找到 Vertex SA」而整段失败。显式 override 不受此守卫影响。
    return _guard_byok_usable(
        user_id, api_id, model,
        allow_override=bool(api_id_override or model_override),
    )


def call_agent_tool_loop(
    api_id: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    user_id: int | None,
    *,
    tools: list[dict],
    terminal_tool_name: str,
    tool_handler: "Callable[[str, dict], str | dict]",
    max_iterations: int = 4,
    max_tokens: int = 1024,
    agent_kind: str | None = None,
    save_id: int | None = None,
    context_run_id: int | None = None,
) -> "tuple[dict | None, dict, list[dict]]":
    """Anthropic native multi-turn tool use 循环。返回 (terminal_tool_args, usage, trace)。

    trace 是 [(tool_name, args, result), ...] 让 caller 审计 LLM 中间动作。
    达 max_iterations 仍未调 terminal_tool → 返 (None, usage, trace)。

    非 anthropic provider:暂不支持,抛 NotImplementedError。
    """
    from typing import Callable as _Callable  # noqa: F401 (used above for annotation)

    if api_id != "anthropic":
        raise NotImplementedError(
            f"call_agent_tool_loop 仅支持 anthropic provider,当前: {api_id}"
        )

    from anthropic import Anthropic
    from platform_app.user_credentials import resolve_api_key

    result = resolve_api_key(user_id, "anthropic", env_fallback="ANTHROPIC_API_KEY")
    key = result.get("key")
    if not key:
        raise RuntimeError("找不到 Anthropic API Key for agent tool_loop")

    client = Anthropic(api_key=key)

    messages: list[dict] = [{"role": "user", "content": user_prompt}]
    trace: list[dict] = []
    cumulative_usage: dict = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_input_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    }

    for _iteration in range(max_iterations):
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=_anthropic_cached_system(system_prompt),
            messages=messages,
            tools=tools,
            tool_choice={"type": "auto"},
        )
        # 累计 usage
        u = _anthropic_usage(resp)
        for k in cumulative_usage:
            cumulative_usage[k] += u.get(k, 0)

        # 检查 content blocks
        tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]

        # 是否调了 terminal tool
        for block in tool_uses:
            if block.name == terminal_tool_name:
                _maybe_record_usage(
                    user_id=user_id, save_id=save_id, context_run_id=context_run_id,
                    api_id=api_id, model=model, usage=cumulative_usage,
                    agent_kind=agent_kind, metadata_extra=None,
                )
                return block.input or {}, cumulative_usage, trace

        # 没有任何 tool_use → LLM 只返了文本,终止
        if not tool_uses:
            break

        # 处理 non-terminal tool_use blocks,构造 tool_result 回应
        assistant_content = [
            _block_to_dict(b) for b in resp.content
        ]
        messages.append({"role": "assistant", "content": assistant_content})

        tool_results = []
        for block in tool_uses:
            raw_result = tool_handler(block.name, block.input or {})
            if isinstance(raw_result, dict):
                result_text = json.dumps(raw_result, ensure_ascii=False)
            else:
                result_text = str(raw_result)
            trace.append({"tool_name": block.name, "args": block.input or {}, "result": result_text})
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result_text,
            })
        messages.append({"role": "user", "content": tool_results})

    _maybe_record_usage(
        user_id=user_id, save_id=save_id, context_run_id=context_run_id,
        api_id=api_id, model=model, usage=cumulative_usage,
        agent_kind=agent_kind, metadata_extra=None,
    )
    return None, cumulative_usage, trace


def _block_to_dict(block: Any) -> dict:
    """把 Anthropic SDK content block 对象序列化为 dict(用于 messages history)。"""
    btype = getattr(block, "type", None)
    if btype == "text":
        return {"type": "text", "text": block.text or ""}
    if btype == "tool_use":
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": block.input or {},
        }
    # 其它类型 fallback
    try:
        return dict(block)
    except Exception:
        return {"type": str(btype)}


__all__ = ["call_agent_json", "call_agent_tool_loop", "resolve_api_and_model"]
