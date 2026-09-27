"""platform_app.knowledge.embedding._breaker — 嵌入供应商熔断 + 按用户隔离的最近错误(叶子层)。

为什么要有它(巡检 2026-09-28):嵌入供应商坏掉(中转站 429 / 余额不足 402 / 模型 404 /
key 失效 401 / 卡住超时)后,每回合的检索照样对它连打 4-5 次,每次失败再退关键词召回。
对「每分钟 2 次、失败也计数」的中转站,这些嵌入请求会把用户自己的聊天配额一起吃掉;
超时的供应商则让上下文阶段一回合串行等好几个 60s。

做法:以 (用户, 供应商, 模型, 接口地址, key 指纹) 为一个熔断单元。失败按类别进冷却,
冷却期内查询路径直接返回「无向量」走关键词降级,不再发请求;成功一次即清除。
换 key / 换地址 / 换模型会自然落到新的单元上,改完配置立即生效;保存/删除凭据时
另有 reset_user 清掉该用户的全部单元(充值后 key 不变的情况)。

分类与冷却:
- config   401/402/403/404/405、维度不符、空地址拒发、SSRF 拒连、代理用不了:改配置前不会好,600s(402 为 900s)
- no_cred  没有凭据 / 没有 Service Account(没发请求):300s
- rate     429:读 Retry-After(夹在 30-600s),没有则 90s;冷却到期后再次 429 翻倍,封顶 900s
- timeout  超时 / 连不上:首次即 60s(免得一回合串行等 N 个 60s)
- server   5xx、200 但响应体坏:连续 3 次才 30s
- request  400(去 dimensions 重试后仍 400)/413/422:跟这条文本有关(内容审核、超长),不熔断

批量写库路径(导入 / 重建向量)只在 config / no_cred 时短路 —— 限流、瞬时故障照常真打,
由各自的重试循环处理;否则一次 429 就会让角色卡 / 世界书 / canon 整片被跳过。
冷却中写库路径又失败时,冷却只延长不缩短,原因按优先级取(config / no_cred > rate > timeout / server)。

进程内状态(多 worker 各自熔断,每个 worker 最多多打一次,可接受)。明文 key 不进内存表,
只存 sha256 指纹。本模块只依赖 _base,测试 reload 包门面时这里的状态与 ContextVar 不会被重建。
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import threading
import time
import urllib.parse
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from ._base import log

KIND_CONFIG = "config"
KIND_NO_CRED = "no_cred"
KIND_RATE = "rate"
KIND_TIMEOUT = "timeout"
KIND_SERVER = "server"
KIND_REQUEST = "request"

_CONFIG_COOLDOWN = 600.0
_PAYMENT_COOLDOWN = 900.0      # 402:余额不足,充值一般比改 key 慢
_NO_CRED_COOLDOWN = 300.0
_TIMEOUT_COOLDOWN = 60.0
_SERVER_COOLDOWN = 30.0
_SERVER_TRIP_AFTER = 3
_RATE_DEFAULT = 90.0
_RATE_MIN = 30.0
_RATE_MAX = 600.0
_RATE_ESCALATE_CAP = 900.0

# 批量写库路径只在这两类熔断时短路(见模块 docstring)
_BATCH_BLOCKING_KINDS = frozenset({KIND_CONFIG, KIND_NO_CRED})

# 冷却中又失败时原因的优先级:要用户改配置的 > 限流 > 超时 / 5xx。高的不被低的改写 ——
# 限流被写库路径的一次超时改成「超时」,写库循环就不按限流退避了;配置类则要让写库循环立即放弃。
_KIND_RANK = {KIND_CONFIG: 3, KIND_NO_CRED: 3, KIND_RATE: 2, KIND_TIMEOUT: 1, KIND_SERVER: 1}

_MAX_ENTRIES = 4096
_MAX_LAST_ERRORS = 2048

_KIND_LABEL = {
    KIND_CONFIG: "配置错误",
    KIND_NO_CRED: "没有可用凭据",
    KIND_RATE: "被限流",
    KIND_TIMEOUT: "超时或连不上",
    KIND_SERVER: "服务端出错",
}

# 可注入时钟(测试替换,免真 sleep)
_clock = time.monotonic


@dataclass
class Attempt:
    """一次 dispatch 期间各通道写下的失败信息(由 dispatch 读取后记入熔断)。"""
    kind: str | None = None
    status: int | None = None
    retry_after: float | None = None
    friendly: str = ""


@dataclass
class _Entry:
    user_id: int | None
    label: str                 # 日志用:host + 模型,不含 key
    kind: str = ""
    until: float = 0.0
    server_strikes: int = 0
    rate_trips: int = 0
    last_rate_cooldown: float = 0.0


_ATTEMPT: contextvars.ContextVar[Attempt | None] = contextvars.ContextVar("_embed_attempt", default=None)
# 本上下文里自上次 reset_dispatched() 起,是否真的调用过某个通道(没被熔断拦下)。
# embed_query 据此区分「真失败」(WARNING,巡检计数口径)与「冷却中短路 / 根本没配」(DEBUG)。
_DISPATCHED: contextvars.ContextVar[bool] = contextvars.ContextVar("_embed_dispatched", default=False)

_LOCK = threading.Lock()
_STATE: OrderedDict[str, _Entry] = OrderedDict()
# user_id → (熔断单元 key, 友好错误文案)。按用户隔离:A 用户的中转站主机名和报错不会出现在 B 的提示里。
_LAST_ERROR: OrderedDict[Any, tuple[str, str]] = OrderedDict()


# ── 熔断单元 key ─────────────────────────────────────────────────────────────
def key_for(user_id: int | None, api_id: str, model: str, base_url: str, api_key: str) -> str:
    parts = urllib.parse.urlsplit(base_url or "")
    endpoint = (parts.netloc + parts.path).rstrip("/").lower() if parts.netloc else (base_url or "").strip().lower()
    key_fp = hashlib.sha256(api_key.encode()).hexdigest()[:16] if api_key else ""
    raw = f"{int(user_id or 0)}|{api_id or ''}|{model or ''}|{endpoint}|{key_fp}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _label(api_id: str, model: str, base_url: str) -> str:
    host = urllib.parse.urlsplit(base_url or "").netloc or (api_id or "?")
    return f"{host} / {model or '?'}"


# ── 通道侧:写下本次失败 ─────────────────────────────────────────────────────
@contextlib.contextmanager
def attempt() -> Iterator[Attempt]:
    att = Attempt()
    token = _ATTEMPT.set(att)
    try:
        yield att
    finally:
        _ATTEMPT.reset(token)


def note(kind: str, *, friendly: str = "", status: int | None = None,
         retry_after: float | None = None, overwrite: bool = True) -> None:
    """通道失败时调用。不在 dispatch 内(测试直接调通道)时为空操作。

    默认后写覆盖先写(最后一次真实尝试的失败原因最准)。overwrite=False 用于「兜底子通道
    根本没发请求」的情形:同一次 dispatch 里前一个子通道已经真打并记下原因时,别用
    「没凭据」把它盖掉(_vertex:平台 Gemini 原生先被 429,再发现没有 SA)。
    """
    att = _ATTEMPT.get()
    if att is None:
        return
    if not overwrite and att.kind:
        return
    att.kind = kind
    att.status = status
    att.retry_after = retry_after
    if friendly:
        att.friendly = friendly


def classify_http(status: int, body: str = "") -> str:
    if status == 429:
        return KIND_RATE
    if status in (401, 402, 403, 404, 405):
        return KIND_CONFIG
    if status == 408:
        return KIND_TIMEOUT
    if status >= 500:
        return KIND_SERVER
    low = (body or "").lower()
    # Gemini 等对坏 key 回 400(API_KEY_INVALID),这是配置问题,不是这条文本的问题
    if status == 400 and ("api_key_invalid" in low or "api key not valid" in low):
        return KIND_CONFIG
    return KIND_REQUEST


def parse_retry_after(headers: Any) -> float | None:
    """Retry-After 只认秒数;HTTP 日期格式或缺失返回 None(用默认冷却)。headers 可能为 None。"""
    if headers is None:
        return None
    try:
        raw = headers.get("Retry-After")
    except Exception:
        return None
    if raw is None:
        return None
    try:
        return max(0.0, float(str(raw).strip()))
    except (TypeError, ValueError):
        return None


def note_http(status: int, *, body: str = "", headers: Any = None, friendly: str = "") -> None:
    note(classify_http(int(status), body), friendly=friendly, status=int(status),
         retry_after=parse_retry_after(headers) if int(status) == 429 else None)


def note_exception(exc: BaseException, host: str = "") -> None:
    """非 HTTP 状态码类的失败:代理用不了 / SSRF 拒连=配置;超时/连不上=timeout;
    其余(多为 200 但响应体坏)=server。所有 urllib 通道的异常都经这里分类,别在通道里各判各的。"""
    where = host or "嵌入接口"
    try:
        from core.outbound import OutboundBlocked, UnsupportedProxy, redact_proxy_url
    except Exception:  # pragma: no cover - core 总在
        OutboundBlocked = UnsupportedProxy = ()  # type: ignore[assignment]
        redact_proxy_url = str  # type: ignore[assignment]
    if UnsupportedProxy and isinstance(exc, UnsupportedProxy):  # 同为 ValueError 子类,须在兜底 server 之前判
        # 凭据里的代理这条出站用不了(urllib 不支持 SOCKS 等):请求根本没发出去,改代理之前不会好。
        note(KIND_CONFIG, friendly=f"向量嵌入请求没有发出去:{redact_proxy_url(exc)}")
    elif OutboundBlocked and isinstance(exc, OutboundBlocked):  # 注意它是 ValueError 子类,须先判
        note(KIND_CONFIG, friendly=(
            f"向量嵌入接口地址({where})解析到内网或保留地址,出于安全已拒绝连接。"
            f"请在「设置 → API & 模型」检查接口地址。"
        ))
    elif isinstance(exc, (TimeoutError, OSError)):  # URLError / 连接被拒 / 断连 都是 OSError 子类
        note(KIND_TIMEOUT, friendly=f"向量嵌入接口 {where} 超时或连不上,请确认地址可以访问。")
    else:
        note(KIND_SERVER, friendly=(
            f"向量嵌入接口 {where} 返回的内容不是 embeddings 格式,请确认这个地址支持 /embeddings 接口。"
        ))


# ── dispatch 侧:查询 / 记录 ─────────────────────────────────────────────────
def blocked(bkey: str, *, batch: bool) -> _Entry | None:
    """该单元是否处于冷却中。batch=True(写库路径)时只认 config / no_cred。"""
    now = _clock()
    with _LOCK:
        ent = _STATE.get(bkey)
        if ent is None or not ent.kind or ent.until <= now:
            return None
        if batch and ent.kind not in _BATCH_BLOCKING_KINDS:
            return None
        return ent


def status(bkey: str) -> dict[str, Any] | None:
    """写库循环用:该单元当前冷却信息 {kind, remaining}(未在冷却返回 None)。"""
    now = _clock()
    with _LOCK:
        ent = _STATE.get(bkey)
        if ent is None or not ent.kind or ent.until <= now:
            return None
        return {"kind": ent.kind, "remaining": ent.until - now}


def reset_dispatched() -> None:
    _DISPATCHED.set(False)


def mark_dispatched() -> None:
    _DISPATCHED.set(True)


def dispatched() -> bool:
    return _DISPATCHED.get()


def _prune_locked(now: float) -> None:
    if len(_STATE) <= _MAX_ENTRIES:
        return
    for k in [k for k, e in _STATE.items() if e.until <= now and not e.server_strikes and not e.rate_trips]:
        _STATE.pop(k, None)
    while len(_STATE) > _MAX_ENTRIES:
        _STATE.popitem(last=False)


def _set_last_error_locked(user_id: int | None, bkey: str, text: str) -> None:
    uk = int(user_id) if user_id else 0
    _LAST_ERROR[uk] = (bkey, text)
    _LAST_ERROR.move_to_end(uk)
    while len(_LAST_ERROR) > _MAX_LAST_ERRORS:
        _LAST_ERROR.popitem(last=False)


def record_success(bkey: str, *, user_id: int | None) -> None:
    uk = int(user_id) if user_id else 0
    with _LOCK:
        _STATE.pop(bkey, None)
        prev = _LAST_ERROR.get(uk)
        if prev is not None and prev[0] == bkey:
            _LAST_ERROR.pop(uk, None)


def record_failure(bkey: str, att: Attempt, *, user_id: int | None, api_id: str,
                   model: str, base_url: str) -> None:
    """按 att.kind 记失败。kind 为空(通道没写,如被测试替身替换)时不动熔断,只保守地什么都不做。"""
    kind = att.kind
    if not kind:
        return
    now = _clock()
    tripped: float = 0.0
    with _LOCK:
        if att.friendly:
            _set_last_error_locked(user_id, bkey, att.friendly)
        if kind == KIND_REQUEST:
            return
        ent = _STATE.get(bkey)
        if ent is None:
            ent = _Entry(user_id=int(user_id) if user_id else None, label=_label(api_id, model, base_url))
            _STATE[bkey] = ent
        _STATE.move_to_end(bkey)
        was_open = bool(ent.kind) and ent.until > now
        prev_kind = ent.kind if was_open else ""
        if kind == KIND_SERVER:
            ent.server_strikes += 1
            if ent.server_strikes >= _SERVER_TRIP_AFTER:
                ent.server_strikes = 0
                tripped = _SERVER_COOLDOWN
        elif kind == KIND_TIMEOUT:
            tripped = _TIMEOUT_COOLDOWN
        elif kind == KIND_RATE:
            base = att.retry_after if att.retry_after is not None else _RATE_DEFAULT
            cooldown = min(max(base, _RATE_MIN), _RATE_MAX)
            if was_open and ent.kind == KIND_RATE:
                # 冷却中又被 429(只有写库路径会在冷却中真打):顺延,不翻倍。
                # 翻倍基数(last_rate_cooldown)不能跟着降:顺延出来的剩余时长可能比上一档短,
                # 拿它当基数,下一次到期再 429 时的冷却反而回退了。
                cooldown = max(cooldown, ent.until - now)
                ent.last_rate_cooldown = max(ent.last_rate_cooldown, cooldown)
            elif ent.rate_trips and ent.last_rate_cooldown:
                # 冷却到期后又被 429:间隔翻倍,两个 worker 周期性试探也不至于持续吃掉用户配额
                cooldown = max(cooldown, min(ent.last_rate_cooldown * 2, _RATE_ESCALATE_CAP))
                ent.rate_trips += 1
                ent.last_rate_cooldown = cooldown
            else:
                ent.rate_trips = 1
                ent.last_rate_cooldown = cooldown
            tripped = cooldown
        elif kind == KIND_CONFIG:
            tripped = _PAYMENT_COOLDOWN if att.status == 402 else _CONFIG_COOLDOWN
        elif kind == KIND_NO_CRED:
            tripped = _NO_CRED_COOLDOWN
        if tripped:
            if was_open:
                # 冷却中(只有写库路径会在冷却中真打)又失败:只延长不缩短。否则还剩 570s 的限流冷却
                # 被一次写库超时改成 60s,查询路径提前恢复,接着吃「失败也计数」的中转站配额。
                ent.until = max(ent.until, now + tripped)
                if _KIND_RANK.get(kind, 0) >= _KIND_RANK.get(ent.kind, 0):
                    ent.kind = kind
            else:
                ent.kind = kind
                ent.until = now + tripped
        label = ent.label
        effective_kind = ent.kind
        effective_secs = int(ent.until - now)
        _prune_locked(now)
    # 只在「进入冷却」或冷却原因变了(如超时冷却中写库路径又撞上 429)时打一条;
    # 冷却中写库路径同类失败、或较低优先级的失败(限流中撞上超时)不重复刷
    if tripped and (not was_open or prev_kind != effective_kind):
        reason = f"HTTP {att.status}" if att.status else _KIND_LABEL.get(kind, kind)
        log.warning(
            "[embedding] 嵌入供应商 %s 失败(%s),冷却 %ds,期间向量召回改走关键词",
            label, reason, effective_secs,
        )


def last_error_for(user_id: int | None, bkey: str | None = None) -> str:
    """该用户最近一次嵌入失败的友好描述。给了 bkey 时只返回同一配置下的错误(换了配置旧错不再显示)。"""
    uk = int(user_id) if user_id else 0
    with _LOCK:
        rec = _LAST_ERROR.get(uk)
    if rec is None:
        return ""
    if bkey is not None and rec[0] != bkey:
        return ""
    return rec[1]


def reset_user(user_id: int | None) -> None:
    """清掉该用户的全部熔断单元和最近错误(保存/删除凭据、手动重建向量时调用)。

    user_id 为空(系统任务 / 平台兜底)时什么都不做:那些单元记的是平台 key 的状态,
    admin/vip 的平台兜底也落在上面;某个系统建库作业开始不代表平台 key 恢复了,
    不能顺手把它们的冷却清掉。
    """
    if not user_id:
        return
    uk = int(user_id)
    with _LOCK:
        for k in [k for k, e in _STATE.items() if e.user_id == uk]:
            _STATE.pop(k, None)
        _LAST_ERROR.pop(uk, None)


def reset_all() -> None:
    """测试用:清空全部进程内状态。"""
    with _LOCK:
        _STATE.clear()
        _LAST_ERROR.clear()
    _DISPATCHED.set(False)
