"""
ui.py - local Claude-like RPG workspace

Run:
    cd rpg/
    ../rpg_env/bin/python ui.py

Then open http://127.0.0.1:7860
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
import re
import shutil
import sys
import time
import uuid
import webbrowser
from pathlib import Path
from threading import Event, Lock
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from starlette.middleware.gzip import GZipMiddleware

load_dotenv(Path(__file__).parent.parent / ".env")

sys.path.insert(0, str(Path(__file__).parent))

from gm import GameMaster
from context_engine import build_context_bundle
from context_agent import run_context_agent
from model_registry import load_model_catalog, selected_model, select_model, upsert_api, upsert_model
from retrieval import retrieve_context
from state import GameState, SAVE_FILE
from tool_registry import (
    delete_mcp_server,
    import_skill_bundle,
    set_mcp_server_enabled,
    tool_payload,
    upsert_mcp_server,
    validate_mcp_server,
)
from platform_app import branches as platform_branches
from platform_app import knowledge as platform_knowledge
from platform_app import runtime as platform_runtime
from platform_app.api import current_user as platform_current_user
from platform_app.api import router as platform_router

APP_TITLE = "我蕾穆丽娜不爱你"
MODEL_LABEL = "Gemini 3.5 Flash"
HOST = "127.0.0.1"
PORT = 7860
APP_DIR = Path(__file__).parent
UPLOAD_DIR = APP_DIR / "uploads"
MAX_ATTACHMENT_BYTES = 12 * 1024 * 1024
API_VERSION = "1"

app = FastAPI(title=f"{APP_TITLE} RPG")


def _cors_origins() -> tuple[list[str], bool]:
    default_origins = (
        "http://127.0.0.1:7860,http://localhost:7860,"
        "http://127.0.0.1:5173,http://localhost:5173,"
        "http://127.0.0.1:3000,http://localhost:3000"
    )
    raw = os.environ.get("RPG_CORS_ORIGINS", default_origins)
    origins = [item.strip() for item in raw.split(",") if item.strip()]
    if not origins:
        origins = ["http://127.0.0.1:7860", "http://localhost:7860"]
    allow_all = "*" in origins
    return (["*"] if allow_all else origins), not allow_all


_origins, _allow_credentials = _cors_origins()


_LOCAL_MODES = {"local", "desktop", "self_hosted", "self-hosted"}
_SERVER_MODES = {"server", "production", "prod", "cloud"}


def _deployment_mode() -> str:
    mode = os.environ.get("RPG_DEPLOYMENT_MODE", "local").strip().lower() or "local"
    return mode


def _api_auth_required() -> bool:
    """鉴权规则（优先级从高到低）：
      1. RPG_REQUIRE_AUTH=1     → 强制鉴权
      2. RPG_REQUIRE_AUTH=0     → 强制关闭（仅本地/桌面用，慎用）
      3. RPG_DEPLOYMENT_MODE in {server,production,prod,cloud}  → 强制鉴权
      4. RPG_DEPLOYMENT_MODE in {local,desktop,self_hosted}     → 不强制
      5. 未设置                  → 默认本地模式，不强制
    """
    explicit = os.environ.get("RPG_REQUIRE_AUTH", "").strip()
    if explicit == "1":
        return True
    if explicit == "0":
        return False
    mode = _deployment_mode()
    if mode in _SERVER_MODES:
        return True
    if mode in _LOCAL_MODES:
        return False
    # 未知部署模式：保守起见，强制鉴权
    return True


def _startup_auth_banner() -> None:
    """启动时打印一次部署模式 + 鉴权策略，避免运维误判。"""
    mode = _deployment_mode()
    required = _api_auth_required()
    explicit = os.environ.get("RPG_REQUIRE_AUTH", "")
    source = f"RPG_REQUIRE_AUTH={explicit}" if explicit else f"RPG_DEPLOYMENT_MODE={mode}"
    if required:
        print(f"[启动] 部署模式={mode} 鉴权=强制 (源={source})")
    else:
        print(f"[启动] 部署模式={mode} 鉴权=不强制 (源={source}) — 仅适用于单用户本地使用")


def _require_api_user(request: Request, *, admin: bool = False) -> dict[str, Any] | None:
    user = platform_current_user(request)
    if not _api_auth_required():
        return user
    if not user:
        raise HTTPException(status_code=401, detail="需要登录")
    if admin and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def _resolve_persist_target(api_user: dict[str, Any] | None) -> tuple[int | None, int | None]:
    """返回 (user_id, save_id)，用于 DB 写入。

    本地未登录时回退到 runtime.json 里的当前激活存档所有者，
    保证 messages/context_runs/memories 表能被写入。
    服务器部署/已登录场景维持原行为。
    """
    if api_user:
        runtime_meta = platform_runtime.read_runtime(user_id=api_user["id"]) or platform_branches.bootstrap_runtime_binding(
            user_id=api_user["id"]
        )
        # 严格校验：runtime 必须属于当前用户
        if runtime_meta and int(runtime_meta.get("user_id") or 0) != int(api_user["id"]):
            runtime_meta = platform_branches.bootstrap_runtime_binding(user_id=api_user["id"])
        save_id = int((runtime_meta or {}).get("save_id") or 0) or None
        return api_user["id"], save_id

    # 未登录：仅在本地模式回退
    if _api_auth_required():
        return None, None

    runtime_meta = platform_runtime.read_runtime() or platform_branches.bootstrap_runtime_binding()
    if not runtime_meta:
        return None, None
    save_id = int(runtime_meta.get("save_id") or 0) or None
    user_id = int(runtime_meta.get("user_id") or 0) or None
    return user_id, save_id


def _origin_allowed(origin: str | None) -> bool:
    if not origin:
        return True
    return "*" in _origins or origin in _origins


MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-API-Version", "X-Request-ID"],
    max_age=int(os.environ.get("RPG_CORS_MAX_AGE", "86400")),
)
app.add_middleware(GZipMiddleware, minimum_size=int(os.environ.get("RPG_GZIP_MIN_BYTES", "1024")))
app.include_router(platform_router)
try:
    from platform_app.frontend_routes import router as _frontend_router
    app.include_router(_frontend_router)
except Exception as _e:
    print(f"[启动] frontend_routes 未挂载：{_e}")

# 启动时一次性触发 schema + migration，避免请求路径 DDL 撞锁
from platform_app.db import init_db as _bootstrap_init_db
try:
    _bootstrap_init_db()
except Exception as _e:
    print(f"[启动] init_db 失败：{_e}")

_startup_auth_banner()


# ── 全局异常 → 4xx，避免 500 泄露 stack trace ─────────────────────────────
from fastapi.exceptions import RequestValidationError
from json import JSONDecodeError

@app.exception_handler(ValueError)
async def _value_error_handler(request: Request, exc: ValueError):
    return JSONResponse({"ok": False, "error": str(exc) or "invalid value"}, status_code=400)

@app.exception_handler(KeyError)
async def _key_error_handler(request: Request, exc: KeyError):
    return JSONResponse({"ok": False, "error": f"missing field: {exc}"}, status_code=400)

@app.exception_handler(TypeError)
async def _type_error_handler(request: Request, exc: TypeError):
    msg = str(exc)
    # 主要 catch int(None) / NoneType subscript 这种"传参类型不对"
    return JSONResponse({"ok": False, "error": f"invalid input type: {msg[:200]}"}, status_code=400)

@app.exception_handler(JSONDecodeError)
async def _json_decode_handler(request: Request, exc: JSONDecodeError):
    return JSONResponse({"ok": False, "error": "invalid JSON body"}, status_code=400)

@app.exception_handler(PermissionError)
async def _permission_handler(request: Request, exc: PermissionError):
    return JSONResponse({"ok": False, "error": str(exc) or "forbidden"}, status_code=403)

@app.exception_handler(FileNotFoundError)
async def _file_not_found_handler(request: Request, exc: FileNotFoundError):
    return JSONResponse({"ok": False, "error": str(exc) or "not found"}, status_code=404)


@app.on_event("startup")
async def _start_mcp_health() -> None:
    try:
        import mcp_broker
        mcp_broker.start_health_loop()
    except Exception:
        pass


@app.on_event("startup")
async def _recover_durable_jobs() -> None:
    """B5：worker 重启时把 DB 里 pending + 超时 running 的 sync job 重新提交进线程池。
    多 worker 同时执行也安全：UPDATE 用 WHERE status=... + 唯一索引兜底，每个 job 只会被一个 worker 真正领走。
    """
    try:
        from platform_app import script_import
        result = script_import.recover_pending_sync_jobs()
        if result.get("recovered_pending") or result.get("reclaimed_stale"):
            import logging
            logging.getLogger("rpg.startup").info(
                "durable sync recovery: pending=%s stale=%s resubmitted=%s",
                result.get("recovered_pending"),
                result.get("reclaimed_stale"),
                len(result.get("resubmitted", [])),
            )
    except Exception:
        # 启动恢复失败不应阻挡服务启动；下次有人调度时会带走 pending
        import logging
        logging.getLogger("rpg.startup").exception("durable sync recovery failed")


@app.on_event("shutdown")
async def _shutdown_mcp_brokers() -> None:
    """uvicorn 退出时优雅关闭所有 MCP 子进程，避免僵尸进程。"""
    try:
        import mcp_broker
        mcp_broker.stop_health_loop()
        mcp_broker.stop_all()
    except Exception:
        pass


@app.middleware("http")
async def api_contract_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    original_path = request.scope.get("path", "")
    prefix = f"/api/v{API_VERSION}"
    if original_path == prefix:
        request.scope["path"] = "/api"
    elif original_path.startswith(prefix + "/"):
        request.scope["path"] = "/api" + original_path[len(prefix):]
    if original_path.startswith("/api") and request.method in MUTATING_METHODS:
        origin = request.headers.get("origin")
        if not _origin_allowed(origin):
            return JSONResponse(
                {"ok": False, "error": "Origin 不在允许列表", "request_id": request_id},
                status_code=403,
                headers={"X-API-Version": API_VERSION, "X-Request-ID": request_id, "Cache-Control": "no-store"},
            )
    response = await call_next(request)
    if original_path.startswith("/api"):
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers["X-API-Version"] = API_VERSION
        response.headers["X-Request-ID"] = request_id
        response.headers.setdefault("Vary", "Origin")
    return response

_state_by_user: dict[int, GameState] = {}     # key = api_user["id"] 或 0 (anonymous local)
_gm_by_user: dict[int, GameMaster] = {}
# B4: 子代理使用独立 GameMaster 实例，独立模型 / 独立 usage / 独立日志
_sub_gm_by_user: dict[int, GameMaster] = {}
_state_mtime_by_user: dict[int, int] = {}
_state_lock = Lock()
_run_lock = Lock()
# 多用户安全：每个 user 独立的 run_id / stop_event。
# 全局 _run_id/_stop_event 会让一个用户的 /api/stop 打断所有其他用户正在跑的 chat。
_run_id_by_user: dict[int, int] = {}
_stop_events_by_user: dict[int, Event] = {}


def _get_run_state(api_user: dict[str, Any] | None) -> tuple[int, Event]:
    """返回 (current_run_id, stop_event) 给当前用户"""
    uid = _user_key(api_user)
    with _run_lock:
        if uid not in _stop_events_by_user:
            _stop_events_by_user[uid] = Event()
        _run_id_by_user[uid] = _run_id_by_user.get(uid, 0) + 1
        _stop_events_by_user[uid].clear()
        return _run_id_by_user[uid], _stop_events_by_user[uid]


def _current_run_id(api_user: dict[str, Any] | None) -> int:
    return _run_id_by_user.get(_user_key(api_user), 0)


def _stop_user(api_user: dict[str, Any] | None) -> None:
    """同时设置进程内信号 + DB 跨进程信号，多 worker 部署也能 stop 到正确的请求。"""
    uid = _user_key(api_user)
    with _run_lock:
        ev = _stop_events_by_user.get(uid)
        if ev:
            ev.set()
    # 跨进程：写 DB stop_signals
    if api_user:
        try:
            from platform_app.cluster import request_stop
            current_run = _run_id_by_user.get(uid, 0)
            if current_run:
                request_stop(int(api_user["id"]), current_run)
        except Exception:
            pass


def _is_stop_requested_global(api_user: dict[str, Any] | None, run_id: int) -> bool:
    """合并检查：进程内 event + DB 跨进程信号。"""
    uid = _user_key(api_user)
    ev = _stop_events_by_user.get(uid)
    if ev and ev.is_set():
        return True
    if api_user:
        try:
            from platform_app.cluster import is_stop_requested
            if is_stop_requested(int(api_user["id"]), run_id):
                return True
        except Exception:
            pass
    return False


def _user_key(api_user: dict[str, Any] | None) -> int:
    """统一返回 cache key：登录用户用其 id，本地匿名用 0"""
    return int(api_user["id"]) if api_user else 0

ROLES = {
    "穿越者·魔女（白毛红瞳，魔力∞）": "穿越者·魔女",
    "欧洲世家信使 - 在各方势力间传递消息": "欧洲世家信使",
    "地联太平洋方面情报协力人员": "地联太平洋方面情报协力人员",
    "薇瑟帝国流亡边缘贵族": "薇瑟帝国流亡边缘贵族",
}

PRESET = {
    "穿越者·魔女（白毛红瞳，魔力∞）": {
        "name": "杭雁菱",
        "background": (
            "原为27岁社畜打工人晓卡，穿越后成为魔力∞的魔女。穿越落点在火星，剧情开始时。"
            "外表白发红瞳，看起来像个少女，实际年龄∞。读过这个世界的原著小说，但现实和书里总有出入。"
        ),
    }
}


def _ensure_loaded(api_user: dict[str, Any] | None = None) -> GameState:
    """加载当前用户的游戏状态。多用户安全：按 user_id 隔离。

    优先走 state_repository（DB 权威源 + 按 user 隔离 + JSON 镜像兜底）。
    每个 user 独立缓存 _state / _gm，避免跨 user 串数据。
    """
    uid = _user_key(api_user)
    with _state_lock:
        cached = _state_by_user.get(uid)
        # 匿名模式下还要看 SAVE_FILE mtime（兼容旧行为）
        if uid == 0:
            current_mtime = SAVE_FILE.stat().st_mtime_ns if SAVE_FILE.exists() else 0
            if cached is None or current_mtime != _state_mtime_by_user.get(uid, 0):
                cached = None
        if cached is None:
            try:
                from state_repository import load_active_state
                state, _ = load_active_state(user_id=api_user["id"] if api_user else None)
            except Exception:
                state = GameState.new() if api_user else GameState.load_or_new()
            _state_by_user[uid] = state
            if uid == 0:
                _state_mtime_by_user[uid] = SAVE_FILE.stat().st_mtime_ns if SAVE_FILE.exists() else 0
        if uid not in _gm_by_user:
            model = selected_model()
            _gm_by_user[uid] = GameMaster(
                api_id=model["api_id"],
                model=model["real_name"],
                user_id=api_user["id"] if api_user else None,
            )
        return _state_by_user[uid]


def _invalidate_user_cache(api_user: dict[str, Any] | None) -> None:
    uid = _user_key(api_user)
    with _state_lock:
        _state_by_user.pop(uid, None)
        _gm_by_user.pop(uid, None)
        _sub_gm_by_user.pop(uid, None)
        _state_mtime_by_user.pop(uid, None)


def _get_gm(api_user: dict[str, Any] | None) -> GameMaster:
    _ensure_loaded(api_user)
    return _gm_by_user[_user_key(api_user)]


def _get_sub_gm(api_user: dict[str, Any] | None) -> GameMaster:
    """B4: 子代理用独立 GameMaster 实例（条件：用户配置了 override）。

    模型选择优先级：
      1. user_preferences.sub_agent_model_override = {api_id, model} → 真·独立实例
      2. 无 override → 复用主 GM 实例（避免 init SDK 二次成本），但 usage 仍按
         "子代理"标签独立记账（snapshot last_usage 后立刻 record）

    无论哪种情况，调用方都应该用「_get_sub_gm(api_user)」拿到的对象去做 curate_context，
    后续 record_usage 时显式标 metadata.kind='sub_agent'。
    """
    uid = _user_key(api_user)
    # 快路径：缓存命中无需取锁的 _get_gm 重入
    cached = _sub_gm_by_user.get(uid)
    if cached is not None:
        return cached
    # 注意：_get_gm/_ensure_loaded 内部会取 _state_lock；这里必须先释放再调，
    # 因为 _state_lock 是非可重入 Lock。
    main_gm = _get_gm(api_user)
    override: dict[str, Any] = {}
    if api_user:
        try:
            from platform_app.db import connect as _connect
            with _connect() as _db:
                _row = _db.execute(
                    "select preferences from user_preferences where user_id = %s",
                    (api_user["id"],),
                ).fetchone()
            prefs = (_row or {}).get("preferences") or {}
            override = prefs.get("sub_agent_model_override") or {}
        except Exception:
            override = {}

    need_separate = bool(
        override
        and (
            override.get("api_id") and override["api_id"] != main_gm.api_id
            or override.get("model") and override["model"] != main_gm._backend.model_name
        )
    )
    if need_separate:
        try:
            sub = GameMaster(
                api_id=override.get("api_id") or main_gm.api_id,
                model=override.get("model") or main_gm._backend.model_name,
                user_id=api_user["id"] if api_user else None,
            )
            print(f"[SUB-AGENT] uid={uid} 独立实例 api={sub.api_id} model={sub._backend.model_name}")
        except Exception as exc:
            print(f"[SUB-AGENT] 独立实例创建失败 ({exc})，回退共用主 GM")
            sub = main_gm
    else:
        sub = main_gm
        print(f"[SUB-AGENT] uid={uid} 复用主 GM api={main_gm.api_id}")
    # 写回缓存时取锁，但这里不会再 reenter
    with _state_lock:
        _sub_gm_by_user.setdefault(uid, sub)
        return _sub_gm_by_user[uid]


def _backup_save(reason: str) -> str | None:
    if not SAVE_FILE.exists():
        return None
    backup_dir = SAVE_FILE.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = backup_dir / f"game_state_{stamp}_{reason}.json"
    shutil.copy2(SAVE_FILE, backup)
    return str(backup)


def _payload(api_user: dict[str, Any] | None = None) -> dict[str, Any]:
    state = _ensure_loaded(api_user)
    model_catalog = load_model_catalog()
    model = selected_model(model_catalog)
    is_admin = bool(api_user and api_user.get("role") == "admin")
    payload = state.status_payload()
    payload["app"] = {
        "title": APP_TITLE,
        "model": model["display_name"],
        "model_real_name": model["real_name"],
        "model_capabilities": model.get("capabilities", []),
        "api": model["api_display_name"],
        "api_id": model["api_id"],
        "roles": list(ROLES.keys()),
        "preset": PRESET,
    }
    # 绝对路径仅 admin 可见
    if is_admin:
        payload["app"]["save_file"] = str(SAVE_FILE)
    # catalog 按角色脱敏（普通用户拿不到 credential_ref/credential_env/base_url）
    payload["models"] = _redact_catalog(model_catalog, is_admin)
    payload["tools"] = _redact_tools(tool_payload(), is_admin)
    return payload


def _redact_catalog(catalog: dict[str, Any], is_admin: bool) -> dict[str, Any]:
    """普通用户拿不到 credential_ref / credential_env / base_url（部署形状信息）"""
    if is_admin:
        return catalog
    import copy
    redacted = copy.deepcopy(catalog)
    for api in redacted.get("apis", []):
        api.pop("credential_ref", None)
        api.pop("credential_env", None)
        api.pop("base_url", None)
    return redacted


_MCP_SECRET_FIELDS = ("command", "args", "env", "credential", "secret", "token")


def _redact_tools(tools: dict[str, Any], is_admin: bool) -> dict[str, Any]:
    """MCP server 的 command/args/env 含 secret，普通用户拿不到。

    实际结构是 tools["mcp"]["servers"]（catalog 形态），不是顶层 mcp_servers。
    递归清理任何位置的 mcp server 节点。
    """
    if is_admin:
        return tools
    import copy
    redacted = copy.deepcopy(tools)
    # 主路径：tool_payload() → mcp.servers
    mcp_block = redacted.get("mcp") or {}
    for srv in (mcp_block.get("servers") or []):
        for field in _MCP_SECRET_FIELDS:
            srv.pop(field, None)
    # 兼容旧路径：万一上游改回 mcp_servers
    for srv in (redacted.get("mcp_servers") or []):
        for field in _MCP_SECRET_FIELDS:
            srv.pop(field, None)
    return redacted


# ── chat handler 辅助函数（避免 /api/chat 重复逻辑膨胀）───────────────────
def _persist_chat_turn(
    api_user: dict[str, Any] | None,
    state: GameState,
    message_for_model: str,
    response: str,
    *,
    persist_user_id: int | None,
    active_save_id: int | None,
    interrupted: bool = False,
) -> None:
    """一轮 chat 结束（正常 or 打断）的持久化集合。
    state.save + record_runtime_turn（创建新 commit）+ record_turn_messages（DB messages 表）。
    """
    state.record_turn(message_for_model, response)
    state.save()
    platform_branches.record_runtime_turn(
        message_for_model,
        response,
        str(SAVE_FILE),
        user_id=api_user["id"] if api_user else None,
        state_data=state.data,
    )
    if persist_user_id and active_save_id:
        try:
            platform_knowledge.record_turn_messages(
                persist_user_id,
                active_save_id,
                state.data,
                message_for_model,
                response,
                {"interrupted": True} if interrupted else None,
            )
        except Exception:
            pass


def _build_usage_payload(
    api_user: dict[str, Any] | None,
    gm: GameMaster,
    bundle: dict[str, Any],
    message_for_model: str,
    persist_user_id: int | None,
    active_save_id: int | None,
    context_run_id: int | None,
) -> dict[str, Any] | None:
    """从 backend.last_usage 抽 SSE usage 形状 + 写 token_usage 表。"""
    try:
        from platform_app import usage as usage_mod
        from platform_app.usage import context_window_for, estimate_input_tokens
        last_usage = getattr(gm._backend, "last_usage", {}) or {}
        ctx_max = context_window_for(gm.api_id, gm._backend.model_name)
        ctx_used = int(last_usage.get("input_tokens", 0)) or estimate_input_tokens(
            bundle["prompt"] + message_for_model
        )
        usage_row = {}
        if persist_user_id:
            usage_row = usage_mod.record_usage(
                user_id=persist_user_id,
                save_id=active_save_id,
                context_run_id=context_run_id,
                api_id=gm.api_id,
                model_real_name=gm._backend.model_name,
                usage=last_usage,
                context_used=ctx_used,
                context_max=ctx_max,
            )
        return {
            "model": gm._backend.model_name,
            "api_id": gm.api_id,
            "input_tokens": int(last_usage.get("input_tokens", 0)),
            "output_tokens": int(last_usage.get("output_tokens", 0)),
            "cached_input_tokens": int(last_usage.get("cached_input_tokens", 0)),
            "reasoning_tokens": int(last_usage.get("reasoning_tokens", 0)),
            "total_tokens": int(last_usage.get("total_tokens", 0)),
            "context_used": ctx_used,
            "context_max": ctx_max,
            "context_pct": round(100 * ctx_used / ctx_max, 1) if ctx_max else 0,
            "cost_usd": float(usage_row.get("cost_usd", 0)),
        }
    except Exception:
        return None


def _mark_context_run(context_run_id: int | None, status: str, error: str = "", duration_ms: int = 0) -> None:
    """安全 wrap context_runs 状态更新；失败静默。"""
    if not context_run_id:
        return
    try:
        platform_knowledge.update_context_run_status(
            int(context_run_id),
            status=status,
            error=error,
            duration_ms=duration_ms,
        )
    except Exception:
        pass


def _persist_runtime_checkpoint(state: GameState, user: dict[str, Any] | None) -> None:
    if not user:
        return
    try:
        result = platform_branches.persist_runtime_state(str(SAVE_FILE), user_id=user["id"], state_data=state.data)
        runtime_meta = (result or {}).get("runtime") or platform_runtime.read_runtime(user_id=user["id"])
        save_id = int((runtime_meta or {}).get("save_id") or 0)
        if save_id:
            platform_knowledge.ensure_game_session(user["id"], save_id, state.data)
    except Exception:
        return


def _build_turn_context(
    state: GameState,
    message: str,
    retrieved_context: str,
    script_id: int | None = None,
    book_id: int | None = None,
) -> dict[str, Any]:
    bundle = build_context_bundle(
        state, message, retrieved_context,
        script_id=script_id, book_id=book_id,
    )
    state.set_last_context(bundle["debug"])
    return bundle


def _active_script_id(api_user: dict[str, Any] | None) -> int | None:
    """从 runtime/save 派生当前 script_id，供 context_engine 走 DB 数据。"""
    if not api_user:
        return None
    try:
        from platform_app.runtime import read_runtime
        from platform_app.db import connect
        meta = read_runtime(user_id=api_user["id"])
        save_id = int((meta or {}).get("save_id") or 0)
        if not save_id:
            return None
        with connect() as db:
            row = db.execute(
                "select script_id from game_saves where id = %s",
                (save_id,),
            ).fetchone()
        return int(row["script_id"]) if row and row.get("script_id") else None
    except Exception:
        return None


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _split_inline_assignment(text: str) -> tuple[str, str]:
    for sep in ("=", "：", ":"):
        if sep in text:
            left, right = text.split(sep, 1)
            return left.strip(), right.strip()
    return "", text.strip()


MAX_ATTACHMENTS_PER_REQUEST = 8


def _save_attachments(raw_items: list[dict[str, Any]], user_id: int | None = None) -> list[dict[str, Any]]:
    saved: list[dict[str, Any]] = []
    if not raw_items:
        return saved
    # 超量明确拒绝，不再静默截断
    if len(raw_items) > MAX_ATTACHMENTS_PER_REQUEST:
        raise ValueError(f"单次最多上传 {MAX_ATTACHMENTS_PER_REQUEST} 个附件，本次提交 {len(raw_items)}")
    upload_dir = UPLOAD_DIR / f"user_{int(user_id)}" if user_id else UPLOAD_DIR / "local"
    upload_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    for index, item in enumerate(raw_items):
        name = Path(str(item.get("name") or f"attachment-{index + 1}")).name
        mime_type = str(item.get("type") or "application/octet-stream")
        data_url = str(item.get("data_url") or item.get("dataUrl") or "")
        encoded = str(item.get("base64") or "")
        if "," in data_url:
            encoded = data_url.split(",", 1)[1]
        if not encoded:
            raise ValueError(f"附件 {name} 内容为空")
        # 严格 base64：非法字符直接拒绝，避免落盘 0 字节脏文件
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"附件 {name} 不是合法 base64：{exc}")
        if not data:
            raise ValueError(f"附件 {name} 解码后为空")
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise ValueError(f"附件 {name} 超过 {MAX_ATTACHMENT_BYTES} 字节")
        safe_name = re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+", "_", name).strip("._") or f"attachment-{index + 1}"
        file_path = upload_dir / f"{stamp}_{index + 1}_{safe_name}"
        file_path.write_bytes(data)
        preview = _text_preview_for_attachment(file_path, mime_type, data)
        saved.append({
            "name": name,
            "type": mime_type,
            "size": len(data),
            "path": str(file_path),
            "is_image": mime_type.startswith("image/"),
            "text_preview": preview,
        })
    return saved


def _text_preview_for_attachment(file_path: Path, mime_type: str, data: bytes) -> str:
    if not (
        mime_type.startswith("text/")
        or file_path.suffix.lower() in {".txt", ".md", ".json", ".csv", ".log"}
    ):
        return ""
    try:
        return data[:6000].decode("utf-8", errors="replace")
    except Exception:
        return ""


def _message_with_attachments(message: str, attachments: list[dict[str, Any]]) -> str:
    if not attachments:
        return message
    lines = [message or "请参考本轮附件。", "", "【用户附件】"]
    for item in attachments:
        lines.append(
            f"- {item['name']} ({item['type'] or 'unknown'}, {item['size']} bytes) -> {item['path']}"
        )
        if item.get("is_image"):
            lines.append("  图片已上传；当前文本管线先记录附件，后续多模态模型接入后可作为视觉输入。")
        if item.get("text_preview"):
            lines.append("  文本预览：")
            lines.append(item["text_preview"])
    return "\n".join(lines)


def _command_response(message: str, state: GameState) -> tuple[str, bool]:
    cmd = message.strip()
    low = cmd.lower()
    changed = False

    if low == "/status":
        return f"```text\n{state.short_summary()}\n```", changed
    if low == "/save":
        state.save()
        return "已手动存档。", changed
    if low == "/debug":
        ctx = state.data["memory"].get("last_retrieval") or "（无）"
        return f"**上轮检索到的参考资料**\n\n```text\n{ctx}\n```", changed
    if low.startswith("/loc "):
        loc = cmd[5:].strip()
        state.update_location(loc)
        state.save()
        return f"位置已更新：{loc}", True
    if low.startswith("/time "):
        time_desc = cmd[6:].strip()
        state.update_time(time_desc)
        state.save()
        return f"时间线已更新：{time_desc}", True
    if low.startswith("/timeline "):
        time_desc = cmd[10:].strip()
        state.update_time(time_desc)
        state.save()
        return f"时间线已更新：{time_desc}", True
    if low.startswith("/rel "):
        parts = cmd[5:].strip().split(" ", 1)
        if len(parts) != 2:
            return "用法：`/rel 角色 关系状态`", changed
        state.update_relationship(parts[0], parts[1])
        state.save()
        return f"关系已更新：{parts[0]} -> {parts[1]}", True
    if low.startswith("/memory "):
        mode = low.split(" ", 1)[1].strip()
        state.set_memory_mode(mode)
        state.save()
        return f"记忆模式已切换为：{state.data['memory']['mode']}", True
    if low.startswith("/permission "):
        mode = cmd.split(" ", 1)[1].strip()
        state.set_permission_mode(mode)
        state.save()
        return f"LLM 写入权限已切换为：{state.data['permissions']['mode']}", True
    if low.startswith("/var "):
        path, value = _split_inline_assignment(cmd[5:].strip())
        if not path:
            return "用法：`/var 变量名=变量值`", changed
        state.set_user_variable(path, value, source="user")
        state.save()
        return f"用户变量已写入：{path}={value}", True
    if low.startswith("/pin "):
        state.add_memory("pinned", cmd[5:].strip())
        state.save()
        return "已加入固定记忆。", True
    if low.startswith("/note "):
        state.add_memory("notes", cmd[6:].strip())
        state.save()
        return "已加入玩家笔记。", True

    return "", changed


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(HTML)


@app.get("/api/state")
async def api_state(request: Request) -> JSONResponse:
    api_user = _require_api_user(request)
    return JSONResponse(_payload(api_user))


@app.post("/api/new")
async def api_new(request: Request) -> JSONResponse:
    """创建新存档。

    切换角色卡（user persona / 用户自创 NPC / 剧本预置角色）一律走这个接口，
    不会污染现有存档。优先级（高 → 低）：
      1. script_card_id + script_id  (扮演某剧本里的角色)
      2. user_card_id                 (用户自创 NPC 卡)
      3. persona_id                   (用户自己的 persona)
      4. body 里直接传 name/role/background
    """
    api_user = _require_api_user(request)
    body = await request.json()
    backup = _backup_save("before_new_game") if api_user is None else None

    source_meta: dict[str, Any] | None = None
    source_kind = ""

    # 优先级 1：剧本预置角色卡
    script_card_id = body.get("script_card_id")
    script_id = body.get("script_id")
    if script_card_id and script_id and api_user:
        from platform_app import knowledge as _know
        card = _know.get_character_card(api_user["id"], int(script_id), int(script_card_id))
        if card:
            source_meta = card
            source_kind = "script_card"

    # 优先级 2：用户自创 NPC 卡
    if source_meta is None:
        user_card_id = body.get("user_card_id")
        if user_card_id and api_user:
            from platform_app import user_cards as _ucards
            card = _ucards.get_user_card(api_user["id"], int(user_card_id))
            if card:
                source_meta = card
                source_kind = "user_card"

    # 优先级 3：persona
    if source_meta is None:
        persona_id = body.get("persona_id")
        if persona_id and api_user:
            from platform_app import user_cards as _ucards
            persona = _ucards.get_persona(api_user["id"], int(persona_id))
            if persona:
                source_meta = persona
                source_kind = "persona"

    if source_meta:
        # 字段映射：script_card / user_card 用 identity 作 role，persona 用 role 字段
        name = source_meta.get("name") or "无名者"
        if source_kind == "persona":
            role = source_meta.get("role") or "未指定"
            background = source_meta.get("background") or "（无背景）"
        else:
            role = source_meta.get("identity") or "未指定"
            background = source_meta.get("appearance") or source_meta.get("personality") or "（来自角色卡）"
    else:
        role_label = body.get("role") or list(ROLES.keys())[0]
        role = ROLES.get(role_label, role_label)
        name = (body.get("name") or "无名者").strip()
        background = (body.get("background") or "原因不明，只是来了。").strip()

    state = GameState.new()
    state.setup_player(name, role, background)
    if source_meta:
        state.data["player"]["source_kind"] = source_kind
        state.data["player"]["source_id"] = int(source_meta["id"])
        for field in ("appearance", "personality", "speech_style"):
            if source_meta.get(field):
                state.data["player"][field] = source_meta[field]
    state.save()
    # 清掉缓存，下次 _ensure_loaded 会用新 state
    _invalidate_user_cache(api_user)
    uid = _user_key(api_user)
    with _state_lock:
        _state_by_user[uid] = state
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "backup": backup, "state": _payload(api_user)})


@app.post("/api/opening")
async def api_opening(request: Request) -> StreamingResponse:
    api_user = _require_api_user(request)
    state = _ensure_loaded(api_user)
    gm = _get_gm(api_user)

    async def stream():
        query = "柏林 图卢兹 娅赛兰 蛇信 蕾穆丽娜"
        ctx = retrieve_context(query, state=state, user_id=api_user["id"] if api_user else None)
        state.set_last_retrieval(ctx)
        bundle = _build_turn_context(state, query, ctx, script_id=_active_script_id(api_user))
        yield _sse("status", _payload(api_user))
        text = ""
        try:
            opening = gm.generate_opening(state, retrieved_context=bundle["prompt"])
            text = opening
            yield _sse("token", {"text": opening})
            state.data["history"].append({"role": "assistant", "content": opening})
            state.save()
            _persist_runtime_checkpoint(state, api_user)
            yield _sse("done", {"status": _payload(api_user)})
        except Exception as exc:
            yield _sse("error", {"message": str(exc), "partial": text})

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/chat/estimate")
async def api_chat_estimate(request: Request) -> JSONResponse:
    """实时上下文预估。前端 debounce 用户输入后调用，显示 ctx X/Y (Z%) · in~A out~B。

    估算思路（轻量，避免真的跑 retrieval）：
      input_tokens ≈ system_prompt + history_window + retrieved_budget + 当前输入
      output_tokens ≈ 该用户最近 10 轮该模型的平均输出
    """
    api_user = _require_api_user(request)
    body = await request.json()
    message = (body.get("message") or "").strip()
    include_retrieval = bool(body.get("include_retrieval", True))

    state = _ensure_loaded(api_user)
    model = selected_model()
    api_id = model["api_id"]
    model_name = model["real_name"]

    # 各部分粗估
    from platform_app.usage import estimate_input_tokens, context_window_for, average_output_tokens
    history = state.history_messages()  # 已限制 MAX_HISTORY_TURNS
    history_text = "\n".join(m.get("content", "") for m in history)
    # system prompt 用 GM 模板的近似长度；不真正构建避免昂贵
    system_estimate = 1200  # 世界观+伯林局势+穿越者补丁 加起来约 1.2K tokens
    # 召回部分按预算（context_engine 配置的 ~800 token）
    retrieval_estimate = 800 if include_retrieval else 0
    # 玩家档案/记忆摘要
    profile_estimate = estimate_input_tokens(state.short_summary())

    input_tokens = (
        system_estimate
        + profile_estimate
        + estimate_input_tokens(history_text)
        + retrieval_estimate
        + estimate_input_tokens(message)
    )
    persist_user_id, _ = _resolve_persist_target(api_user)
    output_estimate = average_output_tokens(persist_user_id, model_name) if persist_user_id else 600
    if output_estimate <= 0:
        output_estimate = 600  # 没历史时的默认猜测

    ctx_max = context_window_for(api_id, model_name) or 0
    total_estimate = input_tokens + output_estimate
    ctx_pct = round(100 * input_tokens / ctx_max, 1) if ctx_max else 0
    will_overflow = (input_tokens + output_estimate > ctx_max) if ctx_max else False

    return JSONResponse({
        "ok": True,
        "api_id": api_id,
        "model": model_name,
        "context_used": input_tokens,
        "context_max": ctx_max,
        "context_pct": ctx_pct,
        "estimated_output_tokens": output_estimate,
        "estimated_total_tokens": total_estimate,
        "will_overflow": will_overflow,
        "breakdown": {
            "system_prompt": system_estimate,
            "profile_and_memory": profile_estimate,
            "history": estimate_input_tokens(history_text),
            "retrieval_budget": retrieval_estimate,
            "current_input": estimate_input_tokens(message),
        },
        "headroom_tokens": max(0, ctx_max - input_tokens - output_estimate) if ctx_max else 0,
    })


@app.post("/api/chat")
async def api_chat(request: Request) -> StreamingResponse:
    api_user = _require_api_user(request)
    body = await request.json()
    message = (body.get("message") or "").strip()
    attachments = _save_attachments(body.get("attachments") or [], user_id=api_user["id"] if api_user else None)
    message_for_model = _message_with_attachments(message, attachments)
    if not message_for_model.strip():
        return StreamingResponse(iter([_sse("error", {"message": "空消息"})]), media_type="text/event-stream")
    _chat_start_time = time.time()

    # 多用户隔离：当前用户的 run_id 自增、stop_event 清零
    run_id, stop_event = _get_run_state(api_user)

    state = _ensure_loaded(api_user)
    gm = _get_gm(api_user)

    async def stream():
        response = ""
        command_text, changed = ("", False) if attachments else _command_response(message, state)
        if command_text:
            if changed:
                _persist_runtime_checkpoint(state, api_user)
                yield _sse("status", _payload(api_user))
            yield _sse("token", {"text": command_text})
            yield _sse("done", {"status": _payload(api_user), "interrupted": False})
            return

        try:
            directive_updates = state.apply_player_directives(message_for_model)
            agent_result = None
            sub_gm = _get_sub_gm(api_user)
            for item in run_context_agent(
                state, message_for_model,
                stop_requested=stop_event.is_set,
                llm_curator=sub_gm.curate_context,
                user_id=api_user["id"] if api_user else None,
                script_id=_active_script_id(api_user),
            ):
                if item["type"] == "step":
                    yield _sse("agent", item["step"])
                    await asyncio.sleep(0)
                elif item["type"] == "stopped":
                    state.set_last_context_agent({"status": "stopped", "steps": item.get("steps", [])})
                    yield _sse("done", {"status": _payload(api_user), "interrupted": True})
                    return
                elif item["type"] == "result":
                    agent_result = item
            if agent_result is None:
                yield _sse("error", {"message": "上下文子代理未返回结果", "partial": response})
                return
            ctx = agent_result["retrieved_context"]
            bundle = agent_result["bundle"]
            state.set_last_retrieval(ctx)
            state.set_last_context(bundle["debug"])
            # B4: 子代理 usage 单独记账（metadata.kind='sub_agent'）
            try:
                sub_usage = getattr(sub_gm._backend, "last_usage", {}) or {}
                if sub_usage and api_user:
                    from platform_app.usage import record_usage as _rec
                    _rec(
                        user_id=api_user["id"],
                        save_id=None,
                        context_run_id=None,
                        api_id=sub_gm.api_id,
                        model_real_name=sub_gm._backend.model_name,
                        usage=sub_usage,
                        metadata={"kind": "sub_agent", "phase": "context_curator"},
                    )
            except Exception:
                pass
            state.set_last_context_agent({
                "status": "done",
                "steps": agent_result["steps"],
                "prompt": agent_result.get("agent_prompt", ""),
                "curator_plan": agent_result.get("curator_plan", {}),
                "cache_plan": bundle["debug"].get("cache_plan", {}),
            })
            persist_user_id, active_save_id = _resolve_persist_target(api_user)
            context_run_id = None
            if persist_user_id and active_save_id:
                try:
                    run_row = platform_knowledge.record_context_run(
                        persist_user_id,
                        active_save_id,
                        state.data,
                        message_for_model,
                        agent_result,
                        bundle,
                        ctx,
                        status="done",
                        duration_ms=int((time.time() - _chat_start_time) * 1000),
                    )
                    context_run_id = (run_row or {}).get("id")
                except Exception:
                    pass
            yield _sse("retrieval", {"text": ctx})
            yield _sse("context", {"debug": bundle["debug"]})
            yield _sse("status", _payload(api_user))

            yield _sse("agent", {
                "phase": "main_gm",
                "message": "主 GM 正在读取上下文并生成正文。",
                "status": "running",
                "elapsed_ms": 0,
            })
            # 收集当前已启动的 MCP 工具，注入 GM
            mcp_tools: list[dict[str, Any]] = []
            try:
                import mcp_broker
                mcp_tools = mcp_broker.discover_all_tools() or []
            except Exception:
                mcp_tools = []
            for event in gm.respond_stream_with_tools(
                message_for_model, bundle["prompt"], state,
                tools=mcp_tools, max_iterations=3,
            ):
                if stop_event.is_set() or run_id != _current_run_id(api_user) or _is_stop_requested_global(api_user, run_id):
                    if response.strip():
                        response += "\n\n【本轮已被玩家打断】"
                        _persist_chat_turn(
                            api_user, state, message_for_model, response,
                            persist_user_id=persist_user_id, active_save_id=active_save_id,
                            interrupted=True,
                        )
                    _mark_context_run(
                        context_run_id, "stopped",
                        duration_ms=int((time.time() - _chat_start_time) * 1000),
                    )
                    yield _sse("done", {"status": _payload(api_user), "interrupted": True})
                    return
                etype = event.get("type")
                if etype == "text":
                    chunk = event.get("text", "")
                    response += chunk
                    yield _sse("token", {"text": chunk})
                elif etype == "tool_call":
                    yield _sse("tool_call", {
                        "server_id": event.get("server_id", ""),
                        "tool": event.get("tool", ""),
                        "arguments": event.get("arguments", {}),
                    })
                elif etype == "tool_result":
                    yield _sse("tool_result", {
                        "ok": event.get("ok", False),
                        "result": event.get("result"),
                        "error": event.get("error"),
                    })
                elif etype == "tool_error":
                    yield _sse("tool_error", {
                        "error": event.get("error", ""),
                        "raw": event.get("raw", ""),
                    })
                await asyncio.sleep(0)

            updates = directive_updates + state.apply_structured_updates(response)
            _persist_chat_turn(
                api_user, state, message_for_model, response,
                persist_user_id=persist_user_id, active_save_id=active_save_id,
            )
            usage_payload = _build_usage_payload(
                api_user, gm, bundle, message_for_model,
                persist_user_id, active_save_id, context_run_id,
            )
            if usage_payload:
                yield _sse("usage", usage_payload)
            yield _sse("updates", {"items": updates})
            yield _sse("done", {"status": _payload(api_user), "interrupted": False, "usage": usage_payload})
        except Exception as exc:
            _mark_context_run(
                locals().get("context_run_id"),
                "failed",
                error=str(exc),
                duration_ms=int((time.time() - _chat_start_time) * 1000),
            )
            yield _sse("error", {"message": str(exc), "partial": response})

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/stop")
async def api_stop(request: Request) -> JSONResponse:
    """打断当前用户正在跑的 chat。其他用户的 chat 不受影响。"""
    api_user = _require_api_user(request)
    _stop_user(api_user)
    return JSONResponse({"ok": True})


@app.post("/api/save")
async def api_save(request: Request) -> JSONResponse:
    api_user = _require_api_user(request)
    state = _ensure_loaded(api_user)
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "state": _payload(api_user)})


@app.post("/api/memory/mode")
async def api_memory_mode(request: Request) -> JSONResponse:
    api_user = _require_api_user(request)
    body = await request.json()
    state = _ensure_loaded(api_user)
    state.set_memory_mode(body.get("mode", "normal"))
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "state": _payload(api_user)})


@app.post("/api/memory/add")
async def api_memory_add(request: Request) -> JSONResponse:
    api_user = _require_api_user(request)
    body = await request.json()
    state = _ensure_loaded(api_user)
    state.add_memory(body.get("bucket", "notes"), body.get("text", ""))
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "state": _payload(api_user)})


@app.post("/api/memory/remove")
async def api_memory_remove(request: Request) -> JSONResponse:
    api_user = _require_api_user(request)
    body = await request.json()
    state = _ensure_loaded(api_user)
    state.remove_memory(body.get("bucket", "notes"), int(body.get("index", -1)))
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "state": _payload(api_user)})


@app.post("/api/permissions")
async def api_permissions(request: Request) -> JSONResponse:
    api_user = _require_api_user(request)
    body = await request.json()
    state = _ensure_loaded(api_user)
    state.set_permission_mode(body.get("mode", "full_access"))
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "state": _payload(api_user)})


@app.post("/api/permissions/pending-write")
async def api_pending_write(request: Request) -> JSONResponse:
    api_user = _require_api_user(request)
    body = await request.json()
    state = _ensure_loaded(api_user)
    index = int(body.get("index", -1))
    decision = str(body.get("decision", "")).lower()
    if decision == "approve":
        result = state.approve_pending_write(index)
    elif decision == "reject":
        result = state.reject_pending_write(index)
    else:
        return JSONResponse({"ok": False, "error": "unknown decision"}, status_code=400)
    state.data["memory"]["last_structured_updates"] = [result] + state.data["memory"].get("last_structured_updates", [])[:11]
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "result": result, "state": _payload(api_user)})


@app.post("/api/questions/clear")
async def api_question_clear(request: Request) -> JSONResponse:
    api_user = _require_api_user(request)
    body = await request.json()
    state = _ensure_loaded(api_user)
    state.clear_pending_question(int(body.get("index", -1)))
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "state": _payload(api_user)})


@app.post("/api/debug/pending-question")
async def api_debug_pending_question(request: Request) -> JSONResponse:
    api_user = _require_api_user(request, admin=True)
    if not os.getenv("RPG_DEBUG_UI"):
        return JSONResponse({"ok": False, "error": "debug disabled"}, status_code=404)
    body = await request.json()
    state = _ensure_loaded(api_user)
    state.add_pending_question(body.get("text", "下一步怎么做？｜选项：继续调查、返回基地、询问同伴"), source="debug")
    state.save()
    return JSONResponse({"ok": True, "state": _payload(api_user)})


@app.get("/api/models")
async def api_models(request: Request) -> JSONResponse:
    api_user = _require_api_user(request)
    catalog = load_model_catalog()
    is_admin = bool(api_user and api_user.get("role") == "admin")
    return JSONResponse({
        "ok": True,
        "models": _redact_catalog(catalog, is_admin),
        "selected": selected_model(catalog),
    })


@app.post("/api/models/select")
async def api_models_select(request: Request) -> JSONResponse:
    api_user = _require_api_user(request, admin=True)
    body = await request.json()
    catalog = select_model(body.get("api_id", ""), body.get("model_id", ""))
    # 切换模型后清掉所有用户的 GM 缓存，下次会用新模型重建
    with _state_lock:
        _gm_by_user.clear()
    return JSONResponse({"ok": True, "models": catalog, "selected": selected_model(catalog), "state": _payload(api_user)})


@app.post("/api/models/api")
async def api_models_upsert_api(request: Request) -> JSONResponse:
    _require_api_user(request, admin=True)
    catalog = upsert_api(await request.json())
    return JSONResponse({"ok": True, "models": catalog, "selected": selected_model(catalog)})


@app.post("/api/models/model")
async def api_models_upsert_model(request: Request) -> JSONResponse:
    _require_api_user(request, admin=True)
    body = await request.json()
    catalog = upsert_model(body.get("api_id", ""), body.get("model", {}))
    return JSONResponse({"ok": True, "models": catalog, "selected": selected_model(catalog)})


@app.get("/api/tools")
async def api_tools(request: Request) -> JSONResponse:
    api_user = _require_api_user(request)
    is_admin = bool(api_user and api_user.get("role") == "admin")
    return JSONResponse({"ok": True, "tools": _redact_tools(tool_payload(), is_admin)})


@app.post("/api/mcp/server")
async def api_mcp_server(request: Request) -> JSONResponse:
    _require_api_user(request, admin=True)
    try:
        catalog = upsert_mcp_server(await request.json())
        return JSONResponse({"ok": True, "mcp": catalog, "tools": tool_payload()})
    except (PermissionError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@app.post("/api/mcp/server/enabled")
async def api_mcp_server_enabled(request: Request) -> JSONResponse:
    _require_api_user(request, admin=True)
    body = await request.json()
    try:
        catalog = set_mcp_server_enabled(body.get("id", ""), bool(body.get("enabled", True)))
        return JSONResponse({"ok": True, "mcp": catalog, "tools": tool_payload()})
    except (PermissionError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@app.post("/api/mcp/server/delete")
async def api_mcp_server_delete(request: Request) -> JSONResponse:
    _require_api_user(request, admin=True)
    body = await request.json()
    try:
        catalog = delete_mcp_server(body.get("id", ""))
        return JSONResponse({"ok": True, "mcp": catalog, "tools": tool_payload()})
    except PermissionError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@app.post("/api/mcp/server/validate")
async def api_mcp_server_validate(request: Request) -> JSONResponse:
    _require_api_user(request, admin=True)
    body = await request.json()
    try:
        return JSONResponse({"ok": True, "result": validate_mcp_server(body.get("id", ""))})
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


# ── API 探测：模型列表 / 可用性 / 定价 / 综合报告 ──────────────────
def _check_probe_permission(api_user: dict[str, Any] | None, api_id: str) -> JSONResponse | None:
    """同 /api/models/probe 的权限策略：admin 或用户已配置该 provider key。
    返回 None 表示允许，否则返回 403。
    """
    if not api_user or api_user.get("role") == "admin":
        return None
    from platform_app import user_credentials as _ucreds
    cred = _ucreds.get_credential(api_user["id"], api_id)
    if cred:
        return None
    return JSONResponse(
        {"ok": False, "error": "需要先在「个人主页 → API 凭证」中配置该 provider 才能调用探测接口"},
        status_code=403,
    )


@app.get("/api/models/remote")
async def api_models_remote(request: Request) -> JSONResponse:
    """从供应商 SDK 拉取真实可用模型清单（带 60s 缓存）"""
    api_user = _require_api_user(request)
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


@app.get("/api/models/diff")
async def api_models_diff(request: Request) -> JSONResponse:
    """对比本地 catalog 和远端真实模型，返回 missing/extra/matching"""
    api_user = _require_api_user(request)
    api_id = request.query_params.get("api_id", "")
    blocked = _check_probe_permission(api_user, api_id)
    if blocked:
        return blocked
    import model_probe
    return JSONResponse(model_probe.diff_catalog(api_id, user_id=api_user["id"] if api_user else None))


@app.post("/api/models/probe")
async def api_models_probe(request: Request) -> JSONResponse:
    """发一条最小请求验证可用性 + 测延迟。

    安全：避免用别人的 key 测试。要么 user 自己配置过该 api_id 的凭证，
    要么必须是 admin。其他普通用户不允许触发付费 API 调用。
    """
    api_user = _require_api_user(request)
    body = await request.json()
    api_id = body.get("api_id", "")
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
        body.get("model"),
        timeout_sec=int(body.get("timeout", 15)),
        user_id=api_user["id"] if api_user else None,
    ))


@app.get("/api/models/pricing")
async def api_models_pricing(request: Request) -> JSONResponse:
    """查询单个模型的定价（USD per million tokens）"""
    _require_api_user(request)
    import model_probe
    from model_registry import load_model_catalog, find_api, find_model
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


@app.get("/api/models/report")
async def api_models_report(request: Request) -> JSONResponse:
    """API 综合健康报告：catalog + 远端 diff + 定价 + 可选 probe"""
    api_user = _require_api_user(request)
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


@app.get("/api/models/capabilities")
async def api_models_capabilities(request: Request) -> JSONResponse:
    """查询单个模型的能力清单（text/vision/tools/json_mode 等）"""
    _require_api_user(request)
    import model_probe
    from model_registry import load_model_catalog, find_api, find_model
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


@app.get("/api/models/capabilities/labels")
async def api_models_capability_labels(request: Request) -> JSONResponse:
    """返回所有已知能力的标签词典（前端筛选器/徽标用）"""
    _require_api_user(request)
    import model_probe
    return JSONResponse({"ok": True, "labels": model_probe.CAPABILITY_LABELS})


# ── MCP runtime broker ──────────────────────────────────────────────
@app.post("/api/mcp/server/start")
async def api_mcp_server_start(request: Request) -> JSONResponse:
    _require_api_user(request, admin=True)
    body = await request.json()
    import mcp_broker
    return JSONResponse(mcp_broker.start_server(body.get("id", "")))


@app.post("/api/mcp/server/stop")
async def api_mcp_server_stop(request: Request) -> JSONResponse:
    _require_api_user(request, admin=True)
    body = await request.json()
    import mcp_broker
    return JSONResponse(mcp_broker.stop_server(body.get("id", "")))


@app.get("/api/mcp/runtime")
async def api_mcp_runtime(request: Request) -> JSONResponse:
    """MCP 运行时状态。普通用户拿不到 stderr（可能含 token/路径）。"""
    api_user = _require_api_user(request)
    is_admin = bool(api_user and api_user.get("role") == "admin")
    import mcp_broker
    payload = mcp_broker.status()
    if not is_admin:
        # 脱 last_stderr 字段
        for entry in payload.get("running") or []:
            entry.pop("last_stderr", None)
    return JSONResponse(payload)


@app.post("/api/mcp/tool/call")
async def api_mcp_tool_call(request: Request) -> JSONResponse:
    """前端或主 GM 调用 MCP 工具的统一入口。

    安全：MCP server 配置目前是全局共享，调用任意工具等于以服务进程身份执行。
    在多用户/服务器模式下只允许 admin；本地匿名模式才允许任意调用。
    后续要让 MCP server 支持 per-user 注册再放宽。
    """
    api_user = _require_api_user(request)
    if _api_auth_required() and (not api_user or api_user.get("role") != "admin"):
        return JSONResponse({"ok": False, "error": "MCP 工具调用目前仅限管理员（per-user 注册待支持）"}, status_code=403)
    body = await request.json()
    import mcp_broker
    return JSONResponse(mcp_broker.call_tool(
        body.get("server_id", ""),
        body.get("tool", ""),
        body.get("arguments", {}) or {},
        timeout=int(body.get("timeout", 30)),
    ))


@app.get("/api/mcp/tools")
async def api_mcp_tools(request: Request) -> JSONResponse:
    """列出所有已启动 server 的工具清单（前端加号菜单/Skill 选择面板用）。"""
    _require_api_user(request)
    import mcp_broker
    return JSONResponse({"ok": True, "tools": mcp_broker.discover_all_tools()})


@app.post("/api/skills/import")
async def api_skills_import(request: Request) -> JSONResponse:
    _require_api_user(request, admin=True)
    body = await request.json()
    try:
        skill = import_skill_bundle(body.get("file", {}))
        return JSONResponse({"ok": True, "skill": skill, "tools": tool_payload()})
    except (PermissionError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@app.post("/api/skills/{skill_id}/run")
async def api_skill_run(request: Request, skill_id: str) -> JSONResponse:
    """在沙箱里跑某个 imported skill。

    Body: {"cmd": ["bash", "script.sh", "arg1"], "stdin": "...", "timeout_sec": 30}

    安全：admin only；本地匿名也允许（开发场景）。
    """
    api_user = _require_api_user(request)
    if _api_auth_required() and (not api_user or api_user.get("role") != "admin"):
        return JSONResponse({"ok": False, "error": "需要管理员权限"}, status_code=403)

    body = await request.json()
    cmd = body.get("cmd") or body.get("command")
    if not isinstance(cmd, list) or not cmd:
        return JSONResponse({"ok": False, "error": "cmd 必须是非空 list"}, status_code=400)

    # 找 skill_id 对应的目录
    from tool_registry import list_imported_skills
    skill = next((s for s in list_imported_skills() if s.get("id") == skill_id), None)
    if not skill:
        return JSONResponse({"ok": False, "error": f"skill 不存在: {skill_id}"}, status_code=404)
    skill_path = skill.get("path") or ""
    if not skill_path:
        return JSONResponse({"ok": False, "error": "skill 路径丢失"}, status_code=500)

    # 找 skill 根目录（SKILL.md 的父目录）
    from pathlib import Path as _Path
    skill_root = _Path(skill_path).parent

    import skill_executor
    result = skill_executor.run_skill_command(
        cmd=cmd,
        skill_root=skill_root,
        timeout_sec=int(body.get("timeout_sec") or skill_executor.DEFAULT_TIMEOUT_SEC),
        stdin_text=body.get("stdin"),
    )
    return JSONResponse({"ok": True, **result})


@app.post("/api/worldline/variable")
async def api_worldline_variable(request: Request) -> JSONResponse:
    api_user = _require_api_user(request)
    body = await request.json()
    key = body.get("key", "")
    value = body.get("value", "")
    state = _ensure_loaded(api_user)
    state.set_user_variable(key, value, source="user")
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    # 同步写入 DB（保证前端管理面板可见）
    persist_user_id, active_save_id = _resolve_persist_target(api_user)
    if persist_user_id and active_save_id:
        try:
            platform_knowledge.set_worldline_variable(persist_user_id, active_save_id, key, value, source="user")
        except Exception:
            pass
    return JSONResponse({"ok": True, "state": _payload(api_user)})


@app.post("/api/worldline/variable/remove")
async def api_worldline_variable_remove(request: Request) -> JSONResponse:
    api_user = _require_api_user(request)
    body = await request.json()
    key = body.get("key", "")
    state = _ensure_loaded(api_user)
    state.remove_user_variable(key)
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    persist_user_id, active_save_id = _resolve_persist_target(api_user)
    if persist_user_id and active_save_id:
        try:
            platform_knowledge.remove_worldline_variable(persist_user_id, active_save_id, key)
        except Exception:
            pass
    return JSONResponse({"ok": True, "state": _payload(api_user)})


HTML = r"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>我蕾穆丽娜不爱你 RPG</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #11100f;
      --panel: #191817;
      --panel-2: #22211f;
      --panel-3: #2c2a27;
      --line: #3d3b37;
      --line-soft: #2f2d2a;
      --text: #f1eee8;
      --muted: #a8a19a;
      --faint: #756f68;
      --accent: #d97745;
      --accent-strong: #f28b52;
      --danger: #ff6f61;
      --green: #72c58f;
      --shadow: 0 14px 40px rgba(0, 0, 0, 0.28);
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 15px/1.55 ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      letter-spacing: 0;
      overflow: hidden;
    }
    button, input, textarea, select {
      font: inherit;
      color: inherit;
    }
    button {
      border: 1px solid var(--line);
      background: var(--panel-2);
      border-radius: 7px;
      min-height: 36px;
      padding: 0 12px;
      cursor: pointer;
    }
    button:hover { background: var(--panel-3); }
    button.primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #fffaf5;
      font-weight: 650;
    }
    button.primary:hover { background: var(--accent-strong); }
    button.ghost { background: transparent; }
    button.icon {
      width: 36px;
      padding: 0;
      display: inline-grid;
      place-items: center;
    }
    button:disabled { opacity: 0.55; cursor: default; }
    .app {
      display: grid;
      grid-template-columns: 280px minmax(0, 1fr) 360px;
      height: 100vh;
      min-height: 0;
      min-width: 1120px;
      overflow: hidden;
    }
    .sidebar, .inspector {
      background: var(--panel);
      border-color: var(--line-soft);
      display: flex;
      flex-direction: column;
      min-width: 0;
      min-height: 0;
      overflow: hidden;
    }
    .sidebar { border-right: 1px solid var(--line-soft); }
    .inspector { border-left: 1px solid var(--line-soft); }
    .brand, .inspect-head {
      height: 64px;
      border-bottom: 1px solid var(--line-soft);
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 0 16px;
    }
    .inspect-head {
      height: auto;
      min-height: 64px;
      flex-wrap: wrap;
      padding-top: 10px;
      padding-bottom: 10px;
    }
    .brand-mark {
      width: 34px;
      height: 34px;
      border-radius: 8px;
      display: grid;
      place-items: center;
      background: #2b211c;
      border: 1px solid #4a352a;
      color: var(--accent-strong);
      font-weight: 800;
    }
    .brand-title { font-weight: 750; line-height: 1.2; }
    .brand-sub { font-size: 12px; color: var(--muted); }
    .side-body, .inspect-body {
      overflow: auto;
      min-height: 0;
      padding: 14px;
    }
    .side-section { margin-bottom: 18px; }
    .section-title {
      font-size: 12px;
      color: var(--faint);
      text-transform: uppercase;
      letter-spacing: 0;
      margin: 0 0 8px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .session-card {
      border: 1px solid var(--line);
      background: var(--panel-2);
      border-radius: 8px;
      padding: 12px;
      box-shadow: var(--shadow);
    }
    .session-top {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
    }
    .session-name { font-weight: 700; }
    .pill {
      border: 1px solid var(--line);
      color: var(--muted);
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 12px;
      white-space: nowrap;
    }
    .meter {
      height: 8px;
      background: #131211;
      border: 1px solid var(--line-soft);
      border-radius: 999px;
      overflow: hidden;
      margin: 12px 0 8px;
    }
    .meter > span {
      display: block;
      height: 100%;
      width: 0%;
      background: linear-gradient(90deg, var(--accent), #ebbd73);
      transition: width 200ms ease;
    }
    .muted { color: var(--muted); }
    .tiny { font-size: 12px; color: var(--muted); }
    .stack { display: grid; gap: 8px; }
    .memory-toggle {
      display: grid;
      gap: 4px;
      background: #11100f;
      border: 1px solid var(--line-soft);
      border-radius: 8px;
      padding: 4px;
    }
    .memory-toggle { grid-template-columns: repeat(3, 1fr); }
    .memory-toggle button {
      min-height: 30px;
      border: 0;
      background: transparent;
      font-size: 12px;
      padding: 0;
    }
    .memory-toggle button.active { background: var(--panel-3); }
    .main {
      min-width: 0;
      min-height: 0;
      display: grid;
      grid-template-rows: 64px minmax(0, 1fr) auto auto;
      overflow: hidden;
      background:
        linear-gradient(180deg, rgba(255,255,255,0.025), transparent 220px),
        var(--bg);
    }
    .topbar {
      border-bottom: 1px solid var(--line-soft);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      padding: 0 20px;
      background: rgba(17,16,15,0.86);
      backdrop-filter: blur(16px);
    }
    .thread-title { font-weight: 780; font-size: 18px; }
    .top-actions { display: flex; gap: 8px; align-items: center; }
    .chat {
      overflow: auto;
      min-height: 0;
      padding: 28px 7vw 26px;
      scroll-behavior: smooth;
      overscroll-behavior: contain;
    }
    .empty {
      max-width: 780px;
      margin: 10vh auto 0;
      text-align: center;
      color: var(--muted);
    }
    .empty h1 {
      margin: 0 0 10px;
      color: var(--text);
      font-size: 32px;
      line-height: 1.16;
    }
    .message {
      max-width: 900px;
      margin: 0 auto 24px;
      display: grid;
      grid-template-columns: 40px minmax(0, 1fr);
      gap: 12px;
    }
    .avatar {
      width: 34px;
      height: 34px;
      border-radius: 8px;
      display: grid;
      place-items: center;
      background: var(--panel-2);
      border: 1px solid var(--line);
      color: var(--muted);
      font-size: 13px;
      font-weight: 800;
    }
    .message.user .avatar {
      background: #2b211c;
      color: #ffd6be;
      border-color: #5b3a2b;
    }
    .bubble {
      min-width: 0;
      color: var(--text);
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .bubble.user-text {
      background: var(--panel-2);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px 14px;
    }
    .bubble.assistant-text {
      padding-top: 4px;
      font-size: 16px;
      line-height: 1.76;
    }
    .cursor {
      display: inline-block;
      width: 8px;
      height: 1.1em;
      margin-left: 2px;
      vertical-align: -2px;
      background: var(--accent-strong);
      animation: blink 1s step-end infinite;
    }
    @keyframes blink { 50% { opacity: 0; } }
    .guide {
      max-width: 900px;
      margin: -6px auto 10px;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      max-height: 38px;
      min-height: 0;
      overflow: auto;
      padding: 0 0 2px;
    }
    .guide button {
      min-height: 28px;
      border-radius: 999px;
      font-size: 12px;
      color: var(--muted);
    }
    .guide:empty { display: none; }
    .composer {
      position: relative;
      border-top: 1px solid var(--line-soft);
      padding: 9px 7vw 11px;
      background: rgba(17,16,15,0.92);
      backdrop-filter: blur(18px);
    }
    .composer-shell {
      max-width: 820px;
      margin: 0 auto;
      position: relative;
    }
    .run-feed, .ask-popover {
      position: absolute;
      left: 0;
      right: 0;
      bottom: calc(100% + 10px);
      z-index: 12;
      pointer-events: none;
    }
    .run-feed {
      display: grid;
      gap: 6px;
    }
    .run-event {
      width: fit-content;
      max-width: min(100%, 660px);
      display: inline-flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      background: rgba(17, 16, 15, 0.92);
      border: 1px solid var(--line-soft);
      border-radius: 10px;
      padding: 7px 10px;
      box-shadow: var(--shadow);
      pointer-events: auto;
      font-size: 13px;
    }
    .run-event.done { color: var(--faint); }
    .run-event.pending { color: var(--accent-strong); border-color: rgba(232, 124, 68, 0.45); }
    .run-icon {
      width: 19px;
      height: 19px;
      border: 1px solid currentColor;
      border-radius: 5px;
      display: inline-grid;
      place-items: center;
      font-size: 11px;
      font-weight: 800;
      flex: 0 0 auto;
    }
    .ask-popover {
      pointer-events: auto;
      display: grid;
      gap: 8px;
      max-height: 44vh;
      overflow: auto;
    }
    .ask-card {
      border: 1px solid var(--line);
      background: rgba(31, 30, 28, 0.98);
      border-radius: 12px;
      box-shadow: var(--shadow);
      padding: 12px;
      color: var(--text);
    }
    .ask-card h3 {
      margin: 0 0 5px;
      font-size: 13px;
    }
    .ask-card p {
      margin: 0;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }
    .ask-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 10px;
    }
    .ask-actions button {
      min-height: 30px;
      font-size: 12px;
      padding: 0 10px;
    }
    .composer-box {
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 16px;
      box-shadow: var(--shadow);
      padding: 8px 10px 7px;
      min-height: 76px;
    }
    textarea {
      width: 100%;
      resize: none;
      min-height: 30px;
      max-height: 116px;
      border: 0;
      outline: 0;
      background: transparent;
      color: var(--text);
      display: block;
      padding: 0 3px 6px;
      font-size: 15px;
      line-height: 1.4;
    }
    .composer-actions {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
    }
    .composer-left, .composer-right {
      display: flex;
      gap: 8px;
      align-items: center;
      min-width: 0;
    }
    .composer-right { margin-left: auto; }
    .icon-btn, .round-btn {
      display: inline-grid;
      place-items: center;
      padding: 0;
      border: 0;
      background: transparent;
      color: var(--muted);
    }
    .icon-btn {
      width: 30px;
      height: 30px;
      font-size: 21px;
    }
    .round-btn {
      width: 34px;
      height: 34px;
      border-radius: 999px;
      font-size: 17px;
      font-weight: 750;
    }
    .round-btn.primary {
      background: #f1eee8;
      color: #181716;
      border-color: #f1eee8;
    }
    .round-btn.stop {
      display: none;
      background: #f1eee8;
      color: #181716;
      border-color: #f1eee8;
      font-size: 15px;
    }
    .round-btn.stop.active { display: inline-grid; }
    .round-btn.primary.hidden { display: none; }
    .permission-chip, .model-chip {
      min-height: 30px;
      border: 0;
      background: transparent;
      color: var(--muted);
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 0 6px;
      font-size: 13px;
      white-space: nowrap;
    }
    .permission-chip { color: var(--accent-strong); font-weight: 700; }
    .permission-icon {
      width: 20px;
      height: 20px;
      border: 2px solid currentColor;
      border-radius: 999px;
      display: inline-grid;
      place-items: center;
      font-size: 11px;
      line-height: 1;
    }
    .chevron { color: inherit; font-size: 14px; }
    .slash-menu, .add-menu, .tool-menu, .permission-menu, .model-menu {
      position: absolute;
      z-index: 15;
      background: rgba(42, 41, 39, 0.98);
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
      backdrop-filter: blur(18px);
      overflow: auto;
    }
    .slash-menu {
      left: 0;
      right: 0;
      bottom: calc(100% + 10px);
      max-height: 312px;
      border-radius: 14px;
      padding: 6px;
    }
    .add-menu {
      left: 0;
      bottom: 42px;
      width: 270px;
      border-radius: 14px;
      padding: 6px;
    }
    .tool-menu {
      left: 278px;
      bottom: 42px;
      width: 270px;
      max-height: 360px;
      border-radius: 14px;
      padding: 8px;
    }
    .permission-menu {
      left: 38px;
      bottom: 42px;
      width: 250px;
      border-radius: 14px;
      padding: 6px;
    }
    .model-menu {
      right: 48px;
      bottom: 42px;
      width: 310px;
      max-height: 340px;
      border-radius: 14px;
      padding: 8px;
    }
    .slash-item, .add-item, .tool-item, .permission-item, .model-item {
      width: 100%;
      min-height: 38px;
      border: 0;
      border-radius: 9px;
      background: transparent;
      display: grid;
      grid-template-columns: 28px minmax(0, 1fr);
      gap: 8px;
      align-items: center;
      text-align: left;
      color: var(--text);
      padding: 6px 8px;
    }
    .permission-item { grid-template-columns: 28px minmax(0, 1fr) 22px; }
    .model-item { grid-template-columns: minmax(0, 1fr) 22px; }
    .add-item {
      grid-template-columns: 38px minmax(0, 1fr) 18px;
      min-height: 54px;
      gap: 10px;
    }
    .tool-item {
      grid-template-columns: 38px minmax(0, 1fr);
      min-height: 52px;
      gap: 10px;
    }
    .slash-item.active, .permission-item.active, .model-item.active,
    .slash-item:hover, .add-item:hover, .tool-item:hover, .permission-item:hover, .model-item:hover {
      background: rgba(255, 255, 255, 0.08);
    }
    .add-item:disabled, .tool-item:disabled {
      opacity: 0.45;
      cursor: default;
    }
    .add-icon, .tool-icon {
      width: 34px;
      height: 34px;
      border-radius: 10px;
      display: inline-grid;
      place-items: center;
      background: rgba(255, 255, 255, 0.07);
      color: var(--text);
      font-size: 14px;
      font-weight: 750;
    }
    .add-icon svg, .tool-icon svg {
      width: 19px;
      height: 19px;
      stroke: currentColor;
      fill: none;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
    }
    .slash-symbol {
      width: 24px;
      height: 24px;
      border: 1px solid var(--line);
      border-radius: 7px;
      display: inline-grid;
      place-items: center;
      color: var(--muted);
      font-weight: 700;
    }
    .slash-main { font-weight: 700; }
    .slash-desc { color: var(--muted); font-size: 12px; line-height: 1.3; }
    .model-api {
      color: var(--faint);
      font-size: 11px;
      margin: 8px 8px 4px;
      letter-spacing: 0;
    }
    .attachment-strip {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      padding: 0 2px 7px;
    }
    .attachment-chip {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      max-width: 230px;
      border: 1px solid var(--line);
      background: var(--panel-2);
      border-radius: 8px;
      padding: 4px 7px;
      color: var(--muted);
      font-size: 12px;
    }
    .attachment-chip span {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .attachment-chip button {
      min-height: 20px;
      width: 20px;
      padding: 0;
      border: 0;
      background: transparent;
      color: var(--danger);
    }
    [hidden] { display: none !important; }
    .tabs {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
      padding: 4px;
      margin-left: auto;
      background: #11100f;
      border: 1px solid var(--line-soft);
      border-radius: 8px;
    }
    .tabs button {
      min-height: 30px;
      border: 0;
      background: transparent;
      font-size: 12px;
      padding: 0 8px;
    }
    .tabs button.active { background: var(--panel-3); }
    .panel { display: none; }
    .panel.active { display: block; }
    .kv {
      border: 1px solid var(--line);
      background: #171615;
      border-radius: 8px;
      padding: 12px;
      margin-bottom: 10px;
    }
    .kv h3 {
      margin: 0 0 8px;
      font-size: 13px;
      color: var(--text);
    }
    .kv p, .kv ul { margin: 0; color: var(--muted); }
    .kv ul { padding-left: 18px; }
    .chips {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 8px;
    }
    .chip {
      border: 1px solid var(--line);
      background: var(--panel-2);
      color: var(--muted);
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 12px;
    }
    .context-layer {
      border: 1px solid var(--line);
      background: #171615;
      border-radius: 8px;
      padding: 10px;
      margin-bottom: 8px;
      color: var(--muted);
    }
    .context-layer strong { color: var(--text); }
    .context-layer .meta { font-size: 12px; color: var(--faint); margin-top: 2px; }
    .list { display: grid; gap: 8px; }
    .mem-item {
      border: 1px solid var(--line);
      background: #171615;
      border-radius: 8px;
      padding: 10px;
      color: var(--muted);
      display: grid;
      grid-template-columns: minmax(0, 1fr) 28px;
      gap: 8px;
      align-items: start;
    }
    .mem-item strong { color: var(--text); }
    .mem-item button {
      min-height: 28px;
      width: 28px;
      padding: 0;
      color: var(--danger);
    }
    .memory-add {
      display: grid;
      gap: 8px;
      margin-bottom: 14px;
    }
    .memory-add select, .memory-add input {
      min-height: 36px;
      border: 1px solid var(--line);
      background: var(--panel-2);
      border-radius: 7px;
      padding: 0 10px;
      outline: 0;
    }
    .setup-modal {
      position: fixed;
      inset: 0;
      display: none;
      place-items: center;
      background: rgba(0, 0, 0, 0.54);
      z-index: 20;
    }
    .setup-modal.show { display: grid; }
    .dialog {
      width: min(620px, calc(100vw - 40px));
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 20px;
      box-shadow: var(--shadow);
    }
    .dialog h2 { margin: 0 0 14px; }
    .form-grid { display: grid; gap: 12px; }
    .form-grid label { display: grid; gap: 6px; color: var(--muted); }
    .form-grid input, .form-grid textarea, .form-grid select {
      border: 1px solid var(--line);
      background: var(--panel-2);
      border-radius: 7px;
      padding: 10px;
      outline: 0;
    }
    .dialog-actions {
      display: flex;
      justify-content: flex-end;
      gap: 8px;
      margin-top: 16px;
    }
    .toast {
      position: fixed;
      right: 18px;
      bottom: 18px;
      max-width: 420px;
      background: #22211f;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px 14px;
      color: var(--text);
      box-shadow: var(--shadow);
      display: none;
      z-index: 30;
    }
    .toast.show { display: block; }
    @media (max-width: 1240px) {
      .app { grid-template-columns: 240px minmax(0, 1fr) 320px; min-width: 980px; }
      .chat, .composer { padding-left: 32px; padding-right: 32px; }
    }
  </style>
</head>
<body>
  <div class="app">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">R</div>
        <div>
          <div class="brand-title">柏林 RPG</div>
          <div class="brand-sub" id="modelLabel">Gemini 3.5 Flash</div>
        </div>
      </div>
      <div class="side-body">
        <section class="side-section">
          <div class="section-title">Session</div>
          <div class="session-card">
            <div class="session-top">
              <div>
                <div class="session-name" id="playerName">未读档</div>
                <div class="tiny" id="playerRole">尚未开始</div>
              </div>
              <span class="pill" id="turnPill">0 回合</span>
            </div>
            <div class="meter"><span id="turnMeter"></span></div>
            <div class="tiny" id="objectiveMini">等待载入存档</div>
          </div>
        </section>
        <section class="side-section">
          <div class="section-title">Actions</div>
          <div class="stack">
            <button class="primary" id="continueBtn">继续存档</button>
            <button id="newGameBtn">新游戏</button>
            <button id="saveBtn">手动存档</button>
          </div>
        </section>
        <section class="side-section">
          <div class="section-title">Memory Mode</div>
          <div class="memory-toggle">
            <button data-mode="concise">精简</button>
            <button data-mode="normal">标准</button>
            <button data-mode="deep">深入</button>
          </div>
          <p class="tiny">影响注入给 GM 的长期记忆量，不会删除存档。</p>
        </section>
        <section class="side-section">
          <div class="section-title">Structured Updates</div>
          <div class="list" id="updateList">
            <div class="tiny">暂无新结构化更新。</div>
          </div>
        </section>
      </div>
    </aside>

    <main class="main">
      <header class="topbar">
        <div>
          <div class="thread-title">《我蕾穆丽娜不爱你》</div>
          <div class="tiny" id="locationLine">读取中...</div>
        </div>
        <div class="top-actions">
          <button class="ghost" id="debugBtn">参考</button>
          <button class="ghost" id="statusBtn">状态</button>
          <button class="primary" id="openingBtn">生成开场</button>
        </div>
      </header>

      <section class="chat" id="chat">
        <div class="empty" id="emptyState">
          <h1>准备继续柏林弧</h1>
          <p>读档后可以直接行动。右侧状态和记忆会随着 GM 输出里的结构化标签自动更新。</p>
        </div>
      </section>

      <section class="guide" id="guide"></section>

      <footer class="composer">
        <div class="composer-shell">
          <div class="slash-menu" id="slashMenu" hidden></div>
          <div class="add-menu" id="addMenu" hidden></div>
          <div class="tool-menu" id="toolMenu" hidden></div>
          <div class="permission-menu" id="permissionMenu" hidden></div>
          <div class="model-menu" id="modelMenu" hidden></div>
          <div class="run-feed" id="runFeed" hidden></div>
          <div class="ask-popover" id="askPopover" hidden></div>
          <input id="fileInput" type="file" multiple hidden />
          <input id="imageInput" type="file" accept="image/*" multiple hidden />
          <input id="skillInput" type="file" accept=".md,.zip,text/markdown,application/zip" hidden />
          <div class="composer-box">
            <div class="attachment-strip" id="attachmentStrip" hidden></div>
            <textarea id="messageInput" placeholder="输入行动、对话，或输入 / 调出命令"></textarea>
            <div class="composer-actions">
              <div class="composer-left">
                <button class="icon-btn" id="addBtn" title="添加照片、文件、MCP 或 Skill">+</button>
                <button class="permission-chip" id="permissionBtn" title="选择 LLM 写入权限">
                  <span class="permission-icon">!</span>
                  <span id="permissionLabel">完全访问权限</span>
                  <span class="chevron">⌄</span>
                </button>
              </div>
              <div class="composer-right">
                <button class="model-chip" id="modelBtn" title="选择模型">
                  <span id="modelChipLabel">Gemini 3.5</span>
                  <span class="chevron">⌄</span>
                </button>
                <button class="icon-btn" id="micBtn" title="语音输入" disabled>⌕</button>
                <button class="round-btn stop" id="stopBtn" disabled title="停止">■</button>
                <button class="round-btn primary" id="sendBtn" title="发送">↑</button>
              </div>
            </div>
          </div>
        </div>
      </footer>
    </main>

    <aside class="inspector">
      <div class="inspect-head">
        <strong>状态与记忆</strong>
        <div class="tabs">
          <button class="active" data-tab="status">状态</button>
          <button data-tab="memory">记忆</button>
          <button data-tab="worldbook">世界书</button>
          <button data-tab="cards">角色卡</button>
          <button data-tab="worldline">世界线</button>
          <button data-tab="context">上下文</button>
          <button data-tab="debug">调试</button>
        </div>
      </div>
      <div class="inspect-body">
        <section class="panel active" id="panel-status"></section>
        <section class="panel" id="panel-memory">
          <div class="memory-add">
            <select id="memoryBucket">
              <option value="pinned">固定记忆</option>
              <option value="facts">事实</option>
              <option value="abilities">能力</option>
              <option value="resources">资源</option>
              <option value="notes">笔记</option>
            </select>
            <input id="memoryText" placeholder="添加一条长期记忆..." />
            <button id="addMemoryBtn">添加记忆</button>
          </div>
          <div id="memoryList"></div>
        </section>
        <section class="panel" id="panel-worldbook">
          <div id="worldbookList"></div>
        </section>
        <section class="panel" id="panel-cards">
          <div id="cardList"></div>
        </section>
        <section class="panel" id="panel-worldline">
          <div class="memory-add">
            <input id="variableKey" placeholder="用户变量名，例如 蕾穆丽娜安全" />
            <input id="variableValue" placeholder="变量值，例如 必须优先保护" />
            <button id="addVariableBtn">添加变量</button>
          </div>
          <div id="worldlineList"></div>
        </section>
        <section class="panel" id="panel-context">
          <div id="contextList"></div>
        </section>
        <section class="panel" id="panel-debug">
          <div class="kv">
            <h3>上轮检索到的参考资料</h3>
            <p id="retrievalText">暂无。</p>
          </div>
        </section>
      </div>
    </aside>
  </div>

  <div class="setup-modal" id="setupModal">
    <div class="dialog">
      <h2>新游戏</h2>
      <div class="form-grid">
        <label>角色定位
          <select id="roleSelect"></select>
        </label>
        <label>名字
          <input id="nameInput" />
        </label>
        <label>背景
          <textarea id="backgroundInput" rows="4"></textarea>
        </label>
      </div>
      <div class="dialog-actions">
        <button id="cancelNewBtn">取消</button>
        <button class="primary" id="confirmNewBtn">创建并备份旧档</button>
      </div>
    </div>
  </div>

  <div class="toast" id="toast"></div>

  <script>
    const $ = (id) => document.getElementById(id);
    const state = { payload: null, busy: false, streamingEl: null, slashIndex: 0, attachments: [], runEvents: [], answeringQuestion: null };
    const permissionModes = [
      ["default", "默认权限", "只自动写入常用剧情状态"],
      ["auto_review", "自动审查", "高风险字段进入待审"],
      ["full_access", "完全访问权限", "允许 GM 写回页面变量"],
    ];
    const slashCommands = [
      { command: "/save", insert: "/save", title: "存档", desc: "立即保存当前游戏" },
      { command: "/status", insert: "/status", title: "状态", desc: "查看玩家、世界线、记忆摘要" },
      { command: "/debug", insert: "/debug", title: "参考", desc: "查看上轮检索资料" },
      { command: "/time", insert: "/time ", title: "时间线", desc: "手动锁定当前时间线" },
      { command: "/loc", insert: "/loc ", title: "地点", desc: "手动更新当前位置" },
      { command: "/rel", insert: "/rel ", title: "关系", desc: "写入角色关系状态" },
      { command: "/memory deep", insert: "/memory deep", title: "深入记忆", desc: "增加长期记忆注入量" },
      { command: "/memory normal", insert: "/memory normal", title: "标准记忆", desc: "恢复标准记忆注入" },
      { command: "/var", insert: "/var ", title: "用户变量", desc: "写入世界线硬约束变量" },
      { command: "/pin", insert: "/pin ", title: "固定记忆", desc: "添加永远保留的记忆" },
      { command: "/note", insert: "/note ", title: "笔记", desc: "添加玩家笔记" },
      { command: "/permission full_access", insert: "/permission full_access", title: "完全访问权限", desc: "允许结构化文本写回页面变量" },
      { command: "/permission auto_review", insert: "/permission auto_review", title: "自动审查", desc: "高风险写入进入待审" },
      { command: "/permission default", insert: "/permission default", title: "默认权限", desc: "仅自动写入常用剧情状态" },
    ];

    const mdEscape = (text) => String(text ?? "").replace(/[&<>]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[ch]));
    const scrollChatToBottom = () => requestAnimationFrame(() => {
      const chat = $("chat");
      chat.scrollTop = chat.scrollHeight;
    });

    function toast(text) {
      const el = $("toast");
      el.textContent = text;
      el.classList.add("show");
      setTimeout(() => el.classList.remove("show"), 2600);
    }

    function runLabel(step) {
      const phase = step.phase || "run";
      const labels = {
        prompt: "正在读取子代理提示",
        intent: "已解析玩家意图",
        llm_curator: step.status === "running" ? "正在运行上下文子代理" : "已完成上下文子代理",
        timeline: "已锚定时间线",
        retrieval: "已检索剧情资料",
        assembly: "已组装主 GM 上下文",
        main_gm: step.status === "running" ? "主 GM 正在生成" : "主 GM 已完成",
        structured_updates: "已处理结构化写回",
        aborted: "已停止本轮运行",
      };
      return step.label || labels[phase] || step.message || phase;
    }

    function pushRunEvent(step) {
      state.runEvents = [...state.runEvents, { ...step, label: runLabel(step), at: Date.now() }].slice(-6);
      renderRunFeed();
    }

    function renderRunFeed() {
      const el = $("runFeed");
      if (!state.busy && !state.runEvents.length) {
        el.hidden = true;
        el.innerHTML = "";
        return;
      }
      const visible = state.runEvents.slice(-4);
      el.hidden = visible.length === 0 || !$("askPopover").hidden;
      el.innerHTML = visible.map(item => `
        <div class="run-event ${item.status === "done" ? "done" : item.status === "stopped" ? "pending" : ""}">
          <span class="run-icon">&gt;_</span>
          <span>${mdEscape(item.label)}</span>
        </div>
      `).join("");
    }

    function clearRunFeedLater() {
      setTimeout(() => {
        if (!state.busy) {
          state.runEvents = [];
          renderRunFeed();
        }
      }, 2600);
    }

    async function api(path, options = {}) {
      const res = await fetch(path, {
        headers: { "Content-Type": "application/json", ...(options.headers || {}) },
        ...options,
      });
      if (!res.ok) throw new Error(await res.text());
      return await res.json();
    }

    function setBusy(busy) {
      state.busy = busy;
      $("sendBtn").disabled = busy;
      $("openingBtn").disabled = busy;
      $("stopBtn").disabled = !busy;
      $("stopBtn").classList.toggle("active", busy);
      $("sendBtn").classList.toggle("hidden", busy);
      renderRunFeed();
      hideSlashMenu();
      hideAddMenu();
      hideModelMenu();
      $("permissionMenu").hidden = true;
    }

    function permissionModeLabel(mode) {
      return (permissionModes.find(([value]) => value === mode) || permissionModes[2])[1];
    }

    function renderPermissionControl(payload) {
      const mode = payload.permissions?.mode || "full_access";
      $("permissionLabel").textContent = permissionModeLabel(mode);
      const menu = $("permissionMenu");
      menu.innerHTML = "";
      permissionModes.forEach(([value, label, desc]) => {
        const btn = document.createElement("button");
        btn.className = `permission-item ${value === mode ? "active" : ""}`;
        btn.type = "button";
        btn.innerHTML = `
          <span class="permission-icon">!</span>
          <span><span class="slash-main">${mdEscape(label)}</span><br><span class="slash-desc">${mdEscape(desc)}</span></span>
          <span>${value === mode ? "✓" : ""}</span>
        `;
        btn.addEventListener("click", async () => {
          const out = await api("/api/permissions", {
            method: "POST",
            body: JSON.stringify({ mode: value }),
          });
          $("permissionMenu").hidden = true;
          renderPayload(out.state, false);
        });
        menu.append(btn);
      });
    }

    function renderModelControl(payload) {
      const catalog = payload.models || {};
      const selected = catalog.selected || {};
      const apis = catalog.apis || [];
      $("modelChipLabel").textContent = payload.app?.model || "模型";
      const menu = $("modelMenu");
      menu.innerHTML = "";
      apis.forEach(apiConfig => {
        const apiTitle = document.createElement("div");
        apiTitle.className = "model-api";
        apiTitle.textContent = `${apiConfig.display_name || apiConfig.id} · ${apiConfig.kind || apiConfig.id}`;
        menu.append(apiTitle);
        (apiConfig.models || []).forEach(model => {
          const active = selected.api_id === apiConfig.id && selected.model_id === model.id;
          const btn = document.createElement("button");
          btn.className = `model-item ${active ? "active" : ""}`;
          btn.type = "button";
          btn.disabled = apiConfig.enabled === false || model.enabled === false;
          btn.innerHTML = `
            <span><span class="slash-main">${mdEscape(model.display_name || model.id)}</span><br><span class="slash-desc">${mdEscape(model.real_name || model.id)}</span></span>
            <span>${active ? "✓" : ""}</span>
          `;
          btn.addEventListener("click", async () => {
            const out = await api("/api/models/select", {
              method: "POST",
              body: JSON.stringify({ api_id: apiConfig.id, model_id: model.id }),
            });
            $("modelMenu").hidden = true;
            renderPayload(out.state, false);
          });
          menu.append(btn);
        });
      });
    }

    function menuIcon(name) {
      const icons = {
        paperclip: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21.4 11.1 12 20.5a6 6 0 0 1-8.5-8.5l9.8-9.8a4 4 0 0 1 5.7 5.7l-9.8 9.8a2 2 0 0 1-2.8-2.8l9.1-9.1"/></svg>`,
        image: `<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="8" cy="10" r="1.5"/><path d="m21 16-5.2-5.2a2 2 0 0 0-2.8 0L5 18"/></svg>`,
        mcp: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 9 9-9 9-9-9 9-9Z"/><path d="M8 12h8"/></svg>`,
        plus: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>`,
        plugin: `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="7" cy="7" r="2.5"/><circle cx="17" cy="7" r="2.5"/><circle cx="7" cy="17" r="2.5"/><circle cx="17" cy="17" r="2.5"/></svg>`,
      };
      return icons[name] || icons.plugin;
    }

    function renderAddMenu(payload) {
      const caps = payload.tools?.capabilities || {};
      const menu = $("addMenu");
      menu.innerHTML = "";
      const items = [
        ["files", "paperclip", "添加照片和文件", "上传图片、文档或资料", ""],
        ["images", "image", "仅添加图片", "为后续多模态输入预留", ""],
        ["mcp", "mcp", "MCP 服务器", "查看或选择已配置 MCP", "›"],
        ["skill", "plus", "导入 Skill", caps.skill_import_enabled ? "本地部署可导入 SKILL.md 或 zip" : "服务器非管理员模式已禁用", ""],
      ];
      items.forEach(([id, icon, title, desc, suffix]) => {
        const btn = document.createElement("button");
        btn.className = "add-item";
        btn.type = "button";
        btn.disabled = id === "skill" && !caps.skill_import_enabled;
        btn.innerHTML = `
          <span class="add-icon">${menuIcon(icon)}</span>
          <span><span class="slash-main">${mdEscape(title)}</span><br><span class="slash-desc">${mdEscape(desc)}</span></span>
          <span class="slash-desc">${suffix}</span>
        `;
        btn.addEventListener("click", () => {
          if (id === "files") $("fileInput").click();
          if (id === "images") $("imageInput").click();
          if (id === "mcp") toggleToolMenu();
          if (id === "skill" && caps.skill_import_enabled) $("skillInput").click();
        });
        menu.append(btn);
      });
    }

    function renderToolMenu(payload) {
      const tools = payload.tools || {};
      const menu = $("toolMenu");
      const plugins = tools.plugins || [];
      const mcpServers = tools.mcp?.servers || [];
      const skills = tools.skills || [];
      const caps = tools.capabilities || {};
      const rows = [
        `<div class="model-api">MCP 服务器 · ${caps.mcp_config_write_enabled ? "本地可配置" : "只读"}</div>`,
        ...(mcpServers.length ? mcpServers.map(server => `
          <button class="tool-item" type="button">
            <span class="tool-icon">${menuIcon("mcp")}</span>
            <span><span class="slash-main">${mdEscape(server.display_name || server.id)}</span><br><span class="slash-desc">${mdEscape(server.command || "未设置启动命令")}</span></span>
          </button>`) : [`<button class="tool-item" type="button" disabled><span class="tool-icon">${menuIcon("mcp")}</span><span><span class="slash-main">暂无 MCP 服务器</span><br><span class="slash-desc">${caps.mcp_config_write_enabled ? "可通过 /api/mcp/server 写入 stdio 配置" : "由管理员预配置"}</span></span></button>`]),
        `<div class="model-api">Skills</div>`,
        ...(skills.length ? skills.map(skill => `
          <button class="tool-item" type="button">
            <span class="tool-icon">${menuIcon("plus")}</span>
            <span><span class="slash-main">${mdEscape(skill.name || skill.id)}</span><br><span class="slash-desc">${mdEscape(skill.path || "")}</span></span>
          </button>`) : [`<button class="tool-item" type="button" disabled><span class="tool-icon">${menuIcon("plus")}</span><span><span class="slash-main">暂无导入 Skill</span><br><span class="slash-desc">${caps.skill_import_enabled ? "用加号菜单导入" : "当前部署模式不开放"}</span></span></button>`]),
        `<div class="model-api">已安装插件</div>`,
        ...plugins.map(tool => `
          <button class="tool-item" type="button">
            <span class="tool-icon">${menuIcon("plugin")}</span>
            <span><span class="slash-main">${mdEscape(tool.name)}</span><br><span class="slash-desc">${mdEscape(tool.kind || "plugin")}</span></span>
          </button>`),
      ];
      menu.innerHTML = rows.join("");
    }

    function toggleAddMenu() {
      $("addMenu").hidden = !$("addMenu").hidden;
      if ($("addMenu").hidden) hideToolMenu();
    }

    function hideAddMenu() {
      $("addMenu").hidden = true;
      hideToolMenu();
    }

    function toggleToolMenu() {
      $("toolMenu").hidden = !$("toolMenu").hidden;
    }

    function hideToolMenu() {
      $("toolMenu").hidden = true;
    }

    async function fileToAttachment(file) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve({
          id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
          name: file.name,
          type: file.type || "application/octet-stream",
          size: file.size,
          data_url: reader.result,
        });
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(file);
      });
    }

    async function addFiles(fileList) {
      const files = Array.from(fileList || []);
      for (const file of files.slice(0, 8)) {
        if (file.size > 12 * 1024 * 1024) {
          toast(`文件过大：${file.name}`);
          continue;
        }
        state.attachments.push(await fileToAttachment(file));
      }
      renderAttachmentStrip();
      hideAddMenu();
    }

    function renderAttachmentStrip() {
      const strip = $("attachmentStrip");
      strip.hidden = state.attachments.length === 0;
      strip.innerHTML = "";
      state.attachments.forEach(item => {
        const chip = document.createElement("div");
        chip.className = "attachment-chip";
        chip.innerHTML = `<span>${item.type.startsWith("image/") ? "图片" : "文件"} · ${mdEscape(item.name)}</span>`;
        const remove = document.createElement("button");
        remove.type = "button";
        remove.textContent = "×";
        remove.title = "移除附件";
        remove.addEventListener("click", () => {
          state.attachments = state.attachments.filter(x => x.id !== item.id);
          renderAttachmentStrip();
        });
        chip.append(remove);
        strip.append(chip);
      });
    }

    function attachmentDisplayText(text, attachments) {
      if (!attachments.length) return text;
      const names = attachments.map(item => `- ${item.name}`).join("\n");
      return `${text || "（发送了附件）"}\n\n[附件]\n${names}`;
    }

    async function importSkillFile(file) {
      try {
        const attachment = await fileToAttachment(file);
        const out = await api("/api/skills/import", {
          method: "POST",
          body: JSON.stringify({ file: attachment }),
        });
        state.payload.tools = out.tools;
        renderAddMenu(state.payload);
        renderToolMenu(state.payload);
        toast(`已导入 Skill：${out.skill.name}`);
      } catch (err) {
        toast(`导入 Skill 失败：${err.message}`);
      } finally {
        $("skillInput").value = "";
        hideAddMenu();
      }
    }

    function syncTextareaHeight() {
      const input = $("messageInput");
      input.style.height = "auto";
      input.style.height = `${Math.min(input.scrollHeight, 116)}px`;
    }

    function slashMatch() {
      const input = $("messageInput");
      const before = input.value.slice(0, input.selectionStart);
      const match = before.match(/(^|\s)(\/[^\s]*)$/);
      if (!match) return null;
      return {
        start: before.length - match[2].length,
        end: before.length,
        query: match[2].slice(1).toLowerCase(),
      };
    }

    function slashMatches() {
      const info = slashMatch();
      if (!info) return [];
      return slashCommands.filter(item => {
        const hay = `${item.command} ${item.title} ${item.desc}`.toLowerCase();
        return hay.includes(info.query);
      });
    }

    function renderSlashMenu() {
      const menu = $("slashMenu");
      const matches = slashMatches();
      if (!matches.length || state.busy) {
        hideSlashMenu();
        return;
      }
      state.slashIndex = Math.min(state.slashIndex, matches.length - 1);
      menu.innerHTML = "";
      matches.forEach((item, index) => {
        const btn = document.createElement("button");
        btn.className = `slash-item ${index === state.slashIndex ? "active" : ""}`;
        btn.type = "button";
        btn.innerHTML = `
          <span class="slash-symbol">/</span>
          <span><span class="slash-main">${mdEscape(item.title)}</span> <span class="slash-desc">${mdEscape(item.command)}</span><br><span class="slash-desc">${mdEscape(item.desc)}</span></span>
        `;
        btn.addEventListener("click", () => applySlashCommand(item));
        menu.append(btn);
      });
      menu.hidden = false;
    }

    function hideSlashMenu() {
      $("slashMenu").hidden = true;
    }

    function hideModelMenu() {
      $("modelMenu").hidden = true;
    }

    function applySlashCommand(item) {
      const input = $("messageInput");
      const info = slashMatch();
      if (!info) return;
      input.value = input.value.slice(0, info.start) + item.insert + input.value.slice(info.end);
      const caret = info.start + item.insert.length;
      input.setSelectionRange(caret, caret);
      input.focus();
      hideSlashMenu();
      syncTextareaHeight();
    }

    function messageNode(role, content = "") {
      const row = document.createElement("article");
      row.className = `message ${role}`;
      const avatar = document.createElement("div");
      avatar.className = "avatar";
      avatar.textContent = role === "user" ? "你" : "GM";
      const bubble = document.createElement("div");
      bubble.className = `bubble ${role === "user" ? "user-text" : "assistant-text"}`;
      bubble.textContent = content;
      row.append(avatar, bubble);
      return { row, bubble };
    }

    function renderMessages(history) {
      const chat = $("chat");
      chat.innerHTML = "";
      if (!history || history.length === 0) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.id = "emptyState";
        empty.innerHTML = "<h1>准备继续柏林弧</h1><p>读档后可以直接行动。右侧状态和记忆会随着 GM 输出里的结构化标签自动更新。</p>";
        chat.append(empty);
        return;
      }
      history.forEach(msg => {
        const node = messageNode(msg.role === "user" ? "user" : "assistant", msg.content || "");
        chat.append(node.row);
      });
      scrollChatToBottom();
    }

    function appendMessage(role, content = "") {
      $("emptyState")?.remove();
      const node = messageNode(role, content);
      $("chat").append(node.row);
      scrollChatToBottom();
      return node.bubble;
    }

    function renderPayload(payload, rerenderChat = false) {
      state.payload = payload;
      const player = payload.player || {};
      const memory = payload.memory || {};
      const turn = payload.turn || 0;

      $("modelLabel").textContent = payload.app?.model || "Gemini";
      $("playerName").textContent = player.name || "未命名";
      $("playerRole").textContent = player.role || "未知角色";
      $("turnPill").textContent = `${turn} 回合`;
      $("turnMeter").style.width = `${Math.min(100, (turn % 24) / 24 * 100)}%`;
      $("objectiveMini").textContent = memory.current_objective || "暂无目标";
      $("locationLine").textContent = `${player.current_location || "未知地点"} · ${payload.world?.time || ""}`;

      document.querySelectorAll(".memory-toggle button").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.mode === (memory.mode || "normal"));
      });
      renderPermissionControl(payload);
      renderModelControl(payload);
      renderAddMenu(payload);
      renderToolMenu(payload);

      renderStatus(payload);
      renderMemory(payload);
      renderWorldbook(payload);
      renderCards(payload);
      renderWorldline(payload);
      renderContext(payload);
      renderGuide(payload.suggestions || []);
      renderAskPopover(payload);
      $("retrievalText").textContent = memory.last_retrieval || "暂无。";

      const updates = memory.last_structured_updates || [];
      $("updateList").innerHTML = updates.length
        ? updates.slice().reverse().map(x => `<div class="mem-item"><span>${mdEscape(x)}</span></div>`).join("")
        : `<div class="tiny">暂无新结构化更新。</div>`;

      if (rerenderChat) renderMessages(payload.history || []);
    }

    function renderStatus(payload) {
      const player = payload.player || {};
      const world = payload.world || {};
      const timeline = world.timeline || {};
      const pending = timeline.pending_jump || null;
      const rel = payload.relationships || {};
      const memory = payload.memory || {};
      const relHtml = Object.keys(rel).length
        ? `<ul>${Object.entries(rel).map(([k,v]) => `<li><strong>${mdEscape(k)}</strong>：${mdEscape(v)}</li>`).join("")}</ul>`
        : `<p>尚未与任何人建立明确关系。</p>`;
      const anchorLabels = {
        locked: "已锁定",
        pending_confirmation: "等待 GM 确认",
      };
      const timelineDetail = pending
        ? `<p><strong>待确认跳跃</strong><br>${mdEscape(pending.from || "")} -> ${mdEscape(pending.to || "")}</p>`
        : `<p><strong>${mdEscape(anchorLabels[timeline.anchor_state] || timeline.anchor_state || "已锁定")}</strong><br>${mdEscape(timeline.current_phase || "未标定")}</p>`;
      $("panel-status").innerHTML = `
        <div class="kv"><h3>玩家档案</h3><p><strong>${mdEscape(player.name)}</strong><br>${mdEscape(player.role)}<br>${mdEscape(player.background)}</p></div>
        <div class="kv"><h3>当前位置</h3><p>${mdEscape(player.current_location)}</p></div>
        <div class="kv"><h3>当前时间线</h3><p>${mdEscape(world.time)}</p>${timelineDetail}</div>
        <div class="kv"><h3>当前目标</h3><p>${mdEscape(memory.current_objective || "暂无")}</p></div>
        <div class="kv"><h3>已知事件</h3><ul>${(world.known_events || []).map(x => `<li>${mdEscape(x)}</li>`).join("")}</ul></div>
        <div class="kv"><h3>关系状态</h3>${relHtml}</div>
      `;
    }

    function renderMemory(payload) {
      const memory = payload.memory || {};
      const buckets = [
        ["pinned", "固定记忆"],
        ["abilities", "能力"],
        ["resources", "资源"],
        ["facts", "事实"],
        ["notes", "笔记"],
      ];
      const html = buckets.map(([key, title]) => {
        const items = memory[key] || [];
        const body = items.length ? items.map((x, idx) => `
          <div class="mem-item">
            <span><strong>${title}</strong><br>${mdEscape(x)}</span>
            <button title="删除" onclick="removeMemory('${key}', ${idx})">×</button>
          </div>`).join("") : `<div class="tiny">暂无${title}。</div>`;
        return `<div class="kv"><h3>${title}</h3><div class="list">${body}</div></div>`;
      }).join("");
      $("memoryList").innerHTML = html;
    }

    function renderWorldbook(payload) {
      const ctx = payload.memory?.last_context || {};
      const entries = ctx.active_worldbook || [];
      $("worldbookList").innerHTML = entries.length ? entries.map(entry => `
        <div class="kv">
          <h3>${mdEscape(entry.title)}</h3>
          <p>${mdEscape(entry.preview || "")}</p>
          <div class="chips">${(entry.matched || []).map(x => `<span class="chip">${mdEscape(x)}</span>`).join("")}</div>
        </div>
      `).join("") : `<div class="kv"><h3>本轮未触发世界书</h3><p>发送行动后，这里会显示被关键词或场景命中的设定条目。</p></div>`;
    }

    function renderCards(payload) {
      const ctx = payload.memory?.last_context || {};
      const cards = ctx.active_character_cards || [];
      $("cardList").innerHTML = cards.length ? cards.map(card => `
        <div class="kv">
          <h3>${mdEscape(card.name)}</h3>
          <p>${mdEscape(card.preview || "")}</p>
          <div class="chips">${(card.matched || []).map(x => `<span class="chip">${mdEscape(x)}</span>`).join("")}</div>
        </div>
      `).join("") : `<div class="kv"><h3>本轮未激活 NPC 角色卡</h3><p>提到角色名、别名，或剧情中出现相关人物时会自动注入。</p></div>`;
    }

    function renderWorldline(payload) {
      const worldline = payload.worldline || {};
      const permissions = payload.permissions || {};
      const variables = worldline.user_variables || {};
      const validation = worldline.last_validation || {};
      const projection = worldline.last_projection || null;
      const pendingProjection = worldline.pending_projection || null;
      const custom = worldline.custom_ui || {};
      const pendingWrites = permissions.pending_writes || [];
      const audit = permissions.audit_log || [];
      const modeLabels = {
        default: "默认权限",
        auto_review: "自动审查",
        full_access: "完全访问权限",
      };
      const varsHtml = Object.keys(variables).length ? Object.entries(variables).map(([key, info]) => `
        <div class="mem-item">
          <span><strong>${mdEscape(key)}</strong><br>${mdEscape(info.value || "")}</span>
          <button title="删除" onclick="removeVariable('${encodeURIComponent(key)}')">×</button>
        </div>
      `).join("") : `<div class="tiny">暂无用户变量。</div>`;
      const customHtml = Object.keys(custom).length ? Object.entries(custom).map(([key, value]) => `
        <div class="mem-item"><span><strong>${mdEscape(key)}</strong><br>${mdEscape(value)}</span></div>
      `).join("") : `<div class="tiny">暂无自定义 UI 变量。</div>`;
      const pendingHtml = pendingWrites.length ? pendingWrites.slice().reverse().map(x => `
        <div class="mem-item"><span><strong>${mdEscape(x.path)}</strong><br>${mdEscape(x.value)}<br><span class="tiny">${mdEscape(x.reason || "")}</span></span></div>
      `).join("") : `<div class="tiny">暂无待审写入。</div>`;
      const auditHtml = audit.length ? audit.slice(-6).reverse().map(x => `
        <div class="mem-item"><span><strong>${mdEscape(x.path)}</strong><br>${mdEscape(x.value)}<br><span class="tiny">${mdEscape(x.source || "")} · ${mdEscape(x.mode || "")}</span></span></div>
      `).join("") : `<div class="tiny">暂无写入记录。</div>`;
      $("worldlineList").innerHTML = `
        <div class="kv"><h3>权限模式</h3><p>${mdEscape(modeLabels[permissions.mode] || permissions.mode || "完全访问权限")}</p></div>
        <div class="kv"><h3>用户变量</h3><div class="list">${varsHtml}</div></div>
        <div class="kv"><h3>设定校验</h3><p>${mdEscape(validation.status || "none")} ${validation.message ? " · " + mdEscape(validation.message) : ""}</p></div>
        <div class="kv"><h3>上次世界线推演</h3><p>${mdEscape(projection?.text || "暂无。")}</p></div>
        <div class="kv"><h3>待确认推演</h3><p>${mdEscape(pendingProjection?.text || "暂无。")}</p></div>
        <div class="kv"><h3>自定义 UI 变量</h3><div class="list">${customHtml}</div></div>
        <div class="kv"><h3>待审写入</h3><div class="list">${pendingHtml}</div></div>
        <div class="kv"><h3>写入记录</h3><div class="list">${auditHtml}</div></div>
      `;
    }

    function renderAskPopover(payload) {
      const permissions = payload.permissions || {};
      const pendingWrites = permissions.pending_writes || [];
      const questions = permissions.pending_questions || [];
      const pop = $("askPopover");
      const cards = [];
      pendingWrites.forEach((item, index) => {
        cards.push(`
          <div class="ask-card">
            <h3>需要授权：状态写入</h3>
            <p><strong>${mdEscape(item.path || "")}</strong> = ${mdEscape(item.value || "")}</p>
            <p class="tiny">${mdEscape(item.reason || "")}</p>
            <div class="ask-actions">
              <button class="primary" onclick="decidePendingWrite(${index}, 'approve')">允许</button>
              <button onclick="decidePendingWrite(${index}, 'reject')">拒绝</button>
            </div>
          </div>
        `);
      });
      questions.forEach((item, index) => {
        const options = item.options || [];
        const optionButtons = options.length ? options.map(option => {
          const encoded = encodeURIComponent(option).replace(/'/g, "%27");
          return `<button onclick="answerPendingQuestion(${index}, '${encoded}')">${mdEscape(option)}</button>`;
        }).join("") : "";
        cards.push(`
          <div class="ask-card">
            <h3>GM 想确认下一步</h3>
            <p>${mdEscape(item.question || "")}</p>
            <div class="ask-actions">
              ${optionButtons}
              <button onclick="focusQuestionAnswer(${index})">手动回答</button>
              <button onclick="clearPendingQuestion(${index})">稍后不显示</button>
            </div>
          </div>
        `);
      });
      pop.innerHTML = cards.join("");
      pop.hidden = cards.length === 0;
      renderRunFeed();
    }

    async function decidePendingWrite(index, decision) {
      try {
        const out = await api("/api/permissions/pending-write", {
          method: "POST",
          body: JSON.stringify({ index, decision }),
        });
        renderPayload(out.state, false);
        toast(out.result || "已处理");
      } catch (err) {
        toast(`处理失败：${err.message}`);
      }
    }

    function focusQuestionAnswer(index) {
      const questions = state.payload?.permissions?.pending_questions || [];
      const question = questions[index]?.question || "";
      const input = $("messageInput");
      input.value = question ? `关于“${question}”，我的选择是：` : "";
      state.answeringQuestion = index;
      input.focus();
      syncTextareaHeight();
    }

    function answerPendingQuestion(index, encodedOption) {
      const option = decodeURIComponent(encodedOption || "");
      const questions = state.payload?.permissions?.pending_questions || [];
      const question = questions[index]?.question || "";
      const input = $("messageInput");
      input.value = question ? `关于“${question}”，我选择：${option}` : option;
      state.answeringQuestion = index;
      input.focus();
      syncTextareaHeight();
    }

    async function clearPendingQuestion(index) {
      try {
        const out = await api("/api/questions/clear", {
          method: "POST",
          body: JSON.stringify({ index }),
        });
        renderPayload(out.state, false);
      } catch (err) {
        toast(`处理失败：${err.message}`);
      }
    }

    function renderContext(payload) {
      const ctx = payload.memory?.last_context || {};
      const agent = payload.memory?.last_context_agent || {};
      const layers = ctx.layers || [];
      const steps = agent.steps || [];
      const cache = ctx.cache_plan || agent.cache_plan || {};
      const plan = ctx.curator_plan || agent.curator_plan || {};
      const stepHtml = steps.length ? steps.map(step => `
        <div class="context-layer">
          <strong>${mdEscape(step.phase || "step")} · ${mdEscape(step.status || "")}</strong>
          <div class="meta">${step.elapsed_ms || 0} ms</div>
          <div>${mdEscape(step.message || "")}</div>
        </div>
      `).join("") : `<div class="tiny">暂无子代理运行记录。</div>`;
      const head = `
        <div class="kv">
          <h3>本轮上下文预算</h3>
          <p>约 ${ctx.estimated_tokens || 0} tokens · ${ctx.total_chars || 0} 字符</p>
        </div>
        <div class="kv">
          <h3>上下文子代理</h3>
          <p>${mdEscape(agent.status || "idle")} · 负责章节锚点、召回裁剪、上下文清单</p>
          <p class="tiny">${mdEscape(plan.intent || "")}</p>
          <div class="list">${stepHtml}</div>
        </div>
        <div class="kv">
          <h3>Prompt Cache 结构</h3>
          <p>${mdEscape(cache.strategy || "stable-prefix-first")} · 稳定前缀约 ${cache.stable_prefix_tokens || 0} tokens · 动态尾部约 ${cache.volatile_tail_tokens || 0} tokens</p>
          <p class="tiny">${mdEscape(cache.note || "真实命中率需由模型厂商用量字段确认。")}</p>
        </div>`;
      const body = layers.length ? layers.map(layer => `
        <div class="context-layer">
          <strong>${mdEscape(layer.title)}</strong>
          <div class="meta">${layer.estimated_tokens || 0} tokens · ${layer.chars || 0} 字符${layer.sticky ? " · 固定" : ""}</div>
          <div>${mdEscape(layer.preview || "")}</div>
        </div>
      `).join("") : `<div class="kv"><h3>暂无上下文记录</h3><p>发送一轮消息后，这里会显示系统实际注入给 GM 的层级。</p></div>`;
      $("contextList").innerHTML = head + body;
    }

    function renderGuide(suggestions) {
      const guide = $("guide");
      guide.innerHTML = "";
      suggestions.forEach(text => {
        const btn = document.createElement("button");
        btn.textContent = text;
        btn.addEventListener("click", () => {
          $("messageInput").value = text;
          $("messageInput").focus();
        });
        guide.append(btn);
      });
    }

	    async function readSSE(res, bubble) {
	      if (!res.ok) {
	        const detail = await res.text().catch(() => "");
	        throw new Error(detail || `HTTP ${res.status}`);
	      }
	      if (!res.body) throw new Error("服务器没有返回流式响应");
	      const reader = res.body.getReader();
	      const decoder = new TextDecoder();
	      let buf = "";
	      let full = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let split;
        while ((split = buf.indexOf("\n\n")) >= 0) {
          const raw = buf.slice(0, split);
          buf = buf.slice(split + 2);
          const lines = raw.split("\n");
          const event = (lines.find(l => l.startsWith("event:")) || "event: message").slice(6).trim();
          const dataLine = lines.find(l => l.startsWith("data:"));
          const data = dataLine ? JSON.parse(dataLine.slice(5).trim()) : {};
          if (event === "token") {
            full += data.text || "";
            bubble.textContent = full;
            scrollChatToBottom();
          } else if (event === "status") {
            renderPayload(data.status || data, false);
          } else if (event === "done") {
            pushRunEvent({ phase: "main_gm", status: data.interrupted ? "stopped" : "done" });
            renderPayload(data.status || data, false);
          } else if (event === "retrieval") {
            pushRunEvent({ phase: "retrieval", status: "done" });
            if (state.payload) {
              state.payload.memory = state.payload.memory || {};
              state.payload.memory.last_retrieval = data.text || "";
              renderPayload(state.payload, false);
            }
          } else if (event === "context") {
            pushRunEvent({ phase: "assembly", status: "done" });
            if (state.payload) {
              state.payload.memory = state.payload.memory || {};
              state.payload.memory.last_context = data.debug || {};
              renderPayload(state.payload, false);
            }
          } else if (event === "agent") {
            pushRunEvent(data);
            if (state.payload) {
              state.payload.memory = state.payload.memory || {};
              const agent = state.payload.memory.last_context_agent || { status: "running", steps: [] };
              agent.status = data.status || "running";
              agent.steps = [...(agent.steps || []), data].slice(-12);
              state.payload.memory.last_context_agent = agent;
              renderPayload(state.payload, false);
            }
          } else if (event === "updates") {
            pushRunEvent({ phase: "structured_updates", status: "done" });
            if (data.items?.length) toast(`状态已更新：${data.items.length} 项`);
          } else if (event === "error") {
            bubble.textContent = (full || data.partial || "") + `\n\n[错误] ${data.message}`;
            toast("生成失败，详情在聊天里。");
          }
        }
      }
    }

    async function sendMessage(textOverride = null) {
      if (state.busy) return;
      const input = $("messageInput");
      const text = (textOverride ?? input.value).trim();
      const outgoingAttachments = textOverride == null ? state.attachments.slice() : [];
      if (!text && !outgoingAttachments.length) return;
      input.value = "";
      state.attachments = [];
      renderAttachmentStrip();
      syncTextareaHeight();
      hideSlashMenu();
      hideAddMenu();
      appendMessage("user", attachmentDisplayText(text, outgoingAttachments));
      const bubble = appendMessage("assistant", "");
      setBusy(true);
      state.runEvents = [];
      pushRunEvent({ phase: "prompt", status: "running", label: "正在准备本轮请求" });
      try {
	        const res = await fetch("/api/chat", {
	          method: "POST",
	          headers: { "Content-Type": "application/json" },
	          body: JSON.stringify({ message: text, attachments: outgoingAttachments }),
	        });
	        await readSSE(res, bubble);
        if (state.answeringQuestion != null) {
          try {
            await api("/api/questions/clear", {
              method: "POST",
              body: JSON.stringify({ index: state.answeringQuestion }),
            });
          } catch (_) {}
          state.answeringQuestion = null;
        }
	      } catch (err) {
	        if (isNetworkError(err)) {
	          restoreDraft(text, outgoingAttachments);
	          const alive = await serverAlive();
	          bubble.textContent = alive
	            ? "[连接中断] 服务现在可用，原输入已放回输入框，请直接重试发送。"
	            : "[连接中断] 本地服务暂时不可用，原输入已放回输入框；等服务恢复后再发送。";
	        } else {
	          bubble.textContent = `[错误] ${err.message}`;
	        }
	      } finally {
	        setBusy(false);
	        clearRunFeedLater();
	        await refresh(false).catch(() => {});
	      }
	    }

    async function generateOpening() {
      if (state.busy) return;
      const bubble = appendMessage("assistant", "");
      setBusy(true);
      state.runEvents = [];
      pushRunEvent({ phase: "main_gm", status: "running", label: "正在生成开场" });
      try {
	        const res = await fetch("/api/opening", { method: "POST" });
	        await readSSE(res, bubble);
	      } catch (err) {
	        bubble.textContent = isNetworkError(err)
	          ? "[连接中断] 本地服务暂时不可用；请刷新后重试生成开场。"
	          : `[错误] ${err.message}`;
	      } finally {
	        setBusy(false);
	        clearRunFeedLater();
	        await refresh(true).catch(() => {});
	      }
	    }

	    function isNetworkError(err) {
	      const message = String(err?.message || err || "");
	      return err instanceof TypeError || /failed to fetch|networkerror|load failed|connection/i.test(message);
	    }

	    async function serverAlive() {
	      try {
	        const res = await fetch("/api/state", { cache: "no-store" });
	        return res.ok;
	      } catch (_) {
	        return false;
	      }
	    }

	    function restoreDraft(text, attachments) {
	      const input = $("messageInput");
	      input.value = text || "";
	      state.attachments = attachments || [];
	      renderAttachmentStrip();
	      syncTextareaHeight();
	      input.focus();
	    }

	    async function refresh(rerenderChat = true) {
	      const payload = await api("/api/state");
      renderPayload(payload, rerenderChat);
    }

    async function removeMemory(bucket, index) {
      const out = await api("/api/memory/remove", {
        method: "POST",
        body: JSON.stringify({ bucket, index }),
      });
      renderPayload(out.state, false);
    }

    async function removeVariable(encodedKey) {
      const out = await api("/api/worldline/variable/remove", {
        method: "POST",
        body: JSON.stringify({ key: decodeURIComponent(encodedKey) }),
      });
      renderPayload(out.state, false);
    }

    function setupNewGameDialog(payload) {
      const roles = payload.app?.roles || [];
      const select = $("roleSelect");
      select.innerHTML = roles.map(x => `<option>${mdEscape(x)}</option>`).join("");
      const applyPreset = () => {
        const preset = payload.app?.preset?.[select.value] || {};
        $("nameInput").value = preset.name || "";
        $("backgroundInput").value = preset.background || "";
      };
      select.addEventListener("change", applyPreset);
      applyPreset();
    }

    document.querySelectorAll(".tabs button").forEach(btn => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".tabs button").forEach(x => x.classList.remove("active"));
        document.querySelectorAll(".panel").forEach(x => x.classList.remove("active"));
        btn.classList.add("active");
        $(`panel-${btn.dataset.tab}`).classList.add("active");
      });
    });

    document.querySelectorAll(".memory-toggle button").forEach(btn => {
      btn.addEventListener("click", async () => {
        const out = await api("/api/memory/mode", {
          method: "POST",
          body: JSON.stringify({ mode: btn.dataset.mode }),
        });
        renderPayload(out.state, false);
      });
    });

    $("sendBtn").addEventListener("click", () => sendMessage());
    $("messageInput").addEventListener("input", () => {
      syncTextareaHeight();
      state.slashIndex = 0;
      renderSlashMenu();
    });
    $("messageInput").addEventListener("click", renderSlashMenu);
    $("messageInput").addEventListener("keydown", (event) => {
      const menuOpen = !$("slashMenu").hidden;
      if (menuOpen && ["ArrowDown", "ArrowUp", "Enter", "Tab", "Escape"].includes(event.key)) {
        event.preventDefault();
        const matches = slashMatches();
        if (event.key === "Escape") {
          hideSlashMenu();
          return;
        }
        if (event.key === "ArrowDown") {
          state.slashIndex = (state.slashIndex + 1) % Math.max(matches.length, 1);
          renderSlashMenu();
          return;
        }
        if (event.key === "ArrowUp") {
          state.slashIndex = (state.slashIndex - 1 + Math.max(matches.length, 1)) % Math.max(matches.length, 1);
          renderSlashMenu();
          return;
        }
        if (matches[state.slashIndex]) {
          applySlashCommand(matches[state.slashIndex]);
        }
        return;
      }
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
      }
    });
    $("addBtn").addEventListener("click", (event) => {
      event.stopPropagation();
      toggleAddMenu();
      hideSlashMenu();
      hideModelMenu();
      $("permissionMenu").hidden = true;
    });
    $("fileInput").addEventListener("change", async (event) => {
      await addFiles(event.target.files);
      event.target.value = "";
    });
    $("imageInput").addEventListener("change", async (event) => {
      await addFiles(event.target.files);
      event.target.value = "";
    });
    $("skillInput").addEventListener("change", async (event) => {
      const file = event.target.files?.[0];
      if (file) await importSkillFile(file);
    });
    $("permissionBtn").addEventListener("click", (event) => {
      event.stopPropagation();
      $("permissionMenu").hidden = !$("permissionMenu").hidden;
      hideSlashMenu();
      hideAddMenu();
      hideModelMenu();
    });
    $("modelBtn").addEventListener("click", (event) => {
      event.stopPropagation();
      $("modelMenu").hidden = !$("modelMenu").hidden;
      hideSlashMenu();
      hideAddMenu();
      $("permissionMenu").hidden = true;
    });
    document.addEventListener("click", (event) => {
      if (!$("addMenu").contains(event.target) && !$("toolMenu").contains(event.target) && !$("addBtn").contains(event.target)) {
        hideAddMenu();
      }
      if (!$("permissionMenu").contains(event.target) && !$("permissionBtn").contains(event.target)) {
        $("permissionMenu").hidden = true;
      }
      if (!$("modelMenu").contains(event.target) && !$("modelBtn").contains(event.target)) {
        hideModelMenu();
      }
      if (!$("slashMenu").contains(event.target) && !$("messageInput").contains(event.target)) {
        hideSlashMenu();
      }
    });
    $("stopBtn").addEventListener("click", async () => {
      await api("/api/stop", { method: "POST" });
      toast("已请求停止生成。");
    });
    $("saveBtn").addEventListener("click", async () => {
      const out = await api("/api/save", { method: "POST" });
      renderPayload(out.state, false);
      toast("已存档。");
    });
    $("continueBtn").addEventListener("click", () => refresh(true));
    $("openingBtn").addEventListener("click", generateOpening);
    $("statusBtn").addEventListener("click", () => sendMessage("/status"));
    $("debugBtn").addEventListener("click", () => sendMessage("/debug"));
    $("newGameBtn").addEventListener("click", () => $("setupModal").classList.add("show"));
    $("cancelNewBtn").addEventListener("click", () => $("setupModal").classList.remove("show"));
    $("confirmNewBtn").addEventListener("click", async () => {
      const out = await api("/api/new", {
        method: "POST",
        body: JSON.stringify({
          role: $("roleSelect").value,
          name: $("nameInput").value,
          background: $("backgroundInput").value,
        }),
      });
      $("setupModal").classList.remove("show");
      renderPayload(out.state, true);
      toast(out.backup ? "已备份旧档并创建新游戏。" : "已创建新游戏。");
    });
    $("addMemoryBtn").addEventListener("click", async () => {
      const text = $("memoryText").value.trim();
      if (!text) return;
      const out = await api("/api/memory/add", {
        method: "POST",
        body: JSON.stringify({ bucket: $("memoryBucket").value, text }),
      });
      $("memoryText").value = "";
      renderPayload(out.state, false);
    });
    $("addVariableBtn").addEventListener("click", async () => {
      const key = $("variableKey").value.trim();
      const value = $("variableValue").value.trim();
      if (!key || !value) return;
      const out = await api("/api/worldline/variable", {
        method: "POST",
        body: JSON.stringify({ key, value }),
      });
      $("variableKey").value = "";
      $("variableValue").value = "";
      renderPayload(out.state, false);
    });

    refresh(true).then(payload => setupNewGameDialog(state.payload)).catch(err => toast(err.message));
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn

    url = f"http://{HOST}:{PORT}"
    print(f"[UI] {APP_TITLE} RPG workspace: {url}")
    webbrowser.open(url)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
