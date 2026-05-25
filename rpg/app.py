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
from pathlib import Path
from threading import Event, Lock
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.middleware.gzip import GZipMiddleware

load_dotenv(Path(__file__).parent.parent / ".env")

sys.path.insert(0, str(Path(__file__).parent))

from gm import GameMaster
from context_engine import build_context_bundle
from context_agent import run_context_agent
from model_registry import delete_model, load_model_catalog, selected_model, select_model, upsert_api, upsert_model
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


def _verify_acceptance_rule(acceptance: list[str], response_text: str, updates: list[str]) -> list[str]:
    """task 81：cheap 规则验证。返回未通过的 acceptance 条款列表。

    Chinese 用 bigram (2-char 连续片段) 匹配，避免长串 greedy token 匹配
    导致永远查不到。例 "回应了去灯塔意图" → bigrams 含 '灯塔'，response
    含 '灯塔' 即认为关联词命中。

    策略：
    1. 否定条款（含 不要/不应/禁止 等关键词）→ 提目标主体 bigram 出现在
       response 就算 unmet
    2. 肯定条款 → 至少 30% 的 ≥2-char 关键 bigram 出现在 response 算通过

    task 84 把这个函数拆出来作为 rule 模式的实现；llm / hybrid 模式见
    acceptance_verifier.py。
    """
    if not acceptance or not response_text:
        return []
    haystack = (response_text + "\n" + "\n".join(str(u) for u in (updates or []))).lower()
    unmet: list[str] = []
    import re as _re
    _STOPWORDS = {
        "回应", "玩家", "本轮", "保留", "正文", "GM", "gm", "应该", "需要",
        "如果", "或者", "包括", "其它", "其他", "可以", "应当", "必须", "条件",
        "这个", "那个", "我们", "他们", "她们", "你们",
    }
    _NEG_KEYWORDS = ("不要", "不应", "禁止", "不能", "不得", "没把", "不可", "杜绝")

    def _key_bigrams(text: str) -> list[str]:
        """从中文条款里取所有 2-3 字 bigram/trigram，过掉 stopword。"""
        # 单独把 stopword 删掉再切 bigram，避免 '玩家' 之类被切到名词里
        cleaned = text
        for sw in _STOPWORDS:
            cleaned = cleaned.replace(sw, " ")
        # 同时去掉否定关键词本身（不希望把"不要"也当成匹配标的）
        for nk in _NEG_KEYWORDS:
            cleaned = cleaned.replace(nk, " ")
        # 切连续中文段
        segs = _re.findall(r"[一-鿿]+", cleaned)
        # 每段做 bigram + trigram
        out: list[str] = []
        for seg in segs:
            if len(seg) >= 2:
                for i in range(len(seg) - 1):
                    out.append(seg[i:i + 2])
                for i in range(len(seg) - 2):
                    out.append(seg[i:i + 3])
        # 字母 token（如英文名词）也加入
        for tok in _re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}", cleaned):
            if tok not in _STOPWORDS:
                out.append(tok.lower())
        # dedup 保持顺序
        seen: set[str] = set()
        dedup = []
        for x in out:
            if x not in seen:
                seen.add(x)
                dedup.append(x)
        return dedup[:30]

    for cond in acceptance[:8]:
        cond_str = str(cond).strip()
        if not cond_str:
            continue
        cond_low = cond_str.lower()
        bigrams = _key_bigrams(cond_str)
        if not bigrams:
            continue
        is_negative = any(k in cond_low for k in _NEG_KEYWORDS)
        # 任意核心 bigram/trigram 命中即视为关联词出现。规则版能抓的最
        # 简单信号：response 有/无包含 acceptance 提到的具体名词。
        # 更精细的语义判断留给后续小 LLM 验证。
        hit = any(b.lower() in haystack for b in bigrams)
        if is_negative:
            # 否定条款：禁词主体的 bigram 在 response 出现 → unmet
            if hit:
                unmet.append(cond_str)
        else:
            # 肯定条款：至少 1 个核心 bigram 命中算通过；全没命中 → unmet
            if not hit:
                unmet.append(cond_str)
    return unmet


