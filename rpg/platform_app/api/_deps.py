"""platform_app.api._deps — 跨 router 共享的 dependency / helper。"""
from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse as BaseJSONResponse

from tools_dsl.tool_registry import tool_payload

from .. import auth, branches, knowledge, library, script_import, settings, workspace
from ..db import connect, init_db, status as db_status
from ..security import public_user

SESSION_COOKIE = "rpg_session"
API_VERSION = "1"

COMMANDS = [
    ("GET", "/", "文字 RPG 主游戏界面"),
    ("GET", "/app", "多用户平台/创作平台界面"),
    ("GET", "/api/state", "读取当前可玩存档状态"),
    ("POST", "/api/new", "创建新游戏并保留旧档备份"),
    ("POST", "/api/opening", "生成开场"),
    ("POST", "/api/chat", "发送玩家行动/对话，支持流式 GM 输出与结构化状态写回"),
    ("POST", "/api/stop", "打断当前生成"),
    ("POST", "/api/save", "手动保存当前游戏"),
    ("POST", "/api/memory/mode", "设置记忆模式"),
    ("POST", "/api/memory/add", "添加长期记忆"),
    ("POST", "/api/memory/remove", "删除长期记忆"),
    ("POST", "/api/permissions", "设置 LLM 状态写入权限"),
    ("GET", "/api/models", "读取 API/模型树与前端显示模型"),
    ("POST", "/api/models/select", "选择当前前端模型"),
    ("POST", "/api/models/api", "新增或更新 API 供应商"),
    ("POST", "/api/models/model", "新增或更新 API 下属模型"),
    ("GET", "/api/tools", "插件/MCP/Skill 能力状态"),
    ("POST", "/api/mcp/server", "新增或更新 MCP 服务器配置"),
    ("POST", "/api/mcp/server/enabled", "启用或禁用 MCP 服务器"),
    ("POST", "/api/mcp/server/delete", "删除 MCP 服务器配置"),
    ("POST", "/api/mcp/server/validate", "校验 MCP stdio 命令可用性"),
    ("POST", "/api/skills/import", "本地部署导入 Skill 包"),
    ("POST", "/api/worldline/variable", "新增或锁定用户世界线变量"),
    ("POST", "/api/worldline/variable/remove", "移除用户世界线变量"),
    ("POST", "/api/auth/register", "注册账号"),
    ("POST", "/api/auth/login", "登录并写入会话 cookie"),
    ("POST", "/api/auth/logout", "退出登录"),
    ("GET", "/api/platform", "平台总览：主页、剧本、存档、库、工具"),
    ("GET", "/api/scripts", "剧本列表"),
    ("POST", "/api/scripts/import", "导入 TXT/MD 剧本并自动识别章节"),
    ("GET", "/api/scripts/{script_id}/chapters", "读取剧本章节目录与预览"),
    ("POST", "/api/scripts/{script_id}/knowledge/sync", "重建剧本 ChapterFact、世界书、人设卡和检索块"),
    ("GET", "/api/scripts/{script_id}/chapter-facts", "读取剧本 ChapterFact 时间线"),
    ("GET", "/api/scripts/{script_id}/birthpoints", "入场选出生点：按 phase 聚合 + 每 phase 均匀采样 anchor"),
    ("GET", "/api/scripts/{script_id}/character-cards", "读取剧本人设卡"),
    ("GET", "/api/scripts/{script_id}/worldbook", "读取剧本世界书条目"),
    ("GET", "/api/saves", "游戏存档目录"),
    ("POST", "/api/saves", "基于剧本创建新存档"),
    ("GET", "/api/branches/{save_id}", "读取某个存档的分支树"),
    ("POST", "/api/branches/continue", "从任意对话节点派生/激活当前游戏 runtime"),
    ("POST", "/api/branches/activate", "直接激活某个分支节点为当前游戏 runtime"),
    ("POST", "/api/branches/delete", "删除某条连线下的整条分支"),
    ("GET", "/api/saves/{save_id}/context-runs", "读取某个存档的上下文子代理运行记录"),
    ("GET", "/api/saves/{save_id}/anchors", "task 136: 读取存档世界线收束锚点状态"),
    ("POST", "/api/saves/{save_id}/anchors/reseed", "task 136: 重 seed 锚点 (调试用)"),
    ("GET", "/api/settings", "读取设置"),
    ("POST", "/api/settings", "写入设置"),
    ("GET", "/api/library", "文件库列表"),
    ("POST", "/api/library/upload", "文件库上传"),
    ("POST", "/api/library/mkdir", "文件库创建文件夹"),
    ("POST", "/api/library/delete", "文件库删除"),
    ("GET", "/api/library/download", "文件库下载"),
    ("GET", "/api/platform/commands", "读取全部功能指令清单"),
]


