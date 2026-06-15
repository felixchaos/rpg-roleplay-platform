"""
model_registry.py - app-level API/model catalog.

The catalog is intentionally separate from game saves. Providers own the real
model identifiers; the UI can choose a display label from those supported models.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

# 规范化逻辑集中在 model_aliases；此处再导出以兼容现有 from model_registry import normalize_api_id。
from model_aliases import _API_ID_ALIASES, normalize_api_id  # noqa: F401

BASE = Path(__file__).parent
MODEL_CONFIG_FILE = BASE / "config" / "model_catalog.json"


def default_api_for(api_id: str | None) -> dict[str, Any] | None:
    target = normalize_api_id(api_id)
    return next((copy.deepcopy(api) for api in DEFAULT_MODEL_CATALOG["apis"] if normalize_api_id(api.get("id")) == target), None)


DEFAULT_MODEL_CATALOG: dict[str, Any] = {
    "schema_version": 1,
    "selected": {
        "api_id": "vertex_ai",
        "model_id": "gemini-2.5-flash",
    },
    "apis": [
        # vertex_ai: 完整保留真实可用 chat 模型 + 所有 embedding 模型。
        # 这是系统默认 provider；fresh/未 sync 实例的 GM 依赖此保底列表。
        {
            "id": "vertex_ai",
            "display_name": "Vertex AI",
            "kind": "vertex_ai",
            "enabled": True,
            "credential_ref": "rpg/vertex_sa.json",
            "models": [
                {"id": "gemini-2.5-flash", "real_name": "gemini-2.5-flash", "display_name": "Gemini 2.5 Flash", "enabled": True,
                 "capabilities": ["text", "streaming", "image_input", "audio_input", "file_input", "tools", "json_mode"]},
                {"id": "gemini-2.5-pro",   "real_name": "gemini-2.5-pro",   "display_name": "Gemini 2.5 Pro", "enabled": True,
                 "capabilities": ["text", "streaming", "image_input", "audio_input", "video_input", "file_input", "tools", "json_mode", "reasoning", "code_exec"]},
                # 向量嵌入(RAG)— 768 维,与 DB 向量列原生一致。text-embedding-004 是系统默认。
                {"id": "text-embedding-004", "real_name": "text-embedding-004", "display_name": "Text Embedding 004 · 默认", "enabled": True,
                 "capabilities": ["embedding"]},
                {"id": "text-multilingual-embedding-002", "real_name": "text-multilingual-embedding-002", "display_name": "Multilingual Embedding 002", "enabled": True,
                 "capabilities": ["embedding"]},
            ],
        },
        # 以下 provider 的 chat models 清空为 []；真实模型列表由用户配 key 后 sync 写入。
        # embedding 模型条目保留（供 RAG 向量选择器在用户配 key 前就能看到条目）。
        {
            "id": "anthropic",
            "display_name": "Anthropic",
            "kind": "anthropic",
            "enabled": False,
            "credential_env": "ANTHROPIC_API_KEY",
            "models": [],
        },
        {
            "id": "openai",
            "display_name": "OpenAI",
            "kind": "openai",
            "enabled": False,
            "credential_env": "OPENAI_API_KEY",
            "base_url": "https://api.openai.com/v1",
            "models": [
                # 向量嵌入(RAG)— 支持 dimensions 降维到 768,与 DB 向量列对齐。
                # (不收 ada-002:它不支持 dimensions,只能输出 1536 维,放不进 768 列。)
                {"id": "text-embedding-3-small", "real_name": "text-embedding-3-small", "display_name": "Text Embedding 3 Small", "enabled": True,
                 "capabilities": ["embedding"]},
                {"id": "text-embedding-3-large", "real_name": "text-embedding-3-large", "display_name": "Text Embedding 3 Large", "enabled": True,
                 "capabilities": ["embedding"]},
            ],
        },
        {
            "id": "openrouter",
            "display_name": "OpenRouter",
            "kind": "openai_compat",
            "enabled": False,
            "credential_env": "OPENROUTER_API_KEY",
            "base_url": "https://openrouter.ai/api/v1",
            "models": [],
        },
        {
            "id": "deepseek",
            "display_name": "DeepSeek",
            "kind": "openai_compat",
            "enabled": False,
            "credential_env": "DEEPSEEK_API_KEY",
            "base_url": "https://api.deepseek.com/v1",
            # 真实可访问模型必须用当前用户 API Key 调 /models 后写入；这里不放静态假清单。
            "models": [],
        },
        {
            "id": "siliconflow",
            "display_name": "SiliconFlow",
            "kind": "openai_compat",
            "enabled": False,
            "credential_env": "SILICONFLOW_API_KEY",
            "base_url": "https://api.siliconflow.cn/v1",
            "models": [],
        },
        {
            "id": "minimax",
            "display_name": "MiniMax",
            "kind": "openai_compat",
            "enabled": False,
            "credential_env": "MINIMAX_API_KEY",
            "base_url": "https://api.minimax.chat/v1",
            "models": [],
        },
        {
            "id": "dashscope",
            "display_name": "DashScope",
            "kind": "openai_compat",
            "enabled": False,
            "credential_env": "DASHSCOPE_API_KEY",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "models": [
                # 向量嵌入(RAG)— text-embedding-v3 支持 dimensions 降维到 768,经 compatible-mode
                # 的 /embeddings(OpenAI 兼容)调用。
                {"id": "text-embedding-v3", "real_name": "text-embedding-v3", "display_name": "Qwen Text Embedding v3", "enabled": True,
                 "capabilities": ["embedding"]},
            ],
        },
        {
            "id": "hunyuan",
            "display_name": "Hunyuan",
            "kind": "openai_compat",
            "enabled": False,
            "credential_env": "HUNYUAN_API_KEY",
            "base_url": "https://api.hunyuan.cloud.tencent.com/v1",
            "models": [],
        },
        {
            "id": "doubao",
            "display_name": "Doubao",
            "kind": "openai_compat",
            "enabled": False,
            "credential_env": "ARK_API_KEY",
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "models": [],
        },
        {
            "id": "xiaomi_mimo",
            "display_name": "MiMo",
            "kind": "openai_compat",
            "enabled": False,
            "credential_env": "MIMO_API_KEY",
            "base_url": "",
            "metadata": {"status": "preview", "note": "MiMo 公共 API 暂未开放，base_url 待小米发布后填入"},
            "models": [],
        },
        {
            "id": "google_ai_studio",
            "display_name": "Google AI Studio (Gemini)",
            "kind": "openai_compat",
            "enabled": False,
            "credential_env": "GOOGLE_API_KEY",
            # Gemini 的 OpenAI 兼容端点在 /v1beta/openai —— base_url 只到
            # generativelanguage.googleapis.com 时,SDK 拼成 .../chat/completions 会 404
            # (用户实测「找不到」)。走兼容层后用户只填自己的 Google AI Studio key 即可。
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
            "models": [],
        },
    ],
}

# 这些 provider 必须存在于 catalog(整条 api)——持久化 catalog 可能在新增它们之前存的,
# 缺了会导致选择器里没有、GM 调用降级失败(用户实测 Google AI Studio「找不到」)。
_CURATED_REQUIRED_APIS = {"google_ai_studio"}


def _ensure_curated_embeddings(catalog: dict[str, Any]) -> dict[str, Any]:
    """确保每个 provider 带上 DEFAULT_MODEL_CATALOG 里人工策展的 embedding 模型。

    持久化 catalog(DB / 文件)可能是在新增 embedding 条目之前存的,不含它们 →
    RAG 向量模型选择器会空。这里在 serve 时把 DEFAULT 的 embedding 模型并回去
    (幂等、按 real_name 去重、只改内存不落库),让新增 embedding 自愈生效。
    """
    try:
        from model_probe import is_embedding_model  # lazy: model_probe imports model_registry
        default_by_id = {normalize_api_id(a.get("id")): a for a in DEFAULT_MODEL_CATALOG["apis"]}
        for api in catalog.get("apis", []):
            d = default_by_id.get(normalize_api_id(api.get("id")))
            if not d:
                continue
            curated = [m for m in (d.get("models") or []) if is_embedding_model(m)]
            if not curated:
                continue
            have = {(m.get("real_name") or m.get("id")) for m in (api.get("models") or [])}
            for m in curated:
                if (m.get("real_name") or m.get("id")) not in have:
                    api.setdefault("models", []).append(copy.deepcopy(m))
    except Exception:
        pass
    return catalog


def _ensure_curated_apis(catalog: dict[str, Any]) -> dict[str, Any]:
    """确保 _CURATED_REQUIRED_APIS 里的整条 provider 存在于已持久化的 catalog。

    持久化 catalog(DB/文件)可能是在新增某 provider 之前存的 → 选择器里没有它、
    GM 调用 find_api 返回 None 被降级失败(如 google_ai_studio「找不到」)。这里把缺失
    的整条 api 从 DEFAULT_MODEL_CATALOG 并回内存(幂等、按 normalize_api_id 去重、不落库)。
    只补 _CURATED_REQUIRED_APIS 白名单,避免复活管理员有意删除的其它 provider。
    """
    try:
        have = {normalize_api_id(a.get("id")) for a in catalog.get("apis", [])}
        for d in DEFAULT_MODEL_CATALOG["apis"]:
            nid = normalize_api_id(d.get("id"))
            if nid in _CURATED_REQUIRED_APIS and nid not in have:
                catalog.setdefault("apis", []).append(copy.deepcopy(d))
    except Exception:
        pass
    return catalog


def load_model_catalog() -> dict[str, Any]:
    db_catalog = _load_model_catalog_from_db()
    if db_catalog:
        return _ensure_curated_apis(_ensure_curated_embeddings(db_catalog))
    MODEL_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not MODEL_CONFIG_FILE.exists():
        catalog = copy.deepcopy(DEFAULT_MODEL_CATALOG)
        save_model_catalog(catalog)
        return catalog
    try:
        with open(MODEL_CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    return _ensure_curated_apis(_ensure_curated_embeddings(_migrate_catalog(data)))


def save_model_catalog(catalog: dict[str, Any]) -> None:
    # 全局模型目录的【权威存储是 DB】(model_apis / model_entries / app_config.selected_model):
    # 所有 worker 读 DB 同一真相源、写走行级 upsert 原子。**不再写 config/model_catalog.json** ——
    # 运行时写它会造成 git churn(反复改动被提交)+ 部署互相覆盖 + 多 worker 文件不一致(用户反馈)。
    # 该 JSON 退化为只读 seed / DB 读失败时的兜底(见 load_model_catalog 的异常分支)。
    catalog = _migrate_catalog(catalog)
    _save_model_catalog_to_db(catalog)


def apply_user_overlay(catalog: dict[str, Any], user_id: int | None) -> dict[str, Any]:
    """把某用户私有的 overlay(remote/sync 拉到的本账号可见模型 + 自建中转站)
    merge 到全局 catalog 之上,返回**新的** catalog。只应用这一个用户的 overlay。

    安全:全局 catalog 永远只含 admin 策展的 provider 菜单。用户的同步模型/
    自定义 provider 绝不写全局,只在该用户自己的视图里出现。

    - 已在全局菜单里的 provider:用该用户同步到的模型清单**替换**其 models
      (这是该用户账号实际可访问的模型,权威来源)。
    - 不在全局菜单里的 api_id(用户自建中转站):从其 user_api_credentials
      (带 base_url_override)合成一个 openai_compat provider 行追加进去。
    """
    if not user_id:
        return catalog
    try:
        from platform_app.user_models import load_overlay
        overlay = load_overlay(int(user_id))
    except Exception:
        overlay = {}
    if not overlay:
        return catalog
    result = copy.deepcopy(catalog)
    by_id = {normalize_api_id(api.get("id")): api for api in result.get("apis", [])}

    # 自建中转站需要 base_url:从用户凭证取
    cred_base: dict[str, str] = {}
    custom_ids = [aid for aid in overlay if normalize_api_id(aid) not in by_id]
    if custom_ids:
        try:
            from platform_app.user_credentials import list_credentials
            for item in (list_credentials(int(user_id)).get("items") or []):
                cred_base[normalize_api_id(item.get("api_id"))] = item.get("base_url_override") or ""
        except Exception:
            cred_base = {}

    for raw_api_id, models in overlay.items():
        api_id = normalize_api_id(raw_api_id)
        existing = by_id.get(api_id)
        cleaned = [m for m in (models or []) if isinstance(m, dict)]
        if existing is not None:
            # 用户同步清单覆盖全局 provider 的 models。清理后为空时
            # **不**用空清单覆盖全局好模型,保留全局菜单 —— 否则该用户该 provider 视图
            # 变空,first_user_model 兜底无可用模型可回退。
            if cleaned:
                # 保留全局菜单里该 provider 人工策展的 embedding 模型:模型同步通常只抓
                # chat 模型(provider 的 /models 多不列 embedding),若被 overlay 直接覆盖,
                # RAG 向量模型选择器就会空 → 用户配了 key 也选不到 embedding。
                from model_probe import is_embedding_model  # lazy: model_probe imports model_registry
                synced_names = {(m.get("real_name") or m.get("id")) for m in cleaned}
                curated_embeds = [
                    m for m in (existing.get("models") or [])
                    if is_embedding_model(m)
                    and (m.get("real_name") or m.get("id")) not in synced_names
                ]
                existing["models"] = cleaned + curated_embeds
            continue
        # 自建中转站:只有当用户确实配过该 provider 的凭证(带 base_url)才合成,
        # 避免悬空条目。base_url 来自用户凭证(per-user,已做 SSRF 校验)。
        base_url = cred_base.get(api_id, "")
        if not base_url:
            continue
        result.setdefault("apis", []).append({
            "id": api_id,
            "display_name": api_id,
            "kind": "openai_compat",
            "enabled": True,
            "credential_ref": "",
            "credential_env": "",
            "base_url": base_url,
            "models": cleaned,
            "_custom": True,
        })
    return result


def load_catalog_for_user(user_id: int | None = None) -> dict[str, Any]:
    """面向用户的 catalog 视图 = 全局菜单 + 该用户私有 overlay。
    所有面向单个用户的读取(模型选择器、BYOK 默认模型)都应走这个,
    而不是裸 load_model_catalog()(那是全局菜单,会跨用户泄露)。"""
    return apply_user_overlay(load_model_catalog(), user_id)


def selected_model(catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    catalog = _migrate_catalog(catalog or load_model_catalog())
    selected = catalog.get("selected") or {}
    api = find_api(catalog, selected.get("api_id")) or first_enabled_api(catalog)
    model = find_model(api, selected.get("model_id")) or first_enabled_model(api)
    return {
        "api_id": api["id"],
        "api_display_name": api.get("display_name") or api["id"],
        "api_kind": api.get("kind") or api["id"],
        "model_id": model["id"],
        "real_name": model.get("real_name") or model["id"],
        "display_name": model.get("display_name") or model.get("real_name") or model["id"],
        "capabilities": list(model.get("capabilities") or []),
    }


def select_model(api_id: str, model_id: str) -> dict[str, Any]:
    catalog = load_model_catalog()
    api = find_api(catalog, api_id)
    if not api:
        raise ValueError(f"未知 API：{api_id}")
    model = find_model(api, model_id)
    if not model:
        raise ValueError(f"API {api_id} 不支持模型：{model_id}")
    catalog["selected"] = {"api_id": api_id, "model_id": model_id}
    save_model_catalog(catalog)
    return load_model_catalog()


def upsert_api(api_data: dict[str, Any]) -> dict[str, Any]:
    catalog = load_model_catalog()
    api_id = normalize_api_id(api_data.get("api_id") or api_data.get("id"))
    if not api_id:
        raise ValueError("API id 不能为空")
    api = find_api(catalog, api_id)
    normalized = copy.deepcopy(api) if api else (default_api_for(api_id) or {"id": api_id, "models": []})
    normalized["id"] = api_id
    if not api:
        normalized.update({
            "display_name": str(api_data.get("display_name") or api_data.get("name") or api_id).strip(),
            "kind": str(api_data.get("kind") or normalized.get("kind") or api_id).strip(),
            "enabled": bool(api_data.get("enabled", True)),
            "credential_ref": api_data.get("credential_ref", normalized.get("credential_ref", "")),
            "credential_env": api_data.get("credential_env", normalized.get("credential_env", "")),
            "base_url": api_data.get("base_url", normalized.get("base_url", "")),
        })
    else:
        if "display_name" in api_data or "name" in api_data:
            normalized["display_name"] = str(api_data.get("display_name") or api_data.get("name") or api_id).strip()
        if "kind" in api_data:
            normalized["kind"] = str(api_data.get("kind") or api_id).strip()
        if "enabled" in api_data:
            normalized["enabled"] = bool(api_data.get("enabled"))  # type: ignore[assignment]
        for key in ("credential_ref", "credential_env", "base_url"):
            if key in api_data:
                normalized[key] = api_data.get(key, "")
    if "models" in api_data:
        normalized["models"] = list(api_data.get("models") or [])
    if api:
        api.clear()
        api.update(normalized)
    else:
        catalog.setdefault("apis", []).append(normalized)
    save_model_catalog(catalog)
    return load_model_catalog()


def upsert_model(api_id: str, model_data: dict[str, Any]) -> dict[str, Any]:
    catalog = load_model_catalog()
    api_id = normalize_api_id(api_id)
    api = find_api(catalog, api_id)
    if not api:
        raise ValueError(f"未知 API：{api_id}")
    model_id = str(model_data.get("id") or model_data.get("real_name") or "").strip()
    if not model_id:
        raise ValueError("模型 id 不能为空")
    model = find_model(api, model_id)
    normalized = {
        "id": model_id,
        "real_name": str(model_data.get("real_name") or model_id).strip(),
        "display_name": str(model_data.get("display_name") or model_data.get("real_name") or model_id).strip(),
        "enabled": bool(model_data.get("enabled", True)),
        "capabilities": list(model_data.get("capabilities") or (model or {}).get("capabilities") or ["text", "streaming"]),
    }
    if model:
        model.clear()
        model.update(normalized)
    else:
        api.setdefault("models", []).append(normalized)
    save_model_catalog(catalog)
    return load_model_catalog()


def delete_model(api_id: str, model_id: str) -> dict[str, Any]:
    catalog = load_model_catalog()
    api = find_api(catalog, api_id)
    if not api:
        raise ValueError(f"未知 API：{api_id}")
    model_id = str(model_id or "").strip()
    if not model_id:
        raise ValueError("模型 id 不能为空")
    models = list(api.get("models") or [])
    remaining = [
        model for model in models
        if model.get("id") != model_id and model.get("real_name") != model_id
    ]
    if len(remaining) == len(models):
        raise ValueError(f"模型不存在：{model_id}")
    if not remaining:
        raise ValueError("每个 API 至少保留一个模型")
    api["models"] = remaining
    selected = catalog.get("selected") or {}
    if selected.get("api_id") == api_id:
        deleted_ids = {
            model.get("id")
            for model in models
            if model.get("id") == model_id or model.get("real_name") == model_id
        }
        if selected.get("model_id") in deleted_ids:
            fallback = first_enabled_model(api)
            catalog["selected"] = {"api_id": api_id, "model_id": fallback["id"]}
    save_model_catalog(catalog)
    return load_model_catalog()


def find_api(catalog: dict[str, Any], api_id: str | None) -> dict[str, Any] | None:
    target = normalize_api_id(api_id)
    return next((api for api in catalog.get("apis", []) if normalize_api_id(api.get("id")) == target), None)


def base_url_for(api_id: str | None) -> str:
    """从 live catalog 取该 provider 的 base_url（OpenAI 兼容兜底用）。

    单一真源:_harness / extractor / command_agent 三处 _api_base_url 字节级重复 →
    统一收到这里。异常或缺失返回空串(调用方据此判断"未知 base_url")。

    注意:与 embedding.py 用 default_api_for(静态 DEFAULT 模板)取 base_url 数据源不同
    —— 那处是【有意】防误连 api.openai.com,不走 live catalog,不收编到此。
    """
    try:
        api = find_api(load_model_catalog(), api_id)
        return api.get("base_url", "") if api else ""
    except Exception:
        return ""


def api_kind(api_id: str | None) -> str:
    """从 live catalog 取该 provider 的 kind;缺失/异常时退回 api_id 本身。

    等价于散落各处的 `(api or {}).get("kind") or api_id`。纯查表 —— 不含
    master.py 的中转站特判(catalog 无该 api 但用户凭证带 base_url_override →
    强制 openai_compat),那是本函数的超集,保留在 GameMaster 构造一层。
    """
    try:
        api = find_api(load_model_catalog(), api_id) or {}
        return str(api.get("kind") or api_id)
    except Exception:
        return str(api_id)


def find_model(api: dict[str, Any] | None, model_id: str | None) -> dict[str, Any] | None:
    if not api:
        return None
    return next((model for model in api.get("models", []) if model.get("id") == model_id), None)


def first_enabled_api(catalog: dict[str, Any]) -> dict[str, Any]:
    apis = catalog.get("apis") or []
    return next((api for api in apis if api.get("enabled", True)), apis[0])


def first_enabled_model(api: dict[str, Any]) -> dict[str, Any]:
    models = api.get("models") or []
    return next((model for model in models if model.get("enabled", True)), models[0])


def _migrate_catalog(data: dict[str, Any]) -> dict[str, Any]:
    catalog = copy.deepcopy(DEFAULT_MODEL_CATALOG)
    if isinstance(data, dict):
        if isinstance(data.get("apis"), list) and data["apis"]:
            by_id = {normalize_api_id(api.get("id")): api for api in catalog["apis"]}
            order = [normalize_api_id(api.get("id")) for api in catalog["apis"]]
            for raw_api in data["apis"]:
                if not isinstance(raw_api, dict):
                    continue
                api_id = normalize_api_id(raw_api.get("id") or raw_api.get("api_id"))
                if not api_id:
                    continue
                base = by_id.get(api_id) or {"id": api_id, "models": []}
                merged = {**copy.deepcopy(base), **copy.deepcopy(raw_api), "id": api_id}
                if base.get("kind") and merged.get("kind") not in {"openai", "openai_compat", "anthropic", "vertex_ai"}:
                    merged["kind"] = base.get("kind")
                if base.get("base_url") and not merged.get("base_url"):
                    merged["base_url"] = base.get("base_url")
                if not isinstance(merged.get("models"), list):
                    merged["models"] = []
                by_id[api_id] = merged
                if api_id not in order:
                    order.append(api_id)
            catalog["apis"] = [by_id[api_id] for api_id in order if api_id in by_id]
        if isinstance(data.get("selected"), dict):
            selected = dict(data["selected"])
            selected["api_id"] = normalize_api_id(selected.get("api_id"))
            catalog["selected"] = selected
    catalog["schema_version"] = 1
    _backfill_model_capabilities(catalog)
    for api in catalog.get("apis", []):
        models = api.get("models") or []
        filtered = [m for m in models if isinstance(m, dict)]
        api["models"] = filtered
    selected = selected_model_without_migration(catalog)
    catalog["selected"] = {
        "api_id": selected["api_id"],
        "model_id": selected["model_id"],
    }
    return catalog


def _backfill_model_capabilities(catalog: dict[str, Any]) -> None:
    defaults: dict[tuple[str, str], list[str]] = {}
    for api in DEFAULT_MODEL_CATALOG["apis"]:
        for model in api.get("models", []):
            defaults[(api["id"], model["id"])] = list(model.get("capabilities") or ["text", "streaming"])
    for api in catalog.get("apis", []):
        for model in api.get("models", []):
            model.setdefault("capabilities", defaults.get((api.get("id"), model.get("id")), ["text", "streaming"]))


def selected_model_without_migration(catalog: dict[str, Any]) -> dict[str, Any]:
    selected = catalog.get("selected") or {}
    api = find_api(catalog, selected.get("api_id")) or first_enabled_api(catalog)
    model = find_model(api, selected.get("model_id")) or first_enabled_model(api)
    return {
        "api_id": api["id"],
        "model_id": model["id"],
    }


def _load_model_catalog_from_db() -> dict[str, Any] | None:
    try:
        from platform_app.db import connect, init_db

        init_db()
        with connect() as db:
            apis = db.execute("select * from model_apis order by api_id").fetchall()
            if not apis:
                _save_model_catalog_to_db(copy.deepcopy(DEFAULT_MODEL_CATALOG), db=db)
                apis = db.execute("select * from model_apis order by api_id").fetchall()
            selected = db.execute("select value from app_config where key = 'selected_model'").fetchone()
            rows = db.execute("select * from model_entries order by api_id, id").fetchall()
        by_api: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            by_api.setdefault(row["api_id"], []).append({
                "id": row["model_id"],
                "real_name": row["real_name"],
                "display_name": row["display_name"],
                "enabled": row["enabled"],
                "capabilities": list(row.get("capabilities") or []),
            })
        catalog = {
            "schema_version": 1,
            "selected": selected["value"] if selected else copy.deepcopy(DEFAULT_MODEL_CATALOG["selected"]),
            "apis": [
                {
                    "id": row["api_id"],
                    "display_name": row["display_name"],
                    "kind": row["kind"],
                    "enabled": row["enabled"],
                    "credential_ref": row["credential_ref"],
                    "credential_env": row["credential_env"],
                    "base_url": row.get("base_url", ""),
                    "models": by_api.get(row["api_id"], []),
                }
                for row in apis
            ],
        }
        return _migrate_catalog(catalog)
    except Exception:
        return None


def _save_model_catalog_to_db(catalog: dict[str, Any], db=None) -> None:
    try:
        from platform_app.db import connect, init_db

        init_db()
        if db is None:
            with connect() as db_conn:
                _write_model_catalog_rows(db_conn, catalog)
        else:
            _write_model_catalog_rows(db, catalog)
    except Exception:
        return


def _write_model_catalog_rows(db, catalog: dict[str, Any]) -> None:
    catalog = _migrate_catalog(catalog)
    keep_api_ids = [
        normalize_api_id(api.get("id"))
        for api in catalog.get("apis", [])
        if normalize_api_id(api.get("id"))
    ]
    db.execute(
        """
        insert into app_config(key, value)
        values ('selected_model', %s)
        on conflict(key) do update set value = excluded.value, updated_at = now()
        """,
        (Jsonb(catalog["selected"]),),
    )
    for api in catalog.get("apis", []):
        db.execute(
            """
            insert into model_apis(api_id, display_name, kind, enabled, credential_ref, credential_env, base_url)
            values (%s, %s, %s, %s, %s, %s, %s)
            on conflict(api_id) do update set
              display_name = excluded.display_name,
              kind = excluded.kind,
              enabled = excluded.enabled,
              credential_ref = excluded.credential_ref,
              credential_env = excluded.credential_env,
              base_url = excluded.base_url,
              updated_at = now()
            """,
            (
                api["id"],
                api.get("display_name") or api["id"],
                api.get("kind") or api["id"],
                bool(api.get("enabled", True)),
                api.get("credential_ref", ""),
                api.get("credential_env", ""),
                api.get("base_url", ""),
            ),
        )
        for model in api.get("models", []):
            model_id = model.get("id") or model.get("real_name")
            if not model_id:
                continue
            db.execute(
                """
                insert into model_entries(api_id, model_id, real_name, display_name, enabled, capabilities)
                values (%s, %s, %s, %s, %s, %s)
                on conflict(api_id, model_id) do update set
                  real_name = excluded.real_name,
                  display_name = excluded.display_name,
                  enabled = excluded.enabled,
                  capabilities = excluded.capabilities,
                  updated_at = now()
                """,
                (
                    api["id"],
                    model_id,
                    model.get("real_name") or model_id,
                    model.get("display_name") or model.get("real_name") or model_id,
                    bool(model.get("enabled", True)),
                    Jsonb(list(model.get("capabilities") or ["text", "streaming"])),
                ),
            )
        keep_model_ids = [
            model.get("id") or model.get("real_name")
            for model in api.get("models", [])
            if model.get("id") or model.get("real_name")
        ]
        if keep_model_ids:
            db.execute(
                "delete from model_entries where api_id = %s and model_id <> all(%s)",
                (api["id"], keep_model_ids),
            )
    if keep_api_ids:
        db.execute(
            "delete from model_apis where api_id <> all(%s)",
            (keep_api_ids,),
        )
