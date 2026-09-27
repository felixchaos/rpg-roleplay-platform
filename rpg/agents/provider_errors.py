"""agents/provider_errors.py — LLM 提供商错误 → 用户可行动文案(确定性分类,单一真相)。

BYOK 场景下「余额耗尽 / key 无效 / 限流」是用户自己能解决的三类错误,绝不能落进
「请重试」泛化兜底(生产实况:DeepSeek 402 余额耗尽,玩家按提示连撞 7 次)。
routes/game.py 的 SSE 错误面与 console_assistant 的 llm loop 共用此分类。

文案必须客户端安全:固定中文文案,不回显 str(exc)(可能含路径/凭据/SDK 内部细节)。
需要带「提供商原话」的分支一律走 _provider_detail(按形状脱敏 + 截断)。

urllib 出站(子代理 harness / extractor / command_agent)抛的 HTTPError,str() 只有
「HTTP Error 410: Gone」,真实原因在响应体里。抛出点用 attach_http_error_body 把响应体
挂到 exc.body 上(与 openai SDK 的 .body 同形),分类器才看得见。
"""

from __future__ import annotations

import json as _json
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
# 前两条明写了 403,中转站把上游 403 包成 400/500 转发时也可信,不看状态码;
# 裸 "forbidden" 只在状态码未知或 400 时生效(400 内容策略拒绝常写 forbidden;404/410 等
# 有自己的结论,错误页正文里顺带出现的 forbidden 不算),且先剔掉参数校验的错误类型名 extra_forbidden
# (vLLM / pydantic 对多余参数回 400 + "extra_forbidden",以前被说成「被拒绝(HTTP 403)」)。
_FORBIDDEN_STRONG_MARKERS = (
    "403 forbidden",                 # 中转站/聚合站对无权限模型常返 403
    "http error 403",                # urllib HTTPError 文案
)
_FORBIDDEN_WEAK_MARKER = "forbidden"  # 通用 403 reason phrase
_FORBIDDEN_FALSE_FRIENDS = ("extra_forbidden",)

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
# 以前这里还有裸 "does not exist":psycopg 的「relation / column "x" does not exist」也会命中,
# 导入阶段写库失败被说成「模型不可用」并让人物卡阶段提前停。改由 _MODEL_MISSING_RE 按「模型做主语」认。
_MODEL_MARKERS = (
    "model_not_found",                   # OpenAI error code
    "not found for account",             # 部分中转站对无权限/不存在模型的措辞
    "model_decommissioned",              # Groq 等对已下线模型的 error code
    "模型不存在",                         # 国内中转站
)

# 模型标识:可带引号,只收 ASCII 字符(\w 在 Python 里会吃中文,「该模型的参数已弃用」会被拼成
# 「模型 + 标识"的参数" + 已弃用」)。model 后面紧跟收尾引号的是别人引号里的名字(psycopg 的
# column "model" does not exist),不算主语。前面有引号不能排除:JSON 的 "message":"model x …"
# 里 model 正好在引号后面。
_MODEL_ID = r"[`'\"「『“]?[a-z0-9_./:@\-]{0,100}[`'\"」』”]?"
_MODEL_WORD = r"\bmodels?\b(?![`'\"」』”])"
_MODEL_HEAD = _MODEL_WORD + r":?\s*" + _MODEL_ID

# 模型已被服务商下线 / 停用 / 到期(生产实况:OpenRouter 410
# "The model 'openai/gpt-oss-120b' has reached its end of life …")。和 404 一样换模型才能解决,
# 归同一个 model_unavailable,不另开平行类别。HTTP 410 直接命中;状态码丢了时靠措辞兜:
# 措辞必须以「模型」做主语、紧跟下线动词,不能裸认 "deprecated / no longer supported /
# end of life" —— 参数级报错("'functions' parameter is no longer supported")和回显的
# 玩家正文("the knight reached the end of life")里都有这些词,宁漏勿误。
# 「模型」只是介词宾语的也不算("The 'max_tokens' parameter of this model is deprecated"
# 说的是参数),见 _model_is_subject。
_MODEL_GONE_RE = _re.compile(
    _MODEL_HEAD + r"\s+(?:has\s+|is\s+)?(?:been\s+)?"
    r"(?:reached\s+(?:its\s+)?end[\s\-]of[\s\-]life|deprecated|decommissioned|retired|shut\s+down"
    r"|no\s+longer\s+(?:available|served|supported))"
    r"|模型\s*" + _MODEL_ID + r"\s*(?:已经|已)被?(?:下线|停用|弃用|停止服务|退役)"
    r"|模型\s*" + _MODEL_ID + r"\s*不再提供服务",
    _re.IGNORECASE,
)

