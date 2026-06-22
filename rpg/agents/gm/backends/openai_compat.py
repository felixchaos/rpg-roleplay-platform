"""agents.gm.backends.openai_compat — OpenAI 兼容 backend。"""
from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from typing import Any, Dict, Set

import httpx

from agents.gm.helpers import _openai_text_marker_loop
from core.logging import get_logger

log = get_logger(__name__)

# P1-1: 最多重试 1 次,仅对 timeout / 5xx 错误
_MAX_RETRIES = 1


def _is_retryable_openai(exc: Exception) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    try:
        from openai import APIStatusError
        if isinstance(exc, APIStatusError) and exc.status_code >= 500:
            return True
    except ImportError:
        pass
    return False


def _is_temperature_rejected(exc: Exception) -> bool:
    """provider 拒绝自定义 temperature(如 moonshot kimi 部分模型「only 1 is allowed for
    this model」、openai o-series/gpt-5 reasoning 只接受 temperature=1)。这类是 400
    BadRequest 且报错文本点名 temperature —— 据此自愈:去掉 temperature 用模型默认重试。"""
    try:
        from openai import BadRequestError
        if not isinstance(exc, BadRequestError):
            return False
    except ImportError:
        return False
    return "temperature" in str(exc).lower()


def _is_tools_unsupported(exc: Exception) -> bool:
    """仅「400 BadRequest」才视为 provider 不支持 tools 参数 → 降级 text marker。
    429 限流 / 401 鉴权 / 5xx / 超时 等是瞬时/配置错误,**不可**据此把 (api,model) 永久标记为
    不支持(类级 set 进程内共享,会让该 worker 此后所有该模型 GM 对话静默降级,且难复现)。"""
    try:
        from openai import BadRequestError
    except ImportError:
        return False
    return isinstance(exc, BadRequestError)