def _verify_acceptance(
    acceptance: list[str],
    response_text: str,
    updates: list[str],
    *,
    mode: str = "rule",
    user_id: int | None = None,
) -> list[str]:
    """task 84：acceptance 验证三模式 dispatcher。

    - mode="rule"   纯规则（task 81 实现），便宜，召回好假阳性多
    - mode="llm"    便宜 LLM 整批判定；调用失败 → 降级到 rule
    - mode="hybrid" 先 rule 跑，rule 判定 unmet 的条款再让 LLM 二次确认；
                    rule 全通过就直接 [] 不浪费 LLM 调用

    返回 unmet 条款列表。调用方负责回填 audit_log。
    """
    mode_norm = (mode or "rule").strip().lower()
    if mode_norm not in ("rule", "llm", "hybrid"):
        mode_norm = "rule"

    if mode_norm == "rule":
        return _verify_acceptance_rule(acceptance, response_text, updates)

    if mode_norm == "llm":
        try:
            from acceptance_verifier import verify_acceptance_llm
            out = verify_acceptance_llm(
                acceptance=acceptance,
                response_text=response_text,
                updates=updates or [],
                user_id=user_id,
            )
        except Exception as exc:
            print(f"[acceptance] llm mode raised; falling back to rule: {exc}")
            return _verify_acceptance_rule(acceptance, response_text, updates)
        if out is None:
            return _verify_acceptance_rule(acceptance, response_text, updates)
        return out

    # hybrid
    rule_unmet = _verify_acceptance_rule(acceptance, response_text, updates)
    if not rule_unmet:
        # 规则都通过：不浪费 LLM 调用
        return []
    try:
        from acceptance_verifier import verify_acceptance_llm
        llm_unmet = verify_acceptance_llm(
            acceptance=rule_unmet,
            response_text=response_text,
            updates=updates or [],
            user_id=user_id,
        )
    except Exception as exc:
        print(f"[acceptance] hybrid llm step raised; keeping rule verdict: {exc}")
        return rule_unmet
    if llm_unmet is None:
        # LLM 不可用 → 保留 rule 判定（保守）
        return rule_unmet
    return llm_unmet


def _is_set_parser_enabled(api_user: dict | None) -> bool:
    """task 77：用户偏好 set_parser.enabled = true 时开启 /set 自然语言解析子代理。
    默认 false（向后兼容：detect_set_directive 简单 path=value 仍工作）。
    """
    if not api_user:
        return False
    uid = api_user.get("id")
    if not uid:
        return False
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            row = db.execute(
                "select preferences from user_preferences where user_id = %s",
                (int(uid),),
            ).fetchone()
        if row and isinstance(row.get("preferences"), dict):
            return bool(row["preferences"].get("set_parser.enabled"))
    except Exception:
        return False
    return False


def _is_extractor_enabled(api_user: dict | None) -> bool:
    """task 62：用户偏好 extractor.enabled = true 时开启 GM-extractor 第二步。
    默认 false（保持向后兼容，单步 GM 流程不变）。
    """
    if not api_user:
        return False
    uid = api_user.get("id")
    if not uid:
        return False
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            row = db.execute(
                "select preferences from user_preferences where user_id = %s",
                (int(uid),),
            ).fetchone()
        if row and isinstance(row.get("preferences"), dict):
            return bool(row["preferences"].get("extractor.enabled"))
    except Exception:
        return False
    return False


def _clarify_threshold(api_user: dict | None) -> float:
    """task 85：用户偏好 curator.confidence_threshold —— curator confidence 低于
    此值时跳过主 GM 直接询问玩家（task 80 routing）。默认 0.5；非法 / 越界值
    一律 clamp 到 [0.0, 1.0]，读不到偏好（匿名 / 数据库异常）也回退 0.5。
    """
    default = 0.5
    if not api_user:
        return default
    uid = api_user.get("id")
    if not uid:
        return default
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            row = db.execute(
                "select preferences from user_preferences where user_id = %s",
                (int(uid),),
            ).fetchone()
        if row and isinstance(row.get("preferences"), dict):
            raw = row["preferences"].get("curator.confidence_threshold")
            if raw is None:
                return default
            try:
                val = float(raw)
            except (TypeError, ValueError):
                return default
            if val != val:  # NaN
                return default
            if val < 0.0:
                return 0.0
            if val > 1.0:
                return 1.0
            return val
    except Exception:
        return default
    return default


