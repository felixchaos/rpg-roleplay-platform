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

# 已知【没有 embedding 接口】的 chat-only provider:选成嵌入器必然 404/失败(记忆 project_rag_embedder:
# Anthropic/DeepSeek 无 embedding)。用户曾把 deepseek 选成嵌入器(embed.api_id=deepseek + 默认模型
# text-embedding-004 = Google 模型)→ 每批 404、重试 5 次(~2.5 分钟)才放弃、导入的小说 RAG 坏掉。
# 各层校验都漏了(picker 只按模型名 heuristic、preflight 只查有无该 provider 凭据),这里做后端权威闸。
_NO_EMBEDDING_PROVIDERS = frozenset({"deepseek", "anthropic", "moonshot"})


def provider_lacks_embedding(api_id: str, base_url: str = "") -> bool:
    """该 provider 是否没有 embedding 接口(选成嵌入器必然失败)。按 api_id + base_url host 双判。"""
    a = (api_id or "").strip().lower()
    if any(k in a for k in _NO_EMBEDDING_PROVIDERS):
        return True
    h = (base_url or "").strip().lower()
    return any(k in h for k in ("deepseek.com", "api.anthropic.com", "moonshot"))
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
# 嵌入请求超时按「本批条数」自适应:本地/自部署慢模型每条 embedding 可能 2-3s(群反馈 abci
# 实测 2.5s/条),一批 30/64 条就要 75-160s,**原硬编码 60s 必超时** → 模型返 200 但平台已放弃 →
# 向量索引永远卡在 0(实测现象:模型日志收到几条请求后「半天才增加一条」)。按条数 × 每条预算
# (取 8s,覆盖到 CPU 慢模型)放大,下限 60s 不变(单条 query 仍快速失败,GM 召回热路径不受拖)。
_EMBED_SECS_PER_TEXT = 8.0


def _embed_req_timeout(n_texts: int) -> float:
    return max(60.0, float(n_texts) * _EMBED_SECS_PER_TEXT)
# 进程内 cache,避免 ChatPipeline 每次 _embed_query 都重新 import vertex SDK
_VERTEX_CLIENT_CACHE: dict[str, Any] = {}
_EMBED_QUEUE_RUNNING: dict[int, bool] = {}  # script_id → 是否在跑(进程内降级标志,多 worker 各自独立)

# Redis key 前缀,用于跨进程去重;TTL 略大于预期最长 embed 时长,兼做宕机自动解锁。
_EMBED_REDIS_PREFIX = "rpg:embed_running:"
_EMBED_REDIS_TTL = 3600  # 1 小时,足够大,宕机后 Redis 自动释放锁


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


def _embed_redis_acquire(script_id: int) -> bool:
    """跨进程去重:用 Redis SETNX+EXPIRE 申请运行权。

    返回 True 表示本进程/worker 拿到运行权;False 表示已有其它 worker 在跑该 script。
    Redis 不可用时 → 优雅降级,返回 True(进程内标志兜底,单 worker 语义不变)。
    """
    try:
        from redis_bus import get_sync_client
        r = get_sync_client()
        if r is None:
            return True  # Redis 未配或不可达,降级到进程内标志
        key = f"{_EMBED_REDIS_PREFIX}{script_id}"
        acquired = r.set(key, "1", nx=True, ex=_EMBED_REDIS_TTL)
        return bool(acquired)
    except Exception as exc:
        log.warning("[embedding] redis acquire failed (graceful degradation): %s", exc)
        return True  # 降级:允许本进程继续,进程内标志兜底


def _embed_redis_release(script_id: int) -> None:
    """释放 Redis 运行权锁(finally 保证调用)。Redis 不可达时静默忽略。"""
    try:
        from redis_bus import get_sync_client
        r = get_sync_client()
        if r is None:
            return
        r.delete(f"{_EMBED_REDIS_PREFIX}{script_id}")
    except Exception as exc:
        log.warning("[embedding] redis release failed (TTL will auto-expire): %s", exc)


