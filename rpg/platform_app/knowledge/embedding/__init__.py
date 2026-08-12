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

---
包结构(拆包 2026-07,纯机械搬家,行为零变化;原 embedding.py 单文件 1038 行):
- 本 __init__ = 公共层 + orchestration + OpenAI 兼容通道 + 共享错误态。测试在包命名空间上
  patch `_embed_via_*` / `_resolve_embed_config` 等并期望本层内部调用看到 patch,故这些
  函数逐字定义在此(globals 在门面解析)。OpenAI 通道因与 `_last_openai_embed_error`
  可变全局 + `embedding_preflight` 读方耦合,亦留在本层同居。
- `_base` = 共享常量/维度/配置低层谓词(叶子)
- `_vertex` / `_gemini` / `_cohere` = 各供应商通道
- `_writer` = 后台 batch embedding 作业 + 运行锁(末尾导入,构成受控有序循环)
"""
from __future__ import annotations

# ruff: noqa: F401
# 门面 re-export:从 _base/_vertex/_gemini/_cohere/_writer 导入的诸多名字有的仅供 re-export
# 与测试 patch(如 _embed_via_* / _GEO_BAN_CACHE / _EMBED_QUEUE_RUNNING),ruff 会误报 F401。
import os
import time
from typing import Any

from ._base import (
    _EMBED_SECS_PER_TEXT,
    _MAX_EMBED_BATCH_RETRIES,
    _NO_EMBEDDING_PROVIDERS,
    _PLATFORM_FALLBACK_ROLES,
    BATCH_SIZE,
    DEFAULT_EMBED_MODEL,
    EMBED_DIM,
    EMBED_MODEL,
    PER_CHUNK_CHAR_LIMIT,
    _embed_req_timeout,
    _is_admin,
    _is_google_generative_openai_base,
    _vec_literal,
    has_platform_fallback_role,
    log,
    provider_lacks_embedding,
)
from ._cohere import _embed_via_cohere
from ._gemini import (
    _GEO_BAN_CACHE,
    _GEO_BAN_CHANNEL_GEMINI_NATIVE,
    _GEO_BAN_TTL,
    _embed_via_gemini,
    _geo_ban_active,
    _geo_ban_mark,
    _is_geo_ban_error,
    _native_gemini_embed_model,
)
from ._vertex import _VERTEX_CLIENT_CACHE, _embed_via_vertex, _get_vertex_client

# 系统默认 embedding 配置(env 可覆盖,用户 BYOK 优先于 env)。
# 注:DEFAULT_EMBED_MODEL / EMBED_MODEL 住 _base;DEFAULT_EMBED_API_ID 留本层——测试会 patch
# EMBED_API_ID env 后 reload 本包,须由本层(被 reload)重读 env 才生效。
DEFAULT_EMBED_API_ID = os.environ.get("EMBED_API_ID", "vertex_ai")

# 最近一次 _embed_via_openai 失败的友好描述(405/401/404 等),供前端引导用户去 RAG 设置用。
_last_openai_embed_error: str = ""

_VERTEX_API_IDS = {"vertex", "google", "vertex_ai"}
_OPENAI_API_IDS = {"openai", "openai_compat"}
_GEMINI_API_IDS = {"gemini", "google_gemini"}
_COHERE_API_IDS = {"cohere"}


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
                if not base_url:
                    # 普通用户禁止自填 base_url(SSRF 闸,见 user_credentials.set_credential),
                    # 从 catalog 取该 provider 官方 base(如 dashscope compatible-mode endpoint),
                    # 否则 _embed_via_openai 会误连 api.openai.com。
                    try:
                        from model_registry import default_api_for
                        base_url = (default_api_for(api_id) or {}).get("base_url", "") or ""
                    except Exception:
                        base_url = ""
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


# ---------------------------------------------------------------------------
# Provider dispatch
# ---------------------------------------------------------------------------

def _embed_via_openai(model: str, api_key: str, texts: list[str], base_url: str = "") -> list[list[float]] | None:
    """OpenAI 兼容 embeddings API。base_url 为空则走官方 https://api.openai.com/v1。

    请求 dimensions=EMBED_DIM,让 text-embedding-3 / qwen text-embedding-v3 等可降维模型输出
    与 DB 向量列(默认 768)一致。模型不支持 dimensions(如 ada-002)时会 400 → 自动去掉
    dimensions 重试一次。
    """
    import json as _json
    import urllib.error
    import urllib.request

    from core.outbound import safe_urlopen  # SSRF: 不跟随重定向 + use-time 重解析 pin IP
    from core.outbound_ua import outbound_user_agent
    global _last_openai_embed_error
    effective_url = (base_url.rstrip("/") if base_url else "https://api.openai.com/v1") + "/embeddings"

    # BUGFIX: 不同 OpenAI 兼容 provider 对单请求 input 数组条数上限不同。DashScope(阿里 dashscope/
    # 百炼)text-embedding 限 ≤10,而上游按 BATCH_SIZE=30 喂入 → "400 batch size ... not larger than 10"。
    # 按 base_url 推断 provider 上限,超限就拆子批保序拼接(对 OpenAI/SiliconFlow 等大上限 provider 不变)。
    _bl = (base_url or "").lower()
    _max_batch = 10 if ("dashscope" in _bl or "aliyun" in _bl or "bailian" in _bl) else 64
    if len(texts) > _max_batch:
        out: list[list[float]] = []
        for _i in range(0, len(texts), _max_batch):
            sub = _embed_via_openai(model, api_key, texts[_i:_i + _max_batch], base_url)
            if sub is None:
                return None
            out.extend(sub)
        return out

    # SEC(H-4): base_url 攻击者端点可用 301 把携 Authorization 的请求跳到 169.254.169.254 / 内网,
    # 且 DNS rebinding 可绕过写时 _validate_base_url。统一走 core.outbound.safe_urlopen
    # (不跟随重定向 + use-time 重解析并 pin 到已校验 IP)。
    def _post(with_dim: bool) -> list[list[float]]:
        body = {"model": model, "input": texts, "encoding_format": "float"}
        if with_dim and EMBED_DIM:
            body["dimensions"] = EMBED_DIM
        req = urllib.request.Request(
            effective_url, data=_json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                # 中转站多挂 Cloudflare,WAF 按默认 urllib UA 拦(实测 403 error 1010)→ 用浏览器 UA 穿透。
                # 聊天/生图/拉模型早已统一走 core.outbound_ua,此前唯独漏了 embedding 路径 → 向量索引生成不了。
                "User-Agent": outbound_user_agent(),
            },
            method="POST",
        )
        with safe_urlopen(req, timeout=_embed_req_timeout(len(texts))) as resp:
            data = _json.loads(resp.read())
        items = sorted(data["data"], key=lambda x: x["index"])
        return [item["embedding"] for item in items]

    def _guard_dim(vecs: list[list[float]] | None) -> list[list[float]] | None:
        # 维度卫士:有的模型(如 BAAI/bge-m3 固定 1024)不支持降到 EMBED_DIM(768),会静默返回
        # 异维向量 → 既存不进 vector(768) 列(索引侧 with_vec=0),又在召回侧维度不符报错被吞 →
        # 用户「RAG 完全失效」却查不出原因。这里维度不符即「响亮失败」+ 写人话错误供前端引导换模型。
        global _last_openai_embed_error
        if vecs and EMBED_DIM and len(vecs[0]) != EMBED_DIM:
            _last_openai_embed_error = (
                f"向量嵌入模型「{model}」输出 {len(vecs[0])} 维,但系统统一用 {EMBED_DIM} 维"
                f"(该模型不支持降到 {EMBED_DIM})。请到「设置 → RAG / 向量模型」改用支持 {EMBED_DIM} 维的模型"
                f"(如 Qwen/Qwen3-Embedding-* 带降维、OpenAI text-embedding-3-*、Gemini text-embedding-004),并重新拆书。"
            )
            log.warning("[embedding] dim mismatch: model=%s got=%d want=%d → 拒绝异维向量", model, len(vecs[0]), EMBED_DIM)
            return None
        return vecs

    try:
        result = _guard_dim(_post(with_dim=bool(EMBED_DIM)))
        if result is not None:
            _last_openai_embed_error = ""  # 仅真正成功才清 sticky 错误(维度不符已写错误,别清掉)
        return result
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        code = e.code
        # 带 dimensions 被 400 拒(模型不支持降维)→ 去掉 dimensions 重试一次
        if code == 400 and EMBED_DIM:
            try:
                result = _guard_dim(_post(with_dim=False))  # 去 dimensions 重试后,模型可能吐回原生维度 → 仍须卡维
                if result is not None:
                    _last_openai_embed_error = ""  # 仅真正成功才清错误(维度不符已写错误,别清掉)
                return result
            except urllib.error.HTTPError as e2:
                body = e2.read().decode(errors="replace"); code = e2.code
            except Exception as e2:
                log.warning("[embedding] openai embed retry-no-dim failed: %s", e2)
                return None
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
            # 区分「模型不存在/无权访问」(模型名问题)与「路径不对」(base_url 问题)——
            # 豆包/火山方舟回的是 Model.NotFound(地址 /api/v3 本就对),旧文案一律说「路径要以 /v1 结尾」
            # 会误导用户把 /v3 改成 /v1 反而搞坏(用户反馈)。
            _bl = body.lower()
            _model_404 = any(m in _bl for m in (
                "does not exist", "do not have access", "model.notfound",
                "model_not_found", "no such model", "model not found", "modelnotfound",
            ))
            if _model_404:
                friendly = (
                    f"向量嵌入模型「{model}」不存在或你的账号无权访问(HTTP 404)。"
                    f"这是模型名/权限问题,不是地址问题——请勿改 base_url;到「设置 → RAG / 向量模型」"
                    f"换成该提供商真实开通的嵌入模型(火山方舟 doubao-embedding-* / OpenAI text-embedding-3-* / "
                    f"Gemini text-embedding-004),或到提供商控制台为该模型开通权限。"
                    f" 原始响应：{body[:160]}"
                )
            else:
                friendly = (
                    f"向量嵌入接口地址(base_url)错误(HTTP 404 Not Found)。"
                    f"请确认路径与提供商匹配:OpenAI/中转站通常以 /v1 结尾、火山方舟(豆包)以 /api/v3 结尾、"
                    f"Gemini 兼容以 /v1beta/openai 结尾。"
                    f" 原始响应：{body[:120]}"
                )
        else:
            friendly = f"向量嵌入请求失败（HTTP {code}）：{body[:200]}"
        log.warning("[embedding] openai embed failed: %s %s | friendly: %s", code, body[:200], friendly)
        # 把友好描述存到模块级变量(global 已在函数顶部声明),供 embedding_preflight 读取
        _last_openai_embed_error = friendly
        return None
    except Exception as e:
        log.warning("[embedding] openai embed failed: %s", e)
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
    if api_id in _GEMINI_API_IDS:
        return _embed_via_gemini(model, api_key, texts, task_type=task_type)
    if api_id in _COHERE_API_IDS:
        if not api_key:
            log.warning("[embedding] cohere api_id but no api_key; falling back to vertex")
            return _embed_via_vertex(DEFAULT_EMBED_MODEL, texts, task_type=task_type, user_id=user_id)
        return _embed_via_cohere(model, api_key, texts)
    # OpenAI 及任何 OpenAI 兼容 provider(openai / openai_compat / dashscope / siliconflow / ...):
    # 走 /embeddings。dashscope 等 api_id 不在字面集合,但只要带 key + base_url 就按 OpenAI
    # 兼容协议处理(de-facto 标准,base_url 已由 _resolve_embed_config 从 catalog 取到)。
    if api_id in _OPENAI_API_IDS or api_key:
        if not api_key:
            log.warning("[embedding] openai-compatible api_id=%r but no api_key; falling back to vertex", api_id)
            return _embed_via_vertex(model or DEFAULT_EMBED_MODEL, texts, task_type=task_type, user_id=user_id)
        return _embed_via_openai(model, api_key, texts, base_url=base_url)
    log.warning("[embedding] unknown api_id=%r and no api_key; falling back to vertex", api_id)
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
        if (api_id in _OPENAI_API_IDS or api_key) and _last_openai_embed_error:
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
        # BUG-A fix: 必须按 force_api_id 取 key/base_url,而非用户当前选中的 provider。
        # 用户切换 embed provider 后,_resolve_embed_config(user_id) 会返回新 provider 的
        # key → 发到旧 provider 端点 → 401/404 → 静默降级 ILIKE。
        api_id, model = force_api_id, force_model
        try:
            from model_registry import default_api_for
            from platform_app.user_credentials import resolve_api_key
            _cred = resolve_api_key(user_id, force_api_id, env_fallback="")
            api_key = _cred.get("key", "")
            base_url = _cred.get("base_url_override", "") or (default_api_for(force_api_id) or {}).get("base_url", "") or ""
        except Exception:
            # 极端情况(catalog 不可用):回退到当前用户 config 的 key/base_url 尽力而为
            _, _, api_key, base_url = _resolve_embed_config(user_id)
        vecs = _embed_provider_dispatch(api_id, model, api_key, [text], base_url=base_url, task_type="RETRIEVAL_QUERY", user_id=user_id)
    else:
        # 常规路径:走 admin fallback(user 自配失败时 admin 自动切平台)
        vecs, _ = _embed_with_admin_fallback([text], user_id, task_type="RETRIEVAL_QUERY")
    if not vecs:
        log.warning("[embedding] embed_query returned no vectors")
        return None
    vec = vecs[0]
    # pgvector 接受 "[v1,v2,...]" 字符串。单一真源 _vec_literal(模块级,call-time 解析)。
    return _vec_literal(vec)


# ---------------------------------------------------------------------------
# 后台 batch embedding 作业(写库侧)。放最后导入:_writer 需要本层已定义的
# _resolve_embed_config / _embed_batch / embedding_preflight —— 到这里它们均已就绪,
# 构成一处受控的有序循环导入(仅本包内)。
# ---------------------------------------------------------------------------
from ._writer import (  # noqa: E402
    _EMBED_QUEUE_RUNNING,
    _EMBED_REDIS_PREFIX,
    _EMBED_REDIS_TTL,
    _embed_chunks_loop,
    _embed_chunks_loop_inner,
    _embed_is_running,
    _embed_redis_acquire,
    _embed_redis_release,
    embed_script,
    embed_status,
)

__all__ = [
    # 公共入口
    "embed_query",
    "embed_script",
    "embed_status",
    "embedding_preflight",
    "provider_lacks_embedding",
    "has_platform_fallback_role",
    # 常量(外部/测试引用)
    "EMBED_MODEL",
    "EMBED_DIM",
    "DEFAULT_EMBED_MODEL",
    "DEFAULT_EMBED_API_ID",
]