def _acceptance_verifier_mode(api_user: dict | None) -> str:
    """task 84：读 preferences.acceptance_verifier.mode；返回 'rule'|'llm'|'hybrid'。

    缺省 'rule'（task 81 行为不变）。值校验在 _verify_acceptance 里也会再做
    一道，未知值都会落到 'rule'。
    """
    default = "rule"
    if not api_user:
        return default
    uid = api_user.get("id")
    if not uid:
        return default
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            row = db.execute(
                "select preferences from user_preferences where user_id = %s",
                (int(uid),),
            ).fetchone()
        if row and isinstance(row.get("preferences"), dict):
            val = row["preferences"].get("acceptance_verifier.mode")
            if isinstance(val, str):
                v = val.strip().lower()
                if v in ("rule", "llm", "hybrid"):
                    return v
    except Exception:
        return default
    return default


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
    # task 10：把当前激活存档的 id/title 直接挂在 /api/state 顶层 + state 字段里，
    # Game Console 左侧栏拿来显示「当前存档」，避免回退到 hard-coded mock id=11。
    try:
        if api_user and api_user.get("id"):
            from platform_app.runtime import read_runtime
            from platform_app.db import connect
            rmeta = read_runtime(user_id=api_user["id"]) or {}
            sid = int(rmeta.get("save_id") or 0) or None
            if sid:
                with connect() as db:
                    row = db.execute(
                        "select id, title, updated_at from game_saves where id = %s and user_id = %s",
                        (sid, int(api_user["id"])),
                    ).fetchone()
                if row:
                    payload["save_id"] = int(row["id"])
                    payload["save_title"] = str(row["title"] or "")
                    if row.get("updated_at"):
                        payload["save_updated_at"] = row["updated_at"].isoformat() if hasattr(row["updated_at"], "isoformat") else str(row["updated_at"])
    except Exception:
        # 任何 DB 异常都不能让 /api/state 整个 500，缺字段前端有兜底
        pass
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