def _embed_is_running(script_id: int) -> bool:
    """检查某 script 是否有任意 worker 在跑 embedding(跨进程感知)。

    优先查 Redis;Redis 不可达时退回本进程内标志。
    """
    try:
        from redis_bus import get_sync_client
        r = get_sync_client()
        if r is not None:
            return bool(r.exists(f"{_EMBED_REDIS_PREFIX}{script_id}"))
    except Exception:
        pass
    return _EMBED_QUEUE_RUNNING.get(script_id, False)
# 最近一次 _embed_via_openai 失败的友好描述(405/401/404 等),供前端引导用户去 RAG 设置用。
_last_openai_embed_error: str = ""

# 向后兼容:保留 EMBED_MODEL 常量名(外部模块如 extract/ 直接引用它)
EMBED_MODEL = DEFAULT_EMBED_MODEL


# 平台兜底资格角色(享受平台共享 embedder / Vertex SA 兜底)。
# 单一来源:与「纯 admin 管理权」(role == 'admin',见 api._deps.is_admin)是不同职责,资格集合不同,绝不跨用。
_PLATFORM_FALLBACK_ROLES = {"admin", "vip_user"}
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


def has_platform_fallback_role(user_or_id) -> bool:
    """是否拥有「平台兜底资格」(role ∈ _PLATFORM_FALLBACK_ROLES = {admin, vip_user})。

    单一谓词,消除散落的硬编码角色集。接受两种入参以省去多余 DB 往返:
      - user dict(已加载,含 'role')→ 直接读 role,不查库。
      - user_id(int / 可转 int)    → 查 users.role。
    其他用户(默认 role='user' / '')不享受,防白嫖付费 Gemini key。

    注意:这是「资格」(admin + vip_user),与「纯 admin 管理权」(role == 'admin',
    见 api._deps.is_admin)是不同职责,资格集合不同,绝不跨用。
    """
    if isinstance(user_or_id, dict):
        return (user_or_id.get("role") or "").lower() in _PLATFORM_FALLBACK_ROLES
    if not user_or_id:
        return False
    try:
        from platform_app.db import connect
        with connect() as db:
            row = db.execute("select role from users where id = %s", (int(user_or_id),)).fetchone()
        return bool(row and (row.get("role") or "").lower() in _PLATFORM_FALLBACK_ROLES)
    except Exception:
        return False


def _is_admin(user_id: int | None) -> bool:
    """检查 user_id 是否享受平台 embedder 兜底(admin 或 vip_user)。

    内部 = has_platform_fallback_role(单一来源);函数名保留向后兼容本模块多处调用。
    """
    return has_platform_fallback_role(user_id)


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


def _get_vertex_client(user_id: int | None = None):
    """返回 Vertex genai Client,按 user_id 走 BYOK 优先链。

    task: Embedder 是 RAG 必需路径(每轮 chat 都要 embed user query),平台
    为用户兜底成本($150 一次性 + $1/月 vs LLM $135-27000/月)。Vertex
    text-embedding-004 有免费配额,平台 SA 兜底实际不花钱。

    平台共享 SA 兜底**仅 admin/vip 及系统任务(user_id=None)**可用 —— 普通用户必须 BYOK
    自己的 Vertex SA(或换 OpenAI 兼容 embedding key),否则不给平台兜底。否则会变成全员
    白嫖平台的 embedding 成本(本来只想给 VIP)。用户自己的 BYOK SA 不受影响,任何用户都优先用自己的。
    """
    cache_key = f"client:{user_id}"
    if cache_key in _VERTEX_CLIENT_CACHE:
        return _VERTEX_CLIENT_CACHE[cache_key]
    try:
        from google import genai
        from core.vertex_sa import load_sa_credentials

        # 平台共享 SA 兜底仅 admin/vip(_is_admin 含 vip_user)+ 系统任务(无 user);
        # 普通用户只能用自己的 BYOK SA,拿不到平台兜底。
        allow_fb = (user_id is None) or _is_admin(user_id)
        credentials, project_id = load_sa_credentials(user_id, allow_platform_fallback=allow_fb)
        if credentials is None or project_id is None:
            log.warning("[embedding] no Vertex SA available (user_id=%s)", user_id)
            # 不缓存 None:SA 暂时不可用(文件未配/key 刷新中)时,下次请求应重试,
            # 永久缓存 None 会让本进程整个生命周期都无法恢复。
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
        # [round-4-P1] 同上:不缓存 None。原代码在此 except 把瞬时构造失败(网络超时/
        # 临时鉴权抖动)永久缓存成 None → 之后该 user_id 在本 worker 生命周期内永远拿不到
        # client、embedding 静默禁用直到重启(workers=2 时两进程可各自中毒,无自愈)。
        return None


