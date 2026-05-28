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


@router.get("/api/models")
async def api_models(
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    from app import _redact_catalog, load_model_catalog, selected_model
    catalog = load_model_catalog()
    is_admin = bool(api_user and api_user.get("role") == "admin")
    return JSONResponse({
        "ok": True,
        "models": _redact_catalog(catalog, is_admin),
        "selected": selected_model(catalog),
    })


@router.post("/api/models/select", response_model=GenericOkResponse, responses=COMMON_ERROR_RESPONSES)
async def api_models_select(
    body: ModelsSelectRequest,
    api_user: dict[str, Any] | None = Depends(get_current_admin),
) -> JSONResponse:
    from app import (
        _gm_by_user,
        _payload,
        _state_lock,
        select_model,
        selected_model,
    )
    body_dict = body.model_dump(exclude_none=True)
    catalog = select_model(body_dict.get("api_id", ""), body_dict.get("model_id", ""))
    # 切换模型后清掉所有用户的 GM 缓存，下次会用新模型重建
    with _state_lock:
        _gm_by_user.clear()
    return JSONResponse({"ok": True, "models": catalog, "selected": selected_model(catalog), "state": _payload(api_user)})


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
    import model_probe
    from model_registry import find_api, find_model, load_model_catalog
    api_id = request.query_params.get("api_id", "")
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
    import model_probe
    from model_registry import find_api, find_model, load_model_catalog
    api_id = request.query_params.get("api_id", "")
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