@app.get("/")
async def index() -> JSONResponse:
    """Backend root。前端由 frontend/ React 应用提供（Vite dev server 或静态部署）。"""
    return JSONResponse({
        "ok": True,
        "service": f"{APP_TITLE} RPG backend",
        "frontend": {
            "platform": "Platform.html (Vite dev: http://127.0.0.1:5173/Platform.html)",
            "game_console": "Game Console.html (Vite dev: http://127.0.0.1:5173/Game%20Console.html)",
        },
        "docs": "/docs",
    })


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
        # task 43：原 query 硬编码『柏林 图卢兹 娅赛兰 蛇信 蕾穆丽娜』+ retrieve_context
        # 没传 script_id → retrieval.py 退化到 is_default=True → 拉 MuMu .webnovel SQLite/
        # indexes JSON（角色卡/原文/摘要），让导入剧本的 /api/opening 也被柏林污染。
        # 修：query 按当前 state 动态构（player 位置 + world 时间 + 当前目标 + 首章 known_events），
        # retrieve_context 收 script_id —— 非默认剧本走 task 42 的 script-scoped 路径，
        # 不读任何 MuMu 私有源；默认剧本保留原硬编码 query 兼容性。
        script_id = _active_script_id(api_user)
        if script_id:
            world = state.data.get("world", {}) or {}
            player = state.data.get("player", {}) or {}
            memory = state.data.get("memory", {}) or {}
            events = world.get("known_events") or []
            query_parts = [
                str(player.get("current_location") or ""),
                str(world.get("time") or ""),
                str(memory.get("current_objective") or ""),
                *[str(e) for e in events[:2]],
            ]
            query = " ".join(p for p in query_parts if p).strip() or "开场"
        else:
            # 兼容旧无 script_id 路径（匿名/未绑定 save）：保留原 MuMu hint
            query = "柏林 图卢兹 娅赛兰 蛇信 蕾穆丽娜"
        ctx = retrieve_context(
            query,
            state=state,
            user_id=api_user["id"] if api_user else None,
            script_id=script_id,
        )
        state.set_last_retrieval(ctx)
        bundle = _build_turn_context(state, query, ctx, script_id=script_id)
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
    # task 31：前端历史上同时存在 {message:...} 和 {text:...} 两套契约。
    # 老的 Game Console.html 发 text，新的 game-app.jsx 也偶尔走 message。
    # 后端必须两边兼容，否则用户输入直接被 "空消息" error 吞掉。
    message = (body.get("message") or body.get("text") or "").strip()
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
            # task 27：/set / 时间跳跃等玩家指令必须先持久化，否则一旦上游 GM 504 / context_agent
            # 抛异常，整轮 try 跳到 except，_persist_chat_turn 永远跑不到 → /set 的状态修改丢失。
            # 把 directive_updates 应用 → 立刻持久化一个 runtime checkpoint → 发 `updates`
            # SSE 事件让 UI 也立刻反映；后续 GM 失败也保留这批硬改动。
            directive_updates = state.apply_player_directives(message_for_model)
            # task 77：如果是 /set + 用户开启 set_parser，让 LLM 子代理把自然语言
            # 拆成额外 ops（detect_set_directive 简单 path=value 之外的复杂关系/事实/变量）
            if (message_for_model.strip().startswith("/set") and
                    _is_set_parser_enabled(api_user)):
                try:
                    import set_parser as _set_parser
                    parser_ops = _set_parser.parse_set_directive(
                        set_text=message_for_model,
                        state_data=state.data,
                        user_id=int(api_user.get("id")) if api_user else None,
                        timeout_sec=15,
                    )
                    for op in parser_ops:
                        kind = (op.get("op") or "set").lower()
                        try:
                            if kind == "hypothesis":
                                txt = op.get("text") or op.get("value") or ""
                                if txt:
                                    mid = state.add_hypothesis(
                                        text=txt, source="user:/set:parser",
                                        time_label=op.get("time_label"),
                                        characters=op.get("characters"),
                                    )
                                    directive_updates.append(f"推测登记（/set 解析）：{mid}")
                            elif kind in ("set", "append", "overwrite"):
                                path = (op.get("path") or "").strip()
                                if path:
                                    spec = f"{path}={op.get('value', '')}"
                                    res = state.apply_state_write(
                                        spec, source="user:/set:parser",
                                        force=True,
                                        append=(kind == "append"),
                                        overwrite=(kind == "overwrite"),
                                    )
                                    directive_updates.append(f"/set 解析: {res}")
                        except Exception as op_exc:
                            print(f"[set_parser] op apply failed: {op_exc} for {op}")
                except Exception as exc:
                    print(f"[chat] set_parser failed: {exc}; 继续走简单 /set 路径")
                    try:
                        from datetime import datetime as _dt
                        audit = state.data.setdefault("permissions", {}).setdefault("audit_log", [])
                        audit.append({
                            "ts": _dt.now().isoformat(timespec="seconds"),
                            "kind": "set_parser_error",
                            "source": "set_parser",
                            "hint": f"/set 自然语言解析失败：{type(exc).__name__}: {str(exc)[:200]}",
                            "turn": state.data.get("turn", 0),
                        })
                        if len(audit) > 200:
                            state.data["permissions"]["audit_log"] = audit[-200:]
                    except Exception:
                        pass
            if directive_updates:
                _persist_runtime_checkpoint(state, api_user)
                yield _sse("status", _payload(api_user))
                yield _sse("updates", {"items": directive_updates, "stage": "pre_llm"})
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

            # task 80：clarifying_question routing —— curator 自评意图模糊时
            # 跳过主 GM，直接把封闭式问题 yield 给玩家。让 LLM 在不确定时主动
            # yield 而不是硬编。
            _curator_plan = agent_result.get("curator_plan", {}) or {}
            _confidence = float(_curator_plan.get("confidence") or 1.0)
            _clarify = (_curator_plan.get("clarifying_question") or "").strip()
            _confidence_threshold = _clarify_threshold(api_user)
            _route_to_clarify = bool(_clarify) or _confidence < _confidence_threshold
            if _route_to_clarify and _clarify:
                # 写 pending_question + audit_log，让玩家看到问题
                try:
                    state.add_pending_question(_clarify, source="curator:clarify")
                except Exception:
                    pass
                try:
                    from datetime import datetime as _dt
                    audit = state.data.setdefault("permissions", {}).setdefault("audit_log", [])
                    audit.append({
                        "ts": _dt.now().isoformat(timespec="seconds"),
                        "kind": "clarify_yield",
                        "source": "curator",
                        "hint": f"confidence={_confidence:.2f}；curator 主动询问：{_clarify[:160]}",
                        "turn": state.data.get("turn", 0),
                    })
                    if len(audit) > 200:
                        state.data["permissions"]["audit_log"] = audit[-200:]
                except Exception:
                    pass
                # 把问题作为 GM 正文输出，让前端 token 流照常显示
                _q_text = f"【需要先确认】{_clarify}"
                yield _sse("token", {"text": _q_text})
                # 持久化（让 chat 历史里也有这条 yield）
                try:
                    _persist_chat_turn(
                        api_user, state, message_for_model, _q_text,
                        persist_user_id=persist_user_id, active_save_id=active_save_id,
                    )
                except Exception:
                    pass
                _mark_context_run(
                    context_run_id, "done",
                    duration_ms=int((time.time() - _chat_start_time) * 1000),
                )
                yield _sse("status", _payload(api_user))
                yield _sse("done", {"status": _payload(api_user), "interrupted": False, "clarify": True})
                return

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

            # task 62：可选 GM-extractor 第二步。
            # 用户在偏好里开 extractor.enabled = true 时，把 GM 叙事 + 当前 state 喂给
            # 便宜模型（默认 gemini-3.5-flash）抽出 JSON ops 追加到 response 末尾，
            # 让 apply_structured_updates 统一处理。错误回灌（task 60）+ 闸门 (task 54)
            # 都自动覆盖到 extractor ops。
            extractor_active = False  # task 69：决定是否跳过 state.py 隐式 regex 兜底
            try:
                if _is_extractor_enabled(api_user) and response.strip():
                    extractor_active = True
                    import extractor as _extractor
                    extractor_ops = _extractor.extract_state_ops(
                        narrative_text=response,
                        state_data=state.data,
                        user_id=int(api_user.get("id")) if api_user else None,
                        timeout_sec=15,
                    )
                    if extractor_ops:
                        # 拼成 ```json fence 让 apply_structured_updates 走 JSON 路径
                        # （和 LLM 自己写的【】协议结果合并）
                        response_with_ops = response + "\n\n```json\n" + json.dumps(extractor_ops, ensure_ascii=False) + "\n```"
                    else:
                        response_with_ops = response
                else:
                    response_with_ops = response
            except Exception as exc:
                # task 65：失败不再只 console.print。写 audit_log kind=extractor_error
                # 让前端 Audit Log 面板能告诉用户「第二步挂了，state ops 这轮没抽到」。
                print(f"[chat] extractor pipeline failed: {exc}; falling back to single-step")
                try:
                    from datetime import datetime as _dt
                    audit = state.data.setdefault("permissions", {}).setdefault("audit_log", [])
                    audit.append({
                        "ts": _dt.now().isoformat(timespec="seconds"),
                        "kind": "extractor_error",
                        "source": "extractor",
                        "hint": f"GM 第二步失败：{type(exc).__name__}: {str(exc)[:200]}",
                        "turn": state.data.get("turn", 0),
                    })
                    if len(audit) > 200:
                        state.data["permissions"]["audit_log"] = audit[-200:]
                except Exception:
                    pass
                response_with_ops = response

            # task 69：extractor 开启时让 state.py 跳过 regex 兜底，避免和 extractor 双写
            updates = directive_updates + state.apply_structured_updates(
                response_with_ops, skip_regex_fallback=extractor_active,
            )
            # task 81：acceptance 自动验证。curator 在 demand_ledger 里列了
            # 本轮成功的验收条件，跑一个 cheap 字面检查看 GM 输出（response
            # + updates）是否满足。未满足 → audit_log kind=acceptance_unmet。
            # task 84：模式可选 rule / llm / hybrid（preferences 配置）。
            try:
                _curator_plan_for_check = (agent_result or {}).get("curator_plan", {}) or {}
                _acceptance = _curator_plan_for_check.get("acceptance") or []
                if _acceptance and response.strip():
                    _acc_mode = _acceptance_verifier_mode(api_user)
                    _acc_user_id = int(api_user.get("id")) if api_user and api_user.get("id") else None
                    unmet = _verify_acceptance(
                        _acceptance, response, updates,
                        mode=_acc_mode, user_id=_acc_user_id,
                    )
                    if unmet:
                        from datetime import datetime as _dt
                        audit = state.data.setdefault("permissions", {}).setdefault("audit_log", [])
                        for item in unmet[:5]:
                            audit.append({
                                "ts": _dt.now().isoformat(timespec="seconds"),
                                "kind": "acceptance_unmet",
                                "source": "curator:acceptance",
                                "hint": f"未通过验收：{item[:160]}",
                                "turn": state.data.get("turn", 0),
                            })
                        if len(audit) > 200:
                            state.data["permissions"]["audit_log"] = audit[-200:]
                        yield _sse("agent", {
                            "phase": "acceptance_check",
                            "message": f"本轮 GM 输出有 {len(unmet)} 条 acceptance 未通过；已记 audit_log",
                            "status": "warning",
                            "elapsed_ms": 0,
                            "unmet": unmet[:5],
                        })
            except Exception as _acc_exc:
                print(f"[acceptance] check failed: {_acc_exc}")
            _persist_chat_turn(
                api_user, state, message_for_model, response,  # 存档时存"纯叙事"不含 extractor JSON
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
    """审批一条待写入。前端发 {id, action} 或 {index, decision}（兼容老 contract）。

    P0 修复（task #53）：之前后端只读 index+decision，前端发 id+action →
    /set 后端 body.get("index", -1) = -1 → "待审写入不存在" → 整个审批流死。
    现在按 id 优先（稳定），index/decision 作 fallback。
    """
    api_user = _require_api_user(request)
    body = await request.json()
    state = _ensure_loaded(api_user)
    item_id = body.get("id")
    raw_index = body.get("index")
    index = int(raw_index) if raw_index is not None else None
    decision = str(body.get("action") or body.get("decision") or "").lower()
    if decision == "approve":
        result = state.approve_pending_write(index=index, id=item_id)
    elif decision == "reject":
        result = state.reject_pending_write(index=index, id=item_id)
    else:
        return JSONResponse({"ok": False, "error": "缺少 action/decision（approve|reject）"}, status_code=400)
    state.data["memory"]["last_structured_updates"] = [result] + state.data["memory"].get("last_structured_updates", [])[:11]
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "result": result, "state": _payload(api_user)})


