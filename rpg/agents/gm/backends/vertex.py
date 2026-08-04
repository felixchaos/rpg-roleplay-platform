"""agents.gm.backends.vertex — Vertex AI (Gemini) backend."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from core.logging import get_logger

log = get_logger(__name__)

# P1-1: 最多重试 1 次,仅对 timeout / 5xx 错误
_MAX_RETRIES = 1
# 读超时原 120s 太紧,带 reasoning 的 Gemini 长回合常被切断。提到 300s,可用 RPG_GM_TIMEOUT 调。
try:
    _VERTEX_TIMEOUT_SECONDS = int(float(os.environ.get("RPG_GM_TIMEOUT", "300")))
except (TypeError, ValueError):
    _VERTEX_TIMEOUT_SECONDS = 300


def _is_retryable_vertex(exc: Exception) -> bool:
    name = type(exc).__name__
    # google.api_core.exceptions.DeadlineExceeded / ServiceUnavailable / InternalServerError
    if any(k in name for k in ("Deadline", "Unavailable", "InternalServer", "Timeout")):
        return True
    return False

BASE = Path(__file__).parent.parent.parent.parent  # rpg/agents/gm/backends/ → rpg/


# task 141: thinking budget 由 _effort 模块统一管理 (跨 backend 共用)。
from ._effort import resolve_budget_tokens as _resolve_budget  # noqa: E402


def _resolve_thinking_budget(user_id: int | None, model_id: str | None) -> int:
    """Vertex (Gemini 2.5/3.x) thinking_budget — 0 禁用,>0 启用。"""
    return _resolve_budget(user_id, "vertex_ai", model_id or "")


def _finish_reason_normalized(resp) -> str | None:
    """从响应 / 流式 chunk 的 candidates[0].finish_reason 取截断信号。

    MAX_TOKENS 归一为 "length"(与 openai finish_reason / anthropic stop_reason 对齐;
    app.py 的截断告警只认 == "length");其余(STOP / SAFETY / RECITATION …)透传其名。
    取不到 / 无候选返回 None。流式末 chunk 通常同时带 usage_metadata 与 finish_reason。"""
    try:
        cands = getattr(resp, "candidates", None) or []
        if not cands:
            return None
        fr = getattr(cands[0], "finish_reason", None)
        if not fr:
            return None
        name = getattr(fr, "name", None) or str(fr)
        name = str(name).rsplit(".", 1)[-1].upper()  # enum → 裸名(去 "FinishReason." 前缀)
        return "length" if name == "MAX_TOKENS" else name
    except Exception:
        return None


def _iter_part_texts(chunk):
    """遍历流式 chunk 的 candidates[0].content.parts,产出 (is_thought, text)。

    thought=True 是思考流(reasoning),必须与正文(narrative)分离:纯文本流丢弃,
    事件流单独走 reasoning 事件。与 stream_with_mcp_loop 的 parts 读取同源。"""
    cands = getattr(chunk, "candidates", None) or []
    if not cands:
        return
    content = getattr(cands[0], "content", None)
    if not content:
        return
    for part in (getattr(content, "parts", None) or []):
        ptext = getattr(part, "text", None)
        if not ptext:
            continue
        yield bool(getattr(part, "thought", False)), ptext


# ── 显式上下文缓存(Vertex cachedContent)────────────────────────────────────
# 实测:Gemini 隐式缓存对本平台 0 命中(cached_content_token_count 恒 0)。显式缓存把
# system(+tools)这段**稳定大前缀**建成 CachedContent,后续调用以 cached_content 引用 →
# 前缀按缓存读取价计费(约 -75%),且**单轮内多次工具迭代 + 同会话多轮**都复用同一缓存。
# 约束(已实测):用 cached_content 时 request **不能**再带 system_instruction / tools /
# tool_config(必须全在 cache 内,否则 400 INVALID_ARGUMENT)。
# 默认开;RPG_VERTEX_EXPLICIT_CACHE=0 关闭。TTL 由 RPG_VERTEX_CACHE_TTL(秒,默认 900)。
def _explicit_cache_enabled() -> bool:
    return os.getenv("RPG_VERTEX_EXPLICIT_CACHE", "1") != "0"


def _cache_ttl_seconds() -> int:
    try:
        return max(60, int(float(os.getenv("RPG_VERTEX_CACHE_TTL", "900"))))
    except (TypeError, ValueError):
        return 900


# Vertex 2.5 显式缓存最小约 1024 token;低于阈值 create 会 400,故粗按字符门控(≈800 token)。
_CACHE_MIN_CHARS = 2400
_PREFIX_CACHE: dict[str, tuple[str | None, float]] = {}
_PREFIX_CACHE_LOCK = threading.Lock()
_PREFIX_CACHE_MAX = 256


def _tools_signature(tools_param) -> str:
    if not tools_param:
        return ""
    try:
        return json.dumps(
            [t.model_dump(exclude_none=True) for t in tools_param],
            ensure_ascii=False, sort_keys=True, default=str,
        )
    except Exception:
        try:
            return str(tools_param)
        except Exception:
            return "tools"


class _VertexBackend:
    def __init__(self, model: str = "gemini-3.5-flash", user_id: int | None = None):
        """初始化 Vertex AI backend。

        凭证优先链:
          1. 生产鉴权模式 user_id 非 None → 用户 BYOK SA (user_api_credentials api_id='AgentPlatform')
          2. 本地/匿名开发模式 → GOOGLE_APPLICATION_CREDENTIALS 或 rpg/vertex_sa.json
          3. 无可用凭证 → RuntimeError

        Args:
            model: Vertex 模型名称（real_name）。
            user_id: 当前用户 ID，用于取 BYOK SA；None 仅在本地/匿名开发模式可走全局 SA。
        """
        from google import genai

        from core.vertex_sa import load_sa_credentials

        self.user_id = user_id
        self.model_name = model
        self.last_usage: dict[str, int] = {}
        self._unavailable_message = ""
        credentials, project_id = load_sa_credentials(user_id)

        if credentials is None or project_id is None:
            self.client = None
            self._genai = genai
            self._unavailable_message = (
                "未找到 Vertex AI Service Account。"
                "请在「设置 → API & 模型 → Agent Platform」上传自己的 SA JSON 文件。"
            )
            log.info(f"[GM] Vertex AI unavailable for user={user_id}: missing service account (expected if user has no SA)")
            return

        self.client = genai.Client(
            vertexai=True,
            project=project_id,
            location="global",
            credentials=credentials,
        )
        self._genai = genai
        sa_src = f"user={user_id}" if user_id else "global"
        log.info(f"[GM] Vertex AI (google-genai) · {model} @ global (SA: {sa_src})")

    def _ensure_available(self) -> None:
        if self.client is None:
            raise RuntimeError(self._unavailable_message)

    def _prefix_cache_name(self, system: str, tools_param=None) -> str | None:
        """把 system(+tools)前缀建成 / 复用 Vertex CachedContent,返回 cache name 或 None。
        任意异常 → None(优雅回退到非缓存路径,绝不打断对话)。"""
        if not _explicit_cache_enabled() or self.client is None:
            return None
        try:
            tools_sig = _tools_signature(tools_param)
            if len(system or "") + len(tools_sig) < _CACHE_MIN_CHARS:
                return None  # 前缀太短,低于 Vertex 最小可缓存阈值,建了也会 400
            key = hashlib.sha256(
                (self.model_name + "\x00" + (system or "") + "\x00" + tools_sig).encode("utf-8")
            ).hexdigest()
            now = time.monotonic()
            with _PREFIX_CACHE_LOCK:
                ent = _PREFIX_CACHE.get(key)
                if ent and ent[1] > now:
                    return ent[0]
            # 建缓存(网络调用放锁外)
            from google.genai import types
            ttl = _cache_ttl_seconds()
            cfg_kwargs: dict[str, Any] = {"system_instruction": system, "ttl": f"{ttl}s"}
            if tools_param:
                cfg_kwargs["tools"] = tools_param
            name: str | None = None
            try:
                cache = self.client.caches.create(
                    model=self.model_name,
                    config=types.CreateCachedContentConfig(**cfg_kwargs),
                )
                name = getattr(cache, "name", None)
            except Exception as exc:  # noqa: BLE001
                log.debug("[vertex] explicit cache create failed (%s); fallback no-cache", exc)
            with _PREFIX_CACHE_LOCK:
                # 成功:缓存到 TTL 前留 30s 余量;失败:短暂(60s)缓存 None 防重试风暴
                _PREFIX_CACHE[key] = (name, now + (ttl - 30 if name else 60))
                if len(_PREFIX_CACHE) > _PREFIX_CACHE_MAX:
                    for k in sorted(_PREFIX_CACHE, key=lambda k: _PREFIX_CACHE[k][1])[: _PREFIX_CACHE_MAX // 2]:
                        _PREFIX_CACHE.pop(k, None)
            return name
        except Exception as exc:  # noqa: BLE001
            log.debug("[vertex] _prefix_cache_name error (%s)", exc)
            return None

    def call(self, system: str, messages: list[dict], max_tokens: int) -> str:
        self._ensure_available()
        from google.genai import types

        contents = self._to_contents(messages, types)

        _cache_name = self._prefix_cache_name(system)
        _cfg: dict[str, Any] = {
            "max_output_tokens": max(max_tokens, 2048),  # thinking 模型需要足够 budget
            "temperature": 0.9,
            "thinking_config": types.ThinkingConfig(  # task 141: 按用户偏好,默认 high=8192
                thinking_budget=_resolve_thinking_budget(self.user_id, self.model_name),
            ),
            "http_options": types.HttpOptions(timeout=_VERTEX_TIMEOUT_SECONDS * 1000),
        }
        # 反馈#93:接入用户生成参数预设(只覆盖设过的键;未设沿用默认 0.9,零行为变化)。
        try:
            from ._gen_params import resolve_gen_params
            _gen = resolve_gen_params(self.user_id)
        except Exception:
            _gen = {}
        for _gk in ("temperature", "top_p", "top_k"):
            if _gk in _gen:
                _cfg[_gk] = _gen[_gk]
        # 显式缓存:命中则以 cached_content 引用前缀(system 在 cache 内,request 不再带 system_instruction)
        if _cache_name:
            _cfg["cached_content"] = _cache_name
        else:
            _cfg["system_instruction"] = system
        config = types.GenerateContentConfig(**_cfg)
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = self.client.models.generate_content(
                    model=self.model_name,
                    contents=contents,
                    config=config,
                )
                break
            except Exception as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES and _is_retryable_vertex(exc):
                    log.warning(f"[vertex] call attempt {attempt+1} failed ({exc}), retrying…")
                    time.sleep(1.0)
                    continue
                # task: 403 → 人类可读错误,让前端能引导用户去 GCP Console 修
                msg = str(exc)
                if "403" in msg or "PERMISSION_DENIED" in msg or "forbidden" in msg.lower():
                    _friendly = RuntimeError(
                        "Vertex AI 调用被拒(403)。请在 Google Cloud Console 检查你的 Service Account:\n"
                        "  1. 该 SA 在此 project 下有「Vertex AI User」角色 (roles/aiplatform.user)\n"
                        "  2. 该 project 已启用 Vertex AI API:\n"
                        "     https://console.cloud.google.com/apis/library/aiplatform.googleapis.com\n"
                        "  3. project 已开 billing(免费试用 / 付费账号都需要绑定 billing)"
                    )
                    # 附 status_code=403 让 classify_provider_error(_http_status)归类为 auth。
                    # 裸 RuntimeError 无状态码,友好中文文案又不含英文 "forbidden"/"http error 403" 标记
                    # → 分类落空,403 被误当未知错误走「请重试」泛化兜底(BYOK 用户按提示连撞)。
                    _friendly.status_code = 403  # type: ignore[attr-defined]
                    raise _friendly from exc
                raise
        else:
            raise last_exc  # type: ignore[misc]
        self._capture_usage(resp)
        return (resp.text or "").strip()

    def _capture_usage(self, resp) -> None:
        meta = getattr(resp, "usage_metadata", None)
        if not meta:
            return
        prompt = int(getattr(meta, "prompt_token_count", 0) or 0)
        candidates = int(getattr(meta, "candidates_token_count", 0) or 0)
        cached = int(getattr(meta, "cached_content_token_count", 0) or 0)
        thoughts = int(getattr(meta, "thoughts_token_count", 0) or 0)
        total = int(getattr(meta, "total_token_count", 0) or (prompt + candidates))
        self.last_usage = {
            "input_tokens": prompt,
            "output_tokens": candidates,
            "cached_input_tokens": cached,
            "reasoning_tokens": thoughts,
            "total_tokens": total,
        }
        # 截断信号:openai 侧一直采 finish_reason,vertex 之前完全不采 → 上游 GM 输出被
        # max_output_tokens 截断时 app.py 的截断告警对 Gemini 恒静默。归一为 length 补齐。
        fr = _finish_reason_normalized(resp)
        if fr:
            self.last_usage["finish_reason"] = fr

    def call_structured(self, system: str, messages: list[dict], max_tokens: int,
                        thinking_budget: int | None = None) -> str:
        self._ensure_available()
        from google.genai import types

        contents = self._to_contents(messages, types)
        # thinking_budget=None → 沿用用户 effort 偏好(现行为,task 141);显式 0 → 结构化微任务禁
        # 深思。对齐 _harness.call_agent_json 的 no_think 强约束:思考模型对判定类 prompt 会无界思考
        # 吃光预算(268 实锤族),vertex 无 tool_schema 走本函数,此前 no_think 在 vertex 结构化路径失效。
        _budget = (
            thinking_budget if thinking_budget is not None
            else _resolve_thinking_budget(self.user_id, self.model_name)
        )
        config_kwargs = {
            "system_instruction": system,
            "max_output_tokens": max_tokens,
            "temperature": 0.1,
            "thinking_config": types.ThinkingConfig(
                thinking_budget=_budget,
            ),
        }
        try:
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                **config_kwargs,
            )
        except TypeError:
            config = types.GenerateContentConfig(**config_kwargs)
        resp = self.client.models.generate_content(
            model=self.model_name,
            contents=contents,
            config=config,
        )
        self._capture_usage(resp)
        return (resp.text or "").strip()

    def stream(self, system: str, messages: list[dict], max_tokens: int) -> Iterator[str]:
        self._ensure_available()
        from google.genai import types

        contents = self._to_contents(messages, types)
        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max(max_tokens, 2048),
            temperature=0.9,
            # 按用户 effort(ModelPopover 思考深度)。原硬编码 0 是死设置:call() 已按用户
            # budget 生效,唯独流式(实际游玩热路径)恒 0 → UI「思考深度」对 Gemini 流式无效。
            thinking_config=types.ThinkingConfig(
                thinking_budget=_resolve_thinking_budget(self.user_id, self.model_name),
            ),
        )
        for chunk in self.client.models.generate_content_stream(
            model=self.model_name,
            contents=contents,
            config=config,
        ):
            if getattr(chunk, "usage_metadata", None):
                self._capture_usage(chunk)
            # 纯文本流(Iterator[str])无法承载 reasoning 事件,故思考部分(part.thought=True)在此
            # 丢弃(与 openai_compat 纯 stream() 丢 reasoning_content 一致),绝不当正文 yield 污染叙事。
            # 需要展示思考流的是 stream_with_mcp_loop(下方以 {"type":"reasoning"} 事件单独 yield)。
            for _is_thought, _text in _iter_part_texts(chunk):
                if _is_thought:
                    continue
                yield _text

    # task 70：Vertex 支持 native function_declarations
    supports_native_tools = True

    def stream_with_mcp_loop(
        self,
        system: str,
        messages: list[dict],
        mcp_tools: list[dict[str, Any]],
        max_iterations: int,
        max_tokens: int,
        mcp_call,
    ) -> Iterator[dict[str, Any]]:
        self._ensure_available()
        """Vertex (Gemini) native function calling MCP 循环。

        Gemini 的工具调用模型：
        - tools=[Tool(function_declarations=[FunctionDeclaration(...)])]
        - 流式时 chunk.candidates[0].content.parts[] 里可能有 text 或 function_call
        - 工具结果通过 types.Part.from_function_response(name=..., response=...)
          作为 user role 的 part 注回
        """
        from google.genai import types

        def _sanitize_schema(node: Any) -> Any:
            """Gemini schema 严校验:
            - type=array 必须带 items(否则整个 request 400 INVALID_ARGUMENT)
            - 不允许的额外字段(如 additionalProperties)需保留以兼容,Gemini 会忽略
            递归补 items={"type":"string"} 作安全默认。
            """
            if isinstance(node, dict):
                out = {k: _sanitize_schema(v) for k, v in node.items()}
                if out.get("type") == "array" and "items" not in out:
                    out["items"] = {"type": "string"}
                if "properties" in out and isinstance(out["properties"], dict):
                    out["properties"] = {k: _sanitize_schema(v) for k, v in out["properties"].items()}
                return out
            if isinstance(node, list):
                return [_sanitize_schema(x) for x in node]
            return node

        sep = "__"  # server_id 与 tool_name 分隔符
        # 截断上限:Gemini 2.5/3.x 实测支持 ≥64 个 FunctionDeclaration,40 太保守把
        # KB 查询工具(lookup_/search_canon)砍出去了。提到 64 + chat_tool_router 已
        # 按优先级排序,KB 查询永远在前面,即使再截也不丢。
        from agents.gm.backends import _tiered
        from core.config import tiered_tools_enabled as _tiered_enabled
        from core.config import tool_window_size as _tool_window

        def _mk(t):
            """unified tool → Gemini FunctionDeclaration;缺 sid/name 返回 None。"""
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
            schema_clean = _sanitize_schema(schema_raw)
            try:
                # Gemini 接受 OpenAPI 风格 schema dict 作为 parameters
                return types.FunctionDeclaration(
                    name=full_name,
                    description=(t.get("description") or "")[:512],
                    parameters=schema_clean if schema_clean.get("type") == "object" else {"type": "object", "properties": {}},
                )
            except Exception:
                # 个别字段不兼容时降级到无 schema 的工具
                return types.FunctionDeclaration(
                    name=full_name, description=(t.get("description") or "")[:512])

        # 阶梯化(原 [:64] 硬截断会**丢弃**第 65+ 个工具 → 模型够不到)。窗口内直发,窗口外进
        # load_tools 目录;load 后追加工具会重建 tools_param + 停用显式缓存(见下方 dispatch)。
        window_tools, overflow_index, catalog_lines = _tiered.split_window(
            mcp_tools, _tool_window(), _tiered_enabled())
        loaded_overflow: set[str] = set()
        fn_decls = []
        for t in window_tools:
            fd = _mk(t)
            if fd is not None:
                fn_decls.append(fd)
        if catalog_lines:
            try:
                fn_decls.append(types.FunctionDeclaration(
                    name=_tiered.LOAD_TOOLS_FULL_NAME,
                    description=_tiered.load_tools_description(catalog_lines),
                    parameters=_sanitize_schema(_tiered.LOAD_TOOLS_PARAMS),
                ))
            except Exception:
                pass

        if not fn_decls:
            for chunk in self.stream(system, messages, max_tokens=max_tokens):
                yield {"type": "text", "text": chunk}
            return

        tools_param = [types.Tool(function_declarations=fn_decls)]
        contents = self._to_contents(messages, types)
        # 显式缓存:把 system+tools 这段稳定大前缀建成 CachedContent —— 单轮内多次工具迭代
        # 与同会话多轮都复用同一缓存(前缀按读取价计费)。命中则 request 不再带 system/tools。
        _cache_name = self._prefix_cache_name(system, tools_param)

        for _iteration in range(max_iterations):
            pending_calls: list[dict[str, Any]] = []
            current_text_parts: list[Any] = []
            current_text_str = ""

            # thinking_budget 按用户 effort(ModelPopover);原两条流式分支硬编码 0 = 死设置。
            # thinking_config 是每请求生成参数,与显式缓存(缓存的是 system+tools)互不冲突。
            _budget = _resolve_thinking_budget(self.user_id, self.model_name)
            if _cache_name:
                config = types.GenerateContentConfig(
                    cached_content=_cache_name,
                    max_output_tokens=max(max_tokens, 2048),
                    temperature=0.9,
                    thinking_config=types.ThinkingConfig(thinking_budget=_budget),
                )
            else:
                config = types.GenerateContentConfig(
                    system_instruction=system,
                    max_output_tokens=max(max_tokens, 2048),
                    temperature=0.9,
                    tools=tools_param,
                    thinking_config=types.ThinkingConfig(thinking_budget=_budget),
                )
            for chunk in self.client.models.generate_content_stream(  # type: ignore[assignment]
                model=self.model_name, contents=contents, config=config,
            ):
                if getattr(chunk, "usage_metadata", None):
                    self._capture_usage(chunk)
                # parts 走候选[0]
                cands = getattr(chunk, "candidates", None) or []
                if not cands:
                    continue
                content = getattr(cands[0], "content", None)
                if not content:
                    continue
                for part in (getattr(content, "parts", None) or []):
                    ptext = getattr(part, "text", None)
                    if ptext:
                        if getattr(part, "thought", False):
                            # 思考流:单独走 reasoning 事件,绝不混进正文、不回灌 contents(model 回合)。
                            # 对齐 openai_compat 的 reasoning 事件形态;消费侧 chat_pipeline/gm.py 已就绪。
                            yield {"type": "reasoning", "text": ptext}
                        else:
                            current_text_str += ptext
                            current_text_parts.append(types.Part.from_text(text=ptext))
                            yield {"type": "text", "text": ptext}
                    fc = getattr(part, "function_call", None)
                    if fc:
                        full_name = getattr(fc, "name", "") or ""
                        args_raw = getattr(fc, "args", None) or {}
                        try:
                            args = dict(args_raw)
                        except Exception:
                            args = {}
                        if sep in full_name:
                            server_id, _, tool_name = full_name.partition(sep)
                        else:
                            server_id, tool_name = "", full_name
                        # task 48 fix: Gemini 2.5 多轮 tool_use 需要把模型上一轮产生的
                        # thought_signature 跟 function_call 一起传回去,否则第 2 轮 API
                        # 返 400 "Function call is missing a thought_signature in functionCall parts"。
                        # 解决: 把整个 part 对象存下来 (含 thought_signature),装回 contents
                        # 时直接 append 原 part,而不是用 name+args 重建。
                        pending_calls.append({
                            "name": full_name, "server_id": server_id,
                            "tool_name": tool_name, "arguments": args,
                            "raw_part": part,  # 保留原 part,含 thought_signature
                        })
                        yield {
                            "type": "tool_call", "server_id": server_id,
                            "tool": tool_name, "arguments": args,
                        }

            if not pending_calls:
                return
            # 把 model 回合（文本 + function_call parts）作为 model role 装回 contents
            model_parts: list[Any] = []
            if current_text_str:
                model_parts.append(types.Part.from_text(text=current_text_str))
            for pc in pending_calls:
                # task 48 fix: 优先直接用 SDK 返回的原 part (它含 thought_signature)。
                # raw_part 不可用时降级到重建 (老 SDK / 离线测试场景)。
                raw_part = pc.get("raw_part")
                if raw_part is not None:
                    model_parts.append(raw_part)
                else:
                    try:
                        fc_part = types.Part.from_function_call(name=pc["name"], args=pc["arguments"])
                    except Exception:
                        fc_part = types.Part(function_call=types.FunctionCall(name=pc["name"], args=pc["arguments"]))
                    model_parts.append(fc_part)
            contents.append(types.Content(role="model", parts=model_parts))

            # 顺序 dispatch，把每个 function_response part 收成 user role 一次性 append
            result_parts: list[Any] = []
            for pc in pending_calls:
                # 阶梯化:load_tools 不路由 dispatcher。追加 fn_decls + 重建 tools_param,并把
                # _cache_name 置空 —— Gemini 命中显式缓存时 request 不带 tools,新加载的工具就发不
                # 出去;故 load 后该轮余下迭代改走 inline tools 路径。
                if _tiered.is_load_tools(pc["server_id"], pc["tool_name"]):
                    newly, ack = _tiered.resolve_load(pc["arguments"], overflow_index, loaded_overflow)
                    for t in newly:
                        fd = _mk(t)
                        if fd is not None:
                            fn_decls.append(fd)
                    if newly:
                        tools_param = [types.Tool(function_declarations=fn_decls)]
                        _cache_name = None
                    yield {"type": "tool_result", "ok": True, "result": ack, "error": None}
                    result_parts.append(types.Part.from_function_response(
                        name=pc["name"], response={"result": ack},
                    ))
                    continue
                try:
                    result = mcp_call(pc["server_id"], pc["tool_name"], pc["arguments"])
                except Exception as exc:
                    result = {"ok": False, "error": f"call_tool 异常: {exc}"}
                yield {
                    "type": "tool_result", "ok": bool(result.get("ok")),
                    "result": result.get("result"), "error": result.get("error"),
                }
                # Gemini 要求 response 是 dict
                response_dict = result if isinstance(result, dict) else {"result": str(result)[:2000]}
                # 截断防爆
                try:
                    response_dict = json.loads(json.dumps(response_dict, ensure_ascii=False)[:2000])
                except Exception:
                    response_dict = {"result_truncated": str(response_dict)[:2000]}
                result_parts.append(types.Part.from_function_response(
                    name=pc["name"], response=response_dict,
                ))
            contents.append(types.Content(role="user", parts=result_parts))
        yield {"type": "text", "text": "\n\n【已达本轮工具调用上限 (限制为本次回复内的调用次数,下一条消息自动重置),本轮终止】"}

    @staticmethod
    def _to_contents(messages: list[dict], types):
        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            contents.append(
                types.Content(
                    role=role,
                    parts=[types.Part.from_text(text=msg["content"])],
                )
            )
        return contents
