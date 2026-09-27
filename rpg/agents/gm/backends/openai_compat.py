"""agents.gm.backends.openai_compat — OpenAI 兼容 backend。"""
from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import httpx

from agents.gm.backends._dsml import DSML_UNPARSED_ERROR, DsmlStreamFilter, resolve_tool_ref
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


# create() 里「我们主动加的、provider 未必认」的顶层 kwargs。extra_body 里还装着
# top_k / repetition_penalty / thinking,一并由 _strip_sampling 处理。
_OPTIONAL_TUNING_KEYS = ("temperature", "top_p", "frequency_penalty", "presence_penalty",
                         "extra_body", "reasoning_effort")


def _is_bad_request(exc: Exception) -> bool:
    """是不是 400 BadRequest(与 _is_tools_unsupported 同一判据,只是语义不同)。"""
    try:
        from openai import BadRequestError
    except ImportError:
        return False
    return isinstance(exc, BadRequestError)


def _strip_sampling(kwargs: dict) -> None:
    """就地剥掉所有可选调参(含 extra_body 里的 top_k/repetition_penalty/thinking)→ 退回模型默认。"""
    for _sp in ("temperature", "top_p", "frequency_penalty", "presence_penalty", "extra_body",
                "reasoning_effort"):
        kwargs.pop(_sp, None)


# DeepSeek 直供的思考档位名单(官方 api-docs「Thinking Mode」:minimal/low/medium/high/xhigh/max)。
# 本仓 effort 枚举里的 "extra" 官方没有,映射到 xhigh(官方 xhigh→high,与 high 同档,不会报错)。
_EFFORT_TO_DEEPSEEK = {
    "low": "low", "medium": "medium", "high": "high", "extra": "xhigh", "max": "max",
}


def _merge_tuning(*parts: dict) -> dict:
    """把多份请求调参字典并成一份,**extra_body 做深合并而不是互相覆盖**。

    采样参数(top_k/repetition_penalty)和 DeepSeek 的 thinking 开关都住在 extra_body 里,
    两边各自 `**` 展开进 create() 会撞出 `multiple values for keyword 'extra_body'` TypeError。
    """
    out: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    for part in parts:
        if not part:
            continue
        for k, v in part.items():
            if k == "extra_body":
                extra.update(v or {})
            else:
                out[k] = v
    if extra:
        out["extra_body"] = extra
    return out