# 模型不存在(状态码丢了时):"The model `gpt-9` does not exist" / DeepSeek 400 "Model Not Exist"。
_MODEL_MISSING_RE = _re.compile(
    _MODEL_HEAD + r"\s+(?:does\s+not|doesn't)\s+exist"
    r"|" + _MODEL_WORD + r"\s+not\s+exists?\b",
    _re.IGNORECASE,
)

# 「模型」前面紧挨介词(of / for / with this model …;对该模型…)= 它是宾语,主语是别的东西。
_PREP_BEFORE_MODEL = _re.compile(
    r"\b(?:of|for|with|in|on|by|to|from)\s+(?:(?:this|the|that|these|those|your|a|an|each|any)\s+)?$"
    r"|(?:对|对于|针对|用于|在)(?:此|该|这个|本|当前)?$",
    _re.IGNORECASE,
)


def _model_is_subject(rx: _re.Pattern, text: str) -> bool:
    """rx 在 text 里有没有一处命中是以「模型」做主语的(排除介词宾语)。"""
    for m in rx.finditer(text):
        if _PREP_BEFORE_MODEL.search(text[max(0, m.start() - 24):m.start()]):
            continue
        return True
    return False


# 请求所需能力(工具调用/系统指令等)该模型不支持:换模型才能解决,重试无用。目前只见
# Gemini 的 400 + "is not enabled for"(如 "Developer instruction is not enabled" /
# "Function calling is not enabled")。
_FEATURE_MARKERS = (
    "is not enabled for",
)

# 请求里有对面不认识 / 不支持的参数:多见于中转站或兼容接口不接受平台发的某个调参
# (思考开关、stream_options、采样参数),也有 OpenAI 官方对推理模型拒 max_tokens 的情况。
# 重试不会好,只能换模型/供应商或让中转站维护者放行。只在 4xx 或无状态码时认。
_UNKNOWN_PARAM_MARKERS = (
    "未识别参数",                         # 生产实况:"code:400 请求失败:请求中含有未识别参数"
    "无法识别的参数",
    "不支持的参数",
    "未知参数",
    "unrecognized request argument",     # OpenAI "Unrecognized request argument supplied: x"
    "unrecognized parameter",
    "unrecognized argument",
    "unrecognized field",
    "unknown parameter",                 # OpenAI "Unknown parameter: 'x'."
    "unknown_parameter",
    "unsupported parameter",             # OpenAI "Unsupported parameter: 'max_tokens' ..."
    "unsupported_parameter",
    "unsupported value",                 # OpenAI 推理模型 "Unsupported value: 'temperature' ..."
    "unsupported_value",
    "extra inputs are not permitted",    # pydantic(vLLM / Anthropic 兼容层)
    "extra_forbidden",
)

# 流内错误里的内容审核类:别当「供应商临时故障」去重试(白白重发整段提示词),也别计入
# 渠道健康失败(model_probe 按 api_id 跨用户聚合,一个人的审核拒绝会把公共渠道标成故障)。
_CONTENT_POLICY_MARKERS = (
    "content_policy",
    "content_filter",
    "content management policy",
    "safety system",
    "moderation",
    "content exists risk",               # DeepSeek 风控
    "敏感",
    "违规",
    "审核",
)


# SDK 构造期就崩:api_key 为空/未传。openai SDK(实测 2.41.1)对 api_key="" 与 None 一视同仁,
# 直接抛 OpenAIError("Missing credentials") —— **连请求都没发出去**,所以没有 HTTP 状态码,
# 前面所有按 status 的分支全不命中,一路落进「本轮处理出错,请重试(错误码 Exxx)」泛化兜底。
# 这是 BYOK 最高频的一类失败(没填 key / key 被清空 / 免鉴权配置残缺),却是用户最看不懂的
# 一类报错:文案让人去"重试",而重试一万次也不会好。归 auth,给出可行动指引。
_CREDENTIAL_MISSING_MARKERS = (
    "missing credentials",                 # openai SDK
    "client option must be set",           # openai/anthropic "The api_key client option must be set"
    "could not resolve authentication",    # anthropic SDK
    "no api key provided",
)