@app.post("/api/questions/clear")
async def api_question_clear(request: Request) -> JSONResponse:
    """回答（或跳过）一条 GM 询问。{id, choice?} 或 {index, choice?}。"""
    api_user = _require_api_user(request)
    body = await request.json()
    state = _ensure_loaded(api_user)
    item_id = body.get("id")
    raw_index = body.get("index")
    index = int(raw_index) if raw_index is not None else None
    choice = body.get("choice")
    popped = state.clear_pending_question(index=index, id=item_id, choice=choice)
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "cleared": bool(popped), "state": _payload(api_user)})


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
    model_payload = body.get("model") if isinstance(body.get("model"), dict) else {
        k: v for k, v in body.items() if k != "api_id"
    }
    catalog = upsert_model(body.get("api_id", ""), model_payload)
    return JSONResponse({"ok": True, "models": catalog, "selected": selected_model(catalog)})


@app.post("/api/models/model/delete")
async def api_models_delete_model(request: Request) -> JSONResponse:
    _require_api_user(request, admin=True)
    body = await request.json()
    catalog = delete_model(body.get("api_id", ""), body.get("model_id") or body.get("real_name", ""))
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
    """MCP 运行时状态 + per-user 调用审计。
    - 普通用户：拿不到 stderr（可能含 token/路径），audit_log 只看自己的
    - admin：full stderr + 全部用户的 audit_log
    """
    api_user = _require_api_user(request)
    is_admin = bool(api_user and api_user.get("role") == "admin")
    import mcp_broker
    payload = mcp_broker.status()
    if not is_admin:
        for entry in payload.get("running") or []:
            entry.pop("last_stderr", None)
    # P0 #3：附 audit_log，让管理员能查跨用户 MCP 调用
    try:
        audit = mcp_broker.get_audit_log(
            user_id=None if is_admin else (api_user["id"] if api_user else None),
            limit=200,
        )
        payload["audit_log"] = audit
    except Exception:
        payload["audit_log"] = []
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
        user_id=api_user["id"] if api_user else None,
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


