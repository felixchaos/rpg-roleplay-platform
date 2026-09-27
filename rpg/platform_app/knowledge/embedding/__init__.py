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
  函数逐字定义在此(globals 在门面解析)。OpenAI 通道亦留在本层;失败信息经 `_breaker`
  记到「该用户、该配置」名下(曾是进程级全局 `_last_openai_embed_error`,A 用户的中转站
  主机名和报错会出现在 B 用户的提示里,已删)。
- `_base` = 共享常量/维度/配置低层谓词(叶子)
- `_breaker` = 按 (用户, 供应商, 模型, 地址, key 指纹) 的熔断 + 按用户隔离的最近错误(叶子)
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

from . import _breaker
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


def _catalog_embed_base_url(api_id: str) -> str:
    """凭据里没有 base_url_override 时,嵌入请求该发往哪。静态模板优先,live catalog 兜底。

    静态模板排前面是有意的:dashscope 在 live catalog 里可能被切成原生模式(/api/v1),
    那条地址没有 OpenAI 兼容的 /embeddings,静态模板恒是 compatible-mode。
    live catalog 只兜「静态模板里根本没有」的供应商(管理员 / 桌面本地用户自己加的)。
    此前缺这一档 → 解析成空串 → _embed_via_openai 退到 api.openai.com,把别家的 key
    发给了 OpenAI,回来的 401 还被翻成「key 无效」(反馈 #104:千问 key「获取模型都正常」,
    因为聊天/拉模型走的 base_url_for 本就读 live catalog)。
    """
    try:
        from model_registry import default_api_for
        base = (default_api_for(api_id) or {}).get("base_url", "") or ""
    except Exception:
        base = ""  # normalize_api_id 对非法 id 会抛,当查不到处理
    if base:
        return base
    try:
        from model_registry import base_url_for
        return base_url_for(api_id) or ""
    except Exception:
        return ""


class _EmbedConfig(tuple):
    """一份嵌入配置。按位置就是 (api_id, model, api_key, base_url) 四元组,解包、比较与旧的
    四元组完全一样(测试替身直接返回普通四元组也照常工作);另外带两样跟着「这次实际用的那份
    凭据」一起解析出来的东西,由 dispatch 使用:

    - proxy:该凭据配的出站代理(core.outbound.credential_proxy,服务器模式恒 None)。平台
      EMBED_* 配置恒为 None —— 平台 key 的请求不套任何用户代理。以前 dispatch 按 (用户, api_id)
      另查一次凭据取代理,admin/vip 用平台配置时被套上了他自己聊天凭据里的代理。
    - cred_version:该凭据的版本(保存时间),进熔断单元 key。充值后用同一把 key 重新保存,
      所有 worker 都落到新单元(保存请求上的 reset_user 只清得到处理它的那个 worker)。

    取这两样一律经 _cfg_proxy / _cfg_version(普通四元组时给空值)。
    """

    proxy: str | None
    cred_version: str

    def __new__(cls, api_id: str, model: str, api_key: str, base_url: str, *,
                proxy: str | None = None, cred_version: str = "") -> _EmbedConfig:
        obj = super().__new__(cls, (api_id, model, api_key, base_url))
        obj.proxy = proxy or None
        obj.cred_version = cred_version or ""
        return obj


def _cfg_proxy(cfg: Any) -> str | None:
    return getattr(cfg, "proxy", None) or None


def _cfg_version(cfg: Any) -> str:
    return getattr(cfg, "cred_version", "") or ""


