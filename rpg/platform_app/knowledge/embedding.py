"""task 51: Vertex text-embedding-004 + pgvector 双层检索。

设计思路(基于 LightRAG / novel2graph 双层检索范式):
- 块层(document_chunks.embedding_vec): 全书切块的向量,用于 RAG 语义召回
- 实体层(character_cards.embedding_vec, worldbook_entries.embedding_vec):
  角色/世界书条目的向量,GM 提到人名时按向量找完整卡片

embedding model: Google `text-embedding-004` (768 维,多语言含中文) — 默认
BYOK: 用户可在 user_preferences 设置 embed.api_id / embed.model_real_name,
      并在 user_api_credentials 保存对应 provider 的 API key,覆盖系统默认。
batch size: 100 chunks/请求(API 限 250,留 buffer)
存储: pgvector(已 brew install + CREATE EXTENSION)
查询: `embedding_vec <=> query_vec` cosine distance + ivfflat 索引

入口:
- `embed_query(text, user_id)` → str(vector) 给 `_search._embed_query` 用
- `embed_script(script_id, user_id)` → 后台 batch embed 全书 chunks + cards + worldbook
- `embed_status(script_id)` → 进度查询
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

log = logging.getLogger(__name__)

# 系统默认 embedding 配置(env 可覆盖,用户 BYOK 优先于 env)
DEFAULT_EMBED_MODEL = os.environ.get("EMBED_MODEL", "text-embedding-004")
DEFAULT_EMBED_API_ID = os.environ.get("EMBED_API_ID", "vertex_ai")
# 向量维度:默认 768(text-embedding-004 / 平台栈)。自部署用别的 provider 时设 EMBED_DIM
# (须与 migrations 建表维度一致,首次部署前设)。仅用于返回维度校验,不强制截断。
EMBED_DIM = int(os.environ.get("EMBED_DIM", "768") or "768")
# Vertex text-embedding-004 限制:**单请求总 token ≤ 20000**(不是 250 项)。
# 中文 chunk 平均 ~200 token,100 项已经超过 20K → 400 INVALID_ARGUMENT。
# 减到 30 项 × ~600 char ≈ 9000 tokens,留足 50% buffer 处理长 chunk。
BATCH_SIZE = 30
# 单批 embedding 连续失败上限:provider 永久故障(坏 key/配额耗尽/模型下线)时
# _embed_batch 始终返 None,原 while True 会 30s 一次无限重试 → daemon 线程永 spin、
# _EMBED_QUEUE_RUNNING flag 永 True(该 script 再不能重 embed)。超限即 raise,由
# _embed_chunks_loop 的 try/finally 优雅收尾(清 flag + 线程退出);chunks 留 null 待重试。
_MAX_EMBED_BATCH_RETRIES = 5
# 每个 chunk 文本上限(char),配合 batch_size 控制总 token。
# Vertex 中文 ~1 char/0.5 token,2400 char ≈ 1200 token;30 × 1200 = 36000 仍超。
# 改成 1200 char/chunk ≈ 600 token;30 × 600 = 18000 安全。
PER_CHUNK_CHAR_LIMIT = 1200
# 进程内 cache,避免 ChatPipeline 每次 _embed_query 都重新 import vertex SDK
_VERTEX_CLIENT_CACHE: dict[str, Any] = {}
_EMBED_QUEUE_RUNNING: dict[int, bool] = {}  # script_id → 是否在跑
# 最近一次 _embed_via_openai 失败的友好描述(405/401/404 等),供前端引导用户去 RAG 设置用。
_last_openai_embed_error: str = ""

# 向后兼容:保留 EMBED_MODEL 常量名(外部模块如 extract/ 直接引用它)
EMBED_MODEL = DEFAULT_EMBED_MODEL


_PLATFORM_FALLBACK_ROLES = ("admin", "vip_user")
_VERTEX_API_IDS = {"vertex", "google", "vertex_ai"}
_OPENAI_API_IDS = {"openai", "openai_compat"}
_GEMINI_API_IDS = {"gemini", "google_gemini"}
_COHERE_API_IDS = {"cohere"}


def _is_google_generative_openai_base(base_url: str) -> bool:
    return "generativelanguage.googleapis.com" in (base_url or "").lower()


def _native_gemini_embed_model(model: str) -> str:
    """Gemini OpenAI-compatible /embeddings hits batchEmbed quota; native uses embedContent."""
    model = (model or "").strip()
    if model in {"", "text-embedding-004"}:
        return os.environ.get("GEMINI_EMBED_MODEL", "gemini-embedding-001")
    return model


def _normalize_platform_embed_config(
    api_id: str,
    model: str,
    api_key: str,
    base_url: str,
) -> tuple[str, str, str, str]:
    """Platform Gemini key should use native embedContent, not OpenAI-compatible batchEmbed."""
    if api_key and api_id in _OPENAI_API_IDS and _is_google_generative_openai_base(base_url):
        return "gemini", _native_gemini_embed_model(model), api_key, ""
    # 自部署兜底:部署者配了 EMBED_API_KEY(+常配 EMBED_BASE_URL)但没设 EMBED_API_ID →
    # 默认值 vertex_ai 会走 Vertex SA(自部署没有)→ 静默失败。Vertex 用 SA 不用 api_key,
    # 所以「有 api_key」本身就说明意图是 OpenAI 兼容 provider(SiliconFlow 等),非 Vertex。
    # 非 google 原生 base 时纠偏成 openai,让自部署开箱即用。
    if api_key and api_id in _VERTEX_API_IDS and not _is_google_generative_openai_base(base_url):
        log.info("[embedding] EMBED_API_KEY set with default vertex_ai api_id → 纠偏为 openai (OpenAI 兼容 provider)")
        return "openai", model, api_key, base_url
    return api_id, model, api_key, base_url


def _is_admin(user_id: int | None) -> bool:
    """检查 user_id 是否为 admin 或 vip_user — 享受平台 embedder 兜底。
    其他用户(默认 role='user' / '')不享受,防白嫖付费 Gemini key。
    """
    if not user_id:
        return False
    try:
        from platform_app.db import connect
        with connect() as db:
            row = db.execute("select role from users where id = %s", (int(user_id),)).fetchone()
        return bool(row and (row.get("role") or "").lower() in _PLATFORM_FALLBACK_ROLES)
    except Exception:
        return False


def _resolve_embed_config(user_id: int | None) -> tuple[str, str, str, str]:
    """返回 (api_id, model, api_key, base_url_override)。

    优先链:
    1. user 自己配的 BYOK embedder credential(任何用户都允许)
    2. 平台 env 兜底(EMBED_API_KEY / EMBED_BASE_URL / EMBED_MODEL)— 只对 admin/vip 生效。
       普通用户没自己配 → 返回空 api_key,_embed_via_openai 会返 None 让上层降级。

    设计理由:Gemini API text-embedding-004 在付费层 $0.025/M tokens,100 用户
    满量 import ≈ $187 一次性。不给普通用户兜底,强制 BYOK。
    """
    env_base_url = os.environ.get("EMBED_BASE_URL", "")
    if user_id:
        try:
            from core.llm_backend import resolve_preferred_api, resolve_preferred_model
            from platform_app.user_credentials import resolve_api_key
            api_id = resolve_preferred_api(user_id, "embed.api_id") or DEFAULT_EMBED_API_ID
            model = resolve_preferred_model(user_id, "embed.model_real_name") or DEFAULT_EMBED_MODEL
            # user 自己配了 — 优先用,任何用户都允许
            cred = resolve_api_key(user_id, api_id, env_fallback="")
            if cred.get("key"):
                base_url = cred.get("base_url_override", "") or env_base_url
                return api_id, model, cred["key"], base_url
            # user 没自配 — 只 admin/vip 才走平台 env 兜底
            if _is_admin(user_id):
                return _platform_fallback_config()
            # 普通用户 + 没自配 → 返空 key 让 _embed_via_openai 返 None
            log.debug("[embedding] non-admin user %s without own embedder cred; refusing platform fallback", user_id)
            return api_id, model, "", ""
        except Exception as exc:
            log.debug("[embedding] resolve_embed_config failed for user %s: %s", user_id, exc)
    # 无 user_id (后台 cron / 内部任务):走 env 兜底
    return _platform_fallback_config()


def _get_vertex_client(user_id: int | None = None):
    """返回 Vertex genai Client,按 user_id 走 BYOK 优先链。

    task: Embedder 是 RAG 必需路径(每轮 chat 都要 embed user query),平台
    为用户兜底成本($150 一次性 + $1/月 vs LLM $135-27000/月)。Vertex
    text-embedding-004 有免费配额,平台 SA 兜底实际不花钱。

    传 allow_platform_fallback=True → 即使生产鉴权模式,user 没 BYOK SA 也允许
    走 rpg/vertex_sa.json 平台共享 SA(跟 LLM 的严格 BYOK 区分对待)。
    """
    cache_key = f"client:{user_id}"
    if cache_key in _VERTEX_CLIENT_CACHE:
        return _VERTEX_CLIENT_CACHE[cache_key]
    try:
        from google import genai

        from core.vertex_sa import load_sa_credentials

        credentials, project_id = load_sa_credentials(user_id, allow_platform_fallback=True)
        if credentials is None or project_id is None:
            log.warning("[embedding] no Vertex SA available (user_id=%s)", user_id)
            _VERTEX_CLIENT_CACHE[cache_key] = None
            return None
        # Vertex AI text-embedding 走 location='us-central1' 比 global 稳定
        client = genai.Client(
            vertexai=True, project=project_id, location="us-central1",
            credentials=credentials,
        )
        _VERTEX_CLIENT_CACHE[cache_key] = client
        sa_src = f"user={user_id}" if user_id else "global"
        log.debug("[embedding] vertex client init ok (SA: %s, project=%s)", sa_src, project_id)
        return client
    except Exception as e:
        log.warning("[embedding] vertex client init failed: %s", e)
        _VERTEX_CLIENT_CACHE[cache_key] = None
        return None


# ---------------------------------------------------------------------------
# Provider dispatch
# ---------------------------------------------------------------------------

def _embed_via_vertex(model: str, texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT", user_id: int | None = None) -> list[list[float]] | None:
    """调 Vertex genai SDK。model 为空时回退 DEFAULT_EMBED_MODEL。user_id 用于 BYOK SA 优先链。"""
    client = _get_vertex_client(user_id=user_id)
    if client is None:
        return None
    try:
        from google.genai import types
        resp = client.models.embed_content(
            model=model or DEFAULT_EMBED_MODEL,
            contents=texts,
            config=types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=EMBED_DIM,
            ),
        )
        return [list(e.values) for e in resp.embeddings]
    except Exception as e:
        log.warning("[embedding] vertex embed failed (%d items): %s", len(texts), e)
        return None


def _embed_via_openai(model: str, api_key: str, texts: list[str], base_url: str = "") -> list[list[float]] | None:
    """OpenAI 兼容 embeddings API。base_url 为空则走官方 https://api.openai.com/v1。"""
    import json as _json
    import urllib.error
    import urllib.request
    effective_url = (base_url.rstrip("/") if base_url else "https://api.openai.com/v1") + "/embeddings"
    payload = _json.dumps({"model": model, "input": texts, "encoding_format": "float"}).encode()
    req = urllib.request.Request(
        effective_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = _json.loads(resp.read())
        items = sorted(data["data"], key=lambda x: x["index"])
        return [item["embedding"] for item in items]
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        code = e.code
        # 把裸 HTTP 错误码映射成对用户有意义的描述，存 _last_openai_embed_error 供
        # embedding_preflight / embed_status 读取以便前端引导用户去 RAG 设置。
        if code == 405:
            friendly = (
                f"你配置的 embedding 中转站地址不支持 /embeddings 接口（HTTP 405 Method Not Allowed）。"
                f" 请确认 base_url 填的是支持 OpenAI embeddings API 的地址，而不是仅支持 /chat/completions 的地址。"
                f" 原始响应：{body[:120]}"
            )
        elif code == 401:
            friendly = (
                f"向量嵌入 API Key 无效或已过期（HTTP 401 Unauthorized）。"
                f" 请在「设置 → RAG / 向量模型」更新 API Key。"
                f" 原始响应：{body[:120]}"
            )
        elif code == 404:
            friendly = (
                f"向量嵌入接口地址错误（HTTP 404 Not Found）。"
                f" 请检查 base_url 是否正确，路径是否以 /v1 结尾（如 https://api.example.com/v1）。"
                f" 原始响应：{body[:120]}"
            )
        else:
            friendly = f"向量嵌入请求失败（HTTP {code}）：{body[:200]}"
        log.warning("[embedding] openai embed failed: %s %s | friendly: %s", code, body[:200], friendly)
        # 把友好描述存到模块级变量，供 embedding_preflight 在 connectivity test 时读取
        global _last_openai_embed_error
        _last_openai_embed_error = friendly
        return None
    except Exception as e:
        log.warning("[embedding] openai embed failed: %s", e)
        return None


def _embed_via_gemini(model: str, api_key: str, texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]] | None:
    """Gemini native embedContent API, avoiding OpenAI-compatible batchEmbed quota."""
    import json as _json
    import urllib.error
    import urllib.request

    if not api_key:
        log.warning("[embedding] gemini api_id but no api_key")
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
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
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
        return None
    except Exception as e:
        log.warning("[embedding] gemini embed failed: %s", e)
        return None