class _OpenAICompatBackend:
    """适配所有 OpenAI 兼容的 provider，只需要 base_url + env_key + model 名。"""

    # task 71：升 native tools，但 provider 兼容度不一（OpenAI/DeepSeek/豆包/
    # 智谱/Kimi/通义 都支持；SiliconFlow/OpenRouter 看模型；本地 ollama 通常
    # 不支持）。第一次调用 try/except，捕获到不支持时自动降级到 text marker
    # 协议（GameMaster.respond_stream_with_tools 会兜底）。
    supports_native_tools = True

    # 类级状态：记录已经验证过不支持 native tools 的 (api_id, model) 组合，
    # 同一进程内之后直接走 text marker 不再重试。
    # 已知限制：进程内缓存，多 worker 模式下各自独立学习（不跨进程共享），可接受——
    # 最坏情况是同一 (api_id, model) 在多 worker 上各自发一次失败请求后才降级。
    _unsupported_combos: Set[tuple[str, str]] = set()

    # 类级状态：记录拒绝自定义 temperature(只接受默认/=1)的 (api_id, model) 组合，
    # 同一进程内之后直接不发 temperature。见 _create / _is_temperature_rejected。
    # 已知限制：进程内缓存，多 worker 模式下各自独立学习（不跨进程共享），可接受。
    _fixed_temp_combos: Set[tuple[str, str]] = set()

    def _create(self, **kwargs):
        """self.client.chat.completions.create 的包装,带 temperature 自愈。

        moonshot kimi 部分模型 / openai o-series 等「只允许 temperature=1」,平台默认发
        0.9/0.1 会被 400 拒(原表现:整轮失败,用户只见随机错误码)。这里首次被拒后去掉
        temperature 用模型默认重试**并记忆**,本进程同 (api_id, model) 后续直接不发 →
        当轮即成功,不再失败。stream=True 时请求在 create() 即发出,400 也在此抛,可拦。
        """
        combo = (self.api_id, self.model_name, self.user_id)
        if combo in self._fixed_temp_combos:
            kwargs.pop("temperature", None)
        try:
            return self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            if "temperature" in kwargs and _is_temperature_rejected(exc):
                self._fixed_temp_combos.add(combo)
                kwargs.pop("temperature", None)
                log.info(f"[GM] {self.api_id}/{self.model_name} 拒绝自定义 temperature → 用模型默认重试")
                return self.client.chat.completions.create(**kwargs)
            raise

    def __init__(self, model: str, base_url: str, env_key: str, display_kind: str = "openai_compat",
                 user_id: int | None = None, api_id: str | None = None):
        from openai import OpenAI

        from platform_app.user_credentials import resolve_api_key
        # task: LLM 严格 BYOK — 生产模式拒绝平台 env fallback,防用户白嫖你的 OPENAI_API_KEY / DEEPSEEK_API_KEY 等
        try:
            from core.config import require_auth as _require_auth
            byok_only = bool(_require_auth())
        except Exception:
            byok_only = True
        env_fb = "" if (byok_only and user_id) else env_key
        result = resolve_api_key(user_id, api_id or display_kind, env_fallback=env_fb)
        key = result.get("key")
        if not key:
            raise ValueError(
                f"{api_id or display_kind} 的 API Key 未配置。请在「设置 → API 设置」添加你自己的 API Key。"
                "(测试服 LLM 调用必须 BYOK,平台不提供共享 key)"
            )
        # 用户覆盖了 base_url 的话优先用用户的
        effective_base = result.get("base_url_override") or base_url
        import os as _os
        # 读超时原 120s 太紧:长回合(deepseek-v4-pro 等)常被中途切断浪费 token。
        # 提到 300s,可用 RPG_GM_TIMEOUT 调。
        _read_to = float(_os.environ.get("RPG_GM_TIMEOUT", "300"))
        # 出站代理:用户在凭据里配的 proxy URL。**仅本地模式(非 require_auth)才真正使用** ——
        # 托管多用户后端永不使用用户 proxy(防 SSRF:代理合法地可指向 127.0.0.1,无法用「禁私网」
        # 校验拦截;故把使用面收窄到自托管单用户场景)。本地梯子用户选「HTTP 代理」即生效。
        _proxy = (result.get("proxy") or "").strip()
        # 出站代理仅本地模式生效(托管多用户后端永不用用户 proxy,见上注释)。
        _use_proxy = _proxy if (_proxy and not byok_only) else None
        if _use_proxy:
            log.info(f"[GM] {display_kind} 出站走用户代理 {_use_proxy}")
        # 覆盖 openai SDK 默认 UA(`OpenAI/Python x.y.z`)→ 浏览器 UA。否则挂在 Cloudflare 后的
        # 中转站会按 UA 用 WAF 把它当 AI 爬虫拦掉(403「Your request was blocked」/ error 1010),
        # 导致这类中转站聊天/校验/拉取模型全部「不可访问」。详见 core.outbound_ua(已实测)。
        from core.outbound_ua import openai_default_headers
        from core.outbound import safe_httpx_client
        kwargs: dict[str, Any] = {
            "api_key": key,
            "timeout": httpx.Timeout(_read_to, connect=10.0),
            "default_headers": openai_default_headers(),
            # SEC(H-5 + 审计): base_url_override 是 user/admin 可控 → 必须走 SSRF 安全出站层。
            # 裸 httpx.Client 只 follow_redirects=False,挡 30x 但挡不住 DNS rebinding(写时闸过后
            # TTL 过期即可把域名指向 169.254.169.254 / 内网)。safe_httpx_client 在传输层 use-time
            # 重解析 + 私网校验(服务器模式 fail-closed;本地/代理模式 no-op,不影响本机大模型/梯子)。
            "http_client": safe_httpx_client(timeout=_read_to, proxy=_use_proxy),
        }
        if effective_base:
            kwargs["base_url"] = effective_base
        self.client = OpenAI(**kwargs)
        self.model_name = model
        self.kind = display_kind
        self.api_id = api_id or display_kind
        self.user_id = user_id  # task 141: 给 _reasoning_param 用
        self.last_usage: dict[str, Any] = {}
        log.info(f"[GM] {display_kind} · {model} (base={effective_base or 'default'}, key from {result.get('source')})")

    def _reasoning_param(self) -> dict:
        """task 141: 按用户偏好返 OpenAI o-series / gpt-5 系列的 reasoning.effort 字段。
        返 {} 表示不传(off 或 model 不支持)。

        DeepSeek / Qwen / Hunyuan / Mimo 等国内 provider 大多无 reasoning 字段,
        传了 SDK 报 400 — 这里**只对 api_id='openai' 的请求传**,其他 provider
        默认空字典(模型自己内置 thinking 行为,不通过 effort 参数控制)。
        """
        try:
            from ._effort import resolve_openai_reasoning
            # 仅 OpenAI 正式 endpoint 支持 reasoning.effort 字段
            if self.api_id not in {"openai"}:
                return {}
            effort = resolve_openai_reasoning(self.user_id, self.api_id, self.model_name)
            if not effort:
                return {}
            return {"reasoning_effort": effort}
        except Exception as exc:
            log.warning(f"[openai_compat] _reasoning_param failed: {exc}")
            return {}

    def _to_messages(self, system: str, messages: list[dict]) -> list[dict]:
        out = []
        if system:
            out.append({"role": "system", "content": system})
        out.extend(messages)
        return out

    def call(self, system: str, messages: list[dict], max_tokens: int) -> str:
        last_exc: Exception | None = None
        _reasoning = self._reasoning_param()  # task 141
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = self._create(
                    model=self.model_name,
                    messages=self._to_messages(system, messages),
                    max_tokens=max_tokens,
                    temperature=0.9,
                    **_reasoning,
                )
                break
            except Exception as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES and _is_retryable_openai(exc):
                    log.warning(f"[openai_compat] call attempt {attempt+1} failed ({exc}), retrying…")
                    time.sleep(1.0)
                    continue
                raise
        else:
            raise last_exc  # type: ignore[misc]
        choice = resp.choices[0]
        self._capture_usage(
            resp,
            finish_reason=getattr(choice, "finish_reason", None),
            max_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "").strip()

    def _capture_usage(
        self,
        resp,
        *,
        finish_reason: str | None = None,
        max_tokens: int | None = None,
    ) -> None:
        usage = getattr(resp, "usage", None)
        if not usage:
            return
        if finish_reason is None:
            finish_reason = self.last_usage.get("finish_reason")
        if max_tokens is None:
            max_tokens = self.last_usage.get("max_tokens")
        # OpenAI 格式：prompt_tokens / completion_tokens / total_tokens
        # 部分 provider 还会带 prompt_tokens_details.cached_tokens
        cached = 0
        details = getattr(usage, "prompt_tokens_details", None)
        if details:
            cached = int(getattr(details, "cached_tokens", 0) or 0)
        # DeepSeek 不走 OpenAI 标准的 prompt_tokens_details,而是顶层
        # usage.prompt_cache_hit_tokens / prompt_cache_miss_tokens。不读这个字段就会
        # 把 deepseek 的缓存命中全记成 0 → UI「缓存命中率」永远显示 0(测量 bug,非真失效)。
        if not cached:
            cached = int(getattr(usage, "prompt_cache_hit_tokens", 0) or 0)
        # 某些 provider 把它塞进 model_extra / 原始 dict
        if not cached:
            try:
                _extra = getattr(usage, "model_extra", None) or {}
                cached = int(_extra.get("prompt_cache_hit_tokens", 0) or 0)
            except Exception:
                pass
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
        if finish_reason:
            self.last_usage["finish_reason"] = str(finish_reason)
        if max_tokens:
            self.last_usage["max_tokens"] = int(max_tokens)

    def call_structured(self, system: str, messages: list[dict], max_tokens: int) -> str:
        sys_text = (system or "") + "\n\n你必须只返回合法 JSON，不能包含 Markdown 代码围栏或解释文字。"
        resp = self._create(
            model=self.model_name,
            messages=self._to_messages(sys_text, messages),
            max_tokens=max_tokens,
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        choice = resp.choices[0]
        self._capture_usage(
            resp,
            finish_reason=getattr(choice, "finish_reason", None),
            max_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "").strip()

    def stream(self, system: str, messages: list[dict], max_tokens: int) -> Iterator[str]:
        _reasoning = self._reasoning_param()  # task 141
        finish_reason: str | None = None
        stream = self._create(
            model=self.model_name,
            messages=self._to_messages(system, messages),
            max_tokens=max_tokens,
            temperature=0.9,
            stream=True,
            stream_options={"include_usage": True},  # 末尾 chunk 带 usage
            **_reasoning,
        )
        for chunk in stream:
            # 末尾 usage chunk 的 choices 可能为空
            try:
                if getattr(chunk, "usage", None):
                    self._capture_usage(chunk, finish_reason=finish_reason, max_tokens=max_tokens)
                if chunk.choices:
                    choice = chunk.choices[0]
                    fr = getattr(choice, "finish_reason", None)
                    if fr:
                        finish_reason = str(fr)
                        if self.last_usage:
                            self.last_usage["finish_reason"] = finish_reason
                            self.last_usage["max_tokens"] = int(max_tokens)
                    delta = choice.delta.content
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
        combo_key = (self.api_id, self.model_name, self.user_id)
        if combo_key in self._unsupported_combos:
            # 已知该 provider/model 不支持 tools → 立即降级到 text marker
            yield from _openai_text_marker_loop(self, system, messages, mcp_tools, max_iterations, max_tokens, mcp_call)
            return

        sep = "__"
        from core.config import tiered_tools_enabled as _tiered_enabled
        from core.config import tool_window_size as _tool_window
        # 窗口外工具进 load_tools 目录按需加载(见 _tiered.py)。默认 16(原硬编码 64 → 91 个
        # 工具里 64 个仍每轮全发 ≈ 6.7k token,阶梯化形同虚设;收到 16 后每轮工具 token 大降)。
        _WINDOW = _tool_window()

        def _mk_openai_tool(t):
            """unified tool → OpenAI function 定义;无效(缺 sid/name)返回 None。"""
            sid = str(t.get("server_id", ""))
            tname = str(t.get("name", ""))
            if not sid or not tname:
                return None
            safe_sid = re.sub(r"[^A-Za-z0-9_-]", "_", sid)
            safe_tname = re.sub(r"[^A-Za-z0-9_-]", "_", tname)
            full_name = f"{safe_sid}{sep}{safe_tname}"[:64]
            schema_raw = t.get("schema") or {"type": "object", "properties": {}}
            if not isinstance(schema_raw, dict):
                schema_raw = {"type": "object", "properties": {}}
            if schema_raw.get("type") != "object":
                schema_raw = {"type": "object", "properties": schema_raw.get("properties", {})}
            return {
                "type": "function",
                "function": {
                    "name": full_name,
                    "description": (t.get("description") or "")[:512],
                    "parameters": schema_raw,
                },
            }

        # 窗口内工具直接进 tools(行为同旧逻辑:前 _WINDOW 个,已按 _rank 排序,酒馆自管理排最前)。
        openai_tools = []
        for t in mcp_tools[:_WINDOW]:
            m = _mk_openai_tool(t)
            if m:
                openai_tools.append(m)

        # 阶梯化:窗口外工具不直接塞 schema,登记进「目录」由 load_tools 按需加载。append-only
        # (只往 openai_tools 末尾加、不重排/删)→ 不破坏 provider 的前缀缓存。
        # RPG_TIERED_TOOLS=0 → 退回旧行为(窗口外工具直接丢弃,即原 [:64] 硬截断)。
        _overflow_index: dict[str, dict[str, Any]] = {}  # openai function name → unified tool
        if _tiered_enabled():
            _cat_lines = []
            for t in mcp_tools[_WINDOW:]:
                m = _mk_openai_tool(t)
                if not m:
                    continue
                fn = m["function"]["name"]
                _overflow_index[fn] = t
                _d = (str(t.get("description") or "").splitlines() or [""])[0][:64]
                _cat_lines.append(f"- {fn}: {_d}")
            if _overflow_index:
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": "tiered" + sep + "load_tools",
                        "description": (
                            "本对话还有以下工具未加载。需要用到时,先用本工具按 name 加载(加载后下一步才能调用)。"
                            "可加载工具:\n" + "\n".join(_cat_lines)
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "names": {
                                    "type": "array", "items": {"type": "string"},
                                    "description": "要加载的工具完整 name(取自上面目录)",
                                },
                            },
                            "required": ["names"],
                        },
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
                _reasoning = self._reasoning_param()  # task 141
                stream = self._create(
                    model=self.model_name,
                    messages=oai_messages,
                    max_tokens=max_tokens,
                    temperature=0.9,
                    tools=openai_tools,
                    tool_choice="auto",
                    stream=True,
                    stream_options={"include_usage": True},
                    **_reasoning,
                )
                for chunk in stream:
                    try:
                        if getattr(chunk, "usage", None):
                            self._capture_usage(chunk, finish_reason=finish_reason, max_tokens=max_tokens)
                        if not chunk.choices:
                            continue
                        choice = chunk.choices[0]
                        delta = getattr(choice, "delta", None)
                        if delta:
                            # #7 reasoning 流式: 思考模型(deepseek-r1/qwen/中转站等)把思考过程放在
                            # reasoning_content / reasoning 增量里。纯增量 yield reasoning 事件,不混入
                            # text(叙事),最坏情况只是不显示、绝不污染正文。
                            rtext = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
                            if rtext:
                                yield {"type": "reasoning", "text": rtext}
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
                            finish_reason = str(fr)
                            if self.last_usage:
                                self.last_usage["finish_reason"] = finish_reason
                                self.last_usage["max_tokens"] = int(max_tokens)
                    except Exception:
                        continue
            except Exception as exc:
                # 仅「首次尝试 + 确属 400 不支持 tools」才标记降级。429/401/5xx/超时等瞬时/鉴权错误
                # 必须上抛(让 harness 正常重试/报错),否则会把该 api+model 永久误标降级。
                if first_attempt and _is_tools_unsupported(exc):
                    log.warning(f"[gm] {self.api_id}/{self.model_name} native tools rejected (400): {exc} → text marker fallback")
                    self._unsupported_combos.add(combo_key)
                    yield from _openai_text_marker_loop(self, system, messages, mcp_tools, max_iterations, max_tokens, mcp_call)
                    return
                # 非 tools-不支持(瞬时/鉴权/5xx)或后续 iteration 异常：let it bubble
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
                # 阶梯化:load_tools 不路由 dispatcher,直接把目录里的工具 schema append 进
                # openai_tools(只增不重排),返回 ack 让模型下一轮直接调用它们。
                if server_id == "tiered" and tool_name == "load_tools":
                    want = args.get("names") or []
                    if isinstance(want, str):
                        want = [want]
                    have = {ot["function"]["name"] for ot in openai_tools}
                    loaded, missing = [], []
                    for nm in want:
                        nm = str(nm)
                        t = _overflow_index.get(nm)
                        if not t:
                            missing.append(nm)
                            continue
                        if nm not in have:
                            m = _mk_openai_tool(t)
                            if m:
                                openai_tools.append(m)
                                have.add(nm)
                        loaded.append(nm)
                    ack = ("已加载: " + ", ".join(loaded) + "。下一步可直接调用它们。") if loaded else "没有匹配到可加载的工具。"
                    if missing:
                        ack += " 未找到: " + ", ".join(missing) + "。"
                    yield {"type": "tool_call", "server_id": "tiered", "tool": "load_tools", "arguments": args}
                    yield {"type": "tool_result", "ok": bool(loaded), "result": ack, "error": None}
                    oai_messages.append({
                        "role": "tool",
                        "tool_call_id": buf["id"] or f"call_{idx}",
                        "content": ack,
                    })
                    continue
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
