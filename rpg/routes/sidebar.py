"""routes/sidebar.py — 游戏侧栏 inline-edit 端点。

补齐侧栏「运行时状态镜像 + 用户直接修改」中 30%→60% 的缺口。
全部走 dispatch_ui_tool(origin='ui_button'),拿 dispatcher 的统一审计 + destructive 检查。

端点:
  · POST /api/relationships/set         {character, status}
  · POST /api/relationships/delete      {character}
  · POST /api/world/set                 {key, value}
        key ∈ {time, weather, phase, location, <world.scalar 其它 set_world_attribute 接受的>}
        - time         → set_world_time  (经 update_time 同步 timeline.current_label/phase)
        - phase        → set_timeline_phase (硬覆盖 + mark_user_locked,后续 update_time 不刷)
        - location     → set_player_location
        - 其它 scalar  → set_world_attribute(value 必填)
  · POST /api/player/profile/set        {field, value}
        field ∈ {appearance, personality, speech_style, background} → set_player_<field>
        (整体替换 + mark_user_locked;此后 GM/史官不可自动改写)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from routes._deps_fastapi import get_current_user
from schemas._common import COMMON_ERROR_RESPONSES, StateResponse
from schemas.sidebar import (
    PlayerProfileSetRequest,
    RelationshipDeleteRequest,
    RelationshipSetRequest,
    WorldSetRequest,
)

router = APIRouter()


# ── 关系编辑 ───────────────────────────────────────────────────
@router.post("/api/relationships/set", response_model=StateResponse,
             responses=COMMON_ERROR_RESPONSES)
async def api_relationship_set(
    body: RelationshipSetRequest,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """设置/覆盖某个 NPC 的关系状态。复用现成 set_relationship tool。"""
    from app import (
        _ensure_loaded,
        _payload,
        _persist_runtime_checkpoint,
        _resolve_persist_target,
    )
    b = body.model_dump(exclude_none=True)
    character = (b.get("character") or "").strip()
    status = (b.get("status") or "").strip()
    if not character or not status:
        return JSONResponse({"ok": False, "error": "character / status 不能为空"}, status_code=400)
    state = _ensure_loaded(api_user)
    from tools_dsl.ui_dispatch_helper import dispatch_ui_tool
    result = dispatch_ui_tool(
        tool_name="set_relationship",
        args={"character": character, "status": status},
        user_id=int(api_user.get("id")) if api_user else 0,
        save_id=_resolve_persist_target(api_user)[1] or 0,
        state=state,
    )
    if not result.ok:
        return JSONResponse({"ok": False, "error": result.error}, status_code=400)
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "state": _payload(api_user)})


@router.post("/api/relationships/delete", response_model=StateResponse,
             responses=COMMON_ERROR_RESPONSES)
async def api_relationship_delete(
    body: RelationshipDeleteRequest,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """整条删除某 NPC 的关系。destructive,只允许 ui_button / llm_set / api_direct origin。"""
    from app import (
        _ensure_loaded,
        _payload,
        _persist_runtime_checkpoint,
        _resolve_persist_target,
    )
    b = body.model_dump(exclude_none=True)
    character = (b.get("character") or "").strip()
    if not character:
        return JSONResponse({"ok": False, "error": "character 不能为空"}, status_code=400)
    state = _ensure_loaded(api_user)
    from tools_dsl.ui_dispatch_helper import dispatch_ui_tool
    result = dispatch_ui_tool(
        tool_name="delete_relationship",
        args={"character": character},
        user_id=int(api_user.get("id")) if api_user else 0,
        save_id=_resolve_persist_target(api_user)[1] or 0,
        state=state,
    )
    if not result.ok:
        return JSONResponse({"ok": False, "error": result.error}, status_code=400)
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "state": _payload(api_user)})


# ── 玩家人设卡编辑 ─────────────────────────────────────────────
_PLAYER_PROFILE_FIELD_ALLOWLIST = {"appearance", "personality", "speech_style", "background"}


@router.post("/api/player/profile/set", response_model=StateResponse,
             responses=COMMON_ERROR_RESPONSES)
async def api_player_profile_set(
    body: PlayerProfileSetRequest,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """设置玩家人设卡字段(appearance/personality/speech_style/background)。整体替换,
    走 origin='ui_button' → 执行器 mark_user_locked,此后 GM/史官不可自动改写。"""
    from app import (
        _ensure_loaded,
        _payload,
        _persist_runtime_checkpoint,
        _resolve_persist_target,
    )
    b = body.model_dump(exclude_none=True)
    field = (b.get("field") or "").strip()
    value = (b.get("value") or "").strip()
    if field not in _PLAYER_PROFILE_FIELD_ALLOWLIST:
        return JSONResponse({"ok": False, "error": f"field 非法(允许 {sorted(_PLAYER_PROFILE_FIELD_ALLOWLIST)})"}, status_code=400)
    if not value:
        return JSONResponse({"ok": False, "error": "value 不能为空"}, status_code=400)
    state = _ensure_loaded(api_user)
    from tools_dsl.ui_dispatch_helper import dispatch_ui_tool
    result = dispatch_ui_tool(
        tool_name=f"set_player_{field}",
        args={field: value},
        user_id=int(api_user.get("id")) if api_user else 0,
        save_id=_resolve_persist_target(api_user)[1] or 0,
        state=state,
    )
    if not result.ok:
        return JSONResponse({"ok": False, "error": result.error}, status_code=400)
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "state": _payload(api_user)})


# ── world 状态编辑 ─────────────────────────────────────────────
# 前端 key 别名 → 后端 tool name + 参数转换
_WORLD_KEY_ALLOWLIST = {
    "time", "weather", "phase", "location", "atmosphere",
    "season", "region", "calendar",
}  # 只允许前端直接 set 的 scalar 字段；复合结构（timeline/known_events 等）不在此列


def _world_dispatch(key: str, value: str) -> tuple[str, dict[str, Any]]:
    """把前端的 (key, value) 翻译成 (tool_name, args)。返回 (None, {}) 表示拒绝。"""
    if key not in _WORLD_KEY_ALLOWLIST:
        return "", {}
    if key == "time":
        return "set_world_time", {"target": value}
    if key == "phase":
        return "set_timeline_phase", {"phase": value}
    if key == "location":
        return "set_player_location", {"location": value}
    # 其它 scalar(weather/atmosphere/season/region 等)走 set_world_attribute
    return "set_world_attribute", {"key": key, "value": value}


@router.post("/api/world/set", response_model=StateResponse,
             responses=COMMON_ERROR_RESPONSES)
async def api_world_set(
    body: WorldSetRequest,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """侧栏「世界书 → 当下世界」inline 编辑入口。
    key=time 走 update_time(同步 timeline.current_label/_phase);
    key=phase 走 set_timeline_phase(硬覆盖 + mark_user_locked);
    key=location 走 update_location;
    其它 scalar 走 set_world_attribute。
    """
    from app import (
        _ensure_loaded,
        _payload,
        _persist_runtime_checkpoint,
        _resolve_persist_target,
    )
    b = body.model_dump(exclude_none=True)
    key = (b.get("key") or "").strip()
    value = (b.get("value") or "").strip()
    if not key:
        return JSONResponse({"ok": False, "error": "key 不能为空"}, status_code=400)
    if not value:
        return JSONResponse({"ok": False, "error": "value 不能为空"}, status_code=400)
    tool_name, args = _world_dispatch(key, value)
    if not tool_name:
        return JSONResponse(
            {"ok": False, "error": f"world.{key} 不允许通过此端点直接修改"},
            status_code=400,
        )
    state = _ensure_loaded(api_user)
    from tools_dsl.ui_dispatch_helper import dispatch_ui_tool
    result = dispatch_ui_tool(
        tool_name=tool_name,
        args=args,
        user_id=int(api_user.get("id")) if api_user else 0,
        save_id=_resolve_persist_target(api_user)[1] or 0,
        state=state,
    )
    if not result.ok:
        return JSONResponse({"ok": False, "error": result.error}, status_code=400)
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "state": _payload(api_user)})
