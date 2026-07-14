"""platform_app.api.me.credentials —— 用户级 API 凭证(加密存储,按用户隔离)+ embedder 状态 + 凭证自检端点。

credentials 列表/设置/删除、embedder/status 生效路径、credentials/test 可用性 ping(含 60s 节流缓存)。
纯机械搬家,行为零变化。
"""
from __future__ import annotations

from fastapi import Depends, Request

from .._deps import json_response, require_user
from ._shared import router


@router.get("/api/me/credentials")
async def api_my_credentials(user=Depends(require_user)):
    """列出当前用户已配置的 API 凭证（不含 raw key）"""
    from ... import user_credentials
    return json_response(user_credentials.list_credentials(user["id"]))


@router.post("/api/me/credentials")
async def api_set_credential(request: Request, user=Depends(require_user)):
    """设置/更新当前用户某个 provider 的 API key。

    base_url_override 仅 admin 可设；普通用户的 base_url 强制走 catalog。
    """
    body = await request.json()
    from ... import user_credentials
    is_admin = user.get("role") == "admin"
    try:
        api_id = body.get("api_id", "")
        base_url_override = (body.get("base_url_override") or "").strip()
        if not is_admin:
            from model_registry import default_api_for, find_api, load_model_catalog, normalize_api_id
            normalized_api_id = normalize_api_id(api_id)
            catalog = load_model_catalog()
            known = bool(find_api(catalog, normalized_api_id) or default_api_for(normalized_api_id))
            # 中转站(第三方 OpenAI 兼容端点): 普通用户也可添加。
            #  · 自定义(未知)provider 必须自带 base_url 指向中转站,否则无从路由;
            #  · 已知 provider 也允许覆盖 base_url(指向自己的中转/代理)。
            # base_url 的 SSRF 防护由下方 set_credential 的 _validate_base_url 兜底
            # (强制 https + 禁私网/本机),不再一刀切拒绝未知 provider。
            # 仅在「真的在设置一个 key」时才要求 base_url;清空 key(api_key='')/纯删除
            # 不该被这条设置态校验挡住(否则自定义中转站删不掉,报「删除失败」)。
            if (body.get("api_key") or "").strip() and not known and not base_url_override:
                raise ValueError("自定义供应商必须填写 Base URL(中转站地址)")
            api_id = normalized_api_id
        result = user_credentials.set_credential(
            user["id"],
            api_id,
            body.get("api_key", ""),
            base_url_override=base_url_override,
            enabled=bool(body.get("enabled", True)),
            allow_base_url=True,  # base_url 不再 admin 限定;SSRF 由 _validate_base_url 强制
            proxy=(body.get("proxy") or "").strip(),  # 出站代理 URL;仅本地模式真正使用(见 openai_compat)
        )
        return json_response(result)
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/me/credentials/delete")
async def api_delete_credential(request: Request, user=Depends(require_user)):
    body = await request.json()
    from ... import user_credentials
    return json_response(user_credentials.delete_credential(user["id"], body.get("api_id", "")))


_PING_CACHE: dict[tuple[int, str], tuple[float, dict]] = {}
_PING_TTL = 60.0  # 60s 内同 user+api_id 的 ping 结果直接复用,防 API 被封