# 连接层失败:请求根本没送达对面(地址不通/端口没开/DNS 解不出/代理不可达/握手超时)。
# 同样没有 HTTP 状态码 —— 之前整类落进泛化兜底,而它恰恰是**自定义 base_url / 本地模型
# (Ollama·LM Studio·vLLM)/中转站**用户的头号故障:玩家只看到一个随机错误码,既不知道是
# 自己网络的问题,也不知道该去看接口地址还是看服务有没有起。
# 类型判定优先于字符串:各 SDK 的措辞差异极大(openai "Connection error." / httpx
# "All connection attempts failed" / urllib "[Errno 61] Connection refused"),枚举必漏;
# 而类名在 openai/anthropic/httpx/urllib 之间反而是稳定的一小撮。
_CONNECTION_EXC_NAMES = frozenset({
    "APIConnectionError",      # openai / anthropic(APITimeoutError 是其子类,走 MRO 命中)
    "APITimeoutError",
    "TransportError",          # httpx 传输层基类(ConnectError/ReadTimeout/ProxyError 全在其下)
    "ConnectError", "ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout",
    "ProxyError", "URLError", "TimeoutError", "ConnectionError",
})

_CONNECTION_MARKERS = (
    "connection error",
    "connection refused",
    "connection reset",
    "connection aborted",
    "all connection attempts failed",
    "failed to establish a new connection",
    "name or service not known",
    "nodename nor servname",
    "temporary failure in name resolution",
    "getaddrinfo failed",
    "timed out",
    "read timeout",
    "proxy",
)


def _outbound_blocked_in_chain(exc: BaseException) -> BaseException | None:
    """异常自身或显式 __cause__ 链上有没有 core.outbound.OutboundBlocked。

    服务器模式下出站 SSRF 闸拒绝(目标解析失败、解析到内网/保留地址)时:httpx 线经
    OutboundBlockedTransportError 被 SDK 包成 APIConnectionError("Connection error."),
    openai / anthropic 都是 `raise ... from err`,真正原因挂在 __cause__ 上;urllib 线
    (子代理 harness)则直接抛裸 OutboundBlocked。两条线都按类名找,不 import core.outbound
    (本模块保持零依赖)。
    不走 __context__:那是「处理 OutboundBlocked 时又出了别的错」的隐式链,顺着它找会把
    无关异常说成「连不上接口地址」、还把闸门原因当成底层报错显示出去。
    """
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen and len(seen) < 8:
        seen.add(id(cur))
        if any(k.__name__ == "OutboundBlocked" for k in type(cur).__mro__):
            return cur
        cur = cur.__cause__
    return None


def _is_connection_failure(exc: Exception) -> bool:
    """请求是否根本没送达对面(连接/DNS/代理/超时),而非对面返回了错误。

    先按异常类的整条 MRO 判类名(子类如 APITimeoutError / ConnectTimeout 一并命中),
    再按措辞兜底。**调用方必须先排完所有带 HTTP 状态码的分支**:504 gateway timeout
    这类带 status 的错误措辞里也有 "timeout",顺序反了会被这里吞掉。

    带 4xx/5xx 状态码 = 对面已经回过话,一定不是连接失败。urllib 的 HTTPError 是 URLError
    的子类,按类名会命中下面的 "URLError",以前没被状态码分支接住的 400/405/410/413/422
    全被说成「连不上接口地址」。例外两类仍归连接层:30x(safe_urlopen 出于安全不跟随重定向,
    多半是 base_url 协议或路径写错;classify_provider_error 在前面按状态码先给了专门文案)
    和 408(请求超时)。
    """
    st = _http_status(exc)
    if st is not None and st >= 400 and st != 408:
        return False
    if _outbound_blocked_in_chain(exc) is not None:
        return True
    for klass in type(exc).__mro__:
        if klass.__name__ in _CONNECTION_EXC_NAMES:
            return True
    return any(m in str(exc).strip().lower() for m in _CONNECTION_MARKERS)


_THREE_DIGITS = _re.compile(r"[0-9]{3}")


def _http_status(exc: Exception) -> int | None:
    """从 SDK 异常上取 HTTP 状态码。

    openai/anthropic APIStatusError 用 .status_code;google.genai ClientError /
    urllib HTTPError 用 .code。只认合法 HTTP 区间,避免误读 sqlstate 等字段。

    openai>=3.14 把 APIError.code 一律转成 str(流内错误 {"code": 502} 变成 "502"),
    所以 .code 为恰好三位 ASCII 数字的字符串也认;4 位业务码(智谱 "1301")、bool、
    "5O2" 这类都不当状态码。status_code 优先。
    """
    for attr in ("status_code", "code"):
        v = getattr(exc, attr, None)
        if isinstance(v, bool):
            continue
        if isinstance(v, str):
            s = v.strip()
            if not _THREE_DIGITS.fullmatch(s):
                continue
            v = int(s)
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