# ---------------------------------------------------------------------------
# Provider dispatch
# ---------------------------------------------------------------------------

def _embed_via_vertex(model: str, texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT", user_id: int | None = None) -> list[list[float]] | None:
    """调 Vertex genai SDK。model 为空时回退 DEFAULT_EMBED_MODEL。user_id 用于 BYOK SA 优先链。"""
    # 关键修复:genai SDK 的 embed_content 实测对【任何】不同文本返回**完全相同**的向量
    # (768/768 维全等,与 contents 无关)→ 语义检索彻底失效(任何查询等距命中,永恒记忆 / 原著
    # RAG 都形同虚设)。而原生 REST embedContent(_embed_via_gemini)正常。允许平台兜底的场景
    # (admin/vip / 系统任务 user_id=None)且有平台 gemini key 时,优先改走原生 REST;
    # 否则退回 genai SDK(用户自己的 BYOK Vertex SA,无平台 key 可用,与原行为一致)。
    _plat_key = os.environ.get("EMBED_API_KEY", "")
    if _plat_key and ((user_id is None) or _is_admin(user_id)):
        _native = _embed_via_gemini(model, _plat_key, texts, task_type=task_type)
        if _native:
            return _native
    client = _get_vertex_client(user_id=user_id)
    if client is None:
        return None
    try:
        from google.genai import types
        resp = client.models.embed_content(
            model=model or DEFAULT_EMBED_MODEL,
            contents=texts,
            config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=EMBED_DIM),
        )
        return [list(e.values) for e in resp.embeddings]
    except Exception as e:
        log.warning("[embedding] vertex embed failed (%d items): %s", len(texts), e)
        return None


def _embed_via_openai(model: str, api_key: str, texts: list[str], base_url: str = "") -> list[list[float]] | None:
    """OpenAI 兼容 embeddings API。base_url 为空则走官方 https://api.openai.com/v1。

    请求 dimensions=EMBED_DIM,让 text-embedding-3 / qwen text-embedding-v3 等可降维模型输出
    与 DB 向量列(默认 768)一致。模型不支持 dimensions(如 ada-002)时会 400 → 自动去掉
    dimensions 重试一次。
    """
    import urllib.request
    import urllib.error
    import json as _json
    from core.outbound_ua import outbound_user_agent
    from core.outbound import safe_urlopen  # SSRF: 不跟随重定向 + use-time 重解析 pin IP
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
    import urllib.request
    import urllib.error
    import json as _json
    from core.outbound_ua import outbound_user_agent
    from core.outbound import safe_urlopen  # SSRF: 不跟随重定向 + use-time 重解析 pin IP

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
            from platform_app.user_credentials import resolve_api_key
            from model_registry import default_api_for
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
        # 知识库人物(canon)用 kb_canon_entities.embedding(vector 列,**不是** embedding_vec)。
        # 此前漏算 canon → 前端「知识库人物」卡 es['canon']=undefined → 永远 0 条·状态未知,
        # 而重做估算/任务却按同样的列正常计数(2924/已完成 N) → 卡片与重做对不上(群反馈)。
        # 与 import_pipeline 估算口径完全一致(count where embedding is not null)。
        canon_total = db.execute(
            "select count(*) as c from kb_canon_entities where script_id = %s",
            (script_id,),
        ).fetchone()["c"]
        canon_done = db.execute(
            "select count(*) as c from kb_canon_entities where script_id = %s and embedding is not null",
            (script_id,),
        ).fetchone()["c"]
    return {
        "running": _embed_is_running(script_id),
        "chunks": {"done": chunks_done, "total": chunks_total},
        "cards": {"done": cards_done, "total": cards_total},
        "worldbook": {"done": wb_done, "total": wb_total},
        "canon": {"done": canon_done, "total": canon_total},
        "model": EMBED_MODEL,
        "dim": EMBED_DIM,
    }


