"""agents.gm.backends.openai_compat — OpenAI 兼容 backend。"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

from agents.gm.helpers import _openai_text_marker_loop


class _OpenAICompatBackend:
    """适配所有 OpenAI 兼容的 provider，只需要 base_url + env_key + model 名。"""

    # task 71：升 native tools，但 provider 兼容度不一（OpenAI/DeepSeek/豆包/
    # 智谱/Kimi/通义 都支持；SiliconFlow/OpenRouter 看模型；本地 ollama 通常
    # 不支持）。第一次调用 try/except，捕获到不支持时自动降级到 text marker
    # 协议（GameMaster.respond_stream_with_tools 会兜底）。
    supports_native_tools = True

    # 类级状态：记录已经验证过不支持 native tools 的 (api_id, model) 组合，
    # 同一进程内之后直接走 text marker 不再重试
    _unsupported_combos: set[tuple[str, str]] = set()

    def __init__(self, model: str, base_url: str, env_key: str, display_kind: str = "openai_compat",
                 user_id: int | None = None, api_id: str | None = None):
        from openai import OpenAI

        from platform_app.user_credentials import resolve_api_key
        result = resolve_api_key(user_id, api_id or display_kind, env_fallback=env_key)
        key = result.get("key")
        if not key:
            raise ValueError(f"找不到 {api_id or display_kind} 的 API Key（用户未配置且无环境变量 {env_key}）")
        # 用户覆盖了 base_url 的话优先用用户的
        effective_base = result.get("base_url_override") or base_url
        kwargs: dict[str, Any] = {"api_key": key}
        if effective_base:
            kwargs["base_url"] = effective_base
        self.client = OpenAI(**kwargs)
        self.model_name = model
        self.kind = display_kind
        self.api_id = api_id or display_kind
        self.last_usage: dict[str, int] = {}
        print(f"[GM] {display_kind} · {model} (base={effective_base or 'default'}, key from {result.get('source')})")

    def _to_messages(self, system: str, messages: list[dict]) -> list[dict]:
        out = []
        if system:
            out.append({"role": "system", "content": system})
        out.extend(messages)
        return out

    def call(self, system: str, messages: list[dict], max_tokens: int) -> str:
        resp = self.client.chat.completions.create(
            model=self.model_name,
            messages=self._to_messages(system, messages),
            max_tokens=max_tokens,
            temperature=0.9,
        )
        self._capture_usage(resp)
        return (resp.choices[0].message.content or "").strip()

    def _capture_usage(self, resp) -> None:
        usage = getattr(resp, "usage", None)
        if not usage:
            return
        # OpenAI 格式：prompt_tokens / completion_tokens / total_tokens
        # 部分 provider 还会带 prompt_tokens_details.cached_tokens
        cached = 0
        details = getattr(usage, "prompt_tokens_details", None)
        if details:
            cached = int(getattr(details, "cached_tokens", 0) or 0)
        reasoning = 0
        comp_details = getattr(usage, "completion_tokens_details", None)
        if comp_details:
            reasoning = int(getattr(comp_details, "reasoning_tokens", 0) or 0)
        self.last_usage = {
            "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            "cached_input_tokens": cached,
            "reasoning_tokens": reasoning,
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        }

    def call_structured(self, system: str, messages: list[dict], max_tokens: int) -> str:
        sys_text = (system or "") + "\n\n你必须只返回合法 JSON，不能包含 Markdown 代码围栏或解释文字。"
        resp = self.client.chat.completions.create(
            model=self.model_name,
            messages=self._to_messages(sys_text, messages),
            max_tokens=max_tokens,
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        return (resp.choices[0].message.content or "").strip()

    def stream(self, system: str, messages: list[dict], max_tokens: int) -> Iterator[str]:
        stream = self.client.chat.completions.create(
            model=self.model_name,
            messages=self._to_messages(system, messages),
            max_tokens=max_tokens,
            temperature=0.9,
            stream=True,
            stream_options={"include_usage": True},  # 末尾 chunk 带 usage
        )
        for chunk in stream:
            # 末尾 usage chunk 的 choices 可能为空
            try:
                if getattr(chunk, "usage", None):
                    self._capture_usage(chunk)
                if chunk.choices:
                    delta = chunk.choices[0].delta.content
                    if delta:
                        yield delta
            except Exception:
                continue

    def stream_with_mcp_loop(
        self,
        system: str,
        messages: list[dict],
        mcp_tools: list[dict[str, Any]],
        max_iterations: int,
        max_tokens: int,
        mcp_call,
    ) -> Iterator[dict[str, Any]]:
        """task 71：OpenAI 兼容 native function calling MCP 循环，带 fallback。

        OpenAI tools schema：
          tools=[{"type":"function","function":{"name":..., "description":..., "parameters":<jsonschema>}}]

        流式中 chunk.choices[0].delta.tool_calls[] 是 list of:
          { index: 0, id: "...", type: "function", function: {name?, arguments?} }
        arguments 是分片字符串，按 index 拼到完整 JSON。
        finish_reason == 'tool_calls' 时表示模型选择调工具，dispatch 后继续。

        Provider 不支持 tools 参数时（HTTP 400 / response 异常）→ 标记
        (api_id, model) 为 unsupported，本进程后续直接走 text marker fallback。
        """
        combo_key = (self.api_id, self.model_name)
        if combo_key in self._unsupported_combos:
            # 已知该 provider/model 不支持 tools → 立即降级到 text marker
            yield from _openai_text_marker_loop(self, system, messages, mcp_tools, max_iterations, max_tokens, mcp_call)
            return

        sep = "__"
        openai_tools = []
        for t in mcp_tools[:40]:
            sid = str(t.get("server_id", ""))
            tname = str(t.get("name", ""))
            if not sid or not tname:
                continue
            safe_sid = re.sub(r"[^A-Za-z0-9_-]", "_", sid)
            safe_tname = re.sub(r"[^A-Za-z0-9_-]", "_", tname)
            full_name = f"{safe_sid}{sep}{safe_tname}"[:64]
            schema_raw = t.get("schema") or {"type": "object", "properties": {}}
            if not isinstance(schema_raw, dict):
                schema_raw = {"type": "object", "properties": {}}
            if schema_raw.get("type") != "object":
                schema_raw = {"type": "object", "properties": schema_raw.get("properties", {})}
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": full_name,
                    "description": (t.get("description") or "")[:512],
                    "parameters": schema_raw,
                },
            })
        if not openai_tools:
            for chunk in self.stream(system, messages, max_tokens=max_tokens):
                yield {"type": "text", "text": chunk}
            return

        oai_messages = self._to_messages(system, messages)

        first_attempt = True
        for _iteration in range(max_iterations):
            tool_calls_buf: dict[int, dict[str, Any]] = {}  # index → {id, name, arguments}
            current_text = ""
            finish_reason: str | None = None
            try:
                stream = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=oai_messages,
                    max_tokens=max_tokens,
                    temperature=0.9,
                    tools=openai_tools,
                    tool_choice="auto",
                    stream=True,
                    stream_options={"include_usage": True},
                )
                for chunk in stream:
                    try:
                        if getattr(chunk, "usage", None):
                            self._capture_usage(chunk)
                        if not chunk.choices:
                            continue
                        choice = chunk.choices[0]
                        delta = getattr(choice, "delta", None)
                        if delta:
                            ctext = getattr(delta, "content", None)
                            if ctext:
                                current_text += ctext
                                yield {"type": "text", "text": ctext}
                            tcs = getattr(delta, "tool_calls", None) or []
                            for tc in tcs:
                                idx = getattr(tc, "index", 0) or 0
                                buf = tool_calls_buf.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                                if getattr(tc, "id", None):
                                    buf["id"] = tc.id
                                fn = getattr(tc, "function", None)
                                if fn:
                                    if getattr(fn, "name", None):
                                        buf["name"] = fn.name
                                    args_delta = getattr(fn, "arguments", None)
                                    if args_delta:
                                        buf["arguments"] += args_delta
                        fr = getattr(choice, "finish_reason", None)
                        if fr:
                            finish_reason = fr
                    except Exception:
                        continue
            except Exception as exc:
                # tools 不支持？标记并降级（只在第一次尝试时降级，避免循环中途异常被当成"不支持"）
                if first_attempt:
                    print(f"[gm] {self.api_id}/{self.model_name} native tools failed: {exc} → text marker fallback")
                    self._unsupported_combos.add(combo_key)
                    yield from _openai_text_marker_loop(self, system, messages, mcp_tools, max_iterations, max_tokens, mcp_call)
                    return
                # 后续 iteration 异常：let it bubble
                raise
            first_attempt = False

            if not tool_calls_buf:
                # 没有 tool_calls → 本轮结束
                return

            # 装回 assistant 消息（含 tool_calls）
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": current_text or None,
                "tool_calls": [
                    {
                        "id": buf["id"] or f"call_{idx}",
                        "type": "function",
                        "function": {"name": buf["name"], "arguments": buf["arguments"] or "{}"},
                    }
                    for idx, buf in sorted(tool_calls_buf.items())
                ],
            }
            oai_messages.append(assistant_msg)

            # dispatch + 装 tool result（OpenAI 用 role=tool, tool_call_id=...）
            for idx in sorted(tool_calls_buf.keys()):
                buf = tool_calls_buf[idx]
                full_name = buf["name"] or ""
                if sep in full_name:
                    server_id, _, tool_name = full_name.partition(sep)
                else:
                    server_id, tool_name = "", full_name
                try:
                    args = json.loads(buf["arguments"] or "{}")
                    if not isinstance(args, dict):
                        args = {}
                except Exception:
                    args = {}
                yield {
                    "type": "tool_call", "server_id": server_id,
                    "tool": tool_name, "arguments": args,
                }
                try:
                    result = mcp_call(server_id, tool_name, args)
                except Exception as exc:
                    result = {"ok": False, "error": f"call_tool 异常: {exc}"}
                yield {
                    "type": "tool_result", "ok": bool(result.get("ok")),
                    "result": result.get("result"), "error": result.get("error"),
                }
                truncated = json.dumps(result, ensure_ascii=False)[:2000]
                oai_messages.append({
                    "role": "tool",
                    "tool_call_id": buf["id"] or f"call_{idx}",
                    "content": truncated,
                })
        yield {"type": "text", "text": "\n\n【已达本轮工具调用上限 (限制为本次回复内的调用次数,下一条消息自动重置),本轮终止】"}
