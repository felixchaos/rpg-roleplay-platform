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

BASE = Path(__file__).parent
MODEL_CONFIG_FILE = BASE / "config" / "model_catalog.json"

DEFAULT_MODEL_CATALOG: dict[str, Any] = {
    "schema_version": 1,
    "selected": {
        "api_id": "vertex_ai",
        "model_id": "gemini-3.5-flash",
    },
    "apis": [
        {
            "id": "vertex_ai",
            "display_name": "Vertex AI",
            "kind": "vertex_ai",
            "enabled": True,
            "credential_ref": "rpg/vertex_sa.json",
            # task 57 (2026-05-25): 校准到最新（同步 rpg/config/model_catalog.json）
            "models": [
                {"id": "gemini-3.5-flash", "real_name": "gemini-3.5-flash", "display_name": "Gemini 3.5 Flash · 当前默认", "enabled": True,
                 "capabilities": ["text", "streaming", "image_input", "audio_input", "file_input", "tools", "json_mode", "reasoning"]},
                {"id": "gemini-3.1-pro",   "real_name": "gemini-3.1-pro",   "display_name": "Gemini 3.1 Pro · 强推理 / 1M context", "enabled": True,
                 "capabilities": ["text", "streaming", "image_input", "audio_input", "video_input", "file_input", "tools", "json_mode", "reasoning", "code_exec"]},
                {"id": "gemini-2.5-pro",   "real_name": "gemini-2.5-pro",   "display_name": "Gemini 2.5 Pro · 旧版便宜", "enabled": True,
                 "capabilities": ["text", "streaming", "image_input", "audio_input", "video_input", "file_input", "tools", "json_mode", "reasoning"]},
                {"id": "gemini-2.5-flash", "real_name": "gemini-2.5-flash", "display_name": "Gemini 2.5 Flash · 旧版便宜", "enabled": True,
                 "capabilities": ["text", "streaming", "image_input", "audio_input", "file_input", "tools", "json_mode"]},
            ],
        },
        {
            "id": "anthropic",
            "display_name": "Anthropic",
            "kind": "anthropic",
            "enabled": False,
            "credential_env": "ANTHROPIC_API_KEY",
            "models": [
                {"id": "claude-opus-4-7",   "real_name": "claude-opus-4-7",   "display_name": "Claude Opus 4.7 · 当前 frontier (2026-04-16)", "enabled": True,
                 "capabilities": ["text", "streaming", "image_input", "file_input", "tools", "json_mode", "reasoning", "computer_use", "code_exec"]},
                {"id": "claude-sonnet-4-6", "real_name": "claude-sonnet-4-6", "display_name": "Claude Sonnet 4.6 · 平衡 (2026-02)", "enabled": True,
                 "capabilities": ["text", "streaming", "image_input", "file_input", "tools", "json_mode", "reasoning", "computer_use"]},
                {"id": "claude-haiku-4-5",  "real_name": "claude-haiku-4-5",  "display_name": "Claude Haiku 4.5 · 低成本 (2025-10)", "enabled": True,
                 "capabilities": ["text", "streaming", "image_input", "tools", "json_mode"]},
                {"id": "claude-opus-4-5",   "real_name": "claude-opus-4-5",   "display_name": "Claude Opus 4.5 · 旧 frontier", "enabled": False,
                 "capabilities": ["text", "streaming", "image_input", "file_input", "tools", "json_mode", "reasoning"]},
            ],
        },
        {
            "id": "openai",
            "display_name": "OpenAI",
            "kind": "openai",
            "enabled": False,
            "credential_env": "OPENAI_API_KEY",
            "base_url": "https://api.openai.com/v1",
            "models": [
                {"id": "gpt-5.5",          "real_name": "gpt-5.5",          "display_name": "GPT-5.5 · 当前默认 (2026-05-05)", "enabled": True,
                 "capabilities": ["text", "streaming", "image_input", "tools", "json_mode", "reasoning"]},
                {"id": "gpt-5.5-pro",      "real_name": "gpt-5.5-pro",      "display_name": "GPT-5.5 Pro · 付费旗舰", "enabled": True,
                 "capabilities": ["text", "streaming", "image_input", "audio_input", "tools", "json_mode", "reasoning", "code_exec", "web_search"]},
                {"id": "gpt-5.5-thinking", "real_name": "gpt-5.5-thinking", "display_name": "GPT-5.5 Thinking · 推理优化", "enabled": True,
                 "capabilities": ["text", "streaming", "image_input", "tools", "json_mode", "reasoning"]},
                {"id": "gpt-5.5-instant",  "real_name": "gpt-5.5-instant",  "display_name": "GPT-5.5 Instant · 低延迟", "enabled": True,
                 "capabilities": ["text", "streaming", "image_input", "tools", "json_mode"]},
                {"id": "gpt-4o",           "real_name": "gpt-4o",           "display_name": "GPT-4o · 旧版多模态", "enabled": False,
                 "capabilities": ["text", "streaming", "image_input", "audio_input", "tools", "json_mode"]},
            ],
        },
        {
            "id": "openrouter",
            "display_name": "OpenRouter (聚合)",
            "kind": "openai_compat",
            "enabled": False,
            "credential_env": "OPENROUTER_API_KEY",
            "base_url": "https://openrouter.ai/api/v1",
            "models": [
                {"id": "anthropic/claude-opus-4-7", "real_name": "anthropic/claude-opus-4-7", "display_name": "Claude Opus 4.7 (OR)", "enabled": True,
                 "capabilities": ["text", "streaming", "image_input", "tools", "json_mode", "reasoning"]},
                {"id": "openai/gpt-5.5",            "real_name": "openai/gpt-5.5",            "display_name": "GPT-5.5 (OR)",         "enabled": True,
                 "capabilities": ["text", "streaming", "image_input", "tools", "json_mode", "reasoning"]},
                {"id": "google/gemini-3.5-flash",   "real_name": "google/gemini-3.5-flash",   "display_name": "Gemini 3.5 Flash (OR)", "enabled": True,
                 "capabilities": ["text", "streaming", "image_input", "tools", "json_mode"]},
            ],
        },
        {
            "id": "siliconflow",
            "display_name": "硅基流动 SiliconFlow",
            "kind": "openai_compat",
            "enabled": False,
            "credential_env": "SILICONFLOW_API_KEY",
            "base_url": "https://api.siliconflow.cn/v1",
            "models": [
                # task 57: DeepSeek V4 (2026-04-24) + Qwen 3.7 (2026-05-21)
                {"id": "deepseek-ai/DeepSeek-V4-Pro",   "real_name": "deepseek-ai/DeepSeek-V4-Pro",   "display_name": "DeepSeek V4 Pro · 1.6T / 1M ctx (2026-04-24)", "enabled": True,
                 "capabilities": ["text", "streaming", "tools", "json_mode", "reasoning", "code_exec"]},
                {"id": "deepseek-ai/DeepSeek-V4-Flash", "real_name": "deepseek-ai/DeepSeek-V4-Flash", "display_name": "DeepSeek V4 Flash · 廉价", "enabled": True,
                 "capabilities": ["text", "streaming", "tools", "json_mode"]},
                {"id": "Qwen/Qwen3.7-Max",              "real_name": "Qwen/Qwen3.7-Max",              "display_name": "Qwen 3.7-Max · 1M ctx (2026-05-21)", "enabled": True,
                 "capabilities": ["text", "streaming", "image_input", "tools", "json_mode", "reasoning", "code_exec"]},
                {"id": "Qwen/Qwen3.6-Flash",            "real_name": "Qwen/Qwen3.6-Flash",            "display_name": "Qwen 3.6 Flash · 便宜", "enabled": True,
                 "capabilities": ["text", "streaming", "tools", "json_mode"]},
            ],
        },
        {
            "id": "minimax",
            "display_name": "MiniMax",
            "kind": "openai_compat",
            "enabled": False,
            "credential_env": "MINIMAX_API_KEY",
            "base_url": "https://api.minimax.chat/v1",
            "models": [
                {"id": "MiniMax-M1",  "real_name": "MiniMax-M1",  "display_name": "MiniMax M1",  "enabled": True, "capabilities": ["text", "streaming"]},
                {"id": "abab6.5s-chat", "real_name": "abab6.5s-chat", "display_name": "abab 6.5s", "enabled": True, "capabilities": ["text", "streaming"]},
            ],
        },
        {
            "id": "dashscope",
            "display_name": "阿里云百炼 DashScope",
            "kind": "openai_compat",
            "enabled": False,
            "credential_env": "DASHSCOPE_API_KEY",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            # task 57: Qwen 3.7-Max (2026-05-21) 国内直供
            "models": [
                {"id": "qwen3.7-max",   "real_name": "qwen3.7-max",   "display_name": "通义千问 3.7-Max · 旗舰 (2026-05-21)", "enabled": True,
                 "capabilities": ["text", "streaming", "image_input", "tools", "json_mode", "reasoning"]},
                {"id": "qwen3.6-flash", "real_name": "qwen3.6-flash", "display_name": "通义千问 3.6 Flash · 便宜", "enabled": True,
                 "capabilities": ["text", "streaming", "tools", "json_mode"]},
                {"id": "qwen-max",      "real_name": "qwen-max",      "display_name": "通义千问 Max · 旧版", "enabled": False, "capabilities": ["text", "streaming"]},
                {"id": "qwen-plus",     "real_name": "qwen-plus",     "display_name": "通义千问 Plus", "enabled": True, "capabilities": ["text", "streaming"]},
                {"id": "qwen-turbo",    "real_name": "qwen-turbo",    "display_name": "通义千问 Turbo", "enabled": True, "capabilities": ["text", "streaming"]},
            ],
        },
        {
            "id": "hunyuan",
            "display_name": "腾讯混元 Hunyuan",
            "kind": "openai_compat",
            "enabled": False,
            "credential_env": "HUNYUAN_API_KEY",
            "base_url": "https://api.hunyuan.cloud.tencent.com/v1",
            "models": [
                {"id": "hunyuan-turbos-latest", "real_name": "hunyuan-turbos-latest", "display_name": "混元 TurboS", "enabled": True, "capabilities": ["text", "streaming"]},
                {"id": "hunyuan-large",         "real_name": "hunyuan-large",         "display_name": "混元 Large",   "enabled": True, "capabilities": ["text", "streaming"]},
            ],
        },
        {
            "id": "doubao",
            "display_name": "火山引擎 豆包 Doubao",
            "kind": "openai_compat",
            "enabled": False,
            "credential_env": "ARK_API_KEY",
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "models": [
                {"id": "doubao-1-5-pro-32k-250115",   "real_name": "doubao-1-5-pro-32k-250115",   "display_name": "豆包 1.5 Pro",   "enabled": True, "capabilities": ["text", "streaming"]},
                {"id": "doubao-1-5-lite-32k-250115",  "real_name": "doubao-1-5-lite-32k-250115",  "display_name": "豆包 1.5 Lite",  "enabled": True, "capabilities": ["text", "streaming"]},
            ],
        },
        {
            "id": "xiaomi_mimo",
            "display_name": "小米 MiMo（占位）",
            "kind": "openai_compat",
            "enabled": False,
            "credential_env": "MIMO_API_KEY",
            "base_url": "",
            "metadata": {"status": "preview", "note": "MiMo 公共 API 暂未开放，base_url 待小米发布后填入"},
            "models": [
                {"id": "mimo-7b-rl", "real_name": "mimo-7b-rl", "display_name": "MiMo-7B-RL", "enabled": False, "capabilities": ["text"]},
            ],
        },
    ],
}