def _embed_via_cohere(model: str, api_key: str, texts: list[str]) -> list[list[float]] | None:
    """Cohere embed API v2。"""
    try:
        import cohere  # type: ignore
        co = cohere.Client(api_key)
        resp = co.embed(texts=texts, model=model, input_type="search_document")
        return [list(e) for e in resp.embeddings]
    except ImportError:
        log.warning("[embedding] cohere SDK not installed; pip install cohere")
        return None
    except Exception as e:
        log.warning("[embedding] cohere embed failed: %s", e)
        return None


def _embed_provider_dispatch(
    api_id: str,
    model: str,
    api_key: str,
    texts: list[str],
    base_url: str = "",
    task_type: str = "RETRIEVAL_DOCUMENT",
    user_id: int | None = None,
) -> list[list[float]] | None:
    """根据 api_id 分发到对应 provider SDK。不识别 → 降级 vertex + warn。
    user_id 传给 Vertex 路径以走 BYOK SA 优先链。
    """
    if api_id in _VERTEX_API_IDS:
        return _embed_via_vertex(model, texts, task_type=task_type, user_id=user_id)
    if api_id in _OPENAI_API_IDS:
        if not api_key:
            log.warning("[embedding] openai api_id but no api_key; falling back to vertex")
            return _embed_via_vertex(model or DEFAULT_EMBED_MODEL, texts, task_type=task_type, user_id=user_id)
        return _embed_via_openai(model, api_key, texts, base_url=base_url)
    if api_id in _GEMINI_API_IDS:
        return _embed_via_gemini(model, api_key, texts, task_type=task_type)
    if api_id in _COHERE_API_IDS:
        if not api_key:
            log.warning("[embedding] cohere api_id but no api_key; falling back to vertex")
            return _embed_via_vertex(DEFAULT_EMBED_MODEL, texts, task_type=task_type, user_id=user_id)
        return _embed_via_cohere(model, api_key, texts)
    log.warning("[embedding] unknown api_id=%r; falling back to vertex", api_id)
    return _embed_via_vertex(DEFAULT_EMBED_MODEL, texts, task_type=task_type, user_id=user_id)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def _platform_fallback_config() -> tuple[str, str, str, str]:
    """读 EMBED_* env 平台配置 (admin 兜底 + 内部 cron 用)。"""
    return _normalize_platform_embed_config(
        DEFAULT_EMBED_API_ID,
        os.environ.get("EMBED_MODEL", DEFAULT_EMBED_MODEL),
        os.environ.get("EMBED_API_KEY", ""),
        os.environ.get("EMBED_BASE_URL", ""),
    )


