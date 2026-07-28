"""agents/provider_errors.py — LLM 提供商错误 → 用户可行动文案(确定性分类,单一真相)。

BYOK 场景下「余额耗尽 / key 无效 / 限流」是用户自己能解决的三类错误,绝不能落进
「请重试」泛化兜底(生产实况:DeepSeek 402 余额耗尽,玩家按提示连撞 7 次)。
routes/game.py 的 SSE 错误面与 console_assistant 的 llm loop 共用此分类。

文案必须客户端安全:固定中文文案,不回显 str(exc)(可能含路径/凭据/SDK 内部细节)。
"""

from __future__ import annotations

import re as _re

# 余额/计费配额耗尽:充值才能解决。注意 OpenAI 的 insufficient_quota 走 HTTP 429,
# 但本质是计费问题,必须先于限流判定。
_BALANCE_MARKERS = (
    "insufficient balance",          # DeepSeek 402
    "insufficient_quota",            # OpenAI 429(计费)
    "exceeded your current quota",   # OpenAI 429(计费)
    "insufficient credits",          # OpenRouter 402
    "payment required",              # 通用 402 reason phrase
)

_AUTH_MARKERS = (
    "incorrect api key",
    "invalid api key",
    "please pass a valid api key",   # Google "API key not valid. Please pass a valid API key."
    "401 unauthorized",
    "authentication fails",          # DeepSeek 401 "Authentication Fails (no such user)"
)

# 403 的文本特征单独一组:状态码被 SDK 吞掉时也要走 403 文案,别落进「key 无效/过期」的断言。
# (原来这三条混在 _AUTH_MARKERS 里,任何 403 都会被说成 key 失效 —— 见下方 403 分支的注释。)
_FORBIDDEN_MARKERS = (
    "403 forbidden",                 # 中转站/聚合站对无权限模型常返 403
    "http error 403",                # urllib HTTPError 文案
    "forbidden",                     # 通用 403 reason phrase
)

# 限流/速率配额:稍后重试可恢复。Google/Vertex 的 RESOURCE_EXHAUSTED(429)归这类
# (google.genai 的 ClientError 只有 .code 没有 .status_code,必须靠 message 兜住)。
_RATELIMIT_MARKERS = (
    "rate limit",
    "rate_limit",
    "too many requests",
    "resource_exhausted",
    "resource has been exhausted",
    "quota exceeded",                # Google "Quota exceeded for quota metric ..."
)

# 上下文超长:本回合提示词(历史+世界书+设定)超过所选模型的上下文窗口。换大上下文模型/精简
# 注入才能解决,重试无用。HTTP 多为 400,但 400 太泛(空 assistant 等也是 400),只认特征短语,
# 不靠裸 400 判定,避免误吞其他 400。
_CONTEXT_MARKERS = (
    "maximum context length",            # OpenAI / OpenRouter "maximum context length is N tokens"
    "context_length_exceeded",           # OpenAI error code
    "reduce the length of",              # OpenRouter "Please reduce the length of either one"
    "prompt is too long",                # Anthropic
    "exceed context limit",              # Anthropic "input length and max_tokens exceed context limit"
    "maximum number of tokens allowed",  # Google "input token count exceeds the maximum number of tokens allowed"
    "exceeds the maximum context",       # 通用
    "string too long",                   # 个别中转站对超长输入的措辞
)

# 模型在该账户/服务商下不存在或不可用:换模型才能解决,重试无用。404 或特征短语(中转站
# 对未知模型名常返 400 而非 404,靠短语兜住)。
_MODEL_MARKERS = (
    "model_not_found",                   # OpenAI error code
    "not found for account",             # 部分中转站对无权限/不存在模型的措辞
    "does not exist",                    # 通用 "model xxx does not exist"
)

# 请求所需能力(工具调用/系统指令等)该模型不支持:换模型才能解决,重试无用。目前只见
# Gemini 的 400 + "is not enabled for"(如 "Developer instruction is not enabled" /
# "Function calling is not enabled")。
_FEATURE_MARKERS = (
    "is not enabled for",
)


def _http_status(exc: Exception) -> int | None:
    """从 SDK 异常上取 HTTP 状态码。

    openai/anthropic APIStatusError 用 .status_code;google.genai ClientError /
    urllib HTTPError 用 .code。只认 int 且在合法 HTTP 区间,避免误读 sqlstate 等字段。
    """
    for attr in ("status_code", "code"):
        v = getattr(exc, attr, None)
        if isinstance(v, int) and 100 <= v <= 599:
            return v
    return None


# 形如 API key / Bearer token 的串:带进日志或客户端文案都不行。按**形状**打码,
# 不枚举供应商前缀(sk-/xai-/AIza… 各家不同,枚举必漏)。
_SECRET_SHAPE = _re.compile(
    r"\b(?:bearer\s+)?[A-Za-z0-9_\-]{2,10}[-_][A-Za-z0-9_\-]{20,}\b"   # sk-… / xai-… / 中转站前缀
    r"|\bbearer\s+[A-Za-z0-9._\-]{16,}\b"
    r"|\b[A-Za-z0-9]{32,}\b",                                          # 无前缀长串
    _re.IGNORECASE,
)


def redact_secrets(text: str, *, limit: int = 400) -> str:
    """把疑似密钥的串打码并截断。用于**服务端日志**与客户端文案的共同前置。"""
    s = _SECRET_SHAPE.sub("<redacted>", str(text or "").strip())
    s = " ".join(s.split())
    return s[:limit] + ("…" if len(s) > limit else "")


