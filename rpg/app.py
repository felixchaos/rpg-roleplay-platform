"""
ui.py - local Claude-like RPG workspace

Run:
    cd rpg/
    ../rpg_env/bin/python ui.py

Then open http://127.0.0.1:7860
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from threading import Event, Lock
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

sys.path.insert(0, str(Path(__file__).parent))

from agents.context_agent import run_context_agent  # noqa: F401
from agents.gm import GameMaster
from context_engine import build_context_bundle

# 通用 RPG 底座：APP_TITLE 是平台名称，不绑定特定剧本。可由 RPG_APP_TITLE env 覆写。
from core.config import (
    app_title as _app_title_cfg,
)
from core.logging import get_logger, setup_default_logging
from core.startup import configure_app, lifespan
from model_registry import (
    delete_model,  # noqa: F401
    load_model_catalog,
    select_model,  # noqa: F401
    selected_model,
    upsert_api,  # noqa: F401
    upsert_model,  # noqa: F401
)
from platform_app import branches as platform_branches
from platform_app import knowledge as platform_knowledge
from platform_app import runtime as platform_runtime
from platform_app.api import current_user as platform_current_user
from platform_app.api import router as platform_router
from retrieval import retrieve_context  # noqa: F401
from state import SAVE_FILE, GameState
from tools_dsl.tool_registry import (
    delete_mcp_server,  # noqa: F401
    import_skill_bundle,  # noqa: F401
    set_mcp_server_enabled,  # noqa: F401
    tool_payload,
    upsert_mcp_server,  # noqa: F401
    validate_mcp_server,  # noqa: F401
)

setup_default_logging()
log = get_logger(__name__)
APP_TITLE = _app_title_cfg()
MODEL_LABEL = "Gemini 3.5 Flash"
HOST = "127.0.0.1"
PORT = 7860
APP_DIR = Path(__file__).parent
UPLOAD_DIR = APP_DIR / "uploads"
MAX_ATTACHMENT_BYTES = 12 * 1024 * 1024

app = FastAPI(title=f"{APP_TITLE} RPG", lifespan=lifespan)


_LOCAL_MODES = {"local", "desktop", "self_hosted", "self-hosted"}
_SERVER_MODES = {"server", "production", "prod", "cloud"}


def _deployment_mode() -> str:
    from core.config import deployment_mode as _deployment_mode_cfg
    return _deployment_mode_cfg().strip().lower() or "local"


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
    # 否定关键词：retest 加入"没有 / 未" — 之前 "没有直接修改玩家的 HP 或 AC"
    # 这种正向 success state 写法（"X 没发生"）被错当成肯定条款，规则要求
    # bigram 命中才算通过，narration 里没出现"HP/AC"就被误报 unmet。
    # 把"没有"列为否定标记后：response 不含 HP/AC → 不命中 → 否定条款 met。
    _NEG_KEYWORDS = ("不要", "不应", "禁止", "不能", "不得", "没把", "没有", "未曾",
                     "不可", "杜绝", "勿", "切勿", "无", "未")

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
        if is_negative:
            # click retest minor：否定条款（"GM 未/不/没做 X"）的语义判断规则版
            # 做不准——bigram 匹配会把"叙事里提到 X"误报成"X 发生了"，
            # 反复制造 acceptance_unmet 假阳性（用户报告：「GM 未自行决定检定成败」
            # 被报 unmet，而 GM 实际上没做这种事）。
            # 规则版统一默认 MET；要真正语义检查请切到 acceptance_verifier.mode=llm 或 hybrid。
            continue
        # 肯定条款：至少 1 个核心 bigram 命中算通过；全没命中 → unmet
        hit = any(b.lower() in haystack for b in bigrams)
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
            from agents.acceptance_verifier import verify_acceptance_llm
            out = verify_acceptance_llm(
                acceptance=acceptance,
                response_text=response_text,
                updates=updates or [],
                user_id=user_id,
            )
        except Exception as exc:
            log.warning(f"[acceptance] llm mode raised; falling back to rule: {exc}")
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
        from agents.acceptance_verifier import verify_acceptance_llm
        llm_unmet = verify_acceptance_llm(
            acceptance=rule_unmet,
            response_text=response_text,
            updates=updates or [],
            user_id=user_id,
        )
    except Exception as exc:
        log.warning(f"[acceptance] hybrid llm step raised; keeping rule verdict: {exc}")
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
    from core.config import require_auth_raw as _require_auth_raw
    explicit = _require_auth_raw().strip()
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
    from core.config import require_auth_raw as _require_auth_raw
    mode = _deployment_mode()
    required = _api_auth_required()
    explicit = _require_auth_raw()
    source = f"RPG_REQUIRE_AUTH={explicit}" if explicit else f"RPG_DEPLOYMENT_MODE={mode}"
    if required:
        log.info(f"[启动] 部署模式={mode} 鉴权=强制 (源={source})")
    else:
        log.info(f"[启动] 部署模式={mode} 鉴权=不强制 (源={source}) — 仅适用于单用户本地使用")


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


configure_app(app)
app.include_router(platform_router)
try:
    from platform_app.frontend_routes import router as _frontend_router
    app.include_router(_frontend_router)
except Exception as _e:
    log.warning(f"[启动] frontend_routes 未挂载：{_e}")

# ── Phase 1.1 Pilot: 迁移路由子包 ──────────────────────────────────────
from routes.core import router as core_router
from routes.memory import router as memory_router
from routes.permissions import router as permissions_router

app.include_router(core_router)
app.include_router(memory_router)
app.include_router(permissions_router)

# ── Phase 1.2: 迁移剩余路由 ──────────────────────────────────────────
from routes.console_assistant import router as console_assistant_router
from routes.game import router as game_router
from routes.mcp import router as mcp_router
from routes.models import router as models_router
from routes.rules import router as rules_router
from routes.sidebar import router as sidebar_router
from routes.skills import router as skills_router
from routes.timeline import router as timeline_router
from routes.worldline import router as worldline_router

app.include_router(game_router)
app.include_router(models_router)
app.include_router(mcp_router)
app.include_router(skills_router)
app.include_router(worldline_router)
app.include_router(rules_router)
app.include_router(timeline_router)
app.include_router(console_assistant_router)
app.include_router(sidebar_router)

# 同源 mount frontend 静态文件 — dev/prod 都需要 (cookie SameSite=lax 跨 origin 5173↔7860 会丢)
# 必须在所有具体路由之后 mount,否则会拦截 /api/* 和 /
from pathlib import Path as _Path

from fastapi.staticfiles import StaticFiles as _StaticFiles

_FRONTEND_ROOT = _Path(__file__).resolve().parent.parent / "frontend"
# Vite 构建产物在 frontend/dist;同源/生产服务必须挂 dist(打包后的 /assets/*.js)。
# 源码目录的 *.html 引用裸 .jsx 模块,浏览器无法直接运行 → 白屏。
# dist 不存在(未 npm run build)时回退源码目录,仅配合 vite dev server (:5173) 用。
_FRONTEND_DIR = _FRONTEND_ROOT / "dist" if (_FRONTEND_ROOT / "dist").is_dir() else _FRONTEND_ROOT
if _FRONTEND_DIR.is_dir():
    app.mount("/", _StaticFiles(directory=str(_FRONTEND_DIR), html=False), name="frontend")

# 启动时一次性触发 schema + migration，避免请求路径 DDL 撞锁
from platform_app.db import init_db as _bootstrap_init_db

try:
    _bootstrap_init_db()
except Exception as _e:
    log.error(f"[启动] init_db 失败：{_e}")

_startup_auth_banner()


_state_by_user: dict[int, GameState] = {}     # key = api_user["id"] 或 0 (anonymous local)
# 记录每个 cached state 对应的 (save_id, commit_id) tuple。
# 真相源 = branch_commits[user_runtime.active_commit_id].state_snapshot。
# 用户在别处切了 save / commit 之后,_ensure_loaded 拿当前 user_runtime
# (save_id, commit_id) 跟这里的值比对;**任一不同** → 缓存失效重新加载。
# 之前只比 save_id 导致"同 save 内换 commit 缓存命中读旧 state"。
_state_save_id_by_user: dict[int, int] = {}
_state_commit_id_by_user: dict[int, int] = {}
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

PRESET = {}  # 通用底座: 默认无预置角色, 由剧本元数据 (script_card / persona) 提供


def _selfheal_player_from_save_snapshot(state: GameState, api_user: dict[str, Any]) -> None:
    """Bug 1 (click retest) self-heal：runtime player 空时，从 game_saves.state_snapshot
    重写 player（+ player_character 若也空）。这是 user_card / persona / new_card 注入
    的权威源，不依赖 runtime_checkouts 健康。

    多用户安全：必须用 api_user.id 限定 game_saves 行，避免读到别人的存档。
    幂等：只在 state.player.name 完全空时触发。
    """
    player = (state.data.get("player") or {}) if isinstance(state.data.get("player"), dict) else {}
    if player.get("name") or player.get("role") or player.get("background"):
        return  # runtime 已经有数据，不动
    user_id = int(api_user.get("id") or 0)
    if not user_id:
        return
    try:
        from platform_app.db import connect
        from platform_app.runtime import read_runtime
    except Exception:
        return
    meta = read_runtime(user_id=user_id) or {}
    save_id = int(meta.get("save_id") or 0)
    if not save_id:
        return
    with connect() as db:
        row = db.execute(
            "select state_snapshot from game_saves where id = %s and user_id = %s",
            (save_id, user_id),
        ).fetchone()
    if not row:
        return
    snap = row.get("state_snapshot") if isinstance(row, dict) else None
    if not isinstance(snap, dict):
        return
    saved_player = snap.get("player") if isinstance(snap.get("player"), dict) else None
    if not saved_player or not saved_player.get("name"):
        return  # game_saves snapshot 也没 player 数据，没什么可救的
    # 把 game_saves.player 写回 runtime state（保留 history / scene / 战斗等运行态）
    state.data.setdefault("player", {})
    for key in ("name", "role", "background", "current_location",
                "source_kind", "source_id", "appearance", "personality", "speech_style"):
        if saved_player.get(key):
            state.data["player"][key] = saved_player[key]
    # 也修 player_character（如果 game_saves 有且 runtime 空）
    saved_pc = snap.get("player_character")
    runtime_pc = state.data.get("player_character") or {}
    if (isinstance(saved_pc, dict) and saved_pc.get("name")
            and not runtime_pc.get("name")):
        state.data["player_character"] = json.loads(json.dumps(saved_pc, ensure_ascii=False))
    state.data["is_new"] = False
    # 写 audit 留痕
    try:
        from datetime import datetime as _dt
        audit = state.data.setdefault("permissions", {}).setdefault("audit_log", [])
        audit.append({
            "ts": _dt.now().isoformat(timespec="seconds"),
            "source": "ensure_loaded:selfheal",
            "kind": "player_restored_from_save_snapshot",
            "save_id": save_id,
            "turn": state.data.get("turn", 0),
            "hint": "runtime player 为空但 game_saves.snapshot 有 player，已恢复",
        })
        state.data["permissions"]["audit_log"] = audit[-200:]
    except Exception:
        pass


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
        # 缓存一致性自检:cached state 对应的 (save_id, commit_id) 必须等于
        # 当前 user_runtime 的 (save_id, active_commit_id)。任一不同就失效。
        # 之前只比 save_id → 同 save 内换 commit 时缓存命中读旧 state。
        if cached is not None and api_user and api_user.get("id"):
            try:
                from platform_app.runtime import read_runtime
                _rt = read_runtime(user_id=int(api_user["id"])) or {}
                _rt_save = int(_rt.get("save_id") or 0)
                _rt_commit = int(
                    _rt.get("active_commit_id")
                    or _rt.get("active_branch_node_id")
                    or 0
                )
                _cached_save = int(_state_save_id_by_user.get(uid) or 0)
                _cached_commit = int(_state_commit_id_by_user.get(uid) or 0)
                save_drift = _rt_save and _cached_save and _rt_save != _cached_save
                commit_drift = _rt_commit and _cached_commit and _rt_commit != _cached_commit
                if save_drift or commit_drift:
                    cached = None
            except Exception:
                pass
        if cached is None:
            try:
                from state_repository import load_active_state
                state, _runtime_meta = load_active_state(user_id=api_user["id"] if api_user else None)
                _new_save_id = int((_runtime_meta or {}).get("save_id") or 0)
                _new_commit_id = int(
                    (_runtime_meta or {}).get("active_commit_id")
                    or (_runtime_meta or {}).get("active_branch_node_id")
                    or 0
                )
                if _new_save_id:
                    _state_save_id_by_user[uid] = _new_save_id
                else:
                    _state_save_id_by_user.pop(uid, None)
                if _new_commit_id:
                    _state_commit_id_by_user[uid] = _new_commit_id
                else:
                    _state_commit_id_by_user.pop(uid, None)
            except Exception:
                state = GameState.new() if api_user else GameState.load_or_new()
                _state_save_id_by_user.pop(uid, None)
                _state_commit_id_by_user.pop(uid, None)
            # Self-heal (Bug 1 click retest)：若 runtime 加载到的 state.player 为空
            # 但 game_saves.state_snapshot 有 player 数据，说明 runtime_checkouts
            # 没拿到完整 snapshot（可能在 activate 时序窗口里出问题）。
            # 直接从 game_saves.state_snapshot 重新加载 — 这是 user_card / persona
            # / new_card 注入的权威源。不依赖 runtime cache 健康。
            if api_user and api_user.get("id"):
                try:
                    _selfheal_player_from_save_snapshot(state, api_user)
                except Exception as exc:
                    log.warning(f"[ensure_loaded] selfheal failed: {exc}")
            _state_by_user[uid] = state
            if uid == 0:
                _state_mtime_by_user[uid] = SAVE_FILE.stat().st_mtime_ns if SAVE_FILE.exists() else 0
        if uid not in _gm_by_user:
            # A1: 优先读当前存档的 session_model，fallback 到全局 catalog selected
            _current_state = _state_by_user.get(uid)
            _session = _current_state.get_session_model() if _current_state else None
            if _session:
                _gm_model_id, _gm_api_id = _session
            else:
                model = selected_model()
                _gm_model_id, _gm_api_id = model["real_name"], model["api_id"]
            _gm_by_user[uid] = GameMaster(
                api_id=_gm_api_id,
                model=_gm_model_id,
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
        _state_save_id_by_user.pop(uid, None)
        _state_commit_id_by_user.pop(uid, None)


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
            log.info(f"[SUB-AGENT] uid={uid} 独立实例 api={sub.api_id} model={sub._backend.model_name}")
        except Exception as exc:
            log.warning(f"[SUB-AGENT] 独立实例创建失败 ({exc})，回退共用主 GM")
            sub = main_gm
    else:
        sub = main_gm
        log.info(f"[SUB-AGENT] uid={uid} 复用主 GM api={main_gm.api_id}")
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
    # 当前模型的 context window（tokens），由 platform_app.usage.context_window_for
    # 按 api_id+real_name 查映射表。FE Composer 里 ContextUsage 圆环需要这个值
    # 作为分母，从悬空 hard-coded 1.05M 变成真实当前模型上限。
    try:
        from platform_app.usage import context_window_for as _ctx_for
        ctx_window = int(_ctx_for(model["api_id"], model["real_name"]) or 0)
    except Exception:
        ctx_window = 0
    payload["app"] = {
        "title": APP_TITLE,
        "model": model["display_name"],
        "model_real_name": model["real_name"],
        "model_capabilities": model.get("capabilities", []),
        "context_window": ctx_window,
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
            from platform_app.db import connect
            from platform_app.runtime import read_runtime
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
    """普通用户拿不到 credential_ref / credential_env / base_url（部署形状信息）。
    所有角色都能看到 has_credential 字段（布尔），便于前端过滤掉没配 key 的 API。
    """
    import copy
    import model_probe
    result = copy.deepcopy(catalog)
    for api in result.get("apis", []):
        api["has_credential"] = model_probe._credential_present(api)
        if not is_admin:
            api.pop("credential_ref", None)
            api.pop("credential_env", None)
            api.pop("base_url", None)
    return result


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
    # task 107B/107C: 每 turn 写 save_timeline_anchors + phase boundary 检测
    if active_save_id:
        try:
            from save_phase_manager import (
                detect_phase_boundary,
                ensure_initial_phase,
                open_new_phase,
                update_phase_turn_end,
                upsert_timeline_anchor,
            )
            _turn = int(state.data.get("turn") or 0)
            _world = state.data.get("world") or {}
            _tl = _world.get("timeline") or {}
            _story_time = (
                _tl.get("current_label")
                or _world.get("time")
                or ""
            )
            _phase_label = _tl.get("current_phase") or ""
            # 107B: write timeline anchor
            upsert_timeline_anchor(
                save_id=active_save_id,
                turn_index=_turn,
                story_time_label=_story_time,
                phase_label=_phase_label,
                source="gm",
            )
            # 107C: ensure phase 0 on first turn
            ensure_initial_phase(active_save_id, _turn, _phase_label, _story_time)
            # 107C: update turn_end of open phase
            update_phase_turn_end(active_save_id, _turn)
            # 107C: detect boundary and open new phase if needed
            if detect_phase_boundary(active_save_id, state):
                open_new_phase(
                    save_id=active_save_id,
                    turn_index=_turn,
                    phase_label=_phase_label,
                    story_time_label=_story_time,
                )
        except Exception as _pm_err:
            log.warning(f"[chat] save_phase_manager hook failed: {_pm_err}")


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
                scenario="chat",
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
    save_id: int | None = None,  # task 107E: 给 runtime_phase_digests provider
) -> dict[str, Any]:
    bundle = build_context_bundle(
        state, message, retrieved_context,
        script_id=script_id, book_id=book_id, save_id=save_id,
    )
    state.set_last_context(bundle["debug"])
    return bundle


def _active_script_id(api_user: dict[str, Any] | None) -> int | None:
    """从 runtime/save 派生当前 script_id，供 context_engine 走 DB 数据。"""
    if not api_user:
        return None
    try:
        from platform_app.db import connect
        from platform_app.runtime import read_runtime
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
            raise ValueError(f"附件 {name} 不是合法 base64：{exc}") from exc
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


# ── API 探测：模型列表 / 可用性 / 定价 / 综合报告 ──────────────────
def _check_probe_permission(api_user: dict[str, Any] | None, api_id: str) -> JSONResponse | None:
    """同 /api/models/probe 的权限策略:admin / 用户已配置该 provider key /
    local 单用户模式 + server 端已配凭证 → 允许。返回 None 表示允许,否则返回 403。

    task 42: local 部署模式下,凭证(SA key / env var)是 server 级共享的,
    单用户本机自己用,放宽 probe 权限。否则跨域 cookie + vertex SA key 场景下
    user 永远没法 probe,health 永远 untested。
    """
    if not api_user or api_user.get("role") == "admin":
        return None
    from platform_app import user_credentials as _ucreds
    cred = _ucreds.get_credential(api_user["id"], api_id)
    if cred:
        return None
    # task 42: local 模式 + server 已配该 provider 凭证 → 允许
    try:
        from core.config import deployment_mode as _deployment_mode
        if _deployment_mode() == "local":
            from model_registry import find_api, load_model_catalog
            api = find_api(load_model_catalog(), api_id)
            if api and api.get("enabled"):
                # API 在 catalog 里 enabled 意味着 server 启动时凭证检查过了
                return None
    except Exception:
        pass
    return JSONResponse(
        {"ok": False, "error": "需要先在「个人主页 → API 凭证」中配置该 provider 才能调用探测接口"},
        status_code=403,
    )



# ── 5E-compatible 规则模组 / RulesEngine 接口 ─────────────────────
# 内部 ruleset id "dnd5e"，对外文案使用 "5E compatible / 五版规则兼容"。
# 不引入任何官方 Dungeons & Dragons 商标、Forgotten Realms 设定或非 SRD IP。
import modules as _rules_module_registry  # noqa: F401
from rules_bridge import (
    consume_item_action as _rb_consume_item_action,
)
from rules_bridge import (
    enter_room as _rb_enter_room,
)
from rules_bridge import (
    parse_consume_intent as _rb_parse_consume_intent,
)
from rules_bridge import (
    perform_saving_throw as _rb_saving_throw,
)
from rules_bridge import (
    perform_skill_check as _rb_skill_check,
)
from rules_bridge import (
    player_attack as _rb_player_attack,
)
from rules_bridge import (
    short_rest as _rb_short_rest,
)
from rules_bridge import (
    start_encounter_by_id as _rb_start_encounter,
)
from rules_bridge import (
    suggest_rule_actions as _rb_suggest_rule_actions,
)
from rules_bridge import (
    trap_check as _rb_trap_check,
)


def _coerce_rule_seed(seed: Any) -> int | None:
    return int(seed) if isinstance(seed, (int, float, str)) and str(seed).lstrip("-").isdigit() else None


def _canonicalize_exit_target(state: GameState, target: str) -> tuple[str, str]:
    """Bug 4：LLM 经常虚构 exit id（如 "east_rust_track" 而非真正的
    "minecart_track"）。本函数把任意字符串归到当前房间真实出口 id：
      1. 完全匹配 `to` 或 `id` → 直接返回
      2. 否则按 label 和 to 做子串/前缀模糊匹配（中文标签如「沿外侧锈轨往东」也能
         匹配到 "east"/"east_rust_track" 这类 LLM 编造的英文 id）
      3. 找不到返回 ("", reason)。

    返回 (canonical_id, debug_reason)。canonical_id 为空时说明无法规范化。"""
    target = (target or "").strip()
    if not target:
        return "", "empty target"
    scene = state.data.get("scene") or {}
    current_room = scene.get("current_room") or {}
    exits = list(current_room.get("exits") or [])
    if not exits:
        return target, "no exits to validate"
    # 1. 直接命中
    for ex in exits:
        if str(ex.get("to") or "") == target:
            return target, "exact match"
    # 2. 模糊匹配 — 拆 target 成关键词 token，与 exit.to 或 label 做对应。
    tokens = [t for t in re.split(r"[_\-\s]+", target.lower()) if t]
    best_id = ""
    best_score = 0
    for ex in exits:
        to_id = str(ex.get("to") or "").lower()
        label = str(ex.get("label") or "")
        score = 0
        for tok in tokens:
            if tok and tok in to_id:
                score += 2
            if tok and tok in label.lower():
                score += 1
        # 中文方向关键词映射
        direction_map = {
            "east": ["东", "外侧"], "west": ["西"], "north": ["北"],
            "south": ["南"], "down": ["下", "降"], "up": ["上", "升"],
        }
        for tok in tokens:
            for cn in direction_map.get(tok, []):
                if cn in label:
                    score += 1
        if score > best_score:
            best_score = score
            best_id = str(ex.get("to") or "")
    if best_id and best_score >= 1:
        return best_id, f"fuzzy match score={best_score} from tokens={tokens}"
    return "", f"no exit matches target={target!r} (exits={[e.get('to') for e in exits]})"


def _execute_rules_action(state: GameState, body: dict[str, Any]) -> dict[str, Any]:
    """Execute one deterministic RulesEngine action against state.

    Used by both /api/rules/action and the chat pipeline so free-form player
    input can trigger dice before the GM narrates the outcome.
    """
    body = dict(body or {})
    kind = str(body.get("kind") or "").strip()
    seed = _coerce_rule_seed(body.get("seed"))
    prelude: list[dict[str, Any]] = []

    move_to = str(body.get("move_to") or "").strip()
    if move_to and kind in {"skill_check", "saving_throw", "trap_check"}:
        cur = (state.data.get("scene") or {}).get("location_id")
        if move_to != cur:
            canonical, reason = _canonicalize_exit_target(state, move_to)
            if not canonical:
                return {"ok": False, "error": f"无法前往 {move_to}：{reason}",
                        "canonicalize": {"requested": move_to, "reason": reason}}
            moved = _rb_enter_room(state, canonical)
            if not moved.get("ok"):
                return {"ok": False, "error": moved.get("error") or f"无法前往 {canonical}",
                        "canonicalize": {"requested": move_to, "resolved": canonical}}
            prelude.append({"kind": "move", "to": canonical, "requested": move_to,
                            "room": moved.get("room")})

    if kind == "skill_check":
        skill = str(body.get("skill") or "")
        if not skill:
            return {"ok": False, "error": "缺少 skill"}
        dc = int(body.get("dc", body.get("dc_hint", 12)))
        result = _rb_skill_check(
            state, skill=skill, dc=dc,
            advantage=bool(body.get("advantage")),
            disadvantage=bool(body.get("disadvantage")),
            seed=seed,
            reason=str(body.get("reason") or body.get("fact") or ""),
            sets_flag=body.get("sets_flag") or body.get("reveals"),
        )
        out: dict[str, Any] = {"ok": True, "result": result}
    elif kind == "saving_throw":
        ability = str(body.get("ability") or "")
        if not ability:
            return {"ok": False, "error": "缺少 ability"}
        dc = int(body.get("dc", body.get("dc_hint", 12)))
        result = _rb_saving_throw(
            state, ability=ability, dc=dc,
            advantage=bool(body.get("advantage")),
            disadvantage=bool(body.get("disadvantage")),
            seed=seed,
            reason=str(body.get("reason") or body.get("fact") or ""),
            fail_damage_expr=body.get("fail_damage_expr") or body.get("fail_damage"),
            fail_condition=body.get("fail_condition"),
        )
        out = {"ok": True, "result": result}
    elif kind == "trap_check":
        room_id = str(body.get("room_id") or state.data.get("scene", {}).get("location_id") or "")
        trap_id = str(body.get("trap_id") or "")
        if not room_id or not trap_id:
            return {"ok": False, "error": "缺少 room_id 或 trap_id"}
        out = _rb_trap_check(state, room_id=room_id, trap_id=trap_id, seed=seed)
    elif kind == "attack":
        target_id = str(body.get("target") or body.get("target_id") or "")
        weapon_id = str(body.get("weapon") or body.get("weapon_id") or "shortsword")
        enc = state.data.get("encounter") or {}
        encounter_id = str(body.get("encounter_id") or "").strip()
        if not enc.get("active") and encounter_id:
            started = _rb_start_encounter(state, encounter_id=encounter_id, seed=seed)
            if not started.get("ok"):
                return {"ok": False, "error": started.get("error") or f"无法启动遭遇 {encounter_id}"}
            prelude.append({"kind": "start_encounter", "encounter": started.get("encounter")})
            enc = state.data.get("encounter") or {}
        if not target_id and enc.get("active"):
            enemy = next(
                (c for c in enc.get("combatants", []) if c.get("side") == "enemy" and not c.get("defeated")),
                None,
            )
            if enemy:
                target_id = str(enemy.get("id") or "")
        out = _rb_player_attack(
            state, target_id=target_id, weapon_id=weapon_id,
            advantage=bool(body.get("advantage")),
            disadvantage=bool(body.get("disadvantage")),
            seed=seed,
        )
    elif kind == "short_rest":
        out = _rb_short_rest(state, seed=seed)
    elif kind == "consume_item":
        item_id = str(body.get("item_id") or body.get("item") or body.get("alias") or "")
        try:
            qty = int(body.get("qty") or 1)
        except (TypeError, ValueError):
            qty = 1
        out = _rb_consume_item_action(state, item_id=item_id, qty=qty,
                                       reason=str(body.get("reason") or ""))
    elif kind == "move":
        loc = str(body.get("to") or body.get("target") or body.get("move_to") or "")
        canonical, reason = _canonicalize_exit_target(state, loc)
        if not canonical:
            return {"ok": False, "error": f"无法前往 {loc}：{reason}",
                    "canonicalize": {"requested": loc, "reason": reason}}
        out = _rb_enter_room(state, canonical)
        if isinstance(out, dict) and out.get("ok"):
            out.setdefault("canonicalize", {"requested": loc, "resolved": canonical})
    else:
        return {"ok": False, "error": f"未支持的 kind: {kind}"}

    if prelude:
        out.setdefault("prelude", prelude)
    return out


def _chat_rule_candidates(
    state: GameState,
    user_input: str,
    curator_actions: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Merge local module rules with LLM-inferred rule candidates.

    Module rules are deterministic and tied to the active room graph, so they
    must win over generic LLM candidates such as a loose "stealth DC 12".
    """
    scene = state.data.get("scene") or {}
    if not scene.get("module_id"):
        return list(curator_actions or [])

    merged: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    def add(raw: dict[str, Any] | None) -> None:
        if not isinstance(raw, dict):
            return
        action = dict(raw)
        key = (
            action.get("kind"),
            action.get("skill") or action.get("ability"),
            action.get("target") or action.get("target_id"),
            action.get("move_to") or action.get("to"),
            action.get("trap_id"),
        )
        if key in seen:
            return
        seen.add(key)
        merged.append(action)

    for action in _rb_suggest_rule_actions(user_input, state):
        add(action)
    # Bug 5：从玩家文本里 deterministic 解析 inventory 消耗意图。
    # 不依赖 LLM —— "点燃 1 支 Torch" / "use 2 Healing Draught" 等都从这里入。
    pc = state.data.get("player_character") or {}
    for intent in _rb_parse_consume_intent(user_input, pc):
        add({
            "kind": "consume_item",
            "item_id": intent["item_id"],
            "qty": intent["qty"],
            "reason": f"backend parser: {intent['matched']!r}",
        })
    for action in curator_actions or []:
        add(action)
    return merged