def _embed_config_for_cred(api_id: str, model: str, cred: dict[str, Any] | None) -> _EmbedConfig:
    """用户凭据 → 这份凭据的嵌入配置(token、base_url、代理、凭据版本一次解析齐)。

    建库(_resolve_embed_config)与召回(embed_query 的 force 分支)共用这一个解析器,
    两条路对同一个 (用户, 供应商) 永远算出同一个主机、同一个代理、同一个熔断单元。
    - token 走 resolved_auth_token:免 Key 的本地嵌入模型(Ollama / LM Studio)拿占位 token,
      不再被当成「没配」。
    - 地址 = 凭据自己的 base_url_override,没有就取该供应商的 catalog 地址。
      **不读 EMBED_BASE_URL**:那是平台那把 key 的地址,只属于 _platform_fallback_config。
      此前用户分支排在 catalog 前面读它 → 部署设了 EMBED_BASE_URL(常见值是 Gemini 兼容端点)
      时,用户的 siliconflow / dashscope / openai key 被发给了 Google(#104 同族,修了一半)。
    - 代理 / 版本:见 _EmbedConfig。代理解析失败按「没配代理」处理,代理只是出站路径的选择,
      不能因为它把向量请求本身弄挂。
    """
    from platform_app.user_credentials import resolved_auth_token
    token = resolved_auth_token(cred)
    base_url = ((cred or {}).get("base_url_override", "") or "") or _catalog_embed_base_url(api_id)
    try:
        from core.outbound import credential_proxy
        proxy = credential_proxy(cred)
    except Exception:
        proxy = None
    return _EmbedConfig(api_id, model, token, base_url, proxy=proxy,
                        cred_version=str((cred or {}).get("updated_at") or ""))


def _default_embed_model_for(api_id: str) -> str:
    """用户选了供应商(或默认供应商)却没选嵌入模型时用哪个:该供应商策展目录里第一个
    带 embedding 能力的模型(openai → text-embedding-3-small,dashscope → text-embedding-v3)。

    目录里没有才退平台 EMBED_MODEL。此前直接用平台 EMBED_MODEL(默认值是 Gemini 的
    text-embedding-004)→ 没设 RAG 偏好、只配了 OpenAI 聊天 key 的用户,嵌入请求带着
    text-embedding-004 发给 OpenAI,必然 404:平台配置漏进用户凭据路径,与 EMBED_BASE_URL 同族。
    """
    try:
        from model_registry import default_api_for
        for m in (default_api_for(api_id) or {}).get("models") or []:
            if "embedding" in (m.get("capabilities") or []) and m.get("real_name"):
                return str(m["real_name"])
    except Exception:
        pass  # 非法 api_id → 查不到,退平台默认
    return DEFAULT_EMBED_MODEL


def _resolve_embed_config(user_id: int | None) -> tuple[str, str, str, str]:
    """返回 (api_id, model, api_key, base_url_override)(实为 _EmbedConfig,另带 proxy / cred_version)。

    优先链:
    1. user 自己配的 BYOK embedder credential(任何用户都允许;地址 / 代理见 _embed_config_for_cred)
    2. 平台 env 兜底(EMBED_API_KEY / EMBED_BASE_URL / EMBED_MODEL)— 只对 admin/vip 生效。
       普通用户没自己配 → 返回空 api_key,_embed_via_openai 会返 None 让上层降级。

    设计理由:Gemini API text-embedding-004 在付费层 $0.025/M tokens,100 用户
    满量 import ≈ $187 一次性。不给普通用户兜底,强制 BYOK。
    """
    if user_id:
        try:
            from core.llm_backend import resolve_preferred_api, resolve_preferred_model
            from platform_app.user_credentials import resolve_api_key, resolved_is_usable
            api_id = resolve_preferred_api(user_id, "embed.api_id") or DEFAULT_EMBED_API_ID
            model = (resolve_preferred_model(user_id, "embed.model_real_name")
                     or _default_embed_model_for(api_id))
            # user 自己配了 — 优先用,任何用户都允许。可用性走 resolved_is_usable(免 Key 本地模型也算)。
            cred = resolve_api_key(user_id, api_id, env_fallback="")
            if resolved_is_usable(cred):
                return _embed_config_for_cred(api_id, model, cred)
            # user 没自配 — 只 admin/vip 才走平台 env 兜底
            if _is_admin(user_id):
                return _platform_fallback_config()
            # 普通用户 + 没自配 → 返空 key 让 _embed_via_openai 返 None
            log.debug("[embedding] non-admin user %s without own embedder cred; refusing platform fallback", user_id)
            return api_id, model, "", ""
        except Exception as exc:
            log.debug("[embedding] resolve_embed_config failed for user %s: %s", user_id, exc)
            # 解析中途出错(DB 抖动等)时也守住 BYOK 闸:只有 admin/vip 才能拿平台 key。
            # 此前直接落到下面的平台兜底 → 普通用户在这一刻拿到的是平台 EMBED_API_KEY。
            if not _is_admin(user_id):
                return DEFAULT_EMBED_API_ID, DEFAULT_EMBED_MODEL, "", ""
    # 无 user_id (后台 cron / 内部任务) 或 admin/vip 解析出错:走 env 兜底
    return _platform_fallback_config()