def json_response(content, status_code: int = 200, **kwargs):
    if isinstance(content, dict) and "meta" not in content:
        content = {
            **content,
            "meta": {
                "api_version": API_VERSION,
                "stable": True,
            },
        }
    return BaseJSONResponse(jsonable_encoder(content), status_code=status_code, **kwargs)


def _set_session_cookie(response: BaseJSONResponse, request: Request, token: str) -> None:
    secure_env = os.environ.get("RPG_COOKIE_SECURE")
    secure = request.url.scheme == "https" if secure_env is None else secure_env == "1"
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=secure,
        samesite=os.environ.get("RPG_COOKIE_SAMESITE", "lax"),
        max_age=auth.SESSION_DAYS * 24 * 60 * 60,
        path="/",
    )


def _auth_required() -> bool:
    """与 ui.py:_api_auth_required 同义，避免循环导入；服务器模式禁止匿名访问。"""
    explicit = os.environ.get("RPG_REQUIRE_AUTH", "").strip()
    if explicit == "1":
        return True
    if explicit == "0":
        return False
    mode = os.environ.get("RPG_DEPLOYMENT_MODE", "local").strip().lower()
    return mode not in {"local", "desktop", "self_hosted", "self-hosted"}


def current_user(request: Request) -> dict | None:
    try:
        init_db()
        user = auth.user_from_token(request.cookies.get(SESSION_COOKIE))
        if user:
            workspace.ensure_default(user["id"])
        return user
    except Exception:
        return None


def require_user(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="需要登录")
    return user


def _resolve_save_id(user_id: int, body: dict) -> int:
    raw = body.get("save_id")
    if raw:
        return int(raw)
    with connect() as db:
        row = db.execute(
            "select id from game_saves where user_id = %s order by updated_at desc, id desc limit 1",
            (user_id,),
        ).fetchone()
    if not row:
        raise ValueError("还没有可写入的存档")
    return int(row["id"])


def platform_for(user: dict | None) -> dict:
    """构建 /api/platform 和注册/登录响应的 payload。

    安全：MCP server 的 command/args/env 含 secret，普通用户必须脱敏。
    与 ui.py:_redact_tools 共用同一份逻辑，避免再次出现"漏脱敏入口"。
    """
    payload = workspace.overview(user)
    is_admin = bool(user and user.get("role") == "admin")
    payload["tools"] = _redact_mcp_in_tools(tool_payload(), is_admin)
    payload["commands"] = command_payload()
    if user:
        payload["library"] = library.list_dir(user["id"], "")
    return payload


_MCP_SECRET_FIELDS = ("command", "args", "env", "credential", "secret", "token")


def _redact_mcp_in_tools(tools: dict, is_admin: bool) -> dict:
    """递归脱敏 tools 里的 mcp.servers[].command/args/env。"""
    if is_admin:
        return tools
    import copy
    out = copy.deepcopy(tools)
    for srv in ((out.get("mcp") or {}).get("servers") or []):
        for field in _MCP_SECRET_FIELDS:
            srv.pop(field, None)
    for srv in (out.get("mcp_servers") or []):
        for field in _MCP_SECRET_FIELDS:
            srv.pop(field, None)
    return out


def command_payload() -> list[dict]:
    return [{"method": method, "path": path, "name": path.rsplit("/", 1)[-1] or path, "desc": desc} for method, path, desc in COMMANDS]


def _client_ip(request: Request) -> str:
    """获取客户端 IP。

    安全：默认只用 TCP 层的 request.client.host，不信任 X-Forwarded-For
    （否则攻击者直接换头就能绕过按 IP 的速率限制）。
    仅当 TCP 对端 IP 在 RPG_TRUSTED_PROXIES 白名单里（如 nginx/cloudflare 后端），
    才信 XFF 的第一段。
    """
    tcp_ip = request.client.host if request.client else ""
    trusted = {
        ip.strip() for ip in os.environ.get("RPG_TRUSTED_PROXIES", "").split(",") if ip.strip()
    }
    if tcp_ip and tcp_ip in trusted:
        xff = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if xff:
            return xff
    return tcp_ip
