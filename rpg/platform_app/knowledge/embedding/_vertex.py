"""platform_app.knowledge.embedding._vertex — Vertex genai SDK 嵌入通道 + client 缓存。

拆包前住在单文件 embedding.py。进程内 client 缓存(可变全局 _VERTEX_CLIENT_CACHE)与其唯一
读写方 _get_vertex_client 同居于此。_embed_via_vertex 有平台 gemini key 时先走原生 REST
(_gemini._embed_via_gemini),否则退回 genai SDK。纯机械搬家,行为零变化。
"""
from __future__ import annotations

import os
from typing import Any

from ._base import DEFAULT_EMBED_MODEL, EMBED_DIM, _is_admin, log
from ._gemini import _embed_via_gemini

# 进程内 cache,避免 ChatPipeline 每次 _embed_query 都重新 import vertex SDK
_VERTEX_CLIENT_CACHE: dict[str, Any] = {}


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