# ---------------------------------------------------------------------------
# Provider dispatch
# ---------------------------------------------------------------------------

def _embed_via_openai(model: str, api_key: str, texts: list[str], base_url: str = "", proxy: str | None = None) -> list[list[float]] | None:
    """OpenAI 兼容 embeddings API。base_url 为空则走官方 https://api.openai.com/v1。

    请求 dimensions=EMBED_DIM,让 text-embedding-3 / qwen text-embedding-v3 等可降维模型输出
    与 DB 向量列(默认 768)一致。模型不支持 dimensions(如 ada-002)时会 400 → 自动去掉
    dimensions 重试一次。

    proxy:凭据代理(调用方传 core.outbound.credential_proxy 的结果,本地模式才有值)。
    """
    import json as _json
    import urllib.error
    import urllib.parse
    import urllib.request

    # safe_urlopen —— SSRF: 不跟随重定向 + use-time 重解析 pin IP
    from core.outbound import proxy_kwargs, safe_urlopen
    from core.outbound_ua import outbound_user_agent
    effective_url = (base_url.rstrip("/") if base_url else "https://api.openai.com/v1") + "/embeddings"
    # 报错里带上实际请求的主机:同一个 401,发错了地方和 key 真坏了是两回事,不写出来用户和我们都分不清。
    _host = urllib.parse.urlsplit(effective_url).netloc or effective_url

    # BUGFIX: 不同 OpenAI 兼容 provider 对单请求 input 数组条数上限不同。DashScope(阿里 dashscope/
    # 百炼)text-embedding 限 ≤10,而上游按 BATCH_SIZE=30 喂入 → "400 batch size ... not larger than 10"。
    # 按 base_url 推断 provider 上限,超限就拆子批保序拼接(对 OpenAI/SiliconFlow 等大上限 provider 不变)。
    _bl = (base_url or "").lower()
    _max_batch = 10 if ("dashscope" in _bl or "aliyun" in _bl or "bailian" in _bl) else 64
    if len(texts) > _max_batch:
        out: list[list[float]] = []
        for _i in range(0, len(texts), _max_batch):
            sub = _embed_via_openai(model, api_key, texts[_i:_i + _max_batch], base_url, **proxy_kwargs(proxy))
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
        with safe_urlopen(req, timeout=_embed_req_timeout(len(texts)), **proxy_kwargs(proxy)) as resp:
            data = _json.loads(resp.read())
        items = sorted(data["data"], key=lambda x: x["index"])
        return [item["embedding"] for item in items]

    def _guard_dim(vecs: list[list[float]] | None) -> list[list[float]] | None:
        # 维度卫士:有的模型(如 BAAI/bge-m3 固定 1024)不支持降到 EMBED_DIM(768),会静默返回
        # 异维向量 → 既存不进 vector(768) 列(索引侧 with_vec=0),又在召回侧维度不符报错被吞 →
        # 用户「RAG 完全失效」却查不出原因。这里维度不符即「响亮失败」+ 写人话错误供前端引导换模型。
        if vecs and EMBED_DIM and len(vecs[0]) != EMBED_DIM:
            _breaker.note(_breaker.KIND_CONFIG, friendly=(
                f"向量嵌入模型「{model}」输出 {len(vecs[0])} 维,但系统统一用 {EMBED_DIM} 维"
                f"(该模型不支持降到 {EMBED_DIM})。请到「设置 → RAG / 向量模型」改用支持 {EMBED_DIM} 维的模型"
                f"(如 Qwen/Qwen3-Embedding-* 带降维、OpenAI text-embedding-3-*、Gemini text-embedding-004),并重新拆书。"
            ))
            log.warning("[embedding] dim mismatch: model=%s got=%d want=%d → 拒绝异维向量", model, len(vecs[0]), EMBED_DIM)
            return None
        return vecs

    try:
        return _guard_dim(_post(with_dim=bool(EMBED_DIM)))
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        code = e.code
        headers = e.headers
        # 带 dimensions 被 400 拒(模型不支持降维)→ 去掉 dimensions 重试一次
        if code == 400 and EMBED_DIM:
            try:
                return _guard_dim(_post(with_dim=False))  # 去 dimensions 重试后,模型可能吐回原生维度 → 仍须卡维
            except urllib.error.HTTPError as e2:
                body = e2.read().decode(errors="replace")
                code = e2.code
                headers = e2.headers
            except Exception as e2:
                log.warning("[embedding] openai embed retry-no-dim failed: %s", e2)
                _breaker.note_exception(e2, _host)
                return None
        # 把裸 HTTP 错误码映射成对用户有意义的描述,经 _breaker 记到本用户、本配置名下,
        # 供 embedding_preflight 读取以便前端引导用户去 RAG 设置。
        if code == 405:
            friendly = (
                f"你配置的 embedding 中转站地址不支持 /embeddings 接口（HTTP 405 Method Not Allowed）。"
                f" 请确认 base_url 填的是支持 OpenAI embeddings API 的地址，而不是仅支持 /chat/completions 的地址。"
                f" 原始响应：{body[:120]}"
            )
        elif code == 401:
            friendly = (
                f"向量嵌入 API Key 被 {_host} 拒绝（HTTP 401 Unauthorized）:key 无效或已过期,"
                f"或者这把 key 本来就不是 {_host} 的。"
                f" 请在「设置 → RAG / 向量模型」检查 API Key 和接口地址。"
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
                    f"向量嵌入接口地址(base_url)错误(HTTP 404 Not Found,请求的是 {_host})。"
                    f"请确认路径与提供商匹配:OpenAI/中转站通常以 /v1 结尾、火山方舟(豆包)以 /api/v3 结尾、"
                    f"Gemini 兼容以 /v1beta/openai 结尾。"
                    f" 原始响应：{body[:120]}"
                )
        else:
            friendly = f"向量嵌入请求失败（HTTP {code}，{_host}）：{body[:200]}"
        log.warning("[embedding] openai embed failed: %s %s | friendly: %s", code, body[:200], friendly)
        # 401/402/403/404/405 → 配置类冷却;429 → 限流冷却(读 Retry-After);400/413/422 只记不熔断
        _breaker.note_http(code, body=body, headers=headers, friendly=friendly)
        return None
    except Exception as e:
        # 代理用不了(UnsupportedProxy,urllib 不支持 SOCKS)也走这里:note_exception 按配置类记,
        # 原因进该用户的最近错误,设置页 / 拆书预检能看到。与 Gemini 通道同一处判断。
        log.warning("[embedding] openai embed failed: %s", e)
        _breaker.note_exception(e, _host)
        return None