# ── 5E-compatible 规则模组 / RulesEngine 接口 ─────────────────────
# 内部 ruleset id "dnd5e"，对外文案使用 "5E compatible / 五版规则兼容"。
# 不引入任何官方 Dungeons & Dragons 商标、Forgotten Realms 设定或非 SRD IP。
from rules_bridge import (
    start_module as _rb_start_module,
    enter_room as _rb_enter_room,
    perform_skill_check as _rb_skill_check,
    perform_saving_throw as _rb_saving_throw,
    trap_check as _rb_trap_check,
    start_encounter_by_id as _rb_start_encounter,
    player_attack as _rb_player_attack,
    enemy_attack as _rb_enemy_attack,
    advance_turn as _rb_advance_turn,
    short_rest as _rb_short_rest,
    suggest_rule_actions as _rb_suggest_rule_actions,
)
import modules as _rules_module_registry


def _rules_payload(state: GameState) -> dict:
    """前端 UI 需要的精简切片：角色卡 + 场景 + 战斗 + 骰子日志 + 模组元信息。"""
    return {
        "ruleset": state.data.get("ruleset", {}),
        "player_character": state.data.get("player_character", {}),
        "scene": state.data.get("scene", {}),
        "encounter": state.data.get("encounter", {}),
        "dice_log": list(state.data.get("dice_log", []))[-30:],
    }


