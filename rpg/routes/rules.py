"""rules.py — 5E 规则模组与战斗路由 (/api/rules/*)。"""
from __future__ import annotations
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/api/rules/modules")
async def api_rules_modules(request: Request) -> JSONResponse:
    """列出可用的 5E-compatible 冒险模组。"""
    from app import _require_api_user
    import modules as _rules_module_registry
    _require_api_user(request)
    return JSONResponse({"ok": True, "modules": _rules_module_registry.list_modules()})


@router.post("/api/rules/module/start")
async def api_rules_module_start(request: Request) -> JSONResponse:
    """低层原语：把模组加载到当前激活的 save，会直接 mutate 该 save state。
    task 87 Phase 6: 走 dispatcher module_load 工具(destructive,UI 直触发)。"""
    from app import (
        _require_api_user, _payload, _ensure_loaded, _resolve_persist_target,
        _persist_runtime_checkpoint, _rules_payload,
    )
    api_user = _require_api_user(request)
    body = await request.json()
    module_id = str(body.get("module_id") or "ash_mine").strip()
    character_overrides = body.get("character") or None

    state = _ensure_loaded(api_user)
    from ui_dispatch_helper import dispatch_ui_tool
    d_result = dispatch_ui_tool(
        tool_name="module_load",
        args={"module_id": module_id, "character_overrides": character_overrides},
        user_id=int(api_user.get("id")) if api_user else 0,
        save_id=_resolve_persist_target(api_user)[1] or 0,
        state=state,
    )
    if not d_result.ok:
        raise HTTPException(status_code=400, detail=d_result.error or "module_load 失败")
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "rules": _rules_payload(state),
                         "opening": "", "state": _payload(api_user)})


@router.post("/api/rules/module/launch")
async def api_rules_module_launch(request: Request) -> JSONResponse:
    """Bug 2：模组启动的标准入口。

    流程：
      1. 后端真正建立一个**独立 game_save**（kind=module_adventure 标题=模组名）
      2. 用模组开局状态填 state_snapshot（Cinder + 灰烬矿坑 scene 等）
      3. 激活该 save（切 runtime_checkout / user_runtime / 缓存）
      4. 返回新 save_id + 状态 → FE 跳 Game Console 看到的就是新存档

    绝不 mutate 当前小说/普通 save。已注册用户必填（匿名不允许，避免污染本地默认 save）。
    """
    from app import (
        _require_api_user, _payload, _ensure_loaded, _invalidate_user_cache,
        _persist_runtime_checkpoint, _rules_payload, SAVE_FILE, GameState,
    )
    api_user = _require_api_user(request)
    if not api_user or not api_user.get("id"):
        raise HTTPException(status_code=401, detail="启动模组需要登录")
    body = await request.json()
    module_id = str(body.get("module_id") or "ash_mine").strip()
    if not module_id:
        raise HTTPException(status_code=400, detail="缺少 module_id")
    character_overrides = body.get("character") or None
    custom_title = str(body.get("title") or "").strip()

    import modules as _rules_module_registry
    from rules_bridge import start_module as _rb_start_module

    # 加载模组 manifest 取标题
    try:
        bundle = _rules_module_registry.load_module(module_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"未知模组 {module_id}：{exc}")
    manifest = bundle.get("manifest") or {}
    title = custom_title or manifest.get("name_cn") or manifest.get("name") or module_id

    # 找到（或创建）一个属于本用户的 ad-hoc script，作为模组 save 的 owner script。
    # 模组不依赖小说章节，但 game_saves.script_id 是 NOT NULL 外键 → 必须给个 script。
    # 复用 ad-hoc"模组容器"剧本，避免每次都建新 script row。
    from platform_app.db import connect as _db_connect
    user_id = int(api_user["id"])
    with _db_connect() as db:
        scr = db.execute(
            "select id from scripts where owner_id = %s and title = %s",
            (user_id, "[内部] 5E 模组容器"),
        ).fetchone()
        if scr:
            container_script_id = int(scr["id"])
        else:
            scr = db.execute(
                "insert into scripts(owner_id, title) values (%s, %s) returning id",
                (user_id, "[内部] 5E 模组容器"),
            ).fetchone()
            container_script_id = int(scr["id"])

    # 用一个空的临时 GameState 跑 rules_bridge.start_module 拿到完整初始 snapshot
    tmp_state = GameState.new()
    res = _rb_start_module(tmp_state, module_id, character_overrides=character_overrides)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "start_module 失败"))

    # 把初始 snapshot 写入新 save
    from platform_app import branches as _branches
    from psycopg.types.json import Jsonb as _Jsonb
    with _db_connect() as db:
        save_row = db.execute(
            """
            insert into game_saves(user_id, script_id, title, state_path, state_snapshot)
            values (%s, %s, %s, %s, %s)
            returning *
            """,
            (user_id, container_script_id, title, str(SAVE_FILE), _Jsonb(tmp_state.data)),
        ).fetchone()
    save_id = int(save_row["id"])
    _branches.seed_tree(save_id, str(SAVE_FILE))
    # 激活
    _branches.activate_save(user_id, save_id)
    # 清缓存让 _ensure_loaded 重读
    _invalidate_user_cache(api_user)

    # 重新拉 state
    state = _ensure_loaded(api_user)
    return JSONResponse({
        "ok": True,
        "save_id": save_id,
        "save_title": title,
        "rules": _rules_payload(state),
        "opening": res.get("opening") or "",
        "state": _payload(api_user),
    })