@router.get("/api/me/embedder/status")
async def api_embedder_status(user=Depends(require_user)):
    """task: RAG 模型设置面板 + 导入向导用 — 告诉前端当前 embedder 实际生效路径。

    Returns:
        - is_admin: 用户是否 admin(决定能否走平台兜底)
        - user_configured: 用户自己配了 embedder credential
        - platform_fallback_available: 平台 EMBED_API_KEY 是否配置
        - effective_source: 'user' / 'platform_fallback' / 'none'
        - fallback_active: 当前是否在用平台兜底
        - preflight: embedding_preflight 结果(含 ok/error/hint/last_error_hint 等)
          - ok=False → 用户无可用 embedder,前端应展示引导 Alert
          - last_error_hint → 上次实际 embed 调用失败的友好描述(如 405 地址不支持)
    """
    import os as _os
    from ... import user_credentials
    from ...knowledge.embedding import embedding_preflight, has_platform_fallback_role
    # task: 享受平台兜底的角色 — admin + vip_user(测试期高级用户)。
    # 角色集合收敛到 has_platform_fallback_role(单一来源);传 user dict 不查库。
    is_admin_user = has_platform_fallback_role(user)
    # 用户自己配了 embedder 任一种 provider?
    # 先认用户在「RAG 模型」里**实际选中**的 embedder provider(embed.api_id)—— dashscope /
    # siliconflow 等 OpenAI 兼容 embedding provider 都支持,不能只认死名单,否则用户配了
    # dashscope key 也会被判「未配置 → 平台兜底」(用户反馈的 bug)。
    user_configured = False
    _selected_embed_api = ""
    try:
        from core.llm_backend import resolve_preferred_api as _resolve_pref
        _selected_embed_api = (_resolve_pref(user["id"], "embed.api_id") or "").strip()
    except Exception:
        _selected_embed_api = ""
    _aliases: list[str] = []
    if _selected_embed_api:
        _aliases.append(_selected_embed_api)
        try:
            _aliases.append(user_credentials.normalize_api_id(_selected_embed_api))
        except Exception:
            pass
    _aliases += ["AgentPlatform", "vertex_ai", "openai", "cohere"]
    _seen: set[str] = set()
    for api_id_alias in _aliases:
        if not api_id_alias or api_id_alias in _seen:
            continue
        _seen.add(api_id_alias)
        if user_credentials.get_credential(user["id"], api_id_alias):
            user_configured = True
            break
    # 权威闸:选了没有 embedding 接口的 chat-only provider(deepseek/anthropic 等)→ 即便配了聊天凭据
    # 也判为「未配置有效嵌入器」+ 明确指引。否则前端显示「已配置」,实际每次 embed 都 404、导入 RAG 坏掉。
    _embed_provider_hint = ""
    try:
        from ...knowledge.embedding import provider_lacks_embedding
        if _selected_embed_api and provider_lacks_embedding(_selected_embed_api):
            user_configured = False
            _embed_provider_hint = (
                f"当前嵌入器 provider「{_selected_embed_api}」没有向量嵌入(embedding)接口,"
                f"请改选支持 embedding 的 provider(OpenAI text-embedding-3 / 阿里 dashscope / siliconflow / "
                f"Vertex,或本地 bge/nomic)。"
            )
    except Exception:
        pass
    platform_available = bool(_os.environ.get("EMBED_API_KEY"))
    # 平台 vertex embedding 兜底已收紧为仅 admin/vip(_get_vertex_client allow_fb / _resolve_embed_config)。
    # platform_fallback_available 必须与执行层一致地 _is_admin gate —— 否则普通用户也收到 True,
    # 前端会据此错误地展示平台 vertex embedding 模型(越权)+ 信息泄露。
    platform_fallback_available = platform_available and is_admin_user
    if user_configured:
        effective = "user"
    elif is_admin_user and platform_available:
        effective = "platform_fallback"
    else:
        effective = "none"
    # 调 preflight 拿详细状态(含 last_error_hint/hint/code 等)
    try:
        preflight = embedding_preflight(user["id"])
    except Exception:
        preflight = {"ok": False, "error": "preflight check failed"}
    return json_response({
        "ok": True,
        "is_admin": is_admin_user,
        "user_configured": user_configured,
        "platform_fallback_available": platform_fallback_available,
        "effective_source": effective,
        "fallback_active": (effective == "platform_fallback"),
        "embed_provider_hint": _embed_provider_hint,
        "preflight": preflight,
    })