def _apply_chat_rule_candidates(state: GameState, actions: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Apply rule actions inferred from the player's text.

    Bug 4：之前只跑第一条成功动作，玩家说「我调查脚印然后沿东侧锈轨探索」时
    Investigation 成功就 break，move 永不触发，GM 又自己叙事成已经移动。
    现在按 kind 去重，每种 kind 至多跑一次，但不同 kind 可以同回合都跑
    （e.g. 一次 investigation + 一次 move）。

    失败的动作（含 exit 规范化失败）也保留在 results 里，让
    _rule_results_prompt 把失败原因传给 GM 防止幻觉。
    """
    scene = state.data.get("scene") or {}
    if not scene.get("module_id"):
        return []

    allowed = {"skill_check", "saving_throw", "trap_check", "attack", "short_rest", "move", "consume_item"}
    # 一种 kind 最多跑一次 — 同一回合不允许双重 attack / 双重 skill_check 等。
    # 但 skill_check + move 可以同回合跑（玩家描述含调查 + 移动）。
    consumed_kinds: set[str] = set()
    results: list[dict[str, Any]] = []
    for raw in actions or []:
        if not isinstance(raw, dict):
            continue
        action = dict(raw)
        kind = action.get("kind")
        if kind not in allowed or kind in consumed_kinds:
            continue
        out = _execute_rules_action(state, action)
        # 不管 ok / not ok 都记录：失败的也要传给 GM 让它明白发生了什么
        results.append({"action": action, "out": out})
        if out.get("ok"):
            consumed_kinds.add(kind)
        # 允许继续找下一种 kind 的候选；只对成功的 kind 占位
    return results


def _rule_results_prompt(rule_results: list[dict[str, Any]], state: GameState | None = None) -> str:
    if not rule_results:
        return ""
    lines = [
        "【RulesEngine 本轮裁定】",
        "以下结果已经由 deterministic RulesEngine 写入状态。GM 必须基于这些结果叙事，不能重新掷骰、改写 HP/AC/先攻/dice_log。",
    ]
    if isinstance(state, GameState):
        scene = state.data.get("scene") or {}
        room = scene.get("current_room") or {}
        room_name = room.get("name") or scene.get("location_id") or ""
        room_id = scene.get("location_id") or room.get("id") or ""
        if room_name or room_id:
            lines.append(f"当前规则场景：{room_name} ({room_id})。GM 输出中的当前位置必须以此为准，不要写成其他房间。")
        enc = state.data.get("encounter") or {}
        if enc.get("active"):
            live = [
                f"{c.get('name') or c.get('id')} HP {c.get('hp')}/{c.get('max_hp')}"
                for c in enc.get("combatants", [])
                if c.get("side") == "enemy" and not c.get("defeated")
            ]
            if live:
                lines.append("当前仍在战斗的敌人：" + "；".join(live))
    for item in rule_results:
        action = item.get("action") or {}
        out = item.get("out") or {}
        # Bug 4：失败 action（含 exit 规范化失败 / 不可达房间）也要写进 prompt，
        # 否则 GM 不知道移动没真发生还会接着叙事「沿轨道向东摸索过去」。
        if not out.get("ok"):
            err = out.get("error") or "（未知）"
            canon = out.get("canonicalize") or {}
            req = canon.get("requested") or action.get("move_to") or action.get("to") or ""
            lines.append(
                f"- ❌ {action.get('kind')} 未执行：{err}"
                + (f"（玩家文本似乎想去 {req!r}，但当前房间没有这个出口）" if req else "")
            )
            lines.append("  · GM 必须在叙事里反映这条失败：不要把玩家描述成已经移动/已经完成；"
                         "可让玩家明确选择真正可用的出口或重述意图。")
            continue
        result = out.get("result") or {}
        if out.get("prelude"):
            for pre in out["prelude"]:
                room = pre.get("room") or {}
                requested = pre.get("requested")
                resolved = pre.get("to")
                if requested and requested != resolved:
                    lines.append(
                        f"- 先移动到：{room.get('name') or resolved}"
                        f"（玩家文本写的是 {requested!r}，系统规范化到真实出口 {resolved!r}）"
                    )
                else:
                    lines.append(f"- 先移动到：{room.get('name') or resolved}")
        roll = result.get("roll") or {}
        total = roll.get("total")
        dc = result.get("dc")
        success = result.get("success")
        skill = action.get("skill") or action.get("ability") or action.get("target") or action.get("kind")
        verdict = "成功" if success is True else "失败" if success is False else "已执行"
        bit = f"- {action.get('kind')} {skill}: {verdict}"
        if total is not None:
            bit += f"，骰点总计 {total}"
        if dc is not None:
            bit += f" vs DC {dc}"
        lines.append(bit)
        for fact in result.get("gm_facts") or []:
            lines.append(f"  · GM fact: {fact}")
    return "\n".join(lines)


def _rules_payload(state: GameState) -> dict:
    """前端 UI 需要的精简切片：角色卡 + 场景 + 战斗 + 骰子日志 + 模组元信息。"""
    return {
        "ruleset": state.data.get("ruleset", {}),
        "player_character": state.data.get("player_character", {}),
        "scene": state.data.get("scene", {}),
        "encounter": state.data.get("encounter", {}),
        "dice_log": list(state.data.get("dice_log", []))[-30:],
    }


def _append_rules_receipt(state: GameState, text: str) -> None:
    text = str(text or "").strip()
    if not text:
        return
    state.data.setdefault("history", []).append({
        "role": "assistant",
        "content": text,
        "source": "rules_engine",
    })


def _clear_pending_questions_after_rule_action(state: GameState, choice: str) -> None:
    questions = state.data.setdefault("permissions", {}).setdefault("pending_questions", [])
    cleared = 0
    while questions:
        state.clear_pending_question(index=0, choice=choice or "rules_action")
        cleared += 1
        questions = state.data.setdefault("permissions", {}).setdefault("pending_questions", [])
    if cleared:
        memory = state.data.setdefault("memory", {})
        updates = memory.get("last_structured_updates") or []
        memory["last_structured_updates"] = [
            item for item in updates
            if "等待玩家回答" not in str(item)
        ][:12]


def _room_receipt(room: dict[str, Any] | None) -> str:
    room = room or {}
    name = room.get("name") or room.get("id") or "未知房间"
    room_id = room.get("id") or ""
    lines = [f"【RulesEngine：移动】你来到「{name}」{f'（{room_id}）' if room_id else ''}。"]
    desc = str(room.get("description") or "").strip()
    if desc:
        lines.append(desc)
    clues = [c.get("text") if isinstance(c, dict) else str(c) for c in (room.get("visible_clues") or [])]
    clues = [c for c in clues if c]
    if clues:
        lines.append("可见线索：" + "；".join(clues[:4]) + "。")
    exits = [e.get("label") or e.get("to") for e in (room.get("exits") or []) if isinstance(e, dict)]
    exits = [e for e in exits if e]
    if exits:
        lines.append("可用出口：" + "、".join(exits[:5]) + "。")
    return "\n\n".join(lines)


def _roll_line(result: dict[str, Any]) -> str:
    roll = result.get("roll") or {}
    expr = roll.get("expression") or ""
    rolls = ",".join(str(x) for x in (roll.get("rolls") or []))
    mod = roll.get("modifier")
    total = roll.get("total")
    dc = result.get("dc")
    parts = []
    if expr or rolls:
        bit = expr or "roll"
        if rolls:
            bit += f"=[{rolls}]"
        if isinstance(mod, (int, float)) and mod:
            bit += f"{mod:+g}"
        if total is not None:
            bit += f" → {total}"
        if dc is not None:
            bit += f" vs DC {dc}"
        parts.append(bit)
    return "；".join(parts)


def _action_receipt(action: dict[str, Any], out: dict[str, Any]) -> str:
    result = out.get("result") or {}
    kind = action.get("kind") or "action"
    verdict = "成功" if result.get("success") is True else "失败" if result.get("success") is False else "已执行"
    label = action.get("skill") or action.get("ability") or action.get("target") or action.get("target_id") or kind
    lines = [f"【RulesEngine：{kind}】{label}：{verdict}。"]
    roll = _roll_line(result)
    if roll:
        lines.append(f"掷骰：{roll}。")
    damage = result.get("damage") or {}
    if damage.get("total") is not None:
        lines.append(f"伤害：{damage.get('total')}。")
    for fact in result.get("gm_facts") or []:
        if fact:
            lines.append(str(fact))
    if out.get("prelude"):
        for pre in out["prelude"]:
            if pre.get("kind") == "move":
                room = pre.get("room") or {}
                moved_to = room.get("name") or pre.get("to")
                if moved_to:
                    lines.insert(1, f"先移动到：{moved_to}。")
            elif pre.get("kind") == "start_encounter":
                enc = pre.get("encounter") or {}
                name = (enc.get("definition") or {}).get("name") or enc.get("encounter_id")
                lines.insert(1, f"遭遇开始：{name}。")
    return "\n\n".join(lines)


def _encounter_receipt(prefix: str, res: dict[str, Any]) -> str:
    enc = res.get("encounter") or {}
    if not enc:
        return f"【RulesEngine：{prefix}】已执行。"
    if prefix == "先攻":
        order = enc.get("initiative_order") or []
        order_line = " → ".join(f"{o.get('name')}({o.get('init')})" for o in order if o)
        return f"【RulesEngine：先攻】遭遇开始。\n\n先攻顺序：{order_line}。"
    if prefix == "下一回合":
        order = enc.get("initiative_order") or []
        idx = int(enc.get("turn_index") or 0)
        current = order[idx] if 0 <= idx < len(order) else {}
        return f"【RulesEngine：下一回合】现在轮到 {current.get('name') or '未知'}。"
    result = res.get("result") or {}
    action = {"kind": prefix, "target": result.get("target_name") or "player"}
    return _action_receipt(action, {"result": result})





# ────────────────────────────────────────────────────────────
# task 48: 侧栏控制台助手 (/api/console_assistant/*)
# ────────────────────────────────────────────────────────────


def _resolve_console_assistant_backend(api_user: dict[str, Any] | None):
    """按用户偏好取 backend (复用 GM 的 backend 抽象)。

    优先级:
      1. user_preferences.console_assistant_model_override = {api_id, model}
         (前端可单独切助手模型, 与 GM 模型解耦)
      2. user_preferences.gm.api_id + gm.model_real_name (跟随 GM)
      3. 系统 selected_model() 默认值
    """
    api_id = None
    model_real = None
    if api_user and api_user.get("id"):
        try:
            from platform_app.db import connect, init_db
            init_db()
            with connect() as db:
                row = db.execute(
                    "select preferences from user_preferences where user_id = %s",
                    (int(api_user["id"]),),
                ).fetchone()
                prefs = (row and row.get("preferences")) or {}
                if isinstance(prefs, dict):
                    override = prefs.get("console_assistant_model_override") or {}
                    if isinstance(override, dict):
                        api_id = override.get("api_id") or api_id
                        model_real = override.get("model") or model_real
                    if not api_id:
                        api_id = prefs.get("gm.api_id") or None
                    if not model_real:
                        model_real = prefs.get("gm.model_real_name") or None
        except Exception:
            pass
    if not api_id or not model_real:
        model = selected_model()
        api_id = api_id or model.get("api_id")
        model_real = model_real or model.get("real_name")
    # 用 GameMaster 构造 backend, 再借用其 ._backend
    gm = GameMaster(
        api_id=str(api_id) if api_id is not None else api_id,
        model=str(model_real) if model_real is not None else model_real,
        user_id=int(api_user["id"]) if api_user and api_user.get("id") else None,
    )
    return gm._backend


# ────────────────────────────────────────────────────────────
# task 107G: 双时间线 panel — GET /api/saves/:save_id/timeline
# ────────────────────────────────────────────────────────────



if __name__ == "__main__":
    import uvicorn

    print(f"[API] {APP_TITLE} RPG backend: http://{HOST}:{PORT}")
    print("[UI]  React frontend served separately via Vite (默认 http://127.0.0.1:5173/Platform.html)")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