def _embed_with_admin_fallback(
    texts: list[str], user_id: int | None,
    task_type: str = "RETRIEVAL_DOCUMENT",
) -> tuple[list[list[float]] | None, str]:
    """task: admin 用户的 embedder 兜底逻辑。

    返回 (vecs, source)。source ∈ {'user', 'platform_fallback', 'failed'}
    让上层(connectivity test / log)能知道当前走哪条路。

    流程:
    1. 先 try user 自配 (或 admin 平台兜底,由 _resolve_embed_config 决定)
    2. 失败 + user 是 admin → retry 平台 EMBED_* env(防 user 配的 vertex 不可用)
    3. 仍失败 → return None
    """
    api_id, model, api_key, base_url = _resolve_embed_config(user_id)
    if api_key or api_id in _VERTEX_API_IDS:  # vertex 不用 api_key,看 SA
        vecs = _embed_provider_dispatch(api_id, model, api_key, texts, base_url=base_url, task_type=task_type, user_id=user_id)
        if vecs:
            return vecs, "user"

    # admin fallback: 即使 user 配了但调用失败,自动切平台兜底
    if user_id and _is_admin(user_id):
        plat_api, plat_model, plat_key, plat_base = _platform_fallback_config()
        if plat_key or plat_api in _VERTEX_API_IDS:
            log.info("[embedding-only] privileged user=%s (admin/vip): RAG fallback to platform EMBED_API_KEY (Gemini API,**非** LLM,LLM 严格 BYOK 不会兜底)", user_id)
            vecs = _embed_provider_dispatch(plat_api, plat_model, plat_key, texts, base_url=plat_base, task_type=task_type, user_id=None)
            if vecs:
                return vecs, "platform_fallback"
    return None, "failed"