@app.get("/api/rules/modules")
async def api_rules_modules(request: Request) -> JSONResponse:
    """列出可用的 5E-compatible 冒险模组。"""
    _require_api_user(request)
    return JSONResponse({"ok": True, "modules": _rules_module_registry.list_modules()})


@app.post("/api/rules/module/start")
async def api_rules_module_start(request: Request) -> JSONResponse:
    """开启一个原创冒险模组（e.g. ash_mine）。"""
    api_user = _require_api_user(request)
    body = await request.json()
    module_id = str(body.get("module_id") or "ash_mine").strip()
    character_overrides = body.get("character") or None

    state = _ensure_loaded(api_user)
    res = _rb_start_module(state, module_id, character_overrides=character_overrides)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "start_module 失败"))
    # opening 已由 rules_bridge.start_module 注入到 history（避免重复 append）
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "rules": _rules_payload(state), "opening": res.get("opening") or "", "state": _payload(api_user)})


@app.get("/api/rules/scene")
async def api_rules_scene(request: Request) -> JSONResponse:
    """返回当前 scene / player_character / encounter / dice_log 快照。"""
    api_user = _require_api_user(request)
    state = _ensure_loaded(api_user)
    return JSONResponse({"ok": True, "rules": _rules_payload(state)})


@app.post("/api/rules/move")
async def api_rules_move(request: Request) -> JSONResponse:
    api_user = _require_api_user(request)
    body = await request.json()
    location_id = str(body.get("to") or "").strip()
    if not location_id:
        raise HTTPException(status_code=400, detail="缺少 to")
    state = _ensure_loaded(api_user)
    res = _rb_enter_room(state, location_id)
    if not res.get("ok"):
        return JSONResponse({"ok": False, "error": res.get("error")}, status_code=400)
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "rules": _rules_payload(state), "room": res.get("room")})


@app.post("/api/rules/action")
async def api_rules_action(request: Request) -> JSONResponse:
    """通用规则动作执行入口。根据 body.kind 路由到具体规则函数。"""
    api_user = _require_api_user(request)
    body = await request.json()
    kind = str(body.get("kind") or "").strip()
    state = _ensure_loaded(api_user)

    seed = body.get("seed")
    seed = int(seed) if isinstance(seed, (int, float, str)) and str(seed).lstrip("-").isdigit() else None

    if kind == "skill_check":
        skill = str(body.get("skill") or "")
        dc = int(body.get("dc", body.get("dc_hint", 12)))
        result = _rb_skill_check(
            state, skill=skill, dc=dc,
            advantage=bool(body.get("advantage")),
            disadvantage=bool(body.get("disadvantage")),
            seed=seed,
            reason=str(body.get("reason") or ""),
            sets_flag=body.get("sets_flag"),
        )
        out: dict = {"ok": True, "result": result}
    elif kind == "saving_throw":
        ability = str(body.get("ability") or "")
        dc = int(body.get("dc", body.get("dc_hint", 12)))
        result = _rb_saving_throw(
            state, ability=ability, dc=dc,
            advantage=bool(body.get("advantage")),
            disadvantage=bool(body.get("disadvantage")),
            seed=seed,
            reason=str(body.get("reason") or ""),
            fail_damage_expr=body.get("fail_damage_expr") or body.get("fail_damage"),
            fail_condition=body.get("fail_condition"),
        )
        out = {"ok": True, "result": result}
    elif kind == "trap_check":
        room_id = str(body.get("room_id") or state.data.get("scene", {}).get("location_id") or "")
        trap_id = str(body.get("trap_id") or "")
        if not room_id or not trap_id:
            raise HTTPException(status_code=400, detail="缺少 room_id 或 trap_id")
        out = _rb_trap_check(state, room_id=room_id, trap_id=trap_id, seed=seed)
        if not out.get("ok"):
            return JSONResponse(out, status_code=400)
    elif kind == "attack":
        target_id = str(body.get("target") or body.get("target_id") or "")
        weapon_id = str(body.get("weapon") or body.get("weapon_id") or "shortsword")
        out = _rb_player_attack(
            state, target_id=target_id, weapon_id=weapon_id,
            advantage=bool(body.get("advantage")),
            disadvantage=bool(body.get("disadvantage")),
            seed=seed,
        )
        if not out.get("ok"):
            return JSONResponse(out, status_code=400)
    elif kind == "short_rest":
        out = _rb_short_rest(state, seed=seed)
        if not out.get("ok"):
            return JSONResponse(out, status_code=400)
    elif kind == "move":
        loc = str(body.get("to") or body.get("target") or "")
        out = _rb_enter_room(state, loc)
        if not out.get("ok"):
            return JSONResponse(out, status_code=400)
    else:
        raise HTTPException(status_code=400, detail=f"未支持的 kind: {kind}")

    state.save()
    _persist_runtime_checkpoint(state, api_user)
    out["rules"] = _rules_payload(state)
    return JSONResponse(out)