def load_model_catalog() -> dict[str, Any]:
    db_catalog = _load_model_catalog_from_db()
    if db_catalog:
        return db_catalog
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
    return _migrate_catalog(data)


def save_model_catalog(catalog: dict[str, Any]) -> None:
    catalog = _migrate_catalog(catalog)
    _save_model_catalog_to_db(catalog)
    MODEL_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = MODEL_CONFIG_FILE.with_suffix(".json.tmp")
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)
    tmp_file.replace(MODEL_CONFIG_FILE)


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
    api_id = str(api_data.get("api_id") or api_data.get("id") or "").strip()
    if not api_id:
        raise ValueError("API id 不能为空")
    api = find_api(catalog, api_id)
    normalized = copy.deepcopy(api) if api else {"id": api_id, "models": []}
    normalized["id"] = api_id
    if not api:
        normalized.update({
            "display_name": str(api_data.get("display_name") or api_data.get("name") or api_id).strip(),
            "kind": str(api_data.get("kind") or api_id).strip(),
            "enabled": bool(api_data.get("enabled", True)),
            "credential_ref": api_data.get("credential_ref", ""),
            "credential_env": api_data.get("credential_env", ""),
            "base_url": api_data.get("base_url", ""),
        })
    else:
        if "display_name" in api_data or "name" in api_data:
            normalized["display_name"] = str(api_data.get("display_name") or api_data.get("name") or api_id).strip()
        if "kind" in api_data:
            normalized["kind"] = str(api_data.get("kind") or api_id).strip()
        if "enabled" in api_data:
            normalized["enabled"] = bool(api_data.get("enabled"))
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
    return next((api for api in catalog.get("apis", []) if api.get("id") == api_id), None)


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
            catalog["apis"] = data["apis"]
        if isinstance(data.get("selected"), dict):
            catalog["selected"] = data["selected"]
    catalog["schema_version"] = 1
    _backfill_model_capabilities(catalog)
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