def _embed_provider_dispatch(
    api_id: str,
    model: str,
    api_key: str,
    texts: list[str],
    base_url: str = "",
    task_type: str = "RETRIEVAL_DOCUMENT",
    user_id: int | None = None,
    *,
    proxy: str | None = None,
    cred_version: str = "",
) -> list[list[float]] | None:
    """根据 api_id 分发到对应 provider SDK。不识别 → 降级 vertex + warn。
    user_id 传给 Vertex 路径以走 BYOK SA 优先链。

    这里是所有嵌入出站的唯一收口,熔断也落在这:该 (用户, 供应商, 模型, 地址, key, 凭据版本)
    在冷却中就直接返回 None(上层退关键词召回),不再发请求。查询路径(RETRIEVAL_QUERY)任何冷却
    都短路;写库路径只在配置类 / 无凭据冷却时短路,限流和瞬时故障照常真打、由写库循环自己退避。

    proxy / cred_version:跟着这次用的配置一起解析出来的(_EmbedConfig),调用方原样传入;
    平台配置两者都为空。这里不再按 (用户, api_id) 反查凭据。
    """
    bkey = _breaker.key_for(user_id, api_id, model, base_url, api_key, cred_version)
    if _breaker.blocked(bkey, batch=(task_type != "RETRIEVAL_QUERY")) is not None:
        log.debug("[embedding] api_id=%s model=%s 仍在冷却,本次不发请求", api_id, model)
        return None
    _breaker.mark_dispatched()
    with _breaker.attempt() as att:
        vecs = _embed_provider_dispatch_inner(
            api_id, model, api_key, texts, base_url=base_url, task_type=task_type, user_id=user_id,
            proxy=proxy)
    if vecs:
        _breaker.record_success(bkey, user_id=user_id)
    else:
        _breaker.record_failure(bkey, att, user_id=user_id, api_id=api_id, model=model, base_url=base_url)
    return vecs