def _body_text(exc: Exception, *, limit: int = 4000) -> str:
    """exc.body(openai SDK 的 dict / attach_http_error_body 挂上的 dict 或文本)转成文本。"""
    body = getattr(exc, "body", None)
    if body is None:
        return ""
    if isinstance(body, (bytes, bytearray)):
        text = bytes(body[:limit]).decode("utf-8", "replace")
    elif isinstance(body, str):
        text = body
    else:
        try:
            text = _json.dumps(body, ensure_ascii=False, default=str)
        except Exception:
            text = str(body)
    return text[:limit]


def _provider_detail(exc: Exception) -> str:
    """取 provider 返回的可读原因(已脱敏截断)。

    SDK 异常的 str() 通常已含响应体;openai SDK 另有 .body(dict)。两者都试,优先 .body
    里的 message/error 字段——它比 str(exc) 干净(不带 URL/状态行)。
    .body 是纯文本(中转站常见,如「code:400 请求失败:请求中含有未识别参数」)时原样取;
    是没有这些字段的 dict/list(如 pydantic 的 {"detail": [...]})时取整段 JSON ——
    urllib HTTPError 的 str() 只有「HTTP Error 400: Bad Request」,退回它等于把原因丢了。
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
    if isinstance(body, str) and body.strip():
        return redact_secrets(body, limit=200)
    if isinstance(body, (dict, list)) and body:
        return redact_secrets(_body_text(exc), limit=200)
    return redact_secrets(exc, limit=200)


def provider_detail(exc: Exception) -> str:
    """公开入口:provider 原话(已脱敏、截断到 200 字)。给导入阶段条目等非对话出错面用。"""
    return _provider_detail(exc)


def http_status(exc: Exception) -> int | None:
    """公开入口:异常上的 HTTP 状态码(取法与分类器一致)。"""
    return _http_status(exc)


_HTML_TITLE = _re.compile(r"<title[^>]*>(.*?)</title>", _re.IGNORECASE | _re.DOTALL)
_HTML_TAG = _re.compile(r"<[^>]+>")


def attach_http_error_body(exc: BaseException, *, limit: int = 2000) -> None:
    """把 urllib HTTPError 的响应体读出来挂到 exc.body(幂等;读不到就算了,绝不抛)。

    urllib 的 HTTPError 不读响应体,str() 只有「HTTP Error 410: Gone」,服务商给的真实原因
    (模型下线说明、未识别参数、上下文超长)对分类器全不可见。挂上后与 openai SDK 的 .body
    同形:JSON 响应取 error 子对象(没有就整个对象),非 JSON 取文本(HTML 错误页只取 <title>
    或去标签后的文字)。只读前 limit 字节。

    会消费 HTTPError 的响应流 —— 只在「抛出后没人再 exc.read()」的出站点用(子代理 harness /
    extractor / command_agent);embedding / gemini 路径自己读 body,别接。
    """
    if getattr(exc, "body", None) is not None:
        return
    read = getattr(exc, "read", None)
    if not callable(read):
        return
    try:
        raw = read(limit)
    except Exception:
        return
    if not raw:
        return
    if isinstance(raw, (bytes, bytearray)):
        text = bytes(raw).decode("utf-8", "replace")
    else:
        text = str(raw)
    text = text.strip()
    if not text:
        return
    body: object = text
    if text[:1] in "{[":
        try:
            parsed = _json.loads(text)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            inner = parsed.get("error")
            body = inner if isinstance(inner, dict) else parsed
        elif isinstance(parsed, list):
            body = parsed
    elif text[:1] == "<":
        m = _HTML_TITLE.search(text)
        plain = m.group(1) if m else _HTML_TAG.sub(" ", text)
        body = " ".join(plain.split())[:300] or text[:300]
    try:
        exc.body = body  # type: ignore[attr-defined]
    except Exception:
        pass


def _is_openai_stream_error(exc: Exception, status: int | None) -> bool:
    """HTTP 200 的流里出现的错误事件。

    openai SDK 在流内遇到 {"error": ...} 时抛**不带状态码**的裸 APIError(_streaming.py 是
    SDK 里唯一抛裸 APIError 的地方);anthropic 抛 status_code=200 的 APIStatusError
    (如 overloaded_error)。google-genai 也有同名的 errors.APIError,按模块排除。
    """
    if status == 200:
        return True
    if status is not None:
        return False
    t = type(exc)
    return t.__name__ == "APIError" and (t.__module__ or "").startswith("openai")


def classify_provider_error(exc: Exception) -> tuple[str, str] | None:
    """已知提供商错误 → (category, 客户端安全文案);未知返回 None(调用方走各自兜底)。

    category ∈ {"balance", "auth", "ratelimit", "context", "upstream", "model_unavailable",
    "feature_unsupported", "bad_request", "network"}。文案不含 error_id,调用方自行追加。
    只有 upstream / ratelimit 可重试(stream_retry)、计入渠道健康(_note_channel_health_failure)。
    """
    body_text = _body_text(exc)
    raw_lower = (str(exc).strip() + (" " + body_text if body_text else "")).lower()
    status = _http_status(exc)
    # 凭据缺失放最前:它是构造期异常,不带 status、也不带 provider 响应体,与下面任何一类
    # 都不重叠,且"请重试"对它绝对无效。
    if status is None and any(m in raw_lower for m in _CREDENTIAL_MISSING_MARKERS):
        return ("auth",
                "这个模型所属的供应商还没有可用的 API Key(请求没能发出去)。"
                "请到「设置 → 模型与密钥」为该供应商填入 API Key 并测试凭证;"
                "若用的是本地模型(Ollama / LM Studio / vLLM 等),请把该供应商的鉴权方式"
                "选为「无需 API Key」并填好接口地址。")
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
    _fb_scan = raw_lower
    for _ff in _FORBIDDEN_FALSE_FRIENDS:
        _fb_scan = _fb_scan.replace(_ff, "")
    _strong_403 = any(m in _fb_scan for m in _FORBIDDEN_STRONG_MARKERS)
    _weak_403 = status in (None, 400) and _FORBIDDEN_WEAK_MARKER in _fb_scan
    if status == 403 or _strong_403 or _weak_403:
        _shown = 403 if (status in (None, 403) or _strong_403) else status
        return ("auth", f"当前模型的请求被提供商拒绝(HTTP {_shown})。"
                        "这通常不是 key 失效(多数提供商 key 无效返 401),"
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
    # 413 Payload Too Large 本质也是这一回合塞给模型的东西太大。
    if status == 413 or any(m in raw_lower for m in _CONTEXT_MARKERS):
        return ("context",
                "本回合的剧情上下文（历史 + 世界书 + 设定）超过了所选模型的上下文长度上限，"
                "重试也无法恢复。请到「设置 → 模型 / API 设置」换用上下文窗口更大的模型"
                "（例如百万级上下文的 Gemini 2.5 Flash / Pro 等），或精简世界书 / 历史注入后再试。")
    # 30x:urllib 线(safe_urlopen 出于安全不跟随重定向)抛 HTTPError,SDK 线是 APIStatusError
    # (如 http 被重定向到 https 的 307)。按状态码统一判,不分异常类型 —— 以前只有 urllib 那条
    # 经连接层分支拿到这句,SDK 的 307 落空成「请重试」。多半是 base_url 协议或路径写错。
    if status is not None and 300 <= status < 400:
        return ("network",
                f"接口地址返回了重定向(HTTP {status}),平台出于安全不跟随重定向。"
                "请到「设置 → API 设置」检查该供应商的接口地址(base_url):"
                "协议是 http 还是 https、路径是否少了 /v1。")
    # 模型不存在(404)/ 已下线(410):状态码本身就是结论,放在网关措辞兜底之前 ——
    # 挂 Cloudflare 的服务商,4xx 错误页正文里也带 "cloudflare"。
    # 404 也可能是路由不存在(base_url 少了 /v1),多给一句怎么分辨。
    if status in (404, 410):
        return ("model_unavailable",
                _MODEL_UNAVAILABLE_MSG + (_ROUTE_404_HINT if status == 404 else ""))
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
    # 该模型不支持本次请求所需的功能(工具调用/系统指令等):400 + 特征短语。重试无法恢复。
    if status == 400 and any(m in raw_lower for m in _FEATURE_MARKERS):
        return ("feature_unsupported",
                "该模型不支持本次请求所需的功能(如工具调用/系统指令)，重试无法恢复。"
                "请切换到支持完整功能的模型。")
    # 请求里有对面不认识/不支持的参数(中转站最常见)。排在模型措辞之前:这类报错里偶尔也会出现
    # "does not exist"(指参数不存在),不能被说成模型不可用。
    if (status is None or 400 <= status < 500) and any(m in raw_lower for m in _UNKNOWN_PARAM_MARKERS):
        return ("bad_request",
                "模型服务拒绝了这次请求:请求里有它不认识或不支持的参数,重试无法恢复。"
                "多半是模型服务或中转站不接受平台发送的某个参数(比如思考开关、采样参数、工具调用)。"
                "请先换一个模型或供应商;如果用的是中转站,可以把下面这句原话转给它的维护者。"
                f"提供商原话:{_provider_detail(exc) or '(未提供)'}")
    # 状态码被 SDK 吞掉时,按措辞认「模型不存在 / 已下线」。
    if (_model_is_subject(_MODEL_GONE_RE, raw_lower) or _model_is_subject(_MODEL_MISSING_RE, raw_lower)
            or any(m in raw_lower for m in _MODEL_MARKERS)):
        return ("model_unavailable", _MODEL_UNAVAILABLE_MSG)
    # 流内错误(HTTP 200 的流里来了 error 事件):多为供应商/中转站在生成中途出错,归 upstream,
    # 首 token 前自动重试、计入渠道健康。内容审核类除外(重试无用,也不该拖累公共渠道的健康标记)。
    if _is_openai_stream_error(exc, status) and not any(m in raw_lower for m in _CONTENT_POLICY_MARKERS):
        return ("upstream",
                "模型服务在生成过程中返回了错误,多为供应商或中转站临时故障,不是平台或存档的问题。"
                f"提供商原话:{_provider_detail(exc) or '(未提供)'} "
                "请稍等片刻重试,或到「设置 → API 设置」换用其他模型或供应商。")
    # 连接层失败放最后:它没有 HTTP 状态码,必须等上面所有带 status 的分支排完
    # (504 gateway timeout 的措辞里也有 "timeout",顺序反了会被误吞成"连不上")。
    if _is_connection_failure(exc):
        _blocked = _outbound_blocked_in_chain(exc)
        _low = redact_secrets(_blocked if _blocked is not None else exc, limit=120)
        return ("network",
                "连不上这个模型的接口地址(请求没送达或没等到响应),不是存档或剧本的问题。"
                "请依次检查:① 「设置 → 模型与密钥」里该供应商的接口地址(base_url)是否正确;"
                "② 若是本地模型,对应的服务(Ollama / LM Studio / vLLM)是否正在运行、端口是否一致;"
                "③ 网络或代理能否访问该地址。"
                f"底层报错:{_low or '(无)'}")
    return None


_MODEL_UNAVAILABLE_MSG = (
    "当前模型不可用:已被服务商下线或不存在(也可能是这个账户无权使用它),重试无法恢复。"
    "请到「设置 → API 设置」换一个模型;如果确认模型名没写错,可以向 API 提供商确认。"
)
_ROUTE_404_HINT = "如果换哪个模型都报这个错,多半是接口地址(base_url)不对,比如少了 /v1。"


def provider_error_summary(exc: Exception) -> str:
    """给「不在对话流里」的出错面(导入阶段条目、job.error)用的一句话原因。

    已分类 → 分类文案;未分类但对面回过话(有状态码或响应体)→ 类型 + 提供商原话
    (urllib HTTPError 挂了 body 后,原话比「HTTP Error 422」有用得多);
    其它 → 类型 + 脱敏截断的异常文本。

    只给**模型调用**抛出的异常用。写库失败、解析失败这类本地异常别往这里送:分类器会拿
    异常文本去匹配服务商措辞,psycopg 的「relation … does not exist」、回显的小说正文里的
    「Forbidden」「timed out」都会被说成服务商的问题 —— 那种用 plain_error_summary。
    """
    known = classify_provider_error(exc)
    if known:
        return known[1]
    if _http_status(exc) is not None or getattr(exc, "body", None) is not None:
        return f"{type(exc).__name__}: {_provider_detail(exc)}"
    return plain_error_summary(exc)


def plain_error_summary(exc: BaseException) -> str:
    """不做服务商分类的一句话摘要:类型 + 脱敏截断的异常文本。给本地异常(写库、解析等)用。"""
    name = type(exc).__name__
    detail = redact_secrets(exc, limit=160)
    return f"{name}: {detail}" if detail else name