def _embed_batch(texts: list[str], user_id: int | None = None) -> list[list[float]] | None:
    """调 embedding provider,返向量列表。失败返 None。
    user_id 非 None 时走 BYOK 优先链 + admin fallback;None 走系统默认。
    """
    if not texts:
        return []
    vecs, _source = _embed_with_admin_fallback(texts, user_id)
    return vecs


def embedding_preflight(user_id: int | None) -> dict[str, Any]:
    """Return user-facing readiness for the configured embedding provider.

    扩展逻辑:
    - 普通"没配 Key"走旧逻辑,返 needs_credentials=True 引导去设置。
    - openai_compat provider 有 Key 但上次实际调用失败(e.g. 405/401/404)时,
      把 _last_openai_embed_error 里的友好描述带进 hint,让前端能显示人话
      而不是技术错误码,并附上"去 RAG 设置检查"按钮所需的 settings_hash。
    """
    api_id, model, api_key, _base_url = _resolve_embed_config(user_id)
    credential_api_id = "AgentPlatform" if api_id in _VERTEX_API_IDS else api_id
    provider_ok = (
        (_get_vertex_client(user_id=user_id) is not None)
        if api_id in _VERTEX_API_IDS
        else bool(api_key)
    )
    if provider_ok:
        # 有 Key/SA,但如果 openai_compat 上次失败了,把友好描述当 warning 带出
        # ok=True 不拦截 rebuild,只给前端额外 hint 显示
        base = {
            "ok": True,
            "api_id": api_id,
            "model": model,
            "credential_api_id": credential_api_id,
        }
        if api_id in _OPENAI_API_IDS and _last_openai_embed_error:
            base["last_error_hint"] = _last_openai_embed_error
            base["settings_hash"] = "settings-models"
        return base
    if api_id in _VERTEX_API_IDS:
        error = "未配置 Agent Platform / Vertex SA JSON,无法建立向量索引"
        hint = "请在「设置 → API 设置」上传 Agent Platform 的 Service Account JSON。"
    else:
        error = f"未配置 {api_id} embedding API Key,无法建立向量索引"
        hint = (
            "请在「设置 → RAG / 向量模型」添加向量嵌入模型对应的 API Key。"
            " 注意：向量嵌入需要独立配置，与主 LLM Key 无关。"
        )
    return {
        "ok": False,
        "api_id": api_id,
        "model": model,
        "credential_api_id": credential_api_id,
        "code": "credentials_required",
        "error_key": "credentials_required",
        "needs_credentials": True,
        "settings_hash": "settings-models",
        "error": error,
        "hint": hint,
    }