def _provider_detail(exc: Exception) -> str:
    """取 provider 返回的可读原因(已脱敏截断)。

    SDK 异常的 str() 通常已含响应体;openai SDK 另有 .body(dict)。两者都试,优先 .body
    里的 message/error 字段——它比 str(exc) 干净(不带 URL/状态行)。
    """
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        for k in ("message", "error", "detail", "code"):
            v = body.get(k)
            if isinstance(v, str) and v.strip():
                return redact_secrets(v, limit=200)
            if isinstance(v, dict):
                vv = v.get("message") or v.get("error")
                if isinstance(vv, str) and vv.strip():
                    return redact_secrets(vv, limit=200)
    return redact_secrets(exc, limit=200)


def classify_provider_error(exc: Exception) -> tuple[str, str] | None:
    """已知提供商错误 → (category, 客户端安全文案);未知返回 None(调用方走各自兜底)。

    category ∈ {"balance", "auth", "ratelimit", "context", "upstream", "model_unavailable",
    "feature_unsupported"}。文案不含 error_id,调用方自行追加。
    """
    raw_lower = str(exc).strip().lower()
    status = _http_status(exc)
    if status == 402 or any(m in raw_lower for m in _BALANCE_MARKERS):
        return ("balance",
                "当前模型的 API 账户余额不足或配额已用尽，重试无法恢复。"
                "请前往对应 API 提供商充值，或到「设置 → API 设置」切换其他已配置的模型。")
    # 403 ≠ key 失效。群反馈(星色マジック,2026-07-28,xai/grok):同一个 key 在 SillyTavern
    # 一直好用,在这里「说不了几句就提示凭证过期 403」。实测 api.x.ai **无凭据时返回 401**
    # (`{"code":"unauthenticated:no-credentials"}`),它的 403 是别的原因(拒绝该请求),而且
    # 生产日志里 200/403 交替(24h 内 21 次 200、12 次 403)—— 断言「key 无效/已过期」把用户
    # 支去查一个根本没坏的东西。故 403 单独成文案:说清是「被拒绝」,并把 provider 自己的原话
    # 带给用户(那是唯一可行动的信息);401 才保留「key 无效/过期」的断言。
    if status == 403 or any(m in raw_lower for m in _FORBIDDEN_MARKERS):
        return ("auth", "当前模型的请求被提供商拒绝(HTTP 403)。"
                        "403 通常**不是** key 失效(多数提供商 key 无效返 401),"
                        "更常见的是该 key/套餐无权访问此模型、或该请求内容被提供商策略拦下。"
                        f"提供商原话:{_provider_detail(exc) or '(未提供)'} "
                        "可先换一个模型试;若同一 key 在别处能用,多半是这个模型或这段内容的问题。")
    if status == 401 or any(m in raw_lower for m in _AUTH_MARKERS):
        return ("auth",
                "当前模型的 API Key 无效、已过期,或该 key 无权访问此模型(401 Unauthorized)。"
                "请到「模型与密钥」重新测试凭证、确认该 key/套餐包含此模型,或切换到已配置的其他模型。")
    if status == 429 or any(m in raw_lower for m in _RATELIMIT_MARKERS):
        return ("ratelimit",
                "当前模型请求过于频繁（提供商限流）。"
                "请稍候片刻再重试，或切换到其他模型。")
    # 上下文超长放在限流之后:它是 400 + 特征短语,与上面三类(402/401/429)不重叠。
    if any(m in raw_lower for m in _CONTEXT_MARKERS):
        return ("context",
                "本回合的剧情上下文（历史 + 世界书 + 设定）超过了所选模型的上下文长度上限，"
                "重试也无法恢复。请到「设置 → 模型 / API 设置」换用上下文窗口更大的模型"
                "（例如百万级上下文的 Gemini 2.5 Flash / Pro 等），或精简世界书 / 历史注入后再试。")
    # 提供商服务器侧 5xx / 网关错误(502/503/504/520-524,含 Cloudflare origin 故障):供应商 / 中转站
    # 过载或宕机,与请求内容、平台、存档都无关,是对面服务器暂时没响应。放最后:前面 4xx 已排除。
    # 双判:HTTP 5xx 状态,或 message 命中网关特征(状态码被 SDK 吞掉时兜住)。
    if (status is not None and 500 <= status <= 599) or any(
        m in raw_lower for m in ("cloudflare", "bad gateway", "gateway time", "service unavailable", "origin_bad_gateway")
    ):
        code = str(status) if status else "5xx"
        return ("upstream",
                f"你的模型服务暂时不可用（服务器返回 {code} 网关错误，多为供应商 / 中转站过载或宕机），"
                "不是平台或存档的问题。请稍等片刻重试，或到「设置 → 模型 / API 设置」换用其他模型 / 供应商。")
    # 模型在该服务商/账户下不可用:404,或中转站对未知模型名的特征短语。重试无法恢复。
    if status == 404 or any(m in raw_lower for m in _MODEL_MARKERS):
        return ("model_unavailable",
                "当前模型在该服务商/账户下不可用，重试无法恢复。"
                "请到「设置 → API 设置」切换其他模型，或联系你的 API 提供商确认模型名。")
    # 该模型不支持本次请求所需的功能(工具调用/系统指令等):400 + 特征短语。重试无法恢复。
    if status == 400 and any(m in raw_lower for m in _FEATURE_MARKERS):
        return ("feature_unsupported",
                "该模型不支持本次请求所需的功能(如工具调用/系统指令)，重试无法恢复。"
                "请切换到支持完整功能的模型。")
    return None