@app.post("/api/rules/encounter/start")
async def api_rules_encounter_start(request: Request) -> JSONResponse:
    api_user = _require_api_user(request)
    body = await request.json()
    encounter_id = str(body.get("encounter_id") or "").strip()
    if not encounter_id:
        raise HTTPException(status_code=400, detail="缺少 encounter_id")
    seed = body.get("seed")
    seed = int(seed) if isinstance(seed, (int, float, str)) and str(seed).lstrip("-").isdigit() else None
    state = _ensure_loaded(api_user)
    res = _rb_start_encounter(state, encounter_id, seed=seed)
    if not res.get("ok"):
        return JSONResponse(res, status_code=400)
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "rules": _rules_payload(state), "encounter": res.get("encounter")})


@app.post("/api/rules/encounter/next")
async def api_rules_encounter_next(request: Request) -> JSONResponse:
    api_user = _require_api_user(request)
    state = _ensure_loaded(api_user)
    res = _rb_advance_turn(state)
    if not res.get("ok"):
        return JSONResponse(res, status_code=400)
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "rules": _rules_payload(state), "encounter": res.get("encounter")})


@app.post("/api/rules/encounter/enemy")
async def api_rules_encounter_enemy(request: Request) -> JSONResponse:
    """敌方回合：让指定敌人对玩家发动一次攻击（用于回合制 demo）。"""
    api_user = _require_api_user(request)
    body = await request.json()
    attacker_id = str(body.get("attacker_id") or "").strip()
    target_id = str(body.get("target_id") or "player").strip()
    seed = body.get("seed")
    seed = int(seed) if isinstance(seed, (int, float, str)) and str(seed).lstrip("-").isdigit() else None
    state = _ensure_loaded(api_user)
    res = _rb_enemy_attack(state, attacker_id=attacker_id, target_id=target_id, seed=seed)
    if not res.get("ok"):
        return JSONResponse(res, status_code=400)
    state.save()
    _persist_runtime_checkpoint(state, api_user)
    return JSONResponse({"ok": True, "rules": _rules_payload(state), "result": res.get("result"), "encounter": res.get("encounter")})


@app.post("/api/rules/suggest")
async def api_rules_suggest(request: Request) -> JSONResponse:
    """从玩家自由文本输入推断候选规则动作（轻量本地匹配，用于前端候选按钮）。"""
    api_user = _require_api_user(request)
    body = await request.json()
    text = str(body.get("text") or "")
    state = _ensure_loaded(api_user)
    return JSONResponse({"ok": True, "actions": _rb_suggest_rule_actions(text, state)})




if __name__ == "__main__":
    import uvicorn

    print(f"[API] {APP_TITLE} RPG backend: http://{HOST}:{PORT}")
    print(f"[UI]  React frontend served separately via Vite (默认 http://127.0.0.1:5173/Platform.html)")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
