"""
model_probe.py — API 探测：远端模型列表 + 可用性 + 定价

提供三类能力（README 没要求但实战必备）：

1. list_remote_models(api_id)
   调用供应商 SDK 拉取真实可用模型清单，对比本地 catalog 标记 missing/extra。

2. probe_availability(api_id, model_id)
   发一条最小（1-token）请求验证 key + model 当前可用，记录延迟。

3. get_pricing(api_id, model_id)
   内置常用模型定价表（输入/输出 / 百万 token，USD）。
   找不到时返回 None；用户可在 model_catalog 里手动覆盖 model.pricing。

设计原则：
- 不抓 HTML / 不爬官网。定价用静态表 + catalog 覆盖。
- 探测调用走真实 SDK，结果缓存 60 秒避免 DDoS 自己。
- 失败不抛异常，统一返回结构化错误。
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

BASE = Path(__file__).parent


# ══════════════════════════════════════════════════════════════════════
#  价格表（USD per million tokens）
# ══════════════════════════════════════════════════════════════════════
# 来源：各家官方公开定价（2026-05-25 校准 · task 57）。准确性责任在调用方，可被 catalog 覆盖。
# 结构：{api_kind: {model_real_name: {"input": X, "output": Y, "context": Z, "notes": ""}}}
#
# 主要变更（vs 旧表 2026-05 早期校准）：
# - Anthropic: 已是 4.x 体系，Opus 4.7 (Apr 16, 2026) 为当前 frontier
# - OpenAI: GPT-5.5 (May 5, 2026) 替代 5.3 成为 default；保留 5.0/4.1/4o 兜底
# - Gemini: 3.5 Flash (May 19, 2026) 实际价 $1.50/$9.00（之前表里 $0.3/$2.5 是估算误差）；3.1 Pro 是 prev flagship
# - Qwen: 3.7-Max (May 21, 2026) 新 flagship $2.50/$7.50；3.6 Flash $0.19/$1.13
# - DeepSeek: V4-Pro (Apr 24, 2026) 1M context $1.74/$3.48
_STATIC_PRICING: dict[str, dict[str, dict[str, Any]]] = {
    "anthropic": {
        # task 57: 2026-05-25 校准
        "claude-opus-4-7":     {"input": 15.0, "output": 75.0, "context": 200000, "notes": "Opus 4.7 · 2026-04-16 当前 frontier"},
        "claude-opus-4-6":     {"input": 15.0, "output": 75.0, "context": 200000, "notes": "Opus 4.6"},
        "claude-opus-4-5":     {"input": 15.0, "output": 75.0, "context": 200000, "notes": "Opus 4.5"},
        "claude-sonnet-4-6":   {"input": 3.0,  "output": 15.0, "context": 200000, "notes": "Sonnet 4.6 · 2026-02"},
        "claude-sonnet-4-5":   {"input": 3.0,  "output": 15.0, "context": 200000},
        "claude-haiku-4-5":    {"input": 1.0,  "output": 5.0,  "context": 200000, "notes": "Haiku 4.5 · 2025-10"},
        "claude-3-5-sonnet":   {"input": 3.0,  "output": 15.0, "context": 200000},
        "claude-3-5-haiku":    {"input": 0.8,  "output": 4.0,  "context": 200000},
    },
    "vertex_ai": {
        # task 57: 2026-05-25 校准 - Gemini 3.5 Flash 是 2026-05-19 当前默认/最便宜旗舰
        "gemini-3.5-flash":    {"input": 1.50,  "output": 9.00,  "context": 1000000, "notes": "Flash · 2026-05-19 当前默认"},
        "gemini-3.1-pro":      {"input": 2.00,  "output": 12.00, "context": 1000000, "notes": "3.1 Pro · prev flagship; >200K 时 $4/$18"},
        "gemini-3-flash":      {"input": 1.50,  "output": 9.00,  "context": 1000000, "notes": "别名指向 3.5 Flash"},
        "gemini-3-pro":        {"input": 1.25,  "output": 10.0,  "context": 2000000, "notes": "3 Pro · 旧 flagship"},
        "gemini-2.5-flash":    {"input": 0.075, "output": 0.3,   "context": 1000000},
        "gemini-2.5-pro":      {"input": 1.25,  "output": 5.0,   "context": 2000000},
        "gemini-2.0-flash":    {"input": 0.075, "output": 0.3,   "context": 1000000},
    },
    "openai": {
        # task 57: GPT-5.5 (2026-05-05) 替代 5.3-instant 成为 ChatGPT 默认
        # 定价未官方公开，按 GPT-5.5 Instant 略高于 5.x 估算（待官方 API 价更新）
        "gpt-5.5":             {"input": 2.5,  "output": 10.0, "context": 400000, "notes": "GPT-5.5 · 2026-05-05 默认"},
        "gpt-5.5-instant":     {"input": 1.25, "output": 5.0,  "context": 400000, "notes": "GPT-5.5 Instant · 低延迟"},
        "gpt-5.5-pro":         {"input": 5.0,  "output": 20.0, "context": 400000, "notes": "GPT-5.5 Pro · 付费"},
        "gpt-5.5-thinking":    {"input": 5.0,  "output": 20.0, "context": 400000, "notes": "GPT-5.5 Thinking · 推理"},
        "gpt-5":               {"input": 2.0,  "output": 8.0,  "context": 400000, "notes": "GPT-5 · 上一代"},
        "gpt-4.1":             {"input": 2.0,  "output": 8.0,  "context": 1000000},
        "gpt-4o":              {"input": 2.5,  "output": 10.0, "context": 128000},
        "gpt-4o-mini":         {"input": 0.15, "output": 0.6,  "context": 128000},
        "gpt-4-turbo":         {"input": 10.0, "output": 30.0, "context": 128000},
        "o1-mini":             {"input": 1.1,  "output": 4.4,  "context": 128000},
    },
    "openrouter": {
        # OpenRouter 聚合，价格 = 上游 + 5% 平台费。task 57 同步刷新代表项。
        "anthropic/claude-opus-4-7":     {"input": 15.75, "output": 78.75, "context": 200000},
        "anthropic/claude-sonnet-4-6":   {"input": 3.15,  "output": 15.75, "context": 200000},
        "openai/gpt-5.5":                {"input": 2.625, "output": 10.5,  "context": 400000},
        "openai/gpt-4o":                 {"input": 2.625, "output": 10.5,  "context": 128000},
        "google/gemini-3.5-flash":       {"input": 1.575, "output": 9.45,  "context": 1000000},
        "google/gemini-3.1-pro":         {"input": 2.10,  "output": 12.60, "context": 1000000},
        "google/gemini-2.5-pro":         {"input": 1.31,  "output": 5.25,  "context": 2000000},
    },
    # DeepSeek 直供平台(api.deepseek.com),区别于 siliconflow 转售
    "deepseek": {
        "deepseek-v4-pro":   {"input": 0.30, "output": 1.20, "context": 1000000, "notes": "DeepSeek V4-Pro 官方"},
        "deepseek-v4-flash": {"input": 0.10, "output": 0.40, "context": 1000000, "notes": "DeepSeek V4-Flash 官方"},
        "deepseek-v3":       {"input": 0.27, "output": 1.10, "context": 64000,  "notes": "V3 旧版"},
        # 兼容驼峰大小写写法
        "DeepSeek-V4-Flash": {"input": 0.10, "output": 0.40, "context": 1000000},
        "DeepSeek-V4-Pro":   {"input": 0.30, "output": 1.20, "context": 1000000},
    },
    "siliconflow": {
        # task 57: DeepSeek V4 系列（2026-04-24 发布）
        "deepseek-ai/DeepSeek-V4-Pro":   {"input": 1.74, "output": 3.48, "context": 1000000, "notes": "DeepSeek V4-Pro · 2026-04-24 · 1.6T 参数"},
        "deepseek-ai/DeepSeek-V4-Flash": {"input": 0.30, "output": 1.20, "context": 1000000, "notes": "DeepSeek V4-Flash · 廉价版"},
        "deepseek-ai/DeepSeek-V3":       {"input": 0.27, "output": 1.10, "context": 64000, "notes": "V3 · 旧版"},
        "Qwen/Qwen3.7-Max":              {"input": 2.50, "output": 7.50, "context": 1000000, "notes": "Qwen 3.7-Max · 2026-05-21"},
        "Qwen/Qwen3.6-Flash":            {"input": 0.19, "output": 1.13, "context": 131072, "notes": "Qwen 3.6 Flash"},
        "Qwen/Qwen2.5-72B-Instruct":     {"input": 0.55, "output": 1.65, "context": 128000},
    },
    "minimax": {
        "MiniMax-M1":      {"input": 0.55,  "output": 2.20, "context": 1000000, "notes": "约合 RMB ¥4/¥16"},
        "abab6.5s-chat":   {"input": 0.14,  "output": 0.14, "context": 245760},
    },
    "dashscope": {
        # task 57: 阿里云 Model Studio 直供 Qwen 3.7 / 3.6 系列
        "qwen3.7-max":     {"input": 2.50,  "output": 7.50, "context": 1000000, "notes": "Qwen 3.7-Max · 2026-05-21 旗舰"},
        "qwen3.6-flash":   {"input": 0.19,  "output": 1.13, "context": 131072, "notes": "Qwen 3.6 Flash"},
        "qwen-max":        {"input": 1.40,  "output": 5.6,  "context": 32000,  "notes": "旧 Qwen Max · 约合 RMB ¥10/¥40"},
        "qwen-plus":       {"input": 0.11,  "output": 0.28, "context": 131072},
        "qwen-turbo":      {"input": 0.04,  "output": 0.08, "context": 1000000},
    },
    "hunyuan": {
        "hunyuan-turbos-latest": {"input": 0.11,  "output": 0.32, "context": 32000, "notes": "约合 RMB ¥0.8/¥2.3"},
        "hunyuan-large":         {"input": 0.55,  "output": 1.65, "context": 28000},
    },
    "doubao": {
        "doubao-1-5-pro-32k-250115":  {"input": 0.11, "output": 0.28, "context": 32000,  "notes": "约合 RMB ¥0.8/¥2"},
        "doubao-1-5-lite-32k-250115": {"input": 0.04, "output": 0.08, "context": 32000},
    },
    "xiaomi_mimo": {
        # 暂未开放公共定价
    },
}


def get_pricing(api_id_or_kind: str, model_real_name: str, catalog_override: dict | None = None) -> dict[str, Any] | None:
    """优先 catalog 中 model.pricing 字段，回退静态表。

    第一个参数既可以传 api_id (siliconflow/dashscope/...) 也可以传 kind (openai_compat/...)。
    静态价格表按 api_id 分组（不同 provider 价格不同，即使都是 openai_compat）。
    """
    if catalog_override and isinstance(catalog_override, dict):
        return {**catalog_override, "source": "catalog", "unit": "USD per million tokens"}
    table = _STATIC_PRICING.get(api_id_or_kind or "", {})
    pricing = table.get(model_real_name)
    if pricing:
        return {**pricing, "source": "static", "unit": "USD per million tokens"}
    return None


# ══════════════════════════════════════════════════════════════════════
#  远端模型列表嗅探
# ══════════════════════════════════════════════════════════════════════
_LIST_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}  # api_id -> (ts, models)

# task 42: 每个 (api_id, real_name) 最后一次 probe_availability 的结果。
# 让 GET /api/models 能 inject health 字段,UI 显示模型可达性。
# value: {"status": "ok|err", "latency_ms": int, "checked_at": float, "error": str}
_HEALTH_CACHE: dict[tuple[str, str], dict[str, Any]] = {}
# P1-3: _HEALTH_CACHE TTL — 超过 300s 的条目视为过期,重新探测
_HEALTH_CACHE_TTL = 300.0


def _health_cache_get(api_id: str, real_name: str) -> dict[str, Any] | None:
    """读 cache;超过 TTL 返回 None(触发重新探测)。"""
    entry = _HEALTH_CACHE.get((api_id, real_name))
    if entry is None:
        return None
    if time.time() - entry.get("_cached_at", 0) > _HEALTH_CACHE_TTL:
        return None
    return entry


def _health_cache_set(api_id: str, real_name: str, val: dict[str, Any]) -> None:
    """写 cache,自动附加时间戳。"""
    _HEALTH_CACHE[(api_id, real_name)] = {**val, "_cached_at": time.time()}


def get_health(api_id: str, real_name: str) -> dict[str, Any] | None:
    """读最近一次 probe 结果。前端 /api/models 用它注入 health 字段。"""
    return _health_cache_get(api_id, real_name)


def all_health() -> dict[str, dict[str, Any]]:
    """全量返回当前 cache 状态,UI 调试用。"""
    return {f"{a}::{m}": v for (a, m), v in _HEALTH_CACHE.items()}
_CACHE_TTL = 60.0


def _require_user_credential() -> bool:
    """服务器模式强制要求 user-scoped 凭证；本地匿名允许走环境变量。"""
    from core.config import is_server_mode as _is_server_mode
    from core.config import require_auth as _require_auth
    return _require_auth() or _is_server_mode()


def _has_user_credential(user_id: int | None, api_id: str) -> bool:
    """user_api_credentials 表里是否有此 user 的此 provider 凭证。"""
    if not user_id:
        return False
    try:
        from platform_app.user_credentials import get_credential
        cred = get_credential(int(user_id), api_id)
        return bool(cred and cred.get("key"))
    except Exception:
        return False


def list_remote_models(
    api_id: str,
    force_refresh: bool = False,
    user_id: int | None = None,
    api_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """从供应商 SDK 拉取真实可用模型清单。

    安全：服务器模式下必须传 user_id，且该 user 必须有 user_api_credentials.api_id 凭证。
    否则禁止调用，防止用服务端凭证（vertex_sa.json / EMBED_API_KEY）替用户付费。

    api_override: 显式 provider 元数据(kind/base_url 等)。用于用户自建中转站——
    这些 provider 不在全局菜单里(绝不写全局,避免跨用户泄露),由调用方从用户
    凭证合成后传入。
    """
    # 服务器模式强制：必须有 user-scoped 凭证
    if _require_user_credential() and not _has_user_credential(user_id, api_id):
        return {"ok": False, "error": "需要在「个人主页 → API 凭证」中配置该 provider 的 key", "models": []}

    cache_key = f"{user_id or 0}::{api_id}"
    if not force_refresh:
        cached = _LIST_CACHE.get(cache_key)
        if cached and (time.monotonic() - cached[0]) < _CACHE_TTL:
            return {"ok": True, "models": cached[1], "cached": True}

    from model_registry import find_api, load_model_catalog
    if api_override:
        api = {**api_override, "id": api_override.get("id") or api_id}
    else:
        catalog = load_model_catalog()
        api = find_api(catalog, api_id)
    if not api:
        return {"ok": False, "error": f"api_id 不存在: {api_id}", "models": []}

    kind = api.get("kind") or api_id
    try:
        if kind == "vertex_ai":
            # 服务器模式：只有用户上传了 BYOK SA 才允许探测；否则拒绝（避免烧服务器 SA）
            if _require_user_credential():
                from core.vertex_sa import has_user_sa
                if not has_user_sa(user_id):
                    return {
                        "ok": False,
                        "error": "服务器模式下需先在「设置 → API & 模型 → Agent Platform」上传 Service Account JSON",
                        "models": [],
                    }
            models = _list_vertex_models(api, user_id=user_id)
        elif kind == "anthropic":
            models = _list_anthropic_models(api, user_id=user_id)
        elif kind in {"openai", "openai_compat"}:
            models = _list_openai_compat_models(api, user_id=user_id)
        else:
            return {"ok": False, "error": f"不支持的 provider kind: {kind}", "models": []}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "models": []}

    _LIST_CACHE[cache_key] = (time.monotonic(), models)
    return {"ok": True, "models": models, "cached": False}


def _list_vertex_models(api: dict[str, Any], user_id: int | None = None) -> list[dict[str, Any]]:
    """列出 Vertex 可用 Gemini 模型。user_id 非 None 时优先使用用户 BYOK SA。"""
    from google import genai

    from core.vertex_sa import load_sa_credentials

    creds, project_id = load_sa_credentials(user_id)
    if creds is None or project_id is None:
        raise RuntimeError(
            "未找到 Vertex Service Account。"
            "请在「设置 → Agent Platform」上传自己的 SA JSON。"
        )
    client = genai.Client(vertexai=True, project=project_id, location="global", credentials=creds)
    models = []
    for m in client.models.list():
        full = getattr(m, "name", "") or ""
        if "gemini" not in full.lower():
            continue
        short = full.replace("publishers/google/models/", "")
        models.append({
            "id": short,
            "real_name": short,
            "full_path": full,
            "display_name": short.replace("-", " ").title(),
        })
    return models


def _resolve_provider_key(api: dict[str, Any], user_id: int | None) -> str:
    """统一取 key：优先 user_api_credentials，本地匿名才回退 env。"""
    api_id = api.get("id") or api.get("kind") or ""
    if user_id:
        try:
            from platform_app.user_credentials import get_credential
            cred = get_credential(int(user_id), api_id)
            if cred and cred.get("key"):
                return cred["key"]
        except Exception:
            pass
    # 服务器模式下不允许回退到 env
    if _require_user_credential():
        raise RuntimeError(f"未在「个人主页 → API 凭证」配置 {api_id} 的 key")
    env_name = api.get("credential_env") or ""
    if env_name:
        env_key = os.environ.get(env_name)
        if env_key:
            return env_key
    raise RuntimeError(f"找不到 {api_id} 的 API key（用户凭证未配置且环境变量未设）")


def _list_anthropic_models(api: dict[str, Any], user_id: int | None = None) -> list[dict[str, Any]]:
    from anthropic import Anthropic
    key = _resolve_provider_key(api, user_id)
    client = Anthropic(api_key=key)
    models = []
    for m in client.models.list():
        models.append({
            "id": m.id,
            "real_name": m.id,
            "display_name": getattr(m, "display_name", m.id),
            "created_at": str(getattr(m, "created_at", "")),
        })
    return models


def _list_openai_compat_models(api: dict[str, Any], user_id: int | None = None) -> list[dict[str, Any]]:
    """通用 OpenAI 兼容拉模型清单，适用于 OpenAI / OpenRouter / 硅基 / 阿里 / 腾讯 / 火山 等。"""
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("openai SDK 未安装") from exc
    key = _resolve_provider_key(api, user_id)
    base_url = api.get("base_url") or None
    kwargs: dict[str, Any] = {"api_key": key}
    if base_url:
        kwargs["base_url"] = base_url
    client = OpenAI(**kwargs)
    models = []
    try:
        data = client.models.list().data
    except Exception as exc:
        # 某些 provider（如部分 OpenAI 兼容厂商）可能不支持 /v1/models，把这种情况显式标出
        raise RuntimeError(f"provider 拒绝列模型（可能不支持 /v1/models）: {exc}") from exc
    for m in data:
        mid = getattr(m, "id", "") or getattr(m, "name", "")
        if mid:
            models.append({"id": mid, "real_name": mid, "display_name": mid})
    return models


# ══════════════════════════════════════════════════════════════════════
#  本地 catalog vs 远端 diff
# ══════════════════════════════════════════════════════════════════════
def diff_catalog(api_id: str, user_id: int | None = None) -> dict[str, Any]:
    """对比本地 catalog 和远端真实可用模型，返回 missing / extra / matching。"""
    remote = list_remote_models(api_id, user_id=user_id)
    if not remote["ok"]:
        return {"ok": False, "error": remote.get("error"), "api_id": api_id}
    from model_registry import find_api, load_model_catalog
    api = find_api(load_model_catalog(), api_id)
    if not api:
        return {"ok": False, "error": f"api_id 不存在: {api_id}"}

    local_ids = {m.get("real_name") for m in api.get("models", [])}
    remote_ids = {m["real_name"] for m in remote["models"]}

    return {
        "ok": True,
        "api_id": api_id,
        "local_only": sorted(local_ids - remote_ids),   # catalog 里有但远端没有（可能下线）
        "remote_only": sorted(remote_ids - local_ids),  # 远端有但 catalog 没注册
        "matching": sorted(local_ids & remote_ids),
        "remote_total": len(remote_ids),
        "local_total": len(local_ids),
    }


# ══════════════════════════════════════════════════════════════════════
#  可用性嗅探（发一条最小请求）
# ══════════════════════════════════════════════════════════════════════

# status_detail 枚举：
#   ok          — 探测成功，模型可用
#   degraded    — 探测成功但延迟高 / 响应异常（预留，暂未触发）
#   key_expired — HTTP 401，API key 失效或未授权
#   forbidden   — HTTP 403，API key 无权限访问该模型
#   err         — 5xx / 网络错误 / timeout / 其他未知错误
#   untested    — 从未探测过（健康缓存中的初始值）

def _classify_probe_error(exc: Exception, err_str: str) -> str:
    """把异常分类为 status_detail 枚举值。

    各 SDK 的 HTTP 错误报法不同，统一按字符串匹配兜底。
    """
    err_lower = err_str.lower()
    # Anthropic SDK: anthropic.AuthenticationError (status_code=401)
    # OpenAI SDK: openai.AuthenticationError
    # httpx: HTTPStatusError with status_code
    cls_name = type(exc).__name__.lower()

    # 优先检查异常类名
    if "authentication" in cls_name or "unauthorized" in cls_name:
        return "key_expired"
    if "permission" in cls_name or "forbidden" in cls_name:
        return "forbidden"

    # 再检查 status_code 属性（openai/anthropic SDK 都有）
    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        # httpx.HTTPStatusError.response.status_code
        resp = getattr(exc, "response", None)
        if resp is not None:
            status_code = getattr(resp, "status_code", None)
    if status_code is not None:
        if status_code == 401:
            return "key_expired"
        if status_code == 403:
            return "forbidden"
        if isinstance(status_code, int) and 500 <= status_code < 600:
            return "err"

    # 字符串关键词兜底
    if "401" in err_str or "authentication" in err_lower or "invalid api key" in err_lower or "unauthorized" in err_lower:
        return "key_expired"
    if "403" in err_str or "forbidden" in err_lower or "permission denied" in err_lower:
        return "forbidden"
    if any(kw in err_lower for kw in ("timeout", "timed out", "connection", "network", "unreachable")):
        return "err"

    return "err"


def _probe_error_message(status_detail: str) -> str:
    """status_detail → 用户可读说明（中文）。"""
    return {
        "key_expired": "API key 已失效或未授权，请在「个人主页 → API 凭证」更新密钥",
        "forbidden": "API key 无权限访问该模型，请检查账号权限或模型授权",
        "err": "探测失败（供应商故障或网络不可达），可稍后重试",
        "degraded": "模型响应异常，功能可能受限",
        "ok": "",
        "untested": "尚未探测",
    }.get(status_detail, "未知错误")


def probe_availability(api_id: str, model_real_name: str | None = None, timeout_sec: int = 15, user_id: int | None = None) -> dict[str, Any]:
    # 服务器模式强制：必须有 user-scoped 凭证才能真实发请求（避免烧服务端凭证）
    if _require_user_credential():
        # vertex_ai BYOK 存在 "AgentPlatform" 这个 api_id 下，需要特殊处理
        from model_registry import find_api
        from model_registry import load_model_catalog as _lmc
        _kind = (find_api(_lmc(), api_id) or {}).get("kind", api_id)
        if _kind == "vertex_ai":
            from core.vertex_sa import has_user_sa
            if not has_user_sa(user_id):
                return {
                    "ok": False,
                    "api_id": api_id,
                    "latency_ms": 0,
                    "error": "服务器模式下需先在「设置 → Agent Platform」上传 Service Account JSON 才能探测 Vertex AI",
                }
        elif not _has_user_credential(user_id, api_id):
            return {
                "ok": False,
                "api_id": api_id,
                "latency_ms": 0,
                "error": "需要在「个人主页 → API 凭证」中配置该 provider 的 key 才能发探测请求",
            }
    """发一条最小请求验证 (api_id, model) 是否真的能调用。

    Returns:
        {
          "ok": True/False,
          "latency_ms": int,
          "response_text": str (first 80 chars),
          "model_used": "...",
          "error": "..." (if failed),
        }
    """
    from model_registry import find_api, load_model_catalog

    catalog = load_model_catalog()
    api = find_api(catalog, api_id)
    base_err = {"api_id": api_id, "latency_ms": 0}
    if not api:
        return {"ok": False, "error": f"api_id 不存在: {api_id}", **base_err}
    if not api.get("enabled"):
        return {"ok": False, "error": "API 未启用（请在 catalog 中 enable，并确认 credential_env 已设置）", **base_err}

    if not model_real_name:
        models = api.get("models", [])
        if not models:
            return {"ok": False, "error": "该 API 没有注册任何 model", **base_err}
        model_real_name = models[0].get("real_name") or models[0].get("id")

    start = time.monotonic()
    try:
        from agents.gm import GameMaster
        # 强制按 user 取 key；服务器模式下 user_id=None 会导致 backend 取不到 key 抛错
        gm = GameMaster(api_id=api_id, model=model_real_name, user_id=user_id)
        text = gm._backend.call(
            system="只回复一个字符：1",
            messages=[{"role": "user", "content": "1"}],
            max_tokens=8,
        )
        latency = int((time.monotonic() - start) * 1000)
        result = {
            "ok": True,
            "status_detail": "ok",
            "latency_ms": latency,
            "response_text": (text or "")[:80],
            "model_used": model_real_name,
            "api_id": api_id,
        }
        # task 42: 写 health cache 让 /api/models 能 surface 状态
        _health_cache_set(api_id, model_real_name, {
            "status": "ok", "status_detail": "ok",
            "latency_ms": latency,
            "checked_at": time.time(), "error": "",
        })
        return result
    except Exception as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        err = str(exc)[:200]
        status_detail = _classify_probe_error(exc, err)
        # 兼容旧 status 字段：key_expired/forbidden 也标 status="err"
        _health_cache_set(api_id, model_real_name, {
            "status": "err", "status_detail": status_detail,
            "latency_ms": latency_ms,
            "checked_at": time.time(), "error": err,
        })
        return {
            "ok": False,
            "status_detail": status_detail,
            "error": err,
            "error_detail": _probe_error_message(status_detail),
            "latency_ms": latency_ms,
            "model_used": model_real_name,
            "api_id": api_id,
        }


# ══════════════════════════════════════════════════════════════════════
#  综合 API 健康报告
# ══════════════════════════════════════════════════════════════════════
def full_report(api_id: str, probe_model: bool = False, user_id: int | None = None) -> dict[str, Any]:
    """一次性返回：模型列表 + diff + 定价 + 可选可用性"""
    from model_registry import find_api, load_model_catalog
    catalog = load_model_catalog()
    api = find_api(catalog, api_id)
    if not api:
        return {"ok": False, "error": f"api_id 不存在: {api_id}"}
    kind = api.get("kind") or api_id

    report = {
        "ok": True,
        "api_id": api_id,
        "kind": kind,
        "enabled": bool(api.get("enabled")),
        "credential_present": _credential_present(api),
        "local_catalog": [
            {
                "id": m.get("id"),
                "real_name": m.get("real_name"),
                "enabled": m.get("enabled"),
                "pricing": (
                    get_pricing(api_id, m.get("real_name"), m.get("pricing"))
                    or get_pricing(kind, m.get("real_name"))
                ),
                "capabilities": describe_capabilities(get_capabilities(api_id, m.get("real_name"), m.get("capabilities"))),
            }
            for m in api.get("models", [])
        ],
    }

    diff = diff_catalog(api_id, user_id=user_id)
    if diff.get("ok"):
        report["remote_models_summary"] = {
            "remote_total": diff["remote_total"],
            "local_total": diff["local_total"],
            "missing_from_catalog": diff["remote_only"][:20],
            "stale_in_catalog": diff["local_only"],
        }
    else:
        report["remote_models_error"] = diff.get("error", "")

    if probe_model:
        report["availability"] = probe_availability(api_id, user_id=user_id)

    return report


# ══════════════════════════════════════════════════════════════════════
#  模型能力查询（capabilities）
# ══════════════════════════════════════════════════════════════════════
# 能力标签词典（前端可用作筛选）
CAPABILITY_LABELS = {
    "text":         "文本生成",
    "streaming":    "流式输出",
    "image_input":  "视觉输入",
    "audio_input":  "音频输入",
    "video_input":  "视频输入",
    "file_input":   "文件附件",
    "tools":        "Function Calling",
    "json_mode":    "JSON 结构化输出",
    "image_gen":    "图像生成",
    "audio_gen":    "音频生成",
    "reasoning":    "深度思考",
    "computer_use": "电脑控制",
    "code_exec":    "代码执行",
    "web_search":   "联网搜索",
}

# 模型默认能力（按 real_name 前缀匹配，精确名优先）。task 57 校准。
_CAPABILITY_DEFAULTS: dict[str, dict[str, list[str]]] = {
    "anthropic": {
        # task 57: 4.x 系列都支持 computer_use（agentic capability）
        "claude-opus-4-7":   ["text", "streaming", "image_input", "file_input", "tools", "json_mode", "reasoning", "computer_use", "code_exec"],
        "claude-opus-4-6":   ["text", "streaming", "image_input", "file_input", "tools", "json_mode", "reasoning", "computer_use"],
        "claude-opus-4-5":   ["text", "streaming", "image_input", "file_input", "tools", "json_mode", "reasoning"],
        "claude-sonnet-4-6": ["text", "streaming", "image_input", "file_input", "tools", "json_mode", "reasoning", "computer_use"],
        "claude-sonnet-4-5": ["text", "streaming", "image_input", "file_input", "tools", "json_mode"],
        "claude-haiku-4-5":  ["text", "streaming", "image_input", "tools", "json_mode"],
        "claude-3-5":        ["text", "streaming", "image_input", "file_input", "tools", "json_mode"],
    },
    "vertex_ai": {
        # task 57: 3.5 Flash + 3.1 Pro 是新主力
        "gemini-3.5-flash": ["text", "streaming", "image_input", "audio_input", "file_input", "tools", "json_mode", "reasoning"],
        "gemini-3.1-pro":   ["text", "streaming", "image_input", "audio_input", "video_input", "file_input", "tools", "json_mode", "reasoning", "code_exec"],
        "gemini-3-pro":     ["text", "streaming", "image_input", "audio_input", "video_input", "file_input", "tools", "json_mode", "reasoning"],
        "gemini-3-flash":   ["text", "streaming", "image_input", "audio_input", "file_input", "tools", "json_mode"],
        "gemini-2.5-pro":   ["text", "streaming", "image_input", "audio_input", "video_input", "file_input", "tools", "json_mode", "reasoning"],
        "gemini-2.5-flash": ["text", "streaming", "image_input", "audio_input", "file_input", "tools", "json_mode"],
        "gemini-2.0-flash": ["text", "streaming", "image_input", "tools", "json_mode"],
    },
    "openai": {
        # task 57: GPT-5.5 family
        "gpt-5.5-pro":      ["text", "streaming", "image_input", "audio_input", "tools", "json_mode", "reasoning", "code_exec", "web_search"],
        "gpt-5.5-thinking": ["text", "streaming", "image_input", "tools", "json_mode", "reasoning"],
        "gpt-5.5-instant": ["text", "streaming", "image_input", "tools", "json_mode"],
        "gpt-5.5":          ["text", "streaming", "image_input", "tools", "json_mode", "reasoning"],
        "gpt-5":            ["text", "streaming", "image_input", "tools", "json_mode"],
        "gpt-4o":           ["text", "streaming", "image_input", "audio_input", "tools", "json_mode"],
        "gpt-4o-mini":      ["text", "streaming", "image_input", "tools", "json_mode"],
        "gpt-4-turbo":      ["text", "streaming", "image_input", "tools", "json_mode"],
        "gpt-4.1":          ["text", "streaming", "image_input", "tools", "json_mode"],
        "o1":               ["text", "streaming", "reasoning"],
        "o1-mini":          ["text", "streaming", "reasoning"],
    },
    "openrouter": {},  # 透传上游能力，调用方需自行查 catalog
    "siliconflow": {
        # task 57: DeepSeek V4 / Qwen 3.7
        "deepseek-ai/DeepSeek-V4-Pro":   ["text", "streaming", "tools", "json_mode", "reasoning", "code_exec"],
        "deepseek-ai/DeepSeek-V4-Flash": ["text", "streaming", "tools", "json_mode"],
        "deepseek-ai/DeepSeek-V3":       ["text", "streaming", "tools", "json_mode"],
        "Qwen/Qwen3.7-Max":              ["text", "streaming", "image_input", "tools", "json_mode", "reasoning", "code_exec"],
        "Qwen/Qwen3.6-Flash":            ["text", "streaming", "tools", "json_mode"],
        "Qwen/Qwen2.5-72B-Instruct":     ["text", "streaming", "tools"],
    },
    "minimax":   {"MiniMax-M1": ["text", "streaming", "tools", "json_mode"]},
    "dashscope": {
        "qwen3.7-max":   ["text", "streaming", "image_input", "tools", "json_mode", "reasoning"],
        "qwen3.6-flash": ["text", "streaming", "tools", "json_mode"],
        "qwen-max":      ["text", "streaming", "tools", "json_mode"],
        "qwen-plus":     ["text", "streaming", "tools"],
        "qwen-turbo":    ["text", "streaming"],
    },
    "hunyuan":   {"hunyuan-turbos-latest": ["text", "streaming", "tools"], "hunyuan-large": ["text", "streaming", "tools"]},
    "doubao":    {"doubao-1-5-pro-32k-250115": ["text", "streaming", "image_input", "tools"], "doubao-1-5-lite-32k-250115": ["text", "streaming"]},
}


# task: embedding 模型名字 heuristic — 用户本地部署的 bge / nomic / mxbai /
# OpenAI text-embedding-* / Cohere embed-* / Voyage / Jina 等都没在 _CAPABILITY_DEFAULTS
# 内置表,但他们名字有强模式。匹配到任一关键字 → 标记 "embedding" capability,
# 这样 RAG 模型下拉(filter caps=["embedding"])就能展示。
_EMBEDDING_NAME_PATTERNS = (
    "embedding", "embed-", "-embed", "embed_",
    "text-embedding", "bge-", "bge_",  # BAAI BGE family
    "nomic-embed", "mxbai-embed",      # ollama / mxbai
    "voyage-", "jina-embed",            # Voyage / Jina
    "e5-", "gte-",                      # E5 / GTE family
    "m3e-",                             # Moka m3e
    "cohere.embed", "embed-multi", "embed-english",  # Cohere
)


def _infer_embedding_capability(model_real_name: str) -> bool:
    """按名字判断是否 embedding 模型(用户本地部署 / OpenAI-compat catalog 外的)。"""
    name = (model_real_name or "").lower()
    return any(pat in name for pat in _EMBEDDING_NAME_PATTERNS)


def get_capabilities(api_id: str, model_real_name: str, catalog_override: list[str] | None = None) -> list[str]:
    """返回模型能力清单。
    catalog 中的 capabilities + 内置默认表 merge,去重保序。
    embedding 模型按名字 heuristic 兜底(_CAPABILITY_DEFAULTS 内置只有 chat 模型)。
    """
    table = _CAPABILITY_DEFAULTS.get(api_id, {})
    defaults: list[str] = []
    if model_real_name in table:
        defaults = list(table[model_real_name])
    else:
        for prefix in sorted(table.keys(), key=len, reverse=True):
            if model_real_name.startswith(prefix):
                defaults = list(table[prefix])
                break
    catalog = list(catalog_override or [])
    seen = set()
    out = []
    for c in catalog + defaults:
        if c not in seen:
            out.append(c)
            seen.add(c)
    # task: 名字 heuristic 兜底加 embedding cap
    if _infer_embedding_capability(model_real_name) and "embedding" not in seen:
        out.append("embedding")
        seen.add("embedding")
    # 默认值 — embedding 模型不应回到 text+streaming
    if not out:
        return ["text", "streaming"]
    return out


def describe_capabilities(caps: list[str]) -> list[dict[str, str]]:
    """把能力代码翻译成带 label 的结构（前端直接渲染徽标）"""
    return [{"id": c, "label": CAPABILITY_LABELS.get(c, c)} for c in caps]


def _credential_present(api: dict[str, Any]) -> bool:
    """轻量检查凭证是否存在（不验证有效性）。"""
    if api.get("credential_env"):
        return bool(os.environ.get(api["credential_env"]))
    if api.get("credential_ref"):
        ref = api["credential_ref"]
        p = Path(ref) if Path(ref).is_absolute() else BASE / Path(ref).name
        return p.exists()
    return False
