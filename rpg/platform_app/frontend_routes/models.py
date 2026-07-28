"""platform_app.frontend_routes.models —— MODELS 补充路由(可见性 & 校验)。

原单文件「MODELS supplements」段逐端点搬运,零行为变化:
/api/models/visibility(admin 全局)/ /api/me/models/visibility(每用户 overlay)/
/api/models/validate(凭据探测)。
"""
from __future__ import annotations

from fastapi import Request

from ..api import json_response, require_user
from ..api._deps import is_admin
from ._shared import _bad, router


# ------------------------------------------------------------
#  MODELS supplements (visibility & validate)
# ------------------------------------------------------------
@router.post("/api/models/visibility")
async def api_models_visibility(request: Request):
    """Toggle visibility flags for individual models.

    Writes through upsert_model so DB + JSON catalog stay in sync.
    Previously wrote only to model_entries.enabled, which diverged from the
    catalog JSON on restart when DB was unavailable.
    """
    # 全局模型目录写操作:与同级 /api/models/* 一致,须管理员(CWE-862)。
    user = require_user(request)
    if not is_admin(user):
        return _bad("需要管理员权限", status=403)
    body = await request.json() or {}
    api_id = body.get("api_id") or body.get("api")
    model_real_name = body.get("model") or body.get("real_name")
    visible = body.get("visible")
    if not api_id or model_real_name is None or visible is None:
        return _bad("缺少参数")
    from model_registry import find_api, load_model_catalog, upsert_model
    catalog = load_model_catalog()
    api = find_api(catalog, api_id)
    if not api:
        return _bad(f"未知 API: {api_id}")
    # locate model by real_name or id
    model_entry = next(
        (m for m in api.get("models", []) if m.get("real_name") == model_real_name or m.get("id") == model_real_name),
        None,
    )
    if not model_entry:
        return _bad(f"模型不存在: {model_real_name}")
    # upsert_model merges into catalog and calls save_model_catalog (writes DB + JSON)
    upsert_model(api_id, {**model_entry, "enabled": bool(visible)})
    return json_response({"ok": True})


@router.post("/api/me/models/visibility")
async def api_me_models_visibility(request: Request):
    """每用户:隐藏/显示自己【同步来的】单个模型(user_model_entries.enabled)。

    与上面 /api/models/visibility(admin、写全局 model_entries)不同 —— 这个任何用户都能调,
    只动自己的 overlay。用户「启用某 provider(如 openrouter)但只想留几个模型」靠这个,
    且 set 后下次 remote/sync 不会被重置(见 user_models.replace_synced_models 的 prev 保留)。
    body: {api_id, model(=model_id 或 real_name), visible: bool}
    """
    user = require_user(request)
    body = await request.json() or {}
    api_id = body.get("api_id") or body.get("api")
    model = body.get("model") or body.get("real_name") or body.get("model_id")
    visible = body.get("visible")
    if not api_id or model is None or visible is None:
        return _bad("缺少参数")
    from platform_app.user_models import set_overlay_model_enabled
    n = set_overlay_model_enabled(user["id"], api_id, model, bool(visible))
    if not n:
        return _bad("该模型不在你的同步清单里(只能隐藏你自己同步来的模型)", status=404)
    return json_response({"ok": True, "updated": n})


@router.post("/api/me/models/model")
async def api_me_models_model(request: Request):
    """每用户:往**自己的** overlay 里加/改一个手填模型(user_model_entries, source='manual')。

    与 /api/models/model(admin-only,写全局 catalog、写进去所有人可见)彻底分开 —— 用户
    自己的模型只属于他自己。这是「provider 没有 /models 接口」时的唯一途径:火山方舟
    Agent Plan 订阅套餐地址 /models 恒 404,模型 ID 只能手填。
    (前科:v1.76.0 的「添加模型」按钮误接了 admin 端点,普通用户直接撞「需要管理员权限」,
     且一旦成功会把私人模型写进全局目录。已整条回滚重写。)

    body: {api_id, real_name(必填), display_name?, capabilities?}
    """
    user = require_user(request)
    body = await request.json() or {}
    api_id = body.get("api_id") or body.get("api")
    real = (body.get("real_name") or body.get("model") or body.get("model_id") or "").strip()
    if not api_id or not real:
        return _bad("缺少参数:api_id 与 real_name 必填")
    from platform_app.user_models import upsert_manual_model
    saved = upsert_manual_model(user["id"], api_id, {
        "id": real,
        "real_name": real,
        "display_name": (body.get("display_name") or body.get("display") or real).strip() or real,
        "capabilities": body.get("capabilities") or ["text", "streaming"],
        "enabled": True,
    })
    if not saved:
        return _bad("参数不合法(api_id / real_name 无法规范化)")
    return json_response({"ok": True, "model": saved})


@router.post("/api/me/models/model/delete")
async def api_me_models_model_delete(request: Request):
    """每用户:从自己的 overlay 删掉一个模型。手填的删了就没了;同步来的下次同步会回来。"""
    user = require_user(request)
    body = await request.json() or {}
    api_id = body.get("api_id") or body.get("api")
    model = body.get("real_name") or body.get("model") or body.get("model_id")
    if not api_id or not model:
        return _bad("缺少参数")
    from platform_app.user_models import delete_overlay_model
    n = delete_overlay_model(user["id"], api_id, str(model))
    if not n:
        return _bad("该模型不在你的清单里", status=404)
    return json_response({"ok": True, "deleted": n})


@router.post("/api/models/validate")
async def api_models_validate(request: Request):
    """Lightweight credentials probe — defers to model_probe.list_remote_models."""
    user = require_user(request)
    body = await request.json() or {}
    api_id = body.get("api_id") or body.get("api_slug") or body.get("api") or ""
    try:
        from model_probe import list_remote_models, probe_availability
        if body.get("model"):
            out = probe_availability(
                api_id=api_id,
                model_real_name=body.get("model"),
                user_id=user["id"],
                timeout_sec=int(body.get("timeout", 8)),
            )
        else:
            out = list_remote_models(api_id=api_id, force_refresh=True, user_id=user["id"])
        return json_response({"ok": True, **(out if isinstance(out, dict) else {"result": out})})
    except Exception as e:
        return json_response({"ok": False, "error": str(e)}, status_code=400)