@router.get("/api/me/credentials/test")
async def api_test_credential(
    api_id: str = "",
    model: str = "",
    force: bool = False,
    user=Depends(require_user),
):
    """task: 用户级凭证可用性自检 — 实际发一次最小 LLM 调用,
    所有 provider(Vertex / Anthropic / OpenAI-compat)复用 GameMaster.call 路径。

    **throttle**: 同 (user_id, api_id) 60s 内只打一次真实 API,后续返缓存结果。
    `?force=1` 跳过缓存(用户手动点「重新测试」按钮时用)。

    Returns:
      ok=True: 可用,带 latency_ms
      ok=False: 不可用,带 error + error_kind
      cached=True 标记结果来自缓存
    """
    import time as _time
    from ... import user_credentials

    # task: throttle — 同 user+api_id 60s 内复用结果
    cache_key = (int(user["id"]), api_id)
    if not force:
        cached = _PING_CACHE.get(cache_key)
        if cached and (_time.monotonic() - cached[0]) < _PING_TTL:
            return json_response({**cached[1], "cached": True})

    cred = user_credentials.get_credential(user["id"], api_id)
    if cred is None:
        # credential 都没有,直接报「没配 key」
        return json_response({
            "ok": False, "api_id": api_id,
            "has_credential": False,
            "error": "未配置 API key/credential,请先在「API 设置」添加。",
        })

    # 找该 api_id 在 catalog 里的一个 enabled 模型(没传 model 时)
    if not model:
        try:
            from model_registry import load_model_catalog, find_api, normalize_api_id
            catalog = load_model_catalog()
            # credential id (AgentPlatform) → catalog id (vertex_ai)
            catalog_api_id = "vertex_ai" if normalize_api_id(api_id) == "AgentPlatform" else api_id
            api_def = find_api(catalog, catalog_api_id)
            models = (api_def or {}).get("models") or []
            enabled = next((m for m in models if m.get("enabled") is not False), None)
            if not enabled:
                return json_response({
                    "ok": False, "api_id": api_id, "has_credential": True,
                    "error": f"provider {catalog_api_id} 在 catalog 里没有 enabled 模型,无法 ping。",
                })
            model = enabled.get("real_name") or enabled.get("id") or ""
        except Exception as exc:
            return json_response({
                "ok": False, "api_id": api_id, "has_credential": True,
                "error": f"读取 catalog 失败: {type(exc).__name__}: {exc}",
            })

    # 实际打 ping:走 GameMaster.call 跟真实游戏一致
    started = _time.monotonic()
    try:
        from agents.gm import GameMaster
        # 走 GM 路径,user_id 传过去让 BYOK 凭证自动加载
        catalog_api_id = "vertex_ai" if user_credentials.normalize_api_id(api_id) == "AgentPlatform" else api_id
        gm = GameMaster(api_id=catalog_api_id, model=model, user_id=int(user["id"]))
        # 最小调用:max_tokens=1,system 空,user "ping"
        gm._backend.call(system="", messages=[{"role": "user", "content": "ping"}], max_tokens=8)
        elapsed_ms = int((_time.monotonic() - started) * 1000)
        result = {
            "ok": True, "api_id": api_id, "has_credential": True,
            "model": model, "latency_ms": elapsed_ms,
        }
        # task: 缓存成功结果 60s 防被频繁触发
        _PING_CACHE[cache_key] = (_time.monotonic(), result)
        return json_response(result)
    except Exception as exc:
        elapsed_ms = int((_time.monotonic() - started) * 1000)
        msg = str(exc) or type(exc).__name__
        # 简单分类:403 / 401 / quota / network
        kind = "unknown"
        if "403" in msg or "PERMISSION_DENIED" in msg or "forbidden" in msg.lower():
            kind = "permission_denied"
        elif "401" in msg or "unauthorized" in msg.lower() or "invalid api key" in msg.lower():
            kind = "auth_failed"
        elif "quota" in msg.lower() or "429" in msg or "rate" in msg.lower():
            kind = "rate_limited"
        elif "404" in msg or "not found" in msg.lower() or "model" in msg.lower() and "exist" in msg.lower():
            kind = "model_not_found"
        elif "timeout" in msg.lower() or "connection" in msg.lower():
            kind = "network"
        err_result = {
            "ok": False, "api_id": api_id, "has_credential": True,
            "model": model, "latency_ms": elapsed_ms,
            "error": msg[:600], "error_kind": kind,
        }
        # task: 错误结果也缓存 60s,防 403 / 401 等反复触发被封
        _PING_CACHE[cache_key] = (_time.monotonic(), err_result)
        return json_response(err_result)