def _embed_provider_dispatch_inner(
    api_id: str,
    model: str,
    api_key: str,
    texts: list[str],
    base_url: str = "",
    task_type: str = "RETRIEVAL_DOCUMENT",
    user_id: int | None = None,
    proxy: str | None = None,
) -> list[list[float]] | None:
    """按 api_id 选通道(熔断判断在外层 _embed_provider_dispatch)。"""
    if api_id in _VERTEX_API_IDS:
        return _embed_via_vertex(model, texts, task_type=task_type, user_id=user_id)
    # 这份配置的凭据代理(本地模式才有值);没配时 **{} 不改变调用形态。
    from core.outbound import proxy_kwargs
    _px = proxy_kwargs(proxy)
    if api_id in _GEMINI_API_IDS:
        return _embed_via_gemini(model, api_key, texts, task_type=task_type, **_px)
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
        if not base_url and api_id not in _OPENAI_API_IDS:
            # 空 base_url 在 _embed_via_openai 里等于 api.openai.com。那只对 OpenAI 自己成立;
            # 别家的 key 发过去必然 401,等于把用户的 key 交给了第三方(反馈 #104)。
            _breaker.note(_breaker.KIND_CONFIG, friendly=(
                f"找不到向量嵌入供应商「{api_id}」的接口地址,已停止发送请求。"
                f"请在「设置 → API & 模型」给这个供应商填上接口地址(base_url),"
                f"或在「设置 → RAG / 向量模型」换一个内置的供应商。"
            ))
            log.warning("[embedding] api_id=%r resolved to empty base_url; refusing to send its key to api.openai.com", api_id)
            return None
        return _embed_via_openai(model, api_key, texts, base_url=base_url, **_px)
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
    allow_platform_fallback: bool = True,
) -> tuple[list[list[float]] | None, str]:
    """task: admin 用户的 embedder 兜底逻辑。

    返回 (vecs, source)。source ∈ {'user', 'platform_fallback', 'failed'}
    让上层(connectivity test / log)能知道当前走哪条路。

    流程:
    1. 先 try user 自配 (或 admin 平台兜底,由 _resolve_embed_config 决定)
    2. 失败 + user 是 admin → retry 平台 EMBED_* env(防 user 配的 vertex 不可用)
    3. 仍失败 → return None
    """
    cfg = _resolve_embed_config(user_id)
    api_id, model, api_key, base_url = cfg
    if api_key or api_id in _VERTEX_API_IDS:  # vertex 不用 api_key,看 SA
        vecs = _embed_provider_dispatch(api_id, model, api_key, texts, base_url=base_url, task_type=task_type,
                                        user_id=user_id, proxy=_cfg_proxy(cfg), cred_version=_cfg_version(cfg))
        if vecs:
            return vecs, "user"

    # admin fallback: 即使 user 配了但调用失败,自动切平台兜底。
    # 写库路径(allow_platform_fallback=False)不切:剧本已按用户自己的 (api_id, model) 绑定,
    # 混进平台模型算的向量会让同一剧本里有两个向量空间(维度同为 768 不报错),召回静默错乱。
    if allow_platform_fallback and user_id and _is_admin(user_id):
        plat_api, plat_model, plat_key, plat_base = _platform_fallback_config()
        # 第一步用的已经就是平台配置(admin/vip 没自配时 _resolve_embed_config 直接给平台配置):
        # 同一把 key、同一个端点刚失败过,别原样再打一次。vertex 除外 —— 它按 user_id 取 SA,
        # 第一步可能用的是该用户自己的 SA,这里 user_id=None 才是平台 SA。
        already_tried = (plat_api not in _VERTEX_API_IDS
                         and (plat_api, plat_model, plat_key, plat_base) == (api_id, model, api_key, base_url))
        if not already_tried and (plat_key or plat_api in _VERTEX_API_IDS):
            log.info("[embedding-only] privileged user=%s (admin/vip): RAG fallback to platform EMBED_API_KEY (Gemini API,**非** LLM,LLM 严格 BYOK 不会兜底)", user_id)
            vecs = _embed_provider_dispatch(plat_api, plat_model, plat_key, texts, base_url=plat_base, task_type=task_type, user_id=None)
            if vecs:
                return vecs, "platform_fallback"
    return None, "failed"