def embed_query(
    text: str,
    user_id: int | None = None,
    force_api_id: str | None = None,
    force_model: str | None = None,
) -> str | None:
    """task 51 / P0-fix: query 文本 → 768 维向量字符串。
    `_search._embed_query` 的 production 实现。失败返 None 自动 fallback ILIKE。

    优先级链：
      1. force_api_id + force_model（召回路径：必须与建库时的 (api_id, model) 完全一致）
      2. user_id BYOK 配置（ad-hoc query / admin 工具）
      3. 系统默认 vertex_ai + text-embedding-004
    """
    text = (text or "").strip()
    if not text:
        return None
    if force_api_id and force_model:
        # 严格锁定建库时的 provider（召回侧强制路径,不走 admin fallback,
        # 因为换 provider 会让向量维度不匹配,反而召回不出来)
        _, _, api_key, base_url = _resolve_embed_config(user_id)
        api_id, model = force_api_id, force_model
        vecs = _embed_provider_dispatch(api_id, model, api_key, [text], base_url=base_url, task_type="RETRIEVAL_QUERY", user_id=user_id)
    else:
        # 常规路径:走 admin fallback(user 自配失败时 admin 自动切平台)
        vecs, _ = _embed_with_admin_fallback([text], user_id, task_type="RETRIEVAL_QUERY")
    if not vecs:
        log.warning("[embedding] embed_query returned no vectors")
        return None
    vec = vecs[0]
    # pgvector 接受 "[v1,v2,...]" 字符串
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