def _is_tools_unsupported(exc: Exception) -> bool:
    """仅「400 BadRequest」才**可能**是 provider 不支持 tools 参数 → 本轮试 text marker。
    429 限流 / 401 鉴权 / 5xx / 超时 等是瞬时/配置错误,连试都不试。
    400 也只是候选:要不要把 (api,model) 记成不支持,由不带 tools 的重试是否被接受来定
    (见 stream_with_mcp_loop;类级 set 进程内共享,记错了该 worker 此后所有对话都静默降级)。"""
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
    _unsupported_combos: set[tuple[str, str]] = set()

    # 类级状态：记录拒绝自定义 temperature(只接受默认/=1)的 (api_id, model) 组合，
    # 同一进程内之后直接不发 temperature。见 _create / _is_temperature_rejected。
    # 已知限制：进程内缓存，多 worker 模式下各自独立学习（不跨进程共享），可接受。
    _fixed_temp_combos: set[tuple[str, str]] = set()

    def _create(self, **kwargs):
        """self.client.chat.completions.create 的包装,带 temperature 自愈。

        moonshot kimi 部分模型 / openai o-series 等「只允许 temperature=1」,平台默认发
        0.9/0.1 会被 400 拒(原表现:整轮失败,用户只见随机错误码)。这里首次被拒后去掉
        temperature 用模型默认重试**并记忆**,本进程同 (api_id, model) 后续直接不发 →
        当轮即成功,不再失败。stream=True 时请求在 create() 即发出,400 也在此抛,可拦。
        """
        combo = (self.api_id, self.model_name, self.user_id)
        if combo in self._fixed_temp_combos:
            _strip_sampling(kwargs)  # 本进程已知该 combo 拒可选调参 → 直接剥掉不再发
        try:
            return self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            # provider 拒绝任一可选调参(采样参数 / reasoning_effort / extra_body 里的 top_k / thinking)
            # → 剥掉全部可选调参用模型默认重试。保证「预设接线」不会让任何 provider 400 断轮。
            #
            # 判据用「是不是 400 + 我们确实发了可选调参」,不再依赖报错文本点名字段:中转站的
            # 400 文案五花八门,点名式嗅探漏一个就是整轮崩。反正 400 本来就要失败,退参重试一次
            # 只可能变好。**记忆放在重试成功之后** —— 若退参也没救回来,说明 400 另有原因,
            # 不能就此把该 combo 永久标成「拒采样参数」、白白吃掉用户的生成参数预设。
            if not (_is_bad_request(exc) and any(
                    k in kwargs for k in _OPTIONAL_TUNING_KEYS)):
                raise
            _strip_sampling(kwargs)
            log.info(f"[GM] {self.api_id}/{self.model_name} 拒绝可选调参(400)→ 用模型默认重试")
            resp = self.client.chat.completions.create(**kwargs)
            self._fixed_temp_combos.add(combo)
            return resp

    def _sampling_kwargs(self, default_temperature: float) -> dict[str, Any]:
        """叙事调用的采样参数 = 用户预设(只覆盖设过的键)叠加在后端默认 temperature 上。
        JSON/结构化调用不走此方法(保持低温 0.1)。反馈#93:让生成参数预设真正生效。"""
        try:
            from ._gen_params import resolve_gen_params
            gen = resolve_gen_params(self.user_id)
        except Exception:
            gen = {}
        out: dict[str, Any] = {"temperature": gen.get("temperature", default_temperature)}
        for k in ("top_p", "frequency_penalty", "presence_penalty"):
            if k in gen:
                out[k] = gen[k]
        extra: dict[str, Any] = {}
        for k in ("top_k", "repetition_penalty"):
            if k in gen:
                extra[k] = gen[k]
        if extra:
            out["extra_body"] = extra
        return out

    def __init__(self, model: str, base_url: str, env_key: str, display_kind: str = "openai_compat",
                 user_id: int | None = None, api_id: str | None = None):
        from openai import OpenAI

        from platform_app.user_credentials import (
            resolve_api_key,
            resolved_auth_token,
            resolved_is_usable,
        )
        # task: LLM 严格 BYOK — 生产模式拒绝平台 env fallback,防用户白嫖你的 OPENAI_API_KEY / DEEPSEEK_API_KEY 等
        try:
            from core.config import require_auth as _require_auth
            byok_only = bool(_require_auth())
        except Exception:
            byok_only = True
        env_fb = "" if (byok_only and user_id) else env_key
        result = resolve_api_key(user_id, api_id or display_kind, env_fallback=env_fb)
        # source='user_db_no_auth' = 用户在设置里显式把该 provider 标成「免鉴权(本地/自托管)」
        # 且没填 key。这是合法可用状态,不是"没配"。判据是 source 而不是「key 为空」——
        # 后者会把任何一个 key 被清空的托管 provider 悄悄放行,撞 401 才发现;
        # 显式声明才放行,BYOK 墙对其余 provider 原样不动。
        key = resolved_auth_token(result)
        if not resolved_is_usable(result):
            raise ValueError(
                f"{api_id or display_kind} 的 API Key 未配置。请在「设置 → API 设置」添加你自己的 API Key。"
                "(测试服 LLM 调用必须 BYOK,平台不提供共享 key;本地/自托管模型可勾选「免 API Key」)"
            )
        # 用户覆盖了 base_url 的话优先用用户的
        effective_base = result.get("base_url_override") or base_url
        import os as _os
        # 读超时:单一来源 config.llm_timeout_seconds —— 用户 settings.request_timeout(UI 可调)
        # > env RPG_GM_TIMEOUT > 部署默认(本地/桌面 1800s 给慢的本地大模型留足首 token 时间;服务器 300s)。
        try:
            from core.config import llm_timeout_seconds as _llm_to
            _read_to = _llm_to(user_id)
        except Exception:
            _read_to = float(_os.environ.get("RPG_GM_TIMEOUT", "300"))
        # 出站代理:用户在凭据里配的 proxy URL。**仅本地模式(非 require_auth)才真正使用** ——
        # 托管多用户后端永不使用用户 proxy(防 SSRF:代理合法地可指向 127.0.0.1,无法用「禁私网」
        # 校验拦截;故把使用面收窄到自托管单用户场景)。本地梯子用户选「HTTP 代理」即生效。
        # 门控收口在 core.outbound.credential_proxy(单一真源,拉模型/子代理/生图/向量同一口径)。
        from core.outbound import credential_proxy, safe_httpx_client
        _use_proxy = credential_proxy(result)
        if _use_proxy:
            log.info(f"[GM] {display_kind} 出站走用户代理 {_use_proxy}")
        # 覆盖 openai SDK 默认 UA(`OpenAI/Python x.y.z`)→ 浏览器 UA。否则挂在 Cloudflare 后的
        # 中转站会按 UA 用 WAF 把它当 AI 爬虫拦掉(403「Your request was blocked」/ error 1010),
        # 导致这类中转站聊天/校验/拉取模型全部「不可访问」。详见 core.outbound_ua(已实测)。
        from core.outbound_ua import openai_default_headers
        kwargs: dict[str, Any] = {
            # key 已由 resolved_auth_token 归一:有 key 用 key,免鉴权用占位 token
            # (绝不能是空串 —— openai SDK 对空串同样抛 Missing credentials,构造即崩)。
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
        """task 141: 按用户偏好返回思考控制字段。返 {} 表示不传(off / provider 不支持)。

        两家的方言不同,各自适配:
        - `openai`:reasoning_effort 字符串(o-series / gpt-5)。
        - `deepseek`:V4.1 起**思考默认开、默认 effort=high**。OpenAI 格式下用
          `reasoning_effort` 调档、`extra_body.thinking.type` 开关(官方 api-docs「Thinking Mode」)。
          此前本函数对非 openai 一律返 {},于是游戏内/设置页的 Effort 选择器对 deepseek
          写了偏好、弹了成功提示,后端却整段丢弃 —— 用户想关思考关不掉(UI 存在≠生效)。

        其余 provider(Qwen / Hunyuan / 中转 / 本地)仍返 {}:模型自己内置 thinking 行为,
        没有权威的开关字段,乱传只会 400。
        """
        try:
            if self.api_id == "openai":
                from ._effort import resolve_openai_reasoning
                effort = resolve_openai_reasoning(self.user_id, self.api_id, self.model_name)
                return {"reasoning_effort": effort} if effort else {}
            if self.api_id == "deepseek":
                from ._effort import resolve_effort
                effort = resolve_effort(self.user_id, self.api_id, self.model_name)
                if effort == "off":
                    # 唯一需要显式关的场合。开着是官方默认,不必多传一个字段去冒 400 的险。
                    return {"extra_body": {"thinking": {"type": "disabled"}}}
                mapped = _EFFORT_TO_DEEPSEEK.get(effort)
                return {"reasoning_effort": mapped} if mapped else {}
            return {}
        except Exception as exc:
            log.warning(f"[openai_compat] _reasoning_param failed: {exc}")
            return {}

    def _tuning_kwargs(self, default_temperature: float) -> dict[str, Any]:
        """叙事调用的全部可选调参 = 采样参数 + 思考控制,extra_body 已深合并。
        三个调用点(call / stream / stream_with_mcp_loop)统一走这里,免得 extra_body 撞车。"""
        return _merge_tuning(self._sampling_kwargs(default_temperature), self._reasoning_param())

    def _to_messages(self, system: str, messages: list[dict]) -> list[dict]:
        out = []
        if system:
            out.append({"role": "system", "content": system})
        out.extend(messages)
        return out

    def call(self, system: str, messages: list[dict], max_tokens: int) -> str:
        last_exc: Exception | None = None
        _tuning = self._tuning_kwargs(0.9)  # task 141: 采样 + 思考控制(extra_body 已深合并)
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = self._create(
                    model=self.model_name,
                    messages=self._to_messages(system, messages),
                    max_tokens=max_tokens,
                    **_tuning,
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
        _tuning = self._tuning_kwargs(0.9)  # task 141: 采样 + 思考控制(extra_body 已深合并)
        finish_reason: str | None = None
        stream = self._create(
            model=self.model_name,
            messages=self._to_messages(system, messages),
            max_tokens=max_tokens,
            stream=True,
            stream_options={"include_usage": True},  # 末尾 chunk 带 usage
            **_tuning,
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
                        # 无条件写:GameMaster 每回合入口会把 last_usage 清成 {},有些中转站只发
                        # finish_reason 不发 usage chunk —— 这里再以「非空才写」为条件,截断/风控信号就丢了。
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
        from agents.gm.backends import _tiered
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
            full_name = _tiered.tool_full_name(t)  # 名字编码收敛到 _tiered(三 backend 单一真源)
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

        # 阶梯化切窗口 / 建目录收敛到 _tiered(与 anthropic/vertex 单一真源,消双维护)。窗口内完整
        # schema 直发;窗口外登记进目录由 load_tools 按需加载(append-only 不破前缀缓存);
        # RPG_TIERED_TOOLS=0 → 窗口外直接丢弃(旧 [:64] 硬截断)。unified 工具产物本地各自 _mk_openai_tool。
        window_tools, overflow_index, catalog_lines = _tiered.split_window(
            mcp_tools, _WINDOW, _tiered_enabled())
        loaded_overflow: set[str] = set()
        openai_tools = []
        for t in window_tools:
            m = _mk_openai_tool(t)
            if m:
                openai_tools.append(m)
        if catalog_lines:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": _tiered.LOAD_TOOLS_FULL_NAME,
                    "description": _tiered.load_tools_description(catalog_lines),
                    "parameters": _tiered.LOAD_TOOLS_PARAMS,
                },
            })

        if not openai_tools:
            for chunk in self.stream(system, messages, max_tokens=max_tokens):
                yield {"type": "text", "text": chunk}
            return

        oai_messages = self._to_messages(system, messages)

        first_attempt = True
        # DeepSeek 思考模式硬要求(官方 api-docs「Thinking Mode · Tool Calls」):**带 tools 的请求,
        # 后续每一次请求都必须把该轮的 reasoning_content 原样回传,否则 API 直接 400**。
        # V4.1 起思考是默认开的,不回传就等于「模型调完工具、下一跳必崩」。这里逐轮累计并装回
        # assistant 消息。对不产 reasoning_content 的 provider 天然是 no-op(累计恒空,字段不加)。
        echo_reasoning = True  # 被中转站以 400 拒绝时降级为 False 重试一次
        for _iteration in range(max_iterations):
            tool_calls_buf: dict[int, dict[str, Any]] = {}  # index → {id, name, arguments}
            current_text = ""
            current_reasoning = ""
            finish_reason: str | None = None
            # 中转站没接 DeepSeek 的 DSML 解析器时,工具调用会以 <｜DSML｜…> 文本落进 content。
            # 这里扣下不外发,解析成调用补进 tool_calls_buf(反馈 #106)。
            dsml = DsmlStreamFilter()
            try:
                _tuning = self._tuning_kwargs(0.9)  # task 141: 采样 + 思考控制(extra_body 已深合并)
                stream = self._create(
                    model=self.model_name,
                    messages=oai_messages,
                    max_tokens=max_tokens,
                    tools=openai_tools,
                    tool_choice="auto",
                    stream=True,
                    stream_options={"include_usage": True},
                    **_tuning,
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
                                current_reasoning += rtext
                                yield {"type": "reasoning", "text": rtext}
                            ctext = dsml.feed(getattr(delta, "content", None) or "")
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
                            # 无条件写,理由同 stream():每回合入口清零后,不发 usage chunk 的渠道靠它留住截断信号。
                            self.last_usage["finish_reason"] = finish_reason
                            self.last_usage["max_tokens"] = int(max_tokens)
                    except Exception:
                        continue
            except Exception as exc:
                # 仅「首次尝试 + 确属 400 不支持 tools」才标记降级。429/401/5xx/超时等瞬时/鉴权错误
                # 必须上抛(让 harness 正常重试/报错),否则会把该 api+model 永久误标降级。
                if first_attempt and _is_tools_unsupported(exc):
                    # 400 不等于「不支持 tools」:内容风控、上下文超长、消息格式不对也都是 400。
                    # 以前见 400 就把 (api, model, user) 永久记成不支持,一次风控拒绝就让这个用户
                    # 此后每一轮都走 text-marker 降级路径(反馈 #106 那位用户多半就是这么进去的)。
                    # 改成看结果:本轮先不带 tools 走 text-marker,这个请求被接受 = 问题确实出在
                    # tools,这时才记;同样被拒 = 跟 tools 无关,异常照常上抛,什么也不记。
                    # 与 _create 的退参自愈同一个原则:记忆放在重试成功之后。
                    log.warning(f"[gm] {self.api_id}/{self.model_name} 带 tools 的首跳被拒(400): {exc}"
                                f" → 本轮改走 text marker,不带 tools 能通才记为不支持")
                    marked = False
                    for ev in _openai_text_marker_loop(self, system, messages, mcp_tools, max_iterations, max_tokens, mcp_call):
                        if not marked:
                            self._unsupported_combos.add(combo_key)
                            marked = True
                            log.warning(f"[gm] {self.api_id}/{self.model_name} 不带 tools 的请求被接受"
                                        f" → 本进程内记为不支持 native tools")
                        yield ev
                    return
                # 少数中转站会把 assistant.reasoning_content 当非法字段 400 拒(自己吐得出、却收不回)。
                # 剥掉重发一次:失去的只是思考连续性,总比整轮崩掉强。stream=True 的 400 在 create()
                # 即抛、尚未 yield 任何增量,重发不会重复输出。
                if echo_reasoning and _is_tools_unsupported(exc) and any(
                        m.get("reasoning_content") for m in oai_messages if isinstance(m, dict)):
                    log.warning(f"[gm] {self.api_id}/{self.model_name} 拒绝回传 reasoning_content(400)→ 剥离重试")
                    echo_reasoning = False
                    for m in oai_messages:
                        if isinstance(m, dict):
                            m.pop("reasoning_content", None)
                    continue
                # 非 tools-不支持(瞬时/鉴权/5xx)或后续 iteration 异常：let it bubble
                raise
            first_attempt = False

            _tail = dsml.finish()
            if _tail:
                current_text += _tail
                yield {"type": "text", "text": _tail}
            if dsml.seen:
                log.warning(f"[gm] {self.api_id}/{self.model_name} 在正文里吐了 DSML 工具标记,"
                            f"已拦下并解析出 {len(dsml.calls)} 个调用")
                if not dsml.calls:
                    # 标记整块被扣下、却一个完整调用都没解析出来(截断在 invoke 里 / 结构走样):正文被吞、
                    # 工具也没执行。此前唯一痕迹是上面那行日志,空回合分诊只能误判成「上游返空」。
                    # 发一个 tool_error 事件(chat 层现成转发并累积),给分诊一个确定性信号。
                    yield {"type": "tool_error", "error": DSML_UNPARSED_ERROR, "raw": ""}
            _next = max(tool_calls_buf, default=-1) + 1
            for _j, (_name, _args) in enumerate(dsml.calls):
                _sid, _tool = resolve_tool_ref(_name, mcp_tools)
                tool_calls_buf[_next + _j] = {
                    "id": f"call_dsml_{_iteration}_{_j}",
                    "name": f"{_sid}{sep}{_tool}" if _sid else _tool,
                    "arguments": json.dumps(_args, ensure_ascii=False),
                }

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
            if current_reasoning and echo_reasoning:
                assistant_msg["reasoning_content"] = current_reasoning
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
                # 阶梯化:load_tools 不路由 dispatcher,把目录里的工具 schema append 进 openai_tools
                # (只增不重排,append-only 不破前缀缓存),返回 ack 让模型下一轮直接调用它们。解析逻辑
                # 收敛到 _tiered.resolve_load(与 anthropic/vertex 单一真源)。ok=bool(newly):有新工具被
                # 加载即真(空 / 全未找到 → 假);仅「重复请求已加载工具」这一极罕见 edge 下与旧 bool(loaded)
                # 的 True 有别,ack 明细一致、无 UX 影响。
                if _tiered.is_load_tools(server_id, tool_name):
                    newly, ack = _tiered.resolve_load(args, overflow_index, loaded_overflow)
                    for t in newly:
                        m = _mk_openai_tool(t)
                        if m:
                            openai_tools.append(m)
                    yield {"type": "tool_call", "server_id": "tiered", "tool": "load_tools", "arguments": args}
                    yield {"type": "tool_result", "ok": bool(newly), "result": ack, "error": None}
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
                if not tool_name:
                    # 个别中转站流式回传 tool_calls 时丢了 function.name。别拿空名去路由(只会得到
                    # 一句「未知工具」,用户以为缺了什么要装),直接告诉模型缺的是名字。
                    result = {"ok": False, "error": "这次工具调用没有工具名,未执行。请带上工具名重新调用。"}
                else:
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