def _embed_batch(
    texts: list[str], user_id: int | None = None, *, allow_platform_fallback: bool = True,
) -> list[list[float]] | None:
    """调 embedding provider,返向量列表。失败返 None。
    user_id 非 None 时走 BYOK 优先链 + admin fallback;None 走系统默认。
    写进已绑定剧本的向量(_writer / canon)传 allow_platform_fallback=False,见 _embed_with_admin_fallback。
    """
    if not texts:
        return []
    vecs, _source = _embed_with_admin_fallback(
        texts, user_id, allow_platform_fallback=allow_platform_fallback)
    return vecs


def embedding_preflight(user_id: int | None) -> dict[str, Any]:
    """Return user-facing readiness for the configured embedding provider.

    扩展逻辑:
    - 普通"没配 Key"走旧逻辑,返 needs_credentials=True 引导去设置。
    - 有 Key 但**本用户、当前这套配置**上次实际调用失败(e.g. 405/401/404/429)时,
      把友好描述带进 hint,让前端能显示人话而不是技术错误码,并附上"去 RAG 设置检查"
      按钮所需的 settings_hash。错误按用户隔离(_breaker.last_error_for),换了配置旧错不再显示。
    """
    cfg = _resolve_embed_config(user_id)
    api_id, model, api_key, base_url = cfg
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
        hint = _breaker.last_error_for(
            user_id, _breaker.key_for(user_id, api_id, model, base_url, api_key, _cfg_version(cfg)))
        if hint:
            base["last_error_hint"] = hint
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
    *,
    allow_platform_fallback: bool = True,
) -> str | None:
    """task 51 / P0-fix: query 文本 → 768 维向量字符串。
    `_search._embed_query` 的 production 实现。失败返 None 自动 fallback ILIKE。

    优先级链：
      1. force_api_id + force_model（召回路径：必须与建库时的 (api_id, model) 完全一致）
      2. user_id BYOK 配置（ad-hoc query / admin 工具）
      3. 系统默认 vertex_ai + text-embedding-004

    同一请求(一回合)内,同一用户、同一锁定 embedder、同一文本只真算一次(失败的 None 也记住):
    一回合的检索会对同一段玩家输入嵌入 4-5 次(新旧两路召回 × chunks/实体/kb_nodes),
    实际只有两种不同文本。缓存容器由中间件在每个请求开头重置;不在请求里(后台线程、cron)
    时不缓存,行为不变。

    allow_platform_fallback=False:常规路径(没给 force_*)在 admin/vip 自己的嵌入器失败时
    不切平台兜底。没有绑定 embedder 元数据、却要和已存向量比相似度的地方用它(kb_events 的
    写入与召回):切过去算出的是另一个向量空间,写进去就混了,拿来比就是乱比。
    """
    text = (text or "").strip()
    if not text:
        return None
    try:
        from core.request_cache import get_embed_vec_cached
    except Exception:  # pragma: no cover - core 总在
        return _embed_query_uncached(text, user_id, force_api_id, force_model, allow_platform_fallback)
    return get_embed_vec_cached(
        (user_id, force_api_id or "", force_model or "", text, bool(allow_platform_fallback)),
        lambda: _embed_query_uncached(text, user_id, force_api_id, force_model, allow_platform_fallback),
    )