@router.get("/api/rules/scene")
async def api_rules_scene(request: Request) -> JSONResponse:
    """返回当前 scene / player_character / encounter / dice_log 快照。"""
    from app import _require_api_user, _ensure_loaded, _rules_payload
    api_user = _require_api_user(request)
    state = _ensure_loaded(api_user)
    return JSONResponse({"ok": True, "rules": _rules_payload(state)})


@router.post("/api/rules/move")
async def api_rules_move(request: Request) -> JSONResponse:
    """task 87 Phase 6: 走 dispatcher module_enter_room 工具。"""
    from app import (
        _require_api_user, _ensure_loaded, _resolve_persist_target,
        _persist_runtime_checkpoint, _rules_payload,
        _clear_pending_questions_after_rule_action, _append_rules_receipt, _room_receipt,
    )
    api_user = _require_api_user(request)
    body = await request.json()
    location_id = str(body.get("to") or "").strip()
    if not location_id:
        raise HTTPException(status_code=400, detail="缺少 to")
    state = _ensure_loaded(api_user)
    from ui_dispatch_helper import dispatch_ui_tool
    d_result = dispatch_ui_tool(
        tool_name="module_enter_room",
        args={"location_id": location_id},
        user_id=int(api_user.get("id")) if api_user else 0,
        save_id=_resolve_persist_target(api_user)[1] or 0,
        state=state,
    )
    if not d_result.ok:
        return JSONResponse({"ok": False, "error": d_result.error}, status_code=400)
    _clear_pending_questions_after_rule_action(state, f"move:{location_id}")
    # 从 state.scene 重新读 current_room 做 receipt
    room = (state.data.get("scene") or {}).get("current_room") or {}
    _append_rules_receipt(state, _room_receipt(room))
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "rules": _rules_payload(state), "room": room})


@router.post("/api/rules/action")
async def api_rules_action(request: Request) -> JSONResponse:
    """通用规则动作执行入口。根据 body.kind 路由到具体规则函数。"""
    from app import (
        _require_api_user, _ensure_loaded, _persist_runtime_checkpoint,
        _rules_payload, _execute_rules_action,
        _clear_pending_questions_after_rule_action, _append_rules_receipt, _action_receipt,
    )
    api_user = _require_api_user(request)
    body = await request.json()
    state = _ensure_loaded(api_user)

    out = _execute_rules_action(state, body)
    if not out.get("ok"):
        return JSONResponse(out, status_code=400)

    _clear_pending_questions_after_rule_action(state, f"rules:{body.get('kind') or 'action'}")
    _append_rules_receipt(state, _action_receipt(body, out))
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    out["rules"] = _rules_payload(state)
    return JSONResponse(out)


