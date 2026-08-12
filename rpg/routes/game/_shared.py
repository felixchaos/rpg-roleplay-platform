"""routes.game._shared —— 拆包共享的单一 router 实例 + 跨资源族 helper。

各资源族子模块 `from ._shared import router[, _log, _client_safe_error, ...]` 后用
`@router.<verb>(...)` 注册端点;`__init__.py` import 全部子模块触发装配,再把这同一个
router 暴露给上层(app.py `from routes.game import router`)。这样装配结果与拆分前的单文件
逐端点一致(共享同一 APIRouter 实例)。

_log / _sanitize_payload / _client_safe_error(+_CLIENT_SAFE_RUNTIME_PREFIXES)/
_note_channel_health_failure 被多个子模块(new/opening/chat/saves)共用,故与 router 同居
本模块(单一真相源,避免跨子模块循环 import);测试侧 `from routes.game import _client_safe_error`
经 __init__ 门面 re-export 不变。
"""
from __future__ import annotations

import logging as _logging
import secrets as _secrets
from typing import Any

from fastapi import APIRouter

_log = _logging.getLogger(__name__)


def _sanitize_payload(obj):
    """裸控制字符兜底(与 api._deps._strip_control_chars 同款):/api/new 家族返回裸
    JSONResponse、不过 json_response 的清洗层,单独兜一层防脏存档 free text 让
    浏览器 JSON.parse 报 Invalid control character。"""
    try:
        from platform_app.api._deps import _strip_control_chars
        return _strip_control_chars(obj)
    except Exception:
        return obj

_CLIENT_SAFE_RUNTIME_PREFIXES = (
    "未找到 Vertex AI Service Account。",
    "Vertex AI 调用被拒(403)。",
)


def _client_safe_error(exc: Exception) -> str:
    """把未预期异常转成对客户端安全的泛化文案 + error_id。

    str(exc) 可能含 DB 表名/连接串、文件路径、第三方 SDK 内部细节(乃至凭据上下文),
    绝不能直透进 SSE 给玩家。原始异常带 error_id 写服务端日志,客户端只拿 id 便于排障对账。
    已知提供商错误(余额/key/限流)走 agents.provider_errors 统一分类,给可行动文案。
    """
    from agents.provider_errors import classify_provider_error, redact_secrets

    # 字母前缀:token_hex(4) 约 2% 概率全是数字(如 65969875),紧跟「余额/配额已用尽」
    # 文案会被用户误读成「本轮消耗了 6500 万 token」(生产实况:用户 @拾酒 据此问"token 消耗
    # 这么大吗")。前缀 E 让它一眼是排障标识、不是数量。日志侧同 id,对账不受影响。
    error_id = "E" + _secrets.token_hex(4)
    raw_message = str(exc).strip()
    if isinstance(exc, RuntimeError) and raw_message.startswith(_CLIENT_SAFE_RUNTIME_PREFIXES):
        _log.warning("[chat] client-safe stream error (error_id=%s): %s", error_id, raw_message)
        return f"{raw_message}\n\n如果已经上传,请重新测试凭证或切换到已配置的模型。(错误码 {error_id})"
    known = classify_provider_error(exc)
    if known:
        category, message = known
        # v1.73.5:服务端日志必须留下 provider 的原话(脱敏后)。
        # 原来这里只记 `type(exc).__name__`(如 "PermissionDeniedError")—— 客户端不回显
        # str(exc) 是对的(可能含路径/SDK 内部细节),但**日志是我们自己的**,把唯一能定位问题
        # 的那句话也丢掉,等于自断排查路。前科(星色マジック,xai/grok 403):生产日志里只有
        # "PermissionDeniedError",24h 内 200/403 交替也说不出对面到底拒了什么,只能靠对
        # api.x.ai 发无凭据探针反推「403 不是没凭据(那是 401)」。而未知异常走的 _log.exception
        # 本来就会记全文 —— 偏偏是**已分类**的提供商错误(最需要原话的那类)被吞了,不对称。
        # redact_secrets 按形状打码 key,不枚举供应商前缀。
        _log.warning("[chat] client-safe %s stream error (error_id=%s): %s | provider: %s",
                     category, error_id, type(exc).__name__, redact_secrets(exc))
        return f"{message}(错误码 {error_id})"
    _log.exception("[chat] unhandled stream error (error_id=%s)", error_id)
    return f"本轮处理出错,请重试(错误码 {error_id})"


def _note_channel_health_failure(exc: Exception, api_id: str, api_user: dict[str, Any] | None) -> None:
    """渠道健康门控(韧性战役):upstream/ratelimit 分类的失败被动记进 model_probe 滑动窗口。

    只认 classify_provider_error 分类为 "upstream"(5xx/网关)或 "ratelimit" 的失败——
    这两类是"渠道本身有问题"(供应商/中转站过载或限流),而非 auth/balance/context 等
    用户自己能一次性解决的问题,不该拖累渠道的健康标记。失败分类/记录本身绝不能让
    异常冒泡打断 SSE 错误面,全程 best-effort。
    """
    if not api_id:
        return
    try:
        from agents.provider_errors import classify_provider_error
        known = classify_provider_error(exc)
        if not known:
            return
        category = known[0]
        if category not in ("upstream", "ratelimit"):
            return
        import model_probe
        model_probe.note_channel_failure(api_id, user_id=(api_user or {}).get("id"))
    except Exception:
        pass


router = APIRouter()