def _embed_query_uncached(
    text: str,
    user_id: int | None,
    force_api_id: str | None,
    force_model: str | None,
    allow_platform_fallback: bool = True,
) -> str | None:
    _breaker.reset_dispatched()
    if force_api_id and force_model:
        # 严格锁定建库时的 provider（召回侧强制路径,不走 admin fallback,
        # 因为换 provider 会让向量维度不匹配,反而召回不出来)
        # BUG-A fix: 必须按 force_api_id 取 key/base_url,而非用户当前选中的 provider。
        # 用户切换 embed provider 后,_resolve_embed_config(user_id) 会返回新 provider 的
        # key → 发到旧 provider 端点 → 401/404 → 静默降级 ILIKE。
        api_id, model = force_api_id, force_model
        try:
            from platform_app.user_credentials import resolve_api_key, resolved_is_usable
            _cred = resolve_api_key(user_id, force_api_id, env_fallback="")
            usable = resolved_is_usable(_cred)
            # 与建库侧 _resolve_embed_config 同一个解析器:同一 (用户, 供应商) 永远同一个主机、同一个代理
            cfg = _embed_config_for_cred(force_api_id, force_model, _cred)
        except Exception:
            # 极端情况(凭据读取失败):回退到当前用户 config 的 key/base_url 尽力而为
            cfg = _resolve_embed_config(user_id)
            usable = bool(cfg[2])
        _, _, api_key, base_url = cfg
        proxy, cred_version = _cfg_proxy(cfg), _cfg_version(cfg)
        if not usable and force_api_id not in _VERTEX_API_IDS:
            # 用户自己没有这家的凭据。admin/vip(及无 user 的系统任务)建库时 _resolve_embed_config
            # 给的就是平台配置,剧本按平台的 (api_id, model) 绑定 —— 召回也用平台配置,
            # 否则这批剧本的向量召回恒失败。只在锁定的 (api_id, model) 与平台配置完全一致时用,
            # 同一个向量空间;普通用户拿不到平台 key。
            if (not user_id) or _is_admin(user_id):
                p_api, p_model, p_key, p_base = _platform_fallback_config()
                if p_key and (p_api, p_model) == (force_api_id, force_model):
                    # 平台 key:不套用户凭据的代理,也没有凭据版本
                    api_key, base_url, usable = p_key, p_base, True
                    proxy, cred_version = None, ""
        if not usable and force_api_id not in _VERTEX_API_IDS:
            # 没有这家供应商的凭据:不发请求。交给 dispatch 的话会降级到 vertex,产出的是
            # 另一个向量空间的查询向量(维度同为 768 不报错),召回静默错乱。
            log.debug("[embedding] 剧本锁定的嵌入供应商 %s 当前用户没有可用凭据,跳过向量召回", force_api_id)
            return None
        vecs = _embed_provider_dispatch(api_id, model, api_key, [text], base_url=base_url, task_type="RETRIEVAL_QUERY",
                                        user_id=user_id, proxy=proxy, cred_version=cred_version)
    else:
        # 常规路径:走 admin fallback(user 自配失败时 admin 自动切平台;调用方可关掉,见 docstring)
        vecs, _ = _embed_with_admin_fallback([text], user_id, task_type="RETRIEVAL_QUERY",
                                             allow_platform_fallback=allow_platform_fallback)
    if not vecs:
        if _breaker.dispatched():
            log.warning("[embedding] embed_query returned no vectors")
        else:
            # 冷却中短路 / 根本没配嵌入器:没发请求,失败原因在进入冷却时已记过
            log.debug("[embedding] embed_query: 没有发出请求(冷却中或未配置嵌入器)")
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
