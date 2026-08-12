"""agents.gm.backends.anthropic — Anthropic backend."""
from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Iterator
from typing import Any

import httpx

from core.logging import get_logger

log = get_logger(__name__)

# P1-1: 最多重试 1 次,仅对 timeout / 5xx 错误
_MAX_RETRIES = 1


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    from anthropic import APIStatusError
    if isinstance(exc, APIStatusError) and exc.status_code >= 500:
        return True
    return False


def _normalize_stop_reason(stop_reason) -> str | None:
    """Anthropic stop_reason 归一为截断信号 finish_reason。

    max_tokens → "length"(与 openai finish_reason / vertex 对齐;app.py 的截断告警只认
    == "length");其余(end_turn / tool_use / stop_sequence …)透传其名。空返回 None。
    此前 anthropic 抓到 stop_reason 只发 stop 事件、不写 last_usage → 截断对 anthropic 恒静默。"""
    if not stop_reason:
        return None
    s = str(stop_reason)
    return "length" if s == "max_tokens" else s


def _system_blocks(system: str, extra: str = "") -> list[dict[str, Any]]:
    """把 system 字符串包成带 cache_control 的 structured blocks。

    Anthropic prompt caching:
      - 最小 1024 token (sonnet/opus) — system 通常 6-7k 直接达标
      - ephemeral type → 5 min TTL,玩家一轮内多次 chat 自动复用
      - cache 写入 +25% input cost,读 -90% input cost
        → 玩家玩 ≥2 轮就回本,玩 ≥4 轮净省 50%+

    extra 可选:某些路径(call_structured)会在 system 末尾追加格式约束,
    那部分跟随 prefix 一起被 cache。
    """
    text = system + (extra or "")
    return [{
        "type": "text",
        "text": text,
        "cache_control": {"type": "ephemeral"},
    }]