def _vec_literal(v: list[float]) -> str:
    """list[float] → pgvector "[..]" 字面量。"""
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def embed_status(script_id: int) -> dict[str, Any]:
    """查询某剧本的 embedding 进度。"""
    from ..db import connect
    with connect() as db:
        chunks_total = db.execute(
            "select count(*) as c from document_chunks where script_id = %s",
            (script_id,),
        ).fetchone()["c"]
        chunks_done = db.execute(
            "select count(*) as c from document_chunks where script_id = %s and embedding_vec is not null",
            (script_id,),
        ).fetchone()["c"]
        # v28: 多态后 embed 进度只统计 NPC 行(PC/persona 不参与剧本检索嵌入)
        cards_total = db.execute(
            "select count(*) as c from character_cards where script_id = %s and card_type = 'npc'",
            (script_id,),
        ).fetchone()["c"]
        cards_done = db.execute(
            "select count(*) as c from character_cards "
            "where script_id = %s and card_type = 'npc' and embedding_vec is not null",
            (script_id,),
        ).fetchone()["c"]
        wb_total = db.execute(
            "select count(*) as c from worldbook_entries where script_id = %s",
            (script_id,),
        ).fetchone()["c"]
        wb_done = db.execute(
            "select count(*) as c from worldbook_entries where script_id = %s and embedding_vec is not null",
            (script_id,),
        ).fetchone()["c"]
    return {
        "running": _EMBED_QUEUE_RUNNING.get(script_id, False),
        "chunks": {"done": chunks_done, "total": chunks_total},
        "cards": {"done": cards_done, "total": cards_total},
        "worldbook": {"done": wb_done, "total": wb_total},
        "model": EMBED_MODEL,
        "dim": EMBED_DIM,
    }


