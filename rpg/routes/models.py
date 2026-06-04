"""models.py — 模型目录与 API 管理路由 (/api/models/*)。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from routes._deps_fastapi import get_current_admin, get_current_user
from schemas._common import COMMON_ERROR_RESPONSES, ErrorResponse, GenericOkResponse
from schemas.models import (
    ModelsDeleteModelRequest,
    ModelsProbeRequest,
    ModelsSelectRequest,
    ModelsUpsertApiRequest,
    ModelsUpsertModelRequest,
)

router = APIRouter()


def _inject_pricing(catalog: dict[str, Any]) -> dict[str, Any]:
    """task 57 follow-up: 把 _STATIC_PRICING 里的 input/output/context 注入每个 model,
    让 settings #models 表格价格/上下文/来源三列有数据。
    字段: input_cost_per_million, output_cost_per_million, context_window, source。
    只在 model 本身没有这些字段时才注入(不覆盖 catalog 已有值)。
    """
    import model_probe
    for api in catalog.get("apis", []):
        api_id = api.get("id", "")
        for m in api.get("models", []):
            # 如果 catalog 里已有 typed 字段就跳过
            if m.get("input_cost_per_million") is not None:
                continue
            real = m.get("real_name") or m.get("id")
            if not real:
                continue
            pricing = model_probe.get_pricing(api_id, real)
            if not pricing:
                continue
            m["input_cost_per_million"] = pricing.get("input")
            m["output_cost_per_million"] = pricing.get("output")
            if m.get("context_window") is None and pricing.get("context"):
                m["context_window"] = pricing.get("context")
            if not m.get("source"):
                m["source"] = pricing.get("source", "static")
    return catalog


def _inject_health(catalog: dict[str, Any]) -> dict[str, Any]:
    """task 42: 把 model_probe._HEALTH_CACHE 的状态合并到每个 model.health 字段。
    UI 据此显示可用/不可达/未校验,picker 灰掉 err 项。"""
    import model_probe
    for api in catalog.get("apis", []):
        api_id = api.get("id", "")
        for m in api.get("models", []):
            real = m.get("real_name") or m.get("id")
            health = model_probe.get_health(api_id, real) if real else None
            if health:
                m["health"] = health.get("status") or "untested"
                m["health_status_detail"] = health.get("status_detail") or health.get("status") or "untested"
                m["health_latency_ms"] = health.get("latency_ms")
                m["health_checked_at"] = health.get("checked_at")
                m["health_error"] = health.get("error") or ""
            else:
                m["health"] = "untested"
                m["health_status_detail"] = "untested"
    return catalog


@router.get("/api/models")
async def api_models(
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    from app import _redact_catalog, selected_model
    from model_registry import load_catalog_for_user
    # 安全:每用户视图 = 全局菜单 + 该用户私有 overlay(同步模型 / 自建中转站)。
    _uid = int(api_user["id"]) if api_user and api_user.get("id") else None
    catalog = load_catalog_for_user(_uid)
    is_admin = bool(api_user and api_user.get("role") == "admin")
    redacted = _redact_catalog(catalog, is_admin, user_id=_uid)
    enriched = _inject_pricing(redacted)
    return JSONResponse({
        "ok": True,
        "models": _inject_health(enriched),
        "selected": selected_model(catalog),
    })


@router.post("/api/models/health/refresh-all")
async def api_models_health_refresh_all(
    request: Request,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """触发后台 probe 所有 enabled API 的 enabled model,fire-and-forget。
    UI 调用后定期轮询 GET /api/models 读 health 字段更新显示。
    安全:跟 /api/models/probe 同策略,user 只能 probe 自己配过 key 的 provider。
    """
    import threading

    import model_probe
    from app import _check_probe_permission, load_model_catalog

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    only_api_id = (body or {}).get("api_id") if isinstance(body, dict) else None

    catalog = load_model_catalog()
    targets: list[tuple[str, str]] = []
    for api in catalog.get("apis", []):
        if not api.get("enabled"):
            continue
        api_id = api.get("id", "")
        if only_api_id and api_id != only_api_id:
            continue
        # 权限检查:user 无凭证的 API 跳过(避免烧 server 凭证)
        if _check_probe_permission(api_user, api_id):
            continue
        for m in api.get("models", []):
            if not m.get("enabled"):
                continue
            real = m.get("real_name") or m.get("id")
            if real:
                targets.append((api_id, real))

    user_id = api_user["id"] if api_user else None

    def _sweep() -> None:
        for api_id, real in targets:
            try:
                model_probe.probe_availability(
                    api_id, real, timeout_sec=10, user_id=user_id,
                )
            except Exception:
                pass

    threading.Thread(target=_sweep, daemon=True).start()
    return JSONResponse({"ok": True, "scheduled": len(targets)})


@router.get("/api/models/health")
async def api_models_health(
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """读全部 health cache 的快照,前端可定期 poll 这个轻量 endpoint
    替代 reload /api/models 整树。"""
    import model_probe
    return JSONResponse({"ok": True, "health": model_probe.all_health()})


@router.post("/api/models/select", response_model=GenericOkResponse, responses=COMMON_ERROR_RESPONSES)
async def api_models_select(
    body: ModelsSelectRequest,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """task: 鉴权拆两层:
    - save_id != None → per-save session model 切换,任何登录用户都能改自己存档的模型
    - save_id == None → 全局 catalog selected 改写,**仅 admin**(影响所有用户的 GM 缓存)

    之前整个 endpoint Depends(get_current_admin) 把 save_id 路径也卡死,导致
    普通/vip 用户在 Game Console 里切自己存档的 GM 模型时返 403「需要管理员权限」。
    """
    from app import (
        _gm_by_user,
        _payload,
        _state_by_user,
        _state_lock,
        _user_key,
        select_model,
        selected_model,
    )
    body_dict = body.model_dump(exclude_none=True)
    api_id = body_dict.get("api_id", "")
    model_id = body_dict.get("model_id", "")
    save_id = body.save_id  # int | None

    # A1: 存档级 session_model — 只写当前用户的 state，不动全局 catalog，不清其他用户 GM 缓存
    if save_id is not None:
        uid = _user_key(api_user)
        with _state_lock:
            state = _state_by_user.get(uid)
            if state is not None:
                state.set_session_model(model_id, api_id)
            # 清掉该用户的 GM 缓存，_ensure_loaded 重建时会读 session_model
            _gm_by_user.pop(uid, None)
        # 同步持久化到 DB（走 state_repository 的 runtime_checkouts）
        try:
            from state_repository import persist_session_model
            persist_session_model(save_id=save_id, model_id=model_id, api_id=api_id,
                                  user_id=api_user["id"] if api_user else None)
        except Exception:
            pass  # 持久化失败不影响本次切换（内存已生效）
        catalog = selected_model()
        return JSONResponse({
            "ok": True,
            "scope": "save",
            "save_id": save_id,
            "model_id": model_id,
            "api_id": api_id,
            "selected": catalog,
        })

    # save_id == None 路径:之前是写**全局 catalog selected**(admin 全平台默认),
    # 这正是测试用户撞到「需要管理员权限」的原因。
    #
    # task: 拆两层 — 普通用户走 per-user prefs(user_preferences.gm.*),admin 可
    # 显式带 ?scope=global 改全局 catalog。前端不带 scope 默认 per-user(无管理员阻拦)。
    scope = body.scope if hasattr(body, "scope") and body.scope else "user"
    role = (api_user.get("role") if api_user else "") or ""
    if scope == "global":
        if role.lower() != "admin":
            from fastapi import HTTPException
            raise HTTPException(
                status_code=403,
                detail={"error_key": "admin_required", "message": "全局模型切换仅 admin 可用。如改个人默认模型请删 scope 参数(默认 per-user)。"},
            )
        catalog = select_model(api_id, model_id)
        with _state_lock:
            _gm_by_user.clear()
        return JSONResponse({"ok": True, "scope": "global", "models": catalog, "selected": selected_model(catalog), "state": _payload(api_user)})

    # per-user 路径:写 user_preferences.preferences['gm.api_id'/'gm.model_real_name']
    # 只清当前用户 GM 缓存。任何登录用户都能用。
    if not api_user or not api_user.get("id"):
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="未登录")
    try:
        from psycopg.types.json import Jsonb

        from platform_app.db import connect as _connect
        with _connect() as db:
            row = db.execute(
                "select preferences from user_preferences where user_id = %s",
                (api_user["id"],),
            ).fetchone()
            prefs = dict(row["preferences"]) if row and row.get("preferences") else {}
            prefs["gm.api_id"] = api_id
            prefs["gm.model_real_name"] = model_id
            if row:
                db.execute(
                    "update user_preferences set preferences = %s, updated_at = now() where user_id = %s",
                    (Jsonb(prefs), api_user["id"]),
                )
            else:
                db.execute(
                    "insert into user_preferences (user_id, preferences) values (%s, %s)",
                    (api_user["id"], Jsonb(prefs)),
                )
        # 失效 request-scoped 缓存(下次 resolve_preferred_* 重新读)
        try:
            from core.request_cache import invalidate_user_prefs_cache
            invalidate_user_prefs_cache(int(api_user["id"]))
        except Exception:
            pass
    except Exception as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=f"prefs 写入失败: {exc}") from exc
    uid = _user_key(api_user)
    with _state_lock:
        _gm_by_user.pop(uid, None)
    catalog = selected_model()
    return JSONResponse({"ok": True, "scope": "user", "api_id": api_id, "model_id": model_id, "selected": catalog})


@router.post("/api/models/api", response_model=GenericOkResponse, responses=COMMON_ERROR_RESPONSES)
async def api_models_upsert_api(
    body: ModelsUpsertApiRequest,
    api_user: dict[str, Any] | None = Depends(get_current_admin),
) -> JSONResponse:
    from app import selected_model, upsert_api
    body_dict = body.model_dump()
    catalog = upsert_api(body_dict)
    return JSONResponse({"ok": True, "models": catalog, "selected": selected_model(catalog)})


@router.post("/api/models/model", response_model=GenericOkResponse, responses=COMMON_ERROR_RESPONSES)
async def api_models_upsert_model(
    body: ModelsUpsertModelRequest,
    api_user: dict[str, Any] | None = Depends(get_current_admin),
) -> JSONResponse:
    from app import selected_model, upsert_model
    body_dict = body.model_dump(exclude_none=True)
    model_payload = body_dict.get("model") if isinstance(body_dict.get("model"), dict) else {
        k: v for k, v in body_dict.items() if k != "api_id" and k != "model"
    }
    catalog = upsert_model(body_dict.get("api_id", ""), model_payload)
    return JSONResponse({"ok": True, "models": catalog, "selected": selected_model(catalog)})


@router.post("/api/models/model/delete", response_model=GenericOkResponse, responses=COMMON_ERROR_RESPONSES)
async def api_models_delete_model(
    body: ModelsDeleteModelRequest,
    api_user: dict[str, Any] | None = Depends(get_current_admin),
) -> JSONResponse:
    from app import delete_model, selected_model
    body_dict = body.model_dump(exclude_none=True)
    catalog = delete_model(body_dict.get("api_id", ""), body_dict.get("model_id") or body_dict.get("real_name", ""))
    return JSONResponse({"ok": True, "models": catalog, "selected": selected_model(catalog)})


@router.get("/api/models/remote")
async def api_models_remote(
    request: Request,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """从供应商 SDK 拉取真实可用模型清单（带 60s 缓存）"""
    from app import _check_probe_permission
    api_id = request.query_params.get("api_id", "")
    blocked = _check_probe_permission(api_user, api_id)
    if blocked:
        return blocked
    force = request.query_params.get("refresh") == "1"
    import model_probe
    return JSONResponse(model_probe.list_remote_models(
        api_id, force_refresh=force,
        user_id=api_user["id"] if api_user else None,
    ))


@router.post("/api/models/remote/sync")
async def api_models_remote_sync(
    request: Request,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """用当前用户的 API Key 拉取供应商真实 /models，写进**该用户私有**的 overlay。

    这是 API Key 页面“可访问模型”的权威来源：静态 catalog 只提供 provider
    元数据（kind/base_url），不能冒充用户账号实际可访问的模型清单。

    安全(重大事故修复):历史上这里把同步结果写进全局 model_apis/model_entries,
    导致一个用户的 provider/模型泄露进所有人(含 admin)的模型选择器。现在改为
    写 user_model_entries(每用户隔离),只在该用户自己的 catalog 视图里 merge。
    全局菜单只有 admin 能改(/api/models/api)。
    """
    import model_probe
    from app import _check_probe_permission
    from model_registry import (
        default_api_for,
        find_api,
        load_catalog_for_user,
        load_model_catalog,
        normalize_api_id,
    )
    from platform_app import user_models

    user_id = int(api_user["id"]) if api_user and api_user.get("id") else None
    if not user_id:
        return JSONResponse({"ok": False, "error": "需要登录"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        body = {}
    api_id = normalize_api_id((body or {}).get("api_id", ""))
    if not api_id:
        return JSONResponse({"ok": False, "error": "api_id 不能为空"}, status_code=400)
    blocked = _check_probe_permission(api_user, api_id)
    if blocked:
        return blocked

    # provider 元数据解析(不写全局):全局菜单 > default_api 模板 > 用户自建中转站凭证。
    catalog = load_model_catalog()
    api = find_api(catalog, api_id) or {}
    default_api = default_api_for(api_id) or {}
    meta_api = {**default_api, **api}
    base_url = (body or {}).get("base_url") or meta_api.get("base_url", "")
    cred_base = ""
    try:
        from platform_app.user_credentials import get_credential
        _cred = get_credential(user_id, api_id)
        cred_base = (_cred or {}).get("base_url_override") or ""
    except Exception:
        cred_base = ""
    if not base_url:
        base_url = cred_base
    # 全局没这个 provider(自建中转站)→ 必须有 base_url 才能调,且按 openai_compat 路由
    kind = meta_api.get("kind") or ("openai_compat" if base_url else api_id)
    if not api and not base_url:
        return JSONResponse(
            {"ok": False, "error": f"未知 provider「{api_id}」需先在凭证里填写 base_url", "models": []},
            status_code=400,
        )

    api_meta = {
        "id": api_id,
        "display_name": meta_api.get("display_name") or api_id,
        "kind": kind,
        "credential_env": meta_api.get("credential_env", ""),
        "base_url": base_url,
    }

    remote = model_probe.list_remote_models(
        api_id,
        force_refresh=True,
        user_id=user_id,
        api_override=api_meta,
    )
    if not remote.get("ok"):
        return JSONResponse({**remote, "api_id": api_id, "synced": 0})

    synced_models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in remote.get("models") or []:
        real = str(item.get("real_name") or item.get("id") or "").strip()
        if not real or real in seen:
            continue
        seen.add(real)
        synced_models.append({
            "id": real,
            "real_name": real,
            "display_name": item.get("display_name") or real,
            "enabled": True,
            "capabilities": list(item.get("capabilities") or ["text", "streaming"]),
        })

    # 写每用户 overlay(绝不写全局)
    user_models.replace_synced_models(user_id, api_id, synced_models)
    saved = load_catalog_for_user(user_id)
    return JSONResponse({
        "ok": True,
        "api_id": api_id,
        "synced": len(synced_models),
        "remote_total": len(remote.get("models") or []),
        "models": synced_models,
        "catalog": saved,
    })


@router.get("/api/models/diff")
async def api_models_diff(
    request: Request,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """对比本地 catalog 和远端真实模型，返回 missing/extra/matching"""
    from app import _check_probe_permission
    api_id = request.query_params.get("api_id", "")
    blocked = _check_probe_permission(api_user, api_id)
    if blocked:
        return blocked
    import model_probe
    return JSONResponse(model_probe.diff_catalog(api_id, user_id=api_user["id"] if api_user else None))


@router.post("/api/models/probe", response_model=GenericOkResponse, responses={**COMMON_ERROR_RESPONSES, 403: {"model": ErrorResponse}})
async def api_models_probe(
    body: ModelsProbeRequest,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """发一条最小请求验证可用性 + 测延迟。

    安全：避免用别人的 key 测试。要么 user 自己配置过该 api_id 的凭证，
    要么必须是 admin。其他普通用户不允许触发付费 API 调用。
    """
    body_dict = body.model_dump(exclude_none=True)
    api_id = body_dict.get("api_id", "")
    # admin 可以测任何 provider；普通用户只能测自己配过 key 的 provider
    if api_user and api_user.get("role") != "admin":
        from platform_app import user_credentials as _ucreds
        cred = _ucreds.get_credential(api_user["id"], api_id)
        if not cred:
            return JSONResponse(
                {"ok": False, "error": "需要先在「个人主页 → API 凭证」中配置该 provider 的 key 才能测试"},
                status_code=403,
            )
    import model_probe
    return JSONResponse(model_probe.probe_availability(
        api_id,
        body_dict.get("model"),
        timeout_sec=int(body_dict.get("timeout", 15)),
        user_id=api_user["id"] if api_user else None,
    ))


@router.get("/api/models/pricing")
async def api_models_pricing(
    request: Request,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """查询单个模型的定价（USD per million tokens）"""
    from app import _check_probe_permission
    api_id = request.query_params.get("api_id", "")
    blocked = _check_probe_permission(api_user, api_id)
    if blocked:
        return blocked
    import model_probe
    from model_registry import find_api, find_model, load_model_catalog
    model_id = request.query_params.get("model", "")
    catalog = load_model_catalog()
    api = find_api(catalog, api_id)
    if not api:
        return JSONResponse({"ok": False, "error": f"api_id 不存在: {api_id}"})
    model = find_model(api, model_id)
    real_name = (model or {}).get("real_name") if model else model_id
    # 先用 api_id 查（按 provider 分组的定价表），找不到再用 kind 兜底
    pricing = model_probe.get_pricing(api_id, real_name, (model or {}).get("pricing"))
    if not pricing:
        pricing = model_probe.get_pricing(api.get("kind") or "", real_name)
    return JSONResponse({"ok": True, "api_id": api_id, "model": real_name, "pricing": pricing})


@router.get("/api/models/report")
async def api_models_report(
    request: Request,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """API 综合健康报告：catalog + 远端 diff + 定价 + 可选 probe"""
    from app import _check_probe_permission
    api_id = request.query_params.get("api_id", "")
    blocked = _check_probe_permission(api_user, api_id)
    if blocked:
        return blocked
    probe = request.query_params.get("probe") == "1"
    import model_probe
    return JSONResponse(model_probe.full_report(
        api_id, probe_model=probe,
        user_id=api_user["id"] if api_user else None,
    ))


@router.get("/api/models/capabilities")
async def api_models_capabilities(
    request: Request,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """查询单个模型的能力清单（text/vision/tools/json_mode 等）"""
    from app import _check_probe_permission
    api_id = request.query_params.get("api_id", "")
    blocked = _check_probe_permission(api_user, api_id)
    if blocked:
        return blocked
    import model_probe
    from model_registry import find_api, find_model, load_model_catalog
    model_id = request.query_params.get("model", "")
    catalog = load_model_catalog()
    api = find_api(catalog, api_id)
    if not api:
        return JSONResponse({"ok": False, "error": f"api_id 不存在: {api_id}"})
    model = find_model(api, model_id)
    real_name = (model or {}).get("real_name") if model else model_id
    caps = model_probe.get_capabilities(api_id, real_name, (model or {}).get("capabilities"))
    return JSONResponse({
        "ok": True,
        "api_id": api_id,
        "model": real_name,
        "capabilities": model_probe.describe_capabilities(caps),
        "capability_ids": caps,
    })


@router.get("/api/models/capabilities/labels")
async def api_models_capability_labels(
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """返回所有已知能力的标签词典（前端筛选器/徽标用）"""
    import model_probe
    return JSONResponse({"ok": True, "labels": model_probe.CAPABILITY_LABELS})