class _AnthropicBackend:
    # task 57 (2026-05-25): 默认改为当前 Sonnet（最新平衡型）；
    # Opus 4.7 是 frontier 但成本 5×，留给用户显式选。
    def __init__(self, model: str = "claude-sonnet-4-6", user_id: int | None = None):
        from anthropic import Anthropic

        from platform_app.user_credentials import resolve_api_key
        # task: LLM 严格 BYOK — 生产模式(require_auth=True)拒绝任何平台 env fallback
        # 防用户白嫖你的 ANTHROPIC_API_KEY。Vertex 已在 core/vertex_sa.py 做同样隔离。
        try:
            from core.config import require_auth as _require_auth
            byok_only = bool(_require_auth())
        except Exception:
            byok_only = True  # 配置读不到时按更保守的生产策略
        env_fb = "" if (byok_only and user_id) else "ANTHROPIC_API_KEY"
        # ANTHROPIC_API_KEY 的 env 回退已由 resolve_api_key(env_fallback) 在非 byok_only 下完成,
        # 不再重复 os.environ 取一次。仅 EMBED_API_KEY 是 resolve_api_key 不覆盖的历史遗留二次回退。
        result = resolve_api_key(user_id, "anthropic", env_fallback=env_fb)
        key = result.get("key")
        if not key and not byok_only:
            # 仅本地/匿名开发模式才看 EMBED_API_KEY 这种历史遗留 fallback
            key = os.environ.get("EMBED_API_KEY")
        if not key:
            raise ValueError(
                "Anthropic API key 未配置。请在「设置 → API 设置」添加你自己的 Anthropic API Key。"
                "(测试服 LLM 调用必须 BYOK,平台不提供共享 key)"
            )
        # 读超时:单一来源 config.llm_timeout_seconds(用户 settings.request_timeout > env RPG_GM_TIMEOUT
        # > 部署默认:本地/桌面 1800s 给慢的本地大模型,服务器 300s)。
        try:
            from core.config import llm_timeout_seconds as _llm_to
            _read_to = _llm_to(user_id)
        except Exception:
            _read_to = float(os.environ.get("RPG_GM_TIMEOUT", "300"))
        # HTTP/2:一个 run 内多个流式调用(推理+工具轮)多路复用同一连接,省掉 ×N TCP+TLS 握手
        # (SDK 流式到 [DONE] 即停不 drain → HTTP/1.1 下 httpx 无法归还 socket;h2 关 stream≠关连接,
        # 故仍复用)。api.anthropic.com 支持 h2;safe_httpx_client 缺 h2 包时自动回退 1.1。
        # 复用 safe_httpx_client 取其 h2+回退+不跟随重定向;固定官方端点,SSRF 守卫为无害冗余。
        from core.outbound import safe_httpx_client
        self.client = Anthropic(
            api_key=key,
            timeout=httpx.Timeout(_read_to, connect=10.0),
            http_client=safe_httpx_client(timeout=_read_to),
        )
        self.model_name = model
        self.user_id = user_id  # task 141: 给 _thinking_param 用
        self.last_usage: dict[str, int] = {}
        log.info(f"[GM] Anthropic · {self.model_name} (key from {result.get('source', 'env')})")

    def _thinking_param(self) -> dict | None:
        """task 141: 按用户偏好返 Anthropic Extended Thinking 参数。
        budget=0 → None;>0 → {type: "enabled", budget_tokens: N}。
        模型不支持时 Anthropic SDK 会忽略 / 返错,由 call/stream try/except 兜底。
        """
        try:
            from ._effort import resolve_budget_tokens
            budget = resolve_budget_tokens(self.user_id, "anthropic", self.model_name)
            if budget <= 0:
                return None
            return {"type": "enabled", "budget_tokens": int(budget)}
        except Exception as exc:
            log.warning(f"[anthropic] _thinking_param resolve failed: {exc}")
            return None

    def _sampling_extra(self, has_thinking: bool) -> dict:
        """反馈#93:用户生成参数预设(temperature/top_p/top_k)。只带用户显式设过的键。
        Extended Thinking 开启时 Anthropic 限制采样参数(temperature 须为默认)→ 全跳过最稳。"""
        if has_thinking:
            return {}
        try:
            from ._gen_params import resolve_gen_params
            gen = resolve_gen_params(self.user_id)
        except Exception:
            return {}
        out: dict = {}
        for k in ("temperature", "top_p", "top_k"):
            if k in gen:
                out[k] = gen[k]
        return out

    def call(self, system: str, messages: list[dict], max_tokens: int) -> str:
        last_exc: Exception | None = None
        # task 141: thinking 模型 max_tokens 要 ≥ budget_tokens + 输出预算,否则 SDK 报错
        _thinking = self._thinking_param()
        if _thinking:
            max_tokens = max(max_tokens, int(_thinking["budget_tokens"]) + 1024)
        _extra = {"thinking": _thinking} if _thinking else {}
        _extra.update(self._sampling_extra(bool(_thinking)))  # 反馈#93:接入生成参数预设
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = self.client.messages.create(
                    model=self.model_name,
                    max_tokens=max_tokens,
                    system=_system_blocks(system),
                    messages=messages,
                    **_extra,
                )
                break
            except Exception as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES and _is_retryable(exc):
                    log.warning(f"[anthropic] call attempt {attempt+1} failed ({exc}), retrying…")
                    time.sleep(1.0)
                    continue
                raise
        else:
            raise last_exc  # type: ignore[misc]
        usage = getattr(resp, "usage", None)
        if usage:
            self.last_usage = {
                "input_tokens": int(getattr(usage, "input_tokens", 0)),
                "output_tokens": int(getattr(usage, "output_tokens", 0)),
                "cached_input_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
                "cache_creation_input_tokens": int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
            }
            self.last_usage["total_tokens"] = self.last_usage["input_tokens"] + self.last_usage["output_tokens"]
            _fr = _normalize_stop_reason(getattr(resp, "stop_reason", None))
            if _fr:
                self.last_usage["finish_reason"] = _fr
        return resp.content[0].text.strip()

    def call_structured(self, system: str, messages: list[dict], max_tokens: int) -> str:
        resp = self.client.messages.create(
            model=self.model_name,
            max_tokens=max_tokens,
            temperature=0.1,
            system=_system_blocks(system, "\n\n你必须只返回合法 JSON，不能包含 Markdown 代码围栏或解释文字。"),
            messages=messages,
        )
        # 同 call()/stream():把本次 usage 写入 self.last_usage,供 record_usage 取
        try:
            usage = getattr(resp, "usage", None)
            if usage:
                self.last_usage = {
                    "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
                    "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
                    "cached_input_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
                "cache_creation_input_tokens": int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
                }
                self.last_usage["total_tokens"] = (
                    self.last_usage["input_tokens"] + self.last_usage["output_tokens"]
                )
                _fr = _normalize_stop_reason(getattr(resp, "stop_reason", None))
                if _fr:
                    self.last_usage["finish_reason"] = _fr
        except Exception:
            pass
        return resp.content[0].text.strip()

    def stream(self, system: str, messages: list[dict], max_tokens: int) -> Iterator[str]:
        # task 141: thinking 模型 max_tokens 必须容下 budget;不支持 thinking 的模型走旧路径
        _thinking = self._thinking_param()
        if _thinking:
            max_tokens = max(max_tokens, int(_thinking["budget_tokens"]) + 1024)
        _extra = {"thinking": _thinking} if _thinking else {}
        _extra.update(self._sampling_extra(bool(_thinking)))  # 反馈#93:接入生成参数预设
        # 思考流(thinking_delta):此处 thinking 生效(_thinking_param),但本方法契约是
        # Iterator[str],由 master.py / helpers.py / chat_pipeline/gm.py 以字符串拼接消费
        # (out-of-scope,不能改契约)。text_stream 只吐正文文本、天然不含思考 → 思考 token 被花掉
        # 却无法作为 {"type":"reasoning"} 事件透传(需要 dict 事件通道,纯字符串流承载不了)。
        # 关键安全属性(思考不污染叙事正文)已由 text_stream 保证;展示思考流待事件化流式改造。
        with self.client.messages.stream(
            model=self.model_name,
            max_tokens=max_tokens,
            system=_system_blocks(system),
            messages=messages,
            **_extra,
        ) as stream:
            yield from stream.text_stream
            # stream 结束后从 final_message 抽 usage
            try:
                final = stream.get_final_message()
                usage = getattr(final, "usage", None)
                if usage:
                    self.last_usage = {
                        "input_tokens": int(getattr(usage, "input_tokens", 0)),
                        "output_tokens": int(getattr(usage, "output_tokens", 0)),
                        "cached_input_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
                "cache_creation_input_tokens": int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
                    }
                    self.last_usage["total_tokens"] = self.last_usage["input_tokens"] + self.last_usage["output_tokens"]
                    _fr = _normalize_stop_reason(getattr(final, "stop_reason", None))
                    if _fr:
                        self.last_usage["finish_reason"] = _fr
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
          - {"type": "reasoning", "text": "..."}       # thinking_delta 思考流(仅展示)
          - {"type": "thinking_block", "block": {...}}  # 完整 thinking/redacted_thinking block
                                                        # (含 signature),供工具循环原样装回历史
          - {"type": "tool_use_block", "id": "...", "name": "...", "input": {...}}
          - {"type": "stop", "stop_reason": "end_turn"|"tool_use"|...}
        每个 tool_use_block 完整产生后才 yield（input JSON 已合并完）。

        Extended Thinking(task 141):按用户 effort 偏好启用(_thinking_param,与 call()/stream()
        同一读取方式;对照 vertex/openai 的 effort)。budget=0/None → 不带 thinking 参数(保持关闭
        语义)。**Anthropic 硬约束**:thinking+tool_use 时,含 thinking block 的 assistant 回合必须
        把每个 thinking/redacted_thinking block(连 signature)原样装回后续请求的 messages,否则
        后续工具轮 400。故此处把每个 thinking/redacted_thinking block 从流式事件重建成
        {"type":"thinking_block", ...} 事件,由 stream_with_mcp_loop 装 assistant 消息时**原样保留、
        且置于 content 首位**(非交错思考下 Anthropic 恒先产 thinking 再产 text/tool_use,置首即
        还原原始顺序)。
        """
        current_block: dict[str, Any] | None = None
        partial_json_buf = ""
        stop_reason: str | None = None
        # 重建中的 thinking / redacted_thinking block(含 signature),content_block_stop 时 yield。
        current_thinking: dict[str, Any] | None = None
        current_redacted: dict[str, Any] | None = None
        # 与 call()/stream() 同源:按用户 effort 偏好取 thinking 参数;budget<=0 → None(不启用)。
        _thinking = self._thinking_param()
        if _thinking:
            # thinking 模型 max_tokens 必须容下 budget(留输出余量),否则 SDK 报错。
            max_tokens = max(max_tokens, int(_thinking["budget_tokens"]) + 1024)
        _extra = {"thinking": _thinking} if _thinking else {}
        with self.client.messages.stream(
            model=self.model_name,
            max_tokens=max_tokens,
            system=_system_blocks(system),
            messages=messages,
            tools=anthropic_tools,
            tool_choice={"type": "auto"},
            **_extra,
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
                    elif bt == "thinking":
                        # 边流边攒思考文本 + signature;signature 通常在块尾以 signature_delta 到达。
                        current_thinking = {"type": "thinking", "thinking": "", "signature": ""}
                    elif bt == "redacted_thinking":
                        # redacted_thinking = 被安全层加密的思考块:data 在 start 事件即完整给出,
                        # 无 delta、无明文。SDK 语义要求原样透传保留(round-trip)以满足 API 合规,
                        # 但**不作为 reasoning 展示**(无明文可显示,展示加密块也无意义)。
                        current_redacted = {
                            "type": "redacted_thinking",
                            "data": getattr(block, "data", "") or "",
                        }
                elif et == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    dt = getattr(delta, "type", None)
                    if dt == "text_delta":
                        text = getattr(delta, "text", "") or ""
                        if text:
                            yield {"type": "text", "text": text}
                    elif dt == "input_json_delta":
                        partial_json_buf += getattr(delta, "partial_json", "") or ""
                    elif dt == "thinking_delta":
                        # 思考流:攒进 block(供 round-trip)+ yield reasoning 事件(展示)。
                        # 事件形态逐字对照 openai_compat/vertex 的 {"type":"reasoning","text":...}。
                        ttext = getattr(delta, "thinking", "") or ""
                        if current_thinking is not None:
                            current_thinking["thinking"] += ttext
                        if ttext:
                            yield {"type": "reasoning", "text": ttext}
                    elif dt == "signature_delta":
                        # signature 是 thinking block 的完整性签名,必须随 block 原样回传,否则
                        # 后续工具轮 400。不展示。累加以兼容单/多分片两种到达形态。
                        sig = getattr(delta, "signature", "") or ""
                        if current_thinking is not None:
                            current_thinking["signature"] += sig
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
                    elif current_thinking is not None:
                        # 完整 thinking block(含 signature)→ 供 loop 原样装回历史。
                        yield {"type": "thinking_block", "block": current_thinking}
                        current_thinking = None
                    elif current_redacted is not None:
                        # redacted_thinking:同样 round-trip 保留,但不产 reasoning 事件。
                        yield {"type": "thinking_block", "block": current_redacted}
                        current_redacted = None
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
                "cache_creation_input_tokens": int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
                    }
                    self.last_usage["total_tokens"] = self.last_usage["input_tokens"] + self.last_usage["output_tokens"]
                    _fr = _normalize_stop_reason(stop_reason or getattr(final, "stop_reason", None))
                    if _fr:
                        self.last_usage["finish_reason"] = _fr
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
        from agents.gm.backends import _tiered
        from core.config import tiered_tools_enabled as _tiered_enabled
        from core.config import tool_window_size as _tool_window

        def _mk(t):
            """unified tool → Anthropic tool 定义;缺 sid/name 返回 None。"""
            sid = str(t.get("server_id", ""))
            tname = str(t.get("name", ""))
            if not sid or not tname:
                return None
            safe_sid = re.sub(r"[^A-Za-z0-9_-]", "_", sid)
            safe_tname = re.sub(r"[^A-Za-z0-9_-]", "_", tname)
            full_name = f"{safe_sid}{sep}{safe_tname}"[:64]
            schema = t.get("schema") or {"type": "object", "properties": {}}
            if not isinstance(schema, dict):
                schema = {"type": "object", "properties": {}}
            if schema.get("type") != "object":
                schema = {"type": "object", "properties": schema.get("properties", {})}
            return {
                "name": full_name,
                "description": (t.get("description") or "")[:512],
                "input_schema": schema,
            }

        # 阶梯化:窗口内完整 schema 直发,窗口外进 load_tools 目录(原 [:40] 硬截断会**丢弃**
        # 第 41+ 个工具 → 模型够不到,只能幻觉式叙述「已调用」)。append-only 不破坏前缀缓存。
        window_tools, overflow_index, catalog_lines = _tiered.split_window(
            mcp_tools, _tool_window(), _tiered_enabled())
        loaded_overflow: set[str] = set()
        anthropic_tools = []
        for t in window_tools:
            m = _mk(t)
            if m:
                anthropic_tools.append(m)
        if catalog_lines:
            anthropic_tools.append({
                "name": _tiered.LOAD_TOOLS_FULL_NAME,
                "description": _tiered.load_tools_description(catalog_lines),
                "input_schema": _tiered.LOAD_TOOLS_PARAMS,
            })
        # 给 tools 数组末尾加 cache_control breakpoint → 把 system+tools 整段稳定前缀纳入缓存
        # (窗口+目录每轮一致;load 后新 append 的工具在 breakpoint 之后,不影响前缀命中)。
        if anthropic_tools:
            anthropic_tools[-1] = {**anthropic_tools[-1], "cache_control": {"type": "ephemeral"}}
        if not anthropic_tools:
            for chunk in self.stream(system, messages, max_tokens=max_tokens):
                yield {"type": "text", "text": chunk}
            return

        for _iteration in range(max_iterations):
            pending_uses: list[dict[str, Any]] = []
            accumulated_blocks: list[dict[str, Any]] = []
            # 本轮 assistant 回合的 thinking / redacted_thinking block(含 signature),按到达顺序。
            # 有工具调用时必须原样装回 assistant 历史(Anthropic 硬约束),否则下一工具轮 400。
            thinking_blocks: list[dict[str, Any]] = []
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
                elif et == "reasoning":
                    # 思考流透传给消费侧(chat_pipeline/gm.py 已就绪:落 _turn_reasoning + 前端显示)。
                    yield ev
                elif et == "thinking_block":
                    # 不外抛,仅收集用于 round-trip(装回 assistant 历史)。
                    _blk = ev.get("block")
                    if _blk:
                        thinking_blocks.append(_blk)
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
            # thinking / redacted_thinking block 置于 content 首位:Anthropic 要求含 thinking 的
            # assistant 回合把这些块(连 signature)原样回传,且它们在原始回合中先于 text/tool_use
            # (非交错思考),置首即还原顺序。省略或改动会导致下一工具轮 400。
            assistant_content.extend(thinking_blocks)
            if current_text:
                assistant_content.append({"type": "text", "text": current_text})
            assistant_content.extend(accumulated_blocks)
            messages.append({"role": "assistant", "content": assistant_content})
            tool_result_blocks: list[dict[str, Any]] = []
            for use in pending_uses:
                # 阶梯化:load_tools 不路由 dispatcher,把目录里的工具 schema append 进
                # anthropic_tools(只增不重排)→ 下一轮迭代该工具即可直接调用。
                if _tiered.is_load_tools(use["server_id"], use["tool_name"]):
                    newly, ack = _tiered.resolve_load(use["arguments"], overflow_index, loaded_overflow)
                    for t in newly:
                        m = _mk(t)
                        if m:
                            anthropic_tools.append(m)
                    yield {"type": "tool_result", "ok": True, "result": ack, "error": None}
                    tool_result_blocks.append({
                        "type": "tool_result", "tool_use_id": use["id"],
                        "content": ack, "is_error": False,
                    })
                    continue
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
