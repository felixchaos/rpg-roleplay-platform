"""platform_app.knowledge.embedding._gemini — Gemini 原生 embedContent 通道 + 地区封禁自愈。

拆包前住在单文件 embedding.py。地区封禁自愈的可变全局 _GEO_BAN_CACHE 与其读写方
(_geo_ban_mark / _geo_ban_active)同居于此;_embed_via_gemini 命中封禁特征后就地标记。
纯机械搬家,行为零变化。
"""
from __future__ import annotations

import os
import time
from typing import Any

from ._base import EMBED_DIM, log

# ---------------------------------------------------------------------------
# 地区封禁自愈(2026-07-05 生产实证):Google AI Studio 从 2026-07-04 起把服务器
# 机房 IP 整段封禁(400 "User location is not supported" / FAILED_PRECONDITION,
# 与 commit 7cc0b2d28 model_probe.py 识别的同类封禁)。每次检索(_search._embed_query
# 每 query 都调一次)先撞这个必然失败的 gemini 原生直连通道再回退 Vertex,白花
# ~300ms + 7 行日志噪声。地区封禁不会几分钟内自愈(是 Google 侧机房 IP 段封禁,非
# 瞬时抖动),命中特征后进程内标记该通道不可用,TTL 拉长到 1 小时,后续调用直接跳过
# 直连、走 Vertex genai SDK 兜底 —— **不改变「所有通道都失败」时的最终报错行为**,
# 只是跳过已知必然失败的一步。
_GEO_BAN_CACHE: dict[str, float] = {}  # channel key → 标记时刻(time.time())
_GEO_BAN_TTL = 3600.0  # 1 小时;地区封禁是机房 IP 段级别,不会分钟级自愈


def _is_geo_ban_error(err_str: str) -> bool:
    """该错误消息是否命中 Google 地区/机房 IP 封禁特征。

    与 model_probe._classify_probe_error 同口径:400 FAILED_PRECONDITION +
    "User location is not supported"。字符串层面判断,不依赖具体异常类型
    (urllib.error.HTTPError / requests 都能命中)。
    """
    low = (err_str or "").lower()
    return (
        "location is not supported" in low
        or "user location" in low
        or "failed_precondition" in low
        or "failed precondition" in low
    )


def _geo_ban_mark(channel: str, clock: Any = time.time) -> None:
    """标记某 embedding 直连通道当前处于地区封禁状态(进程内,TTL=_GEO_BAN_TTL)。

    clock: 可注入的时间函数,默认 time.time;测试传假 clock 验证 TTL 边界,避免真 sleep。
    """
    _GEO_BAN_CACHE[channel] = clock()
    log.warning(
        "[embedding] channel=%s 命中地区封禁特征(User location is not supported),"
        "进程内标记不可用 %ds,期间自动跳过直连、走下一通道兜底",
        channel, int(_GEO_BAN_TTL),
    )


def _geo_ban_active(channel: str, clock: Any = time.time) -> bool:
    """该通道是否仍在地区封禁标记的 TTL 窗口内(命中即跳过,不再发请求)。

    clock: 同 _geo_ban_mark,测试可注入假时钟。
    """
    ts = _GEO_BAN_CACHE.get(channel)
    if ts is None:
        return False
    if clock() - ts > _GEO_BAN_TTL:
        _GEO_BAN_CACHE.pop(channel, None)
        return False
    return True


def _native_gemini_embed_model(model: str) -> str:
    """Gemini OpenAI-compatible /embeddings hits batchEmbed quota; native uses embedContent."""
    model = (model or "").strip()
    if model in {"", "text-embedding-004"}:
        return os.environ.get("GEMINI_EMBED_MODEL", "gemini-embedding-001")
    return model


# _embed_via_gemini 是直连 generativelanguage.googleapis.com 的原生 REST 通道,
# 地区封禁按这个 key 记录(与 _embed_via_vertex 内部的「先试原生再退 SDK」共用同一标记)。
_GEO_BAN_CHANNEL_GEMINI_NATIVE = "gemini_native_embedcontent"


def _embed_via_gemini(model: str, api_key: str, texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]] | None:
    """Gemini native embedContent API, avoiding OpenAI-compatible batchEmbed quota.

    注意:此处对多文本逐条串行调用 embedContent(v1beta)。
    Gemini 也提供 batchEmbedContents 批量端点,可一次请求处理多条文本,
    但该端点与 outputDimensionality 参数的兼容性在不同模型版本下不稳定,
    且 API 配额限制与 embedContent 共享同一个池——串行代码逻辑更简单可靠。
    如 texts 条数大、性能成为瓶颈,可考虑改用 batchEmbedContents 合并请求。

    地区封禁自愈:命中特征后进程内标记 _GEO_BAN_CHANNEL_GEMINI_NATIVE 不可用
    (TTL=_GEO_BAN_TTL),入口先查缓存,标记生效时直接跳过网络调用返 None
    (由上层继续尝试下一通道),避免每次检索都白撞一次注定失败的直连。
    """
    import json as _json
    import urllib.error
    import urllib.request

    from core.outbound import safe_urlopen  # SSRF: 不跟随重定向 + use-time 重解析 pin IP
    from core.outbound_ua import outbound_user_agent

    if not api_key:
        log.warning("[embedding] gemini api_id but no api_key")
        return None

    if _geo_ban_active(_GEO_BAN_CHANNEL_GEMINI_NATIVE):
        log.debug("[embedding] gemini_native 仍在地区封禁 TTL 窗口内,跳过直连")
        return None

    effective_model = _native_gemini_embed_model(model)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{effective_model}:embedContent?key={api_key}"
    out: list[list[float]] = []
    try:
        for text in texts:
            payload = _json.dumps({
                "content": {"parts": [{"text": text}]},
                "taskType": task_type,
                "outputDimensionality": EMBED_DIM,
            }).encode()
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json", "User-Agent": outbound_user_agent()},
                method="POST",
            )
            with safe_urlopen(req, timeout=60) as resp:
                data = _json.loads(resp.read())
            values = data.get("embedding", {}).get("values") or []
            if len(values) != EMBED_DIM:
                log.warning("[embedding] gemini embed returned dim=%s expected=%s", len(values), EMBED_DIM)
                return None
            out.append(list(values))
        return out
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        log.warning("[embedding] gemini embed failed: %s %s", e.code, body[:200])
        if _is_geo_ban_error(body) or _is_geo_ban_error(str(e)):
            _geo_ban_mark(_GEO_BAN_CHANNEL_GEMINI_NATIVE)
        return None
    except Exception as e:
        log.warning("[embedding] gemini embed failed: %s", e)
        if _is_geo_ban_error(str(e)):
            _geo_ban_mark(_GEO_BAN_CHANNEL_GEMINI_NATIVE)
        return None