def _embed_chunks_loop(script_id: int, user_id: int) -> None:
    """后台线程:遍历 document_chunks 分批调 Vertex,写 embedding_vec。

    P0:整个函数 try/finally 包裹,保证 _EMBED_QUEUE_RUNNING flag 总被清,
    daemon thread 异常死亡 / backend 重启后下次 embed_script 不会卡在
    already_running 状态。
    """
    from ..db import connect
    log.info("[embedding] start chunks: script_id=%s user=%s", script_id, user_id)
    try:
        _embed_chunks_loop_inner(script_id, user_id)
    except Exception as exc:
        log.warning("[embedding] loop crashed for script %s: %s", script_id, exc, exc_info=True)
    finally:
        _EMBED_QUEUE_RUNNING[script_id] = False
        _embed_redis_release(script_id)  # 释放跨进程锁(Redis 不可达时静默忽略)
        log.info("[embedding] done script_id=%s (flag cleared)", script_id)


def _embed_chunks_loop_inner(script_id: int, user_id: int) -> None:
    """实际工作循环 — 由 _embed_chunks_loop 包裹保证 flag 清理"""
    from ..db import connect

    # P0-fix: 拆书开始时立即将 (api_id, model) 绑定到 scripts 表，
    # 保证召回时能读到确定的向量空间配置。
    _bind_api_id, _bind_model, _bind_key, _bind_base = _resolve_embed_config(user_id)
    # 权威闸:选了没有 embedding 接口的 provider(deepseek/anthropic 等)→ 快速失败 + 清晰指引,
    # 不绑坏 meta、不进 5 次 404 重试(~2.5 分钟)。这是各层校验(picker/preflight)漏掉的兜底。
    if provider_lacks_embedding(_bind_api_id, _bind_base):
        raise RuntimeError(
            f"嵌入器 provider「{_bind_api_id}」没有向量嵌入(embedding)接口,无法为 script {script_id} 建向量索引。"
            f"请在「设置 → API & 模型 → RAG 模型」改选支持 embedding 的 provider"
            f"(如 OpenAI text-embedding-3 / 阿里 dashscope / siliconflow / Vertex,或本地 bge/nomic),再重新导入。"
        )
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
        from platform_app.knowledge._search import _SCRIPT_EMBED_META_CACHE, _UNBOUND_EMBED_WARNED
        _SCRIPT_EMBED_META_CACHE.pop(script_id, None)
        # 重新绑定成功：该 script 不再是「未绑定」状态，把降频标记也一并清掉，
        # 万一将来又变回未绑定（如再次拆书失败），警告能重新提醒一次而不是永久沉默。
        _UNBOUND_EMBED_WARNED.discard(script_id)
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
                for r, v in zip(rows[:_n], vecs[:_n]):
                    db.execute(
                        "update document_chunks set embedding_vec = %s::vector, embedded_at = now() where id = %s",
                        (_vec_literal(v), r["id"]),
                    )
            continue

        with connect() as db:
            for r, v in zip(rows, vecs):
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
                for c, v in zip(batch, vecs):
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
                for w, v in zip(batch, vecs):
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
    if _embed_is_running(script_id):
        return {"ok": True, "already_running": True, "status": embed_status(script_id)}
    # 检查 embedding provider 是否可用：生产鉴权模式必须有用户 BYOK/API key。
    preflight = embedding_preflight(user_id)
    if not preflight.get("ok"):
        return preflight
    # 申请运行权(跨进程 SETNX,降级到进程内标志)。
    # 二次检查 + acquire 之间存在极小窗口,最坏情况两 worker 同时进入跑一次重复 embed;
    # embed 本身幂等(skip already-embedded rows),重复可接受。
    if not _embed_redis_acquire(script_id):
        return {"ok": True, "already_running": True, "status": embed_status(script_id)}
    _EMBED_QUEUE_RUNNING[script_id] = True
    threading.Thread(target=_embed_chunks_loop, args=(script_id, user_id), daemon=True).start()
    return {"ok": True, "status": embed_status(script_id)}