def _embed_chunks_loop(script_id: int, user_id: int) -> None:
    """后台线程:遍历 document_chunks 分批调 Vertex,写 embedding_vec。

    P0:整个函数 try/finally 包裹,保证 _EMBED_QUEUE_RUNNING flag 总被清,
    daemon thread 异常死亡 / backend 重启后下次 embed_script 不会卡在
    already_running 状态。
    """
    log.info("[embedding] start chunks: script_id=%s user=%s", script_id, user_id)
    try:
        _embed_chunks_loop_inner(script_id, user_id)
    except Exception as exc:
        log.warning("[embedding] loop crashed for script %s: %s", script_id, exc, exc_info=True)
    finally:
        _EMBED_QUEUE_RUNNING[script_id] = False
        log.info("[embedding] done script_id=%s (flag cleared)", script_id)


def _embed_chunks_loop_inner(script_id: int, user_id: int) -> None:
    """实际工作循环 — 由 _embed_chunks_loop 包裹保证 flag 清理"""
    from ..db import connect

    # P0-fix: 拆书开始时立即将 (api_id, model) 绑定到 scripts 表，
    # 保证召回时能读到确定的向量空间配置。
    _bind_api_id, _bind_model, _, _ = _resolve_embed_config(user_id)
    try:
        with connect() as db:
            db.execute(
                "update scripts set embed_api_id = %s, embed_model = %s where id = %s",
                (_bind_api_id, _bind_model, script_id),
            )
        log.info(
            "[embedding] bound embed meta to script %s: api_id=%s model=%s",
            script_id, _bind_api_id, _bind_model,
        )
        # 使新 meta 立即生效（进程内 cache 失效）
        from platform_app.knowledge._search import _SCRIPT_EMBED_META_CACHE
        _SCRIPT_EMBED_META_CACHE.pop(script_id, None)
    except Exception as exc:
        log.warning("[embedding] failed to bind embed meta to script %s: %s", script_id, exc)

    _consecutive_fails = 0
    while True:
        with connect() as db:
            # 拉一批未 embed 的(只拉 id+content,内存友好)
            rows = db.execute(
                "select id, content from document_chunks "
                "where script_id = %s and embedding_vec is null "
                "order by chapter_index, chunk_index limit %s",
                (script_id, BATCH_SIZE),
            ).fetchall()
        if not rows:
            break

        texts = [r["content"][:PER_CHUNK_CHAR_LIMIT] for r in rows]  # 见模块顶 PER_CHUNK_CHAR_LIMIT 注释
        vecs = _embed_batch(texts, user_id=user_id)
        if vecs is None:
            _consecutive_fails += 1
            if _consecutive_fails >= _MAX_EMBED_BATCH_RETRIES:
                # 连续失败达上限:大概率 provider 永久故障(坏 key/配额/模型下线)。
                # 抛出 → _embed_chunks_loop 优雅收尾(清 flag、线程退出),不再无限 spin。
                raise RuntimeError(
                    f"embedding batch 连续失败 {_consecutive_fails} 次,放弃 script {script_id}"
                    f"(剩余 chunk 留 null 待修复 provider 后重试)"
                )
            log.warning("[embedding] batch failed (%d/%d), sleeping 30s then retry",
                        _consecutive_fails, _MAX_EMBED_BATCH_RETRIES)
            time.sleep(30)
            continue
        _consecutive_fails = 0  # 成功一批即重置连续失败计数(仅对持续性故障熔断)
        if len(vecs) != len(rows):
            # 行数不匹配(供应商异常)。原来直接 break → 整个 script 剩余 chunk 永不 embed
            # 且静默(RAG 召回残缺)。改为:写入可匹配的前 N 对(保证推进),再继续下一批;
            # 0 匹配才放弃(避免死循环)。
            _n = min(len(vecs), len(rows))
            log.error("[embedding] vec count mismatch: got %d expected %d (script_id=%s) — 写入前 %d 对后继续",
                      len(vecs), len(rows), script_id, _n)
            if _n == 0:
                break
            with connect() as db:
                for r, v in zip(rows[:_n], vecs[:_n], strict=False):
                    db.execute(
                        "update document_chunks set embedding_vec = %s::vector, embedded_at = now() where id = %s",
                        (_vec_literal(v), r["id"]),
                    )
            continue

        with connect() as db:
            for r, v in zip(rows, vecs, strict=False):
                db.execute(
                    "update document_chunks set embedding_vec = %s::vector, embedded_at = now() where id = %s",
                    (_vec_literal(v), r["id"]),
                )
        log.info("[embedding] chunks +%d (script_id=%s)", len(rows), script_id)

    # BUG-1: 旧 task 52 在此用全文 LIKE 回填 character_cards/worldbook_entries 的
    # first_chapter / last_seen_chapter —— 但那两列全库从未建过,整段 SQL 恒抛
    # "column does not exist",被 try/except 静默吞,从未生效过。
    # 进度过滤已统一到 first_revealed_chapter:character_cards 由 extraction/resolve 写
    # (v28 _sync upsert),worldbook_entries 由 migration v53 补列 + 从 metadata.chapter_min
    # 回填。_search_entities 直接读 first_revealed_chapter,无需此回填。故移除死代码避免误导。

    # entity 层:character_cards
    with connect() as db:
        cards = db.execute(
            "select id, name, identity, personality, appearance from character_cards "
            "where script_id = %s and card_type = 'npc' and embedding_vec is null",
            (script_id,),
        ).fetchall()
    if cards:
        for i in range(0, len(cards), BATCH_SIZE):
            batch = cards[i:i+BATCH_SIZE]
            texts = [
                # 拼接成"角色档案",embedding 更准
                f"{c['name']}。{c.get('identity') or ''}。{(c.get('personality') or '')[:1000]}。{(c.get('appearance') or '')[:500]}"
                for c in batch
            ]
            vecs = _embed_batch(texts, user_id=user_id)
            if vecs is None:
                continue
            with connect() as db:
                for c, v in zip(batch, vecs, strict=False):
                    db.execute(
                        "update character_cards set embedding_vec = %s::vector, embedded_at = now() where id = %s",
                        (_vec_literal(v), c["id"]),
                    )
        log.info("[embedding] cards +%d (script_id=%s)", len(cards), script_id)

    # entity 层:worldbook_entries
    with connect() as db:
        wb = db.execute(
            "select id, title, content from worldbook_entries "
            "where script_id = %s and embedding_vec is null",
            (script_id,),
        ).fetchall()
    if wb:
        for i in range(0, len(wb), BATCH_SIZE):
            batch = wb[i:i+BATCH_SIZE]
            texts = [
                f"{w['title']}。{(w.get('content') or '')[:2000]}"
                for w in batch
            ]
            vecs = _embed_batch(texts, user_id=user_id)
            if vecs is None:
                continue
            with connect() as db:
                for w, v in zip(batch, vecs, strict=False):
                    db.execute(
                        "update worldbook_entries set embedding_vec = %s::vector, embedded_at = now() where id = %s",
                        (_vec_literal(v), w["id"]),
                    )
        log.info("[embedding] worldbook +%d (script_id=%s)", len(wb), script_id)


def embed_script(user_id: int, script_id: int) -> dict[str, Any]:
    """触发后台 embedding。fire-and-forget,前端 poll embed_status。

    安全:要求 script.owner_id == user_id 才能触发。
    幂等:已有 embedding_vec 的行跳过,可重复调。
    """
    from ..db import connect, init_db
    init_db()
    with connect() as db:
        row = db.execute(
            "select id from scripts where id = %s and owner_id = %s",
            (script_id, user_id),
        ).fetchone()
    if not row:
        raise ValueError("无权访问该剧本")
    if _EMBED_QUEUE_RUNNING.get(script_id):
        return {"ok": True, "already_running": True, "status": embed_status(script_id)}
    # 检查 embedding provider 是否可用：生产鉴权模式必须有用户 BYOK/API key。
    preflight = embedding_preflight(user_id)
    if not preflight.get("ok"):
        return preflight
    _EMBED_QUEUE_RUNNING[script_id] = True
    threading.Thread(target=_embed_chunks_loop, args=(script_id, user_id), daemon=True).start()
    return {"ok": True, "status": embed_status(script_id)}
