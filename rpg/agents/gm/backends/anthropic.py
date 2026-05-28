"""agents.gm.backends.anthropic — Anthropic backend."""
from __future__ import annotations
import json
import os
import re
from collections.abc import Iterator
from typing import Any


class _AnthropicBackend:
    # task 57 (2026-05-25): 默认改为当前 Sonnet（最新平衡型）；
    # Opus 4.7 是 frontier 但成本 5×，留给用户显式选。
    def __init__(self, model: str = "claude-sonnet-4-6", user_id: int | None = None):
        from anthropic import Anthropic
        from platform_app.user_credentials import resolve_api_key
        result = resolve_api_key(user_id, "anthropic", env_fallback="ANTHROPIC_API_KEY")
        key = result.get("key") or os.environ.get("EMBED_API_KEY")
        if not key:
            raise ValueError("找不到 Anthropic API Key（用户未配置且无环境变量）")
        self.client = Anthropic(api_key=key)
        self.model_name = model
        self.last_usage: dict[str, int] = {}
        print(f"[GM] Anthropic · {self.model_name} (key from {result.get('source', 'env')})")

    def call(self, system: str, messages: list[dict], max_tokens: int) -> str:
        resp = self.client.messages.create(
            model=self.model_name,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        )
        usage = getattr(resp, "usage", None)
        if usage:
            self.last_usage = {
                "input_tokens": int(getattr(usage, "input_tokens", 0)),
                "output_tokens": int(getattr(usage, "output_tokens", 0)),
                "cached_input_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            }
            self.last_usage["total_tokens"] = self.last_usage["input_tokens"] + self.last_usage["output_tokens"]
        return resp.content[0].text.strip()

    def call_structured(self, system: str, messages: list[dict], max_tokens: int) -> str:
        resp = self.client.messages.create(
            model=self.model_name,
            max_tokens=max_tokens,
            temperature=0.1,
            system=system + "\n\n你必须只返回合法 JSON，不能包含 Markdown 代码围栏或解释文字。",
            messages=messages,
        )
        return resp.content[0].text.strip()

    def stream(self, system: str, messages: list[dict], max_tokens: int) -> Iterator[str]:
        with self.client.messages.stream(
            model=self.model_name,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        ) as stream:
            for text in stream.text_stream:
                yield text
            # stream 结束后从 final_message 抽 usage
            try:
                final = stream.get_final_message()
                usage = getattr(final, "usage", None)
                if usage:
                    self.last_usage = {
                        "input_tokens": int(getattr(usage, "input_tokens", 0)),
                        "output_tokens": int(getattr(usage, "output_tokens", 0)),
                        "cached_input_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
                    }
                    self.last_usage["total_tokens"] = self.last_usage["input_tokens"] + self.last_usage["output_tokens"]
            except Exception:
                pass

    # task 66：native tool_use 流式 — 替代文本协议 <<TOOL_CALL>>。
    # 错误率比 text marker 低 5-10×，input_schema 校验直接由 Anthropic 做。
    supports_native_tools = True

    def stream_with_tools_native(
        self,
        system: str,
        messages: list[dict],
        anthropic_tools: list[dict],
        max_tokens: int,
    ) -> Iterator[dict[str, Any]]:
        """流式 + native tool_use。yields:
          - {"type": "text", "text": "..."}
          - {"type": "tool_use_block", "id": "...", "name": "...", "input": {...}}
          - {"type": "stop", "stop_reason": "end_turn"|"tool_use"|...}
        每个 tool_use_block 完整产生后才 yield（input JSON 已合并完）。
        """
        current_block: dict[str, Any] | None = None
        partial_json_buf = ""
        stop_reason: str | None = None
        with self.client.messages.stream(
            model=self.model_name,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            tools=anthropic_tools,
            tool_choice={"type": "auto"},
        ) as stream:
            for event in stream:
                et = getattr(event, "type", None)
                if et == "content_block_start":
                    block = getattr(event, "content_block", None)
                    bt = getattr(block, "type", None)
                    if bt == "tool_use":
                        current_block = {
                            "id": getattr(block, "id", ""),
                            "name": getattr(block, "name", ""),
                        }
                        partial_json_buf = ""
                elif et == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    dt = getattr(delta, "type", None)
                    if dt == "text_delta":
                        text = getattr(delta, "text", "") or ""
                        if text:
                            yield {"type": "text", "text": text}
                    elif dt == "input_json_delta":
                        partial_json_buf += getattr(delta, "partial_json", "") or ""
                elif et == "content_block_stop":
                    if current_block is not None:
                        try:
                            parsed = json.loads(partial_json_buf or "{}")
                            if not isinstance(parsed, dict):
                                parsed = {}
                        except Exception:
                            parsed = {}
                        yield {
                            "type": "tool_use_block",
                            "id": current_block["id"],
                            "name": current_block["name"],
                            "input": parsed,
                        }
                        current_block = None
                        partial_json_buf = ""
                elif et == "message_delta":
                    delta = getattr(event, "delta", None)
                    if delta:
                        sr = getattr(delta, "stop_reason", None)
                        if sr:
                            stop_reason = sr
            # capture usage
            try:
                final = stream.get_final_message()
                usage = getattr(final, "usage", None)
                if usage:
                    self.last_usage = {
                        "input_tokens": int(getattr(usage, "input_tokens", 0)),
                        "output_tokens": int(getattr(usage, "output_tokens", 0)),
                        "cached_input_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
                    }
                    self.last_usage["total_tokens"] = self.last_usage["input_tokens"] + self.last_usage["output_tokens"]
            except Exception:
                pass
        yield {"type": "stop", "stop_reason": stop_reason or "end_turn"}

    def stream_with_mcp_loop(
        self,
        system: str,
        messages: list[dict],
        mcp_tools: list[dict[str, Any]],
        max_iterations: int,
        max_tokens: int,
        mcp_call,
    ) -> Iterator[dict[str, Any]]:
        """task 66：完整的 native tool_use MCP 循环（Anthropic 路径）。

        每个 backend 拥有自己的 loop，封装该 provider 的：
        - 工具列表 → 原生格式
        - 流式 event → 统一事件
        - assistant + tool_result 消息装回历史的具体形态
        """
        sep = "__"  # server_id 与 tool_name 分隔符
        # MCP → Anthropic tools
        anthropic_tools = []
        for t in mcp_tools[:40]:
            sid = str(t.get("server_id", ""))
            tname = str(t.get("name", ""))
            if not sid or not tname:
                continue
            safe_sid = re.sub(r"[^A-Za-z0-9_-]", "_", sid)
            safe_tname = re.sub(r"[^A-Za-z0-9_-]", "_", tname)
            full_name = f"{safe_sid}{sep}{safe_tname}"[:64]
            schema = t.get("schema") or {"type": "object", "properties": {}}
            if not isinstance(schema, dict):
                schema = {"type": "object", "properties": {}}
            if schema.get("type") != "object":
                schema = {"type": "object", "properties": schema.get("properties", {})}
            anthropic_tools.append({
                "name": full_name,
                "description": (t.get("description") or "")[:512],
                "input_schema": schema,
            })
        if not anthropic_tools:
            for chunk in self.stream(system, messages, max_tokens=max_tokens):
                yield {"type": "text", "text": chunk}
            return

        for iteration in range(max_iterations):
            pending_uses: list[dict[str, Any]] = []
            accumulated_blocks: list[dict[str, Any]] = []
            current_text = ""
            for ev in self.stream_with_tools_native(
                system, messages, anthropic_tools, max_tokens=max_tokens,
            ):
                et = ev.get("type")
                if et == "text":
                    text = ev.get("text", "")
                    if text:
                        current_text += text
                        yield {"type": "text", "text": text}
                elif et == "tool_use_block":
                    full_name = ev.get("name", "")
                    if sep in full_name:
                        server_id, _, tool_name = full_name.partition(sep)
                    else:
                        server_id, tool_name = "", full_name
                    arguments = ev.get("input") or {}
                    tu_id = ev.get("id", "")
                    pending_uses.append({
                        "id": tu_id, "server_id": server_id,
                        "tool_name": tool_name, "arguments": arguments,
                    })
                    accumulated_blocks.append({
                        "type": "tool_use", "id": tu_id,
                        "name": full_name, "input": arguments,
                    })
                    yield {
                        "type": "tool_call", "server_id": server_id,
                        "tool": tool_name, "arguments": arguments,
                    }
                elif et == "stop":
                    break
            if not pending_uses:
                return
            assistant_content: list[dict[str, Any]] = []
            if current_text:
                assistant_content.append({"type": "text", "text": current_text})
            assistant_content.extend(accumulated_blocks)
            messages.append({"role": "assistant", "content": assistant_content})
            tool_result_blocks: list[dict[str, Any]] = []
            for use in pending_uses:
                try:
                    result = mcp_call(use["server_id"], use["tool_name"], use["arguments"])
                except Exception as exc:
                    result = {"ok": False, "error": f"call_tool 异常: {exc}"}
                yield {
                    "type": "tool_result", "ok": bool(result.get("ok")),
                    "result": result.get("result"), "error": result.get("error"),
                }
                truncated = json.dumps(result, ensure_ascii=False)[:2000]
                tool_result_blocks.append({
                    "type": "tool_result", "tool_use_id": use["id"],
                    "content": truncated, "is_error": not bool(result.get("ok")),
                })
            messages.append({"role": "user", "content": tool_result_blocks})
        yield {"type": "text", "text": "\n\n【已达本轮工具调用上限 (限制为本次回复内的调用次数,下一条消息自动重置),本轮终止】"}