@router.post("/api/rules/encounter/start")
async def api_rules_encounter_start(request: Request) -> JSONResponse:
    """task 87 Phase 6: 走 dispatcher combat_start 工具。"""
    from app import (
        _require_api_user, _ensure_loaded, _resolve_persist_target,
        _persist_runtime_checkpoint, _rules_payload,
        _clear_pending_questions_after_rule_action, _append_rules_receipt, _encounter_receipt,
    )
    api_user = _require_api_user(request)
    body = await request.json()
    encounter_id = str(body.get("encounter_id") or "").strip()
    if not encounter_id:
        raise HTTPException(status_code=400, detail="缺少 encounter_id")
    seed = body.get("seed")
    state = _ensure_loaded(api_user)
    from ui_dispatch_helper import dispatch_ui_tool
    args: dict = {"encounter_id": encounter_id}
    if seed is not None and str(seed).lstrip("-").isdigit():
        args["seed"] = int(seed)
    d_result = dispatch_ui_tool(
        tool_name="combat_start", args=args,
        user_id=int(api_user.get("id")) if api_user else 0,
        save_id=_resolve_persist_target(api_user)[1] or 0,
        state=state,
    )
    if not d_result.ok:
        return JSONResponse({"ok": False, "error": d_result.error}, status_code=400)
    encounter = state.data.get("encounter") or {}
    _clear_pending_questions_after_rule_action(state, f"encounter:start:{encounter_id}")
    _append_rules_receipt(state, _encounter_receipt("先攻", {"encounter": encounter}))
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "rules": _rules_payload(state), "encounter": encounter})


@router.post("/api/rules/encounter/next")
async def api_rules_encounter_next(request: Request) -> JSONResponse:
    """task 87 Phase 6: 走 dispatcher combat_next_turn 工具。"""
    from app import (
        _require_api_user, _ensure_loaded, _resolve_persist_target,
        _persist_runtime_checkpoint, _rules_payload,
        _clear_pending_questions_after_rule_action, _append_rules_receipt, _encounter_receipt,
    )
    api_user = _require_api_user(request)
    state = _ensure_loaded(api_user)
    from ui_dispatch_helper import dispatch_ui_tool
    d_result = dispatch_ui_tool(
        tool_name="combat_next_turn", args={},
        user_id=int(api_user.get("id")) if api_user else 0,
        save_id=_resolve_persist_target(api_user)[1] or 0,
        state=state,
    )
    if not d_result.ok:
        return JSONResponse({"ok": False, "error": d_result.error}, status_code=400)
    encounter = state.data.get("encounter") or {}
    _clear_pending_questions_after_rule_action(state, "encounter:next")
    _append_rules_receipt(state, _encounter_receipt("下一回合", {"encounter": encounter}))
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "rules": _rules_payload(state), "encounter": encounter})


@router.post("/api/rules/encounter/enemy")
async def api_rules_encounter_enemy(request: Request) -> JSONResponse:
    """敌方回合：task 87 Phase 6 走 dispatcher combat_enemy_attack。"""
    from app import (
        _require_api_user, _ensure_loaded, _resolve_persist_target,
        _persist_runtime_checkpoint, _rules_payload,
        _clear_pending_questions_after_rule_action, _append_rules_receipt, _encounter_receipt,
    )
    api_user = _require_api_user(request)
    body = await request.json()
    attacker_id = str(body.get("attacker_id") or "").strip()
    target_id = str(body.get("target_id") or "player").strip()
    seed = body.get("seed")
    state = _ensure_loaded(api_user)
    from ui_dispatch_helper import dispatch_ui_tool
    args: dict = {"attacker_id": attacker_id, "target_id": target_id}
    if seed is not None and str(seed).lstrip("-").isdigit():
        args["seed"] = int(seed)
    d_result = dispatch_ui_tool(
        tool_name="combat_enemy_attack", args=args,
        user_id=int(api_user.get("id")) if api_user else 0,
        save_id=_resolve_persist_target(api_user)[1] or 0,
        state=state,
    )
    if not d_result.ok:
        return JSONResponse({"ok": False, "error": d_result.error}, status_code=400)
    encounter = state.data.get("encounter") or {}
    _clear_pending_questions_after_rule_action(state, f"enemy:{attacker_id}")
    _append_rules_receipt(state, _encounter_receipt(
        "敌方攻击", {"result": {"target_name": target_id, "summary": d_result.result}}
    ))
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "rules": _rules_payload(state),
                         "result": {"summary": d_result.result},
                         "encounter": encounter})


@router.post("/api/rules/suggest")
async def api_rules_suggest(request: Request) -> JSONResponse:
    """从玩家自由文本输入推断候选规则动作（轻量本地匹配，用于前端候选按钮）。"""
    from app import _require_api_user, _ensure_loaded
    from rules_bridge import suggest_rule_actions as _rb_suggest_rule_actions
    api_user = _require_api_user(request)
    body = await request.json()
    text = str(body.get("text") or "")
    state = _ensure_loaded(api_user)
    return JSONResponse({"ok": True, "actions": _rb_suggest_rule_actions(text, state)})
